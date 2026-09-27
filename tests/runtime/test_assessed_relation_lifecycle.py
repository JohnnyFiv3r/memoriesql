"""Installed fictional schema-30 governance; no provider calls or semantic verdicts."""

from __future__ import annotations

import hashlib
import unittest
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from psycopg.types.json import Jsonb

from memoriesql.application.assessed_relation_lifecycle import (
    AssessedRelationEventReceipt,
    BeadRelationsInspectionV3,
    InspectBeadRelationsV3,
    RecordAssessedRelationEvent,
)
from memoriesql.application.semantic_task_contracts import canonical_json_bytes
from memoriesql.infrastructure.postgres.assessed_relation_lifecycle import (
    PostgresAssessedRelationLifecycle,
)

if TYPE_CHECKING:
    from tests.runtime import test_relation_assessment as fixtures
    from tests.runtime.test_postgres_runtime import migrate
else:
    import test_relation_assessment as fixtures
    from test_postgres_runtime import migrate


class AssessedLifecycle(fixtures.RelationAssessment):
    def setUp(self) -> None:
        super().setUp()
        migrate(self.db, expected_current_version=29, target_version=30)
        self.governance: Any = PostgresAssessedRelationLifecycle(
            self.db,
            credential_sha256=self.secret_hash,
            workspace_id=self.workspace,
        )

    def assertion(
        self,
        *,
        key: str = "supports",
        accepted: bool = True,
        same: bool = False,
        scope: uuid.UUID | None = None,
        source_object: uuid.UUID | None = None,
    ) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
        local_scope = self.scope
        if scope is not None:
            self.scope = scope
        source = self.bead(
            "Fictional source: observations agree.",
            "Fictional second observation.",
            key="source",
            source=source_object,
        )
        target = (
            source
            if same
            else self.bead(
                "Fictional target: observations agree.",
                key="target",
                source=source_object,
            )
        )
        self.scope = local_scope
        self.activate_relations(source, () if same else (target,))
        self.propose(
            lambda packet: [
                self.proposal(
                    packet,
                    key,
                    self.endpoint(packet, source, 0),
                    self.endpoint(packet, target, 1 if same else 0),
                    basis="agent_inferred",
                )
            ]
        )
        if not accepted:
            self.specialist_plan = lambda packet: dict(
                contract_version=1,
                judgments=[
                    dict(
                        proposal_id=p["proposal_id"],
                        outcome="abstained",
                        consistent=None,
                        warranted=[],
                        abstention="ambiguous",
                        rationale="Fictional abstention.",
                    )
                    for p in packet["proposals"]
                ],
            )
        self.run_relations()
        row = self.row("SELECT relation_id FROM memoriesql.assessed_relations")
        return source, target, row[0]

    def read(self, bead: uuid.UUID, known: datetime | None = None) -> Any:
        return self.governance.inspect_relations(
            InspectBeadRelationsV3(contract_version=3, bead_id=bead, known_at=known)
        )

    def lifecycle_command(
        self, bead: uuid.UUID, relation: uuid.UUID, action: str, key: str, **extra: Any
    ) -> RecordAssessedRelationEvent:
        row = self.read(bead)
        self.assertIsInstance(row, BeadRelationsInspectionV3, row)
        assertion = next(r for r in row.relations if r.relation_id == relation)
        evidence = (
            [
                dict(
                    statement_id=e.statement_id,
                    source_unit_id=e.source_unit_id,
                    content_hash=e.content_sha256,
                )
                for e in assertion.evidence[:1]
            ]
            if action == "confirm"
            else []
        )
        return RecordAssessedRelationEvent.model_validate(
            dict(
                contract_version=1,
                expected_schema_version=30,
                idempotency_key=key,
                relation_id=relation,
                action=action,
                reason="Human fictional decision — β 🚀.",
                evidence=evidence,
                effective_at=None,
                expected_head_token=assertion.head_token,
            )
            | extra
        )

    def original_records(self) -> Any:
        tables = (
            "beads",
            "bead_versions",
            "accepted_bead_semantics",
            "bead_semantic_statements",
            "bead_semantic_statement_evidence",
            "semantic_evidence_links",
            "semantic_task_receipts",
            "semantic_tasks",
            "semantic_task_attempts",
            "semantic_task_runs",
            "relation_assessments",
            "relation_assessment_deliveries",
            "assessed_relations",
            "assessed_relation_statements",
            "relation_pair_dispositions",
        )
        return {
            table: self.row(
                "SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),'[]') FROM memoriesql."
                + table
                + " t"
            )[0]
            for table in tables
        } | {
            "original_receipts": self.row(
                "SELECT jsonb_agg(to_jsonb(r) ORDER BY idempotency_receipt_id) FROM memoriesql.idempotency_receipts r WHERE operation_kind<>'assessed_relation.event.v1'"
            )[0]
        }

    def test_lifecycle_transitions_replay_preservation_and_read_agreement(self) -> None:
        source, target, relation = self.assertion()
        before = self.row("SELECT to_jsonb(r) FROM memoriesql.assessed_relations r")[0]
        versions = self.beads_as_they_were()
        immutable = self.original_records()
        tasks = self.row("SELECT count(*) FROM memoriesql.semantic_tasks")[0]
        stale = self.lifecycle_command(source, relation, "dispute", "stale")
        confirm = self.lifecycle_command(source, relation, "confirm", "confirm-0")
        first = self.governance.record_event(confirm)
        self.assertIsInstance(first, AssessedRelationEventReceipt, first)
        self.assertEqual(first.state, "active")
        self.assertEqual(self.governance.record_event(stale).error, "head_conflict")
        self.assertEqual(
            self.governance.record_event(confirm).relation_event_id,
            first.relation_event_id,
        )
        for action, key, state in [
            ("dispute", "d1", "disputed"),
            ("dispute", "d2", "disputed"),
            ("confirm", "c1", "active"),
            ("dispute", "d3", "disputed"),
            ("retract", "r1", "retracted"),
        ]:
            cmd = self.lifecycle_command(source, relation, action, key)
            receipt = self.governance.record_event(cmd)
            self.assertIsInstance(receipt, AssessedRelationEventReceipt, receipt)
            self.assertEqual(receipt.state, state)
            for bead in (source, target):
                current = self.read(bead).relations[0]
                self.assertEqual(current.state, state)
                self.assertEqual(current.support_eligible, state == "active")
                self.assertEqual(self.inspect_v2(bead).relations[0].state, state)
            replay = self.governance.record_event(cmd)
            self.assertTrue(replay.replayed)
            self.assertEqual(replay.relation_event_id, receipt.relation_event_id)
        for action in ("confirm", "dispute", "retract"):
            refused = self.governance.record_event(
                self.lifecycle_command(source, relation, action, "terminal-" + action)
            )
            self.assertEqual(refused.error, "transition_invalid")
        self.assertEqual(self.beads_as_they_were(), versions)
        self.assertEqual(
            self.row("SELECT count(*) FROM memoriesql.semantic_tasks")[0], tasks
        )
        self.assertEqual(
            self.row("SELECT to_jsonb(r) FROM memoriesql.assessed_relations r")[0],
            before,
        )
        self.assertEqual(self.original_records(), immutable)
        events = self.read(source).relations[0].events
        self.assertEqual([e.event_number for e in events], list(range(1, 7)))
        self.assertTrue(
            all(e.recorded_by_principal_id == self.principal for e in events)
        )
        self.assertTrue(all(e.recorded_by_user_id == self.user for e in events))
        self.assertEqual(
            [e.previous_event_id for e in events],
            [None, *(e.event_id for e in events[:-1])],
        )

    def test_lifecycle_roots_uncertainty_asof_and_terminal_replay(self) -> None:
        source, target, relation = self.assertion(key="derived_from")
        accepted = self.read(source)
        self.assertEqual(accepted.relations[0].roots_status, "qualified")
        cmd = self.lifecycle_command(source, relation, "dispute", "d1")
        receipt = self.governance.record_event(cmd)
        row = self.read(source).relations[0]
        self.assertEqual(row.roots_status, "indeterminate")
        self.assertIsNone(row.independent_root_count)
        self.assertEqual(self.inspect_v2(source).outcome, "unavailable")
        self.assertEqual(self.inspect(source).outcome, "unavailable")
        old = self.read(source, accepted.frame.known_at).relations[0]
        self.assertTrue(old.support_eligible)
        self.assertEqual(old.roots_status, "qualified")
        self.assertEqual(
            self.governance.record_event(
                self.lifecycle_command(source, relation, "confirm", "c1")
            ).state,
            "active",
        )
        withdrawn = self.governance.record_event(
            self.lifecycle_command(
                source,
                relation,
                "retract",
                "r1",
                effective_at=datetime.now(UTC) - timedelta(days=1),
            )
        )
        self.assertEqual(withdrawn.state, "retracted")
        row = self.read(source).relations[0]
        self.assertEqual(row.roots_status, "indeterminate")
        self.assertEqual(
            {
                gap.reason
                for item in row.evidence
                for gap in item.roots_gap_relation_ids
            },
            {"withdrawn"},
        )
        self.assertEqual(self.governance.record_event(cmd).state, receipt.state)

    def test_lifecycle_same_bead_roots_are_unsupported(self) -> None:
        source, _, relation = self.assertion(key="derived_from", same=True)
        row = self.read(source).relations[0]
        self.assertEqual(row.roots_status, "unsupported")
        self.assertIsNone(row.independent_root_count)
        self.assertTrue(all(e.derivation_root_ids is None for e in row.evidence))
        self.assertEqual(
            row.evidence[0].roots_gap_relation_ids[0].reason,
            "statement_roots_unsupported",
        )
        self.assertEqual(self.inspect_v2(source).outcome, "unavailable")
        self.governance.record_event(
            self.lifecycle_command(source, relation, "dispute", "d1")
        )
        self.assertEqual(self.read(source).relations[0].roots_status, "indeterminate")

    def test_lifecycle_unaccepted_and_structural_refusals(self) -> None:
        source, _, relation = self.assertion(accepted=False)
        for action in ("confirm", "dispute", "retract"):
            self.assertEqual(
                self.governance.record_event(
                    self.lifecycle_command(
                        source, relation, action, "unaccepted-" + action
                    )
                ).error,
                "transition_invalid",
            )
        base = self.lifecycle_command(source, relation, "dispute", "base").model_dump(
            mode="json"
        )
        refusals: list[tuple[dict[str, Any], str]] = [
            ({"unknown": 1}, "invalid_request"),
            ({"expected_schema_version": 29}, "schema_mismatch"),
            ({"action": "confirm", "evidence": []}, "invalid_request"),
            ({"expected_head_token": None}, "invalid_request"),
            ({"effective_at": "2026-09-26T12:00:00"}, "invalid_request"),
        ]
        for changed, error in refusals:
            with self.db.transaction():
                self.begin()
                response = self.row(
                    "SELECT memoriesql.record_assessed_relation_event_v1(%s)",
                    (Jsonb(base | changed),),
                )[0]
            self.assertEqual(
                response, dict(contract_version=1, outcome="refused", error=error)
            )
        self.assertEqual(
            self.row("SELECT count(*) FROM memoriesql.assessed_relation_events")[0], 0
        )

    def test_lifecycle_hash_matches_python_exact_bytes(self) -> None:
        value = dict(z='β 🚀\n"\\', a=["é", None, True, 1], control="\x7f")
        expected = hashlib.sha256(canonical_json_bytes(value)).hexdigest()
        self.assertEqual(
            self.row("SELECT memoriesql.lifecycle_hash_v1(%s)", (Jsonb(value),))[0],
            expected,
        )

    def test_lifecycle_concurrent_distinct_and_identical_keys(self) -> None:
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier

        source, _, relation = self.assertion()

        def race(commands: list[RecordAssessedRelationEvent]) -> list[Any]:
            barrier = Barrier(2)

            def decide(cmd: RecordAssessedRelationEvent) -> Any:
                with self.connection() as connection:
                    port = PostgresAssessedRelationLifecycle(
                        connection,
                        credential_sha256=self.secret_hash,
                        workspace_id=self.workspace,
                    )
                    barrier.wait(timeout=5)
                    return port.record_event(cmd)

            with ThreadPoolExecutor(max_workers=2) as pool:
                return list(pool.map(decide, commands))

        first = self.lifecycle_command(source, relation, "dispute", "first")
        second = self.lifecycle_command(source, relation, "dispute", "second")
        results = race([first, second])
        self.assertEqual(
            sum(isinstance(r, AssessedRelationEventReceipt) for r in results), 1
        )
        self.assertEqual(
            [
                r.error
                for r in results
                if not isinstance(r, AssessedRelationEventReceipt)
            ],
            ["head_conflict"],
        )
        identical = self.lifecycle_command(source, relation, "confirm", "same")
        results = race([identical, identical])
        self.assertEqual(
            {r.relation_event_id for r in results}, {results[0].relation_event_id}
        )
        self.assertEqual(sorted(r.replayed for r in results), [False, True])
        self.assertEqual(
            self.row("SELECT count(*) FROM memoriesql.assessed_relation_events")[0], 2
        )

    def test_lifecycle_time_idempotency_and_receipt_scope(self) -> None:
        source, _, relation = self.assertion()
        future = self.lifecycle_command(
            source,
            relation,
            "dispute",
            "future",
            effective_at=datetime.now(UTC) + timedelta(days=1),
        )
        self.assertEqual(self.governance.record_event(future).error, "invalid_request")
        self.assertEqual(
            self.row("SELECT count(*) FROM memoriesql.assessed_relation_events")[0], 0
        )
        past = self.lifecycle_command(
            source,
            relation,
            "confirm",
            "past",
            effective_at="2026-09-25T01:00:00+01:00",
        )
        accepted = self.governance.record_event(past)
        equivalent = past.model_dump(mode="json") | dict(
            effective_at="2026-09-25T00:00:00Z"
        )
        self.assertTrue(
            self.governance.record_event(
                RecordAssessedRelationEvent.model_validate(equivalent)
            ).replayed
        )
        different = past.model_dump(mode="json") | dict(reason="Another human reason.")
        self.assertEqual(
            self.governance.record_event(
                RecordAssessedRelationEvent.model_validate(different)
            ).error,
            "idempotency_conflict",
        )
        workspace, _, secret, _ = self.second_tenant()
        other: Any = PostgresAssessedRelationLifecycle(
            self.db, credential_sha256=secret, workspace_id=workspace
        )
        self.assertEqual(other.record_event(past).error, "unavailable")
        self.assertEqual(
            other.inspect_relations(
                InspectBeadRelationsV3(
                    contract_version=3, bead_id=source, known_at=None
                )
            ).outcome,
            "unavailable",
        )
        self.assertEqual(
            self.read(source, datetime.now(UTC) + timedelta(days=1)).error,
            "invalid_request",
        )
        with self.connection() as connection:
            restarted: Any = PostgresAssessedRelationLifecycle(
                connection,
                credential_sha256=self.secret_hash,
                workspace_id=self.workspace,
            )
            self.assertEqual(
                restarted.record_event(past).idempotency_receipt_id,
                accepted.idempotency_receipt_id,
            )

    def test_lifecycle_nonhuman_and_revoked_authority_refuse_without_disclosure(
        self,
    ) -> None:
        source, _, relation = self.assertion()
        cmd = self.lifecycle_command(source, relation, "dispute", "decision")
        for kind in ("agent", "service", "device"):
            secret = hashlib.sha256(("fictional " + kind).encode()).hexdigest()
            with self.db.transaction():
                self.begin()
                self.db.execute(
                    "SELECT memoriesql.pair_local_client(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        uuid.uuid4(),
                        uuid.uuid4(),
                        uuid.uuid4(),
                        uuid.uuid4(),
                        kind,
                        {
                            "agent": "paired_agent",
                            "service": "background_service",
                            "device": "paired_device",
                        }[kind],
                        {
                            "agent": ["memory.query", "source.read"],
                            "service": [
                                "memory.maintain",
                                "memory.query",
                                "source.read",
                            ],
                            "device": ["source.read"],
                        }[kind],
                        [self.scope],
                        secret,
                        self.now,
                        self.now + timedelta(hours=1),
                    ),
                )
            other: Any = PostgresAssessedRelationLifecycle(
                self.db, credential_sha256=secret, workspace_id=self.workspace
            )
            self.assertEqual(
                other.record_event(cmd).model_dump(),
                dict(contract_version=1, outcome="refused", error="unavailable"),
            )
        self.db.execute(
            "UPDATE memoriesql.protected_resources SET status='revoked',revoked_at=clock_timestamp() WHERE resource_id=%s",
            (self.source,),
        )
        self.assertEqual(self.governance.record_event(cmd).error, "unavailable")
        self.assertEqual(self.read(source).outcome, "unavailable")
        self.assertEqual(
            self.row("SELECT count(*) FROM memoriesql.assessed_relation_events")[0], 0
        )

    def correction(self, pinned: uuid.UUID, before_apply: Any = None) -> uuid.UUID:
        # Use the existing public whole-bead correction command and fictional run ledger.
        from memoriesql.application.observation_commands import (
            AcceptedBeadPin,
            AcceptSourceEventV2Command,
        )
        from memoriesql.application.observation_tasks import OBSERVATION_CORRECTION_TASK
        from memoriesql.infrastructure.postgres.canonical_transactions import (
            PostgresCanonicalTransactions,
        )

        if TYPE_CHECKING:
            from tests.runtime.test_immutable_observations import ImmutableObservations
        else:
            from test_immutable_observations import ImmutableObservations
        helpers: Any = ImmutableObservations
        self.db.execute(
            "INSERT INTO memoriesql.semantic_worker_claim_policies SELECT c.tenant_id,c.workspace_id,c.principal_id,c.pairing_grant_id,p.semantic_registry_hash,p.task_kind,p.contract_revision,p.queue_name FROM memoriesql.semantic_worker_claim_policies c CROSS JOIN memoriesql.semantic_task_admission_policies p WHERE c.principal_id=%s AND c.task_kind='memory.semantic.author-complete-unit' AND c.contract_revision=6 AND p.task_kind='memory.semantic.correct-observation' ON CONFLICT DO NOTHING",
            (self.worker_principal,),
        )
        capture_data = self.command().model_dump(mode="json")
        capture_data["event"]["source_identity_key"] = "correction-" + str(pinned)
        capture_data["checkpoint"]["checkpoint_key"] = "correction-" + str(pinned)
        capture_data["semantic_task"]["idempotency_key"] = "correction-" + str(pinned)
        capture = AcceptSourceEventV2Command.model_validate(
            capture_data
            | dict(
                contract_version=2,
                expected_schema_version=15,
                idempotency_key="correction-capture-" + str(pinned),
            )
        )
        captures = getattr(self, "correction_captures", {})
        if pinned in captures:
            capture, accepted = captures[pinned]
        else:
            with self.db.transaction():
                self.begin()
                accepted = PostgresCanonicalTransactions(
                    self.db
                ).accept_source_event_v2(capture, recorded_at=datetime.now(UTC))
            captures[pinned] = (capture, accepted)
            self.correction_captures = captures
        version = self.row(
            "SELECT bead_version_id FROM memoriesql.accepted_bead_semantics WHERE bead_id=%s",
            (pinned,),
        )[0]
        pins = (AcceptedBeadPin(bead_id=pinned, bead_version_id=version),)
        target, claimed = helpers.enqueue_authorship(self, capture, accepted, pins=pins)
        result = helpers.result_for(
            self, target, capture, claimed, OBSERVATION_CORRECTION_TASK, pins=pins
        )
        with self.db.transaction():
            self.worker_queue()
            if before_apply is not None:
                before_apply()
            PostgresCanonicalTransactions(self.db).correct_observation(
                helpers.command_for(self, claimed, result),
                worker_id=claimed.fence.worker_id,
                worker_instance_id=claimed.fence.worker_instance_id,
                recorded_at=datetime.now(UTC),
            )
        return uuid.UUID(str(target.bead_ids[0]))

    def test_lifecycle_corrected_endpoint_refuses_confirmation_and_changes_head(
        self,
    ) -> None:
        source, _, relation = self.assertion()
        old = self.lifecycle_command(source, relation, "confirm", "old")
        successor = self.correction(source)
        row = self.read(source).relations[0]
        self.assertEqual(row.state, "reassessment_pending")
        self.assertTrue(row.correction_pending)
        self.assertIn(successor, row.endpoint_corrected_by)
        self.assertEqual(self.governance.record_event(old).error, "head_conflict")
        self.assertEqual(
            self.governance.record_event(
                self.lifecycle_command(source, relation, "confirm", "new")
            ).error,
            "reassessment_required",
        )
        self.assertEqual(
            self.governance.record_event(
                self.lifecycle_command(source, relation, "dispute", "d1")
            ).state,
            "disputed",
        )
        self.assertTrue(self.read(source).relations[0].correction_pending)
        self.assertEqual(
            self.governance.record_event(
                self.lifecycle_command(source, relation, "retract", "r1")
            ).state,
            "retracted",
        )

    def test_lifecycle_replacement_is_terminal_and_preserves_retired_history(
        self,
    ) -> None:
        source, target, relation = self.assertion()
        self.governance.record_event(
            self.lifecycle_command(source, relation, "dispute", "d1")
        )
        self.activate_relations(source, (target,), key="replacement")
        self.propose(
            lambda packet: [
                self.proposal(
                    packet,
                    "supports",
                    self.endpoint(packet, source, 0),
                    self.endpoint(packet, target, 0),
                    basis="agent_inferred",
                    retires=dict(
                        relation_kind="assessed",
                        relation_id=str(relation),
                        reason="Human-triggered authored replacement.",
                    ),
                )
            ]
        )
        self.run_relations()
        row = next(r for r in self.read(source).relations if r.relation_id == relation)
        self.assertEqual(row.state, "superseded")
        self.assertEqual(row.events[-1].action, "retire")
        for action in ("confirm", "dispute", "retract"):
            self.assertEqual(
                self.governance.record_event(
                    self.lifecycle_command(
                        source, relation, action, "retired-" + action
                    )
                ).error,
                "transition_invalid",
            )
        replacement = row.superseded_by[0]
        self.governance.record_event(
            self.lifecycle_command(
                source, replacement, "retract", "replacement-withdrawn"
            )
        )
        self.assertEqual(
            next(
                r for r in self.read(source).relations if r.relation_id == relation
            ).state,
            "superseded",
        )

    def second_human(self, scope: uuid.UUID) -> tuple[uuid.UUID, str]:
        from psycopg import sql

        user, principal, credential = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        secret = hashlib.sha256(b"fictional delegated human").hexdigest()
        for table, selector, identity, updates in [
            ("users", "user_id", self.user, dict(user_id=str(user))),
            (
                "principals",
                "principal_id",
                self.principal,
                dict(
                    principal_id=str(principal),
                    user_id=str(user),
                    owner_user_id=str(user),
                ),
            ),
            (
                "workspace_memberships",
                "principal_id",
                self.principal,
                dict(principal_id=str(principal), membership_id=str(uuid.uuid4())),
            ),
            (
                "authentication_credentials",
                "principal_id",
                self.principal,
                dict(
                    principal_id=str(principal),
                    credential_id=str(credential),
                    secret_sha256=secret,
                ),
            ),
        ]:
            self.db.execute(
                sql.SQL(
                    "INSERT INTO memoriesql.{table} SELECT (jsonb_populate_record(NULL::memoriesql.{table},to_jsonb(t)||%s)).* FROM memoriesql.{table} t WHERE t.{selector}=%s"
                ).format(
                    table=sql.Identifier(table), selector=sql.Identifier(selector)
                ),
                (Jsonb(updates), identity),
            )
        identity = uuid.uuid4()
        self.db.execute(
            "INSERT INTO memoriesql.auth_identities SELECT (jsonb_populate_record(NULL::memoriesql.auth_identities,to_jsonb(i)||%s)).* FROM memoriesql.auth_identities i WHERE i.user_id=%s",
            (
                Jsonb(
                    dict(
                        identity_id=str(identity),
                        user_id=str(user),
                        subject=str(identity),
                    )
                ),
                self.user,
            ),
        )
        self.db.execute(
            "INSERT INTO memoriesql.local_auth_sessions SELECT tenant_id,%s,%s,%s,created_at FROM memoriesql.local_auth_sessions WHERE credential_id=%s",
            (uuid.uuid4(), credential, identity, self.ids[7]),
        )
        with self.db.transaction():
            self.begin()
            self.db.execute(
                "SELECT memoriesql.create_access_grant(%s,%s,%s,%s,%s,%s)",
                (
                    uuid.uuid4(),
                    principal,
                    scope,
                    ["read", "write"],
                    self.now,
                    self.now + timedelta(hours=1),
                ),
            )
        return principal, secret

    def test_lifecycle_delegated_human_and_origin_revocation(self) -> None:
        scope, source_object, _ = self.remote_scope()
        source, _, relation = self.assertion(scope=scope, source_object=source_object)
        principal, secret = self.second_human(scope)
        cmd = self.lifecycle_command(source, relation, "dispute", "shared-key")
        other: Any = PostgresAssessedRelationLifecycle(
            self.db, credential_sha256=secret, workspace_id=self.workspace
        )
        first = other.record_event(cmd)
        self.assertIsInstance(first, AssessedRelationEventReceipt, first)
        self.assertEqual(
            self.read(source).relations[0].events[0].recorded_by_principal_id, principal
        )
        self.assertEqual(self.governance.record_event(cmd).error, "head_conflict")
        # Original activator's current credential is irrelevant to immutable acceptance.
        self.db.execute(
            "UPDATE memoriesql.authentication_credentials SET status='revoked',revoked_at=clock_timestamp() WHERE principal_id=%s",
            (self.principal,),
        )
        current = other.inspect_relations(
            InspectBeadRelationsV3(contract_version=3, bead_id=source, known_at=None)
        )
        self.assertIsInstance(current, BeadRelationsInspectionV3, current)
        evidence = current.relations[0].evidence[0]
        confirm = RecordAssessedRelationEvent.model_validate(
            cmd.model_dump(mode="json")
            | dict(
                idempotency_key="confirm",
                action="confirm",
                expected_head_token=current.relations[0].head_token,
                evidence=[
                    dict(
                        statement_id=str(evidence.statement_id),
                        source_unit_id=str(evidence.source_unit_id),
                        content_hash=evidence.content_sha256,
                    )
                ],
            )
        )
        self.assertEqual(other.record_event(confirm).state, "active")
        self.assertEqual(
            other.record_event(cmd).relation_event_id, first.relation_event_id
        )
        self.assertEqual(self.governance.record_event(cmd).error, "unavailable")

    def test_lifecycle_basis_read_without_write_and_denial(self) -> None:
        scope, source, grant = self.remote_scope()
        first = self.bead("Fictional first endpoint.", key="first")
        second = self.bead("Fictional second endpoint.", key="second")
        basis = self.remote_bead(
            "Fictional attribution explicitly connects endpoints.",
            key="basis",
            scope=scope,
            source=source,
        )
        self.activate_relations(first, (second, basis))
        self.propose(
            lambda packet: [
                self.proposal(
                    packet,
                    "supports",
                    self.endpoint(packet, first, 0),
                    self.endpoint(packet, second, 0),
                    basis_statements=(self.basis_pin(packet, basis, 0),),
                )
            ]
        )
        self.run_relations()
        relation = self.row("SELECT relation_id FROM memoriesql.assessed_relations")[0]
        with self.db.transaction():
            self.begin()
            self.db.execute(
                "SELECT memoriesql.revise_access_grant(%s,1,%s,'active',%s,%s,%s)",
                (
                    grant,
                    ["read"],
                    self.now,
                    self.now + timedelta(hours=1),
                    datetime.now(UTC),
                ),
            )
        self.assertEqual(
            self.governance.record_event(
                self.lifecycle_command(first, relation, "dispute", "basis-read")
            ).state,
            "disputed",
        )
        self.revoke_grant_revision(grant, 2)
        self.assertEqual(self.read(first).outcome, "unavailable")
        self.assertEqual(
            self.governance.record_event(
                RecordAssessedRelationEvent(
                    contract_version=1,
                    expected_schema_version=30,
                    idempotency_key="denied",
                    relation_id=relation,
                    action="retract",
                    reason="Readable closure required.",
                    evidence=(),
                    effective_at=None,
                    expected_head_token="a" * 64,
                )
            ).error,
            "unavailable",
        )

    def revoke_grant_revision(self, grant: uuid.UUID, revision: int) -> None:
        with self.db.transaction():
            self.begin()
            self.db.execute(
                "SELECT memoriesql.revise_access_grant(%s,%s,%s,'revoked',%s,%s,%s)",
                (
                    grant,
                    revision,
                    ["read"],
                    self.now,
                    self.now + timedelta(hours=1),
                    datetime.now(UTC),
                ),
            )

    def test_lifecycle_actual_128_and_129_bead_root_bound(self) -> None:
        previous: uuid.UUID | None = None
        for index in range(129):
            if previous is None:
                previous = self.author(
                    "Fictional original for bounded lineage.", "lineage-0"
                )
                continue
            target = previous
            previous = self.author(
                "Fictional derived ledger " + str(index),
                "lineage-" + str(index),
                candidates=(target,),
                plan=lambda extras, bead, candidates: self.relate(
                    extras, bead, candidates[0], "derived_from", basis="agent_inferred"
                ),
            )
            if index == 127:
                with self.db.transaction():
                    self.begin()
                    self.db.execute("RESET ROLE")
                    roots = self.row(
                        "SELECT memoriesql.qualified_bead_roots_v1(%s,%s,clock_timestamp())",
                        (self.tenant, previous),
                    )[0]
                self.assertEqual(roots["roots_status"], "qualified")
                self.assertEqual(roots["derivation_root_ids"], [str(self.source)])
        import psycopg

        with (
            self.assertRaises(psycopg.errors.ProgramLimitExceeded),
            self.db.transaction(),
        ):
            self.begin()
            self.db.execute("RESET ROLE")
            self.db.execute(
                "SELECT memoriesql.qualified_bead_roots_v1(%s,%s,clock_timestamp())",
                (self.tenant, previous),
            )

    def protected_frame(self, bead: uuid.UUID, known: datetime | None = None) -> Any:
        from memoriesql.infrastructure.postgres.relation_projection import (
            read_relation_projection,
        )

        self.db.execute(
            "CREATE OR REPLACE FUNCTION public.fictional_relation_frame(request jsonb) RETURNS jsonb LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql AS 'SELECT memoriesql.relation_inspection_frame_v1(request)'; GRANT EXECUTE ON FUNCTION public.fictional_relation_frame(jsonb) TO memoriesql_application"
        )
        return read_relation_projection(
            self.db,
            credential_sha256=self.secret_hash,
            workspace_id=self.workspace,
            query="SELECT public.fictional_relation_frame(%s)",
            payload=InspectBeadRelationsV3(
                contract_version=3, bead_id=bead, known_at=known
            ).model_dump(mode="json"),
        )

    def test_lifecycle_protected_manifest_and_same_cutoff_visibility(self) -> None:
        source, _, relation = self.assertion(key="derived_from")
        # Timestamp alone is not a saved frame: an RR snapshot cannot see a later
        # transaction even when the selected cutoff includes its recorded event.
        frame = self.protected_frame(source)
        records, manifest = frame["dependency_records"], frame["dependency_manifest"]
        expected = sorted(
            [
                dict(
                    kind=r["kind"],
                    id=r["id"],
                    content_sha256=hashlib.sha256(
                        canonical_json_bytes(r["row"])
                    ).hexdigest(),
                )
                for r in records
            ],
            key=lambda r: (r["kind"], r["id"], r["content_sha256"]),
        )
        self.assertEqual(manifest, expected)
        self.assertEqual(
            frame["response"]["frame"]["dependency_manifest_sha256"],
            hashlib.sha256(canonical_json_bytes(expected)).hexdigest(),
        )
        classes = {r["kind"] for r in records}
        self.assertTrue(
            {
                "bead",
                "accepted_bead",
                "statement",
                "statement_evidence",
                "source_unit",
                "source_event",
                "source_object",
                "assertion_acceptance",
                "acceptance_receipt",
                "relation_head",
                "type_definition",
                "specialist_judgment",
                "pair_disposition",
                "task_status",
            }
            <= classes,
            classes,
        )
        first_head = frame["response"]["relations"][0]["head_token"]
        receipt = self.governance.record_event(
            self.lifecycle_command(source, relation, "dispute", "manifest")
        )
        later = self.protected_frame(source)
        self.assertNotEqual(
            later["response"]["frame"]["dependency_manifest_sha256"],
            frame["response"]["frame"]["dependency_manifest_sha256"],
        )
        self.assertEqual(frame["response"]["relations"][0]["head_token"], first_head)
        self.assertIn(
            "relation_event", {r["kind"] for r in later["dependency_records"]}
        )
        self.assertEqual(
            self.read(source, receipt.recorded_at).relations[0].state, "disputed"
        )
        # Public inspection refuses an unfenced or non-RR direct SQL invocation.
        with self.db.transaction():
            self.begin()
            result = self.row(
                "SELECT memoriesql.inspect_bead_relations_v3(%s)",
                (Jsonb(dict(contract_version=3, bead_id=str(source), known_at=None)),),
            )[0]
        self.assertEqual(result, dict(contract_version=3, outcome="unavailable"))
        self.assertEqual(
            self.row(
                "SELECT count(*) FROM pg_locks WHERE pid=pg_backend_pid() AND locktype='advisory'"
            )[0],
            0,
        )

        import psycopg
        from psycopg.conninfo import make_conninfo

        confirm = self.lifecycle_command(
            source, relation, "confirm", "snapshot-confirm"
        )
        with self.db.transaction():
            self.begin()
            key = self.row("SELECT memoriesql.acquire_relation_read_fence_v1()")[0]
        try:
            with self.db.transaction():
                self.db.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                self.begin()  # Establish the data snapshot before the other writer.
                with psycopg.connect(
                    make_conninfo(self.admin, dbname=self.database), autocommit=True
                ) as writer:
                    port = PostgresAssessedRelationLifecycle(
                        writer,
                        credential_sha256=self.secret_hash,
                        workspace_id=self.workspace,
                    )
                    confirmed = port.record_event(confirm)
                    assert isinstance(confirmed, AssessedRelationEventReceipt), (
                        confirmed
                    )
                    self.assertEqual(confirmed.state, "active")
                cutoff = self.row("SELECT clock_timestamp()")[0]
                old_snapshot = self.row(
                    "SELECT public.fictional_relation_frame(%s)",
                    (
                        Jsonb(
                            dict(
                                contract_version=3,
                                bead_id=str(source),
                                known_at=cutoff.isoformat(),
                            )
                        ),
                    ),
                )[0]
            fresh = self.protected_frame(source, cutoff)
            self.assertEqual(
                old_snapshot["response"]["known_at"], fresh["response"]["known_at"]
            )
            self.assertEqual(
                old_snapshot["response"]["relations"][0]["state"], "disputed"
            )
            self.assertEqual(fresh["response"]["relations"][0]["state"], "active")
            self.assertNotEqual(
                old_snapshot["response"]["frame"]["dependency_manifest_sha256"],
                fresh["response"]["frame"]["dependency_manifest_sha256"],
            )
        finally:
            self.db.execute("SELECT pg_advisory_unlock_shared(%s)", (key,))

    def test_lifecycle_evidence_refusals_receipt_incomplete_and_budget(self) -> None:
        import psycopg
        from psycopg.conninfo import make_conninfo

        source, _, relation = self.assertion()
        command = self.lifecycle_command(source, relation, "confirm", "evidence")
        evidence = command.evidence[0].model_dump(mode="json")
        for changes in (
            dict(content_hash="0" * 64),
            dict(statement_id=str(uuid.uuid4())),
            dict(source_unit_id=str(uuid.uuid4())),
        ):
            wrong = RecordAssessedRelationEvent.model_validate(
                command.model_dump(mode="json") | dict(evidence=[evidence | changes])
            )
            self.assertEqual(self.governance.record_event(wrong).error, "unavailable")
        for updates in (
            dict(evidence=[evidence, evidence]),
            dict(reason=" \t "),
            dict(effective_at="2026-09-26T01:01:01.1234567Z"),
            dict(action=None),
            dict(evidence=None),
        ):
            with self.db.transaction():
                self.begin()
                result = self.row(
                    "SELECT memoriesql.record_assessed_relation_event_v1(%s)",
                    (Jsonb(command.model_dump(mode="json") | updates),),
                )[0]
            self.assertEqual(result["error"], "invalid_request", result)
        self.assertEqual(
            self.row("SELECT count(*) FROM memoriesql.assessed_relation_events")[0], 0
        )
        first = self.governance.record_event(command)
        # A trusted interrupted fixture can leave an in-progress receipt; public
        # terminal outcomes themselves cannot be mutated into corruption.
        incomplete = self.lifecycle_command(source, relation, "dispute", "incomplete")
        internal_key = hashlib.sha256(
            canonical_json_bytes(
                [str(self.workspace), str(self.principal), "incomplete"]
            )
        ).hexdigest()
        request_hash = hashlib.sha256(
            canonical_json_bytes(incomplete.model_dump(mode="json"))
        ).hexdigest()
        self.db.execute(
            "INSERT INTO memoriesql.idempotency_receipts(tenant_id,workspace_id,access_scope_id,idempotency_receipt_id,operation_kind,idempotency_key,request_hash,status,resource_kind,resource_id,attempt_count,created_at,updated_at) SELECT tenant_id,workspace_id,access_scope_id,%s,'assessed_relation.event.v1',%s,%s,'in_progress','assessed_relation',resource_id,1,clock_timestamp(),clock_timestamp() FROM memoriesql.idempotency_receipts WHERE idempotency_receipt_id=%s",
            (uuid.uuid4(), internal_key, request_hash, first.idempotency_receipt_id),
        )
        self.assertEqual(
            self.governance.record_event(incomplete).error, "receipt_incomplete"
        )
        # A refused wait reserves no key and does not add an event.
        next_command = self.lifecycle_command(source, relation, "dispute", "wait")
        with psycopg.connect(
            make_conninfo(self.admin, dbname=self.database), autocommit=True
        ) as blocker:
            with blocker.transaction():
                blocker.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                    (str(self.tenant) + ":relation-lineage:",),
                )
                self.assertEqual(
                    self.governance.record_event(next_command).error, "budget_exhausted"
                )
        self.assertEqual(self.governance.record_event(next_command).event_number, 2)

    def test_lifecycle_revocation_wins_waiting_read_and_write_fence(self) -> None:
        from concurrent.futures import ThreadPoolExecutor
        from time import monotonic, sleep

        import psycopg
        from psycopg.conninfo import make_conninfo

        source, _, relation = self.assertion()
        command = self.lifecycle_command(source, relation, "dispute", "revocation-race")

        def operation(write: bool) -> Any:
            with psycopg.connect(
                make_conninfo(self.admin, dbname=self.database), autocommit=True
            ) as db:
                port = PostgresAssessedRelationLifecycle(
                    db, credential_sha256=self.secret_hash, workspace_id=self.workspace
                )
                return (
                    port.record_event(command)
                    if write
                    else port.inspect_relations(
                        InspectBeadRelationsV3(
                            contract_version=3, bead_id=source, known_at=None
                        )
                    )
                )

        with ThreadPoolExecutor(max_workers=2) as pool:
            with self.db.transaction():
                self.db.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                    (str(self.tenant) + ":semantic_outcome_authority:",),
                )
                writes, reads = (
                    pool.submit(operation, True),
                    pool.submit(operation, False),
                )
                deadline = monotonic() + 0.4
                while (
                    self.row(
                        "SELECT count(*) FROM pg_locks WHERE locktype='advisory' AND NOT granted"
                    )[0]
                    < 2
                ):
                    self.assertLess(
                        monotonic(),
                        deadline,
                        "both operations must actually wait behind the authority fence",
                    )
                    sleep(0.005)
                self.db.execute(
                    "UPDATE memoriesql.authentication_credentials SET status='revoked',revoked_at=clock_timestamp() WHERE principal_id=%s",
                    (self.principal,),
                )
            self.assertEqual(writes.result().error, "unavailable")
            self.assertEqual(reads.result().outcome, "unavailable")
        self.assertEqual(
            self.row("SELECT count(*) FROM memoriesql.assessed_relation_events")[0], 0
        )

    def test_lifecycle_endpoint_write_membership_principal_and_evidence_authority(
        self,
    ) -> None:
        scope, source_object, grant = self.remote_scope()
        source, _, relation = self.assertion(scope=scope, source_object=source_object)
        command = self.lifecycle_command(source, relation, "dispute", "write")
        with self.db.transaction():
            self.begin()
            self.db.execute(
                "SELECT memoriesql.revise_access_grant(%s,1,%s,'active',%s,%s,%s)",
                (
                    grant,
                    ["read"],
                    self.now,
                    self.now + timedelta(hours=1),
                    datetime.now(UTC),
                ),
            )
        self.assertEqual(self.read(source).outcome, "available")
        self.assertEqual(self.governance.record_event(command).error, "unavailable")
        # Current authority, not origin attribution, decides every access.
        self.db.execute(
            "UPDATE memoriesql.workspace_memberships SET status='revoked',revoked_at=clock_timestamp() WHERE principal_id=%s",
            (self.principal,),
        )
        self.assertEqual(self.read(source).outcome, "unavailable")
        self.db.execute(
            "UPDATE memoriesql.workspace_memberships SET status='active',revoked_at=NULL WHERE principal_id=%s",
            (self.principal,),
        )
        self.db.execute(
            "UPDATE memoriesql.principals SET status='revoked',revoked_at=clock_timestamp() WHERE principal_id=%s",
            (self.principal,),
        )
        self.assertEqual(self.read(source).outcome, "unavailable")

    def test_lifecycle_basis_correction_and_transitive_successors(self) -> None:
        first = self.bead("Fictional first endpoint.", key="first")
        second = self.bead("Fictional second endpoint.", key="second")
        basis = self.bead("Fictional attribution connects both endpoints.", key="basis")
        self.activate_relations(first, (second, basis))
        self.propose(
            lambda packet: [
                self.proposal(
                    packet,
                    "supports",
                    self.endpoint(packet, first, 0),
                    self.endpoint(packet, second, 0),
                    basis_statements=(self.basis_pin(packet, basis, 0),),
                )
            ]
        )
        self.run_relations()
        relation = self.row("SELECT relation_id FROM memoriesql.assessed_relations")[0]
        old = self.lifecycle_command(first, relation, "confirm", "before-basis")
        successor = self.correction(basis)
        further = self.correction(successor)
        row = self.read(first).relations[0]
        self.assertEqual(row.state, "reassessment_pending")
        self.assertEqual(set(row.basis_corrected_by), {successor, further})
        self.assertEqual(self.governance.record_event(old).error, "head_conflict")
        self.assertEqual(
            self.governance.record_event(
                self.lifecycle_command(first, relation, "confirm", "after-basis")
            ).error,
            "reassessment_required",
        )
        self.assertEqual(
            self.governance.record_event(
                self.lifecycle_command(first, relation, "retract", "withdraw-basis")
            ).state,
            "retracted",
        )

    def test_lifecycle_cycle_reservations_survive_dispute_until_withdrawal(
        self,
    ) -> None:
        source, target, relation = self.assertion(key="derived_from")
        self.governance.record_event(
            self.lifecycle_command(source, relation, "dispute", "reserved")
        )

        def reverse(packet: Any) -> list[Any]:
            return [
                self.proposal(
                    packet,
                    "derived_from",
                    self.endpoint(packet, target, 0),
                    self.endpoint(packet, source, 0),
                    basis="agent_inferred",
                )
            ]

        self.activate_relations(target, (source,), key="reverse-reserved")
        self.propose(reverse)
        self.run_relations(expect="failed_terminal")
        self.assertEqual(
            self.row("SELECT count(*) FROM memoriesql.assessed_relations")[0], 1
        )
        self.governance.record_event(
            self.lifecycle_command(source, relation, "retract", "released")
        )
        self.activate_relations(target, (source,), key="reverse-released")
        self.run_relations()
        self.assertEqual(
            self.row(
                "SELECT count(*) FROM memoriesql.assessed_relations WHERE acceptance='accepted'"
            )[0],
            2,
        )
        self.assertFalse(
            next(
                r for r in self.read(source).relations if r.relation_id == relation
            ).support_eligible
        )

    def test_lifecycle_unsupported_propagates_and_observing_union_has_no_aggregate_cap(
        self,
    ) -> None:
        source, _, relation = self.assertion(key="derived_from", same=True)
        ancestor = self.author(
            "Fictional upstream derivative.",
            "ancestor",
            candidates=(source,),
            plan=lambda extras, bead, c: self.relate(
                extras, bead, c[0], "derived_from", basis="agent_inferred"
            ),
        )
        row = self.read(ancestor).relations[0]
        self.assertEqual(row.roots_status, "unsupported")
        self.assertIsNone(row.independent_root_count)
        self.assertIn(
            relation,
            {gap.relation_id for e in row.evidence for gap in e.roots_gap_relation_ids},
        )
        # The observing set is populated through canonical whole-bead correction
        # commands, including multiple visible branches on one captured unit.
        original = self.bead("Fictional independent original.", key="original")
        observer = self.correction(original)
        for _ in range(128):
            observer = self.correction(original)
        unit = self.row(
            "SELECT source_unit_id FROM memoriesql.beads WHERE bead_id=%s", (observer,)
        )[0]
        with self.db.transaction():
            self.begin()
            self.db.execute("RESET ROLE")
            roots = self.row(
                "SELECT memoriesql.qualified_unit_roots_v1(%s,%s,clock_timestamp())",
                (self.tenant, unit),
            )[0]
        self.assertEqual(roots["roots_status"], "qualified")
        self.assertEqual(roots["derivation_root_ids"], [str(self.source)])
        self.assertGreaterEqual(
            len({r["id"] for r in roots["dependencies"] if r["kind"] == "bead"}), 129
        )

    def test_lifecycle_correction_wins_competing_confirmation(self) -> None:
        from concurrent.futures import ThreadPoolExecutor
        from time import monotonic, sleep

        import psycopg
        from psycopg.conninfo import make_conninfo

        source, _, relation = self.assertion()
        command = self.lifecycle_command(source, relation, "confirm", "correction-race")

        def confirm() -> Any:
            with psycopg.connect(
                make_conninfo(self.admin, dbname=self.database), autocommit=True
            ) as db:
                return PostgresAssessedRelationLifecycle(
                    db, credential_sha256=self.secret_hash, workspace_id=self.workspace
                ).record_event(command)

        with ThreadPoolExecutor(max_workers=1) as pool:
            futures: list[Any] = []

            def before_apply() -> None:
                self.db.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                    (str(self.tenant) + ":relation-lineage:",),
                )
                futures.append(pool.submit(confirm))
                deadline = monotonic() + 0.4
                while not self.row(
                    "SELECT EXISTS(SELECT 1 FROM pg_locks WHERE locktype='advisory' AND NOT granted)"
                )[0]:
                    self.assertLess(monotonic(), deadline)
                    sleep(0.005)

            self.correction(source, before_apply=before_apply)
            self.assertEqual(futures[0].result().error, "head_conflict")
        self.assertEqual(self.read(source).relations[0].state, "reassessment_pending")
        self.assertEqual(
            self.row("SELECT count(*) FROM memoriesql.assessed_relation_events")[0], 0
        )

    def replacement_race(self, replacement_wins: bool) -> None:
        from concurrent.futures import ThreadPoolExecutor
        from time import monotonic, sleep
        from unittest.mock import patch

        import psycopg
        from psycopg.conninfo import make_conninfo

        from memoriesql.infrastructure.jobs.postgres_semantic_queue import (
            PostgresSemanticTaskQueue,
        )

        source, target, relation = self.assertion()
        command = self.lifecycle_command(source, relation, "retract", "terminal-race")
        self.activate_relations(source, (target,), key="replacement-race")
        self.propose(
            lambda packet: [
                self.proposal(
                    packet,
                    "supports",
                    self.endpoint(packet, source, 0),
                    self.endpoint(packet, target, 0),
                    basis="agent_inferred",
                    retires=dict(
                        relation_kind="assessed",
                        relation_id=str(relation),
                        reason="Fictional exact replacement.",
                    ),
                )
            ]
        )

        from pydantic import TypeAdapter

        from memoriesql.application.assessed_relation_lifecycle import EventResult
        from memoriesql.infrastructure.jobs.integrated_semantic_worker import (
            IntegratedSemanticWorker,
        )
        from memoriesql.infrastructure.postgres.authorization import (
            PostgresAuthorizationPort,
        )

        # Prepare authentication after all model/attestation work and before the
        # competing write transactions. Dead-session context cleanup otherwise
        # waits on the worker's preparation transaction, before any lifecycle lock.
        human: Any = None
        canonical_original = PostgresSemanticTaskQueue.record_canonical_result
        persist_original = IntegratedSemanticWorker._persist_result
        versions = self.beads_as_they_were()
        with ThreadPoolExecutor(max_workers=1) as pool:
            futures: list[Any] = []

            def prepared(worker: Any, *args: Any, **kwargs: Any) -> Any:
                nonlocal human
                human = psycopg.connect(
                    make_conninfo(self.admin, dbname=self.database), autocommit=True
                )
                with human.transaction():
                    human.execute("SET LOCAL ROLE memoriesql_application")
                    PostgresAuthorizationPort(human).begin_context(
                        credential_sha256=self.secret_hash,
                        requested_workspace_id=self.workspace,
                    )
                human.execute("BEGIN")
                human.execute("SET LOCAL ROLE memoriesql_application")
                PostgresAuthorizationPort(human).begin_context(
                    credential_sha256=self.secret_hash,
                    requested_workspace_id=self.workspace,
                )
                try:
                    outcome = persist_original(worker, *args, **kwargs)
                    if futures:
                        futures[0].result()
                    return outcome
                finally:
                    human.rollback()
                    human.close()

            def withdraw() -> Any:
                row = human.execute(
                    "SELECT memoriesql.record_assessed_relation_event_v1(%s)",
                    (Jsonb(command.model_dump(mode="json")),),
                ).fetchone()
                human.commit()
                return TypeAdapter(EventResult).validate_python(row[0])

            def intercept(queue: Any, *args: Any, **kwargs: Any) -> Any:
                if replacement_wins:
                    queue._connection.execute(
                        "SELECT pg_advisory_xact_lock_shared(hashtextextended(%s,0)),pg_advisory_xact_lock(hashtextextended(%s,0))",
                        (
                            str(self.tenant) + ":semantic_outcome_authority:",
                            str(self.tenant) + ":relation-lineage:",
                        ),
                    )
                futures.append(pool.submit(withdraw))
                if replacement_wins:
                    deadline = monotonic() + 0.4
                    while not self.row(
                        "SELECT EXISTS(SELECT 1 FROM pg_locks WHERE locktype='advisory' AND NOT granted)"
                    )[0]:
                        self.assertLess(monotonic(), deadline)
                        sleep(0.005)
                else:
                    result = futures[0].result()
                    self.assertIsInstance(result, AssessedRelationEventReceipt, result)
                    self.assertEqual(result.state, "retracted")
                return canonical_original(queue, *args, **kwargs)

            with (
                patch.object(IntegratedSemanticWorker, "_persist_result", prepared),
                patch.object(
                    PostgresSemanticTaskQueue, "record_canonical_result", intercept
                ),
            ):
                self.run_relations(
                    expect="succeeded" if replacement_wins else "failed_terminal"
                )
            result = futures[0].result()
        self.assertEqual(self.beads_as_they_were(), versions)
        row = next(r for r in self.read(source).relations if r.relation_id == relation)
        self.assertEqual(row.state, "superseded" if replacement_wins else "retracted")
        self.assertFalse(row.support_eligible)
        if replacement_wins:
            self.assertEqual(result.error, "head_conflict")
            self.assertEqual(
                self.row("SELECT count(*) FROM memoriesql.relation_retirements")[0], 1
            )
            self.assertEqual(
                self.row("SELECT count(*) FROM memoriesql.assessed_relation_events")[0],
                0,
            )
        else:
            self.assertEqual(
                self.row("SELECT count(*) FROM memoriesql.relation_retirements")[0], 0
            )
            self.assertEqual(
                self.row("SELECT count(*) FROM memoriesql.assessed_relations")[0], 1
            )

    def test_lifecycle_replacement_wins_concurrent_withdrawal(self) -> None:
        self.replacement_race(True)

    def test_lifecycle_withdrawal_wins_replacement_and_losing_apply_preserves_beads(
        self,
    ) -> None:
        self.replacement_race(False)

    def test_lifecycle_governance_evidence_does_not_change_roots_and_revocation_refuses_replay(
        self,
    ) -> None:
        source, _, relation = self.assertion()
        scope, source_object, grant = self.remote_scope()
        other = self.remote_bead(
            "Fictional additional human evidence.",
            key="human-evidence",
            scope=scope,
            source=source_object,
        )
        statement, unit, content_hash = self.row(
            "SELECT s.statement_id,e.evidence_source_unit_id,e.evidence_content_hash FROM memoriesql.bead_semantic_statements s JOIN memoriesql.bead_semantic_statement_evidence e USING(tenant_id,statement_id) WHERE s.bead_id=%s",
            (other,),
        )
        command = RecordAssessedRelationEvent.model_validate(
            self.lifecycle_command(
                source, relation, "confirm", "foreign-evidence"
            ).model_dump(mode="json")
            | dict(
                evidence=[
                    dict(
                        statement_id=str(statement),
                        source_unit_id=str(unit),
                        content_hash=content_hash,
                    )
                ]
            )
        )
        before = self.read(source).relations[0].independent_root_count
        receipt = self.governance.record_event(command)
        current = self.read(source).relations[0]
        self.assertEqual(current.independent_root_count, before)
        self.assertEqual(current.events[0].evidence[0].statement_id, statement)
        self.revoke(grant)
        self.assertEqual(self.governance.record_event(command).error, "unavailable")
        self.assertEqual(self.read(source).outcome, "unavailable")
        self.assertEqual(
            self.row(
                "SELECT count(*) FROM memoriesql.assessed_relation_events WHERE relation_event_id=%s",
                (receipt.relation_event_id,),
            )[0],
            1,
        )

    def private_roots(self, bead: uuid.UUID) -> Any:
        with self.db.transaction():
            self.begin()
            self.db.execute("RESET ROLE")
            return self.row(
                "SELECT memoriesql.qualified_bead_roots_v1(%s,%s,clock_timestamp())",
                (self.tenant, bead),
            )[0]

    def test_lifecycle_pending_assertion_can_be_retired_without_clearing_correction(
        self,
    ) -> None:
        source, target, relation = self.assertion()
        successor = self.correction(source)
        self.assertEqual(self.read(source).relations[0].state, "reassessment_pending")
        self.activate_relations(source, (target,), key="pending-replacement")
        self.propose(
            lambda packet: [
                self.proposal(
                    packet,
                    "supports",
                    self.endpoint(packet, source, 0),
                    self.endpoint(packet, target, 0),
                    basis="agent_inferred",
                    retires=dict(
                        relation_kind="assessed",
                        relation_id=str(relation),
                        reason="Fictional explicit pending replacement.",
                    ),
                )
            ]
        )
        self.run_relations()
        old = next(r for r in self.read(source).relations if r.relation_id == relation)
        self.assertEqual(old.state, "superseded")
        self.assertTrue(old.correction_pending)
        self.assertIn(successor, old.endpoint_corrected_by)
        self.assertEqual(len(old.superseded_by), 1)
        self.assertFalse(old.support_eligible)

    def test_lifecycle_not_accepted_assertion_cannot_be_retired(self) -> None:
        source, target, relation = self.assertion(accepted=False)
        before = self.beads_as_they_were()
        self.specialist_plan = None
        self.activate_relations(source, (target,), key="unaccepted-replacement")
        self.propose(
            lambda packet: [
                self.proposal(
                    packet,
                    "supports",
                    self.endpoint(packet, source, 0),
                    self.endpoint(packet, target, 0),
                    basis="agent_inferred",
                    retires=dict(
                        relation_kind="assessed",
                        relation_id=str(relation),
                        reason="Fictional illegal unaccepted retirement.",
                    ),
                )
            ]
        )
        self.run_relations(expect="failed_terminal")
        self.assertEqual(self.beads_as_they_were(), before)
        self.assertEqual(
            self.row("SELECT count(*) FROM memoriesql.relation_retirements")[0], 0
        )
        self.assertEqual(
            self.row("SELECT count(*) FROM memoriesql.assessed_relations")[0], 1
        )
        self.assertEqual(self.read(source).relations[0].state, "not_accepted")

    def test_lifecycle_equal_recorded_times_follow_sequence_not_uuid(self) -> None:
        source, _, relation = self.assertion()
        known = self.row("SELECT clock_timestamp()")[0]
        # Generation-only fixture: exercise the installed function's identical
        # authority, transition, concurrency, receipt and storage code with a tied
        # recording clock and deliberately reversed event UUID order. No stored
        # original event/receipt is changed and no lifecycle rule is substituted.
        definition = self.row(
            "SELECT pg_get_functiondef('memoriesql.record_assessed_relation_event_v1(jsonb)'::regprocedure)"
        )[0]
        high, low = uuid.UUID(int=(1 << 128) - 2), uuid.UUID(int=1)
        tied = definition.replace(
            "eid uuid:=uuidv7()",
            "eid uuid:=CASE WHEN request->>'action'='dispute' THEN '"
            + str(high)
            + "'::uuid ELSE '"
            + str(low)
            + "'::uuid END",
            1,
        ).replace(
            "now_at:=clock_timestamp();",
            "now_at:='" + known.isoformat() + "'::timestamptz;",
            1,
        )
        self.assertNotEqual(tied, definition)
        self.db.execute(tied)
        try:
            dispute = self.governance.record_event(
                self.lifecycle_command(source, relation, "dispute", "tied-dispute")
            )
            confirm_command = self.lifecycle_command(
                source, relation, "confirm", "tied-confirm"
            )
            confirm = self.governance.record_event(confirm_command)
        finally:
            self.db.execute(definition)
        self.assertEqual(dispute.recorded_at, confirm.recorded_at)
        self.assertGreater(dispute.relation_event_id.int, confirm.relation_event_id.int)
        self.assertEqual(confirm.previous_event_id, dispute.relation_event_id)
        current = self.read(source, known).relations[0]
        self.assertEqual(current.state, "active")
        self.assertEqual([e.event_number for e in current.events], [1, 2])
        self.assertEqual([e.action for e in current.events], ["dispute", "confirm"])
        self.assertTrue(self.governance.record_event(confirm_command).replayed)

    def test_lifecycle_legacy_authored_mixed_cycle_reservation_and_release(
        self,
    ) -> None:
        source, target, relation = self.assertion(key="derived_from")
        self.governance.record_event(
            self.lifecycle_command(source, relation, "dispute", "mixed-reserve")
        )
        before = self.beads_as_they_were()

        def bridge(extras: Any, bead: Any, candidates: Any) -> None:
            self.relate(
                extras, bead, candidates[0], "derived_from", basis="agent_inferred"
            )
            self.relate(
                extras,
                bead,
                candidates[1],
                "derived_from",
                direction="to_authored",
                basis="agent_inferred",
            )

        self.author(
            "Fictional legacy cycle bridge.",
            "mixed-refuse",
            candidates=(source, target),
            plan=bridge,
            expect=None,
        )
        self.assertEqual(self.beads_as_they_were(), before)
        self.governance.record_event(
            self.lifecycle_command(source, relation, "retract", "mixed-release")
        )
        accepted = self.author(
            "Fictional released legacy bridge.",
            "mixed-allow",
            candidates=(source, target),
            plan=bridge,
        )
        self.assertEqual(len(self.read(accepted).relations), 2)

    def test_lifecycle_legacy_permitted_revision_cannot_close_forbidden_pin(
        self,
    ) -> None:
        from unittest.mock import patch

        from memoriesql.application.authored_relations import RelationTypePin
        from memoriesql.application.relation_lifecycle import (
            DecideRelationType,
            ProposeRelationType,
        )

        proposal = ProposeRelationType(
            idempotency_key="legacy-custom-1",
            access_scope_id=self.scope,
            key="mitigates",
            label="Mitigates",
            definition="Fictional direction.",
            endpoint_rule="action -> harm",
            forward_reading="mitigates",
            inverse_reading="is mitigated by",
            symmetric=False,
            cycle_policy="forbidden",
            reason="Fictional controlled vocabulary.",
        )

        def accept(p: Any, key: str) -> None:
            candidate = self.lifecycle.propose_relation_type(p)
            self.lifecycle.decide_relation_type(
                DecideRelationType(
                    idempotency_key=key,
                    access_scope_id=self.scope,
                    candidate_id=candidate.candidate_id,
                    decision="accepted",
                    reason="Fictional owner acceptance.",
                )
            )

        accept(proposal, "legacy-custom-1-accept")
        source = self.bead("Fictional first endpoint.", key="legacy-policy-source")
        target = self.bead("Fictional second endpoint.", key="legacy-policy-target")
        self.activate_relations(
            source,
            (target,),
            key="legacy-policy-forward",
            vocabulary=fixtures.VOCABULARY
            + (RelationTypePin(key="mitigates", revision=1),),
        )
        self.propose(
            lambda packet: [
                self.proposal(
                    packet,
                    "mitigates",
                    self.endpoint(packet, source, 0),
                    self.endpoint(packet, target, 0),
                    basis="agent_inferred",
                )
            ]
        )
        self.run_relations()
        relation = self.read(source).relations[0].relation_id
        accept(
            proposal.model_copy(
                update=dict(idempotency_key="legacy-custom-2", cycle_policy="permitted")
            ),
            "legacy-custom-2-accept",
        )

        def bridge(extras: Any, bead: Any, candidates: Any) -> None:
            self.relate(
                extras, bead, candidates[0], "mitigates", basis="agent_inferred"
            )
            self.relate(
                extras,
                bead,
                candidates[1],
                "mitigates",
                direction="to_authored",
                basis="agent_inferred",
            )
            for row in extras["relations"]:
                row["relation_type"]["revision"] = 2

        activate = self.activate

        def latest(*args: Any, **kwargs: Any) -> Any:
            return activate(
                *args,
                **(
                    kwargs
                    | dict(
                        vocabulary=fixtures.VOCABULARY
                        + (RelationTypePin(key="mitigates", revision=2),)
                    )
                ),
            )

        before = self.beads_as_they_were()
        with patch.object(self, "activate", side_effect=latest):
            self.author(
                "Fictional later permitted cycle bridge.",
                "legacy-permitted-refuse",
                candidates=(source, target),
                plan=bridge,
                expect=None,
            )
        self.assertEqual(self.beads_as_they_were(), before)
        self.governance.record_event(
            self.lifecycle_command(source, relation, "retract", "legacy-policy-release")
        )
        with patch.object(self, "activate", side_effect=latest):
            self.author(
                "Fictional permitted released bridge.",
                "legacy-permitted-allow",
                candidates=(source, target),
                plan=bridge,
            )

    def test_lifecycle_separate_evidence_needs_query_raw_read_without_maintain(
        self,
    ) -> None:
        source, _, relation = self.assertion()
        scope, source_object, grant = self.remote_scope()
        other = self.remote_bead(
            "Fictional read-only governance evidence.",
            key="raw-only",
            scope=scope,
            source=source_object,
        )
        statement, unit, content_hash = self.row(
            "SELECT s.statement_id,e.evidence_source_unit_id,e.evidence_content_hash FROM memoriesql.bead_semantic_statements s JOIN memoriesql.bead_semantic_statement_evidence e USING(tenant_id,statement_id) WHERE s.bead_id=%s",
            (other,),
        )
        # A trusted denial-only policy fixture makes the capability distinction
        # observable without creating a product grant API or granting authority.
        definition = self.row(
            "SELECT pg_get_functiondef('memoriesql.current_context_event_authorized(uuid,uuid,text,text)'::regprocedure)"
        )[0]
        self.db.execute(
            definition.replace(
                "SELECT EXISTS (",
                "SELECT NOT (requested_access_scope_id='"
                + str(scope)
                + "'::uuid AND requested_capability='memory.maintain') AND EXISTS (",
                1,
            )
        )
        with self.db.transaction():
            self.begin()
            self.db.execute("RESET ROLE")
            self.assertFalse(
                self.row(
                    "SELECT memoriesql.current_context_event_authorized(%s,(SELECT event_id FROM memoriesql.beads WHERE bead_id=%s),'memory.maintain','read')",
                    (scope, other),
                )[0]
            )
        command = self.lifecycle_command(
            source, relation, "confirm", "raw-only-confirm"
        ).model_copy(
            update=dict(
                evidence=(
                    self.lifecycle_command(source, relation, "confirm", "raw-pin")
                    .evidence[0]
                    .model_copy(
                        update=dict(
                            statement_id=statement,
                            source_unit_id=unit,
                            content_hash=content_hash,
                        )
                    ),
                )
            )
        )
        receipt = self.governance.record_event(command)
        self.assertIsInstance(receipt, AssessedRelationEventReceipt, receipt)
        self.assertTrue(self.governance.record_event(command).replayed)
        self.assertEqual(
            self.read(source).relations[0].events[0].evidence[0].statement_id, statement
        )
        self.assertEqual(
            self.governance.record_event(
                self.lifecycle_command(
                    source, relation, "dispute", "raw-history-dispute"
                )
            ).state,
            "disputed",
        )
        self.assertEqual(
            self.governance.record_event(
                self.lifecycle_command(
                    source, relation, "retract", "raw-history-retract"
                )
            ).state,
            "retracted",
        )
        self.revoke(grant)
        self.assertEqual(self.governance.record_event(command).error, "unavailable")

    def test_lifecycle_true_no_observer_fallback_and_future_unit_refusal(self) -> None:
        import psycopg

        original = self.command()
        unit = original.units[0].model_copy(
            update=dict(
                source_unit_id=uuid.uuid4(),
                is_observation=False,
                parent_unit_id=original.units[0].source_unit_id,
                detail=original.units[0].detail.model_copy(
                    update=dict(turn_id="orchard.non-observer")
                ),
                external_unit_id="orchard.non-observer",
                unit_ordinal=1,
            )
        )
        command = original.model_copy(
            update=dict(units=original.units + (unit,), checkpoint=None)
        )
        self.accept(command)
        known = self.row(
            "SELECT GREATEST(u.created_at,e.recorded_at) FROM memoriesql.source_units u JOIN memoriesql.source_events e USING(tenant_id,event_id) WHERE u.source_unit_id=%s",
            (unit.source_unit_id,),
        )[0]
        self.assertEqual(
            self.row(
                "SELECT count(*) FROM memoriesql.beads WHERE source_unit_id=%s",
                (unit.source_unit_id,),
            )[0],
            0,
        )

        def qualify(known: Any, source_unit: uuid.UUID = unit.source_unit_id) -> Any:
            with self.db.transaction():
                self.begin()
                self.db.execute("RESET ROLE")
                return self.row(
                    "SELECT memoriesql.qualified_unit_roots_v1(%s,%s,%s)",
                    (self.tenant, source_unit, known),
                )[0]

        roots = qualify(known)
        self.assertEqual(roots["roots_status"], "qualified")
        self.assertEqual(roots["derivation_root_ids"], [str(self.source)])
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            qualify(known, uuid.uuid4())
        # Trusted clock-only seed adjustment isolates a future unit on an already
        # visible event; no content/evidence or application authority is changed.
        with self.db.transaction():
            self.db.execute(
                "ALTER TABLE memoriesql.source_units DISABLE TRIGGER source_units_immutable"
            )
            self.db.execute(
                "UPDATE memoriesql.source_units SET created_at=%s WHERE source_unit_id=%s",
                (known + timedelta(microseconds=1), unit.source_unit_id),
            )
            self.db.execute(
                "ALTER TABLE memoriesql.source_units ENABLE TRIGGER source_units_immutable"
            )
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            qualify(known)
        self.assertEqual(
            qualify(known + timedelta(microseconds=1))["roots_status"], "qualified"
        )

    def test_lifecycle_shared_frame_context_keeps_one_snapshot_and_cleans_up(
        self,
    ) -> None:
        from psycopg.pq import TransactionStatus

        from memoriesql.infrastructure.postgres.relation_projection import (
            relation_projection_frame,
        )

        source, _, relation = self.assertion()
        command = self.lifecycle_command(source, relation, "dispute", "shared-frame")
        with self.connection() as connection:
            with relation_projection_frame(
                connection,
                credential_sha256=self.secret_hash,
                workspace_id=self.workspace,
            ) as frame:
                payload = Jsonb(
                    dict(contract_version=3, bead_id=str(source), known_at=None)
                )
                first_result = frame.execute(
                    "SELECT memoriesql.inspect_bead_relations_v3(%s)", (payload,)
                ).fetchone()
                assert first_result is not None
                first = first_result[0]
                with self.connection() as writer:
                    port = PostgresAssessedRelationLifecycle(
                        writer,
                        credential_sha256=self.secret_hash,
                        workspace_id=self.workspace,
                    )
                    receipt = port.record_event(command)
                    assert isinstance(receipt, AssessedRelationEventReceipt), receipt
                    self.assertEqual(receipt.state, "disputed")
                second_result = frame.execute(
                    "SELECT memoriesql.inspect_bead_relations_v3(%s)", (payload,)
                ).fetchone()
                assert second_result is not None
                second = second_result[0]
                self.assertEqual(first["relations"], second["relations"])
                self.assertEqual(first["relations"][0]["state"], "active")
            self.assertEqual(connection.info.transaction_status, TransactionStatus.IDLE)
            self.assertEqual(
                connection.execute(
                    "SELECT count(*) FROM pg_locks WHERE pid=pg_backend_pid() AND locktype='advisory'"
                ).fetchone()[0],
                0,
            )
        self.assertEqual(self.read(source).relations[0].state, "disputed")

    def test_lifecycle_corrected_before_acceptance_and_legacy_read_agreement(
        self,
    ) -> None:
        source = self.bead("Fictional old source.", key="pre-correct-source")
        target = self.bead("Fictional target.", key="pre-correct-target")
        successor = self.correction(source)
        self.activate_relations(source, (target,), key="pre-correct-assess")
        self.propose(
            lambda packet: [
                self.proposal(
                    packet,
                    "supports",
                    self.endpoint(packet, source, 0),
                    self.endpoint(packet, target, 0),
                    basis="agent_inferred",
                )
            ]
        )
        self.run_relations()
        row = self.read(source).relations[0]
        self.assertEqual(row.state, "reassessment_pending")
        self.assertFalse(row.support_eligible)
        self.assertIn(successor, row.endpoint_corrected_by)
        self.assertEqual(self.inspect_v2(source).relations[0].state, row.state)
        refused = self.governance.record_event(
            self.lifecycle_command(
                source, row.relation_id, "confirm", "pre-correct-confirm"
            )
        )
        self.assertEqual(refused.error, "reassessment_required")
        dispute = self.governance.record_event(
            self.lifecycle_command(
                source, row.relation_id, "dispute", "pre-correct-dispute"
            )
        )
        self.assertEqual(dispute.state, "disputed")
        self.assertTrue(self.read(source).relations[0].correction_pending)

    def test_lifecycle_forbidden_pin_survives_later_permitted_revision(self) -> None:
        from memoriesql.application.authored_relations import RelationTypePin
        from memoriesql.application.relation_lifecycle import (
            DecideRelationType,
            ProposeRelationType,
        )

        proposal = ProposeRelationType(
            idempotency_key="custom-1",
            access_scope_id=self.scope,
            key="mitigates",
            label="Mitigates",
            definition="Fictional direction.",
            endpoint_rule="action -> harm",
            forward_reading="mitigates",
            inverse_reading="is mitigated by",
            symmetric=False,
            cycle_policy="forbidden",
            reason="Fictional controlled vocabulary.",
        )

        def accept(p: Any, key: str) -> None:
            candidate = self.lifecycle.propose_relation_type(p)
            self.lifecycle.decide_relation_type(
                DecideRelationType(
                    idempotency_key=key,
                    access_scope_id=self.scope,
                    candidate_id=candidate.candidate_id,
                    decision="accepted",
                    reason="Fictional owner acceptance.",
                )
            )

        accept(proposal, "custom-1-accept")
        source = self.bead("Fictional first endpoint.", key="policy-source")
        target = self.bead("Fictional second endpoint.", key="policy-target")
        self.activate_relations(
            source,
            (target,),
            key="policy-forward",
            vocabulary=fixtures.VOCABULARY
            + (RelationTypePin(key="mitigates", revision=1),),
        )
        self.propose(
            lambda packet: [
                self.proposal(
                    packet,
                    "mitigates",
                    self.endpoint(packet, source, 0),
                    self.endpoint(packet, target, 0),
                    basis="agent_inferred",
                )
            ]
        )
        self.run_relations()
        relation = self.read(source).relations[0].relation_id
        self.governance.record_event(
            self.lifecycle_command(source, relation, "dispute", "policy-dispute")
        )
        accept(
            proposal.model_copy(
                update=dict(idempotency_key="custom-2", cycle_policy="permitted")
            ),
            "custom-2-accept",
        )

        def reverse(packet: Any) -> Any:
            result = self.proposal(
                packet,
                "mitigates",
                self.endpoint(packet, target, 0),
                self.endpoint(packet, source, 0),
                basis="agent_inferred",
            )
            result["relation_type"]["revision"] = 2
            return [result]

        self.activate_relations(
            target,
            (source,),
            key="policy-reverse-refused",
            vocabulary=fixtures.VOCABULARY
            + (RelationTypePin(key="mitigates", revision=2),),
        )
        self.propose(reverse)
        self.run_relations(expect="failed_terminal")
        self.assertEqual(
            self.row("SELECT count(*) FROM memoriesql.assessed_relations")[0], 1
        )
        self.governance.record_event(
            self.lifecycle_command(source, relation, "retract", "policy-release")
        )
        self.activate_relations(
            target,
            (source,),
            key="policy-reverse-accepted",
            vocabulary=fixtures.VOCABULARY
            + (RelationTypePin(key="mitigates", revision=2),),
        )
        self.run_relations()
        self.assertEqual(
            self.row("SELECT count(*) FROM memoriesql.assessed_relations")[0], 2
        )

    def test_lifecycle_scc_creation_clock_uuid_ties_and_shared_roots(self) -> None:
        quarry = self.add_source("quarry")
        ledger = self.author(
            "Fictional original ledger.",
            "ledger",
            occurred_at=self.now + timedelta(days=1),
        )

        def cycle(extras: Any, bead: Any, candidates: Any) -> None:
            restated = dict(
                bead["statements"][0],
                statement_id=str(uuid.uuid4()),
                statement_text="Fictional separate statement.",
            )
            bead["statements"].append(restated)
            bead["render"]["summary"].append(
                dict(
                    text=restated["statement_text"],
                    statement_ids=[restated["statement_id"]],
                )
            )
            self.relate(
                extras, bead, candidates[0], "derived_from", basis="agent_inferred"
            )
            self.relate(
                extras,
                bead,
                candidates[0],
                "derived_from",
                direction="to_authored",
                basis="agent_inferred",
            )
            extras["relations"][1]["authored_statement_ids"] = [
                restated["statement_id"]
            ]

        note = self.author(
            "Fictional later-created quarry note.",
            "quarry",
            source=quarry,
            candidates=(ledger,),
            plan=cycle,
            occurred_at=self.now - timedelta(days=1),
        )
        self.assertEqual(
            self.private_roots(note)["derivation_root_ids"], [str(self.source)]
        )
        # Controlled immutable creation-time fixtures isolate the mechanical tie
        # rule; application/worker roles cannot perform these trusted seed edits.
        with self.db.transaction():
            self.db.execute(
                "ALTER TABLE memoriesql.beads DISABLE TRIGGER beads_immutable"
            )
            self.db.execute(
                "UPDATE memoriesql.beads SET created_at=(SELECT created_at+interval '1 microsecond' FROM memoriesql.beads WHERE bead_id=%s) WHERE bead_id=%s",
                (note, ledger),
            )
            self.db.execute(
                "ALTER TABLE memoriesql.beads ENABLE TRIGGER beads_immutable"
            )
        self.assertEqual(self.private_roots(note)["derivation_root_ids"], [str(quarry)])
        with self.db.transaction():
            self.db.execute(
                "ALTER TABLE memoriesql.beads DISABLE TRIGGER beads_immutable"
            )
            self.db.execute(
                "UPDATE memoriesql.beads SET created_at=(SELECT created_at FROM memoriesql.beads WHERE bead_id=%s) WHERE bead_id=%s",
                (ledger, note),
            )
            self.db.execute(
                "ALTER TABLE memoriesql.beads ENABLE TRIGGER beads_immutable"
            )
        expected = self.source if ledger.int < note.int else quarry
        self.assertEqual(
            self.private_roots(note)["derivation_root_ids"], [str(expected)]
        )
        second = self.author(
            "Fictional independent quarry original.", "second-quarry", source=quarry
        )
        other_source = self.add_source("other")
        third = self.author("Fictional other original.", "other", source=other_source)
        summary = self.author(
            "Fictional union of shared sources.",
            "summary",
            candidates=(note, second, third),
            plan=lambda extras, bead, c: [
                self.relate(extras, bead, p, "derived_from", basis="agent_inferred")
                for p in c
            ],
        )
        self.assertEqual(
            self.private_roots(summary)["derivation_root_ids"],
            sorted({str(expected), str(quarry), str(other_source)}),
        )

    def test_lifecycle_full_history_and_whole_response_budget_refusal(self) -> None:
        source, _, relation = self.assertion()
        command = self.lifecycle_command(source, relation, "dispute", "history-0")
        for index in range(70):
            command = RecordAssessedRelationEvent.model_validate(
                command.model_dump(mode="json")
                | dict(idempotency_key="history-" + str(index))
            )
            receipt = self.governance.record_event(command)
            self.assertIsInstance(receipt, AssessedRelationEventReceipt, receipt)
            command = command.model_copy(
                update=dict(expected_head_token=receipt.head_token)
            )
        current = self.read(source).relations[0]
        self.assertEqual(len(current.events), 70)
        self.assertEqual([e.event_number for e in current.events], list(range(1, 71)))
        self.assertEqual(len(self.inspect_v2(source).relations[0].events), 70)
        confirmed = self.governance.record_event(
            self.lifecycle_command(source, relation, "confirm", "close-history")
        )
        self.assertTrue(confirmed.support_eligible)
        # Fifty legal reasons alone exceed 512 KiB in the normative ASCII JSON
        # encoding. Receipts supply the compared head without pruning the history.
        command = command.model_copy(
            update=dict(
                expected_head_token=confirmed.head_token,
                reason="Fictional " + "🧭" * 1000,
            )
        )
        for index in range(50):
            command = command.model_copy(
                update=dict(idempotency_key="large-history-" + str(index))
            )
            receipt = self.governance.record_event(command)
            self.assertIsInstance(receipt, AssessedRelationEventReceipt, receipt)
            command = command.model_copy(
                update=dict(expected_head_token=receipt.head_token)
            )
        self.assertEqual(
            self.row("SELECT count(*) FROM memoriesql.assessed_relation_events")[0], 121
        )
        refused = self.read(source)
        self.assertEqual(
            refused.model_dump(), dict(contract_version=3, outcome="budget_exhausted")
        )
        self.assertEqual(self.inspect_v2(source).outcome, "budget_exhausted")

    def test_lifecycle_same_tenant_workspace_boundary_and_storage_fences(self) -> None:
        from psycopg import sql

        source, _, relation = self.assertion()
        command = self.lifecycle_command(
            source, relation, "dispute", "workspace-boundary"
        )
        workspace = uuid.uuid4()
        self.db.execute(
            "INSERT INTO memoriesql.workspaces SELECT (jsonb_populate_record(NULL::memoriesql.workspaces,to_jsonb(w)||%s)).* FROM memoriesql.workspaces w WHERE workspace_id=%s",
            (Jsonb(dict(workspace_id=str(workspace))), self.workspace),
        )
        self.db.execute(
            "INSERT INTO memoriesql.workspace_memberships SELECT (jsonb_populate_record(NULL::memoriesql.workspace_memberships,to_jsonb(m)||%s)).* FROM memoriesql.workspace_memberships m WHERE workspace_id=%s AND principal_id=%s",
            (Jsonb(dict(workspace_id=str(workspace))), self.workspace, self.principal),
        )
        other = PostgresAssessedRelationLifecycle(
            self.db, credential_sha256=self.secret_hash, workspace_id=workspace
        )
        result = other.record_event(command)
        assert not isinstance(result, AssessedRelationEventReceipt), result
        self.assertEqual(result.error, "unavailable")
        self.assertEqual(
            other.inspect_relations(
                InspectBeadRelationsV3(
                    contract_version=3, bead_id=source, known_at=None
                )
            ).outcome,
            "unavailable",
        )
        for table in ("assessed_relation_events", "assessed_relation_event_evidence"):
            self.assertEqual(
                self.row(
                    "SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid=%s::regclass",
                    ("memoriesql." + table,),
                ),
                (True, True),
            )
            for role in ("memoriesql_application", "memoriesql_worker"):
                for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                    self.assertFalse(
                        self.row(
                            "SELECT has_table_privilege(%s,%s,%s)",
                            (role, "memoriesql." + table, privilege),
                        )[0]
                    )
        receipt = self.governance.record_event(command)
        import psycopg

        with (
            self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState),
            self.db.transaction(),
        ):
            self.db.execute(
                sql.SQL(
                    "UPDATE memoriesql.assessed_relation_events SET reason='Changed' WHERE relation_event_id=%s"
                ),
                (receipt.relation_event_id,),
            )


