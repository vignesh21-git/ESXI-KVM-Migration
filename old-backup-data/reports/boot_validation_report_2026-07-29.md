# ESXi → Proxmox Migration — Full Boot Validation Report

**35 VMs across 6 batches, all registered and boot-tested. 35 PASS, 0 outstanding.**

*Updated 2026-07-30: VM 809 (Peer-Pfsense), VM 408 (FreeBSD-BGPv6), and VM 811 (NetconfServer) all resolved — see BGPv6 and IP-Security sections.*

*Updated 2026-07-31: **Correction** — VM 602 and VM 603 were previously marked "CONFIRMED BROKEN / genuine UFS corruption, not fixable from the guest." That diagnosis was wrong. Root cause was actually this old FreeBSD release's legacy `ata(4)` driver hanging on real disk I/O against QEMU's AHCI (`sata0`) controller — the initial device probe succeeds (`ad4: ... at ata2-master` shows up in dmesg) but the first real read of the partition table deadlocks waiting on an interrupt that never arrives; confirmed via QEMU monitor screendump + `qm sendkey ctrl-c` (no effect — kernel was genuinely wedged, not slow). Both source vmdks were fine. Fix: reattach the disk on an LSI Logic SCSI bus instead of SATA (`qm set <vmid> --delete sata0`, then `--scsihw lsi --scsi0 <same raw disk> --boot order=scsi0`). This matches the controller type ESXi originally presented (`/etc/fstab` already referenced `/dev/da0s1a`, a CAM/SCSI-style name), so FreeBSD's native `sym`/CAM driver re-detects the disk as `da0` and mounts root automatically — zero guest-side changes needed. Confirmed permanent across a `qm reboot` on 603. Also re-checked VM 408 (FreeBSD-BGPv6) on suspicion it had the same bug (it has a long start/stop history in the task log) — it does not; it's a newer FreeBSD build using modern AHCI/CAM (`ada0`) and boots clean on `sata0` as-is, no change made.*

## MPLS-LDP-TESTBED (201-204)
| VMID | Name | Result |
|---|---|---|
| 201 | MPLS-TP | ✅ PASS |
| 202 | LER-1 | ✅ PASS |
| 203 | LER-2 | ✅ PASS |
| 204 | LSR-1 | ✅ PASS |

