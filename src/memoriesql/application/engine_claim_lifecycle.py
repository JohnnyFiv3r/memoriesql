"""Prepare governed lifecycle commands from bound Core Memory v2 correction evidence."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from uuid import UUID

from memoriesql.application.relation_lifecycle import (
    ClaimAction,
    InspectedClaim,
    LifecycleEvidence,
    RecordClaimEvent,
)
from memoriesql.application.semantic_task_contracts import canonical_json_bytes

_CLAIM_EDGE_ACTIONS: dict[str, ClaimAction] = {
    "repl": "supersede",
    "withdraws": "retract",
    "!contradicts": "dispute",
}


@dataclass(frozen=True)
class _BoundClaimEndpoint:
    """An enrolled engine chunk and its explicitly selected accepted evidence."""

    enrollment_id: UUID
    producer_workspace: str
    chunk_id: UUID
    evidence: LifecycleEvidence
    claim: InspectedClaim | None


def _prepare_claim_event(
    *,
    record_key: str,
    arrow: str,
    origin: _BoundClaimEndpoint,
    target: _BoundClaimEndpoint,
    reviewed_reason: str,
    expected_last_event_id: UUID | None,
) -> RecordClaimEvent:
    """Prepare a governed command from an already bound, review-selected edge.

    The upstream binder must establish that the edge was committed in the same
    enrollment as its endpoints and that each chunk maps to the pinned evidence.
    This function works with accepted statements and claims. It creates no
    acceptance or write authority. Submit through the existing human lifecycle
    command and retry with the same payload and compared head.
    """
    action = _CLAIM_EDGE_ACTIONS.get(arrow)
    if (
        action is None
        or record_key != f"edge:{arrow}:{origin.chunk_id}:{target.chunk_id}"
    ):
        raise ValueError("expected a committed correction edge matching both endpoints")
    if (
        origin.enrollment_id != target.enrollment_id
        or not target.producer_workspace.strip()
        or origin.producer_workspace != target.producer_workspace
        or origin.chunk_id == target.chunk_id
    ):
        raise ValueError(
            "correction endpoints must be distinct chunks of one enrollment"
        )
    if target.claim is None or (action != "retract" and origin.claim is None):
        raise ValueError("missing accepted claim for the correction")
    for endpoint in (target, origin):
        if endpoint.claim is not None and all(
            statement.statement_id != endpoint.evidence.statement_id
            for statement in endpoint.claim.statements
        ):
            raise ValueError("correction evidence must belong to the pinned claim")
    if origin.claim is not None and origin.claim.claim_id == target.claim.claim_id:
        raise ValueError("a claim cannot correct itself")

    related = None if action == "retract" else origin.claim
    identity = (str(target.enrollment_id), target.producer_workspace, record_key)
    key = hashlib.sha256(canonical_json_bytes(identity)).hexdigest()
    return RecordClaimEvent(
        claim_id=target.claim.claim_id,
        related_claim_id=related.claim_id if related else None,
        action=action,
        idempotency_key=f"core-memory-v2:{key}",
        reason=reviewed_reason,
        evidence=(target.evidence, origin.evidence),
        expected_last_event_id=expected_last_event_id,
    )
