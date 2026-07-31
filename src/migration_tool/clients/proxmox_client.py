"""Thin wrapper around the native `qm` / `pvesh` binaries.

Deployment model: this tool runs locally ON the Proxmox host (see README),
so shelling out to the node's own management binaries is the natural choice
-- it's what proxmoxer would do under the hood anyway via its local socket
transport, and it needs zero extra dependencies on a host where pip isn't
even installed.

Unlike esxi_client.py, mutation here is the whole point (this is the
migration TARGET). Safety here comes from register_engine.py's mandatory
post-action verification, not from restricting what commands exist.
"""
from __future__ import annotations

import json
import subprocess

from ..types import CommandResult


def _exec(argv: list[str], timeout_s: int = 60) -> CommandResult:
    try:
        proc = subprocess.run(
            argv, shell=False, capture_output=True, text=True, timeout=timeout_s
        )
        return CommandResult(
            ok=proc.returncode == 0,
            returncode=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
            argv=argv,
        )
    except subprocess.TimeoutExpired as e:
        return CommandResult(
            ok=False, returncode=-1, stdout=(e.stdout or ""),
            stderr=f"timed out after {timeout_s}s", argv=argv,
        )


def qm(*args: str, timeout_s: int = 60) -> CommandResult:
    return _exec(["qm", *args], timeout_s=timeout_s)


def pvesh(*args: str, timeout_s: int = 60) -> CommandResult:
    return _exec(["pvesh", *args], timeout_s=timeout_s)


def pvesh_json(*args: str, timeout_s: int = 60) -> dict | list | None:
    result = pvesh(*args, "--output-format", "json", timeout_s=timeout_s)
    if not result.ok:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def qm_config(target_vmid: int) -> dict[str, str]:
    """Parses `qm config <vmid>` into a flat dict. This is the ONLY function
    register_engine.py trusts to check whether a disk is actually attached --
    never the exit code of the command that attached it.
    """
    result = qm("config", str(target_vmid))
    parsed: dict[str, str] = {}
    if not result.ok:
        return parsed
    for line in result.stdout.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        parsed[key.strip()] = value.strip()
    return parsed


def vmid_exists(target_vmid: int) -> bool:
    return qm("status", str(target_vmid)).ok


def pool_exists(pool_name: str) -> bool:
    data = pvesh_json("get", "/pools")
    if not isinstance(data, list):
        return False
    return any(p.get("poolid") == pool_name for p in data)
