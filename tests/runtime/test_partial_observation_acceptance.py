"""Partial acceptance of authored observations at head schema (guard audit P1-f (b)).

Fictional two-unit capture in a disposable database. A result that withholds
one bead settles succeeded: the other bead is accepted, the withheld bead stays
thin, and an owner-only row with a fixed reason records it. Only the human
owner reads that row; a paired agent and the worker cannot, and no other
database function reads the table. Without a recorded row a missing bead is
still refused, and a withheld bead can never also be annotated.
"""

from __future__ import annotations

import hashlib
import unittest
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import psycopg
from psycopg.types.json import Jsonb

from memoriesql.application.observation_commands import AcceptSourceEventV2Command
from memoriesql.application.observation_tasks import INITIAL_OBSERVATIONS_TASK
from memoriesql.application.semantic_task_contracts import (
    SemanticAgentContractIdentity,
    SemanticResultStatus,
    SemanticRootRunCorrelation,
    SemanticRunEvent,
    SemanticRunEventKind,
    SemanticTaskResult,
    WithheldOutputItem,
    canonical_sha256,
)
from memoriesql.infrastructure.postgres.authorization import PostgresAuthorizationPort
from memoriesql.infrastructure.postgres.canonical_transactions import (
    PostgresCanonicalTransactions,
)
from memoriesql.infrastructure.postgres.migration_runner import discover_migrations

if TYPE_CHECKING:
    from tests.runtime.test_postgres_runtime import PostgresRuntime, migrate
else:
    from test_postgres_runtime import PostgresRuntime, migrate

TABLE = "memoriesql.semantic_withheld_output_items"
READ = "SELECT item_kind,item_id,reason_code FROM memoriesql.withheld_semantic_output_v1(%s)"


