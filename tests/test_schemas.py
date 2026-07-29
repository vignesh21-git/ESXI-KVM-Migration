"""Verifies the agent-facing schema layer: dict-in/dict-out round-tripping,
tool registry completeness, and that call_tool() is the only entry point
(no bypass). Run directly: `python3 tests/test_schemas.py`
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tool import schemas
from tool.esxi_client import ESXiClient

FAILURES = []


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    if not condition:
        FAILURES.append(label)


# --------------------------------------------------------------------------- #
# Registry sanity
# --------------------------------------------------------------------------- #
expected_tools = {
    "esxi_list_all_vms", "esxi_resolve_vm_path", "esxi_disk_usage_kb", "detect_collisions",
    "check_staging_capacity", "stage_vm_disk", "convert_vm_disk",
    "register_vm", "qm_config_readback", "vmid_exists", "pool_exists",
    "start_vm", "stop_vm", "observe_boot",
}
check("registry contains all expected tools", expected_tools.issubset(set(schemas.TOOL_REGISTRY)))

esxi_tools = [s for s in schemas.TOOL_REGISTRY.values() if s.target == "esxi"]
check("every esxi-target tool is marked non-mutating",
      all(not s.mutating for s in esxi_tools))
check("at least esxi_list_all_vms and esxi_resolve_vm_path are esxi-target",
      {"esxi_list_all_vms", "esxi_resolve_vm_path"}.issubset({s.name for s in esxi_tools}))

# No schema handler reaches for anything outside the tool.* package (i.e. no
# handler shells out directly instead of going through Phase 1 modules).
import inspect
for name, schema in schemas.TOOL_REGISTRY.items():
    src = inspect.getsource(schema.handler)
    check(f"{name}: handler does not call subprocess directly", "subprocess" not in src)

# to_llm_tool_def() shape, since Phase 4 will feed this straight to an API
sample = schemas.TOOL_REGISTRY["esxi_resolve_vm_path"].to_llm_tool_def()
check("to_llm_tool_def has name/description/input_schema keys",
      set(sample.keys()) == {"name", "description", "input_schema"})

# --------------------------------------------------------------------------- #
# call_tool: unknown tool name raises rather than silently no-op-ing
# --------------------------------------------------------------------------- #
try:
    schemas.call_tool("esxi_delete_vm", {})
    check("call_tool rejects unknown/nonexistent tool name", False)
except KeyError:
    check("call_tool rejects unknown/nonexistent tool name", True)

# --------------------------------------------------------------------------- #
# detect_collisions round trip: build ResolvedPath dataclasses, run them
# through esxi_resolve_vm_path's own JSON shape manually (since we have no
# live ESXi host in this test), then through call_tool("detect_collisions").
# --------------------------------------------------------------------------- #
from tool.types import ApplianceRisk, DiskFile, PowerState, ResolvedPath

rp1 = ResolvedPath(
    esxi_vmid=402, display_name="Host-D", datastore="Data_Storage",
    vmx_relative_path="Host4/Host4.vmx",
    disk_files=[
        DiskFile(relative_path="Host4/IPv6_WIN10-HOST.vmdk", role="base_descriptor"),
        DiskFile(relative_path="Host4/IPv6_WIN10-HOST-flat.vmdk", role="base_flat"),
    ],
    has_snapshot_chain=False, power_state=PowerState.OFF,
    appliance_risk=ApplianceRisk.NONE, appliance_reason=None, resolution_ok=True,
)
rp2 = ResolvedPath(
    esxi_vmid=403, display_name="Host-F", datastore="Data_Storage",
    vmx_relative_path="Host-2/Host-2.vmx",
    disk_files=[
        DiskFile(relative_path="Host-2/IPv6_WIN10-HOST.vmdk", role="base_descriptor"),
        DiskFile(relative_path="Host-2/IPv6_WIN10-HOST-flat.vmdk", role="base_flat"),
    ],
    has_snapshot_chain=False, power_state=PowerState.OFF,
    appliance_risk=ApplianceRisk.NONE, appliance_reason=None, resolution_ok=True,
)

rp1_json = schemas._to_jsonable(rp1)
rp2_json = schemas._to_jsonable(rp2)
check("_to_jsonable output is plain dict (no Enum objects leaking through)",
      isinstance(rp1_json["power_state"], str) and isinstance(rp1_json["appliance_risk"], str))

result = schemas.call_tool("detect_collisions", {"resolved_vms": [rp1_json, rp2_json]})
check("call_tool('detect_collisions') round trip detects the real collision",
      result["has_collisions"] and result["collisions"].get("IPv6_WIN10-HOST.vmdk") == [402, 403])

# Reconstruction helper used internally by stage_vm_disk etc.
reconstructed = schemas._resolved_path_from_dict(rp1_json)
check("_resolved_path_from_dict reconstructs an equivalent ResolvedPath",
      reconstructed.esxi_vmid == 402 and reconstructed.power_state == PowerState.OFF
      and len(reconstructed.disk_files) == 2)

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
    sys.exit(1)
else:
    print("All schema layer sanity checks passed.")