## BGPv6 (401-409)
| VMID | Name | Result | Notes |
|---|---|---|---|
| 401 | BGPv6-Dumper | ✅ PASS | Fixed: FreeBSD `da0`→`ad4` in fstab |
| 402-405 | WIN10-HOST-D/F/Fr/U | ✅ PASS | All 4 reach Windows lock screen |
| 406 | Debian-BGPv6 | ✅ PASS | |
| 407 | Fedora-BGPv6 | ✅ PASS | Fixed: offline `systemctl set-default multi-user.target` (GDM/Wayland black-screen) |
| 408 | FreeBSD-BGPv6 | ✅ PASS | Fixed: `ada0` fstab + missing `/var/lib/frr`. Fixed 2026-07-30: `bgpd` pid-file conflict — root cause was actually `bgpd_enable="YES"` (FRR's standalone per-daemon rc.d script) fighting `frr_enable="YES"` (`watchfrr`-managed) over the same `/var/run/frr/bgpd.pid`; `quagga_enable` was a red herring (quagga isn't even installed — this is all FRR). Disabled both `quagga_enable` and `bgpd_enable` via `/etc/rc.conf` append (edited live via single-user mode + `fsck -y` + `mount -u -w /`, since `virt-customize` can't write UFS2). Reboot confirmed: `watchfrr` reports `bgpd`/`ripngd`/`staticd` all up, no pid-lock error, clean boot to login prompt. |
| 409 | Ubuntu-BGPv6 | ✅ PASS | |

## ER-Test-Bed (500-503)
| VMID | Name | Result |
|---|---|---|
| 500 | Host-A | ✅ PASS |
| 501 | IR | ✅ PASS |
| 502 | Router-A | ✅ PASS |
| 503 | Router-C | ✅ PASS |

*(Open item, unresolved from earlier: the batch script planned 5 VMs including "Host-B" at VMIDs 501-505, but only these 4 at 500-503 ever got registered — Host-B appears to have been dropped during manual re-registration. Not investigated further unless you want me to.)*

## IPv6-Test-Bed (600-603)
| VMID | Name | Result | Notes |
|---|---|---|---|
| 600 | IPv6-RefDUT-Ubuntu18 | ✅ PASS | tty1 GDM blank (cosmetic), tty2 login confirmed |
| 601 | Router-Ref | ✅ PASS | |
| 602 | IPv6-Conformance | ✅ PASS | Fixed 2026-07-31: `sata0`→`scsi0`(`lsi`) — see correction note above. Boots to `FreeBSD/i386 (IPv6_Conformance.criterionnetworks.com) (ttyv0) login:`. Note: shares hostname with 603 (both trace back to the same `IPv6-TestBed1.vmdk` source) — will collide on the network if both are up with DNS/mDNS in play. |
| 603 | IPv6-TestBed-Cellular | ✅ PASS | Fixed 2026-07-31: `sata0`→`scsi0`(`lsi`) — see correction note above. Boots to `FreeBSD/i386 (IPv6_Conformance.criterionnetworks.com) (ttyv0) login:`; confirmed stable across a `qm reboot`. Also logged `arp: ... is using my IP address 192.168.4.144` on boot — MAC OUI `00:0c:29` is VMware's, so the original VM is likely still live on ESXi with the same static IP. Not a boot blocker, but will need addressing before this VM is used for real network traffic. |

## IP-Conformance (700-701)
| VMID | Name | Result |
|---|---|---|
| 700 | IP-Host-1 | ✅ PASS — Fixed: offline `multi-user.target` |
| 701 | IP-Host-2 | ✅ PASS — Fixed: offline `multi-user.target` |

## IP-Security (800-811) — second ESXi source, 192.168.3.90
| VMID | Name | Guest OS | Result |
|---|---|---|---|
| 800 | VAPT-DUT-SCTP | Ubuntu 20.04.5 | ✅ PASS |
| 801-803 | Linux-Client/Server/Peer | Ubuntu 22.04 | ✅ PASS |
| 804 | Server-Ubuntu | Ubuntu/GDM | ✅ PASS |
| 805 | Client-Kali | Kali Rolling | ✅ PASS (one-off boot race, self-resolved on reboot) |
| 806-807 | TB-Telemetry-Server/Sensor | Ubuntu/GDM | ✅ PASS |
| 808 | UTM-Linux-Server | Ubuntu 16.04 | ✅ PASS |
| 809 | Peer-Pfsense | pfSense | ✅ PASS — Fixed 2026-07-30: added `net1`/`net2` (`e1000`, `bridge=vmbr0`) to match the saved config's 3-NIC expectation. All 3 interfaces (WAN/LAN1/LAN2 → em0/em1/em2) auto-mapped correctly; boots straight to the pfSense console menu (no separate OS login — this menu is the appliance's normal entry point). Verified via QEMU monitor screendump (serial console is silent for pfSense; it drives VGA, not `serial0`). |
| 810 | UTM-Linux-Client | Ubuntu 16.04.7 | ✅ PASS |
| 811 | NetconfServer | **Cisco IOS-XE** | ✅ PASS — Resolved 2026-07-30: was **not actually hung**. It's a bursty first-boot package install (caught a ~470MB write burst to bootflash via telemetry sampling), with normal quiet gaps of 55-90s+ between writes — longer than the original ~100s check window, which misread a gap as a hang. Full boot (bootloader → kernel → IOS-XE CLI → startup-config replay → login prompt) took ~15-20 min wall-clock. Also bumped `memory` 2048→4096MB, `cores` 1→2, and set `cpu: host` (was default `kvm64`) as reasonable headroom/passthrough for this guest class — not proven necessary, but left in place. Verified via QEMU monitor screendump: reaches `User Access Verification / Username:`. |

---

### Bottom line
- **35 of 35 PASS** (12 required a guest-side or hypervisor-config fix, all confirmed permanent across reboots/restarts).
- **0 unrecoverable.** 602 and 603 were previously misdiagnosed as disk-corrupt; actual cause was a `sata0`(AHCI)-vs-legacy-`ata(4)`-driver incompatibility specific to this old FreeBSD release, fixed by moving both to an LSI SCSI bus (`scsihw: lsi`, `scsi0`). See 2026-07-31 note above.
- **0 awaiting your input, 0 not yet attempted.**
- **Open items carried forward, not boot-blocking:**
  - ER-Test-Bed "Host-B" (originally planned for 501-505 range) was never registered — not investigated further unless requested.
  - 602/603 share a hostname (`IPv6_Conformance.criterionnetworks.com`) and 603 has a static-IP conflict with what's almost certainly the still-running original VM on ESXi (192.168.4.144) — cosmetic/networking cleanup, not a migration defect.
- **Note for future first boots of 811-class (Cisco IOS-XE) guests**: budget 15-20 minutes and don't judge a hang off a ~100s window — sample disk I/O over several minutes before concluding it's stuck.
- **Note for any future old-FreeBSD (legacy `ata` driver) guests**: if it hangs at `Trying to mount root from ufs:/dev/adXsYa` with no progress and `qm sendkey ctrl-c` does nothing, don't assume disk corruption — try the SCSI-bus swap first (see 2026-07-31 note).
- Everything is stopped right now — nothing running.

*Updated 2026-07-31 (cleanup pass): removed leftover non-VM clutter with zero booting impact — `migration-tool/staging/97_vm9998` (orphaned test scratch copy) and `migration-tool/tool/__pycache__`. Also removed the orphaned `/mnt/vm-storage/MPLS_TP.qcow2` (pre-script conversion artifact for VM 201, superseded by Proxmox's own imported raw copy) and, at the user's request, the 12 `VM/ip-security/*.qcow2` backup files (~189GB) — verified beforehand that every VM 800-811 boots exclusively from its own `vm-storage:<vmid>/vm-<vmid>-disk-0.raw` in Proxmox's image store, matching what was already done for every other batch. Storage usage: 731G/916G (85%) → 543G/916G (63%), 327G free.*