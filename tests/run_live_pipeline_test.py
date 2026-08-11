"""Live pipeline test: runs the REAL end-to-end orchestrator loop (resolve ->
capacity -> copy -> convert -> register, all through the actual tool layer,
not mocked) against one small, already-known, low-risk VM of your choosing --
this run targets a separate throwaway VMID and does not touch the source
VM's existing registration (if it has one).

Deliberately NOT part of the tests/test_*.py suite (those must stay fast and
infrastructure-free) -- run manually against YOUR OWN ESXi/Proxmox
environment, e.g.:

    python3 tests/run_live_pipeline_test.py \\
        --esxi-host 192.168.4.90 --esxi-vmid 17 --display-name Host-A \\
        --guest-os ubuntuGuest --target-vmid 9999

--esxi-host and --esxi-vmid are required and have no defaults on purpose:
this repo is public and there is no ESXi host/VMID that's meaningfully
"default" for an arbitrary clone of it. Pick a small, disposable, low-risk
VM from your own `./migrate.py --esxi-host <host> list` output.

--target-vmid defaults to 9999 and --pool to "Migration-Tool-Test" so a run
with no other flags lands somewhere obviously throwaway -- but you're
expected to confirm those VMIDs/pool names don't collide with anything real
in your own environment before running this.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from migration_tool.clients.esxi_client import ESXiHost
from migration_tool.agent.orchestrator import Orchestrator, OrchestratorConfig
from migration_tool.clients import proxmox_client as px

DEFAULT_TARGET_VMID = 9999
DEFAULT_POOL = "Migration-Tool-Test"
DEFAULT_STORAGE_ID = "vm-storage"
DEFAULT_STAGING_ROOT = str(Path(__file__).resolve().parent.parent / "staging")
DEFAULT_BULK_STORAGE_MOUNT = "/mnt/vm-storage"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the real migration pipeline end-to-end against a disposable target VMID.",
    )
    parser.add_argument("--esxi-host", required=True, help="ESXi host address, e.g. 192.168.4.90")
    parser.add_argument("--esxi-vmid", type=int, required=True, help="VMID on the ESXi source (see `./migrate.py --esxi-host <host> list`)")
    parser.add_argument("--ssh-key", default=None, help="explicit SSH key path (default: ssh-agent / ~/.ssh/config)")
    parser.add_argument("--target-vmid", type=int, default=DEFAULT_TARGET_VMID, help=f"disposable Proxmox VMID to register into (default: {DEFAULT_TARGET_VMID})")
    parser.add_argument("--pool", default=DEFAULT_POOL, help=f"Proxmox pool to assign into (default: {DEFAULT_POOL!r})")
    parser.add_argument("--display-name", default="", help="original ESXi display name, for the Proxmox description")
    parser.add_argument("--guest-os", default="", help="ESXi guest OS string, e.g. ubuntuGuest (helps appliance-risk detection)")
    parser.add_argument("--memory-mb", type=int, default=1024)
    parser.add_argument("--cores", type=int, default=1)
    parser.add_argument("--storage-id", default=DEFAULT_STORAGE_ID, help=f"Proxmox storage ID (default: {DEFAULT_STORAGE_ID})")
    parser.add_argument("--staging-root", default=DEFAULT_STAGING_ROOT, help=f"local staging directory (default: {DEFAULT_STAGING_ROOT})")
    parser.add_argument("--bulk-storage-mount", default=DEFAULT_BULK_STORAGE_MOUNT, help=f"mount point staging must live under (default: {DEFAULT_BULK_STORAGE_MOUNT})")
    return parser


def main() -> None:
    args = build_parser().parse_args()

    config = OrchestratorConfig(
        esxi_host=ESXiHost(address=args.esxi_host, ssh_key_path=args.ssh_key),
        storage_id=args.storage_id,
        staging_root=args.staging_root,
        bulk_storage_mount=args.bulk_storage_mount,
        pool=args.pool,
    )
    orch = Orchestrator(config)

    print(f"Running full pipeline: ESXi vmid={args.esxi_vmid} ({args.display_name or 'unnamed'}) -> target Proxmox vmid={args.target_vmid}\n")

    record = orch.run_vm(
        esxi_vmid=args.esxi_vmid,
        target_vmid=args.target_vmid,
        memory_mb=args.memory_mb,
        cores=args.cores,
        display_name=args.display_name,
        guest_os_raw=args.guest_os,
    )

    print("=" * 70)
    print(f"final_status: {record.final_status}")
    print("decisions:")
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
    cfg = px.qm_config(args.target_vmid)
    for key in ("name", "sata0", "boot", "description", "serial0"):
        print(f"  {key}: {cfg.get(key, '<missing>')!r}")

    print("\n--- pool membership check ---")
    members = px.pvesh_json("get", f"/pools/{args.pool}")
    print(" ", members)

    sys.exit(0 if record.final_status == "migrated" else 1)


if __name__ == "__main__":
    main()
