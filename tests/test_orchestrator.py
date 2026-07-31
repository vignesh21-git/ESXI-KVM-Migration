"""Proves the orchestrator loop's control flow and fixed decision table, by
mocking call_tool() so every scenario runs fast and deterministically
without touching real ESXi/Proxmox infrastructure. This is deliberately
separate from tests/run_live_pipeline_test.py, which exercises the real
thing against one low-risk VM -- this file isolates loop bugs from
infrastructure/reasoning-quality concerns.

Run directly: `python3 tests/test_orchestrator.py`
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import migration_tool.agent.orchestrator as orch_mod
from migration_tool.clients.esxi_client import ESXiHost
from migration_tool.agent.orchestrator import Orchestrator, OrchestratorConfig
from migration_tool.types import DecisionKind

FAILURES = []


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    if not condition:
        FAILURES.append(label)


def _resolved(esxi_vmid=100, power_state="poweredOff", appliance_risk="none",
              appliance_reason=None, has_snapshot_chain=False, resolution_ok=True):
    return {
        "esxi_vmid": esxi_vmid, "display_name": f"vm-{esxi_vmid}", "datastore": "datastore1",
        "vmx_relative_path": f"vm{esxi_vmid}/vm{esxi_vmid}.vmx",
        "disk_files": [
            {"relative_path": f"vm{esxi_vmid}/vm{esxi_vmid}.vmdk", "role": "base_descriptor", "size_bytes": None},
            {"relative_path": f"vm{esxi_vmid}/vm{esxi_vmid}-flat.vmdk", "role": "base_flat", "size_bytes": None},
        ],
        "has_snapshot_chain": has_snapshot_chain, "power_state": power_state,
        "appliance_risk": appliance_risk, "appliance_reason": appliance_reason,
        "resolution_ok": resolution_ok, "parse_warnings": [], "raw_filelayout": "",
    }


class ScriptedToolCalls:
    """Records every call_tool invocation and returns pre-scripted responses,
    with special-cased counters for tools that need different answers on
    successive calls within one scenario (e.g. register_vm's retry).
    """
    def __init__(self, responses: dict):
        self.responses = responses
        self.calls = []
        self._register_call_count = 0

    def __call__(self, name, args):
        self.calls.append((name, args))
        if name == "register_vm" and isinstance(self.responses.get("register_vm"), list):
            idx = min(self._register_call_count, len(self.responses["register_vm"]) - 1)
            self._register_call_count += 1
            return self.responses["register_vm"][idx]
        if name not in self.responses:
            raise AssertionError(f"unscripted call_tool invocation: {name}({args})")
        return self.responses[name]


def make_orchestrator(scripted: ScriptedToolCalls) -> Orchestrator:
    orch_mod.call_tool = scripted
    config = OrchestratorConfig(
        esxi_host=ESXiHost(address="192.168.4.90"),
        storage_id="vm-storage",
        staging_root="/tmp",  # bypassed below via monkeypatch of the safety check
        bulk_storage_mount="/tmp",
        pool="Test-Pool",
    )
    return Orchestrator(config)


CONVERT_OK = {
    "ok": True, "source_descriptor": "x", "output_qcow2": "/tmp/1_vm1/vm1.qcow2",
    "reported_virtual_size_bytes": 1024, "expected_virtual_size_bytes": None,
    "size_matches_expectation": True, "error": None,
}
COPY_OK = {
    "esxi_vmid": 100, "ok": True,
    "files": [
        {"source_relative_path": "vm100/vm100-flat.vmdk", "staged_path": "/tmp/100_vm100/vm100-src-flat.vmdk", "bytes_transferred": 1000, "renamed": True},
        {"source_relative_path": "vm100/vm100.vmdk", "staged_path": "/tmp/100_vm100/vm100-src.vmdk", "bytes_transferred": 500, "renamed": True},
    ],
    "total_bytes": 1500, "error": None,
}
CAPACITY_OK = {"storage_path": "/tmp", "available_bytes": 10**12, "required_bytes": 1500, "margin_bytes": 10**12, "sufficient": True, "checked_at_utc": "now"}
DISK_USAGE_OK = {"ok": True, "error": None, "size_kb": 1}
REGISTER_FULLY_VERIFIED = {
    "target_vmid": 900, "proxmox_name": "vm-900", "created": True, "disk_imported": True,
    "disk_attached": True, "boot_order_set": True, "description_set": True,
    "serial_console_set": True, "pool_assigned": True, "fully_verified": True, "errors": [],
}


# --------------------------------------------------------------------------- #
# Scenario 1: everything clean -> migrated
# --------------------------------------------------------------------------- #
scripted = ScriptedToolCalls({
    "esxi_resolve_vm_path": _resolved(esxi_vmid=100),
    "esxi_disk_usage_kb": DISK_USAGE_OK,
    "check_staging_capacity": CAPACITY_OK,
    "stage_vm_disk": COPY_OK,
    "convert_vm_disk": CONVERT_OK,
    "register_vm": REGISTER_FULLY_VERIFIED,
})
o = make_orchestrator(scripted)
record = o.run_vm(esxi_vmid=100, target_vmid=900, memory_mb=2048, cores=2, display_name="vm-100")
check("scenario1 (clean run): final_status == migrated", record.final_status == "migrated")
check("scenario1: no escalations raised", record.escalations == [])
check("scenario1: register_vm called exactly once (no retry needed)",
      sum(1 for n, _ in scripted.calls if n == "register_vm") == 1)

# --------------------------------------------------------------------------- #
# Scenario 2: appliance-risk VM -> skipped, no copy/convert/register attempted
# --------------------------------------------------------------------------- #
scripted2 = ScriptedToolCalls({
    "esxi_resolve_vm_path": _resolved(esxi_vmid=101, appliance_risk="likely_appliance",
                                       appliance_reason="matches vManage pattern"),
})
o2 = make_orchestrator(scripted2)
record2 = o2.run_vm(esxi_vmid=101, target_vmid=901, memory_mb=4096, cores=4, display_name="vManage-1")
check("scenario2 (appliance): final_status == skipped", record2.final_status == "skipped")
check("scenario2: no copy/convert/register tools were ever called",
      not any(n in ("stage_vm_disk", "convert_vm_disk", "register_vm") for n, _ in scripted2.calls))
check("scenario2: decision recorded with the appliance known_issue_id",
      any(d.known_issue_id == "vendor_appliance_unsupported" for d in record2.decisions))

# --------------------------------------------------------------------------- #
# Scenario 3: powered-on VM -> escalate, ask_human says "skip"
# --------------------------------------------------------------------------- #
scripted3 = ScriptedToolCalls({
    "esxi_resolve_vm_path": _resolved(esxi_vmid=102, power_state="poweredOn"),
})
asked = []
def fake_ask_human(question, options, context):
    asked.append(question)
    return "skip"
orch_mod.call_tool = scripted3
config3 = OrchestratorConfig(
    esxi_host=ESXiHost(address="192.168.4.90"), storage_id="vm-storage",
    staging_root="/tmp", bulk_storage_mount="/tmp",
)
o3 = Orchestrator(config3, ask_human=fake_ask_human)
record3 = o3.run_vm(esxi_vmid=102, target_vmid=902, memory_mb=2048, cores=1, display_name="maybe-running")
check("scenario3 (powered on): escalation was raised", len(record3.escalations) == 1)
check("scenario3: escalation question mentions the powered-on state", "power_state" in asked[0])
check("scenario3: final_status == skipped (human said skip)", record3.final_status == "skipped")
check("scenario3: never reached the copy stage", not any(n == "stage_vm_disk" for n, _ in scripted3.calls))

# --------------------------------------------------------------------------- #
# Scenario 4: disk attachment silent-failure -> auto-retry -> succeeds on 2nd try
# --------------------------------------------------------------------------- #
REGISTER_ATTACH_FAILED = {
    **REGISTER_FULLY_VERIFIED, "disk_attached": False, "boot_order_set": False,
    "fully_verified": False, "errors": ["disk attachment could not be verified"],
}
scripted4 = ScriptedToolCalls({
    "esxi_resolve_vm_path": _resolved(esxi_vmid=103),
    "esxi_disk_usage_kb": DISK_USAGE_OK,
    "check_staging_capacity": CAPACITY_OK,
    "stage_vm_disk": COPY_OK,
    "convert_vm_disk": CONVERT_OK,
    "register_vm": [REGISTER_ATTACH_FAILED, REGISTER_FULLY_VERIFIED],  # fails once, then succeeds
})
o4 = make_orchestrator(scripted4)
record4 = o4.run_vm(esxi_vmid=103, target_vmid=903, memory_mb=2048, cores=1, display_name="flaky-attach")
check("scenario4 (retry-then-success): final_status == migrated", record4.final_status == "migrated")
check("scenario4: register_vm called exactly twice (initial + retry)",
      sum(1 for n, _ in scripted4.calls if n == "register_vm") == 2)
check("scenario4: known_fixes_applied records the retry",
      "disk_attachment_not_verified" in record4.known_fixes_applied)
check("scenario4: no human escalation needed (auto-fix succeeded)", record4.escalations == [])

# --------------------------------------------------------------------------- #
# Scenario 5: disk attachment fails BOTH times -> escalate, don't retry forever
# --------------------------------------------------------------------------- #
scripted5 = ScriptedToolCalls({
    "esxi_resolve_vm_path": _resolved(esxi_vmid=104),
    "esxi_disk_usage_kb": DISK_USAGE_OK,
    "check_staging_capacity": CAPACITY_OK,
    "stage_vm_disk": COPY_OK,
    "convert_vm_disk": CONVERT_OK,
    "register_vm": [REGISTER_ATTACH_FAILED, REGISTER_ATTACH_FAILED],
})
asked5 = []
def fake_ask_human5(question, options, context):
    asked5.append(question)
    return "abort"
orch_mod.call_tool = scripted5
o5 = Orchestrator(config3, ask_human=fake_ask_human5)
record5 = o5.run_vm(esxi_vmid=104, target_vmid=904, memory_mb=2048, cores=1, display_name="persistently-flaky")
check("scenario5 (persistent failure): register_vm called exactly twice, not more",
      sum(1 for n, _ in scripted5.calls if n == "register_vm") == 2)
check("scenario5: escalated after the retry also failed", len(asked5) == 1)
check("scenario5: final_status is needs_human (abort != skip)", record5.final_status == "needs_human")

# --------------------------------------------------------------------------- #
# Scenario 6: capacity insufficient -> escalate before any copy is attempted
# --------------------------------------------------------------------------- #
CAPACITY_BAD = {"storage_path": "/tmp", "available_bytes": 100, "required_bytes": 10**9, "margin_bytes": -999999900, "sufficient": False, "checked_at_utc": "now"}
scripted6 = ScriptedToolCalls({
    "esxi_resolve_vm_path": _resolved(esxi_vmid=105),
    "esxi_disk_usage_kb": DISK_USAGE_OK,
    "check_staging_capacity": CAPACITY_BAD,
})
orch_mod.call_tool = scripted6
o6 = Orchestrator(config3, ask_human=lambda question, options, context: "abort")
record6 = o6.run_vm(esxi_vmid=105, target_vmid=905, memory_mb=2048, cores=1, display_name="too-big")
check("scenario6 (insufficient capacity): never called stage_vm_disk",
      not any(n == "stage_vm_disk" for n, _ in scripted6.calls))
check("scenario6: escalation known_issue_id is insufficient_staging_capacity",
      any(d.known_issue_id == "insufficient_staging_capacity" for d in record6.decisions))

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
    sys.exit(1)
else:
    print("All orchestrator loop sanity checks passed.")
