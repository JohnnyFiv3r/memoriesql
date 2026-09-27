"""Seen fictional invocation fixtures; no host-isolation or quality certificate."""

from __future__ import annotations

import os
import threading
import unittest
from dataclasses import replace
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from unittest.mock import patch
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from memoriesql.application.agent_sql_catalog import SqlParameter
from memoriesql.infrastructure.postgres import restricted_query as runtime
from memoriesql.infrastructure.postgres.relation_sql_population import (
    prepare_relation_population,
)
from memoriesql.infrastructure.postgres.result_preparation import (
    PostgresResultPreparation,
)

if TYPE_CHECKING:
    from tests.runtime import query_authority_fixture as authority_fixture
    from tests.runtime import test_assessed_relation_lifecycle as fixtures
    from tests.runtime.test_postgres_runtime import migrate
else:
    import query_authority_fixture as authority_fixture
    import test_assessed_relation_lifecycle as fixtures
    from test_postgres_runtime import migrate


@unittest.skipUnless(
    os.environ.get("N1_TEST_DATABASE_URL"), "disposable PostgreSQL required"
)
class RestrictedQuery(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = fixtures.AssessedLifecycle()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.db = self.fixture.db
        migrate(self.db, expected_current_version=30, target_version=33)
        self.reader = "pr05_query_" + uuid4().hex
        self.profile = authority_fixture.provision_fictional_reader(
            self.db, self.reader
        )
        self.addCleanup(self.drop_reader)
        self.store = PostgresResultPreparation(
            self.db,
            credential_sha256=self.fixture.secret_hash,
            workspace_id=self.fixture.workspace,
        )
        self.executor = runtime.PostgresRestrictedQuery(
            reader_factory=self.reader_connection,
            control_factory=self.fixture.connection,
            authority_profile=self.profile,
            credential_sha256=self.fixture.secret_hash,
            workspace_id=self.fixture.workspace,
            policy_hash="5" * 64,
        )

    def drop_reader(self) -> None:
        # Fixture cleanup drops the DB later; its objects/grants must go first.
        self.db.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(self.reader)))
        self.db.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(self.reader)))

    def scalar(self, text: str, values: tuple[Any, ...] = ()) -> Any:
        row = self.db.execute(text, values).fetchone()
        assert row is not None
        return row[0]

    def reader_connection(self) -> psycopg.Connection[Any]:
        return psycopg.connect(
            make_conninfo(
                os.environ["N1_TEST_DATABASE_URL"],
                dbname=self.db.info.dbname,
                user=self.reader,
                password="fictional-only",
            ),
            autocommit=True,
        )

    def prepare(self) -> Any:
        return prepare_relation_population(
            self.db,
            credential_sha256=self.fixture.secret_hash,
            workspace_id=self.fixture.workspace,
            known_at=None,
            byte_budget=64 * 1024 * 1024,
        )

    def reserve(self) -> Any:
        return self.store.reserve(
            run_ref=uuid4(),
            step_key=uuid4(),
            request_fingerprint="a" * 64,
            reservation_bytes=64 * 1024 * 1024,
        )

    def test_real_login_composes_canonical_populations_and_confirms_settlement(
        self,
    ) -> None:
        _, _, relation = self.fixture.assertion()
        owner = self.reserve()
        before = self.fixture.original_records()
        with self.prepare() as population:
            query = """WITH pins AS (SELECT r.relation_id,s.statement_id FROM memory_v1.assessed_relations r
              JOIN memory_v1.relation_statements s ON r.relation_id=s.relation_id
              WHERE r.support_eligible AND r.roots_status=$1),
              copies AS (SELECT relation_id,statement_id FROM pins UNION ALL SELECT relation_id,statement_id FROM pins)
              SELECT relation_id,count(*) AS multiplicity,
                sum(count(*)) OVER (ORDER BY relation_id ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS running
              FROM copies GROUP BY relation_id ORDER BY relation_id"""
            result = self.executor.execute(
                self.db,
                population,
                owner,
                query,
                (SqlParameter(1, "text", "qualified"),),
            )
            self.assertEqual(result.outcome, "complete", result)
            self.assertEqual(result.rows, ((relation, 4, Decimal(4)),))
            self.assertEqual(result.settlement_state, "settled")
        self.assertEqual(self.fixture.original_records(), before)
        self.assertEqual(
            self.scalar("SELECT count(*) FROM memoriesql_query.population_rows"), 0
        )
        self.assertEqual(
            self.scalar("SELECT state FROM memoriesql_query.invocations"), "settled"
        )
        # Confirmed native settlement permits private discard, not public reuse.
        self.store.discard(
            operation_ref=owner.operation_ref, ownership_ref=owner.ownership_ref
        )

    def test_admission_rejects_setting_and_bypass_commands_before_connections(
        self,
    ) -> None:
        owner = self.reserve()
        with self.prepare() as population:
            with patch.object(
                self.executor,
                "_reader_factory",
                side_effect=AssertionError("dispatch forbidden"),
            ):
                for text in (
                    "SET work_mem='1GB'",
                    "SELECT set_config('work_mem','1GB',false)",
                    "SELECT * FROM pg_catalog.pg_authid",
                    "WITH x AS (DELETE FROM memory_v1.assessed_relations RETURNING relation_id) SELECT relation_id FROM x",
                ):
                    with self.subTest(text=text):
                        result = self.executor.execute(self.db, population, owner, text)
                        self.assertNotEqual(result.outcome, "complete")
                        self.assertEqual(result.rows, ())
                        self.assertIsNone(result.invocation_ref)

    def test_missing_relation_is_unsupported_not_an_empty_fallback(self) -> None:
        owner = self.reserve()
        with self.prepare() as population:
            result = self.executor.execute(
                self.db, population, owner, "SELECT bead_id FROM memory_v1.observations"
            )
        self.assertEqual(
            (result.outcome, result.error, result.rows),
            ("unsupported_query", "unprepared_relation", ()),
        )

    def test_wrong_login_and_manifest_drift_refuse_before_invocation(self) -> None:
        owner = self.reserve()
        with self.prepare() as population:
            with patch.object(
                self.executor, "_reader_factory", self.fixture.connection
            ):
                self.assertEqual(
                    self.executor.execute(
                        self.db,
                        population,
                        owner,
                        "SELECT relation_id FROM memory_v1.assessed_relations",
                    ).outcome,
                    "unavailable",
                )
            with patch.object(
                self.executor, "_profile", replace(self.profile, procedure_hashes={})
            ):
                self.assertEqual(
                    self.executor.execute(
                        self.db,
                        population,
                        owner,
                        "SELECT relation_id FROM memory_v1.assessed_relations",
                    ).outcome,
                    "unavailable",
                )
        self.assertEqual(
            self.scalar("SELECT count(*) FROM memoriesql_query.invocations"), 0
        )

    def test_copied_source_frame_cannot_be_used_in_another_transaction(self) -> None:
        owner = self.reserve()
        with self.prepare() as old:
            pass
        with self.prepare():
            result = self.executor.execute(
                self.db,
                old,
                owner,
                "SELECT relation_id FROM memory_v1.assessed_relations",
            )
        self.assertEqual(
            (result.outcome, result.error), ("unavailable", "source_frame")
        )

    def test_output_overflow_refuses_wholly_and_purges_stage(self) -> None:
        self.fixture.assertion()
        owner = self.reserve()
        with self.prepare() as population:
            result = self.executor.execute(
                self.db,
                population,
                owner,
                "SELECT rationale FROM memory_v1.assessed_relations",
                max_output_bytes=8192,
            )
        self.assertEqual(result.outcome, "budget_exhausted")
        self.assertEqual(result.rows, ())
        self.assertEqual(
            self.scalar("SELECT count(*) FROM memoriesql_query.population_rows"), 0
        )

    def test_replayed_owner_never_dispatches_again(self) -> None:
        self.fixture.assertion()
        owner = self.reserve()
        with self.prepare() as population:
            self.assertEqual(
                self.executor.execute(
                    self.db,
                    population,
                    owner,
                    "SELECT relation_id FROM memory_v1.assessed_relations",
                ).outcome,
                "complete",
            )
            result = self.executor.execute(
                self.db,
                population,
                replace(owner, replayed=True),
                "SELECT relation_id FROM memory_v1.assessed_relations",
            )
            # A stale in-memory non-replayed receipt cannot bypass the durable
            # invocation uniqueness check either.
            stale = self.executor.execute(
                self.db,
                population,
                owner,
                "SELECT relation_id FROM memory_v1.assessed_relations",
            )
        self.assertEqual((result.outcome, result.rows), ("settlement_pending", ()))
        self.assertEqual((stale.outcome, stale.rows), ("settlement_pending", ()))
        self.assertEqual(
            self.scalar("SELECT count(*) FROM memoriesql_query.invocations"), 1
        )

    def test_native_privileges_independently_deny_scope_writes_and_unsafe_functions(
        self,
    ) -> None:
        with self.reader_connection() as reader:
            for statement in (
                "SELECT * FROM memoriesql_query.invocations",
                "SELECT * FROM memoriesql_query.population_rows",
                "INSERT INTO memoriesql_query.population_rows VALUES (gen_random_uuid(),'relation_pairs',1,'{}')",
                "SELECT memoriesql.stage_relation_query_v1('{}')",
                "SELECT pg_sleep(0)",
                "SELECT set_config('work_mem','1GB',false)",
                "CREATE TEMP TABLE attack(id int)",
                "SET ROLE memoriesql_application",
                "SET ROLE memoriesql_query_view_owner",
            ):
                with (
                    self.subTest(statement=statement),
                    self.assertRaises(psycopg.Error),
                ):
                    reader.execute(statement)
            reader.execute("SET work_mem='8MB'")
            self.assertEqual(reader.execute("SHOW work_mem").fetchone(), ("8MB",))
            reader.execute("SET memoriesql_query.invocation_ref='guessed'")
            self.assertEqual(
                reader.execute(
                    "SELECT relation_id FROM memory_v1.assessed_relations"
                ).fetchall(),
                [],
            )

    @staticmethod
    def fanout_query() -> str:
        return "SELECT count(*) AS tuples FROM memory_v1.relation_pairs p0 " + " ".join(
            f"JOIN memory_v1.relation_pairs p{i} ON p{i}.task_id=p0.task_id"
            for i in range(1, 16)
        )

    def test_independent_deadline_cancels_even_with_native_timeout_disabled(
        self,
    ) -> None:
        self.fixture.assertion()
        owner = self.reserve()
        original = runtime._configure_reader

        def altered(reader: Any, remaining: int) -> None:
            original(reader, remaining)
            reader.execute("SET LOCAL statement_timeout=0")

        with (
            self.prepare() as population,
            patch.object(runtime, "_configure_reader", altered),
        ):
            result = self.executor.execute(
                self.db, population, owner, self.fanout_query(), budget_ms=1000
            )
        self.assertEqual(result.outcome, "budget_exhausted", result)
        self.assertIsNotNone(result.cancellation_started_ms)
        self.assertEqual(result.settlement_state, "settled")
        self.assertEqual(result.rows, ())

    def test_explicit_cancellation_is_owned_and_confirmed(self) -> None:
        self.fixture.assertion()
        owner = self.reserve()
        cancellation = threading.Event()
        trigger = threading.Timer(0.7, cancellation.set)
        with self.prepare() as population:
            trigger.start()
            try:
                result = self.executor.execute(
                    self.db,
                    population,
                    owner,
                    self.fanout_query(),
                    cancellation=cancellation,
                )
            finally:
                trigger.cancel()
                trigger.join()
        self.assertEqual(
            (result.outcome, result.settlement_state, result.rows),
            ("cancelled", "settled", ()),
            result,
        )

    def paused_reader(
        self, ready: threading.Event, release: threading.Event, captured: list[Any]
    ) -> Any:
        class Paused(psycopg.Connection[Any]):
            def cursor(self, *args: Any, **kwargs: Any) -> Any:
                if kwargs.get("name"):
                    ready.set()
                    if not release.wait(5):
                        raise RuntimeError("fictional pause not released")
                return super().cursor(*args, **kwargs)

        connection = Paused.connect(
            make_conninfo(
                os.environ["N1_TEST_DATABASE_URL"],
                dbname=self.db.info.dbname,
                user=self.reader,
                password="fictional-only",
            ),
            autocommit=True,
        )
        captured.append(connection)
        return connection

    def test_staged_scope_rejects_other_backend_and_new_transaction_on_same_login(
        self,
    ) -> None:
        self.fixture.assertion()
        owner = self.reserve()
        ready, release = threading.Event(), threading.Event()
        captured: list[Any] = []
        results: list[Any] = []
        with (
            self.prepare() as population,
            patch.object(
                self.executor,
                "_reader_factory",
                lambda: self.paused_reader(ready, release, captured),
            ),
        ):
            thread = threading.Thread(
                target=lambda: results.append(
                    self.executor.execute(
                        self.db,
                        population,
                        owner,
                        "SELECT relation_id FROM memory_v1.assessed_relations",
                    )
                )
            )
            thread.start()
            try:
                self.assertTrue(ready.wait(5))
                with self.reader_connection() as attacker:
                    attacker.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
                    self.assertEqual(
                        attacker.execute(
                            "SELECT relation_id FROM memory_v1.assessed_relations"
                        ).fetchall(),
                        [],
                    )
                    attacker.rollback()
                # Even this trusted test's possession of the real connection
                # cannot reuse its former transaction's scope for a new one.
                captured[0].rollback()
                captured[0].execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
                self.assertEqual(
                    captured[0]
                    .execute("SELECT relation_id FROM memory_v1.assessed_relations")
                    .fetchall(),
                    [],
                )
            finally:
                release.set()
                thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual((results[0].outcome, results[0].rows), ("unavailable", ()))

    def test_unconfirmed_stop_keeps_stage_and_blocks_preparation_refund(self) -> None:
        self.fixture.assertion()
        owner = self.reserve()
        ready, release = threading.Event(), threading.Event()
        captured: list[Any] = []
        results: list[Any] = []
        with (
            self.prepare() as population,
            patch.object(
                self.executor,
                "_reader_factory",
                lambda: self.paused_reader(ready, release, captured),
            ),
        ):
            thread = threading.Thread(
                target=lambda: results.append(
                    self.executor.execute(
                        self.db,
                        population,
                        owner,
                        "SELECT relation_id FROM memory_v1.assessed_relations",
                    )
                )
            )
            thread.start()
            try:
                self.assertTrue(ready.wait(5))
                with self.fixture.connection() as monitor:
                    ref = monitor.execute(
                        "SELECT invocation_ref FROM memoriesql_query.invocations"
                    ).fetchone()[0]
                    pending = runtime._control_call(
                        monitor,
                        "SELECT memoriesql.settle_relation_query_v1(%s,%s,%s,%s)",
                        (ref, owner.ownership_ref, "cancelled", None),
                    )
                    self.assertEqual(pending["state"], "settlement_pending")
                    self.assertIsNone(pending["observed_ms"])
                    self.assertEqual(pending["charged_ms"], pending["reserved_ms"])
                    recovered = self.executor.recover(owner, cancel=True)
                    self.assertIsNotNone(recovered)
                    assert recovered is not None
                    self.assertEqual(recovered.state, "settlement_pending")
                    self.assertIsNone(recovered.observed_ms)
                    self.assertEqual(recovered.charged_ms, recovered.reserved_ms)
                    self.assertGreater(
                        monitor.execute(
                            "SELECT count(*) FROM memoriesql_query.population_rows"
                        ).fetchone()[0],
                        0,
                    )
                    with (
                        monitor.transaction(),
                        self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState),
                    ):
                        monitor.execute(
                            "UPDATE memoriesql.result_preparation_operations SET state='discarded',request_fingerprint=NULL WHERE operation_ref=%s",
                            (owner.operation_ref,),
                        )
                    # Wrong ownership is refused; repeated cancellation doesn't
                    # assert rollback or refund anything.
                    with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                        runtime._control_call(
                            monitor,
                            "SELECT memoriesql.cancel_relation_query_v1(%s,%s)",
                            (ref, uuid4()),
                        )
                    runtime._control_call(
                        monitor,
                        "SELECT memoriesql.cancel_relation_query_v1(%s,%s)",
                        (ref, owner.ownership_ref),
                    )
                    runtime._control_call(
                        monitor,
                        "SELECT memoriesql.cancel_relation_query_v1(%s,%s)",
                        (ref, owner.ownership_ref),
                    )
            finally:
                release.set()
                thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(results[0].rows, ())
        self.assertEqual(results[0].settlement_state, "settled")
        recovered = self.executor.recover(owner)
        assert recovered is not None
        self.assertEqual(recovered.state, "settled")
        self.assertIsNotNone(recovered.observed_ms)
        self.store.discard(
            operation_ref=owner.operation_ref, ownership_ref=owner.ownership_ref
        )

    def test_expired_authority_refuses_whole_aggregate_and_metadata(self) -> None:
        self.fixture.assertion()
        owner = self.reserve()
        self.db.execute(
            "UPDATE memoriesql.authentication_credentials SET expires_at=clock_timestamp()+interval '4 seconds'"
        )
        ready, release = threading.Event(), threading.Event()
        captured: list[Any] = []
        results: list[Any] = []
        with (
            self.prepare() as population,
            patch.object(
                self.executor,
                "_reader_factory",
                lambda: self.paused_reader(ready, release, captured),
            ),
        ):
            thread = threading.Thread(
                target=lambda: results.append(
                    self.executor.execute(
                        self.db,
                        population,
                        owner,
                        "SELECT count(*) AS protected_count FROM memory_v1.assessed_relations",
                    )
                )
            )
            thread.start()
            try:
                self.assertTrue(ready.wait(5))
                # Wait for the actual credential deadline, not an assumed sleep
                # or simulated authorization response. Canonical fences do not
                # turn expired credentials into continuing authority.
                with self.fixture.connection() as monitor:
                    monitor.execute(
                        "SELECT pg_sleep(GREATEST(EXTRACT(EPOCH FROM expires_at-clock_timestamp())+0.05,0)) FROM memoriesql.authentication_credentials"
                    )
            finally:
                release.set()
                thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(
            (results[0].outcome, results[0].rows, results[0].columns),
            ("unavailable", (), ()),
        )
        self.assertEqual(results[0].settlement_state, "settled")
