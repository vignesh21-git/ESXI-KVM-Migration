"""Boot-time observation for a registered VM: start/stop, screendump capture,
and activity telemetry.

Not explicitly named in the Phase 1 module list, but added because the
known-issue library (FreeBSD mountroot/ada0, GDM black-screen/qxl) is
meaningless without a way to actually observe a boot in progress. Kept
narrowly scoped and consistent with the QEMU-monitor-based method proven
throughout the manual migration project (`qm terminal` never reliably
produced output against any of these guest images -- no guest-side serial
getty was configured on most of them -- so `qm monitor ... screendump` +
`sendkey` is the reliable path, not a fallback).

DELIBERATE SCOPE BOUNDARY: this module produces evidence (an image file, a
CPU/disk-activity reading) -- it does NOT attempt to classify that evidence
("is this a login prompt? a black screen? a mountroot prompt?"). That
classification is genuinely a vision/language judgment call, not a
mechanical one, which is exactly why it belongs in Phase 4 (LLM-driven
decisions) rather than Phase 3 (rule-based, no LLM yet). Phase 3's
orchestrator proves its loop mechanics using the anomaly types that ARE
mechanically decidable (registration verification, capacity, collisions,
appliance risk, running-VM state) -- see orchestrator.py's module docstring.
"""
from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass

from . import proxmox_client as px


@dataclass
class ActivityReading:
    target_vmid: int
    cpu_percent: float
    diskread_bytes: int


@dataclass
class BootObservation:
    target_vmid: int
    screenshot_path: str | None
    activity_before: ActivityReading | None
    activity_after: ActivityReading | None
    diskread_delta_bytes: int | None
    likely_hung: bool | None   # None = inconclusive (not enough signal yet)
    notes: list[str]


def start(target_vmid: int) -> bool:
    return px.qm("start", str(target_vmid)).ok


def stop(target_vmid: int) -> bool:
    return px.qm("stop", str(target_vmid)).ok


def _read_activity(target_vmid: int) -> ActivityReading | None:
    data = px.pvesh_json("get", f"/nodes/localhost/qemu/{target_vmid}/status/current")
    if not isinstance(data, dict):
        return None
    return ActivityReading(
        target_vmid=target_vmid,
        cpu_percent=float(data.get("cpu", 0.0)) * 100,
        diskread_bytes=int(data.get("diskread", 0)),
    )


def screendump(target_vmid: int, output_ppm_path: str, output_png_path: str | None = None) -> bool:
    """Captures a screendump via the QEMU monitor (the only console access
    method that reliably worked throughout the manual project). Optionally
    converts to PNG via pnmtopng if a png path is given and the tool is
    available -- falls back silently to leaving only the .ppm if not.
    """
    proc = subprocess.run(
        ["qm", "monitor", str(target_vmid)],
        input=f"screendump {output_ppm_path}\n",
        text=True, capture_output=True, timeout=15,
    )
    if proc.returncode != 0:
        return False

    if output_png_path:
        try:
            with open(output_png_path, "wb") as out:
                subprocess.run(
                    ["pnmtopng", output_ppm_path], stdout=out,
                    stderr=subprocess.DEVNULL, timeout=10, check=False,
                )
        except FileNotFoundError:
            pass  # pnmtopng not installed -- caller still has the .ppm
    return True


def observe_boot(
    target_vmid: int,
    screenshot_ppm_path: str,
    screenshot_png_path: str | None = None,
    settle_seconds: int = 15,
) -> BootObservation:
    """One observation pass: read activity, wait, read activity again, take
    a screenshot. The before/after activity delta is what let this project
    reliably tell "still booting slowly" (climbing diskread, e.g. VM 700
    sitting on a purple GRUB screen at 0% CPU for 2-3 minutes while genuinely
    still working) apart from "actually hung" (flat diskread AND 0% CPU --
    confirmed multiple times against VM 602/603's real, unrecoverable hang).
    A single reading was never enough; this always takes two.
    """
    notes: list[str] = []
    before = _read_activity(target_vmid)
    if before is None:
        notes.append("could not read activity telemetry before settle window")

    time.sleep(settle_seconds)

    after = _read_activity(target_vmid)
    if after is None:
        notes.append("could not read activity telemetry after settle window")

    shot_ok = screendump(target_vmid, screenshot_ppm_path, screenshot_png_path)
    if not shot_ok:
        notes.append("screendump failed")

    delta = None
    likely_hung = None
    if before is not None and after is not None:
        delta = after.diskread_bytes - before.diskread_bytes
        # Conservative: only call it "likely hung" when BOTH signals agree
        # (flat disk I/O and ~0% CPU). A single flat signal alone was
        # historically not enough to conclude a hang -- e.g. a VM idling
        # briefly at a menu is flat on both but not hung.
        likely_hung = delta == 0 and after.cpu_percent < 1.0

    return BootObservation(
        target_vmid=target_vmid,
        screenshot_path=screenshot_png_path or screenshot_ppm_path,
        activity_before=before,
        activity_after=after,
        diskread_delta_bytes=delta,
        likely_hung=likely_hung,
        notes=notes,
    )
