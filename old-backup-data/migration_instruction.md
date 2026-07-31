CONTEXT: Execute and validate 3 remaining ESXi-to-Proxmox migration batches

## Prior work already complete (do not redo)
- MPLS_LDP_TESTBED batch (VMIDs 201-204, pool "MPLS-LDP-TESTBED") is fully
  migrated and validated on Proxmox host 192.168.2.61 ("proxmax" node).
  VM 204 (LSR-1) may still need final registration steps confirmed —
  check `qm list` and `qm config 204` first; if sata0/boot/description/
  serial0/pool are missing, complete them using the pattern below before
  moving to the new batches.
- Three migration scripts already exist and are ready to run, at:
  /mnt/vm-storage/VM/bgpv6/migrate_bgpv6.sh
  /mnt/vm-storage/VM/er-testbed/migrate_er_testbed.sh
  /mnt/vm-storage/VM/ipv6-testbed/migrate_ipv6.sh
  These scripts handle: scp copy from ESXi (with immediate rename to
  avoid filename collisions — several source VMs share identical vmdk
  names across different folders, e.g. all 4 BGPv6 Windows hosts use
  "IPv6_WIN10-HOST.vmdk", all 5 ER-TB VMs use "IPv6_RefDUT_2.vmdk",
  IPv6-Conformance and IPv6-TestBed_Cellular both use "IPv6-TestBed1.vmdk")
  and qemu-img conversion to qcow2. They do NOT handle qm registration —
  that's your job, per the pattern below.

## CRITICAL SAFETY CONSTRAINT
- 192.168.4.90 (ESXi source) is READ-ONLY. NEVER run any destructive/
  mutating command against it. Only SSH read ops and scp FROM it (already
  what the scripts do) are permitted.

## Environment
- Proxmox VE 9.2.5, node "proxmax", at 192.168.2.61.
- Storage: "vm-storage" (Proxmox storage ID, ~916GB, mounted at
  /mnt/vm-storage) — ALL migrated VM disks go here, never local-lvm.
- SSH to ESXi already works without needing an explicit -i key flag
  (default key location or ssh config already handles auth) — scripts
  reflect this, no -i needed in scp/ssh calls.
- Compute ceiling: 8c/16t, 31GB RAM total on the Proxmox host — be
  mindful of cumulative allocated RAM/cores across all registered VMs,
  even if only some are running at once.

## Task 1: Run each script, in this order, one at a time
1. cd /mnt/vm-storage/VM/mpls-ldp-testbed — confirm/complete VM 204 first
   if needed (see above).
2. cd /mnt/vm-storage/VM/bgpv6 && ./migrate_bgpv6.sh
   (This is the largest batch — includes 4 Windows 10 VMs, likely
   100GB+ each. Run inside tmux/screen so a dropped connection doesn't
   kill a multi-hour transfer. Check df -h /mnt/vm-storage has enough
   headroom before starting — do NOT let / (pve-root, only ~96GB) get
   used for anything; that filesystem filled to 100% once already
   during manual testing and caused failures.)
3. cd /mnt/vm-storage/VM/er-testbed && ./migrate_er_testbed.sh
4. cd /mnt/vm-storage/VM/ipv6-testbed && ./migrate_ipv6.sh

Monitor each script's output for errors (scp failures, insufficient
disk space, qemu-img conversion errors) before proceeding to registration
for that batch. If a script fails partway, diagnose before re-running
the whole thing — check what already succeeded (ls the target dir) to
avoid re-copying files that already transferred successfully.

## Task 2: After each script's conversions complete, register the VMs
## on Proxmox using this exact pattern (validated, don't deviate):

Create the pool first (once per testbed, skip if already exists):
  pvesh create /pools --poolid BGPv6
  pvesh create /pools --poolid ER-Test-Bed
  pvesh create /pools --poolid IPv6-Test-Bed

VMID ranges to use:
  BGPv6         -> 400-409
  ER Test Bed   -> 500-509
  IPv6 Test Bed -> 600-609

For each VM, using ABSOLUTE paths always (relative path + shell cwd
issues caused repeated failures during manual testing):

  qm create <vmid> --name <sanitized-name> --memory <MB> --cores <N> \
      --net0 e1000,bridge=vmbr0
  qm importdisk <vmid> /mnt/vm-storage/VM/<category>/<vm>.qcow2 vm-storage
  qm set <vmid> --sata0 vm-storage:<vmid>/vm-<vmid>-disk-0.raw
    (NOTE: qm importdisk always converts to .raw regardless of qcow2
    input — always reference .raw in the sata0 line, never .qcow2)
  qm set <vmid> --boot order=sata0
  qm set <vmid> --description "Original ESXi name: <original-name>
    (VMID <esxi-vmid>, path: <original-esxi-path>)"
  qm set <vmid> --serial0 socket
  pvesh set /pools/<POOLNAME> -vms <vmid>

