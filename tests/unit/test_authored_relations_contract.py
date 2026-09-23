"""Pure revision-6 contract checks; no database, provider or owner data."""

from __future__ import annotations

import hashlib
import unittest
import uuid
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from memoriesql.application import authored_relations as ar
from memoriesql.application.module_registry import (
    BuiltInModuleRegistry,
    ProfileDefinition,
)
from memoriesql.application.relation_lifecycle import (
    BeadRelationsInspection,
    RecordClaimEvent,
    RecordRelationEvent,
)
from memoriesql.application.semantic_task_contracts import canonical_json_bytes

ROOT = Path(__file__).resolve().parents[2]
HASH = "a" * 64


def bundle(**updates: Any) -> dict[str, Any]:
    statement = str(uuid.uuid4())
    unit = str(uuid.uuid4())
    clause = {"text": "Fictional note.", "statement_ids": [statement]}
    data: dict[str, Any] = {
        "annotations": [{
            "bead_id": str(uuid.uuid4()),
            "event_id": str(uuid.uuid4()),
            "source_unit_id": unit,
            "bead_version_id": str(uuid.uuid4()),
            "expected_bead_version": 0,
            "bead_type_key": "observation",
            "bead_type_revision": 1,
            "statements": [{
                "statement_id": statement,
                "statement_kind": "observation",
                "statement_text": "Fictional note.",
                "evidence": [{"source_unit_id": unit, "content_hash": HASH}],
                "model_run_ref": "orchard.run",
            }],
            "render": {"title": clause, "summary": [clause]},
            "mentions": [],
        }],
        "relations": [],
        "candidate_assessments": [],
        "claims": [],
        "claim_updates": [],
    }
    data.update(updates)
    return data


def statement_of(data: dict[str, Any]) -> str:
    return str(data["annotations"][0]["statements"][0]["statement_id"])


def relation(data: dict[str, Any], candidate: str, **updates: Any) -> dict[str, Any]:
    unit = data["annotations"][0]["source_unit_id"]
    return {
        "relation_id": str(uuid.uuid4()),
        "candidate_bead_id": candidate,
        "direction": "from_authored",
        "relation_type": {"key": "supports", "revision": 1},
        "basis": "agent_inferred",
        "authored_statement_ids": [statement_of(data)],
        "candidate_statement_ids": [str(uuid.uuid4())],
        "evidence": [{"source_unit_id": unit, "content_hash": HASH}],
        "rationale": "Fictional rationale.",
        "qualification": None,
        "author_confidence": 0.5,
    } | updates


