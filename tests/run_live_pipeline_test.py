"""Live pipeline test: runs the REAL end-to-end orchestrator loop (resolve ->
capacity -> copy -> convert -> register, all through the actual tool layer,
not mocked) against one small, already-known, low-risk VM (Host-A from the
ER-Test-Bed batch, previously migrated and validated as VMID 500 -- this run
targets a separate throwaway VMID and does not touch that existing
registration).

Deliberately NOT part of the tests/test_*.py suite (those must stay fast and
infrastructure-free) -- run manually: `python3 tests/run_live_pipeline_test.py`
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from migration_tool.clients.esxi_client import ESXiHost
from migration_tool.agent.orchestrator import Orchestrator, OrchestratorConfig
from migration_tool.clients import proxmox_client as px

TEST_VMID = 9999
TEST_POOL = "Migration-Tool-Test"

config = OrchestratorConfig(
    esxi_host=ESXiHost(address="192.168.4.90"),
    storage_id="vm-storage",
    staging_root="/mnt/vm-storage/VM/migration-tool/staging",
    bulk_storage_mount="/mnt/vm-storage",
    pool=TEST_POOL,
)

orch = Orchestrator(config)

print(f"Running full pipeline: ESXi vmid=17 (Host-A) -> target Proxmox vmid={TEST_VMID}\n")

record = orch.run_vm(
    esxi_vmid=17,
    target_vmid=TEST_VMID,
    memory_mb=1024,
    cores=1,
    display_name="Host-A",
    guest_os_raw="ubuntuGuest",
)

print("=" * 70)
print(f"final_status: {record.final_status}")
print(f"decisions:")
for d in record.decisions:
    print(f"  - [{d.kind.value}] {d.reason}" + (f" (known_issue: {d.known_issue_id})" if d.known_issue_id else ""))
print(f"known_fixes_applied: {record.known_fixes_applied}")
print(f"escalations: {len(record.escalations)}")
if record.copy:
    print(f"copy.ok: {record.copy['ok']}  total_bytes: {record.copy['total_bytes']}")
if record.convert:
    print(f"convert.ok: {record.convert['ok']}  size_matches: {record.convert['size_matches_expectation']}  reported_size: {record.convert['reported_virtual_size_bytes']}")
if record.register:
    print(f"register.fully_verified: {record.register['fully_verified']}")
    print(f"  created={record.register['created']} disk_imported={record.register['disk_imported']} "
          f"disk_attached={record.register['disk_attached']} boot_order_set={record.register['boot_order_set']} "
          f"description_set={record.register['description_set']} serial_console_set={record.register['serial_console_set']} "
          f"pool_assigned={record.register['pool_assigned']}")
    if record.register["errors"]:
        print(f"  errors: {record.register['errors']}")
print("=" * 70)

print("\n--- independent verification via qm config (not trusting the above) ---")
cfg = px.qm_config(TEST_VMID)
for key in ("name", "sata0", "boot", "description", "serial0"):
    print(f"  {key}: {cfg.get(key, '<missing>')!r}")

print("\n--- pool membership check ---")
members = px.pvesh_json("get", f"/pools/{TEST_POOL}")
print(" ", members)

sys.exit(0 if record.final_status == "migrated" else 1)
