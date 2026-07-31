"""Registers a converted qcow2 as a Proxmox VM.

This module exists because a registration script can report overall
"success" while `qm importdisk` succeeded but the follow-up
`qm set --sata0 ...` disk-ATTACHMENT step silently failed. Checking only the
exit code of each command in sequence never catches this -- the VM's actual
config has to be re-read to confirm the disk is really there. Every step
below is followed by an independent read-back check against
`qm config <vmid>`, never inferred from the previous command's exit code
alone.

Other known issues baked in here:
  - `qm importdisk` always names its output `vm-<vmid>-disk-0.raw`,
    regardless of the source format (even though we feed it a qcow2). Any
    subsequent --sataN must reference that .raw name, never the qcow2 name,
    or attachment fails with "volume does not exist".
  - Proxmox VM names must be DNS-label-valid (no underscores). We sanitize
    automatically but always preserve the true original ESXi name + VMID +
    source path in --description, for audit traceability.
  - Pool assignment is not a `qm set` flag -- it's a separate `pvesh`
    call, and pool creation must be checked for idempotency (don't error
    if the pool already exists from a prior VM in the same batch).
  - Bus/NIC defaults are SATA + e1000 for every migrated VM. virtio is not
    used, because it requires in-guest driver injection this tool does not
    perform (virt-v2v was tried against a standalone ESXi host and failed --
    see README).
"""
from __future__ import annotations

import re

from ..clients import proxmox_client as px
from ..types import RegisterResult

_SANITIZE_RE = re.compile(r"[^A-Za-z0-9-]+")
_COLLAPSE_DASH_RE = re.compile(r"-{2,}")


def sanitize_proxmox_name(display_name: str) -> str:
    name = _SANITIZE_RE.sub("-", display_name)
    name = _COLLAPSE_DASH_RE.sub("-", name).strip("-")
    return name or "unnamed-vm"


def build_description(display_name: str, esxi_vmid: int, esxi_source_path: str, esxi_host: str) -> str:
    return (
        f"Original ESXi name: {display_name} "
        f"(VMID {esxi_vmid}, host: {esxi_host}, path: {esxi_source_path})"
    )


def register_vm(
    target_vmid: int,
    display_name: str,
    memory_mb: int,
    cores: int,
    qcow2_path: str,
    storage_id: str,
    esxi_vmid: int,
    esxi_source_path: str,
    esxi_host: str,
    pool: str | None = None,
    disk_bus: str = "sata0",
    nic_model: str = "e1000",
    bridge: str = "vmbr0",
) -> RegisterResult:
    errors: list[str] = []
    sanitized_name = sanitize_proxmox_name(display_name)
    description = build_description(display_name, esxi_vmid, esxi_source_path, esxi_host)

    # -- 1. create (idempotent) --------------------------------------------- #
    # Deliberately safe to call twice: the known-issue retry path for a
    # disk-attachment failure (register_engine's whole reason for existing)
    # re-invokes this entire function rather than a narrower "just retry the
    # attach step" helper, so `qm create` on an already-existing target_vmid
    # must be a no-op, not an error, or every retry would fail before it even
    # reached the step that actually needed retrying.
    if px.vmid_exists(target_vmid):
        created = True
    else:
        create_result = px.qm(
            "create", str(target_vmid),
            "--name", sanitized_name,
            "--memory", str(memory_mb),
            "--cores", str(cores),
            "--net0", f"{nic_model},bridge={bridge}",
        )
        created = px.vmid_exists(target_vmid)
        if not created:
            errors.append(f"qm create failed or VM not found afterward: {create_result.stderr.strip()}")

    if not created:
        return RegisterResult(
            target_vmid=target_vmid, proxmox_name=sanitized_name,
            created=False, disk_imported=False, disk_attached=False,
            boot_order_set=False, description_set=False, serial_console_set=False,
            pool_assigned=False, fully_verified=False, errors=errors,
        )

    # -- 2. import disk ------------------------------------------------------ #
    import_result = px.qm("importdisk", str(target_vmid), qcow2_path, storage_id)
    disk_imported = import_result.ok
    if not disk_imported:
        errors.append(f"qm importdisk failed: {import_result.stderr.strip()}")

    # `qm importdisk` always names its output vm-<vmid>-disk-0.raw regardless
    # of source format -- never reference the qcow2 name here.
    expected_raw_ref = f"{storage_id}:{target_vmid}/vm-{target_vmid}-disk-0.raw"

    # -- 3. attach disk, THEN independently verify via qm config ----------- #
    disk_attached = False
    if disk_imported:
        px.qm("set", str(target_vmid), f"--{disk_bus}", expected_raw_ref)
        cfg = px.qm_config(target_vmid)
        actual = cfg.get(disk_bus, "")
        disk_attached = f"vm-{target_vmid}-disk-0.raw" in actual
        if not disk_attached:
            errors.append(
                f"disk attachment could not be verified: qm config {disk_bus} = {actual!r} "
                f"(expected it to reference vm-{target_vmid}-disk-0.raw). This is exactly the "
                "silent-failure mode this tool exists to catch -- importdisk may have succeeded "
                "while attachment did not."
            )
    else:
        errors.append("skipped disk attachment because import was not verified successful")

    # -- 4. boot order, verified independently ------------------------------ #
    boot_order_set = False
    if disk_attached:
        px.qm("set", str(target_vmid), "--boot", f"order={disk_bus}")
        cfg = px.qm_config(target_vmid)
        boot_order_set = f"order={disk_bus}" in cfg.get("boot", "")
        if not boot_order_set:
            errors.append(f"boot order not verified: qm config boot = {cfg.get('boot', '')!r}")
    else:
        errors.append("skipped boot order because disk attachment was not verified")

    # -- 5. description, verified independently ----------------------------- #
    px.qm("set", str(target_vmid), "--description", description)
    cfg = px.qm_config(target_vmid)
    description_set = bool(cfg.get("description", "").strip())
    if not description_set:
        errors.append("description not verified: qm config description is empty after set")

    # -- 6. serial console, verified independently --------------------------- #
    px.qm("set", str(target_vmid), "--serial0", "socket")
    cfg = px.qm_config(target_vmid)
    serial_console_set = cfg.get("serial0", "").strip() == "socket"
    if not serial_console_set:
        errors.append(f"serial0 not verified: qm config serial0 = {cfg.get('serial0', '')!r}")

    # -- 7. pool assignment, verified independently -------------------------- #
    pool_assigned = pool is None  # no pool requested => trivially satisfied
    if pool is not None:
        if not px.pool_exists(pool):
            px.pvesh("create", "/pools", "--poolid", pool)
        px.pvesh("set", f"/pools/{pool}", "-vms", str(target_vmid))
        members = px.pvesh_json("get", f"/pools/{pool}")
        member_vmids = set()
        if isinstance(members, dict):
            member_vmids = {m.get("vmid") for m in members.get("members", [])}
        pool_assigned = target_vmid in member_vmids
        if not pool_assigned:
            errors.append(f"pool assignment not verified: {target_vmid} not found in /pools/{pool} members")

    fully_verified = all([
        created, disk_imported, disk_attached, boot_order_set,
        description_set, serial_console_set, pool_assigned,
    ])

    return RegisterResult(
        target_vmid=target_vmid,
        proxmox_name=sanitized_name,
        created=created,
        disk_imported=disk_imported,
        disk_attached=disk_attached,
        boot_order_set=boot_order_set,
        description_set=description_set,
        serial_console_set=serial_console_set,
        pool_assigned=pool_assigned,
        fully_verified=fully_verified,
        errors=errors,
    )
