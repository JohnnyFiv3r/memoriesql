"""Pre-apply checks of authored annotations against their own task.

Canonical apply stays authoritative and unchanged: it refuses every output these
checks flag, with the refusal named beside each code below. A typed output
contract cannot express these cross-references, so they are found here, before
apply, where the executor can still tell the model what to fix (guard audit
P1-f (a)). No check is stricter than canonical apply; a check that cannot be
decided without database state is left to apply. An issue carries a fixed code
and only identifiers the model wrote or its task input named, never evidence
text or a database message.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

# Each code, with the canonical-apply refusals it anticipates.
_COVERAGE = ("semantic annotation result does not cover its task",)
_PIN = (
    "v2 requires complete initial semantics for distinct beads",
    "complete_input_exposure_required",
)
ISSUE_REFUSALS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "annotation.bead_missing": _COVERAGE,
        "annotation.bead_duplicated": _COVERAGE,
        "annotation.bead_outside_task": _COVERAGE,
        "annotation.event_mismatch": _PIN,
        "annotation.source_unit_mismatch": _PIN,
        "evidence.outside_manifest": ("evidence_hash_mismatch",),
        "evidence.not_declared_as_used": ("evidence_hash_mismatch",),
        "statement.context_without_evidence": ("context source lacks statement evidence",),
    }
)

# Pins are checked where canonical apply checks them: v2 and complete units.
_COMPLETE_KIND = "memory.semantic.author-complete-unit"
_PINNED: frozenset[tuple[str, int]] = frozenset(
    {
        ("memory.semantic.author-observations", 2),
        ("memory.semantic.correct-observation", 2),
        *((_COMPLETE_KIND, revision) for revision in range(2, 8)),
    }
)


@dataclass(frozen=True)
class AuthoredOutputIssue:
    code: str
    subject: str
    expected: str | None = None

    def __post_init__(self) -> None:
        if self.code not in ISSUE_REFUSALS:
            raise ValueError(f"unregistered authored output issue: {self.code}")

    def feedback(self) -> str:
        detail = f"{self.code}: {self.subject}"
        return detail if self.expected is None else f"{detail} (expected {self.expected})"


def authored_annotation_issues(
    task_input: Mapping[str, Any],
    payload: Mapping[str, Any],
    used_evidence_refs: Sequence[str],
    *,
    task_kind: str,
    contract_revision: int,
) -> tuple[AuthoredOutputIssue, ...]:
    """Issues of one annotation payload, from JSON dumps of the task input and output.

    A payload without annotations is not an annotation output and has none.
    """

    annotations = payload.get("annotations")
    if not isinstance(annotations, list):
        return ()
    task = task_input.get("payload") or {}
    manifest = {
        str(item.get("reference_id")): str(item.get("content_hash"))
        for item in (task_input.get("evidence_manifest") or {}).get("references") or ()
    }
    used = {str(ref) for ref in used_evidence_refs}
    issues: list[AuthoredOutputIssue] = []

    task_beads = [str(bead) for bead in task.get("bead_ids") or ()]
    written = Counter(str(item.get("bead_id")) for item in annotations)
    for bead in task_beads:
        if written[bead] == 0:
            issues.append(AuthoredOutputIssue("annotation.bead_missing", bead))
    for bead, count in sorted(written.items()):
        if bead not in task_beads:
            issues.append(AuthoredOutputIssue("annotation.bead_outside_task", bead))
        elif count > 1:
            issues.append(AuthoredOutputIssue("annotation.bead_duplicated", bead))

    for ref in sorted(used - set(manifest)):
        issues.append(AuthoredOutputIssue("evidence.outside_manifest", ref))

    pinned = (task_kind, contract_revision) in _PINNED
    event = str(task.get("event_id"))
    units = [str(unit) for unit in task.get("source_unit_ids") or ()]
    for annotation in annotations:
        bead = str(annotation.get("bead_id"))
        if pinned and str(annotation.get("event_id")) != event:
            issues.append(AuthoredOutputIssue("annotation.event_mismatch", bead, event))
        if pinned and str(annotation.get("source_unit_id")) not in (
            units[:1] if task_kind == _COMPLETE_KIND else units
        ):
            issues.append(
                AuthoredOutputIssue(
                    "annotation.source_unit_mismatch", bead, ", ".join(units)
                )
            )
        for statement in annotation.get("statements") or ():
            subject = str(statement.get("statement_id"))
            cited = set()
            for item in statement.get("evidence") or ():
                unit = str(item.get("source_unit_id"))
                cited.add(unit)
                if manifest.get(unit) != str(item.get("content_hash")):
                    issues.append(
                        AuthoredOutputIssue("evidence.outside_manifest", f"{subject} cites {unit}")
                    )
                elif unit not in used:
                    issues.append(
                        AuthoredOutputIssue(
                            "evidence.not_declared_as_used", f"{subject} cites {unit}"
                        )
                    )
            for context in statement.get("context_source_ids") or ():
                if str(context) not in cited:
                    issues.append(
                        AuthoredOutputIssue(
                            "statement.context_without_evidence", f"{subject} names {context}"
                        )
                    )
    return tuple(issues)
