"""Fictional authored claims, relations, evidence and lifecycle history (schema 27)."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import unittest
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import patch

import psycopg
from psycopg import sql
from pydantic_ai.messages import ModelRequest, ToolCallPart, UserPromptPart

from memoriesql.application.authored_relations import (
    RELATED_EXECUTION_TASK,
    ActivateRelatedAuthorship,
    RelationTypePin,
    load_related_authorship_task_registry,
)
from memoriesql.application.canonical_transactions import SourceType
from memoriesql.application.engine_claim_lifecycle import (
    _BoundClaimEndpoint,
    _prepare_claim_event,
)
from memoriesql.application.engine_intake import (
    BindEngineEventEvidence,
    EngineChunkEvidence,
    EngineChunkSpan,
)
from memoriesql.application.evidence_packages import NativeFacts
from memoriesql.application.logical_unit_materialization import LogicalEventDeclaration
from memoriesql.application.relation_inspection import InspectBeadRelationsV2
from memoriesql.application.relation_lifecycle import (
    DecideRelationType,
    InspectBeadRelations,
    LifecycleEvidence,
    ProposeRelationType,
    RecordClaimEvent,
    RecordRelationEvent,
    RelationAction,
)
from memoriesql.application.source_enrollment import RevokeExactSource
from memoriesql.application.source_revisiting import SourceDelivery
from memoriesql.infrastructure.jobs.source_revisiting_access import (
    SourceRevisitingEvidenceAccess,
)
from memoriesql.infrastructure.postgres.authorization import PostgresAuthorizationPort
from memoriesql.infrastructure.postgres.engine_intake import PostgresEngineIntake
from memoriesql.infrastructure.postgres.relation_assessment import (
    PostgresRelationAssessments,
)
from memoriesql.infrastructure.postgres.relation_lifecycle import (
    PostgresRelationLifecycle,
)
from memoriesql.infrastructure.postgres.source_enrollment import (
    PostgresSourceEnrollment,
)

if TYPE_CHECKING:
    from tests.runtime import agent_relation_fixtures as relation_agents
    from tests.runtime import test_local_entity_mentions as fixtures
    from tests.runtime import test_source_revisiting as source_fixtures
    from tests.runtime.test_postgres_runtime import migrate
else:
    import agent_relation_fixtures as relation_agents
    import test_local_entity_mentions as fixtures
    import test_source_revisiting as source_fixtures
    from test_postgres_runtime import migrate

BUILT_INS = (
    "supports", "contradicts", "caused_by", "led_to", "enables", "part_of",
    "depends_on", "blocks", "derived_from", "supersedes", "associated_with",
)
VOCABULARY = tuple(RelationTypePin(key=key, revision=1) for key in BUILT_INS)
RELATION_TABLES = (
    "relation_types", "relation_type_revisions", "relation_type_candidates",
    "relation_type_decisions", "bead_claims", "bead_claim_statements",
    "bead_claim_events", "bead_relations", "bead_relation_statements",
    "bead_relation_events", "relation_candidate_assessments", "semantic_evidence_links",
)
Plan = Any


class AuthoredRelations(fixtures.LocalMentions):
    def setUp(self) -> None:
        super().setUp()
        migrate(self.db, expected_current_version=22, target_version=27)
        self.policies = {self.source: self.revision_six_policy(self.dispatch_policy)}
        self.producers = {self.source: self.policy}
        self.db.execute(
            "INSERT INTO memoriesql.semantic_worker_claim_policies SELECT c.tenant_id,c.workspace_id,c.principal_id,c.pairing_grant_id,p.semantic_registry_hash,p.task_kind,p.contract_revision,p.queue_name FROM memoriesql.semantic_worker_claim_policies c JOIN memoriesql.semantic_task_admission_policies p ON p.task_kind=c.task_kind AND p.contract_revision=6 WHERE c.principal_id=%s AND c.contract_revision=4",
            (self.worker_principal,),
        )
        self.lifecycle = PostgresRelationLifecycle(
            self.db, credential_sha256=self.secret_hash, workspace_id=self.workspace
        )
        self.plan: Plan | None = None
        self.statement_text = "Fictional orchard note."
        self.checkpoints = {self.source: "orchard.raw"}
        self.positions = {self.source: (0, 0)}

    # -- fixture helpers -------------------------------------------------

    def revision_six_policy(self, template: uuid.UUID, source: uuid.UUID | None = None) -> uuid.UUID:
        policy = uuid.uuid4()
        self.db.execute(
            "INSERT INTO memoriesql.complete_input_dispatch_policies SELECT tenant_id,%s,workspace_id,access_scope_id,COALESCE(%s,source_object_id),attestor_principal_id,approved_by_principal_id,qualification_evidence_sha256,created_at,expires_at,status,6 FROM memoriesql.complete_input_dispatch_policies WHERE dispatch_policy_id=%s",
            (policy, source, template),
        )
        return policy

    def add_source(self, name: str, *, engine_scope: str | None = None) -> uuid.UUID:
        """A second fictional source object with its own producer and dispatch policy."""
        other = uuid.uuid4()
        self.db.execute(
            "INSERT INTO memoriesql.protected_resources SELECT tenant_id,workspace_id,access_scope_id,%s,resource_kind,owner_user_id,status,created_by_principal_id,created_at FROM memoriesql.protected_resources WHERE resource_id=%s",
            (other, self.source),
        )
        self.db.execute(
            "INSERT INTO memoriesql.source_objects(tenant_id,workspace_id,access_scope_id,source_object_id,source_system,object_kind,external_object_id,schema_version,metadata,created_at,last_observed_at,owner_user_id) SELECT tenant_id,workspace_id,access_scope_id,%s,COALESCE(%s,source_system),COALESCE(%s,object_kind),%s,schema_version,metadata,created_at,last_observed_at,owner_user_id FROM memoriesql.source_objects WHERE source_object_id=%s",
            (
                other,
                "core-memory" if engine_scope else None,
                "engine_scope" if engine_scope else None,
                engine_scope or "orchard." + name,
                self.source,
            ),
        )
        producer = uuid.uuid4()
        self.db.execute(
            "INSERT INTO memoriesql.evidence_producer_policies SELECT tenant_id,workspace_id,access_scope_id,%s,%s,producer_principal_id,qualification_ref,normalization_policy_version,qualification_evidence_sha256,approved_by_principal_id,created_at,expires_at,status FROM memoriesql.evidence_producer_policies WHERE producer_policy_id=%s",
            (producer, other, self.policy),
        )
        self.producers[other] = producer
        self.policies[other] = self.revision_six_policy(self.dispatch_policy, other)
        self.checkpoints[other] = "orchard." + name + ".raw"
        self.positions[other] = (0, 0)
        return other

    def unit(
        self,
        text: str,
        key: str,
        *,
        source: uuid.UUID | None = None,
        occurred_at: datetime | None = None,
    ) -> Any:
        """Capture, package and materialize one fictional unit as its own event."""
        source = source or self.source
        original = self.source
        self.source = source
        self.offset, self.sequence = self.positions[source]
        fixture = __import__("sys").modules[self.part.__module__]
        builder = fixture.build_capture_source_range_command
        checkpoint = self.checkpoints[source]
        try:
            with patch.object(
                fixture,
                "build_capture_source_range_command",
                side_effect=lambda **kw: builder(**(kw | {"checkpoint_key": checkpoint})),
            ):
                part = self.part(text).model_copy(
                    update={"component_key": "orchard.whole", "component_offset": 0}
                )
            native = NativeFacts(
                native_id="native." + key,
                occurred_at=occurred_at,
                time_precision="second" if occurred_at else None,
            )
            package = self.create((part,), occurrence_key="orchard." + key, native=native)
            self.append(package, part)
            self.seal(package)
            self.positions[source] = (self.offset, self.sequence)
            return self.materializer.materialize(
                self.materialization(
                    package,
                    producer_policy_id=self.producers[source],
                    event=LogicalEventDeclaration(
                        event_key="orchard.event." + key,
                        identity_basis="native",
                        source_type=SourceType.TRANSCRIPT,
                        native=native.model_copy(update={"native_id": "event." + key}),
                        expected_units=1,
                    ),
                )
            )
        finally:
            self.source = original

    def activate(
        self,
        bound: Any,
        key: str,
        candidates: tuple[uuid.UUID, ...] = (),
        vocabulary: tuple[RelationTypePin, ...] = VOCABULARY,
        source: uuid.UUID | None = None,
    ) -> Any:
        self.activation = self.complete.activate_related(
            ActivateRelatedAuthorship(
                idempotency_key="orchard.related." + key,
                binding_task_id=bound.task_id,
                dispatch_policy_id=self.policies[source or self.source],
                relation_candidates=candidates,
                relation_vocabulary=vocabulary,
            )
        )
        return self.activation

    def author(
        self,
        text: str,
        key: str,
        *,
        candidates: tuple[uuid.UUID, ...] = (),
        plan: Plan | None = None,
        source: uuid.UUID | None = None,
        occurred_at: datetime | None = None,
        expect: str | None = "succeeded",
    ) -> uuid.UUID:
        bound = self.unit(text, key, source=source, occurred_at=occurred_at)
        self.activate(bound, key, candidates, source=source)
        self.statement_text = text
        self.plan = plan
        self.steps = []
        self.mentions = [dict(self.mentions[0], entity_mention_id=str(uuid.uuid4()))] if self.mentions else []
        result = asyncio.run(self.worker().run_once())
        if expect is None:
            self.assertNotEqual(result.task_status, "succeeded", result)
        else:
            self.assertEqual(result.task_status, expect, result)
        return cast(uuid.UUID, bound.bead_id)

    def worker(self, callback: Any = None, *, recorder: Any = True, **kwargs: Any) -> Any:
        return source_fixtures.SourceRevisiting.worker(
            self,
            callback,
            recorder=recorder,
            task_definition=RELATED_EXECUTION_TASK,
            registry_factory=load_related_authorship_task_registry,
        )

    def response(self, messages: Any, info: Any) -> Any:
        frame = json.loads(
            cast(
                str,
                next(
                    p.content
                    for m in messages
                    if isinstance(m, ModelRequest)
                    for p in m.parts
                    if isinstance(p, UserPromptPart)
                ),
            )
        )
        if "relation_candidates" not in frame["task_input"]["payload"]:
            return super().response(messages, info)
        # The inherited fictional author understands the revision-4 frame only.
        legacy = copy.deepcopy(frame)
        legacy["task_input"]["payload"].pop("relation_candidates", None)
        legacy["task_input"]["payload"].pop("relation_vocabulary", None)
        response = super().response(
            [ModelRequest(parts=[UserPromptPart(json.dumps(legacy))])], info
        )
        part = cast(ToolCallPart, response.parts[0])
        data = part.args_as_dict()
        output = data["typed_output"]
        if output is not None:
            bead = output["annotations"][0]
            bead["statements"][0]["statement_text"] = self.statement_text
            bead["render"]["title"]["text"] = self.statement_text[:240]
            bead["render"]["summary"][0]["text"] = self.statement_text
            candidates = frame["task_input"]["payload"]["relation_candidates"]
            extras: dict[str, Any] = {
                "relations": [],
                "candidate_assessments": [
                    {"candidate_bead_id": c["bead_id"], "assessment": "no_edge", "reason": None}
                    for c in candidates
                ],
                "claims": [],
                "claim_updates": [],
            }
            if self.plan is not None:
                self.plan(extras, bead, candidates)
            output.update(extras)
        part.args = data
        return response

    # -- plan builders ---------------------------------------------------

    @staticmethod
    def statement(bead: dict[str, Any]) -> str:
        return cast(str, bead["statements"][0]["statement_id"])

    @staticmethod
    def evidence(bead: dict[str, Any]) -> dict[str, str]:
        return cast(dict[str, str], bead["statements"][0]["evidence"][0])

    def relate(
        self,
        extras: dict[str, Any],
        bead: dict[str, Any],
        candidate: dict[str, Any],
        type_key: str,
        *,
        direction: str = "from_authored",
        basis: str = "source_stated",
        confidence: float = 0.8,
        cite_candidate: bool = True,
    ) -> str:
        relation_id = str(uuid.uuid4())
        evidence = [self.evidence(bead)]
        if cite_candidate:
            evidence.append(candidate["statements"][0]["evidence"][0])
        extras["relations"].append({
            "relation_id": relation_id,
            "candidate_bead_id": candidate["bead_id"],
            "direction": direction,
            "relation_type": {"key": type_key, "revision": 1},
            "basis": basis,
            "authored_statement_ids": [self.statement(bead)],
            "candidate_statement_ids": [candidate["statements"][0]["statement_id"]],
            "evidence": evidence,
            "rationale": "The fictional note states this connection.",
            "qualification": None,
            "author_confidence": confidence,
        })
        for assessment in extras["candidate_assessments"]:
            if assessment["candidate_bead_id"] == candidate["bead_id"]:
                assessment["assessment"] = "edge"
        return relation_id

    def add_claim(
        self, extras: dict[str, Any], bead: dict[str, Any], subject: str, slot: str, value: str
    ) -> str:
        claim_id = str(uuid.uuid4())
        extras["claims"].append({
            "claim_id": claim_id,
            "statement_ids": [self.statement(bead)],
            "subject": subject,
            "subject_mention_id": None,
            "slot": slot,
            "value": value,
            "applicability": None,
        })
        return claim_id

    def update(
        self,
        extras: dict[str, Any],
        bead: dict[str, Any],
        target: str,
        action: str,
        related: str | None = None,
    ) -> None:
        extras["claim_updates"].append({
            "target_claim_id": target,
            "action": action,
            "related_claim_id": related,
            "basis_statement_ids": [self.statement(bead)],
            "reason": "The fictional note bears on the earlier claim.",
        })

    # -- read helpers ----------------------------------------------------

    def inspect(self, bead: uuid.UUID, known_at: datetime | None = None) -> Any:
        return self.lifecycle.inspect_relations(
            InspectBeadRelations(bead_id=bead, known_at=known_at)
        )

    def claim_ids(self, bead: uuid.UUID) -> list[uuid.UUID]:
        return [
            row[0]
            for row in self.db.execute(
                "SELECT claim_id FROM memoriesql.bead_claims WHERE bead_id=%s ORDER BY claim_id",
                (bead,),
            ).fetchall()
        ]

    # -- tests -----------------------------------------------------------

    def test_optional_empty_bundle_accepts_with_revision_six_capability(self) -> None:
        bead = self.author("The fictional pump failed on Monday.", "pump")
        inspection = self.inspect(bead)
        self.assertEqual(inspection.outcome, "available")
        self.assertTrue(inspection.relations_capable)
        self.assertEqual(
            (inspection.claims, inspection.relations, inspection.candidate_assessments),
            ((), (), ()),
        )
        self.assertEqual(
            self.row(
                "SELECT r.task_contract_version,i.operation_kind FROM memoriesql.bead_versions v JOIN memoriesql.semantic_task_receipts r USING(tenant_id,semantic_task_receipt_id) JOIN memoriesql.idempotency_receipts i ON i.tenant_id=r.tenant_id AND i.idempotency_receipt_id=r.idempotency_receipt_id"
            ),
            (6, "complete_input.apply.v5"),
        )

    def test_claims_relation_evidence_and_coverage_accept_atomically(self) -> None:
        pump = self.author(
            "The fictional pump failed on Monday.",
            "pump",
            plan=lambda extras, bead, _: self.add_claim(extras, bead, "orchard pump", "status", "failed"),
        )
        created: dict[str, str] = {}

        def plan(extras: dict[str, Any], bead: dict[str, Any], candidates: list[Any]) -> None:
            created["relation"] = self.relate(extras, bead, candidates[0], "caused_by")
            created["claim"] = self.add_claim(extras, bead, "orchard trees", "wilted count", "three")

        trees = self.author(
            "Three fictional trees wilted because the pump failed.",
            "trees",
            candidates=(pump,),
            plan=plan,
        )
        inspection = self.inspect(trees)
        self.assertEqual(inspection.outcome, "available", inspection)
        (relation,) = inspection.relations
        self.assertEqual(str(relation.relation_id), created["relation"])
        self.assertEqual(
            (relation.relation_type.key, relation.direction, relation.state, relation.basis),
            ("caused_by", "outgoing", "active", "source_stated"),
        )
        self.assertEqual((relation.source_bead_id, relation.target_bead_id), (trees, pump))
        self.assertEqual(relation.author_confidence, 0.8)
        self.assertEqual(len(relation.evidence), 2)
        # Both units come from one fictional source object: one independent root.
        self.assertEqual(relation.independent_root_count, 1)
        self.assertEqual(
            {e.derivation_root_ids for e in relation.evidence}, {(self.source,)}
        )
        self.assertEqual(relation.source_statements[0].text, "Three fictional trees wilted because the pump failed.")
        self.assertEqual(relation.target_statements[0].text, "The fictional pump failed on Monday.")
        (claim,) = inspection.claims
        self.assertEqual((claim.subject, claim.slot, claim.value, claim.state), ("orchard trees", "wilted count", "three", "current"))
        self.assertEqual(claim.source_time.time_basis, "capture_time")
        (assessment,) = inspection.candidate_assessments
        self.assertEqual((assessment.candidate_bead_id, assessment.assessment), (pump, "edge"))
        incoming = self.inspect(pump)
        self.assertEqual(
            [(r.direction, r.relation_id) for r in incoming.relations],
            [("incoming", relation.relation_id)],
        )
        self.assertFalse(incoming.candidate_assessments)
        with self.assertRaises(psycopg.Error), self.db.transaction():
            self.db.execute("UPDATE memoriesql.bead_relations SET author_confidence=0.1")
        with self.assertRaises(psycopg.Error), self.db.transaction():
            self.db.execute("DELETE FROM memoriesql.bead_claims")


    def latest_claim_event(self, claim: str | uuid.UUID) -> uuid.UUID | None:
        row = self.db.execute(
            "SELECT claim_event_id FROM memoriesql.bead_claim_events WHERE claim_id=%s ORDER BY recorded_at DESC, claim_event_id DESC LIMIT 1",
            (claim,),
        ).fetchone()
        return None if row is None else cast(uuid.UUID, row[0])

    def latest_relation_event(self, relation: uuid.UUID) -> uuid.UUID | None:
        row = self.db.execute(
            "SELECT relation_event_id FROM memoriesql.bead_relation_events WHERE relation_id=%s ORDER BY recorded_at DESC, relation_event_id DESC LIMIT 1",
            (relation,),
        ).fetchone()
        return None if row is None else cast(uuid.UUID, row[0])

    def claim_state(self, bead: uuid.UUID) -> list[tuple[str, str, set[str], set[str]]]:
        return [
            (c.value, c.state, set(map(str, c.superseded_by)), set(map(str, c.disputed_with)))
            for c in self.inspect(bead).claims
        ]

    def test_competing_branches_and_disputes_never_pick_a_recency_winner(self) -> None:
        ids: dict[str, str] = {}
        seen: dict[str, str] = {}

        def base(extras: dict[str, Any], bead: dict[str, Any], _: Any) -> None:
            ids["a"] = self.add_claim(extras, bead, "orchard pump", "status", "working")

        a = self.author("The fictional pump works.", "a", plan=base)

        def successor(name: str, value: str) -> Plan:
            def plan(extras: dict[str, Any], bead: dict[str, Any], candidates: list[Any]) -> None:
                ids[name] = self.add_claim(extras, bead, "orchard pump", "status", value)
                (claim,) = candidates[0]["claims"]
                seen[name] = claim["state_at_activation"]
                self.update(extras, bead, claim["claim_id"], "supersede", ids[name])
            return plan

        b = self.author("The fictional pump failed.", "b", candidates=(a,), plan=successor("b", "failed"))
        c = self.author("The fictional pump was replaced.", "c", candidates=(a,), plan=successor("c", "replaced"))
        # The second author saw the first branch and still proposed its own.
        self.assertEqual(seen, {"b": "current", "c": "superseded"})
        # Two authored successors remain competing branches; neither is chosen by time.
        self.assertEqual(self.claim_state(a), [("working", "superseded", {ids["b"], ids["c"]}, set())])
        self.assertEqual(self.claim_state(b), [("failed", "current", set(), set())])
        self.assertEqual(self.claim_state(c), [("replaced", "current", set(), set())])

        def dispute(extras: dict[str, Any], bead: dict[str, Any], candidates: list[Any]) -> None:
            ids["d"] = self.add_claim(extras, bead, "orchard pump", "status", "working")
            self.update(extras, bead, ids["b"], "dispute", ids["d"])

        d = self.author("A later fictional check found the pump working.", "d", candidates=(b,), plan=dispute)
        self.assertEqual(self.claim_state(b), [("failed", "disputed", set(), {ids["d"]})])
        self.assertEqual(self.claim_state(d), [("working", "disputed", set(), {ids["b"]})])

        resolution = RecordClaimEvent(
            idempotency_key="orchard.resolve",
            claim_id=uuid.UUID(ids["b"]),
            action="resolve_dispute",
            related_claim_id=uuid.UUID(ids["d"]),
            reason="The owner reviewed both fictional checks.",
            expected_last_event_id=self.latest_claim_event(ids["b"]),
        )
        receipt = self.lifecycle.record_claim_event(resolution)
        self.assertFalse(receipt.replayed)
        replay = self.lifecycle.record_claim_event(resolution)
        self.assertEqual((replay.claim_event_id, replay.replayed), (receipt.claim_event_id, True))
        with self.assertRaises(psycopg.errors.UniqueViolation):
            self.lifecycle.record_claim_event(resolution.model_copy(update={"reason": "Changed."}))
        self.assertEqual(self.claim_state(b), [("failed", "current", set(), set())])
        self.assertEqual(self.claim_state(d), [("working", "current", set(), set())])
        # The competing claim's history explains its state with the incoming events.
        self.assertEqual(
            [(e.action, str(e.target_id), str(e.related_id)) for e in self.inspect(d).claims[0].events],
            [("dispute", ids["b"], ids["d"]), ("resolve_dispute", ids["b"], ids["d"])],
        )
        # A stale compare-and-swap never appends.
        with self.assertRaises(psycopg.errors.SerializationFailure):
            self.lifecycle.record_claim_event(RecordClaimEvent(
                idempotency_key="orchard.stale",
                claim_id=uuid.UUID(ids["b"]),
                action="retract",
                reason="Stale view.",
                expected_last_event_id=None,
            ))
        # Retracting one successor restores the other as the only replacement.
        self.lifecycle.record_claim_event(RecordClaimEvent(
            idempotency_key="orchard.retract.c",
            claim_id=uuid.UUID(ids["c"]),
            action="retract",
            reason="The replacement note was fictional noise.",
        ))
        self.assertEqual(self.claim_state(a), [("working", "superseded", {ids["b"]}, set())])
        self.assertEqual(self.claim_state(c), [("replaced", "retracted", set(), set())])
        with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState):
            self.lifecycle.record_claim_event(RecordClaimEvent(
                idempotency_key="orchard.retract.c.again",
                claim_id=uuid.UUID(ids["c"]),
                action="reaffirm",
                reason="Retraction is terminal.",
                expected_last_event_id=self.latest_claim_event(ids["c"]),
            ))
        events = [(e.action, e.origin) for e in self.inspect(b).claims[0].events]
        self.assertEqual(events, [("dispute", "authored"), ("resolve_dispute", "governed")])

    def engine_claim_endpoint(
        self, bead: uuid.UUID, enrollment_id: uuid.UUID
    ) -> _BoundClaimEndpoint:
        statement_id, source_unit_id, content_hash = self.row(
            "SELECT e.statement_id,e.evidence_source_unit_id,e.evidence_content_hash "
            "FROM memoriesql.bead_semantic_statement_evidence e "
            "JOIN memoriesql.bead_semantic_statements s USING(tenant_id,statement_id) "
            "WHERE s.bead_id=%s",
            (bead,),
        )
        claims = self.inspect(bead).claims
        return _BoundClaimEndpoint(
            enrollment_id=enrollment_id,
            producer_workspace="fictional-orchard",
            chunk_id=uuid.uuid4(),
            evidence=LifecycleEvidence(
                statement_id=statement_id,
                source_unit_id=source_unit_id,
                content_hash=content_hash,
            ),
            claim=claims[0] if claims else None,
        )

    def revoke_engine_fixture_source(self, source: uuid.UUID) -> None:
        port = PostgresSourceEnrollment(
            self.db, credential_sha256=self.secret_hash, workspace_id=self.workspace
        )
        port.revoke(
            RevokeExactSource(
                request_id=uuid.uuid4(),
                source_object_id=source,
                reason="Withdraw access to the fictional source.",
            )
        )

    def test_committed_engine_replacement_uses_canonical_chunk_resolver(self) -> None:
        fixture = Path(__file__).with_name("engine_201_correction.jsonl")
        if not fixture.is_file():
            fixture = fixture.parent.parent / "fixtures" / fixture.name
        first, first_chunk, second, second_chunk, edge = map(
            json.loads, fixture.read_bytes().splitlines()
        )
        producer_workspace = first["workspace"]
        source = self.add_source("bound-engine", engine_scope=first["scope"])
        materialization = self.materialization
        capture_part = self.part

        def normalized_part(content: str) -> Any:
            return capture_part("Fictional capture: " + content).model_copy(
                update={
                    "derivation": "producer_normalized",
                    "content": content,
                    "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
                }
            )

        normalized_part.__module__ = capture_part.__module__

        def native_materialization(
            package: Any, *, event: LogicalEventDeclaration, **updates: Any
        ) -> Any:
            native = event.native.model_copy(update={"native_id": native_id})
            return materialization(
                package, event=event.model_copy(update={"native": native}), **updates
            )

        def prepare_claim(stage: str) -> Plan:
            return lambda extras, bead, _: self.add_claim(
                extras, bead, "fictional opportunity", "stage", stage
            )

        accepted = []
        for event, chunk, stage in (
            (first, first_chunk, "negotiation"),
            (second, second_chunk, "closed won"),
        ):
            native_id = f"core-memory.v2:{producer_workspace}:{event['event_id']}"
            with (
                patch.object(self, "materialization", native_materialization),
                patch.object(self, "part", normalized_part),
            ):
                bead = self.author(
                    chunk["text"],
                    stage.replace(" ", "-"),
                    source=source,
                    plan=prepare_claim(stage),
                )
            accepted.append((event, chunk, bead))

        migrate(self.db, expected_current_version=27, target_version=62)
        intake = PostgresEngineIntake(
            self.db, credential_sha256=self.secret_hash, workspace_id=self.workspace
        )
        endpoints = {}
        for event, chunk, bead in accepted:
            (claim,) = self.inspect(bead).claims
            (statement,) = claim.statements
            span = EngineChunkSpan(
                chunk_id=uuid.UUID(chunk["chunk_id"]),
                byte_start=chunk["byte_start"],
                byte_end=chunk["byte_end"],
                char_start=chunk["char_start"],
                char_end=chunk["char_end"],
                chunk_sha256=chunk["content_hash"],
            )
            binding = BindEngineEventEvidence(
                source_object_id=source,
                engine_workspace=producer_workspace,
                engine_event_id=uuid.UUID(event["event_id"]),
                event_sha256=event["content_hash"],
                source_unit_id=claim.source_time.source_unit_id,
                statement_id=statement.statement_id,
                chunks=(span,),
            )
            self.assertEqual(intake.bind_event_evidence(binding), "bound")
            with self.db.transaction():
                PostgresAuthorizationPort(self.db).begin_context(
                    credential_sha256=self.secret_hash,
                    requested_workspace_id=self.workspace,
                )
                (resolved,) = self.row(
                    "SELECT memoriesql.resolve_engine_chunk_evidence_v1(%s,%s,%s,%s)",
                    (
                        source,
                        producer_workspace,
                        binding.engine_event_id,
                        span.chunk_id,
                    ),
                )
            evidence = EngineChunkEvidence.model_validate(resolved)
            self.assertEqual(evidence.event_id, claim.source_time.event_id)
            endpoints[chunk["chunk_id"]] = _BoundClaimEndpoint(
                enrollment_id=source,
                producer_workspace=producer_workspace,
                chunk_id=evidence.chunk_id,
                claim=claim,
                evidence=LifecycleEvidence(
                    statement_id=statement.statement_id,
                    source_unit_id=evidence.source_unit_id,
                    content_hash=evidence.content_sha256,
                ),
            )

        request = _prepare_claim_event(
            record_key=edge["record_key"],
            arrow=edge["arrow"],
            origin=endpoints[edge["from_bead"]],
            target=endpoints[edge["to_bead"]],
            reviewed_reason="The fictional opportunity owner confirmed the new stage.",
            expected_last_event_id=None,
        )
        applied = self.lifecycle.record_claim_event(request)
        replay = self.lifecycle.record_claim_event(request)
        self.assertTrue(replay.replayed)
        self.assertEqual(applied.claim_event_id, replay.claim_event_id)
        target, origin = (self.inspect(row[2]).claims[0] for row in accepted)
        self.assertEqual((target.value, target.state), ("negotiation", "superseded"))
        self.assertEqual((origin.value, origin.state), ("closed won", "current"))
        self.assertEqual(target.superseded_by, (origin.claim_id,))

    def test_bound_engine_replacement_history_and_replay_use_current_authority(
        self,
    ) -> None:
        monday_source = self.add_source("engine-monday")
        friday_bead = self.author(
            "The fictional launch is Friday.",
            "engine-friday",
            plan=lambda extras, bead, _: self.add_claim(
                extras, bead, "fictional launch", "day", "Friday"
            ),
        )
        monday_bead = self.author(
            "The fictional launch moved to Monday.",
            "engine-monday",
            source=monday_source,
            plan=lambda extras, bead, _: self.add_claim(
                extras, bead, "fictional launch", "day", "Monday"
            ),
        )
        migrate(self.db, expected_current_version=27, target_version=62)
        enrollment = uuid.uuid4()
        friday = self.engine_claim_endpoint(friday_bead, enrollment)
        monday = self.engine_claim_endpoint(monday_bead, enrollment)
        before_replacement = self.inspect(friday_bead).known_at
        command = _prepare_claim_event(
            record_key=f"edge:repl:{monday.chunk_id}:{friday.chunk_id}",
            arrow="repl",
            origin=monday,
            target=friday,
            reviewed_reason="The launch owner confirmed Monday.",
            expected_last_event_id=None,
        )
        receipt = self.lifecycle.record_claim_event(command)
        replay = self.lifecycle.record_claim_event(command)
        self.assertEqual(
            (replay.claim_event_id, replay.replayed), (receipt.claim_event_id, True)
        )
        (current,) = self.inspect(monday_bead).claims
        self.assertEqual((current.value, current.state), ("Monday", "current"))
        (ended,) = self.inspect(friday_bead).claims
        self.assertEqual(
            (ended.state, ended.superseded_by), ("superseded", (current.claim_id,))
        )
        self.assertEqual(
            [(event.action, event.origin) for event in ended.events],
            [
                ("supersede", "governed"),
            ],
        )
        (historical,) = self.inspect(friday_bead, before_replacement).claims
        self.assertEqual(
            (historical.value, historical.state, historical.events),
            ("Friday", "current", ()),
        )

        # A revoked source exercises the authorization fence, not physical erasure.
        self.revoke_engine_fixture_source(monday_source)
        self.assertEqual(
            self.inspect(monday_bead, before_replacement).outcome, "unavailable"
        )
        self.assertEqual(self.inspect(friday_bead).outcome, "unavailable")
        self.assertEqual(
            self.inspect(friday_bead, before_replacement).claims, (historical,)
        )
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            self.lifecycle.record_claim_event(command)
        self.assertEqual(self.inspect(friday_bead).outcome, "unavailable")
        self.assertEqual(
            self.latest_claim_event(receipt.claim_id), receipt.claim_event_id
        )

    def test_bound_engine_dispute_requires_resolution_and_withdrawal_ends_claim(
        self,
    ) -> None:
        notice_source = self.add_source("engine-withdraw-notice")
        friday_bead = self.author(
            "The fictional launch is Friday.",
            "engine-dispute-friday",
            plan=lambda extras, bead, _: self.add_claim(
                extras, bead, "launch", "day", "Friday"
            ),
        )
        monday_bead = self.author(
            "The fictional launch is Monday.",
            "engine-dispute-monday",
            plan=lambda extras, bead, _: self.add_claim(
                extras, bead, "launch", "day", "Monday"
            ),
        )
        notice_bead = self.author(
            "The fictional team withdrew Friday.",
            "engine-withdraw-friday",
            source=notice_source,
        )
        migrate(self.db, expected_current_version=27, target_version=62)
        enrollment = uuid.uuid4()
        friday = self.engine_claim_endpoint(friday_bead, enrollment)
        monday = self.engine_claim_endpoint(monday_bead, enrollment)
        dispute = self.lifecycle.record_claim_event(
            _prepare_claim_event(
                record_key=f"edge:!contradicts:{monday.chunk_id}:{friday.chunk_id}",
                arrow="!contradicts",
                origin=monday,
                target=friday,
                reviewed_reason="Both dates have conflicting evidence.",
                expected_last_event_id=None,
            )
        )
        notice = self.engine_claim_endpoint(notice_bead, enrollment)
        self.assertIsNone(notice.claim)
        for bead in (friday_bead, monday_bead):
            (claim,) = self.inspect(bead).claims
            self.assertEqual(
                (claim.state, claim.events[0].event_id),
                ("disputed", dispute.claim_event_id),
            )

        before_withdrawal = self.inspect(friday_bead).known_at
        reason = "The launch owner withdrew Friday."
        withdrawal = self.lifecycle.record_claim_event(
            _prepare_claim_event(
                record_key=f"edge:withdraws:{notice.chunk_id}:{friday.chunk_id}",
                arrow="withdraws",
                origin=notice,
                target=friday,
                reviewed_reason=reason,
                expected_last_event_id=dispute.claim_event_id,
            )
        )
        (withdrawn,) = self.inspect(friday_bead).claims
        self.assertEqual(withdrawn.state, "retracted")
        event = withdrawn.events[-1]
        self.assertEqual(
            (event.event_id, event.reason), (withdrawal.claim_event_id, reason)
        )
        self.assertEqual(self.inspect(monday_bead).claims[0].state, "current")
        self.assertEqual(self.inspect(notice_bead).claims, ())

        self.revoke_engine_fixture_source(notice_source)
        missing = self.inspect(uuid.uuid4())
        self.assertEqual(missing.outcome, "unavailable")
        for bead in (friday_bead, monday_bead):
            with self.subTest(bead=bead):
                self.assertEqual(self.inspect(bead), missing)
                self.assertEqual(self.inspect(bead, event.recorded_at), missing)
                self.assertEqual(
                    self.inspect(bead, before_withdrawal).claims[0].state, "disputed"
                )

    def test_invalid_coverage_or_unpinned_candidate_refuses_the_whole_bundle(self) -> None:
        a = self.author("Fictional note A.", "a")
        b = self.author("Fictional note B.", "b")
        before = self.row(
            "SELECT (SELECT count(*) FROM memoriesql.bead_relations),(SELECT count(*) FROM memoriesql.bead_claims),(SELECT count(*) FROM memoriesql.relation_candidate_assessments),(SELECT count(*) FROM memoriesql.accepted_bead_semantics)"
        )

        def unpinned(extras: dict[str, Any], bead: dict[str, Any], candidates: list[Any]) -> None:
            self.add_claim(extras, bead, "orchard", "note", "C")
            extras["candidate_assessments"] = [
                {"candidate_bead_id": str(b), "assessment": "no_edge", "reason": None}
            ]

        self.author("Fictional note C.", "c", candidates=(a,), plan=unpinned, expect=None)

        def forged(extras: dict[str, Any], bead: dict[str, Any], candidates: list[Any]) -> None:
            relation = self.relate(extras, bead, candidates[0], "supports")
            extras["relations"][0]["candidate_statement_ids"] = [self.statement(bead)]
            self.assertTrue(relation)

        self.author("Fictional note D.", "d", candidates=(a,), plan=forged, expect=None)
        self.assertEqual(
            self.row(
                "SELECT (SELECT count(*) FROM memoriesql.bead_relations),(SELECT count(*) FROM memoriesql.bead_claims),(SELECT count(*) FROM memoriesql.relation_candidate_assessments),(SELECT count(*) FROM memoriesql.accepted_bead_semantics)"
            ),
            before,
        )
        self.assertEqual(
            self.row(
                "SELECT count(*) FROM memoriesql.semantic_tasks WHERE contract_revision=6 AND status='succeeded'"
            ),
            (2,),
        )

    def test_governed_relation_lifecycle_is_append_only_and_derived(self) -> None:
        a = self.author("The fictional pump failed on Monday.", "a")
        ids: dict[str, str] = {}
        b = self.author(
            "Three fictional trees wilted.",
            "b",
            candidates=(a,),
            plan=lambda extras, bead, c: ids.setdefault("first", self.relate(extras, bead, c[0], "caused_by", basis="agent_inferred", confidence=0.4)),
        )
        c = self.author(
            "The fictional pump failure led to wilting.",
            "c",
            candidates=(a,),
            plan=lambda extras, bead, cand: ids.setdefault("second", self.relate(extras, bead, cand[0], "led_to", direction="to_authored")),
        )
        first, second = uuid.UUID(ids["first"]), uuid.UUID(ids["second"])

        def act(key: str, relation: uuid.UUID, action: RelationAction, **extra: Any) -> Any:
            return self.lifecycle.record_relation_event(RecordRelationEvent(
                idempotency_key="orchard." + key,
                relation_id=relation,
                action=action,
                reason="Fictional owner review.",
                expected_last_event_id=self.latest_relation_event(relation),
                **extra,
            ))

        def state(bead: uuid.UUID, relation: uuid.UUID) -> tuple[str, tuple[uuid.UUID, ...]]:
            found = next(r for r in self.inspect(bead).relations if r.relation_id == relation)
            return found.state, found.superseded_by

        statement, unit, content = self.row(
            "SELECT e.statement_id,e.evidence_source_unit_id,e.evidence_content_hash FROM memoriesql.bead_semantic_statement_evidence e JOIN memoriesql.bead_semantic_statements s USING(tenant_id,statement_id) WHERE s.bead_id=%s",
            (c,),
        )
        act("dispute", first, "dispute", evidence=(
            LifecycleEvidence(statement_id=statement, source_unit_id=unit, content_hash=content),
        ))
        self.assertEqual(state(b, first), ("disputed", ()))
        self.assertEqual(
            self.row("SELECT owner_kind,statement_id FROM memoriesql.semantic_evidence_links WHERE owner_kind='relation_event'"),
            ("relation_event", statement),
        )
        act("confirm", first, "confirm")
        self.assertEqual(state(b, first), ("active", ()))
        act("supersede", first, "supersede", replacement_relation_id=second)
        self.assertEqual(state(b, first), ("superseded", (second,)))
        self.assertEqual(state(a, first), ("superseded", (second,)))
        act("retract.second", second, "retract")
        self.assertEqual(state(c, second), ("retracted", ()))
        # Supersession is final: retracting the replacement never reinstates it.
        self.assertEqual(state(b, first), ("superseded", (second,)))
        with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState):
            act("confirm.second", second, "confirm")
        (inverse,) = [r for r in self.inspect(a).relations if r.relation_id == second]
        self.assertEqual((inverse.source_bead_id, inverse.target_bead_id, inverse.direction), (a, c, "outgoing"))
        self.assertEqual(
            [e.action for e in next(r for r in self.inspect(b).relations if r.relation_id == first).events],
            ["dispute", "confirm", "supersede"],
        )
        with self.assertRaises(psycopg.Error), self.db.transaction():
            self.db.execute("DELETE FROM memoriesql.bead_relation_events")

    def test_forbidden_cycles_are_refused_and_permitted_keys_are_not(self) -> None:
        a = self.author("Fictional release notes, first draft.", "a")
        b = self.author(
            "Fictional release notes, restated.",
            "b",
            candidates=(a,),
            plan=lambda extras, bead, c: self.relate(extras, bead, c[0], "derived_from"),
        )

        def both_ways(key: str) -> Plan:
            def plan(extras: dict[str, Any], bead: dict[str, Any], candidates: list[Any]) -> None:
                self.relate(extras, bead, candidates[0], key)
                self.relate(extras, bead, candidates[0], key, direction="to_authored")
            return plan

        def through_existing(key: str) -> Plan:
            def plan(extras: dict[str, Any], bead: dict[str, Any], candidates: list[Any]) -> None:
                by_id = {c["bead_id"]: c for c in candidates}
                self.relate(extras, bead, by_id[str(b)], key)
                self.relate(extras, bead, by_id[str(a)], key, direction="to_authored")
            return plan

        relations = self.row("SELECT count(*) FROM memoriesql.bead_relations")
        # A reciprocal pair in one bundle, and a longer cycle through the existing
        # b -> a assertion, are refused whole for a cycle-forbidden key.
        reciprocal = self.author("Fictional notes, copied both ways.", "c", candidates=(a,),
                                 plan=both_ways("derived_from"), expect=None)
        longer = self.author("Fictional notes, a three-way copy.", "d", candidates=(a, b),
                             plan=through_existing("derived_from"), expect=None)
        self.assertEqual(self.row("SELECT count(*) FROM memoriesql.bead_relations"), relations)
        for refused in (reciprocal, longer):
            self.assertEqual(
                self.row("SELECT count(*) FROM memoriesql.accepted_bead_semantics WHERE bead_id=%s", (refused,)),
                (0,),
            )
        # The same shapes under a key whose cycles are permitted are accepted.
        self.author("Fictional notes, mutual influence.", "e", candidates=(a,), plan=both_ways("led_to"))
        self.author("Fictional notes, a three-way influence.", "f", candidates=(a, b),
                    plan=through_existing("led_to"))
        # An acyclic cycle-forbidden bundle is accepted.
        self.author(
            "Fictional notes, a second restatement.",
            "g",
            candidates=(a, b),
            plan=lambda extras, bead, c: [self.relate(extras, bead, item, "derived_from") for item in c],
        )

    def test_built_in_vocabulary_is_profile_revision_one(self) -> None:
        from memoriesql.application.relation_profile import RELATION_SEMANTIC_PROFILE

        rows = self.db.execute(
            "SELECT ty.type_key,r.revision,r.endpoint_rule,r.forward_reading,r.inverse_reading,r.is_symmetric,r.cycle_policy,r.status FROM memoriesql.relation_types ty JOIN memoriesql.relation_type_revisions r USING(relation_type_id) WHERE ty.namespace='memoriesql' ORDER BY ty.type_key"
        ).fetchall()
        self.assertEqual(
            rows,
            [
                ("associated_with", 1, "symmetric", "is associated with", "is associated with", True, "permitted", "active"),
                ("blocks", 1, "impediment → impeded", "blocks", "is blocked by", False, "permitted", "active"),
                ("caused_by", 1, "effect → cause", "is caused by", "causes", False, "permitted", "active"),
                ("contradicts", 1, "symmetric", "contradicts", "contradicts", True, "permitted", "active"),
                ("depends_on", 1, "dependent → prerequisite", "depends on", "is required by", False, "permitted", "active"),
                ("derived_from", 1, "derivative → source", "is derived from", "is the source of", False, "forbidden", "active"),
                ("enables", 1, "enabler → enabled", "enables", "is enabled by", False, "permitted", "active"),
                ("led_to", 1, "antecedent → result", "led to", "resulted from", False, "permitted", "active"),
                ("part_of", 1, "part → whole", "is part of", "has part", False, "forbidden", "active"),
                ("supersedes", 1, "replacement → replaced", "supersedes", "is superseded by", False, "forbidden", "active"),
                ("supports", 1, "evidence → proposition", "supports", "is supported by", False, "permitted", "active"),
            ],
        )
        self.assertEqual(
            sorted(m.key for m in RELATION_SEMANTIC_PROFILE.mappings), [row[0] for row in rows]
        )
        activation = self.activate(self.unit("Fictional note for the pinned vocabulary.", "v"), "v")
        pinned = self.row(
            "SELECT input_payload->'payload'->'relation_vocabulary' FROM memoriesql.semantic_tasks WHERE task_id=%s",
            (activation.execution_task_id,),
        )[0]
        caused = next(item for item in pinned if item["key"] == "caused_by")
        self.assertEqual(caused["cycle_policy"], "permitted")
        self.assertIn("Temporal order alone never suffices", caused["evidence_expectation"])
        self.assertIsNotNone(caused["example"])

    def test_named_replacement_relations_must_stay_readable(self) -> None:
        a = self.author("The fictional pump failed on Monday.", "a")
        ids: dict[str, str] = {}
        b = self.author(
            "Three fictional trees wilted.",
            "b",
            candidates=(a,),
            plan=lambda extras, bead, c: ids.setdefault("first", self.relate(extras, bead, c[0], "caused_by")),
        )
        other = self.add_source("replacement")
        self.author(
            "A fictional report says the pump failure wilted the trees.",
            "c",
            candidates=(a,),
            source=other,
            plan=lambda extras, bead, c: ids.setdefault("second", self.relate(extras, bead, c[0], "caused_by")),
        )
        first = uuid.UUID(ids["first"])
        self.lifecycle.record_relation_event(RecordRelationEvent(
            idempotency_key="orchard.replace",
            relation_id=first,
            action="supersede",
            replacement_relation_id=uuid.UUID(ids["second"]),
            reason="The fictional report restates the relation with better evidence.",
        ))
        self.assertEqual(self.inspect(b).relations[0].superseded_by, (uuid.UUID(ids["second"]),))
        self.db.execute(
            "UPDATE memoriesql.protected_resources SET status='revoked',revoked_at=clock_timestamp() WHERE resource_id=%s",
            (other,),
        )
        # The replacement's identifier is never disclosed once it is unreadable.
        self.assertEqual(self.inspect(b).outcome, "unavailable")

    def test_workspace_vocabulary_enters_only_by_decision_and_pins_exact_revisions(self) -> None:
        proposal = ProposeRelationType(
            idempotency_key="orchard.mitigates.1",
            access_scope_id=self.scope,
            key="mitigates",
            label="Mitigates",
            definition="The source's cited action reduces the target's cited fictional harm.",
            endpoint_rule="action → harm",
            forward_reading="mitigates",
            inverse_reading="is mitigated by",
            symmetric=False,
            cycle_policy="permitted",
            reason="Fictional orchard maintenance vocabulary.",
        )
        proposed = self.lifecycle.propose_relation_type(proposal)
        self.assertEqual((proposed.key, proposed.proposed_revision), ("mitigates", 1))
        mitigates = RelationTypePin(key="mitigates", revision=1)
        early = self.unit("Fictional shade cloth was installed.", "early")
        with self.assertRaises(psycopg.errors.InvalidParameterValue):
            self.activate(early, "early", vocabulary=VOCABULARY + (mitigates,))
        decision = self.lifecycle.decide_relation_type(DecideRelationType(
            idempotency_key="orchard.mitigates.1.decide",
            access_scope_id=self.scope,
            candidate_id=proposed.candidate_id,
            decision="accepted",
            reason="The owner accepts the fictional type.",
        ))
        self.assertEqual(decision.relation_type, mitigates)
        with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState):
            self.lifecycle.decide_relation_type(DecideRelationType(
                idempotency_key="orchard.mitigates.1.again",
                access_scope_id=self.scope,
                candidate_id=proposed.candidate_id,
                decision="rejected",
                reason="Already decided.",
            ))
        with self.assertRaises(psycopg.errors.InvalidParameterValue):
            self.lifecycle.propose_relation_type(
                proposal.model_copy(update={"idempotency_key": "orchard.supports", "key": "supports"})
            )
        harm = self.author("Fictional heat stressed the trees.", "harm")
        bound = self.unit("Fictional shade cloth now shields the trees.", "shade")
        self.activate(bound, "shade", (harm,), VOCABULARY + (mitigates,))
        self.statement_text = "Fictional shade cloth now shields the trees."
        self.plan = lambda extras, bead, c: self.relate(extras, bead, c[0], "mitigates")
        self.steps = []
        self.mentions = []
        result = asyncio.run(self.worker().run_once())
        self.assertEqual(result.task_status, "succeeded", result)
        (relation,) = self.inspect(bound.bead_id).relations
        self.assertEqual(
            (relation.relation_type.key, relation.relation_type.namespace, relation.relation_type.forward_reading),
            ("mitigates", "workspace", "mitigates"),
        )
        revision = self.lifecycle.propose_relation_type(proposal.model_copy(update={
            "idempotency_key": "orchard.mitigates.2", "status": "inactive",
            "reason": "Retire the fictional type.",
        }))
        self.assertEqual(revision.proposed_revision, 2)
        self.lifecycle.decide_relation_type(DecideRelationType(
            idempotency_key="orchard.mitigates.2.decide",
            access_scope_id=self.scope,
            candidate_id=revision.candidate_id,
            decision="accepted",
            reason="Retired.",
        ))
        late = self.unit("Fictional mulch was added.", "late")
        for pin in (mitigates, RelationTypePin(key="mitigates", revision=2)):
            with self.assertRaises(psycopg.errors.InvalidParameterValue):
                self.activate(late, "late." + str(pin.revision), vocabulary=VOCABULARY + (pin,))
        # The accepted relation keeps its exact historical revision.
        self.assertEqual(self.inspect(bound.bead_id).relations[0].relation_type.revision, 1)

    def test_shared_roots_count_once_and_derivatives_add_no_root(self) -> None:
        log = self.author("Fictional pump log: pressure low.", "log")
        copy_id = self.author(
            "Fictional pump log copy: pressure low.",
            "copy",
            candidates=(log,),
            plan=lambda extras, bead, c: self.relate(extras, bead, c[0], "supports"),
        )
        (duplicate,) = self.inspect(copy_id).relations
        self.assertEqual((len(duplicate.evidence), duplicate.independent_root_count), (2, 1))
        north, south = self.add_source("north"), self.add_source("south")
        a = self.author("Fictional north sensor: dry soil.", "north", source=north)
        b = self.author("Fictional south sensor: dry soil.", "south", source=south)

        def derive(extras: dict[str, Any], bead: dict[str, Any], candidates: list[Any]) -> None:
            for candidate in candidates:
                self.relate(extras, bead, candidate, "derived_from")

        summary = self.author(
            "Fictional summary: both sensors report dry soil.",
            "summary",
            candidates=(a, b),
            plan=derive,
        )
        relations = self.inspect(summary).relations
        self.assertEqual(len(relations), 2)
        own_unit = self.row("SELECT source_unit_id FROM memoriesql.beads WHERE bead_id=%s", (summary,))[0]
        for relation in relations:
            own = next(e for e in relation.evidence if e.source_unit_id == own_unit)
            # The derivative's own evidence is rooted in its inputs, never a new root.
            self.assertEqual(set(own.derivation_root_ids), {north, south})
            self.assertEqual(relation.independent_root_count, 2)

        def cite(extras: dict[str, Any], bead: dict[str, Any], candidates: list[Any]) -> None:
            by_id = {c["bead_id"]: c for c in candidates}
            self.relate(extras, bead, by_id[str(summary)], "supports")
            self.relate(extras, bead, by_id[str(a)], "supports")

        check = self.author(
            "Fictional check: the soil is dry.", "check", candidates=(summary, a), plan=cite
        )
        counts = {
            r.target_bead_id: r.independent_root_count for r in self.inspect(check).relations
        }
        # Own root plus north and south: the summary adds nothing of its own.
        self.assertEqual(counts, {summary: 3, a: 2})

    def test_roots_follow_the_whole_lineage_and_are_never_truncated(self) -> None:
        quarry, mill, depot = (self.add_source(name) for name in ("quarry", "mill", "depot"))
        ledger = self.author("Fictional ledger: four crates left the orchard.", "ledger")

        def cycle(extras: dict[str, Any], bead: dict[str, Any], candidates: list[Any]) -> None:
            # Through different statements, each bead derives from the other.
            restated = dict(
                bead["statements"][0],
                statement_id=str(uuid.uuid4()),
                statement_text="Fictional quarry note: the ledger counted the crates.",
            )
            bead["statements"].append(restated)
            bead["render"]["summary"].append(
                {"text": restated["statement_text"], "statement_ids": [restated["statement_id"]]}
            )
            self.relate(extras, bead, candidates[0], "derived_from")
            self.relate(extras, bead, candidates[0], "derived_from", direction="to_authored")
            extras["relations"][1]["authored_statement_ids"] = [restated["statement_id"]]

        note = self.author(
            "Fictional quarry note: four crates left the orchard.",
            "quarry",
            source=quarry,
            candidates=(ledger,),
            plan=cycle,
        )
        summary = self.author(
            "Fictional mill summary: four crates.",
            "mill",
            source=mill,
            candidates=(note,),
            plan=lambda extras, bead, c: self.relate(extras, bead, c[0], "derived_from"),
        )

        def roots(bead: uuid.UUID, known_at: datetime | None = None) -> set[tuple[uuid.UUID, ...]]:
            inspection = self.inspect(bead, known_at)
            self.assertEqual(inspection.outcome, "available")
            for relation in inspection.relations:
                self.assertEqual(relation.independent_root_count, 1)
            return {tuple(e.derivation_root_ids) for r in inspection.relations for e in r.evidence}

        # Beads that derive only from one another are one lineage with one root, the
        # source of its earliest bead; nothing derived from it adds a root of its own.
        for bead in (ledger, note, summary):
            self.assertEqual(roots(bead), {(self.source,)})
        before = datetime.now(UTC)
        manifest = self.author(
            "Fictional depot manifest: four crates.",
            "depot",
            source=depot,
            candidates=(note,),
            plan=lambda extras, bead, c: self.relate(
                extras, bead, c[0], "derived_from", direction="to_authored"
            ),
        )
        # A late original ends the lineage: every derivative now roots there alone, at
        # any depth, while reads as of an earlier time keep the earlier root.
        for bead in (ledger, note, summary, manifest):
            self.assertEqual(roots(bead), {(depot,)})
        for bead in (ledger, note, summary):
            self.assertEqual(roots(bead, before), {(self.source,)})

        # A lineage past its limit is refused visibly, never truncated. Lowering the
        # limit in this disposable database stands in for a 129-bead lineage.
        self.db.execute(
            "CREATE OR REPLACE FUNCTION memoriesql.derivation_lineage_limit() RETURNS integer "
            "LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$ SELECT 3 $$"
        )
        # The summary's lineage is summary, note, ledger and manifest; the note's
        # inspection cites the summary's evidence through its incoming relation.
        for bead in (summary, note):
            self.assertEqual(self.inspect(bead).outcome, "budget_exhausted")
        self.assertEqual(roots(ledger), {(depot,)})
        self.assertEqual(roots(summary, before), {(self.source,)})

    def test_known_time_and_late_arrival_inherit_source_clocks(self) -> None:
        self.assert_known_time_and_late_arrival()

    def test_known_time_cutoff_does_not_use_a_future_client_clock(self) -> None:
        # A host-clock cutoff after both operations would admit the late claim.
        # Only this fixture's client clock changes; database/runtime clocks do not.
        with patch(f"{__name__}.datetime", wraps=datetime) as client_clock:
            client_clock.now.return_value = self.row("SELECT clock_timestamp()")[0] + timedelta(days=1)
            self.assert_known_time_and_late_arrival()

    def assert_known_time_and_late_arrival(self) -> None:
        monday = datetime(2026, 9, 21, 9, 0, tzinfo=UTC)
        sunday = monday - timedelta(days=1)
        ids: dict[str, str] = {}
        gate = self.author(
            "The fictional gate was open on Monday.",
            "gate",
            occurred_at=monday,
            plan=lambda extras, bead, _: ids.setdefault("open", self.add_claim(extras, bead, "orchard gate", "position", "open")),
        )
        # author() has committed. Knowledge time and this cutoff share the DB
        # clock domain; there is no required ordering with the client's clock.
        before = self.row("SELECT clock_timestamp()")[0]

        def late_plan(extras: dict[str, Any], bead: dict[str, Any], candidates: list[Any]) -> None:
            ids["closed"] = self.add_claim(extras, bead, "orchard gate", "position", "closed")
            self.relate(extras, bead, candidates[0], "contradicts", basis="agent_inferred", confidence=0.55)
            (candidate,) = candidates
            self.assertEqual(candidate["source"]["time_basis"], "unit_source_time")
            self.assertEqual(candidate["source"]["occurred_at"], "2026-09-21T09:00:00Z")

        late = self.author(
            "The fictional gate was closed on Sunday.",
            "late",
            candidates=(gate,),
            occurred_at=sunday,
            plan=late_plan,
        )
        # Incompatible claims both stay current: neither recency nor occurrence decides.
        self.assertEqual(self.claim_state(gate), [("open", "current", set(), set())])
        self.assertEqual(self.claim_state(late), [("closed", "current", set(), set())])
        (open_claim,) = self.inspect(gate).claims
        (closed_claim,) = self.inspect(late).claims
        self.assertEqual(open_claim.source_time.occurred_at, monday)
        self.assertEqual(closed_claim.source_time.occurred_at, sunday)
        self.assertEqual(
            (closed_claim.source_time.time_basis, closed_claim.source_time.time_precision),
            ("unit_source_time", "second"),
        )
        earlier = self.inspect(gate, known_at=before)
        not_yet = self.inspect(late, known_at=before)
        trace = {
            "cutoff": before.isoformat(),
            "first_recorded_at": open_claim.recorded_at.isoformat(),
            "late_recorded_at": closed_claim.recorded_at.isoformat(),
            "earlier_outcome": earlier.outcome,
            "earlier_known_at": str(earlier.known_at),
            "late_outcome": not_yet.outcome,
        }
        print("fictional knowledge-clock trace " + json.dumps(trace, sort_keys=True))
        self.assertGreater(closed_claim.recorded_at, open_claim.recorded_at, trace)
        self.assertGreaterEqual(before, open_claim.recorded_at, trace)
        self.assertLess(before, closed_claim.recorded_at, trace)
        self.assertEqual(earlier.outcome, "available", trace)
        self.assertEqual((earlier.relations, len(earlier.claims)), ((), 1), trace)
        self.assertEqual(
            (not_yet.outcome, not_yet.relations_capable, not_yet.claims), ("available", False, ()), trace
        )
        first = self.inspect(late)
        again = self.inspect(late, known_at=first.known_at)
        digest = [
            hashlib.sha256(json.dumps(x.model_dump(mode="json"), sort_keys=True).encode()).hexdigest()
            for x in (first, again)
        ]
        self.assertEqual(digest[0], digest[1])

    def test_activation_pins_candidates_and_replays_exactly(self) -> None:
        a = self.author("Fictional note A.", "a")
        b = self.author("Fictional note B.", "b")
        bound = self.unit("Fictional note C.", "c")
        first = self.activate(bound, "c", (a,))
        self.assertEqual(self.activate(bound, "c", (a,)).execution_task_id, first.execution_task_id)
        with self.assertRaises(psycopg.errors.UniqueViolation):
            self.activate(bound, "c", (a, b))
        with self.assertRaises(psycopg.errors.UniqueViolation):
            self.activate(bound, "c.natural", (b,))
        natural = self.activate(bound, "c.natural", (a,))
        self.assertEqual((natural.execution_task_id, natural.replayed), (first.execution_task_id, True))
        pinned = self.row(
            "SELECT relation_candidates,relation_vocabulary FROM memoriesql.complete_input_executions WHERE execution_task_id=%s",
            (first.execution_task_id,),
        )
        self.assertEqual([c["bead_id"] for c in pinned[0]], [str(a)])
        self.assertEqual(len(pinned[1]), len(BUILT_INS))
        payload = self.row(
            "SELECT input_payload FROM memoriesql.semantic_tasks WHERE task_id=%s",
            (first.execution_task_id,),
        )[0]
        self.assertEqual(payload["payload"]["relation_candidates"], pinned[0])
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            self.activate(self.unit("Fictional note D.", "d"), "d", (uuid.uuid4(),))
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            self.activate(self.unit("Fictional note E.", "e"), "e", (bound.bead_id,))

    def test_worker_refuses_candidates_whose_authority_lapses(self) -> None:
        other = self.add_source("lapsing")
        a = self.author("Fictional note A.", "a", source=other)
        bound = self.unit("Fictional note B.", "b")
        self.activate(bound, "b", (a,))
        intents = self.row("SELECT count(*) FROM memoriesql.model_provider_request_intents")
        self.db.execute(
            "UPDATE memoriesql.protected_resources SET status='revoked',revoked_at=clock_timestamp() WHERE resource_id=%s",
            (other,),
        )
        self.statement_text = "Fictional note B."
        self.plan = None
        self.steps = []
        self.mentions = []
        result = asyncio.run(self.worker().run_once())
        self.assertNotEqual(result.task_status, "succeeded", result)
        self.assertEqual(
            self.row("SELECT count(*) FROM memoriesql.accepted_bead_semantics"), (1,)
        )
        # The pinned candidate is re-authorized before any provider dispatch.
        self.assertEqual(
            self.row("SELECT count(*) FROM memoriesql.model_provider_request_intents"), intents
        )

    def test_worker_withholds_cached_claim_state_after_withdrawal_evidence_revocation(
        self,
    ) -> None:
        notice_source = self.add_source("candidate-withdrawal-notice")
        friday = self.author(
            "The fictional launch is Friday.",
            "candidate-withdrawal-friday",
            plan=lambda extras, bead, _: self.add_claim(
                extras, bead, "launch", "day", "Friday"
            ),
        )
        notice = self.author(
            "The fictional team withdrew Friday.",
            "candidate-withdrawal-notice",
            source=notice_source,
        )
        migrate(self.db, expected_current_version=27, target_version=62)
        text = "A fictional check refers to the launch."
        before = self.activate(
            self.unit(text, "before-withdrawal"), "before-withdrawal", (friday,)
        )
        enrollment = uuid.uuid4()
        earlier = self.engine_claim_endpoint(friday, enrollment)
        withdrawal = self.engine_claim_endpoint(notice, enrollment)
        self.lifecycle.record_claim_event(
            _prepare_claim_event(
                record_key=f"edge:withdraws:{withdrawal.chunk_id}:{earlier.chunk_id}",
                arrow="withdraws",
                origin=withdrawal,
                target=earlier,
                reviewed_reason="The fictional launch owner withdrew Friday.",
                expected_last_event_id=None,
            )
        )
        after = self.activate(
            self.unit(text, "after-withdrawal"), "after-withdrawal", (friday,)
        )
        for receipt, state in ((before, "current"), (after, "retracted")):
            self.assertEqual(
                self.row(
                    "SELECT relation_candidates #>> '{0,claims,0,state_at_activation}' "
                    "FROM memoriesql.complete_input_executions WHERE execution_task_id=%s",
                    (receipt.execution_task_id,),
                ),
                (state,),
            )

        migrate(self.db, expected_current_version=62, target_version=63)
        self.revoke_engine_fixture_source(notice_source)
        self.statement_text = text
        self.plan = None
        self.steps = []
        self.mentions = []
        results = {}
        for _ in (before, after):
            result = asyncio.run(self.worker().run_once())
            results[result.task_id] = result.task_status
        self.assertEqual(
            results,
            {
                before.execution_task_id: "succeeded",
                after.execution_task_id: "policy_paused",
            },
        )
        self.assertEqual(
            self.row(
                "SELECT count(*) FROM memoriesql.model_provider_request_intents WHERE task_id=%s",
                (after.execution_task_id,),
            ),
            (0,),
        )

    def test_candidate_origin_access_is_rechecked_at_delivery(self) -> None:
        candidate_scope, candidate_source = relation_agents.explicit_scope(
            self.db, self
        )
        with patch.object(self, "scope", candidate_scope):
            candidate = self.author(
                "The fictional launch is Friday.",
                "origin-candidate",
                source=candidate_source,
                plan=lambda extras, bead, _: self.add_claim(
                    extras, bead, "launch", "day", "Friday"
                ),
            )
        (claim,) = self.inspect(candidate).claims
        (origin_grant,) = self.row(
            "SELECT grant_id FROM memoriesql.access_grants "
            "WHERE access_scope_id=%s AND target_principal_id=%s",
            (candidate_scope, self.principal),
        )
        migrate(self.db, expected_current_version=27, target_version=63)

        text = "A fictional note refers to the launch."
        self.statement_text, self.plan, self.steps, self.mentions = text, None, [], []
        control = self.activate(
            self.unit(text, "origin-control"), "origin-control", (candidate,)
        )
        calls: list[bool] = []

        def transport(messages: Any, info: Any) -> Any:
            calls.append(True)
            return self.response(messages, info)

        result = asyncio.run(self.worker(transport).run_once())
        self.assertEqual(
            (result.task_id, result.task_status),
            (control.execution_task_id, "succeeded"),
        )
        self.assertTrue(calls)
        calls.clear()

        affected = self.activate(
            self.unit(text, "origin-denial"), "origin-denial", (candidate,)
        )
        worker = self.worker(transport)
        authorize_delivery = SourceRevisitingEvidenceAccess.authorize_delivery
        refused: list[uuid.UUID] = []

        async def revoke_at_delivery(
            access: SourceRevisitingEvidenceAccess, delivery: SourceDelivery
        ) -> None:
            self.assertEqual(delivery.task_id, affected.execution_task_id)
            with self.db.transaction():
                self.begin()
                self.db.execute(
                    "SELECT memoriesql.revise_access_grant(%s,1,%s,'revoked',%s,%s,%s)",
                    (
                        origin_grant,
                        ["read", "write"],
                        self.now,
                        self.now + timedelta(hours=1),
                        self.now,
                    ),
                )
            self.assertEqual(self.inspect(candidate).outcome, "unavailable")
            with self.db.transaction():
                PostgresAuthorizationPort(self.db).begin_context(
                    credential_sha256=self.worker_secret,
                    requested_workspace_id=self.workspace,
                )
                self.assertEqual(
                    self.row(
                        "SELECT memoriesql.current_context_accepted_bead_maintain_authorized(%s,%s,%s,%s)",
                        (
                            self.tenant,
                            self.workspace,
                            candidate_scope,
                            claim.bead_version_id,
                        ),
                    ),
                    (True,),
                )
            try:
                await authorize_delivery(access, delivery)
            except psycopg.errors.InsufficientPrivilege:
                refused.append(delivery.task_id)
                raise

        with patch.object(SourceRevisitingEvidenceAccess, "authorize_delivery", revoke_at_delivery):
            result = asyncio.run(worker.run_once())
        self.assertEqual(
            (result.task_id, result.task_status),
            (affected.execution_task_id, "policy_paused"),
        )
        self.assertEqual(refused, [affected.execution_task_id])
        self.assertEqual(calls, [])

    def test_reads_and_governed_judgments_require_their_authority(self) -> None:
        ids: dict[str, str] = {}
        a = self.author(
            "Fictional note A.",
            "a",
            plan=lambda extras, bead, _: ids.setdefault("claim", self.add_claim(extras, bead, "orchard", "note", "A")),
        )
        worker = PostgresRelationLifecycle(
            self.db, credential_sha256=self.worker_secret, workspace_id=self.workspace
        )
        self.assertEqual(
            worker.inspect_relations(InspectBeadRelations(bead_id=a)).outcome, "unavailable"
        )
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            worker.record_claim_event(RecordClaimEvent(
                idempotency_key="orchard.worker.retract",
                claim_id=uuid.UUID(ids["claim"]),
                action="retract",
                reason="A service never governs claims.",
            ))
        self.assertEqual(self.inspect(uuid.uuid4()).outcome, "unavailable")
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            self.lifecycle.record_claim_event(RecordClaimEvent(
                idempotency_key="orchard.unknown",
                claim_id=uuid.uuid4(),
                action="retract",
                reason="No such claim.",
            ))
        self.assertEqual(self.claim_state(a), [("A", "current", set(), set())])

    def test_an_authored_relation_needs_every_candidate_its_author_was_supplied(
        self,
    ) -> None:
        # Owner decision 7, extended by the owner on 2026-10-07 to revision-6
        # authored relations. The author is shown every candidate supplied to
        # it, so its relation's rationale may quote any of them. Disclosure needs
        # current authorization to every one; otherwise the whole relation is
        # withheld, and the read is exactly a read of missing data. Inspecting
        # the relation's target used to show the text once a candidate other
        # than its endpoints became unreadable. The withholding reason goes only
        # to the server log, for operators: never to the caller's connection.
        hidden_text = "Fictional hidden candidate: the west orchard flooded on day nine."
        hidden_source = self.add_source("hidden-candidate")
        target = self.author("Fictional target: the orchard audit passed.", "target")
        hidden = self.author(hidden_text, "hidden", source=hidden_source)

        def plan(extras: dict[str, Any], bead: dict[str, Any], candidates: list[Any]) -> None:
            (pinned,) = [c for c in candidates if c["bead_id"] == str(target)]
            self.relate(extras, bead, pinned, "caused_by")
            extras["relations"][0]["rationale"] = "Unless " + hidden_text

        authoring = self.author(
            "Fictional source: the orchard ledger balanced.",
            "authoring",
            candidates=(target, hidden),
            plan=plan,
        )
        migrate(self.db, expected_current_version=27, target_version=43)
        reader = PostgresRelationAssessments(
            self.db, credential_sha256=self.secret_hash, workspace_id=self.workspace
        )

        def both(bead: uuid.UUID) -> tuple[Any, Any]:
            return (
                reader.inspect_relations(InspectBeadRelationsV2(bead_id=bead)),
                self.inspect(bead),
            )

        # Fully authorized, both endpoints show the relation with its text.
        for bead in (authoring, target):
            current, legacy = both(bead)
            self.assertEqual((current.outcome, legacy.outcome), ("available", "available"))
            self.assertIn(hidden_text, current.model_dump_json())
        # A candidate that is neither endpoint loses its source's authorization.
        self.db.execute(
            "UPDATE memoriesql.protected_resources SET status='revoked',"
            "revoked_at=clock_timestamp() WHERE resource_id=%s",
            (hidden_source,),
        )
        # A caller's connection that asks for server log messages hears nothing.
        notices: list[str] = []

        def heard(notice: psycopg.errors.Diagnostic) -> None:
            notices.append(str(notice.message_primary))

        self.db.add_notice_handler(heard)
        self.db.execute("SET client_min_messages=log")
        audit = "SELECT count(*) FROM memoriesql.authorization_audit_events"
        try:
            before = self.row(audit)[0]
            missing = both(uuid.uuid4())
            missing_rows = self.row(audit)[0] - before
            for bead in (authoring, target):
                before = self.row(audit)[0]
                self.assertEqual(both(bead), missing)
                self.assertEqual(self.row(audit)[0] - before, missing_rows)
        finally:
            self.db.execute("RESET client_min_messages")
            self.db.remove_notice_handler(heard)
        # Nothing the caller heard names the withholding, the relation or the
        # unreadable candidate.
        relation = self.row(
            "SELECT relation_id FROM memoriesql.bead_relations WHERE authoring_bead_id=%s",
            (authoring,),
        )[0]
        named = ("memoriesql relation inspection", "withheld", str(relation), str(hidden))
        self.assertFalse([n for n in notices if any(i in n for i in named)], notices)

    # -- tenant evidence (ADR-0011 RAA-02) ------------------------------

    def second_tenant(self) -> tuple[uuid.UUID, uuid.UUID, str, PostgresRelationLifecycle]:
        """A complete second fictional tenant with its own owner, scope and session.

        Personal-local bootstrap admits one active workspace per database, so the
        fixture writes the rows that bootstrap writes, as the test superuser."""
        tenant, user, identity, principal, workspace, scope, policy, credential, session = (
            uuid.uuid4() for _ in range(9)
        )
        secret = hashlib.sha256(b"fictional second orchard session").hexdigest()
        now = datetime.now(UTC)
        rows: tuple[tuple[str, tuple[Any, ...]], ...] = (
            ("users", (tenant, user, "active", "Fictional second orchard", now, None)),
            ("auth_identities", (
                tenant, identity, user, "memoriesql.local", str(identity),
                "local_interactive", "active", now, None,
            )),
            ("principals", (tenant, principal, "human", user, user, "active", now, None)),
            ("workspaces", (tenant, workspace, "personal_local", user, "active", now)),
            ("workspace_memberships", (
                tenant, workspace, principal, "personal_owner", "active", 1, now, now, None,
            )),
            ("access_scopes", (tenant, workspace, scope, user, "owner_private", policy, "active", now)),
            ("access_policy_revisions", (
                tenant, workspace, scope, policy, 1, "owner_private", user,
                "personal-local owner-private default", principal, now,
            )),
            ("authentication_credentials", (
                tenant, credential, principal, "local_session", secret, "active",
                now, now + timedelta(hours=1), None,
            )),
            ("local_auth_sessions", (tenant, session, credential, identity, now)),
        )
        with self.db.transaction():
            for table, values in rows:
                self.db.execute(
                    sql.SQL("INSERT INTO memoriesql.{} VALUES ({})").format(
                        sql.Identifier(table), sql.SQL(",").join([sql.Placeholder()] * len(values))
                    ),
                    values,
                )
        lifecycle = PostgresRelationLifecycle(
            self.db, credential_sha256=secret, workspace_id=workspace
        )
        return workspace, scope, secret, lifecycle

    def related_pair(self, source: uuid.UUID | None = None) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
        """A claim on one fictional bead and a caused_by relation to it from a second."""
        ids: dict[str, str] = {}
        cause = self.author(
            "Fictional gate note: the north gate was locked.",
            "gate",
            source=source,
            plan=lambda extras, bead, _: ids.setdefault(
                "claim", self.add_claim(extras, bead, "north gate", "state", "locked")
            ),
        )
        effect = self.author(
            "Fictional crew note: the crew waited because the north gate was locked.",
            "crew",
            candidates=(cause,),
            plan=lambda extras, bead, c: ids.setdefault(
                "relation", self.relate(extras, bead, c[0], "caused_by")
            ),
        )
        return cause, effect, uuid.UUID(ids["claim"]), uuid.UUID(ids["relation"])

    def test_relation_tables_force_row_security_and_grant_no_direct_access(self) -> None:
        for table in RELATION_TABLES:
            name = "memoriesql." + table
            with self.subTest(table=table):
                self.assertEqual(
                    self.row(
                        "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE oid=%s::regclass",
                        (name,),
                    ),
                    (True, True),
                )
                for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                    self.assertEqual(
                        self.row(
                            "SELECT has_table_privilege('memoriesql_application', %s, %s)",
                            (name, privilege),
                        ),
                        (False,),
                        privilege,
                    )

    def test_another_tenant_cannot_read_or_probe_claims_and_relations(self) -> None:
        cause, effect, _, relation = self.related_pair()
        self.assertEqual([r.relation_id for r in self.inspect(effect).relations], [relation])
        _, _, _, other = self.second_tenant()
        unknown = other.inspect_relations(InspectBeadRelations(bead_id=uuid.uuid4()))
        self.assertEqual(unknown.outcome, "unavailable")
        for bead in (cause, effect):
            with self.subTest(bead=bead):
                seen = other.inspect_relations(InspectBeadRelations(bead_id=bead))
                # An existing bead of another tenant reads exactly like an unknown one.
                self.assertEqual(
                    seen.model_dump(exclude={"bead_id"}), unknown.model_dump(exclude={"bead_id"})
                )

    def test_another_tenant_cannot_govern_claims_relations_or_vocabulary(self) -> None:
        _, _, claim, relation = self.related_pair()
        proposed = self.lifecycle.propose_relation_type(ProposeRelationType(
            idempotency_key="orchard.shelters.1",
            access_scope_id=self.scope,
            key="shelters",
            label="Shelters",
            definition="The source's cited structure shields the target's cited fictional crop.",
            endpoint_rule="structure → crop",
            forward_reading="shelters",
            inverse_reading="is sheltered by",
            symmetric=False,
            cycle_policy="permitted",
            reason="Fictional orchard vocabulary.",
        ))
        counts = (
            "SELECT (SELECT count(*) FROM memoriesql.bead_relation_events),"
            " (SELECT count(*) FROM memoriesql.bead_claim_events),"
            " (SELECT count(*) FROM memoriesql.relation_type_decisions)"
        )
        before = self.row(counts)
        workspace, scope, secret, other = self.second_tenant()
        refused: tuple[tuple[str, Any], ...] = (
            ("relation dispute", lambda: other.record_relation_event(RecordRelationEvent(
                idempotency_key="orchard.cross.dispute",
                relation_id=relation,
                action="dispute",
                reason="Another tenant's review.",
            ))),
            ("claim retraction", lambda: other.record_claim_event(RecordClaimEvent(
                idempotency_key="orchard.cross.retract",
                claim_id=claim,
                action="retract",
                reason="Another tenant's review.",
            ))),
            ("decision in this scope", lambda: other.decide_relation_type(DecideRelationType(
                idempotency_key="orchard.cross.decide",
                access_scope_id=self.scope,
                candidate_id=proposed.candidate_id,
                decision="accepted",
                reason="Another tenant's decision.",
            ))),
            ("decision in its own scope", lambda: other.decide_relation_type(DecideRelationType(
                idempotency_key="orchard.cross.decide.own",
                access_scope_id=scope,
                candidate_id=proposed.candidate_id,
                decision="accepted",
                reason="Another tenant's decision.",
            ))),
        )
        for label, attempt in refused:
            with self.subTest(label), self.assertRaises(psycopg.errors.InsufficientPrivilege):
                attempt()
        # Its session cannot even open an authorization context in this workspace.
        intruder = PostgresRelationLifecycle(
            self.db, credential_sha256=secret, workspace_id=self.workspace
        )
        self.assertEqual(
            intruder.inspect_relations(InspectBeadRelations(bead_id=uuid.uuid4())).outcome,
            "unavailable",
        )
        with self.assertRaises(psycopg.errors.InvalidAuthorizationSpecification):
            intruder.record_relation_event(RecordRelationEvent(
                idempotency_key="orchard.cross.bound",
                relation_id=relation,
                action="confirm",
                reason="Another tenant bound to this workspace.",
            ))
        self.assertEqual(self.row(counts), before)
        self.assertNotEqual(workspace, self.workspace)

    def test_replay_never_crosses_a_tenant(self) -> None:
        _, effect, claim, relation = self.related_pair()
        confirm = RecordRelationEvent(
            idempotency_key="orchard.replay.confirm",
            relation_id=relation,
            action="confirm",
            reason="Fictional owner confirmation.",
            expected_last_event_id=self.latest_relation_event(relation),
        )
        reaffirm = RecordClaimEvent(
            idempotency_key="orchard.replay.reaffirm",
            claim_id=claim,
            action="reaffirm",
            reason="Fictional owner reaffirmation.",
        )
        receipts = (
            self.lifecycle.record_relation_event(confirm),
            self.lifecycle.record_claim_event(reaffirm),
        )
        # The owner's exact replay returns the stored receipts, marked as replays.
        self.assertEqual(
            (self.lifecycle.record_relation_event(confirm), self.lifecycle.record_claim_event(reaffirm)),
            tuple(receipt.model_copy(update={"replayed": True}) for receipt in receipts),
        )
        _, _, _, other = self.second_tenant()
        # The same key and target from another tenant never returns the receipt.
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            other.record_relation_event(confirm)
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            other.record_claim_event(reaffirm)
        self.assertEqual(
            self.row(
                "SELECT (SELECT count(*) FROM memoriesql.bead_relation_events WHERE relation_id=%s),"
                " (SELECT count(*) FROM memoriesql.bead_claim_events WHERE claim_id=%s)",
                (relation, claim),
            ),
            (1, 1),
        )
        self.assertEqual(self.inspect(effect).relations[0].state, "active")

    def test_revoked_endpoint_or_session_hides_claims_and_relations_whole(self) -> None:
        fenced = self.add_source("fence")
        cause, effect, claim, relation = self.related_pair(source=fenced)
        self.assertEqual(self.inspect(effect).outcome, "available")
        self.db.execute(
            "UPDATE memoriesql.protected_resources SET status='revoked',revoked_at=clock_timestamp() WHERE resource_id=%s",
            (fenced,),
        )
        # A relation to an unreadable endpoint never leaks it: the whole read is unavailable.
        for bead in (cause, effect):
            with self.subTest(bead=bead):
                self.assertEqual(self.inspect(bead).outcome, "unavailable")
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            self.lifecycle.record_relation_event(RecordRelationEvent(
                idempotency_key="orchard.revoked.dispute",
                relation_id=relation,
                action="dispute",
                reason="The endpoint is no longer readable.",
            ))
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            self.lifecycle.record_claim_event(RecordClaimEvent(
                idempotency_key="orchard.revoked.retract",
                claim_id=claim,
                action="retract",
                reason="The claim's bead is no longer readable.",
            ))
        readable = self.author("Fictional note: the south gate is open.", "south")
        self.assertEqual(self.inspect(readable).outcome, "available")
        self.db.execute(
            "UPDATE memoriesql.authentication_credentials SET status='revoked',revoked_at=clock_timestamp() WHERE principal_id=%s",
            (self.principal,),
        )
        # A revoked session cannot open an authorization context, so it reads and
        # governs nothing, even what it could read before.
        self.assertEqual(self.inspect(readable).outcome, "unavailable")
        with self.assertRaises(psycopg.errors.InvalidAuthorizationSpecification):
            self.lifecycle.record_claim_event(RecordClaimEvent(
                idempotency_key="orchard.revoked.session",
                claim_id=claim,
                action="reaffirm",
                reason="A revoked session tries to govern.",
            ))

    def test_revision_four_mentions_still_accept_at_schema_27(self) -> None:
        self.setup_mentions()
        result = asyncio.run(
            fixtures.LocalMentions.worker(self).run_once()
        )
        self.assertEqual(result.task_status, "succeeded", result)
        self.assertFalse(self.inspect(self.bound.bead_id).relations_capable)


def load_tests(loader: Any, standard_tests: Any, pattern: Any) -> Any:
    return unittest.TestSuite(
        AuthoredRelations(name)
        for name in AuthoredRelations.__dict__
        if name.startswith("test_")
    )


if __name__ == "__main__":
    unittest.main()
