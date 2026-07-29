"""Phase 2: agent-callable tool schemas wrapping every Phase 1 function.

This is the ENTIRE action space available to the orchestrator (Phase 3+).
There is no path from the orchestrator to a raw shell command, an arbitrary
SSH invocation, or any ESXi-mutating call -- because none of those things
exist anywhere below this layer either (esxi_client.py has no write methods
to wrap in the first place).

Each entry in TOOL_REGISTRY carries:
  - a plain-JSON-serializable input schema (so this can be handed directly
    to an LLM tool-calling API in Phase 4 without modification)
  - `target`: "esxi" (always read-only, structurally) | "proxmox" (may
    mutate) | "local" (filesystem/pure-logic, no network)
  - `mutating`: bool, for orchestrator-side audit logging emphasis
  - `handler`: the actual callable, taking/returning plain JSON-safe dicts

Every mutating function's output dict preserves the per-step verification
booleans from its underlying dataclass (RegisterResult, CopyResult, etc.) --
this module never collapses those down to a single ok/fail flag. That
collapsing is exactly the bug this whole project's tool layer exists to
prevent.
"""
from __future__ import annotations

import dataclasses
import enum
from dataclasses import dataclass
from typing import Any, Callable

from . import collision_detector, convert_engine, copy_engine, path_resolver, proxmox_client as px
from . import boot_validator
from . import capacity_check as capacity_check_mod
from . import register_engine
from .esxi_client import ESXiClient, ESXiHost
from .inventory import parse_getallvms
from .types import ResolvedPath


