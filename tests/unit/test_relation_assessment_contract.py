"""Pure relation assessment contract checks; no database, provider or owner data."""

from __future__ import annotations

import hashlib
import unittest
import uuid
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from memoriesql.application import relation_assessment as ra
from memoriesql.application.module_registry import (
    BuiltInModuleRegistry,
    ProfileDefinition,
)
from memoriesql.application.relation_inspection import BeadRelationsInspectionV2
from memoriesql.application.semantic_task_contracts import canonical_json_bytes

ROOT = Path(__file__).resolve().parents[2]
HASH = "a" * 64
SUBJECT, OTHER = sorted((uuid.uuid4(), uuid.uuid4()), key=str)


def endpoint(bead: uuid.UUID, *statements: str) -> dict[str, Any]:
    return {"bead_id": str(bead), "bead_version_id": str(uuid.uuid4()), "statement_ids": list(statements)}


def proposal(**updates: Any) -> dict[str, Any]:
    source, target, basis = (str(uuid.uuid4()) for _ in range(3))
    data: dict[str, Any] = {
        "proposal_id": str(uuid.uuid4()),
        "relation_type": {"key": "caused_by", "revision": 1},
        "source": endpoint(SUBJECT, source),
        "target": endpoint(OTHER, target),
        "basis_statements": [{"bead_id": str(SUBJECT), "bead_version_id": str(uuid.uuid4()),
                              "statement_id": basis}],
        "evidence": [{"statement_id": basis, "source_unit_id": str(uuid.uuid4()), "content_hash": HASH}],
        "basis": "source_stated",
        "qualification": "Only under the fictional dry-week conditions.",
        "rationale": "The fictional report states the connection.",
        "author_confidence": 0.8,
        "retires": None,
    }
    return data | updates


def dispositions(related: bool = True) -> list[dict[str, Any]]:
    rows = []
    for first, second in ((SUBJECT, SUBJECT), (SUBJECT, OTHER), (OTHER, OTHER)):
        is_related = related and first != second
        rows.append({"first_bead_id": str(first), "second_bead_id": str(second),
                     "disposition": "related" if is_related else "not_related",
                     "abstention": None, "reason": None})
    return rows


def judgment(item: dict[str, Any], **updates: Any) -> dict[str, Any]:
    return {"proposal_id": item["proposal_id"], "outcome": "assessed", "consistent": True,
            "warranted": [{"relation_type": item["relation_type"], "direction": "as_proposed"}],
            "abstention": None, "rationale": "The fictional evidence states it."} | updates


def contribution(items: list[dict[str, Any]], **updates: Any) -> dict[str, Any]:
    return {"contract_version": 1, "request_id": str(uuid.uuid4()), "model_run_ref": "orchard.specialist",
            "author_run_ref": "orchard.author", "packet_sha256": HASH, "proposals_sha256": HASH,
            "vocabulary_sha256": HASH,
            "decision": {"contract_version": 1, "judgments": [judgment(i) for i in items]}} | updates


