"""Cross-references resolved disk filenames across a batch, before any copy
starts.

Real example from the manual project this exists to catch: 5 VMs in one
testbed batch all had a disk file literally named "IPv6_RefDUT_2.vmdk". If
copy_engine staged them one after another using the source filename verbatim,
VM #2's copy would silently overwrite VM #1's. The fix used throughout the
manual project was: detect every collision up front, then rename immediately
after each individual copy completes (before the next VM's copy begins) --
copy_engine.py implements the rename half, this module implements the
detection half.
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import PurePosixPath

from .types import CollisionReport, ResolvedPath


def detect(resolved_vms: list[ResolvedPath]) -> CollisionReport:
    filename_to_vmids: dict[str, list[int]] = defaultdict(list)

    for rp in resolved_vms:
        if not rp.resolution_ok:
            continue
        # Only base disk files matter for the collision that would actually
        # bite during staging (snapshot deltas travel alongside their base
        # and are renamed together as one unit by copy_engine).
        base_files = [f for f in rp.disk_files if f.role in ("base_descriptor", "base_flat")]
        for f in base_files:
            filename = PurePosixPath(f.relative_path).name
            filename_to_vmids[filename].append(rp.esxi_vmid)

    collisions = {
        name: sorted(set(vmids))
        for name, vmids in filename_to_vmids.items()
        if len(set(vmids)) > 1
    }

    return CollisionReport(has_collisions=bool(collisions), collisions=collisions)
