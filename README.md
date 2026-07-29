# ESXi → Proxmox Migration Tool

A reusable, general-purpose tool automating the ESXi-to-Proxmox migration
pipeline proven manually across 50+ VMs / 5 batches / 2 standalone ESXi
hosts earlier in this project. Two structurally separate layers:

1. **Deterministic tool layer** (`tool/*.py`) — narrow functions that
   actually touch ESXi/Proxmox, each returning an explicit typed result
   (never a bare success/fail boolean for anything multi-step).
2. **Agentic orchestrator** (`tool/orchestrator.py`) — a reasoning loop
   whose only means of taking action is `tool/schemas.py`'s `call_tool()`.
   It cannot construct a shell command or SSH invocation itself.

## Deployment model

Runs **locally on the Proxmox host**, not on a laptop or inside a guest VM.
Disk data flows ESXi → local staging → Proxmox-managed storage in one hop;
`qm`/`pvesh` are native host binaries; long transfers survive an operator's
connection dropping because nothing depends on a remote client staying
attached. See `tool/*.py` docstrings for the reasoning per-module; this
mirrors exactly how the manual project ran its batches (tmux/screen on the
Proxmox host itself).

No pip/venv — this host has no pip installed, and the deployment model gives
no reason to want one. Everything is Python 3.13 standard library
(`subprocess`, `dataclasses`, `re`, `json`), shelling out to the native
`ssh`/`scp`/`qemu-img`/`qm`/`pvesh` binaries.

## Structural safety guarantee

`tool/esxi_client.py` has **no write/delete method of any kind, and no
generic "run a command string" method** — its entire public API is nine
named, single-purpose read-only operations, each building its own fixed
argv. `tests/test_esxi_safety.py` verifies this by introspecting the class's
method list, not by trusting a comment. The orchestrator's action space is
exactly `tool/schemas.py`'s `TOOL_REGISTRY`, so there is no code path from
"agent decides something" to "arbitrary command against the ESXi source" —
that path doesn't exist anywhere in the codebase for it to reach.

## Directory layout

```
tool/
  types.py             typed dataclasses for every function's return value
  esxi_client.py        read-only SSH client (Phase 1)
  proxmox_client.py      qm/pvesh subprocess wrapper (Phase 1)
  inventory.py           parses getallvms (display-only, never authoritative for paths)
  path_resolver.py       THE authoritative disk-path resolver (Phase 1)
  collision_detector.py  cross-VM filename collision detection (Phase 1)
  capacity_check.py      staging-space verification (Phase 1)
  copy_engine.py         collision-safe ESXi->local staging (Phase 1)
  convert_engine.py      qemu-img convert + verify (Phase 1)
  register_engine.py     qm create/importdisk/set + pvesh pool, fully verified (Phase 1)
  boot_validator.py      start/stop/screendump/activity-probe (Phase 1 addition, see below)
  known_issues.py        loads known_issues/library.json
  schemas.py             Phase 2: agent-callable tool schemas wrapping all of the above
  orchestrator.py        Phase 3: rule-based observe/assess/decide/act/verify/record loop
known_issues/
  library.json           known-issue library, seeded from the manual project
tests/
  test_*.py              fast, infrastructure-free unit/logic tests (run these normally)
  run_live_pipeline_test.py   manual-only: runs the REAL pipeline against live infra
```

Run the test suite: `for f in tests/test_*.py; do python3 "$f"; done` — no
pytest, no dependencies, just `python3`.

## Phase status

| Phase | Status | Notes |
|---|---|---|
| 1: deterministic tool layer | **Done** | All 9 modules built and validated against real infrastructure (see below) |
| 2: strict tool schemas | **Done** | `schemas.py`, 13 tools registered, Anthropic-Messages-shaped `to_llm_tool_def()` |
| 3: rule-based orchestrator | **Done** | Fixed decision table, 6 scenarios proven via mocked `call_tool` in `test_orchestrator.py` |
| 4: LLM-driven decisions | Not started | Replace `_decide_*` methods' bodies with a Claude call; everything else in `orchestrator.py` designed to stay unchanged |
| 5: human escalation interface | **Stubbed** | `default_ask_human()` is a real blocking CLI prompt; swap for richer UI later |
| 6: validate against known-good data | **Done** | See "Real-world validation" below |
| 7: first live run on unfamiliar data | Not started | Needs a human watching, per the task's own calibration |
| 8: reporting | Partially done | `VMMigrationRecord` (in `types.py`) is the manifest row shape; `known_issues.propose_new_issue()` is the Phase 8 hook for learned patterns; no report-writer script yet |

## Real-world validation (Phase 6 + live pipeline test)

Before trusting any of this, Task #15 ran it against real, already-known
infrastructure rather than only synthetic fixtures:

- **Dry-run discovery**: listed all 42 VMs on the real ESXi host
  (192.168.4.90) and resolved the 3 known MPLS-LDP-TESTBED VMs. Every
  result matched what was already known-correct from the manual project,
  including the famous `LER_2` display name / `LER2_New` folder /
  `LER_1.vmdk` internal filename three-way mismatch, and exact byte-for-byte
  disk sizes matching the real registered VMs (LSR-1's 50GB, LER-1/LER-2's
  20GB each).
- **Real collision detection**: resolved the 4 real BGPv6 Windows VMs and
  confirmed the tool detects the exact known 4-way `IPv6_WIN10-HOST.vmdk`
  collision across their 4 different source folders.
- **This validation pass caught two real parsing bugs** that synthetic test
  fixtures had missed, because the fixtures were built from an *assumption*
  about VMware's CLI output shape rather than the real thing:
  1. Plain `vim-cmd vmsvc/get.filelayout` does not reliably enumerate a
     disk's `-flat.vmdk` extent on this ESXi version — only the descriptor.
     Switched `path_resolver.py` to `get.filelayoutex`, which enumerates
     every file with an explicit `type` field (`diskDescriptor`,
     `diskExtent`, ...) and a real byte size — better in every way, and it
     removed the need for a separate `du` round-trip to learn file sizes.
  2. `vim-cmd vmsvc/power.getstate`'s real output is plain text
     ("`Retrieved runtime info\nPowered off\n`"), not the structured
     `powerState = "poweredOff"` form originally assumed.
  3. `ESXiClient.pull_file` inherited the class's general 30-second command
     timeout (sized for metadata queries), which a real 10GB transfer blew
     straight through. Given its own long timeout (4 hours, matching this
     project's own historical experience with multi-hour Windows disk
     transfers).
- **Full live pipeline test**: ran the real `Orchestrator.run_vm()` — not
  mocked — against ER-Test-Bed's `Host-A` (ESXi vmid 17, already known and
  previously validated in this project as Proxmox VMID 500), targeting a
  separate throwaway VMID (9999) so the existing registration was never
  touched. Result: `final_status: migrated`, every registration step
  independently re-verified via a fresh `qm config` read-back (not trusting
  the tool's own report), converted image size exactly matched the source's
  real 10,737,418,240-byte flat file. Test VM, pool, and staged files were
  all cleaned up afterward.

## Known-issue library

`known_issues/library.json` is data, not code, per Phase 8's design intent
("any new pattern resolved via human escalation should be proposed for
addition, with human confirmation"). Each entry records whether Phase 3 can
mechanically detect its trigger today (`mechanically_triggerable`) — honestly
`false` for the two boot-time visual patterns (FreeBSD `mountroot`, GDM
black-screen), since classifying a screenshot is a Phase 4 concern, not
something rule-based code can do. Phase 3 proves its loop using the patterns
that *are* mechanically decidable instead: vendor-appliance exclusion,
powered-on-VM escalation, insufficient capacity, filename collisions, and
the disk-attachment silent-failure retry.

Two independently-verified fixes are recorded for the "GUI black screen"
symptom class, not one — `--vga qxl` (from the task spec) and an offline
`systemctl set-default multi-user.target` via `virt-customize` (empirically
verified working in this same project's manual migration, on VMs 407/700/701).
They address different root causes that happen to look the same, and the
library entry explicitly flags that a task spec once conflated the two
incorrectly — don't assume one implies the other without checking per-VM.

## Why not virt-v2v?

Tried and failed twice against a standalone ESXi host (no vCenter) earlier
in this project, with two different failure modes: HTTP range-request
incompatibility on the default transport, and a broken libvirt
path-translation layer on the SSH transport. `scp` + `qemu-img convert` is
the only supported pipeline for now — see `register_engine.py`'s docstring
for the resulting bus/NIC defaults (SATA + e1000, never virtio, since virtio
needs guest driver injection this tool doesn't perform).

## Not yet built

- Phase 4: the LLM decision step itself (schemas are ready — see
  `ToolSchema.to_llm_tool_def()` — this is genuinely just wiring a Claude
  call into `Orchestrator._decide_after_*`, seeded with
  `known_issues/library.json`'s contents).
- Boot-time known-issue *auto-fixing* (FreeBSD mountroot, GDM black-screen):
  `boot_validator.py` mechanically captures the evidence (screendump +
  disk-activity telemetry) but does not classify it — that's exactly the
  judgment call Phase 4 exists for.
- A batch-level CLI entrypoint (right now, driving a batch means writing a
  short script like `tests/run_live_pipeline_test.py` that constructs an
  `Orchestrator` and calls `run_vm()` per VM, plus `check_batch_collisions()`
  once up front).
- Phase 7 (first live run on unfamiliar data) and Phase 8's report-writer.