def _to_jsonable(obj: Any) -> Any:
    """Recursively converts dataclasses/Enums into plain JSON-safe values."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: _to_jsonable(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, enum.Enum):
        return obj.value
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    return obj


def _resolved_path_from_dict(d: dict) -> ResolvedPath:
    """Reconstructs a ResolvedPath from the JSON-safe dict produced by
    esxi_resolve_vm_path's output -- needed because several downstream tools
    (stage_vm_disk, detect_collisions) take a ResolvedPath as input and the
    agent only ever holds the JSON form of one.
    """
    from .types import ApplianceRisk, DiskFile, PowerState

    return ResolvedPath(
        esxi_vmid=d["esxi_vmid"],
        display_name=d["display_name"],
        datastore=d["datastore"],
        vmx_relative_path=d["vmx_relative_path"],
        disk_files=[DiskFile(**f) for f in d["disk_files"]],
        has_snapshot_chain=d["has_snapshot_chain"],
        power_state=PowerState(d["power_state"]),
        appliance_risk=ApplianceRisk(d["appliance_risk"]),
        appliance_reason=d["appliance_reason"],
        resolution_ok=d["resolution_ok"],
        parse_warnings=d.get("parse_warnings", []),
        raw_filelayout=d.get("raw_filelayout", ""),
    )


# --------------------------------------------------------------------------- #
# Handlers -- plain dict in, plain dict out. Each thinly wraps a Phase 1
# function; none of them add new capabilities beyond what Phase 1 exposes.
# --------------------------------------------------------------------------- #
def _h_esxi_list_all_vms(args: dict) -> dict:
    client = ESXiClient(ESXiHost(address=args["esxi_host"], ssh_key_path=args.get("ssh_key_path")))
    result = client.list_all_vms()
    if not result.ok:
        return {"ok": False, "error": result.stderr.strip(), "vms": []}
    vms = parse_getallvms(result.stdout)
    return {"ok": True, "error": None, "vms": [_to_jsonable(v) for v in vms]}


def _h_esxi_disk_usage_kb(args: dict) -> dict:
    client = ESXiClient(ESXiHost(address=args["esxi_host"], ssh_key_path=args.get("ssh_key_path")))
    result = client.disk_usage_kb(args["remote_path"])
    if not result.ok:
        return {"ok": False, "error": result.stderr.strip(), "size_kb": None}
    # `du -sk <path>` output: "<kb>\t<path>"
    try:
        size_kb = int(result.stdout.split()[0])
    except (IndexError, ValueError):
        return {"ok": False, "error": f"could not parse du output: {result.stdout!r}", "size_kb": None}
    return {"ok": True, "error": None, "size_kb": size_kb}


def _h_esxi_resolve_vm_path(args: dict) -> dict:
    client = ESXiClient(ESXiHost(address=args["esxi_host"], ssh_key_path=args.get("ssh_key_path")))
    resolved = path_resolver.resolve(
        client,
        esxi_vmid=args["esxi_vmid"],
        display_name=args.get("display_name", ""),
        guest_os_raw=args.get("guest_os_raw", ""),
        annotation=args.get("annotation", ""),
    )
    return _to_jsonable(resolved)


def _h_detect_collisions(args: dict) -> dict:
    resolved_vms = [_resolved_path_from_dict(d) for d in args["resolved_vms"]]
    report = collision_detector.detect(resolved_vms)
    return _to_jsonable(report)


def _h_check_staging_capacity(args: dict) -> dict:
    result = capacity_check_mod.check_with_safety_margin(
        staging_dir=args["staging_dir"],
        required_bytes=args["required_bytes"],
        safety_margin_ratio=args.get("safety_margin_ratio", 0.10),
    )
    return _to_jsonable(result)


def _h_stage_vm_disk(args: dict) -> dict:
    client = ESXiClient(ESXiHost(address=args["esxi_host"], ssh_key_path=args.get("ssh_key_path")))
    resolved = _resolved_path_from_dict(args["resolved"])
    result = copy_engine.stage_vm_disk(
        client=client,
        resolved=resolved,
        staging_root=args["staging_root"],
        local_name=args["local_name"],
    )
    return _to_jsonable(result)


def _h_convert_vm_disk(args: dict) -> dict:
    result = convert_engine.convert_to_qcow2(
        source_descriptor=args["source_descriptor"],
        output_qcow2_path=args["output_qcow2_path"],
        expected_virtual_size_bytes=args.get("expected_virtual_size_bytes"),
    )
    return _to_jsonable(result)


def _h_register_vm(args: dict) -> dict:
    result = register_engine.register_vm(
        target_vmid=args["target_vmid"],
        display_name=args["display_name"],
        memory_mb=args["memory_mb"],
        cores=args["cores"],
        qcow2_path=args["qcow2_path"],
        storage_id=args["storage_id"],
        esxi_vmid=args["esxi_vmid"],
        esxi_source_path=args["esxi_source_path"],
        esxi_host=args["esxi_host"],
        pool=args.get("pool"),
        disk_bus=args.get("disk_bus", "sata0"),
        nic_model=args.get("nic_model", "e1000"),
        bridge=args.get("bridge", "vmbr0"),
    )
    return _to_jsonable(result)


def _h_qm_config_readback(args: dict) -> dict:
    cfg = px.qm_config(args["target_vmid"])
    return {"target_vmid": args["target_vmid"], "config": cfg}


def _h_vmid_exists(args: dict) -> dict:
    return {"target_vmid": args["target_vmid"], "exists": px.vmid_exists(args["target_vmid"])}


def _h_pool_exists(args: dict) -> dict:
    return {"pool": args["pool"], "exists": px.pool_exists(args["pool"])}


def _h_start_vm(args: dict) -> dict:
    return {"target_vmid": args["target_vmid"], "started": boot_validator.start(args["target_vmid"])}


def _h_stop_vm(args: dict) -> dict:
    return {"target_vmid": args["target_vmid"], "stopped": boot_validator.stop(args["target_vmid"])}


def _h_observe_boot(args: dict) -> dict:
    obs = boot_validator.observe_boot(
        target_vmid=args["target_vmid"],
        screenshot_ppm_path=args["screenshot_ppm_path"],
        screenshot_png_path=args.get("screenshot_png_path"),
        settle_seconds=args.get("settle_seconds", 15),
    )
    return _to_jsonable(obs)


# --------------------------------------------------------------------------- #
# Schema definitions
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ToolSchema:
    name: str
    description: str
    target: str            # "esxi" | "proxmox" | "local"
    mutating: bool
    input_schema: dict      # JSON Schema
    handler: Callable[[dict], dict]

    def to_llm_tool_def(self) -> dict:
        """Renders as an Anthropic-Messages-API-shaped tool definition, for
        direct use in Phase 4.
        """
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


_ESXI_HOST_ARGS = {
    "esxi_host": {"type": "string", "description": "ESXi host address, e.g. 192.168.4.90"},
    "ssh_key_path": {"type": ["string", "null"], "description": "Optional explicit SSH key path"},
}

TOOL_REGISTRY: dict[str, ToolSchema] = {}


def _register(schema: ToolSchema) -> None:
    TOOL_REGISTRY[schema.name] = schema


_register(ToolSchema(
    name="esxi_list_all_vms",
    description=(
        "Read-only. Lists every VM known to the given ESXi host, best-effort "
        "parsed for display purposes (VMID, display name, guest OS, annotation). "
        "Do NOT use any path/filename info from this call's output -- it has none. "
        "Call esxi_resolve_vm_path per VMID for authoritative disk paths."
    ),
    target="esxi", mutating=False,
    input_schema={
        "type": "object",
        "properties": {**_ESXI_HOST_ARGS},
        "required": ["esxi_host"],
    },
    handler=_h_esxi_list_all_vms,
))

_register(ToolSchema(
    name="esxi_resolve_vm_path",
    description=(
        "Read-only. THE authoritative way to find a VM's real disk file(s), "
        "power state, snapshot-chain status, and appliance-guest risk. Always "
        "call this before planning any copy -- display names and folder names "
        "are known to disagree with actual .vmdk paths on real ESXi hosts. "
        "If resolution_ok is false in the result, treat this VM as blocked "
        "and do not proceed with it."
    ),
    target="esxi", mutating=False,
    input_schema={
        "type": "object",
        "properties": {
            **_ESXI_HOST_ARGS,
            "esxi_vmid": {"type": "integer"},
            "display_name": {"type": "string", "description": "for appliance-risk heuristics only"},
            "guest_os_raw": {"type": "string"},
            "annotation": {"type": "string"},
        },
        "required": ["esxi_host", "esxi_vmid"],
    },
    handler=_h_esxi_resolve_vm_path,
))

_register(ToolSchema(
    name="esxi_disk_usage_kb",
    description=(
        "Read-only. `du -sk` against a specific remote path (kibibytes -- BusyBox `du` "
        "has no -h, and human-readable size parsing is a known trap; always kibibytes "
        "here). Used to compute real required_bytes for check_staging_capacity before "
        "a copy, rather than guessing from disk_files metadata alone."
    ),
    target="esxi", mutating=False,
    input_schema={
        "type": "object",
        "properties": {
            **_ESXI_HOST_ARGS,
            "remote_path": {"type": "string", "description": "absolute path under /vmfs/volumes/"},
        },
        "required": ["esxi_host", "remote_path"],
    },
    handler=_h_esxi_disk_usage_kb,
))

_register(ToolSchema(
    name="detect_collisions",
    description=(
        "Local, pure logic. Given the resolved paths for every VM in a planned "
        "batch, reports any disk filename collisions across VMs (a known, "
        "recurring situation on real ESXi hosts -- e.g. several unrelated VMs "
        "sharing an identical .vmdk filename in different folders). Call this "
        "once for the whole batch before starting ANY copy in that batch."
    ),
    target="local", mutating=False,
    input_schema={
        "type": "object",
        "properties": {
            "resolved_vms": {
                "type": "array",
                "items": {"type": "object"},
                "description": "list of esxi_resolve_vm_path output dicts",
            },
        },
        "required": ["resolved_vms"],
    },
    handler=_h_detect_collisions,
))

_register(ToolSchema(
    name="check_staging_capacity",
    description=(
        "Local filesystem check. Confirms enough free space exists at the "
        "staging path for the required bytes (with a 10% safety margin by "
        "default) before a copy starts. Call this again before EACH VM's copy "
        "within a batch, not just once at the start -- available space shrinks "
        "as earlier VMs in the batch get staged."
    ),
    target="local", mutating=False,
    input_schema={
        "type": "object",
        "properties": {
            "staging_dir": {"type": "string"},
            "required_bytes": {"type": "integer"},
            "safety_margin_ratio": {"type": "number"},
        },
        "required": ["staging_dir", "required_bytes"],
    },
    handler=_h_check_staging_capacity,
))

_register(ToolSchema(
    name="stage_vm_disk",
    description=(
        "MUTATING (writes to local staging storage only -- never to ESXi). "
        "Pulls one VM's disk file(s) from ESXi into an isolated per-VM staging "
        "subdirectory, applying collision-safe renaming for the simple case or "
        "verbatim copying for snapshot chains. Refuses to run if the given "
        "resolved path has resolution_ok=false or is flagged as a likely "
        "vendor appliance."
    ),
    target="local", mutating=True,
    input_schema={
        "type": "object",
        "properties": {
            **_ESXI_HOST_ARGS,
            "resolved": {"type": "object", "description": "esxi_resolve_vm_path output dict"},
            "staging_root": {"type": "string"},
            "local_name": {"type": "string", "description": "sanitized local base name for this VM"},
        },
        "required": ["esxi_host", "resolved", "staging_root", "local_name"],
    },
    handler=_h_stage_vm_disk,
))

_register(ToolSchema(
    name="convert_vm_disk",
    description=(
        "MUTATING (local disk only). Runs qemu-img convert to qcow2 and "
        "verifies the result with qemu-img info before reporting ok=true. "
        "For snapshot chains, source_descriptor must be the SNAPSHOT "
        "descriptor, not the base -- qemu-img resolves the parent chain "
        "itself. If expected_virtual_size_bytes is given and doesn't match "
        "the converted image, ok will be false even though the qemu-img "
        "command itself succeeded -- treat that as untrustworthy output."
    ),
    target="local", mutating=True,
    input_schema={
        "type": "object",
        "properties": {
            "source_descriptor": {"type": "string"},
            "output_qcow2_path": {"type": "string"},
            "expected_virtual_size_bytes": {"type": ["integer", "null"]},
        },
        "required": ["source_descriptor", "output_qcow2_path"],
    },
    handler=_h_convert_vm_disk,
))

_register(ToolSchema(
    name="register_vm",
    description=(
        "MUTATING (Proxmox only). Creates a VM, imports the qcow2 disk, "
        "attaches it, sets boot order, sets description (always includes the "
        "true original ESXi name/VMID/host/path for audit traceability even "
        "though the Proxmox --name is sanitized), sets serial console, and "
        "assigns to a pool if given. Every one of those steps is independently "
        "verified by reading back `qm config` afterward -- the result's "
        "fully_verified field is only true if every single step was "
        "independently confirmed, not just attempted without error."
    ),
    target="proxmox", mutating=True,
    input_schema={
        "type": "object",
        "properties": {
            "target_vmid": {"type": "integer"},
            "display_name": {"type": "string"},
            "memory_mb": {"type": "integer"},
            "cores": {"type": "integer"},
            "qcow2_path": {"type": "string"},
            "storage_id": {"type": "string"},
            "esxi_vmid": {"type": "integer"},
            "esxi_source_path": {"type": "string"},
            "esxi_host": {"type": "string"},
            "pool": {"type": ["string", "null"]},
            "disk_bus": {"type": "string", "default": "sata0"},
            "nic_model": {"type": "string", "default": "e1000"},
            "bridge": {"type": "string", "default": "vmbr0"},
        },
        "required": [
            "target_vmid", "display_name", "memory_mb", "cores", "qcow2_path",
            "storage_id", "esxi_vmid", "esxi_source_path", "esxi_host",
        ],
    },
    handler=_h_register_vm,
))

_register(ToolSchema(
    name="qm_config_readback",
    description="Read-only. Returns the live `qm config <vmid>` state as a dict, for independent verification at any point.",
    target="proxmox", mutating=False,
    input_schema={
        "type": "object",
        "properties": {"target_vmid": {"type": "integer"}},
        "required": ["target_vmid"],
    },
    handler=_h_qm_config_readback,
))

_register(ToolSchema(
    name="vmid_exists",
    description="Read-only. Checks whether a target VMID is already registered on Proxmox.",
    target="proxmox", mutating=False,
    input_schema={
        "type": "object",
        "properties": {"target_vmid": {"type": "integer"}},
        "required": ["target_vmid"],
    },
    handler=_h_vmid_exists,
))

_register(ToolSchema(
    name="pool_exists",
    description="Read-only. Checks whether a Proxmox pool already exists.",
    target="proxmox", mutating=False,
    input_schema={
        "type": "object",
        "properties": {"pool": {"type": "string"}},
        "required": ["pool"],
    },
    handler=_h_pool_exists,
))


_register(ToolSchema(
    name="start_vm",
    description="MUTATING (Proxmox only). Starts a registered VM (`qm start`). Used for boot validation.",
    target="proxmox", mutating=True,
    input_schema={
        "type": "object",
        "properties": {"target_vmid": {"type": "integer"}},
        "required": ["target_vmid"],
    },
    handler=_h_start_vm,
))

_register(ToolSchema(
    name="stop_vm",
    description=(
        "MUTATING (Proxmox only). Stops a VM (`qm stop`). Always call this after a boot "
        "observation, whether it passed, failed, or is being escalated -- never leave a "
        "VM running unattended (host RAM is a shared, finite resource across the whole node)."
    ),
    target="proxmox", mutating=True,
    input_schema={
        "type": "object",
        "properties": {"target_vmid": {"type": "integer"}},
        "required": ["target_vmid"],
    },
    handler=_h_stop_vm,
))

_register(ToolSchema(
    name="observe_boot",
    description=(
        "Read-only against Proxmox telemetry, writes only a screenshot file locally. "
        "Captures a screendump plus a before/after disk-activity reading (over "
        "settle_seconds, default 15) of a running VM. Sets likely_hung=true only when "
        "BOTH disk I/O is completely flat AND CPU is under 1% across the window -- a "
        "single flat signal alone was historically not enough (e.g. some Linux guests "
        "spend 2-3 minutes on a boot splash screen at 0% CPU while genuinely still "
        "working, distinguishable only by climbing disk reads). "
        "IMPORTANT: this tool does NOT classify what's on screen (login prompt? "
        "black screen? a specific error prompt?) -- that judgment call requires looking "
        "at the returned screenshot, which is a reasoning step, not a tool call."
    ),
    target="proxmox", mutating=False,
    input_schema={
        "type": "object",
        "properties": {
            "target_vmid": {"type": "integer"},
            "screenshot_ppm_path": {"type": "string"},
            "screenshot_png_path": {"type": ["string", "null"]},
            "settle_seconds": {"type": "integer", "default": 15},
        },
        "required": ["target_vmid", "screenshot_ppm_path"],
    },
    handler=_h_observe_boot,
))


def call_tool(name: str, args: dict) -> dict:
    """The orchestrator's ONLY entry point for taking action. If `name` isn't
    in TOOL_REGISTRY, this raises -- there is no fallback path to anything
    else.
    """
    if name not in TOOL_REGISTRY:
        raise KeyError(f"no such tool: {name!r}. Available: {sorted(TOOL_REGISTRY)}")
    return TOOL_REGISTRY[name].handler(args)