class AuthoredRelationsContract(unittest.TestCase):
    def test_empty_bundle_is_valid_and_optional(self) -> None:
        output = ar.RelatedExecutionOutput.model_validate(bundle())
        self.assertEqual((output.relations, output.claims), ((), ()))

    def test_confidence_is_a_two_decimal_diagnostic(self) -> None:
        candidate = str(uuid.uuid4())
        data = bundle()
        for value in (0, 0.01, 0.29, 0.57, 1):
            ar.AuthoredRelation.model_validate(relation(data, candidate, author_confidence=value))
        for value in (0.555, -0.01, 1.01, float("nan")):
            with self.assertRaises(ValidationError):
                ar.AuthoredRelation.model_validate(relation(data, candidate, author_confidence=value))

    def test_coverage_requires_exactly_one_assessment_per_related_candidate(self) -> None:
        candidate = str(uuid.uuid4())
        data = bundle()
        edge = relation(data, candidate)
        with self.assertRaisesRegex(ValidationError, "requires its assessment"):
            ar.RelatedExecutionOutput.model_validate(dict(data, relations=[edge]))
        with self.assertRaisesRegex(ValidationError, "only it permits"):
            ar.RelatedExecutionOutput.model_validate(dict(data, candidate_assessments=[
                {"candidate_bead_id": candidate, "assessment": "edge", "reason": None}
            ]))
        with self.assertRaisesRegex(ValidationError, "requires its reason"):
            ar.CandidateAssessment.model_validate(
                {"candidate_bead_id": candidate, "assessment": "unassessed", "reason": None}
            )
        ar.RelatedExecutionOutput.model_validate(dict(
            data,
            relations=[edge],
            candidate_assessments=[{"candidate_bead_id": candidate, "assessment": "edge", "reason": None}],
        ))

    def test_claims_and_updates_bind_authored_statements_only(self) -> None:
        data = bundle()
        claim = {
            "claim_id": str(uuid.uuid4()), "statement_ids": [statement_of(data)],
            "subject": "orchard gate", "subject_mention_id": None, "slot": "position",
            "value": "open", "applicability": None,
        }
        foreign = dict(claim, claim_id=str(uuid.uuid4()), statement_ids=[str(uuid.uuid4())])
        with self.assertRaisesRegex(ValidationError, "authored bead"):
            ar.RelatedExecutionOutput.model_validate(dict(data, claims=[foreign]))
        own_target = {
            "target_claim_id": claim["claim_id"], "action": "supersede",
            "related_claim_id": claim["claim_id"], "basis_statement_ids": [statement_of(data)],
            "reason": "Fictional.",
        }
        with self.assertRaisesRegex(ValidationError, "pinned candidate"):
            ar.RelatedExecutionOutput.model_validate(dict(data, claims=[claim], claim_updates=[own_target]))
        for action, related in (("reaffirm", claim["claim_id"]), ("supersede", None)):
            with self.assertRaises(ValidationError):
                ar.ClaimUpdate.model_validate(dict(own_target, action=action, related_claim_id=related))

    def test_whole_output_is_bounded_at_32768_canonical_bytes(self) -> None:
        data = bundle()
        claims = [
            {
                "claim_id": str(uuid.uuid4()), "statement_ids": [statement_of(data)],
                "subject": "s" * 256, "subject_mention_id": None, "slot": "t" * 128,
                "value": "v" * 1024, "applicability": "a" * 1024,
            }
            for _ in range(16)
        ]
        with self.assertRaisesRegex(ValidationError, "32768"):
            ar.RelatedExecutionOutput.model_validate(dict(data, claims=claims))
        ar.RelatedExecutionOutput.model_validate(dict(data, claims=claims[:8]))

    def test_lifecycle_commands_name_exactly_their_related_records(self) -> None:
        claim = uuid.uuid4()
        with self.assertRaises(ValidationError):
            RecordClaimEvent(idempotency_key="k", claim_id=claim, action="supersede", reason="r")
        with self.assertRaises(ValidationError):
            RecordClaimEvent(idempotency_key="k", claim_id=claim, action="retract",
                             related_claim_id=uuid.uuid4(), reason="r")
        with self.assertRaises(ValidationError):
            RecordRelationEvent(idempotency_key="k", relation_id=claim, action="supersede",
                                replacement_relation_id=claim, reason="r")
        with self.assertRaises(ValidationError):
            BeadRelationsInspection(outcome="unavailable", bead_id=claim)

    def test_sql_pins_match_the_revision_six_contract(self) -> None:
        migration = (ROOT / "migrations/0027_authored_claims_and_relations.sql").read_text()
        registry = ar.load_related_authorship_task_registry(
            BuiltInModuleRegistry._from_source_controlled((), (ProfileDefinition("core", ()),), {})
        )
        for pin in (
            ar.RELATED_EXECUTION_TASK.contract_hash,
            registry.registry_hash,
            ar.RELATED_EXECUTION_TASK.output_contract.schema_hash,
        ):
            self.assertIn(pin, migration)

    def test_apply_command_binds_the_exact_canonical_payload(self) -> None:
        payload = ar.RelatedExecutionOutput.model_validate(bundle())
        data = canonical_json_bytes(payload.model_dump(mode="json"))
        command = dict(
            idempotency_key="k", tenant_id=uuid.uuid4(), workspace_id=uuid.uuid4(),
            access_scope_id=uuid.uuid4(), task_id=uuid.uuid4(), attempt_id=uuid.uuid4(),
            lease_generation=1, task_kind="memory.semantic.author-complete-unit",
            output_contract_hash=ar.RELATED_EXECUTION_TASK.output_contract.schema_hash,
            semantic_result_hash=hashlib.sha256(data).hexdigest(),
            semantic_payload_canonical_json=data.decode(),
            used_evidence_refs=[str(payload.annotations[0].source_unit_id)],
            model_run_refs=["orchard.run"],
            payload=payload.model_dump(mode="json"),
        )
        self.assertEqual(ar.ApplyRelatedAuthorship.model_validate(command).contract_version, 7)
        with self.assertRaisesRegex(ValidationError, "hash mismatch"):
            ar.ApplyRelatedAuthorship.model_validate(dict(command, semantic_result_hash=HASH))


if __name__ == "__main__":
    unittest.main()
