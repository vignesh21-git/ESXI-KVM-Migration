# ESXi → Proxmox Migration Tool

Automates the ESXi-to-Proxmox migration pipeline: resolve a VM's real disk
path on ESXi, check staging capacity, copy the disk off, convert it to
qcow2, and register it as a VM on Proxmox — with every step independently
verified rather than trusted from a command's exit code alone.

Two structurally separate layers:

1. **Deterministic tool layer** (`src/migration_tool/clients`, `discovery`,
   `pipeline`) — narrow functions that actually touch ESXi/Proxmox, each
   returning an explicit typed result (never a bare success/fail boolean for
   anything multi-step).
2. **Agentic orchestrator** (`src/migration_tool/agent/orchestrator.py`) — an
   observe/assess/decide/act/verify/record loop whose only means of taking
   action is `src/migration_tool/agent/schemas.py`'s `call_tool()`. It cannot
   construct a shell command or SSH invocation itself.

## Deployment model

Runs **locally on the Proxmox host**, not on a laptop or inside a guest VM.
Disk data flows ESXi → local staging → Proxmox-managed storage in one hop;
`qm`/`pvesh` are native host binaries; long transfers survive an operator's
connection dropping because nothing depends on a remote client staying
attached.

No pip/venv required — everything is Python 3.9+ standard library
(`subprocess`, `dataclasses`, `re`, `json`), shelling out to the native
`ssh`/`scp`/`qemu-img`/`qm`/`pvesh` binaries. No `pyproject.toml`/`setup.py`
either — it's run straight out of `src/` via `PYTHONPATH`, not installed.

## SSH access to the ESXi host

The tool shells out to `ssh`/`scp` against the ESXi host directly from the
Proxmox host — there's no separate credential store, so key-based auth needs
to already work before running anything. For a new ESXi host, copy the
Proxmox host's key over once:

```bash
ssh-copy-id root@<esxi-host-ip>
```

If `ssh-copy-id` isn't available, append the Proxmox host's
`~/.ssh/id_rsa.pub` to `/etc/ssh/keys-root/authorized_keys` on the ESXi host
manually — ESXi doesn't use the standard `~/.ssh/authorized_keys` path.
Once set up, `ssh root@<esxi-host-ip>` should succeed with no password
prompt, and `--ssh-key` can be left out entirely (or pointed at a non-default
key explicitly, e.g. `--ssh-key ~/.ssh/id_ed25519`).

## How to run

**List VMs on the ESXi source** (read-only, safe anytime):

```bash
./migrate.py --esxi-host 192.168.4.90 list 
```

**Preview a migration without touching anything** (`--dry-run` resolves the
VM's real disk path, power state, and appliance risk, and prints what a real
run would do — no copy, convert, or register):

```bash
./migrate.py --esxi-host 192.168.4.90 run --esxi-vmid 17 --target-vmid 500 \
    --pool ER-Test-Bed --dry-run
```

**Run it for real:**

```bash
./migrate.py --esxi-host 192.168.4.90 run --esxi-vmid 17 --target-vmid 500 \
    --memory-mb 2048 --cores 2 --pool ER-Test-Bed
```

If `--esxi-host` is omitted you'll be prompted for it interactively. Run
`./migrate.py --help` / `./migrate.py run --help` for the full flag list
(`--storage-id`, `--staging-root`, `--bulk-storage-mount`, `--guest-os`,
`--display-name`, `--ssh-key`, etc.) — all default sensibly, so a first run
usually only needs `--esxi-host`, `--esxi-vmid`, and `--target-vmid`.

The orchestrator escalates anything it can't auto-decide (appliance risk,
a powered-on source VM, insufficient capacity, a registration step that
didn't verify) via a blocking CLI prompt rather than guessing — watch for
that when running a real migration.

**Run the fast test suite** (no infrastructure touched):

```bash
export PYTHONPATH="$PWD/src"
for f in tests/test_*.py; do python3 "$f"; done
```

**Run the real pipeline against live infrastructure** (a smoke test that
targets a disposable VMID and does not touch any existing registration).
`--esxi-host`/`--esxi-vmid` are required with no defaults — pick a small,
low-risk VM from your own environment (see `./migrate.py --esxi-host <host>
list`), and confirm the target VMID/pool (defaults: `9999` /
`Migration-Tool-Test`) don't collide with anything real:

```bash
export PYTHONPATH="$PWD/src"
python3 tests/run_live_pipeline_test.py \
    --esxi-host 192.168.4.90 --esxi-vmid 17 --display-name Host-A --guest-os ubuntuGuest
```

## Structural safety guarantee