class PartialObservationAcceptance(PostgresRuntime):
    def setUp(self) -> None:
        super().setUp()
        migrate(
            self.db,
            expected_current_version=14,
            target_version=discover_migrations()[-1].version,
        )
        original = self.command()
        content = "The fictional orchard has one gate."
        gate = original.units[0].model_copy(
            update=dict(
                source_unit_id=uuid.uuid4(),
                external_unit_id="orchard.gate",
                unit_ordinal=1,
                content_text=content,
                content_hash=hashlib.sha256(content.encode()).hexdigest(),
                detail=original.units[0].detail.model_copy(update=dict(turn_id="orchard.gate")),
            )
        )
        self.capture = AcceptSourceEventV2Command.model_validate(
            {
                **original.model_copy(
                    update=dict(units=original.units + (gate,), checkpoint=None)
                ).model_dump(mode="json"),
                "contract_version": 2,
                "expected_schema_version": 15,
            }
        )
        with self.db.transaction():
            self.begin()
            self.accepted = PostgresCanonicalTransactions(self.db).accept_source_event_v2(
                self.capture, recorded_at=datetime.now(UTC)
            )
        self.pair_worker()
        self.claimed = self.claim()
        self.task = self.claimed.fence.task_id
        # Each observation unit's bead, in unit order.
        self.beads = [
            self.row(
                "SELECT bead_id FROM memoriesql.beads WHERE tenant_id=%s AND source_unit_id=%s",
                (self.tenant, unit.source_unit_id),
            )[0]
            for unit in self.capture.units
        ]
        self.trees, self.gate = self.beads
        definition: Any = INITIAL_OBSERVATIONS_TASK
        with self.db.transaction():
            self.assertTrue(
                self.worker_queue().record_run_event(
                    self.claimed.fence,
                    SemanticRunEvent(
                        event_kind=SemanticRunEventKind.RUN_STARTED,
                        task_id=str(self.task),
                        attempt_id=str(self.claimed.fence.attempt_id),
                        correlation=SemanticRootRunCorrelation(
                            run_id="orchard.run",
                            run_role="direct_leaf",
                            agent_contract=SemanticAgentContractIdentity(
                                agent_key=definition.leaf_agent_key,
                                input_contract=definition.input_contract.reference,
                                output_contract=definition.output_contract.reference,
                            ),
                            model_profile=definition.model_profile,
                        ),
                    ),
                    recorded_at=datetime.now(UTC),
                )
            )

    def row(self, query: str, parameters: tuple[Any, ...] = ()) -> Any:
        found = self.db.execute(query, parameters).fetchone()
        assert found is not None
        return found

    def annotation(self, index: int) -> dict[str, Any]:
        unit = self.capture.units[index]
        statement = str(uuid.uuid4())
        clause = {"text": "A fictional count.", "statement_ids": [statement]}
        return {
            "bead_id": str(self.beads[index]),
            "event_id": str(self.accepted.event_id),
            "source_unit_id": str(unit.source_unit_id),
            "bead_version_id": str(uuid.uuid4()),
            "expected_bead_version": 0,
            "bead_type_key": "observation",
            "bead_type_revision": 1,
            "statements": [
                {
                    "statement_id": statement,
                    "statement_kind": "observation",
                    "statement_text": "A fictional count.",
                    "evidence": [
                        {
                            "source_unit_id": str(unit.source_unit_id),
                            "content_hash": unit.content_hash,
                        }
                    ],
                    "model_run_ref": "orchard.run",
                }
            ],
            "render": {"title": clause, "summary": [clause]},
        }

    def result(
        self, indexes: tuple[int, ...], withheld: tuple[tuple[Any, str], ...] = ()
    ) -> Any:
        definition: Any = INITIAL_OBSERVATIONS_TASK
        payload = definition.output_contract.model_type.model_validate(
            {"annotations": [self.annotation(index) for index in indexes]}
        )
        return SemanticTaskResult[definition.output_contract.model_type](
            status=SemanticResultStatus.SUCCEEDED,
            task_id=str(self.task),
            attempt_id=str(self.claimed.fence.attempt_id),
            task_kind=definition.task_kind,
            contract_revision=definition.contract_revision,
            output_contract_hash=definition.output_contract.schema_hash,
            typed_output=payload,
            output_hash=canonical_sha256(payload.model_dump(mode="json")),
            used_evidence_refs=tuple(
                str(self.capture.units[index].source_unit_id) for index in indexes
            ),
            model_run_refs=("orchard.run",),
            withheld_items=tuple(
                WithheldOutputItem(item_kind="bead", item_id=str(bead), reason_code=reason)
                for bead, reason in withheld
            ),
        )

    def persist(self, result: Any) -> str:
        with self.db.transaction():
            return str(
                self.worker_queue().record_canonical_result(
                    self.claimed.fence, result, recorded_at=datetime.now(UTC)
                )
            )

    def statements(self, bead: Any) -> int:
        return int(
            self.row(
                "SELECT count(*) FROM memoriesql.bead_semantic_statements "
                "WHERE tenant_id=%s AND bead_id=%s",
                (self.tenant, bead),
            )[0]
        )

    def withheld_rows(self) -> int:
        return int(self.row(f"SELECT count(*) FROM {TABLE}")[0])

    def owner_read(self, task: Any = None) -> list[Any]:
        with self.db.transaction():
            self.begin()
            return self.db.execute(READ, (task,)).fetchall()

    def pair_agent(self) -> str:
        secret = hashlib.sha256(b"fictional orchard agent session").hexdigest()
        principal = uuid.uuid4()
        with self.db.transaction():
            self.begin()
            self.db.execute(
                "SELECT memoriesql.pair_local_client("
                "%s,%s,%s,%s,'agent','paired_agent',%s,%s,%s,%s,%s)",
                (
                    principal,
                    uuid.uuid4(),
                    uuid.uuid4(),
                    uuid.uuid4(),
                    ["memory.inspect", "memory.query"],
                    [self.scope],
                    secret,
                    self.now,
                    self.now + timedelta(hours=1),
                ),
            )
            self.db.execute(
                "SELECT memoriesql.create_access_grant(%s,%s,%s,%s,%s,%s)",
                (
                    uuid.uuid4(),
                    principal,
                    self.scope,
                    ["read"],
                    self.now,
                    self.now + timedelta(hours=1),
                ),
            )
        return secret

    def versions(self, bead: Any) -> int:
        return int(
            self.row(
                "SELECT count(*) FROM memoriesql.bead_versions WHERE tenant_id=%s AND bead_id=%s",
                (self.tenant, bead),
            )[0]
        )

    def test_a_withheld_bead_stays_thin_and_only_the_owner_sees_why(self) -> None:
        trees, gate = self.versions(self.trees), self.versions(self.gate)
        self.assertEqual(
            self.persist(self.result((0,), ((self.gate, "withheld.not_authored"),))),
            "succeeded",
        )
        self.assertEqual(
            self.row(
                "SELECT status FROM memoriesql.semantic_tasks WHERE tenant_id=%s AND task_id=%s",
                (self.tenant, self.task),
            )[0],
            "succeeded",
        )
        self.assertGreater(self.statements(self.trees), 0)
        self.assertEqual(self.versions(self.trees), trees + 1)
        self.assertEqual((self.statements(self.gate), self.versions(self.gate)), (0, gate))
        self.assertEqual(
            self.owner_read(self.task), [("bead", self.gate, "withheld.not_authored")]
        )
        self.assertEqual(self.owner_read(), self.owner_read(self.task))
        self.assertEqual(self.owner_read(uuid.uuid4()), [])
        # A paired agent gets no rows and cannot read the table itself.
        secret = self.pair_agent()
        with self.db.transaction():
            self.db.execute("SET LOCAL ROLE memoriesql_application")
            PostgresAuthorizationPort(self.db).begin_context(
                credential_sha256=secret, requested_workspace_id=self.workspace
            )
            self.assertEqual(self.db.execute(READ, (None,)).fetchall(), [])
        for role, query in (
            ("memoriesql_application", f"SELECT * FROM {TABLE}"),
            ("memoriesql_worker", f"SELECT * FROM {TABLE}"),
            ("memoriesql_worker", READ),
        ):
            with self.subTest(role=role, query=query):
                with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                    with self.db.transaction():
                        self.db.execute(f"SET LOCAL ROLE {role}")
                        self.db.execute(query, (None,) if "%s" in query else ())
        # No other function reads the table, and no other role may.
        self.assertEqual(
            {
                row[0]
                for row in self.db.execute(
                    "SELECT proname FROM pg_proc WHERE pronamespace='memoriesql'::regnamespace "
                    "AND prosrc LIKE '%%semantic_withheld_output_items%%'"
                ).fetchall()
            },
            {
                "record_withheld_semantic_output_v1",
                "withheld_semantic_output_v1",
                "apply_semantic_annotations_schema29",
            },
        )
        self.assertEqual(
            self.db.execute(
                "SELECT rolname FROM pg_roles WHERE rolname LIKE 'memoriesql%%' AND "
                "has_table_privilege(oid,%s,'SELECT,INSERT,UPDATE,DELETE')",
                (TABLE,),
            ).fetchall(),
            [],
        )
        with self.assertRaises(psycopg.Error):
            with self.db.transaction():
                self.db.execute(f"UPDATE {TABLE} SET reason_code='withheld.duplicated'")

    def test_without_a_withheld_row_a_missing_bead_is_still_refused(self) -> None:
        with self.assertRaises(psycopg.errors.DataError):
            self.persist(self.result((0,)))
        self.assertEqual((self.statements(self.trees), self.withheld_rows()), (0, 0))

    def test_a_withheld_bead_cannot_also_be_annotated(self) -> None:
        with self.assertRaises(psycopg.errors.DataError):
            self.persist(self.result((0, 1), ((self.gate, "withheld.not_authored"),)))
        # The recorded row rolled back with the refused apply.
        self.assertEqual((self.statements(self.trees), self.withheld_rows()), (0, 0))

    def test_the_recorder_refuses_what_is_not_this_tasks_partial_output(self) -> None:
        fence = self.claimed.fence
        record = "SELECT memoriesql.record_withheld_semantic_output_v1(%s,%s,%s,%s,%s,%s,%s)"

        def recorded(items: list[dict[str, Any]], **fence_updates: Any) -> Any:
            parameters = (
                fence.tenant_id,
                fence.task_id,
                fence.attempt_id,
                fence_updates.get("lease_generation", fence.lease_generation),
                fence.worker_id,
                fence.worker_instance_id,
                Jsonb(items),
            )
            with self.db.transaction():
                self.worker_queue()
                first = self.db.execute(record, parameters).fetchone()
                again = self.db.execute(record, parameters).fetchone()
                assert first is not None and again is not None
                return first[0], again[0]

        def item(bead: Any, reason: str = "withheld.not_authored") -> dict[str, Any]:
            return {"item_kind": "bead", "item_id": str(bead), "reason_code": reason}

        for items in (
            [],
            [item(uuid.uuid4())],
            [item(self.trees), item(self.gate)],
            [item(self.gate), item(self.gate)],
            [item(self.gate, "withheld.unregistered")],
            [{**item(self.gate), "detail": "free text"}],
        ):
            with self.subTest(items=items):
                with self.assertRaises(psycopg.errors.InvalidParameterValue):
                    recorded(items)
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            recorded([item(self.gate)], lease_generation=fence.lease_generation + 1)
        # A replay records nothing new; another set for the attempt conflicts.
        self.assertEqual(recorded([item(self.gate)]), (1, 0))
        self.assertEqual(self.withheld_rows(), 1)
        with self.assertRaises(psycopg.errors.UniqueViolation):
            recorded([item(self.gate, "withheld.duplicated")])


def load_tests(
    loader: unittest.TestLoader, tests: unittest.TestSuite, pattern: str | None
) -> unittest.TestSuite:
    # Only this module's cases; the fixture's own tests run in their module.
    return unittest.TestSuite(
        PartialObservationAcceptance(name)
        for name in loader.getTestCaseNames(PartialObservationAcceptance)
        if name in PartialObservationAcceptance.__dict__
    )


if __name__ == "__main__":
    unittest.main()
