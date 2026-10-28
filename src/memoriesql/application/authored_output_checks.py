"""Pre-apply checks of authored annotations against their own task.

Canonical apply stays authoritative and unchanged: it refuses every output these
checks flag, with the refusal named beside each code below. A typed output
contract cannot express these cross-references, so they are found here, before
apply, where the executor can still tell the model what to fix (guard audit
P1-f (a)). No check is stricter than canonical apply; a check that cannot be
decided without database state is left to apply. An issue carries a fixed code
and only identifiers the model wrote or its task input named, never evidence
text or a database message.

Observation authoring may then be accepted in part (P1-f (b)): each task bead
with an issue is withheld whole, with a fixed reason, and the rest goes to
canonical apply, which checks it exactly as before.
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
        *((_COMPLETE_KIND, revision) for revision in range(2, 9)),
    }
)


# The withheld reason of each issue that makes one task bead unacceptable.
WITHHELD_REASONS: Mapping[str, str] = MappingProxyType(
    {
        "annotation.bead_missing": "withheld.not_authored",
        "annotation.bead_duplicated": "withheld.duplicated",
        "annotation.event_mismatch": "withheld.pin_mismatch",
        "annotation.source_unit_mismatch": "withheld.pin_mismatch",
        "evidence.outside_manifest": "withheld.evidence_outside_manifest",
        "evidence.not_declared_as_used": "withheld.evidence_not_declared_as_used",
        "statement.context_without_evidence": "withheld.context_without_evidence",
    }
)

# Multi-bead observation authoring; a complete unit is one bead, all or nothing.
PARTIAL_ACCEPTANCE: frozenset[tuple[str, int]] = frozenset(
    {
        ("memory.semantic.author-observations", 1),
        ("memory.semantic.author-observations", 2),
    }
)


@dataclass(frozen=True)
class AuthoredOutputIssue:
    code: str
    subject: str
    expected: str | None = None
    # The task bead this issue makes unacceptable; None for an annotation of a
    # bead outside the task or a used reference outside the manifest.
    bead: str | None = None

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
            issues.append(AuthoredOutputIssue("annotation.bead_missing", bead, bead=bead))
    for bead, count in sorted(written.items()):
        if bead not in task_beads:
            issues.append(AuthoredOutputIssue("annotation.bead_outside_task", bead))
        elif count > 1:
            issues.append(
                AuthoredOutputIssue("annotation.bead_duplicated", bead, bead=bead)
            )

    for ref in sorted(used - set(manifest)):
        issues.append(AuthoredOutputIssue("evidence.outside_manifest", ref))

    pinned = (task_kind, contract_revision) in _PINNED
    event = str(task.get("event_id"))
    units = [str(unit) for unit in task.get("source_unit_ids") or ()]
    for annotation in annotations:
        bead = str(annotation.get("bead_id"))
        owner = bead if bead in task_beads else None
        if pinned and str(annotation.get("event_id")) != event:
            issues.append(
                AuthoredOutputIssue("annotation.event_mismatch", bead, event, owner)
            )
        if pinned and str(annotation.get("source_unit_id")) not in (
            units[:1] if task_kind == _COMPLETE_KIND else units
        ):
            issues.append(
                AuthoredOutputIssue(
                    "annotation.source_unit_mismatch", bead, ", ".join(units), owner
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
                        AuthoredOutputIssue(
                            "evidence.outside_manifest",
                            f"{subject} cites {unit}",
                            bead=owner,
                        )
                    )
                elif unit not in used:
                    issues.append(
                        AuthoredOutputIssue(
                            "evidence.not_declared_as_used",
                            f"{subject} cites {unit}",
                            bead=owner,
                        )
                    )
            for context in statement.get("context_source_ids") or ():
                if str(context) not in cited:
                    issues.append(
                        AuthoredOutputIssue(
                            "statement.context_without_evidence",
                            f"{subject} names {context}",
                            bead=owner,
                        )
                    )
    return tuple(issues)


@dataclass(frozen=True)
class PartialAnnotations:
    """The acceptable part of an annotation payload and what it withholds."""

    payload: dict[str, Any]
    used_evidence_refs: tuple[str, ...]
    # (bead, reason code) in task order.
    withheld: tuple[tuple[str, str], ...]


def partial_annotations(
    task_input: Mapping[str, Any],
    payload: Mapping[str, Any],
    used_evidence_refs: Sequence[str],
    issues: Sequence[AuthoredOutputIssue],
    *,
    task_kind: str,
    contract_revision: int,
) -> PartialAnnotations | None:
    """Withhold each task bead with an issue, or None when nothing acceptable is left.

    A withheld bead takes the reason of its first issue. Annotations of beads
    outside the task and used references outside the manifest are dropped; what
    is kept is unchanged and must have no issue left but the withheld beads'
    absence.
    """

    annotations = payload.get("annotations")
    if (
        (task_kind, contract_revision) not in PARTIAL_ACCEPTANCE
        or not issues
        or not isinstance(annotations, list)
    ):
        return None
    task_beads = [
        str(bead) for bead in (task_input.get("payload") or {}).get("bead_ids") or ()
    ]
    reasons: dict[str, str] = {}
    for issue in issues:
        if issue.bead is None:
            continue
        if issue.bead not in task_beads:
            return None
        reasons.setdefault(issue.bead, WITHHELD_REASONS[issue.code])
    kept = [
        annotation
        for annotation in annotations
        if str(annotation.get("bead_id")) in task_beads
        and str(annotation.get("bead_id")) not in reasons
    ]
    if not kept:
        return None
    manifest = {
        str(item.get("reference_id"))
        for item in (task_input.get("evidence_manifest") or {}).get("references") or ()
    }
    used = tuple(str(ref) for ref in used_evidence_refs if str(ref) in manifest)
    partial = {**payload, "annotations": kept}
    left = authored_annotation_issues(
        task_input,
        partial,
        used,
        task_kind=task_kind,
        contract_revision=contract_revision,
    )
    if any(
        issue.code != "annotation.bead_missing" or issue.bead not in reasons
        for issue in left
    ):
        return None
    return PartialAnnotations(
        payload=partial,
        used_evidence_refs=used,
        withheld=tuple((bead, reasons[bead]) for bead in task_beads if bead in reasons),
    )
