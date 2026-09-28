"""Fictional private saved-closure checks; no result disclosure claim."""

from __future__ import annotations

import json
import os
import unittest
from typing import TYPE_CHECKING, Any

from psycopg import errors

from memoriesql.infrastructure.postgres.query_result_closure import (
    PostgresQueryResultClosure,
)

if TYPE_CHECKING:
    from tests.runtime import test_query_result_commit as commit_tests
    from tests.runtime.test_postgres_runtime import migrate
else:
    import test_query_result_commit as commit_tests
    from test_postgres_runtime import migrate


@unittest.skipUnless(
    os.environ.get("N1_TEST_DATABASE_URL"), "disposable PostgreSQL required"
)
class QueryResultClosure(unittest.TestCase):
    def setUp(self) -> None:
        self.h = commit_tests.QueryResultCommit(methodName="runTest")
        self.h.setUp()
        self.addCleanup(self.h.doCleanups)
        migrate(self.h.db, expected_current_version=34, target_version=35)

    def closure(
        self, connection: Any, *, secret: str | None = None
    ) -> PostgresQueryResultClosure:
        return PostgresQueryResultClosure(
            connection,
            credential_sha256=secret or self.h.fixture.secret_hash,
            workspace_id=self.h.fixture.workspace,
        )

    def create(
        self, sql: str = "SELECT relation_id FROM memory_v1.assessed_relations"
    ) -> tuple[Any, Any]:
        request = self.h.request(sql)
        owner = self.h.reserve_request(request)
        with self.h.population(request) as population:
            execution = self.h.execute_request(request, owner, population)
            self.assertEqual(execution.outcome, "complete", execution)
            candidate = self.h.candidate(request, population, execution)
            with self.h.fixture.connection() as control:
                receipt = self.h.adapter(control).commit(owner, candidate)
                self.assertEqual(receipt["state"], "committed")
        return candidate, receipt

    def test_original_identity_full_closure_and_restart_without_redispatch(
        self,
    ) -> None:
        self.h.fixture.assertion()
        candidate, receipt = self.create()
        with self.h.fixture.connection() as restarted:
            self.closure(restarted).check_standalone(
                candidate.result_id, candidate.content_digest
            )
        self.assertEqual(
            self.h.scalar("SELECT count(*) FROM memoriesql_query.invocations"), 1
        )
        self.assertEqual(
            self.h.scalar("SELECT count(*) FROM memoriesql.query_result_identities"),
            1,
        )
        self.assertEqual(receipt["result_id"], str(candidate.result_id))
        with self.h.fixture.connection() as control:
            with self.assertRaises(errors.InsufficientPrivilege):
                self.closure(control).check_standalone(candidate.result_id, "0" * 64)
        with self.h.runtime.reader_connection() as reader:
            with self.assertRaises(errors.InsufficientPrivilege):
                reader.execute(
                    "SELECT memoriesql.check_query_result_closure_v1(%s,%s)",
                    (candidate.result_id, candidate.content_digest),
                )

    def test_current_source_revocation_refuses_every_aggregate_and_metadata(
        self,
    ) -> None:
        self.h.fixture.assertion()
        candidate, _ = self.create(
            "SELECT count(*) AS n FROM memory_v1.assessed_relations"
        )
        with self.h.fixture.connection() as control:
            self.closure(control).check_standalone(
                candidate.result_id, candidate.content_digest
            )
        self.h.db.execute(
            "UPDATE memoriesql.protected_resources "
            "SET status='revoked',revoked_at=clock_timestamp() "
            "WHERE resource_kind='source'"
        )
        with self.h.fixture.connection() as control:
            with self.assertRaises(errors.InsufficientPrivilege):
                self.closure(control).check_standalone(
                    candidate.result_id, candidate.content_digest
                )
        self.assertEqual(
            self.h.scalar("SELECT count(*) FROM memoriesql.query_result_creations"),
            1,
        )
        self.h.db.execute(
            "UPDATE memoriesql.protected_resources "
            "SET status='active',revoked_at=NULL WHERE resource_kind='source'"
        )
        with self.h.fixture.connection() as control:
            self.closure(control).check_standalone(
                candidate.result_id, candidate.content_digest
            )

    def test_same_workspace_new_principal_cannot_adopt_result(self) -> None:
        scope, source_object, _ = self.h.fixture.remote_scope()
        self.h.fixture.assertion(scope=scope, source_object=source_object)
        candidate, _ = self.create()
        _, other_secret = self.h.fixture.second_human(scope)
        with self.h.fixture.connection() as control:
            with self.assertRaises(errors.InsufficientPrivilege):
                self.closure(control, secret=other_secret).check_standalone(
                    candidate.result_id, candidate.content_digest
                )

    def test_standalone_expiry_refuses_even_while_body_is_retained(self) -> None:
        self.h.fixture.assertion()
        candidate, _ = self.create()
        self.h.db.execute(
            "ALTER TABLE memoriesql.query_result_creations "
            "DISABLE TRIGGER query_result_creations_no_update"
        )
        try:
            self.h.db.execute(
                "WITH old_clock AS (SELECT clock_timestamp()-interval '721 hours' AS value) "
                "UPDATE memoriesql.query_result_creations SET "
                "created_at=old_clock.value,"
                "expires_at=old_clock.value+interval '720 hours' "
                "FROM old_clock WHERE result_id=%s",
                (candidate.result_id,),
            )
        finally:
            self.h.db.execute(
                "ALTER TABLE memoriesql.query_result_creations "
                "ENABLE TRIGGER query_result_creations_no_update"
            )
        with self.h.fixture.connection() as control:
            with self.assertRaises(errors.InsufficientPrivilege):
                self.closure(control).check_standalone(
                    candidate.result_id, candidate.content_digest
                )
        self.assertEqual(
            self.h.scalar(
                "SELECT count(*) FROM memoriesql.result_preparation_artifacts"
            ),
            1,
        )

    def test_missing_publication_identity_never_borrows_current_caller(self) -> None:
        self.h.fixture.assertion()
        candidate, _ = self.create()
        self.h.db.execute(
            "DELETE FROM memoriesql.query_result_identities WHERE result_id=%s",
            (candidate.result_id,),
        )
        with self.h.fixture.connection() as control:
            with self.assertRaises(errors.InsufficientPrivilege):
                self.closure(control).check_standalone(
                    candidate.result_id, candidate.content_digest
                )

    def test_identity_capture_failure_rolls_back_all_publication_partitions(
        self,
    ) -> None:
        self.h.fixture.assertion()
        request = self.h.request("SELECT relation_id FROM memory_v1.assessed_relations")
        owner = self.h.reserve_request(request)
        self.h.db.execute(
            "CREATE FUNCTION memoriesql.fixture_identity_failure() RETURNS trigger "
            "LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'fictional identity crash'; END $$"
        )
        self.h.db.execute(
            "CREATE TRIGGER fixture_identity_failure AFTER INSERT "
            "ON memoriesql.query_result_identities FOR EACH ROW "
            "EXECUTE FUNCTION memoriesql.fixture_identity_failure()"
        )
        try:
            with self.h.population(request) as population:
                candidate = self.h.candidate(
                    request,
                    population,
                    self.h.execute_request(request, owner, population),
                )
                with self.h.fixture.connection() as control:
                    with self.assertRaises(errors.RaiseException):
                        self.h.adapter(control).commit(owner, candidate)
        finally:
            self.h.db.execute(
                "DROP TRIGGER fixture_identity_failure ON memoriesql.query_result_identities"
            )
            self.h.db.execute("DROP FUNCTION memoriesql.fixture_identity_failure()")
        for table in (
            "query_result_creations",
            "query_result_identities",
            "result_preparation_artifacts",
        ):
            self.assertEqual(
                self.h.scalar(f"SELECT count(*) FROM memoriesql.{table}"), 0
            )

    def test_late_record_never_mutates_old_result(self) -> None:
        self.h.fixture.assertion()
        candidate, _ = self.create()
        before = self.h.scalar(
            "SELECT content_bytes FROM memoriesql.result_preparation_artifacts"
        )
        body = json.loads(bytes(before))
        self.assertEqual(len(body["rows"]), 1)
        # A newly assessed relation records after the old historical cutoff.
        later_source = self.h.fixture.bead(
            "Fictional later source.", "Fictional later support.", key="later-source"
        )
        later_target = self.h.fixture.bead(
            "Fictional later target.", key="later-target"
        )
        self.h.fixture.activate_relations(
            later_source, (later_target,), key="orchard.assess.later"
        )
        self.h.fixture.propose(
            lambda packet: [
                self.h.fixture.proposal(
                    packet,
                    "derived_from",
                    self.h.fixture.endpoint(packet, later_source, 0),
                    self.h.fixture.endpoint(packet, later_target, 0),
                    basis="agent_inferred",
                )
            ]
        )
        self.h.fixture.run_relations()
        with self.h.fixture.connection() as control:
            self.closure(control).check_standalone(
                candidate.result_id, candidate.content_digest
            )
        self.assertEqual(
            self.h.scalar(
                "SELECT content_bytes FROM memoriesql.result_preparation_artifacts"
            ),
            before,
        )


if __name__ == "__main__":
    unittest.main()
