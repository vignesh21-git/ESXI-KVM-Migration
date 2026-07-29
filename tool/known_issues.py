"""Loads known_issues/library.json and wires up the mechanically-triggerable
entries to actual trigger/fix logic that orchestrator.py's Phase 3 decision
table can evaluate.

Split deliberately mirrors the library's own `mechanically_triggerable`
field: entries where the trigger condition is readable straight off
structured tool output (RegisterResult, CapacityCheck, ConvertResult,
CollisionReport, ResolvedPath) get real trigger/fix callables here and
Phase 3 can act on them today. Entries whose trigger requires reading a
screenshot (FreeBSD mountroot, GDM black screen) are loaded as data for
Phase 4 to reason over, but have no callable here -- Phase 3 correctly
does not pretend to detect them.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

_LIBRARY_PATH = Path(__file__).resolve().parent.parent / "known_issues" / "library.json"


@dataclass(frozen=True)
class KnownIssue:
    id: str
    category: str
    mechanically_triggerable: bool
    trigger_description: str
    fix_description: str
    auto_apply_when_detected: bool
    escalate_if_fix_fails: bool
    action: str | None = None
    max_auto_retries: int = 0


def load_library() -> dict[str, KnownIssue]:
    data = json.loads(_LIBRARY_PATH.read_text())
    issues = {}
    for raw in data["issues"]:
        issues[raw["id"]] = KnownIssue(
            id=raw["id"],
            category=raw["category"],
            mechanically_triggerable=raw["mechanically_triggerable"],
            trigger_description=raw["trigger_description"],
            fix_description=raw["fix_description"],
            auto_apply_when_detected=raw["auto_apply_when_detected"],
            escalate_if_fix_fails=raw["escalate_if_fix_fails"],
            action=raw.get("action"),
            max_auto_retries=raw.get("max_auto_retries", 0),
        )
    return issues


def propose_new_issue(
    issue_id: str,
    category: str,
    trigger_description: str,
    fix_description: str,
    resolved_via: str,
) -> dict:
    """Phase 8 hook: builds a new library entry from something a human just
    resolved via escalation, in the SAME shape as the seeded entries. Never
    writes to library.json itself -- returns the proposed entry for a human
    to review and confirm before it's appended (see README's Phase 8 notes).
    """
    return {
        "id": issue_id,
        "category": category,
        "mechanically_triggerable": False,  # conservative default; a human should upgrade this after review
        "trigger_description": trigger_description,
        "fix_description": fix_description,
        "risk": "unknown",
        "destructive": False,
        "auto_apply_when_detected": False,  # never auto-enable a freshly-learned fix without review
        "escalate_if_fix_fails": True,
        "source": f"learned_from_escalation:{resolved_via}",
    }
