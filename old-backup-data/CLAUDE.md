# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

This is not a software project — it's a working directory for a one-time ESXi-to-Proxmox VM
migration effort. It holds per-testbed shell scripts that copy VM disks off an ESXi host, convert
them to qcow2, and (in one case) register them on Proxmox. There is no build/lint/test tooling;
"running" this repo means executing these scripts against real infrastructure.

Full task context, current progress, and the exact registration/validation procedure to follow
live in `migration_instruction.md` at the repo root — read it before doing any migration work here.
It is the source of truth, not a historical note; treat its VMID ranges, pool names, and step
ordering as current instructions.

## Environment

- Source: ESXi host `192.168.4.90` — **READ-ONLY**. Never run a destructive/mutating command
  against it. Only SSH reads and `scp` copies *from* it are permitted.
- Destination: Proxmox VE 9.2.5, node `proxmax`, at `192.168.2.61`.
- Storage: Proxmox storage ID `vm-storage` (~916GB, mounted at `/mnt/vm-storage`) — all migrated
  VM disks go here, never `local-lvm`.
- Compute ceiling on the Proxmox host: 8c/16t, 31GB RAM total — stay mindful of cumulative
  allocated RAM/cores across all *registered* VMs, even ones that aren't currently running.
- `/` (pve-root) is only ~96GB and must never be used for VM disk data — it has filled to 100%
  during past manual testing and caused failures.
- SSH to the ESXi host works without an explicit `-i` key flag (default key / ssh config already
  handles auth); scripts reflect this.

## Directory layout

Each subdirectory is a "batch" corresponding to one ESXi testbed folder, and holds a migration
script plus (once converted) the resulting `.qcow2` files:

- `bgpv6/migrate_bgpv6.sh` — BGPv6 batch (9 VMs: Debian, Fedora, Ubuntu, FreeBSD, a "Dumper" VM,
  and 4 Windows 10 hosts). Largest batch — Windows disks are likely 100GB+ each.
- `er-testbed/migrate_er_testbed.sh` — ER Test Bed batch (5 VMs).
- `ipv6-testbed/migrate_ipv6.sh` — IPv6 Test Bed batch (4 VMs).
- `mpls-ldp-testbed/migrate_mpls.sh` — MPLS-LDP-TESTBED batch (3 VMs: LER-1, LER-2, LSR-1).
  Already run; this is the reference implementation for the copy+convert+register+start pattern
  the other three scripts stop short of (see below). VMIDs 201-204 are already migrated/validated
  on Proxmox; check `migration_instruction.md` for what may still be outstanding on VMID 204.

Each script only does copy-from-ESXi + qcow2 conversion — **not** Proxmox registration (`qm
create`/`importdisk`/etc.), except `migrate_mpls.sh`, which does the full pattern end-to-end
including `qm start` for validation. When running the other three scripts, registration is a
separate manual step to perform afterward, following the exact pattern in `migration_instruction.md`.

## Key conventions used by (and required of) these scripts

- **Filename collisions**: several source VMs across different ESXi folders share identical vmdk
  base filenames (e.g. all 4 BGPv6 Windows hosts are `IPv6_WIN10-HOST.vmdk`, all 5 ER-TB VMs are
  `IPv6_RefDUT_2.vmdk`). Every `scp` copy is immediately followed by a rename to a unique local
  name (`<name>-src.vmdk` / `<name>-src-flat.vmdk`), including a `sed` fixup of the descriptor
  file's internal reference to the renamed `-flat.vmdk`. Preserve this pattern for any new scripts.
- **VMID ranges by pool**: MPLS-LDP-TESTBED 201-204 (done), BGPv6 400-409, ER-Test-Bed 500-509,
  IPv6-Test-Bed 600-609.
- **`qm create --name` is DNS-valid only** — no underscores. Sanitize `_` to `-` in the Proxmox
  name, but always preserve the true original ESXi name in `qm set ... --description`.
- **Disk bus / NIC**: always `sata0` + `e1000` for these VMs, never `virtio` — no virt-v2v /
  driver injection is being done, so virtio guests won't boot without injected drivers.
- **`qm importdisk` always produces `.raw`** regardless of qcow2 input — reference `.raw` (not
  `.qcow2`) in the `--sata0` line.
- **Always use absolute paths** in `qm` invocations — relative paths + shell cwd have caused
  repeated registration failures.
- After registration and validation, the intermediate `-src.vmdk`/`-src-flat.vmdk` copies are
  deleted (scripts do this via `rm -f`), but the `.qcow2` files are kept as a local backup/audit
  trail even after `qm importdisk` has consumed them into Proxmox's own image store.
- Proxmox Pools are the real grouping mechanism (`pvesh create /pools ...`, `pvesh set
  /pools/<name> -vms <vmid>`); the category subdirectories under `/mnt/vm-storage/VM/` are just
  cosmetic staging folders and not otherwise meaningful to Proxmox.

## Running a migration batch

1. Run the batch's script from its own directory (e.g. `cd bgpv6 && ./migrate_bgpv6.sh`) — for
   large/multi-hour transfers (BGPv6 especially, with 100GB+ Windows disks), run inside
   `tmux`/`screen` so a dropped connection doesn't kill the transfer, and check `df -h
   /mnt/vm-storage` has headroom first.
2. Watch for scp failures, disk-space errors, or qemu-img conversion errors before proceeding. If
   a script fails partway, check what already succeeded (`ls` the target dir) before re-running —
   don't blindly re-copy files that already transferred.
3. Register each resulting VM on Proxmox by hand following the pattern above (pool creation is
   idempotent-checked — skip if it already exists).
4. Validate every VM individually after registration: `qm start <vmid>`, then `qm terminal
   <vmid>` (Ctrl+O to exit) for Linux/BSD guests, or the Proxmox web UI Console tab for Windows
   guests (no serial getty by default). Confirm it reaches a login prompt; note any anomalies
   (wrong hostname, network not coming up, unexpected fsck, Windows "hardware changed" prompt is
   expected after the SATA bus change). Don't skip or batch validation across multiple VMs.
5. If a VM fails to boot, do basic troubleshooting (`qm config`, `qemu-img check` on the qcow2),
   but if it's not a quick fix, flag it as "needs manual review" and continue with the rest of the
   batch rather than blocking on one VM.

## Useful verification commands

```
qm list
qm config <vmid>
pvesh get /pools/<POOLNAME>
```
