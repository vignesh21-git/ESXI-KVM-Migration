"""Typed return values for every tool-layer function.

Nothing in this codebase returns a bare bool for a multi-step action. During the
manual migration project, a registration script reported "success" while disk
IMPORT had succeeded but disk ATTACHMENT (qm set --sata0) had silently failed —
because the wrapper only checked the exit code of the last command, not the
actual resulting state. Every dataclass below exists to make that class of bug
structurally harder: each step of a multi-step action gets its own explicit
verified field.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


# --------------------------------------------------------------------------- #
# Generic command result (used internally by esxi_client / register_engine /
# convert_engine / copy_engine — never exposed directly as an agent tool, only
# wrapped by higher-level typed results below).
# --------------------------------------------------------------------------- #
@dataclass
class CommandResult:
    ok: bool
    returncode: int
    stdout: str
    stderr: str
    argv: list[str] = field(default_factory=list)  # for audit logging, never includes secrets


# --------------------------------------------------------------------------- #
# Inventory / discovery
# --------------------------------------------------------------------------- #
class PowerState(str, Enum):
    ON = "poweredOn"
    OFF = "poweredOff"
    SUSPENDED = "suspended"
    UNKNOWN = "unknown"


@dataclass
class VMSummary:
    """Best-effort parse of `vim-cmd vmsvc/getallvms`.

    Name/annotation here are for HUMAN DISPLAY ONLY. Never derive a disk path
    from anything in this dataclass — real ESXi hosts routinely have display
    names that don't match folder names or .vmdk filenames. Always resolve
    paths via path_resolver.resolve().
    """
    esxi_vmid: int
    display_name: str
    guest_os_raw: str
    version: str
    annotation: str
    parse_warnings: list[str] = field(default_factory=list)


@dataclass
class DiskFile:
    """One file belonging to a disk chain, as reported by get.filelayout."""
    relative_path: str          # path as returned by ESXi, relative to datastore root
    role: str                   # "base_descriptor" | "base_flat" | "snapshot_descriptor" | "snapshot_delta" | "unknown"
    size_bytes: int | None = None  # populated only if capacity_check / du was run against it


class ApplianceRisk(str, Enum):
    NONE = "none"
    LIKELY_APPLIANCE = "likely_appliance"


@dataclass
class ResolvedPath:
    """Authoritative disk-path resolution for one VM. This is the ONLY thing
    copy_engine is allowed to read paths from — never VMSummary, never a folder
    listing, never a guess from the display name.
    """
    esxi_vmid: int
    display_name: str
    datastore: str | None
    vmx_relative_path: str | None
    disk_files: list[DiskFile]
    has_snapshot_chain: bool
    power_state: PowerState
    appliance_risk: ApplianceRisk
    appliance_reason: str | None
    resolution_ok: bool          # False => do not proceed, needs a human/agent look at raw output
    parse_warnings: list[str] = field(default_factory=list)
    raw_filelayout: str = ""     # kept for audit / manual inspection on parse failure


# --------------------------------------------------------------------------- #
# Collision detection
# --------------------------------------------------------------------------- #
@dataclass
class CollisionReport:
    has_collisions: bool
    # filename -> list of esxi_vmids whose resolved disk files share that filename
    collisions: dict[str, list[int]] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Capacity check
# --------------------------------------------------------------------------- #
@dataclass
class CapacityCheck:
    storage_path: str
    available_bytes: int
    required_bytes: int
    margin_bytes: int            # available - required (can be negative)
    sufficient: bool
    checked_at_utc: str


# --------------------------------------------------------------------------- #
# Copy engine
# --------------------------------------------------------------------------- #
@dataclass
class CopiedFile:
    source_relative_path: str
    staged_path: str             # final local path after collision-safe renaming
    bytes_transferred: int
    renamed: bool


@dataclass
class CopyResult:
    esxi_vmid: int
    ok: bool
    files: list[CopiedFile]
    total_bytes: int
    error: str | None = None


# --------------------------------------------------------------------------- #
# Convert engine
# --------------------------------------------------------------------------- #
@dataclass
class ConvertResult:
    esxi_vmid: int
    ok: bool
    source_descriptor: str       # the vmdk descriptor qemu-img was pointed at
    output_qcow2: str
    reported_virtual_size_bytes: int | None
    expected_virtual_size_bytes: int | None
    size_matches_expectation: bool
    error: str | None = None


# --------------------------------------------------------------------------- #
# Register engine — the dataclass the whole project's "silent failure" lesson
# is directly encoded into. Every field is verified independently; none of
# them are inferred from another.
# --------------------------------------------------------------------------- #
@dataclass
class RegisterResult:
    target_vmid: int
    proxmox_name: str
    created: bool
    disk_imported: bool
    disk_attached: bool          # verified via `qm config <vmid> | grep sataN`, not assumed from importdisk exit code
    boot_order_set: bool
    description_set: bool
    serial_console_set: bool
    pool_assigned: bool
    fully_verified: bool         # True only if every field above is True
    errors: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Orchestrator-level records
# --------------------------------------------------------------------------- #
class DecisionKind(str, Enum):
    PROCEED = "proceed"
    APPLY_KNOWN_FIX = "apply_known_fix"
    ESCALATE = "escalate"
    SKIP = "skip"


@dataclass
class Decision:
    kind: DecisionKind
    reason: str
    known_issue_id: str | None = None


@dataclass
class EscalationAnswer:
    question: str
    answer: str
    resolved_at_utc: str


@dataclass
class VMMigrationRecord:
    """One row of the final per-run manifest (Phase 8, stubbed now for later use)."""
    esxi_vmid: int
    esxi_host: str
    display_name: str
    target_vmid: int | None
    pool: str | None
    disk_size_bytes: int | None
    resolved: ResolvedPath | None = None
    # collision/capacity/copy/convert/register are stored as the plain JSON-safe
    # dicts produced by schemas.call_tool(), not reconstructed dataclasses --
    # this is what actually flows through the orchestrator (see
    # orchestrator.py), and it's also the natural shape for the Phase 8 JSON
    # manifest this record ultimately feeds.
    collision: dict | None = None
    capacity: dict | None = None
    copy: dict | None = None
    convert: dict | None = None
    register: dict | None = None
    decisions: list[Decision] = field(default_factory=list)
    escalations: list[EscalationAnswer] = field(default_factory=list)
    known_fixes_applied: list[str] = field(default_factory=list)
    final_status: str = "pending"   # pending | migrated | skipped | failed | needs_human
