"""Pulls a VM's disk file(s) from ESXi to local staging on the Proxmox host.

Collision safety strategy (belt AND suspenders, given how many real
collisions the manual project hit -- see path_resolver.py's docstring):

  1. STRUCTURAL: every VM gets its own staging subdirectory, namespaced by
     esxi_vmid. Two VMs literally cannot collide on disk regardless of what
     their source .vmdk filenames are, because they're never written into
     the same directory.
  2. CONVENTION: for the common case (no snapshot chain), files are also
     renamed to `<local_name>-src.vmdk` / `<local_name>-src-flat.vmdk` on
     arrival, matching the naming convention used throughout the manual
     project, with the descriptor's internal extent reference rewritten to
     match -- this is what the manual project's per-batch scripts did by
     hand with `mv` + `sed`. Renaming happens immediately after each file's
     copy completes, before the next file (or next VM) starts copying.
  3. Snapshot chains (known issue #4) are copied verbatim into their
     isolated subdirectory instead, deliberately NOT renamed -- their
     internal parent/child cross-references must stay intact for
     `qemu-img convert` to resolve the chain, and subdirectory isolation
     alone is already sufficient to prevent collision with any other VM.

Caller is expected to have already run capacity_check against the staging
root before calling this, and collision_detector across the whole batch
before starting any VM's copy.
"""
from __future__ import annotations

import re
from pathlib import Path

from .esxi_client import ESXiClient
from .types import CopiedFile, CopyResult, ResolvedPath

_EXTENT_LINE_RE = re.compile(
    r'^(?P<prefix>(RW|RDONLY|NOACCESS)\s+\d+\s+\S+\s+")(?P<filename>[^"]+)(?P<suffix>".*)$',
    re.MULTILINE,
)


def _rewrite_descriptor_extent_reference(descriptor_path: Path, new_flat_name: str) -> bool:
    """Rewrites the extent line inside a VMDK descriptor to point at the
    renamed flat file. Returns True if a rewrite was made.

    Equivalent to the `sed -i "s/OLD-flat.vmdk/NEW-flat.vmdk/"` step every
    batch script in the manual project performed by hand.
    """
    text = descriptor_path.read_text(errors="replace")
    match = _EXTENT_LINE_RE.search(text)
    if not match:
        return False
    new_text = (
        text[: match.start("filename")] + new_flat_name + text[match.end("filename"):]
    )
    descriptor_path.write_text(new_text)
    return True


def _remote_path_for(resolved: ResolvedPath, relative_path: str) -> str:
    if not resolved.datastore:
        raise ValueError(
            f"esxi_vmid={resolved.esxi_vmid}: no datastore resolved -- "
            "resolution_ok should have been False; refusing to guess a path"
        )
    return f"/vmfs/volumes/{resolved.datastore}/{relative_path}"


def stage_vm_disk(
    client: ESXiClient,
    resolved: ResolvedPath,
    staging_root: str,
    local_name: str,
) -> CopyResult:
    if not resolved.resolution_ok:
        return CopyResult(
            esxi_vmid=resolved.esxi_vmid,
            ok=False,
            files=[],
            total_bytes=0,
            error="resolved.resolution_ok is False -- refusing to copy from an unverified path",
        )
    if resolved.appliance_risk.value == "likely_appliance":
        return CopyResult(
            esxi_vmid=resolved.esxi_vmid,
            ok=False,
            files=[],
            total_bytes=0,
            error=f"flagged as likely vendor appliance: {resolved.appliance_reason}",
        )

    vm_dir = Path(staging_root) / f"{resolved.esxi_vmid}_{local_name}"
    vm_dir.mkdir(parents=True, exist_ok=True)

    copied: list[CopiedFile] = []
    total_bytes = 0

    if resolved.has_snapshot_chain:
        for f in resolved.disk_files:
            remote_path = _remote_path_for(resolved, f.relative_path)
            local_path = vm_dir / Path(f.relative_path).name
            result = client.pull_file(remote_path, str(local_path))
            if not result.ok:
                return CopyResult(
                    esxi_vmid=resolved.esxi_vmid, ok=False, files=copied,
                    total_bytes=total_bytes,
                    error=f"failed to copy {f.relative_path}: {result.stderr.strip()}",
                )
            size = local_path.stat().st_size if local_path.exists() else 0
            total_bytes += size
            copied.append(
                CopiedFile(
                    source_relative_path=f.relative_path,
                    staged_path=str(local_path),
                    bytes_transferred=size,
                    renamed=False,
                )
            )
        return CopyResult(esxi_vmid=resolved.esxi_vmid, ok=True, files=copied, total_bytes=total_bytes)

    # -- simple case: single base descriptor + flat, renamed on arrival ---- #
    descriptor = next((f for f in resolved.disk_files if f.role == "base_descriptor"), None)
    flat = next((f for f in resolved.disk_files if f.role == "base_flat"), None)

    if descriptor is None:
        return CopyResult(
            esxi_vmid=resolved.esxi_vmid, ok=False, files=[], total_bytes=0,
            error="no base_descriptor .vmdk found in resolved disk files",
        )

    # Flat file first (it's the large one; if it fails we haven't touched
    # the descriptor's contents yet).
    if flat is not None:
        remote_flat = _remote_path_for(resolved, flat.relative_path)
        new_flat_name = f"{local_name}-src-flat.vmdk"
        local_flat_path = vm_dir / new_flat_name
        result = client.pull_file(remote_flat, str(local_flat_path))
        if not result.ok:
            return CopyResult(
                esxi_vmid=resolved.esxi_vmid, ok=False, files=copied, total_bytes=total_bytes,
                error=f"failed to copy flat file {flat.relative_path}: {result.stderr.strip()}",
            )
        size = local_flat_path.stat().st_size if local_flat_path.exists() else 0
        total_bytes += size
        copied.append(
            CopiedFile(
                source_relative_path=flat.relative_path,
                staged_path=str(local_flat_path),
                bytes_transferred=size,
                renamed=True,
            )
        )

    remote_descriptor = _remote_path_for(resolved, descriptor.relative_path)
    new_descriptor_name = f"{local_name}-src.vmdk"
    local_descriptor_path = vm_dir / new_descriptor_name
    result = client.pull_file(remote_descriptor, str(local_descriptor_path))
    if not result.ok:
        return CopyResult(
            esxi_vmid=resolved.esxi_vmid, ok=False, files=copied, total_bytes=total_bytes,
            error=f"failed to copy descriptor {descriptor.relative_path}: {result.stderr.strip()}",
        )
    size = local_descriptor_path.stat().st_size if local_descriptor_path.exists() else 0
    total_bytes += size
    copied.append(
        CopiedFile(
            source_relative_path=descriptor.relative_path,
            staged_path=str(local_descriptor_path),
            bytes_transferred=size,
            renamed=True,
        )
    )

    if flat is not None:
        rewritten = _rewrite_descriptor_extent_reference(
            local_descriptor_path, f"{local_name}-src-flat.vmdk"
        )
        if not rewritten:
            return CopyResult(
                esxi_vmid=resolved.esxi_vmid, ok=False, files=copied, total_bytes=total_bytes,
                error=(
                    "copied descriptor+flat but could not find/rewrite the extent "
                    "line inside the descriptor -- the renamed flat file's internal "
                    "reference is now stale; qemu-img convert would fail against this. "
                    "Needs manual inspection, not safe to proceed automatically."
                ),
            )

    return CopyResult(esxi_vmid=resolved.esxi_vmid, ok=True, files=copied, total_bytes=total_bytes)