`clients/esxi_client.py` has **no write/delete method of any kind, and no
generic "run a command string" method** — its entire public API is nine
named, single-purpose read-only operations, each building its own fixed
argv. `tests/test_esxi_safety.py` verifies this by introspecting the class's
method list, not by trusting a comment. The orchestrator's action space is
exactly `agent/schemas.py`'s `TOOL_REGISTRY`, so there is no code path from
"agent decides something" to "arbitrary command against the ESXi source" —
that path doesn't exist anywhere in the codebase for it to reach.

## Directory layout

```
migrate.py                    CLI entrypoint
src/migration_tool/
  __init__.py
  types.py                    typed dataclasses for every function's return value
  clients/                    subprocess wrappers for the two remote systems
    esxi_client.py             read-only SSH client
    proxmox_client.py          qm/pvesh subprocess wrapper
  discovery/                  read-only inventory/path-resolution/collision detection
    inventory.py                parses getallvms (display-only, never authoritative for paths)
    path_resolver.py            THE authoritative disk-path resolver
    collision_detector.py       cross-VM filename collision detection
  pipeline/                   the actual migration steps, each independently verified
    capacity_check.py           staging-space verification
    copy_engine.py              collision-safe ESXi->local staging
    convert_engine.py           qemu-img convert + verify
    register_engine.py          qm create/importdisk/set + pvesh pool, fully verified
    boot_validator.py           start/stop/screendump/activity-probe
  agent/                      the agentic layer built on top of clients/discovery/pipeline
    known_issues.py             loads known_issues/library.json
    schemas.py                  agent-callable tool schemas wrapping all of the above
    orchestrator.py              rule-based observe/assess/decide/act/verify/record loop
  known_issues/
    library.json                known-issue library
tests/
  test_*.py                    fast, infrastructure-free unit/logic tests (run these normally)
  run_live_pipeline_test.py     manual-only: runs the REAL pipeline against live infra
```

## Known-issue library

`known_issues/library.json` is data, not code: any new pattern resolved via
human escalation should be proposed for addition (`known_issues.propose_new_issue()`),
with human confirmation before it's appended. Each entry records whether the
orchestrator can mechanically detect its trigger today
(`mechanically_triggerable`) — honestly `false` for the two boot-time visual
patterns (FreeBSD `mountroot`, GDM black-screen), since classifying a
screenshot is a vision/language judgment call, not something rule-based code
can do. The orchestrator proves its loop using the patterns that *are*
mechanically decidable instead: vendor-appliance exclusion,
powered-on-VM escalation, insufficient capacity, filename collisions, and
the disk-attachment silent-failure retry.

Two independently-verified fixes are recorded for the "GUI black screen"
symptom class, not one — `--vga qxl` and an offline
`systemctl set-default multi-user.target` via `virt-customize`. They address
different root causes that happen to look the same — don't assume one
implies the other without checking per-VM.

## Validation notes

Beyond the fast test suite, the pipeline has been run against real,
already-known ESXi infrastructure (not just synthetic fixtures), which
surfaced a few real-world quirks worth knowing about:

- Plain `vim-cmd vmsvc/get.filelayout` does not reliably enumerate a disk's
  `-flat.vmdk` extent on at least some ESXi versions — only the descriptor.
  `path_resolver.py` uses `get.filelayoutex` instead, which enumerates every
  file with an explicit `type` field (`diskDescriptor`, `diskExtent`, ...)
  and a real byte size — better in every way, and it removes the need for a
  separate `du` round-trip to learn file sizes.
- `vim-cmd vmsvc/power.getstate`'s real output is plain text
  ("`Retrieved runtime info\nPowered off\n`"), not the structured
  `powerState = "poweredOff"` form some documentation implies.
- `ESXiClient.pull_file` needs its own long timeout (4 hours), separate from
  the class's general 30-second command timeout sized for metadata queries —
  a real multi-GB transfer will blow straight through the short one.
- A full end-to-end run (resolve → capacity → copy → convert → register, all
  through the real tool layer, not mocked) against a known VM, targeting a
  disposable VMID, came back `final_status: migrated` with every
  registration step independently re-verified via a fresh `qm config`
  read-back and the converted image size matching the source's real flat
  file byte-for-byte.

## Why not virt-v2v?

Tried and failed against a standalone ESXi host (no vCenter), with two
different failure modes: HTTP range-request incompatibility on the default
transport, and a broken libvirt path-translation layer on the SSH transport.
`scp` + `qemu-img convert` is the supported pipeline for now — see
`register_engine.py`'s docstring for the resulting bus/NIC defaults
(SATA + e1000, never virtio, since virtio needs guest driver injection this
tool doesn't perform).
