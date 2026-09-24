"""Generate the explicit relation assessment record; no discovery."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from memoriesql.application import relation_assessment as ra
from memoriesql.application import relation_inspection as ri
from memoriesql.application.module_registry import (
    BuiltInModuleRegistry,
    ProfileDefinition,
)

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "contracts/records/memoriesql-relation-assessment-v1.json"


def record() -> dict[str, object]:
    registry = ra.load_relation_assessment_task_registry(
        BuiltInModuleRegistry._from_source_controlled((), (ProfileDefinition("core", ()),), {})
    )
    models = (
        ra.PinnedStatement, ra.PinnedBead, ra.StatementPin, ra.RelationEndpoint,
        ra.RelationEvidencePin, ra.RetiredAssertion, ra.RelationProposal, ra.PairDisposition,
        ra.RelationAuthorOutput, ra.WarrantedRelation, ra.RelationJudgment,
        ra.RelationSpecialistDecision, ra.RelationSpecialistContribution,
        ra.RelationAssessmentOutput, ra.Reconsideration, ra.RelationAssessmentPayload,
        ra.RelationAssessmentInput, ra.EvidencePart, ra.EvidenceExcerpt,
        ra.RelationAuthorPacket, ra.RelationSpecialistPacket, ra.RelationAuthorStep,
        ra.ActivateRelationAssessment, ra.RelationAssessmentActivationReceipt,
        ra.ApplyRelationAssessment, ri.InspectBeadRelationsV2, ri.BeadRelationsInspectionV2,
        ri.InspectRelationVocabulary, ri.RelationVocabularyInspection,
    )
    return dict(
        id="memoriesql.relation-assessment.v1", version="1", kind="json_schema",
        status="available",
        summary="A separate relation task after acceptance: exact assertions over existing statements of explicitly pinned accepted beads, a disposition for every pinned pair, and a provider-neutral specialist whose agreement on the exact assertion is required before code accepts it; no inferred edges, votes or winners.",
        contract=dict(
            schemas={m.__name__: m.model_json_schema() for m in models},
            task_definition=ra.RELATION_ASSESSMENT_TASK.canonical_payload(),
            task_contract_hash=ra.RELATION_ASSESSMENT_TASK.contract_hash,
            registry_hash=registry.registry_hash,
            specialist=dict(
                agent_key=ra.SPECIALIST_KEY,
                input_contract=ra.SPECIALIST_INPUT.reference.model_dump(mode="json"),
                output_contract=ra.SPECIALIST_OUTPUT.reference.model_dump(mode="json"),
                model_profile=ra.SPECIALIST_PROFILE.model_dump(mode="json"),
            ),
            placement="Runs only after its subject bead version is accepted, against immutable accepted versions. It never adds statements, changes a bead or vetoes acceptance; a refused, failed or paused task leaves every bead and receipt untouched and keeps its own status. Revision 1 is limited to relations.",
            evidence="The author and the specialist receive the actual authorized evidence behind every pinned statement: the whole observation unit or every normalized part of the sealed package each pin names. An attestor records what each provider request received and re-derives it from storage; an oversized or incomplete packet or batch is refused, never truncated. Each assertion cites 1-8 exact statement-evidence pairs of its own endpoint or basis statements.",
            binding="An endpoint is a pinned accepted bead version and 1-8 of its statements; neither endpoint has to be the subject and both may sit in one bead when their statements differ. Separate basis statements carry attribution; a source-stated assertion names at least one.",
            coverage="Every unordered pair of pinned beads, each bead with itself, gets related, not_related, abstained (no_fit, insufficient_evidence or ambiguous) or not_assessed with a reason. Not assessed never means unrelated, and coverage never searches other beads.",
            agreement="Code accepts a proposal only when the specialist finds that exact assertion consistent and its warranted set contains the author's predicate, revision and direction. Disagreement or abstention leaves the proposal unaccepted with both contributions recorded; reconsideration is a new, explicitly activated task linked to the assessment whose disagreement it carries, never automatic and never capped. Each task runs one bounded attempt and is never retried automatically. Author confidence is diagnostic, never authority.",
            lifecycle="An accepted assertion may retire an earlier active assertion of either kind in the same apply; retirement is final. One cycle check covers both kinds under the per-tenant, per-key lock, and accepted derived_from between different beads feeds bead-level roots. Revision 1 is an explicitly incomplete substrate: assessed assertions have no governed confirm, dispute or retract, and append-only dispute and retraction must exist before assessed relations are used durably in live memory or qualified for recall.",
        ),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected = json.dumps(record(), indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    if args.check:
        if PATH.read_text() != expected:
            raise SystemExit("relation assessment contract drift")
    else:
        PATH.write_text(expected)


if __name__ == "__main__":
    main()