class RelationAssessmentContract(unittest.TestCase):
    def test_sql_pins_match_the_relation_assessment_contract(self) -> None:
        migration = (ROOT / "migrations/0029_relation_assessment.sql").read_text()
        registry = ra.load_relation_assessment_task_registry(
            BuiltInModuleRegistry._from_source_controlled((), (ProfileDefinition("core", ()),), {})
        )
        for pin in (
            ra.RELATION_ASSESSMENT_TASK.contract_hash,
            registry.registry_hash,
            ra.RELATION_ASSESSMENT_TASK.output_contract.schema_hash,
            ra.SPECIALIST_INPUT.schema_hash,
            ra.SPECIALIST_OUTPUT.schema_hash,
        ):
            self.assertIn(pin, migration)

    def test_proposals_bind_distinct_existing_statements_with_attribution(self) -> None:
        ra.RelationProposal.model_validate(proposal())
        shared = str(uuid.uuid4())

        def pin(statement: str) -> dict[str, Any]:
            return {"statement_id": statement, "source_unit_id": str(uuid.uuid4()), "content_hash": HASH}

        basis = [{"bead_id": str(SUBJECT), "bead_version_id": str(uuid.uuid4()), "statement_id": shared}]
        invalid = {
            "endpoints overlap": proposal(source=endpoint(SUBJECT, shared), target=endpoint(SUBJECT, shared),
                                          basis="agent_inferred", basis_statements=[], evidence=[pin(shared)]),
            "basis is an endpoint": proposal(source=endpoint(SUBJECT, shared), basis_statements=basis,
                                             evidence=[pin(shared)]),
            "source-stated without basis": proposal(source=endpoint(SUBJECT, shared), basis_statements=[],
                                                    evidence=[pin(shared)]),
            "foreign evidence": proposal(evidence=[pin(str(uuid.uuid4()))]),
            "three decimals": proposal(author_confidence=0.805),
            "blank rationale": proposal(rationale="   "),
        }
        for name, data in invalid.items():
            with self.subTest(name), self.assertRaises(ValidationError):
                ra.RelationProposal.model_validate(data)
        inferred = proposal(source=endpoint(SUBJECT, shared), basis="agent_inferred", basis_statements=[],
                            evidence=[pin(shared)])
        ra.RelationProposal.model_validate(inferred)

    def test_every_pinned_pair_is_disposed_and_related_needs_a_proposal(self) -> None:
        item = proposal()
        ra.RelationAuthorOutput.model_validate({"proposals": [item], "dispositions": dispositions()})
        with self.assertRaisesRegex(ValidationError, "related disposition"):
            ra.RelationAuthorOutput.model_validate({"proposals": [item], "dispositions": dispositions(False)})
        with self.assertRaisesRegex(ValidationError, "related disposition"):
            ra.RelationAuthorOutput.model_validate({"proposals": [], "dispositions": dispositions()})
        with self.assertRaisesRegex(ValidationError, "canonical order"):
            ra.PairDisposition(first_bead_id=OTHER, second_bead_id=SUBJECT, disposition="not_related")
        with self.assertRaisesRegex(ValidationError, "abstention"):
            ra.PairDisposition(first_bead_id=SUBJECT, second_bead_id=OTHER, disposition="abstained")
        with self.assertRaisesRegex(ValidationError, "reason"):
            ra.PairDisposition(first_bead_id=SUBJECT, second_bead_id=OTHER, disposition="not_assessed")
        ra.PairDisposition(first_bead_id=SUBJECT, second_bead_id=OTHER, disposition="not_assessed",
                           reason="Not considered in this pass.")
        beads = tuple(ra.PinnedBead.model_construct(bead_id=b) for b in (OTHER, SUBJECT))
        self.assertEqual(ra.required_pairs(beads),
                         frozenset({(SUBJECT, SUBJECT), (SUBJECT, OTHER), (OTHER, OTHER)}))

    def test_agreement_is_on_the_exact_assertion_and_direction(self) -> None:
        item = ra.RelationProposal.model_validate(proposal())
        agreed = ra.RelationJudgment.model_validate(judgment(proposal() | {"proposal_id": str(item.proposal_id)}))
        self.assertTrue(agreed.agrees_with(item))
        for updates in (
            {"consistent": False},
            {"warranted": [{"relation_type": {"key": "caused_by", "revision": 1}, "direction": "reversed"}]},
            {"warranted": [{"relation_type": {"key": "led_to", "revision": 1}, "direction": "as_proposed"}]},
        ):
            with self.subTest(updates):
                self.assertFalse(ra.RelationJudgment.model_validate(
                    judgment(proposal() | {"proposal_id": str(item.proposal_id)}, **updates)).agrees_with(item))
        abstained = ra.RelationJudgment(proposal_id=item.proposal_id, outcome="abstained",
                                        abstention="insufficient_evidence", rationale="Not in the packet.")
        self.assertFalse(abstained.agrees_with(item))
        with self.assertRaises(ValidationError):
            ra.RelationJudgment(proposal_id=item.proposal_id, outcome="abstained", rationale="No reason.")
        with self.assertRaises(ValidationError):
            ra.RelationJudgment(proposal_id=item.proposal_id, outcome="assessed", rationale="No verdict.")

    def test_every_proposal_is_judged_once_by_a_distinct_run(self) -> None:
        items = [proposal(), proposal(relation_type={"key": "led_to", "revision": 1})]
        # One disposition row covers both proposals over the same pair.
        output = ra.RelationAssessmentOutput.model_validate(
            {"proposals": items, "dispositions": dispositions(), "specialist_contributions": [contribution(items)]})
        self.assertEqual(len(output.accepted()), 2)
        with self.assertRaisesRegex(ValidationError, "judged exactly once"):
            ra.RelationAssessmentOutput.model_validate(
                {"proposals": items, "dispositions": dispositions(),
                 "specialist_contributions": [contribution(items[:1])]})
        with self.assertRaisesRegex(ValidationError, "distinct attributable runs"):
            ra.RelationSpecialistContribution.model_validate(
                contribution(items, model_run_ref="orchard.author"))
        with self.assertRaisesRegex(ValidationError, "judged exactly once"):
            ra.RelationAssessmentOutput.model_validate(
                {"proposals": [], "dispositions": dispositions(False),
                 "specialist_contributions": [contribution(items)]})

    def test_a_reconsideration_carries_only_unaccepted_proposals(self) -> None:
        item = proposal()
        refused = judgment(item, consistent=False)
        ra.Reconsideration.model_validate(
            {"reconsiders_task_id": str(uuid.uuid4()), "proposals": [item], "judgments": [refused]})
        with self.assertRaisesRegex(ValidationError, "only unaccepted"):
            ra.Reconsideration.model_validate(
                {"reconsiders_task_id": str(uuid.uuid4()), "proposals": [item], "judgments": [judgment(item)]})

    def test_apply_command_binds_the_exact_canonical_payload(self) -> None:
        items = [proposal()]
        payload = ra.RelationAssessmentOutput.model_validate(
            {"proposals": items, "dispositions": dispositions(), "specialist_contributions": [contribution(items)]})
        data = canonical_json_bytes(payload.model_dump(mode="json"))
        command = dict(
            idempotency_key="k", tenant_id=uuid.uuid4(), workspace_id=uuid.uuid4(),
            access_scope_id=uuid.uuid4(), task_id=uuid.uuid4(), attempt_id=uuid.uuid4(),
            lease_generation=1, output_contract_hash=ra.RELATION_ASSESSMENT_TASK.output_contract.schema_hash,
            semantic_result_hash=hashlib.sha256(data).hexdigest(),
            semantic_payload_canonical_json=data.decode(),
            used_evidence_refs=[str(uuid.uuid4())],
            model_run_refs=["orchard.author", "orchard.specialist"],
            payload=payload.model_dump(mode="json"),
        )
        self.assertEqual(ra.ApplyRelationAssessment.model_validate(command).expected_schema_version, 29)
        with self.assertRaisesRegex(ValidationError, "hash mismatch"):
            ra.ApplyRelationAssessment.model_validate(dict(command, semantic_result_hash=HASH))
        with self.assertRaisesRegex(ValidationError, "run tree"):
            ra.ApplyRelationAssessment.model_validate(dict(command, model_run_refs=["orchard.author"]))

    def test_activation_names_distinct_beads_and_one_revision_per_key(self) -> None:
        pin = {"key": "caused_by", "revision": 1}
        base = {"idempotency_key": "k", "subject_bead_id": str(SUBJECT), "relation_vocabulary": [pin],
                "dispatch_policy_id": str(uuid.uuid4())}
        ra.ActivateRelationAssessment.model_validate(base | {"candidate_bead_ids": [str(OTHER)]})
        for updates in ({"candidate_bead_ids": [str(SUBJECT)]},
                        {"candidate_bead_ids": [str(OTHER), str(OTHER)]},
                        {"relation_vocabulary": [pin, pin | {"revision": 2}]}):
            with self.subTest(updates), self.assertRaises(ValidationError):
                ra.ActivateRelationAssessment.model_validate(base | updates)

    def test_an_unavailable_read_discloses_nothing(self) -> None:
        with self.assertRaises(ValidationError):
            BeadRelationsInspectionV2(outcome="unavailable", bead_id=SUBJECT)
        self.assertEqual(BeadRelationsInspectionV2(outcome="budget_exhausted").relations, ())


if __name__ == "__main__":
    unittest.main()
