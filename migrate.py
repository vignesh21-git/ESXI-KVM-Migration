#!/usr/bin/env python3
"""Command-line entrypoint for the ESXi -> Proxmox migration tool.

Examples:
    ./migrate.py list --esxi-host 192.168.4.90
    ./migrate.py run --esxi-vmid 17 --target-vmid 500 --pool ER-Test-Bed --dry-run
    ./migrate.py run --esxi-host 192.168.4.90 --esxi-vmid 17 --target-vmid 500 \\
        --memory-mb 2048 --cores 2 --pool ER-Test-Bed

If --esxi-host is omitted you'll be prompted for it interactively.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from migration_tool.agent.orchestrator import Orchestrator, OrchestratorConfig
from migration_tool.clients.esxi_client import ESXiClient, ESXiHost
from migration_tool.discovery import path_resolver
from migration_tool.discovery.inventory import parse_getallvms

DEFAULT_STORAGE_ID = "vm-storage"
DEFAULT_BULK_STORAGE_MOUNT = "/mnt/vm-storage"
DEFAULT_STAGING_ROOT = str(REPO_ROOT / "staging")


def _prompt(label: str) -> str:
    try:
        return input(f"{label}: ").strip()
    except EOFError:
        return ""


def _resolve_esxi_host(args: argparse.Namespace) -> str:
    if args.esxi_host:
        return args.esxi_host
    host = _prompt("ESXi host address (e.g. 192.168.4.90)")
    if not host:
        print("error: an ESXi host address is required (--esxi-host or interactive prompt)", file=sys.stderr)
        sys.exit(2)
    return host


def cmd_list(args: argparse.Namespace) -> None:
    host = _resolve_esxi_host(args)
    client = ESXiClient(ESXiHost(address=host, ssh_key_path=args.ssh_key))
    result = client.list_all_vms()
    if not result.ok:
        print(f"error listing VMs on {host}: {result.stderr.strip()}", file=sys.stderr)
        sys.exit(1)

    vms = parse_getallvms(result.stdout)
    print(f"{'VMID':>6}  {'Name':<32} {'Guest OS':<20} Annotation")
    for vm in vms:
        warn = "  (parse warning)" if vm.parse_warnings else ""
        print(f"{vm.esxi_vmid:>6}  {vm.display_name:<32} {vm.guest_os_raw:<20} {vm.annotation}{warn}")


def cmd_run(args: argparse.Namespace) -> None:
    host = _resolve_esxi_host(args)
    esxi_host = ESXiHost(address=host, ssh_key_path=args.ssh_key)

    if args.dry_run:
        client = ESXiClient(esxi_host)
        resolved = path_resolver.resolve(
            client,
            esxi_vmid=args.esxi_vmid,
            display_name=args.display_name or "",
            guest_os_raw=args.guest_os or "",
        )
        print("DRY RUN -- nothing will be copied, converted, or registered.\n")
        print(f"esxi_vmid:      {resolved.esxi_vmid}")
        print(f"display_name:   {resolved.display_name}")
        print(f"datastore:      {resolved.datastore}")
        print(f"power_state:    {resolved.power_state.value}")
        print(f"appliance_risk: {resolved.appliance_risk.value}" + (f" ({resolved.appliance_reason})" if resolved.appliance_reason else ""))
        print(f"snapshot_chain: {resolved.has_snapshot_chain}")
        print(f"resolution_ok:  {resolved.resolution_ok}")
        print("disk files:")
        for f in resolved.disk_files:
            size = f"{f.size_bytes:,} bytes" if f.size_bytes else "size unknown"
            print(f"  - {f.relative_path}  [{f.role}]  {size}")
        if resolved.parse_warnings:
            print("warnings:")
            for w in resolved.parse_warnings:
                print(f"  - {w}")
        pool_note = f" in pool {args.pool!r}" if args.pool else ""
        print(
            f"\nWould register as Proxmox VMID {args.target_vmid}{pool_note}, "
            f"{args.memory_mb}MB RAM, {args.cores} core(s), storage {args.storage_id!r}."
        )
        if not resolved.resolution_ok:
            print("\nresolution_ok is False -- a real run would escalate/stop here, not proceed.")
        return

    config = OrchestratorConfig(
        esxi_host=esxi_host,
        storage_id=args.storage_id,
        staging_root=args.staging_root,
        bulk_storage_mount=args.bulk_storage_mount,
        pool=args.pool,
    )
    orch = Orchestrator(config)
    record = orch.run_vm(
        esxi_vmid=args.esxi_vmid,
        target_vmid=args.target_vmid,
        memory_mb=args.memory_mb,
        cores=args.cores,
        display_name=args.display_name or "",
        guest_os_raw=args.guest_os or "",
    )

    print(f"\nfinal_status: {record.final_status}")
    for d in record.decisions:
        tag = f"  (known_issue: {d.known_issue_id})" if d.known_issue_id else ""
        print(f"  [{d.kind.value}] {d.reason}{tag}")
    if record.known_fixes_applied:
        print(f"known_fixes_applied: {record.known_fixes_applied}")
    if record.escalations:
        print(f"escalations: {len(record.escalations)}")

    sys.exit(0 if record.final_status == "migrated" else 1)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="migrate.py", description="ESXi -> Proxmox migration tool.")
    parser.add_argument("--esxi-host", help="ESXi host address, e.g. 192.168.4.90 (prompted if omitted)")
    parser.add_argument("--ssh-key", default=None, help="explicit SSH key path (default: ssh-agent / ~/.ssh/config)")

    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="list VMs known to the ESXi host")
    p_list.set_defaults(func=cmd_list)

    p_run = sub.add_parser("run", help="migrate one VM to Proxmox")
    p_run.add_argument("--esxi-vmid", type=int, required=True, help="VMID on the ESXi source (see `list`)")
    p_run.add_argument("--target-vmid", type=int, required=True, help="VMID to register on Proxmox")
    p_run.add_argument("--memory-mb", type=int, default=2048)
    p_run.add_argument("--cores", type=int, default=2)
    p_run.add_argument("--display-name", default=None, help="original ESXi display name (kept in the Proxmox description for audit purposes)")
    p_run.add_argument("--guest-os", default=None, help="ESXi guest OS string, e.g. ubuntuGuest (helps appliance-risk detection)")
    p_run.add_argument("--pool", default=None, help="Proxmox pool to assign the VM to")
    p_run.add_argument("--storage-id", default=DEFAULT_STORAGE_ID, help=f"Proxmox storage ID (default: {DEFAULT_STORAGE_ID})")
    p_run.add_argument("--staging-root", default=DEFAULT_STAGING_ROOT, help=f"local staging directory (default: {DEFAULT_STAGING_ROOT})")
    p_run.add_argument("--bulk-storage-mount", default=DEFAULT_BULK_STORAGE_MOUNT, help=f"mount point staging must live under (default: {DEFAULT_BULK_STORAGE_MOUNT})")
    p_run.add_argument("--dry-run", action="store_true", help="resolve the VM and print the migration plan without copying, converting, or registering anything")
    p_run.set_defaults(func=cmd_run)

    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
