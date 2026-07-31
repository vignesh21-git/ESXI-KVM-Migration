"""Wraps `qemu-img convert` and, critically, verifies the result before
anything downstream is allowed to treat it as trustworthy.

For a snapshot-chain disk, `source_descriptor` should point at the SNAPSHOT
descriptor (not the base) -- qemu-img resolves parentFileNameHint references
itself and produces a single flattened image. copy_engine.py stages snapshot
chains verbatim (uncollided via per-VM subdirectories) specifically so this
resolution works unmodified.

Never deletes the source files itself -- "convert, verify, THEN clean up the
raw vmdk/flat copies" is an explicit separate step, and
register_engine.py's disk-attachment verification is what actually gates
whether it's safe to do that cleanup. This module only produces the
verified qcow2 and reports whether it's trustworthy; cleanup is the
orchestrator's decision after registration succeeds.
"""
from __future__ import annotations

import json
import subprocess

from ..types import ConvertResult


def _run(argv: list[str], timeout_s: int) -> tuple[bool, str, str]:
    try:
        proc = subprocess.run(argv, shell=False, capture_output=True, text=True, timeout=timeout_s)
        return proc.returncode == 0, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as e:
        return False, (e.stdout or ""), f"timed out after {timeout_s}s"


def convert_to_qcow2(
    source_descriptor: str,
    output_qcow2_path: str,
    expected_virtual_size_bytes: int | None = None,
    timeout_s: int = 3600,
) -> ConvertResult:
    ok, _stdout, stderr = _run(
        ["qemu-img", "convert", "-f", "vmdk", "-O", "qcow2", source_descriptor, output_qcow2_path],
        timeout_s=timeout_s,
    )
    if not ok:
        return ConvertResult(
            esxi_vmid=-1,  # caller (orchestrator) fills this in from context if needed
            ok=False,
            source_descriptor=source_descriptor,
            output_qcow2=output_qcow2_path,
            reported_virtual_size_bytes=None,
            expected_virtual_size_bytes=expected_virtual_size_bytes,
            size_matches_expectation=False,
            error=f"qemu-img convert failed: {stderr.strip()}",
        )

    return _verify(source_descriptor, output_qcow2_path, expected_virtual_size_bytes, timeout_s)


def _verify(
    source_descriptor: str,
    output_qcow2_path: str,
    expected_virtual_size_bytes: int | None,
    timeout_s: int,
) -> ConvertResult:
    ok, stdout, stderr = _run(
        ["qemu-img", "info", "--output=json", output_qcow2_path], timeout_s=timeout_s,
    )
    if not ok:
        return ConvertResult(
            esxi_vmid=-1, ok=False, source_descriptor=source_descriptor,
            output_qcow2=output_qcow2_path, reported_virtual_size_bytes=None,
            expected_virtual_size_bytes=expected_virtual_size_bytes,
            size_matches_expectation=False,
            error=f"conversion produced a file but `qemu-img info` failed against it: {stderr.strip()}",
        )

    try:
        info = json.loads(stdout)
        reported_size = int(info["virtual-size"])
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        return ConvertResult(
            esxi_vmid=-1, ok=False, source_descriptor=source_descriptor,
            output_qcow2=output_qcow2_path, reported_virtual_size_bytes=None,
            expected_virtual_size_bytes=expected_virtual_size_bytes,
            size_matches_expectation=False,
            error=f"could not parse qemu-img info output: {e}",
        )

    if reported_size <= 0:
        return ConvertResult(
            esxi_vmid=-1, ok=False, source_descriptor=source_descriptor,
            output_qcow2=output_qcow2_path, reported_virtual_size_bytes=reported_size,
            expected_virtual_size_bytes=expected_virtual_size_bytes,
            size_matches_expectation=False,
            error=f"converted image reports a non-positive virtual size ({reported_size}); untrustworthy",
        )

    size_matches = True
    if expected_virtual_size_bytes is not None:
        size_matches = reported_size == expected_virtual_size_bytes

    return ConvertResult(
        esxi_vmid=-1,
        ok=size_matches,
        source_descriptor=source_descriptor,
        output_qcow2=output_qcow2_path,
        reported_virtual_size_bytes=reported_size,
        expected_virtual_size_bytes=expected_virtual_size_bytes,
        size_matches_expectation=size_matches,
        error=None if size_matches else (
            f"virtual size mismatch: converted={reported_size} expected={expected_virtual_size_bytes} "
            "-- refusing to trust this conversion"
        ),
    )
