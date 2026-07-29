"""Verifies there's enough room on the TARGET (Proxmox) side before a copy or
convert starts.

Known issue this exists to prevent: during the manual project, staging copied
disk files under /root filled a ~96GB root partition to 100%, twice, causing
hard mid-transfer failures. The fixes baked in here:
  1. Staging directory must live under the configured bulk-storage mount
     point -- never on the root filesystem. Checked structurally, not just
     documented.
  2. Capacity is checked against the ACTUAL required bytes for the specific
     batch about to run, and callers are expected to call this again before
     each large copy (not just once at the start of a whole run), since
     available space shrinks as prior VMs in the same batch get staged.

Runs locally on the Proxmox host (see README) -- statvfs against a local
path is simpler and more reliable here than parsing `df` output over SSH.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

from .types import CapacityCheck


class UnsafeStagingLocationError(ValueError):
    pass


def assert_staging_on_bulk_storage(staging_dir: str, bulk_storage_mount: str) -> None:
    """Refuses to proceed if staging_dir isn't under the bulk storage mount.
    Call this once when a staging directory is first chosen, and treat a
    raised exception as fatal -- do not catch-and-continue.
    """
    staging_real = os.path.realpath(staging_dir)
    bulk_real = os.path.realpath(bulk_storage_mount)
    if os.path.commonpath([staging_real, bulk_real]) != bulk_real:
        raise UnsafeStagingLocationError(
            f"staging directory {staging_dir!r} (resolved: {staging_real!r}) is not "
            f"under the bulk storage mount {bulk_storage_mount!r}. Refusing to stage "
            "large disk copies outside bulk storage -- this filled the root "
            "filesystem to 100% twice during the manual migration project."
        )


def check(staging_dir: str, required_bytes: int) -> CapacityCheck:
    """Checks available space on the filesystem backing `staging_dir`.

    Caller is responsible for having already called
    assert_staging_on_bulk_storage() once for this staging_dir -- this
    function only checks the numbers, not the location, so it can be cheaply
    re-called before every individual VM's copy within a batch.
    """
    st = os.statvfs(staging_dir)
    available_bytes = st.f_bavail * st.f_frsize
    margin = available_bytes - required_bytes

    return CapacityCheck(
        storage_path=staging_dir,
        available_bytes=available_bytes,
        required_bytes=required_bytes,
        margin_bytes=margin,
        sufficient=margin > 0,
        checked_at_utc=datetime.now(timezone.utc).isoformat(),
    )


def check_with_safety_margin(
    staging_dir: str, required_bytes: int, safety_margin_ratio: float = 0.10
) -> CapacityCheck:
    """Same as check(), but requires `safety_margin_ratio` extra headroom
    beyond the raw required bytes (default 10%), since qemu-img convert
    output size and staging copies can both run slightly over raw disk size
    estimates.
    """
    padded_required = int(required_bytes * (1 + safety_margin_ratio))
    return check(staging_dir, padded_required)