class ForwardUpgrade(fixtures.RelationAssessment):
    def test_installed_schema29_upgrade_preserves_acceptance_and_refuses_missing_capability(
        self,
    ) -> None:
        helpers: Any = AssessedLifecycle
        source, _, relation = helpers.assertion(self)
        before = helpers.original_records(self)
        port = PostgresAssessedRelationLifecycle(
            self.db, credential_sha256=self.secret_hash, workspace_id=self.workspace
        )
        command = RecordAssessedRelationEvent(
            contract_version=1,
            expected_schema_version=30,
            idempotency_key="before-upgrade",
            relation_id=relation,
            action="dispute",
            reason="Fictional upgrade fence.",
            evidence=(),
            effective_at=None,
            expected_head_token="a" * 64,
        )
        refused = port.record_event(command)
        assert not isinstance(refused, AssessedRelationEventReceipt), refused
        self.assertEqual(refused.error, "schema_mismatch")
        migrate(self.db, expected_current_version=29, target_version=30)
        self.assertEqual(helpers.original_records(self), before)
        frame = port.inspect_relations(
            InspectBeadRelationsV3(contract_version=3, bead_id=source, known_at=None)
        )
        assert isinstance(frame, BeadRelationsInspectionV3), frame
        self.assertEqual(frame.relations[0].state, "active")
        self.assertEqual(frame.relations[0].events, ())


def load_tests(
    loader: unittest.TestLoader, tests: unittest.TestSuite, pattern: str | None
) -> unittest.TestSuite:
    # Only new cases: inherited schema-29 scenarios already have their own convergence module.
    return loader.loadTestsFromNames(
        [
            __name__
            + ".ForwardUpgrade.test_installed_schema29_upgrade_preserves_acceptance_and_refuses_missing_capability"
        ]
        + [
            __name__ + ".AssessedLifecycle." + name
            for name in AssessedLifecycle.__dict__
            if name.startswith("test_lifecycle_")
        ]
    )