Naming rule: qm create --name requires DNS-valid format — NO
underscores allowed. Sanitize any "_" to "-" (e.g. ESXi "Debian-BGPV6"
stays as-is since no underscore, but e.g. hypothetical "Test_VM" would
become "Test-VM"). Always preserve the TRUE original ESXi name in the
--description field regardless of sanitization.

Use e1000 NIC + sata0 disk bus for ALL VMs in this migration (not
virtio) — we are NOT running virt-v2v, so no driver injection happens,
and virtio guests will fail to boot without injected drivers. This is
a deliberate validated tradeoff (lower throughput, reliable boot).

RAM/vCPU allocation — use the values from ESXi inventory. If not known
for a given VM, reasonable defaults for this class of test-lab VM are
1 vCPU / 1024-2048MB for small Linux utility VMs, 2 vCPU / 2048-4096MB
for router/conformance nodes, and check actual Windows 10 host specs
from vSphere client (likely 2 vCPU / 8192MB based on similar VMs seen
in this environment) — do not under-allocate Windows guests.

## Task 3: Validate every single VM after registration — MANDATORY,
## do not skip, do not batch multiple boots without checking each one

For each VM after registration:
  qm start <vmid>
  qm terminal <vmid>
  (Ctrl+O to exit the terminal session)

Confirm the VM actually boots to a login prompt (username/password
prompt visible, or for Windows, confirm it reaches the Windows boot
screen / login screen via the Proxmox web UI Console tab instead of
qm terminal, since Windows won't have a serial getty by default).

For EACH VM, record and report back:
  - VM name (both Proxmox sanitized name and original ESXi name)
  - VMID
  - Boot result: reached login prompt (yes/no), or specific error/hang
    point if it didn't boot cleanly
  - Any anomalies (e.g. wrong hostname shown, network interface not
    coming up, unexpected fsck/repair prompts, Windows showing a
    "hardware changed" message which is expected/normal after a SATA
    bus change and just needs to boot through it once)

If a VM fails to boot: do not just move on silently. Try basic
troubleshooting (check qm config for obvious misconfig, check the
qcow2 file isn't corrupt/truncated via qemu-img check), but if it's
not a quick fix, flag it clearly in your final report as "needs manual
review" and continue with the remaining VMs rather than blocking the
whole batch on one problem VM.

## Task 4: Maintain the directory/pool structure exactly as follows
## (this mirrors the source ESXi folder tree, done Proxmox-natively
## since Proxmox storage doesn't support arbitrary category folders
## for registered VM disks — the images/<vmid>/ path is enforced by
## Proxmox itself and cannot be changed)

Staging/conversion folders (cosmetic organization, already exist):
  /mnt/vm-storage/VM/bgpv6/         <- BGPv6 category
  /mnt/vm-storage/VM/er-testbed/    <- ER Test Bed category
  /mnt/vm-storage/VM/ipv6-testbed/  <- IPv6 Test Bed category
  /mnt/vm-storage/VM/mpls-ldp-testbed/  <- already done

After registration + validation, clean up the raw vmdk/-flat.vmdk
source copies in each category folder (scripts already do this via
rm -f *-src.vmdk *-src-flat.vmdk) but LEAVE the .qcow2 files in place
even after qm importdisk — they're small relative to the raw copies
already deleted and serve as a local backup/reference outside Proxmox's
own image store, useful for audit trail.

Proxmox Pools (real grouping mechanism, visible in web UI via
Server View -> Pool View):
  MPLS-LDP-TESTBED (already exists, has 201-204)
  BGPv6 (create if not exists)
  ER-Test-Bed (create if not exists)
  IPv6-Test-Bed (create if not exists)

Verify final state with:
  qm list
  pvesh get /pools/BGPv6
  pvesh get /pools/ER-Test-Bed
  pvesh get /pools/IPv6-Test-Bed

## Final deliverable
A summary table (VM name, VMID, pool, boot status, any notes) covering
all VMs across all three batches, plus explicit callout of any VMs that
failed validation and need manual follow-up.
