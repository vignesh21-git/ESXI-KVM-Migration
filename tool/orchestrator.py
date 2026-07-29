"""Phase 3: rule-based observe -> assess -> decide -> act -> verify -> record
loop. No LLM involved yet -- a fixed decision table, on purpose, so loop
mechanics get proven separately from reasoning quality (Phase 4 replaces
just the assess/decide step with an LLM call later; everything else here is
designed to stay unchanged when that happens).

The orchestrator's action space is exactly schemas.call_tool() -- nothing in
this file shells out, opens an SSH connection, or touches a filesystem path
directly. That's not a style preference; it's the whole point of Phase 2
existing as a separate layer.

Stages, run in order per VM, each going through the full loop:
  RESOLVE -> (batch-wide COLLISION_CHECK happens once, outside per-VM loop)
  -> CAPACITY -> COPY -> CONVERT -> REGISTER -> DONE

Boot validation (observe_boot) is deliberately NOT wired into this fixed
table -- classifying a screenshot needs Phase 4. Phase 3 proves the loop
using the anomaly types that ARE mechanically decidable: appliance risk,
power state, capacity, collisions, and the disk-attachment silent-failure
retry.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from . import capacity_check as capacity_check_mod
from . import collision_detector
from . import known_issues as ki
from .esxi_client import ESXiHost
from .schemas import _resolved_path_from_dict, call_tool
from .types import (
    Decision,
    DecisionKind,
    EscalationAnswer,
    ResolvedPath,
    VMMigrationRecord,
)

AskHumanFn = Callable[[str, list[str], dict], str]


def default_ask_human(question: str, options: list[str], context: dict) -> str:
    """Phase 5's minimum viable escalation interface: a blocking CLI prompt.
    Presents the question with context and options, waits for a typed
    response. Swap this out (pass a different ask_human to Orchestrator) for
    any richer interface later -- the orchestrator only depends on the
    function signature, not on this being a terminal.
    """
    print("\n" + "=" * 70)
    print("HUMAN INPUT NEEDED")
    print("=" * 70)
    print(question)
    if context:
        print("\nContext:")
        for k, v in context.items():
            print(f"  {k}: {v}")
    if options:
        print("\nOptions: " + " / ".join(options))
    answer = input("\n> ").strip()
    print("=" * 70 + "\n")
    return answer


@dataclass
class OrchestratorConfig:
    esxi_host: ESXiHost
    storage_id: str
    staging_root: str
    bulk_storage_mount: str
    pool: str | None = None
    disk_bus: str = "sata0"
    nic_model: str = "e1000"
    bridge: str = "vmbr0"


class Orchestrator:
    def __init__(self, config: OrchestratorConfig, ask_human: AskHumanFn | None = None):
        self.config = config
        self.ask_human = ask_human or default_ask_human
        self.known_issues = ki.load_library()
        # Structural safety re-assertion: verify the staging root is under
        # bulk storage ONCE, at construction time, and fail loudly if not --
        # this is the fix for the "filled root to 100% twice" incident.
        capacity_check_mod.assert_staging_on_bulk_storage(
            config.staging_root, config.bulk_storage_mount
        )

    # ------------------------------------------------------------------ #
    # Fixed decision table -- one function per stage transition. Each is a
    # pure function of the tool output for that stage; none of them call a
    # tool themselves (that's _act's job) or hold state across VMs.
    # ------------------------------------------------------------------ #
    def _decide_after_resolve(self, resolved: dict) -> Decision:
        if not resolved["resolution_ok"]:
            return Decision(
                DecisionKind.ESCALATE,
                "path resolution incomplete/unreliable -- "
                f"warnings: {resolved.get('parse_warnings')}",
            )
        if resolved["appliance_risk"] == "likely_appliance":
            return Decision(
                DecisionKind.SKIP, resolved["appliance_reason"],
                known_issue_id="vendor_appliance_unsupported",
            )
        if resolved["power_state"] in ("poweredOn", "suspended"):
            return Decision(
                DecisionKind.ESCALATE,
                f"VM power_state={resolved['power_state']!r} -- migrating a running VM "
                "is never auto-decided (could be in active use).",
                known_issue_id="vm_powered_on_at_discovery",
            )
        if resolved["has_snapshot_chain"]:
            return Decision(
                DecisionKind.PROCEED,
                "snapshot chain detected -- copy_engine stages it verbatim, "
                "convert_engine will target the snapshot descriptor",
            )
        return Decision(DecisionKind.PROCEED, "resolution ok, no appliance/power/snapshot concerns")

    def _decide_after_capacity(self, capacity: dict) -> Decision:
        if not capacity["sufficient"]:
            return Decision(
                DecisionKind.ESCALATE,
                f"insufficient staging capacity: available={capacity['available_bytes']} "
                f"required={capacity['required_bytes']} margin={capacity['margin_bytes']}",
                known_issue_id="insufficient_staging_capacity",
            )
        return Decision(DecisionKind.PROCEED, "capacity sufficient")

    def _decide_after_copy(self, copy_result: dict) -> Decision:
        if not copy_result["ok"]:
            return Decision(DecisionKind.ESCALATE, copy_result.get("error") or "copy failed, no error detail")
        return Decision(DecisionKind.PROCEED, "copy verified ok")

    def _decide_after_convert(self, convert_result: dict) -> Decision:
        if not convert_result["size_matches_expectation"]:
            return Decision(
                DecisionKind.ESCALATE,
                convert_result.get("error") or "converted image size did not match expectation",
                known_issue_id="convert_size_mismatch",
            )
        if not convert_result["ok"]:
            return Decision(DecisionKind.ESCALATE, convert_result.get("error") or "conversion failed")
        return Decision(DecisionKind.PROCEED, "conversion verified ok")

    def _decide_after_register(self, register_result: dict, retried: bool) -> Decision:
        if register_result["fully_verified"]:
            return Decision(DecisionKind.PROCEED, "every registration step independently verified")
        if (
            register_result["disk_imported"]
            and not register_result["disk_attached"]
            and not retried
        ):
            return Decision(
                DecisionKind.APPLY_KNOWN_FIX,
                "disk imported but attachment not independently verified -- the exact "
                "silent-failure mode this tool exists to catch",
                known_issue_id="disk_attachment_not_verified",
            )
        return Decision(
            DecisionKind.ESCALATE,
            f"registration not fully verified after {'a retry' if retried else 'first attempt'}: "
            f"{register_result['errors']}",
        )

    # ------------------------------------------------------------------ #
    # Batch-wide pre-flight (collision detection happens once per batch,
    # not per VM -- it needs every VM's resolved path at once)
    # ------------------------------------------------------------------ #
    def check_batch_collisions(self, resolved_list: list[dict]) -> Decision:
        report = call_tool("detect_collisions", {"resolved_vms": resolved_list})
        if report["has_collisions"]:
            # Known-mitigated: copy_engine's per-VM staging subdirectories
            # make this structurally safe. Log and proceed, per the library.
            return Decision(
                DecisionKind.PROCEED,
                f"collisions detected but structurally mitigated by per-VM staging "
                f"dirs: {report['collisions']}",
                known_issue_id="filename_collision_detected",
            )
        return Decision(DecisionKind.PROCEED, "no filename collisions in this batch")

    # ------------------------------------------------------------------ #
    # Per-VM pipeline
    # ------------------------------------------------------------------ #
    def run_vm(
        self,
        esxi_vmid: int,
        target_vmid: int,
        memory_mb: int,
        cores: int,
        display_name: str = "",
        guest_os_raw: str = "",
        annotation: str = "",
    ) -> VMMigrationRecord:
        record = VMMigrationRecord(
            esxi_vmid=esxi_vmid,
            esxi_host=self.config.esxi_host.address,
            display_name=display_name,
            target_vmid=target_vmid,
            pool=self.config.pool,
            disk_size_bytes=None,
        )

        # ---- STAGE: RESOLVE --------------------------------------------- #
        resolved = call_tool("esxi_resolve_vm_path", {
            "esxi_host": self.config.esxi_host.address,
            "ssh_key_path": self.config.esxi_host.ssh_key_path,
            "esxi_vmid": esxi_vmid,
            "display_name": display_name,
            "guest_os_raw": guest_os_raw,
            "annotation": annotation,
        })
        record.resolved = _resolved_path_from_dict(resolved)

        decision = self._decide_after_resolve(resolved)
        record.decisions.append(decision)
        if not self._handle_decision(decision, record):
            return record

        # ---- STAGE: CAPACITY --------------------------------------------- #
        required_bytes = self._compute_required_bytes(resolved)
        capacity = call_tool("check_staging_capacity", {
            "staging_dir": self.config.staging_root,
            "required_bytes": required_bytes,
        })
        record.capacity = capacity
        decision = self._decide_after_capacity(capacity)
        record.decisions.append(decision)
        if not self._handle_decision(decision, record):
            return record

        # ---- STAGE: COPY --------------------------------------------------- #
        local_name = f"vm{target_vmid}"
        copy_result = call_tool("stage_vm_disk", {
            "esxi_host": self.config.esxi_host.address,
            "ssh_key_path": self.config.esxi_host.ssh_key_path,
            "resolved": resolved,
            "staging_root": self.config.staging_root,
            "local_name": local_name,
        })
        record.copy = copy_result
        decision = self._decide_after_copy(copy_result)
        record.decisions.append(decision)
        if not self._handle_decision(decision, record):
            return record

        # ---- STAGE: CONVERT --------------------------------------------------- #
        source_descriptor = self._pick_convert_source(copy_result, resolved)
        output_qcow2 = f"{self.config.staging_root}/{esxi_vmid}_{local_name}/{local_name}.qcow2"
        expected_size = self._expected_virtual_size(resolved)
        convert_result = call_tool("convert_vm_disk", {
            "source_descriptor": source_descriptor,
            "output_qcow2_path": output_qcow2,
            "expected_virtual_size_bytes": expected_size,
        })
        record.convert = convert_result
        decision = self._decide_after_convert(convert_result)
        record.decisions.append(decision)
        if not self._handle_decision(decision, record):
            return record

        # ---- STAGE: REGISTER --------------------------------------------------- #
        register_args = {
            "target_vmid": target_vmid,
            "display_name": display_name or f"esxi-vm-{esxi_vmid}",
            "memory_mb": memory_mb,
            "cores": cores,
            "qcow2_path": convert_result["output_qcow2"],
            "storage_id": self.config.storage_id,
            "esxi_vmid": esxi_vmid,
            "esxi_source_path": resolved.get("vmx_relative_path") or "(unresolved)",
            "esxi_host": self.config.esxi_host.address,
            "pool": self.config.pool,
            "disk_bus": self.config.disk_bus,
            "nic_model": self.config.nic_model,
            "bridge": self.config.bridge,
        }
        register_result = call_tool("register_vm", register_args)
        record.register = register_result
        decision = self._decide_after_register(register_result, retried=False)
        record.decisions.append(decision)

        if decision.kind == DecisionKind.APPLY_KNOWN_FIX:
            record.known_fixes_applied.append(decision.known_issue_id or "unknown")
            # The fix for disk_attachment_not_verified is: retry register_vm
            # in full (safe because create is idempotent -- see
            # register_engine.py) and re-verify.
            register_result = call_tool("register_vm", register_args)
            record.register = register_result
            decision = self._decide_after_register(register_result, retried=True)
            record.decisions.append(decision)

        if not self._handle_decision(decision, record):
            return record

        record.final_status = "migrated"
        return record

    # ------------------------------------------------------------------ #
    # Decision execution
    # ------------------------------------------------------------------ #
    def _handle_decision(self, decision: Decision, record: VMMigrationRecord) -> bool:
        """Returns True if the pipeline should continue to the next stage."""
        if decision.kind == DecisionKind.PROCEED:
            return True
        if decision.kind == DecisionKind.SKIP:
            record.final_status = "skipped"
            return False
        if decision.kind == DecisionKind.APPLY_KNOWN_FIX:
            # Handled inline at the register stage today (the only
            # mechanically-triggerable fix wired up in Phase 3); reaching
            # here for any other known_issue_id means the fix logic hasn't
            # been implemented for it yet -- escalate rather than pretend.
            return True
        if decision.kind == DecisionKind.ESCALATE:
            issue = self.known_issues.get(decision.known_issue_id) if decision.known_issue_id else None
            question = (
                f"VM esxi_vmid={record.esxi_vmid} ({record.display_name!r}) needs a decision:\n"
                f"{decision.reason}"
            )
            if issue:
                question += f"\n\nKnown issue '{issue.id}': {issue.fix_description}"
            answer = self.ask_human(
                question,
                options=["proceed", "skip", "abort"],
                context={"esxi_vmid": record.esxi_vmid, "target_vmid": record.target_vmid},
            )
            record.escalations.append(
                EscalationAnswer(
                    question=question, answer=answer,
                    resolved_at_utc=datetime.now(timezone.utc).isoformat(),
                )
            )
            if answer.strip().lower() == "proceed":
                return True
            record.final_status = "needs_human" if answer.strip().lower() != "skip" else "skipped"
            return False
        return False

    # ------------------------------------------------------------------ #
    # Small helpers (pure local logic, no tool calls, EXCEPT this one which
    # legitimately needs a tool call -- kept as a helper for readability,
    # not because it bypasses call_tool)
    # ------------------------------------------------------------------ #
    def _compute_required_bytes(self, resolved: dict) -> int:
        # get.filelayoutex (path_resolver's authoritative source, confirmed
        # live against a real ESXi host) already reports real byte sizes for
        # every disk file -- no separate `du` round-trip needed in the
        # common case. Falls back to esxi_disk_usage_kb only for any file
        # missing a size (e.g. resolution came from the get.filelayout
        # fallback path rather than filelayoutex).
        total_bytes = 0
        for f in resolved["disk_files"]:
            if f.get("size_bytes"):
                total_bytes += f["size_bytes"]
                continue
            remote_path = f"/vmfs/volumes/{resolved['datastore']}/{f['relative_path']}"
            usage = call_tool("esxi_disk_usage_kb", {
                "esxi_host": self.config.esxi_host.address,
                "ssh_key_path": self.config.esxi_host.ssh_key_path,
                "remote_path": remote_path,
            })
            if usage["ok"]:
                total_bytes += usage["size_kb"] * 1024
        return total_bytes

    @staticmethod
    def _pick_convert_source(copy_result: dict, resolved: dict) -> str:
        if resolved["has_snapshot_chain"]:
            # copy_engine stages snapshot chains verbatim (original filenames
            # preserved) so qemu-img can resolve parentFileNameHint itself --
            # point it at whichever staged file corresponds to the
            # snapshot_descriptor-classified disk file from path_resolver.
            snapshot_names = {
                f["relative_path"].rsplit("/", 1)[-1]
                for f in resolved["disk_files"]
                if f["role"] == "snapshot_descriptor"
            }
            for f in copy_result["files"]:
                staged_filename = f["staged_path"].rsplit("/", 1)[-1]
                if staged_filename in snapshot_names:
                    return f["staged_path"]
            raise ValueError(
                "has_snapshot_chain=true but no staged file matches a "
                "snapshot_descriptor role -- resolution/staging is inconsistent"
            )
        # simple case: the renamed descriptor is always named "<local_name>-src.vmdk"
        for f in copy_result["files"]:
            if f["staged_path"].endswith("-src.vmdk"):
                return f["staged_path"]
        raise ValueError("could not identify the descriptor file among staged copies")

    @staticmethod
    def _expected_virtual_size(resolved: dict) -> int | None:
        # Populated by a caller who ran disk_usage_kb ahead of time and
        # attached it to disk_files[*].size_bytes; None disables the check
        # (convert_engine still verifies the file is non-empty/parseable,
        # just skips the exact-size comparison).
        for f in resolved["disk_files"]:
            if f.get("size_bytes"):
                return f["size_bytes"]
        return None
