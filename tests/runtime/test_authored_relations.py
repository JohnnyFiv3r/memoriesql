"""Fictional authored claims, relations, evidence and lifecycle history (schema 27)."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import unittest
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import patch

import psycopg
from pydantic_ai.messages import ModelRequest, ToolCallPart, UserPromptPart

from memoriesql.application.authored_relations import (
    RELATED_EXECUTION_TASK,
    ActivateRelatedAuthorship,
    RelationTypePin,
    load_related_authorship_task_registry,
)
from memoriesql.application.canonical_transactions import SourceType
from memoriesql.application.evidence_packages import NativeFacts
from memoriesql.application.logical_unit_materialization import LogicalEventDeclaration
from memoriesql.application.relation_lifecycle import (
    DecideRelationType,
    InspectBeadRelations,
    LifecycleEvidence,
    ProposeRelationType,
    RecordClaimEvent,
    RecordRelationEvent,
    RelationAction,
)
from memoriesql.infrastructure.postgres.relation_lifecycle import (
    PostgresRelationLifecycle,
)

if TYPE_CHECKING:
    from tests.runtime import test_local_entity_mentions as fixtures
    from tests.runtime import test_source_revisiting as source_fixtures
    from tests.runtime.test_postgres_runtime import migrate
else:
    import test_local_entity_mentions as fixtures
    import test_source_revisiting as source_fixtures
    from test_postgres_runtime import migrate

BUILT_INS = (
    "supports", "contradicts", "caused_by", "led_to", "enables", "part_of",
    "depends_on", "blocks", "derived_from", "supersedes", "associated_with",
)
VOCABULARY = tuple(RelationTypePin(key=key, revision=1) for key in BUILT_INS)
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

    def add_source(self, name: str) -> uuid.UUID:
        """A second fictional source object with its own producer and dispatch policy."""
        other = uuid.uuid4()
        self.db.execute(
            "INSERT INTO memoriesql.protected_resources SELECT tenant_id,workspace_id,access_scope_id,%s,resource_kind,owner_user_id,status,created_by_principal_id,created_at FROM memoriesql.protected_resources WHERE resource_id=%s",
            (other, self.source),
        )
        self.db.execute(
            "INSERT INTO memoriesql.source_objects(tenant_id,workspace_id,access_scope_id,source_object_id,source_system,object_kind,external_object_id,schema_version,metadata,created_at,last_observed_at,owner_user_id) SELECT tenant_id,workspace_id,access_scope_id,%s,source_system,object_kind,%s,schema_version,metadata,created_at,last_observed_at,owner_user_id FROM memoriesql.source_objects WHERE source_object_id=%s",
            (other, "orchard." + name, self.source),
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
            "uncertainty": None,
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
            plan=lambda extras, bead, c: ids.setdefault("first", self.relate(extras, bead, c[0], "caused_by", basis="inferred", confidence=0.4)),
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
        # Supersession only counts while its replacement stands.
        self.assertEqual(state(b, first), ("active", ()))
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

    def test_workspace_vocabulary_enters_only_by_decision_and_pins_exact_revisions(self) -> None:
        proposal = ProposeRelationType(
            idempotency_key="orchard.mitigates.1",
            access_scope_id=self.scope,
            key="mitigates",
            label="Mitigates",
            definition="The source's cited action reduces the target's cited fictional harm.",
            forward_reading="mitigates",
            inverse_reading="is mitigated by",
            symmetric=False,
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

    def test_known_time_and_late_arrival_inherit_source_clocks(self) -> None:
        monday = datetime(2026, 9, 21, 9, 0, tzinfo=UTC)
        sunday = monday - timedelta(days=1)
        ids: dict[str, str] = {}
        gate = self.author(
            "The fictional gate was open on Monday.",
            "gate",
            occurred_at=monday,
            plan=lambda extras, bead, _: ids.setdefault("open", self.add_claim(extras, bead, "orchard gate", "position", "open")),
        )
        before = datetime.now(UTC)

        def late_plan(extras: dict[str, Any], bead: dict[str, Any], candidates: list[Any]) -> None:
            ids["closed"] = self.add_claim(extras, bead, "orchard gate", "position", "closed")
            self.relate(extras, bead, candidates[0], "contradicts", basis="inferred", confidence=0.55)
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
        self.assertGreater(closed_claim.recorded_at, open_claim.recorded_at)
        earlier = self.inspect(gate, known_at=before)
        self.assertEqual((earlier.relations, len(earlier.claims)), ((), 1))
        not_yet = self.inspect(late, known_at=before)
        self.assertEqual(
            (not_yet.outcome, not_yet.relations_capable, not_yet.claims), ("available", False, ())
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
