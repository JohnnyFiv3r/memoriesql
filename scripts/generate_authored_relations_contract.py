"""Generate the explicit authored claims and relations record; no discovery."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from memoriesql.application import authored_relations as ar
from memoriesql.application import relation_lifecycle as rl
from memoriesql.application.module_registry import (
    BuiltInModuleRegistry,
    ProfileDefinition,
)

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "contracts/records/memoriesql-authored-relations-v1.json"


def record() -> dict[str, object]:
    registry = ar.load_related_authorship_task_registry(
        BuiltInModuleRegistry._from_source_controlled((), (ProfileDefinition("core", ()),), {})
    )
    models = (
        ar.RelationTypePin, ar.RelationTypeDefinition, ar.SourceClock, ar.RelationCandidate,
        ar.RelatedExecutionInput, ar.AuthoredRelation, ar.CandidateAssessment,
        ar.AuthoredClaim, ar.ClaimUpdate, ar.RelatedExecutionOutput, ar.RelatedAuthorStep,
        ar.ActivateRelatedAuthorship, ar.RelatedActivationReceipt, ar.ApplyRelatedAuthorship,
        rl.RecordClaimEvent, rl.ClaimEventReceipt, rl.RecordRelationEvent,
        rl.RelationEventReceipt, rl.ProposeRelationType, rl.RelationTypeProposalReceipt,
        rl.DecideRelationType, rl.RelationTypeDecisionReceipt, rl.InspectBeadRelations,
        rl.BeadRelationsInspection,
    )
    return dict(
        id="memoriesql.authored-relations.v1", version="1", kind="json_schema",
        status="available",
        summary="Optional authored claims, relations to explicitly pinned candidates, candidate coverage and append-only lifecycle history through revision-6 authorship; no inferred edges, conflicts or winners.",
        contract=dict(
            schemas={m.__name__: m.model_json_schema() for m in models},
            task_definition=ar.RELATED_EXECUTION_TASK.canonical_payload(),
            task_contract_hash=ar.RELATED_EXECUTION_TASK.contract_hash,
            registry_hash=registry.registry_hash,
            evidence="Each relation cites 1-8 statement-evidence pairs of its own named endpoint propositions; lifecycle judgments cite existing accepted pairs. Derivation roots are computed as of a known time from the whole active derived_from lineage: a derivative inherits its lineage's roots and adds none of its own, beads that derive only from one another share one root, shared roots count once, and a lineage past its limit is reported as budget exhausted, never truncated.",
            empty_set="Relations, claims and claim updates may be empty. Every pinned candidate is assessed edge, no_edge or unassessed with a reason; unassessed never means unrelated. Only a revision-6 apply receipt proves the capability.",
            uncertainty="Relation type, direction, basis and 0-1 author confidence are the author's diagnostic proposal, never authority. Competing supersessions and open disputes stay visible; neither recency nor occurrence order selects a winner.",
            lifecycle="Claims derive current, superseded, disputed or retracted and relations derive active, disputed, superseded, retracted or reassessment_pending from append-only authored and governed events as known at a time.",
            vocabulary="Eleven immutable built-in revision-1 types plus workspace types that enter only through a proposal and a human decision; activation pins exact latest active revisions.",
        ),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected = json.dumps(record(), indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    if args.check:
        if PATH.read_text() != expected:
            raise SystemExit("authored relations contract drift")
    else:
        PATH.write_text(expected)


if __name__ == "__main__":
    main()
