"""Seen fictional native membership/atomicity; no public availability claim."""

from __future__ import annotations

import base64
import concurrent.futures
import hashlib
import json
import os
import unittest
from dataclasses import replace
from datetime import datetime
from typing import TYPE_CHECKING, Any
from unittest.mock import patch
from uuid import UUID, uuid4

from psycopg import errors

from memoriesql.application.agent_sql_catalog import SqlCatalog
from memoriesql.application.investigation_contracts import (
    QueryRequest,
    request_fingerprint,
)
from memoriesql.infrastructure.postgres.query_result_commit import (
    InternalResultCandidate,
    PostgresQueryResultCommit,
)
from memoriesql.infrastructure.postgres.relation_sql_population import (
    prepare_relation_population,
)
from memoriesql.infrastructure.postgres.result_preparation import PreparedContent

if TYPE_CHECKING:
    from tests.runtime import test_restricted_query as fixtures
    from tests.runtime.test_postgres_runtime import migrate
else:
    import test_restricted_query as fixtures
    from test_postgres_runtime import migrate


@unittest.skipUnless(
    os.environ.get("N1_TEST_DATABASE_URL"), "disposable PostgreSQL required"
)
class QueryResultCommit(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = fixtures.RestrictedQuery()
        self.runtime.setUp()
        self.addCleanup(self.runtime.doCleanups)
        self.fixture = self.runtime.fixture
        self.db, self.store, self.executor = (
            self.runtime.db,
            self.runtime.store,
            self.runtime.executor,
        )
        migrate(self.db, expected_current_version=33, target_version=34)

    def scalar(self, statement: str, values: tuple[Any, ...] = ()) -> Any:
        return self.runtime.scalar(statement, values)

    def adapter(self, db: Any) -> PostgresQueryResultCommit:
        return PostgresQueryResultCommit(
            db,
            credential_sha256=self.fixture.secret_hash,
            workspace_id=self.fixture.workspace,
        )

    def request(
        self, sql: str, *, parameters: list[dict[str, Any]] | None = None
    ) -> QueryRequest:
        return QueryRequest.model_validate(
            {
                "contract_version": 1,
                "run_ref": str(uuid4()),
                "step_key": str(uuid4()),
                "kind": "query",
                "catalog_hash": SqlCatalog.installed().hash,
                "sql": sql,
                "parameters": parameters or [],
                "inputs": [],
                "parents": [],
                "scope": {
                    "source_refs": [],
                    "known_at": self.scalar("SELECT clock_timestamp()")
                    .isoformat()
                    .replace("+00:00", "Z"),
                    "view": "historical",
                },
                "intent": "enumerate",
                "max_result_bytes": 64 * 1024 * 1024,
                "page_size": 2,
            }
        )

    def reserve_request(self, request: QueryRequest) -> Any:
        return self.store.reserve(
            run_ref=UUID(request.run_ref),
            step_key=UUID(request.step_key),
            request_fingerprint=request_fingerprint(request),
            reservation_bytes=request.max_result_bytes,
        )

    def population(self, request: QueryRequest) -> Any:
        return prepare_relation_population(
            self.db,
            credential_sha256=self.fixture.secret_hash,
            workspace_id=self.fixture.workspace,
            known_at=datetime.fromisoformat(request.scope.known_at),
            byte_budget=64 * 1024 * 1024,
        )

    def execute_request(
        self,
        request: QueryRequest,
        owner: Any,
        population: Any,
        *,
        witnesses: bool = True,
    ) -> Any:
        return self.executor.execute(
            self.db,
            population,
            owner,
            request.sql,
            tuple(p.sql_parameter(SqlCatalog.installed()) for p in request.parameters),
            collect_bag_witnesses=witnesses,
        )

    def candidate(
        self, request: QueryRequest, population: Any, execution: Any
    ) -> InternalResultCandidate:
        return InternalResultCandidate.construct(
            request, population, execution, policy_hash="5" * 64
        )

    def test_duplicate_join_occurrences_and_union_branches_have_native_witnesses(
        self,
    ) -> None:
        _, _, relation = self.fixture.assertion()
        request = self.request("""WITH pins AS (SELECT r.relation_id,s.statement_id FROM memory_v1.assessed_relations r
            JOIN memory_v1.relation_statements s ON r.relation_id=s.relation_id WHERE r.support_eligible)
            SELECT relation_id,statement_id FROM pins UNION ALL SELECT relation_id,statement_id FROM pins
            ORDER BY relation_id,statement_id""")
        owner = self.reserve_request(request)
        plain_owner = self.reserve_request(replace_request_step(request))
        with self.population(request) as population:
            plain = self.execute_request(
                request, plain_owner, population, witnesses=False
            )
            execution = self.execute_request(request, owner, population)
            self.assertEqual(execution.outcome, "complete", execution)
            self.assertEqual(execution.rows, plain.rows)
            self.assertEqual(len(execution.rows), 4)
            self.assertTrue(all(row[0] == relation for row in execution.rows))
            graph = json.loads(execution.witnesses.bytes)
            self.assertEqual(len(set(graph["row_provenance"])), 4)
            self.assertEqual(
                {n["operation"] for n in graph["nodes"]},
                {"scan", "join", "project", "filter", "set"},
            )
            candidate = self.candidate(request, population, execution)
            with self.fixture.connection() as control:
                receipt = self.adapter(control).commit(owner, candidate)
                self.assertEqual(receipt["state"], "committed")
                replay = self.adapter(control).commit(owner, candidate)
                self.assertTrue(replay["replayed"])
                self.assertEqual(receipt["result_id"], replay["result_id"])
            self.assertEqual(
                candidate.content_digest,
                hashlib.sha256(candidate.content.content).hexdigest(),
            )
        self.assertEqual(
            self.scalar("SELECT count(*) FROM memoriesql.query_result_creations"), 1
        )
        self.assertEqual(
            self.scalar("SELECT count(*) FROM memoriesql.result_preparation_artifacts"),
            1,
        )
        self.assertEqual(
            self.scalar("SELECT count(*) FROM memoriesql_query.population_rows"), 0
        )

    def test_left_right_full_nonmatches_bind_both_searched_populations(self) -> None:
        self.fixture.assertion()
        for side in ("LEFT", "RIGHT", "FULL"):
            request = self.request(
                f"""WITH missing AS (SELECT relation_id,statement_id FROM memory_v1.relation_statements WHERE role=$1)
                SELECT r.relation_id,m.statement_id FROM memory_v1.assessed_relations r
                {side} JOIN missing m ON r.relation_id=m.relation_id""",
                parameters=[{"position": 1, "type": "text", "value": "never-a-role"}],
            )
            owner = self.reserve_request(request)
            with self.population(request) as population:
                execution = self.execute_request(request, owner, population)
                self.assertEqual(execution.outcome, "complete", execution)
                graph = json.loads(execution.witnesses.bytes)
                self.assertEqual(
                    set(graph["searched_relations"]),
                    {"memory_v1.assessed_relations", "memory_v1.relation_statements"},
                )
                self.assertGreater(len(graph["members"]), 0)
                if side != "RIGHT":
                    self.assertEqual(execution.rows[0][1], None)
                    self.assertTrue(any(n["absent_inputs"] for n in graph["nodes"]))
                else:
                    self.assertEqual(execution.rows, ())
                    self.assertEqual(graph["row_provenance"], [])

    def test_empty_filter_is_complete_with_population_and_predicate(self) -> None:
        self.fixture.assertion()
        request = self.request(
            "SELECT relation_id FROM memory_v1.assessed_relations WHERE rationale=$1",
            parameters=[
                {"position": 1, "type": "text", "value": "fictional-impossible-value"}
            ],
        )
        owner = self.reserve_request(request)
        with self.population(request) as population:
            execution = self.execute_request(request, owner, population)
            self.assertEqual(execution.outcome, "complete", execution)
            self.assertEqual(execution.rows, ())
            graph = json.loads(execution.witnesses.bytes)
            self.assertTrue(graph["members"])
            self.assertTrue(
                any(
                    s["operation"] == "filter" and s["predicate"]
                    for s in graph["stages"]
                )
            )
            candidate = self.candidate(request, population, execution)
            with self.fixture.connection() as control:
                self.adapter(control).commit(owner, candidate)
            deps = json.loads(candidate.content.dependencies)
            self.assertEqual(
                base64.b64decode(deps["records_base64"]), population.dependency_records
            )
            self.assertEqual(
                base64.b64decode(deps["manifest_base64"]),
                population.dependency_manifest,
            )

    def test_crash_after_body_seal_has_no_identity_receipt_or_partial_body(
        self,
    ) -> None:
        self.fixture.assertion()
        request = self.request("SELECT relation_id FROM memory_v1.assessed_relations")
        owner = self.reserve_request(request)
        self.db.execute("""CREATE FUNCTION public.fictional_commit_failure() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION 'fictional crash after body'; END $$;
            CREATE TRIGGER fictional_commit_failure BEFORE INSERT ON memoriesql.query_result_creations
            FOR EACH ROW EXECUTE FUNCTION public.fictional_commit_failure()""")
        with self.population(request) as population:
            execution = self.execute_request(request, owner, population)
            candidate = self.candidate(request, population, execution)
            with self.fixture.connection() as control:
                with self.assertRaises(errors.RaiseException):
                    self.adapter(control).commit(owner, candidate)
                self.assertEqual(
                    control.execute(
                        "SELECT count(*) FROM memoriesql.query_result_creations"
                    ).fetchone(),
                    (0,),
                )
                self.assertEqual(
                    control.execute(
                        "SELECT count(*) FROM memoriesql.result_preparation_artifacts"
                    ).fetchone(),
                    (0,),
                )
                self.assertEqual(
                    control.execute(
                        "SELECT state FROM memoriesql.result_preparation_operations"
                    ).fetchone(),
                    ("reserved",),
                )
                control.execute(
                    "DROP TRIGGER fictional_commit_failure ON memoriesql.query_result_creations"
                )
                self.assertEqual(
                    self.adapter(control).commit(owner, candidate)["state"], "committed"
                )

    def test_restart_lost_response_and_concurrent_replay_never_repeat_select(
        self,
    ) -> None:
        self.fixture.assertion()
        request = self.request("SELECT relation_id FROM memory_v1.assessed_relations")
        owner = self.reserve_request(request)
        with self.population(request) as population:
            execution = self.execute_request(request, owner, population)
            candidate = self.candidate(request, population, execution)
            with self.fixture.connection() as control:
                self.adapter(control).commit(
                    owner, candidate
                )  # reply deliberately lost
        with self.fixture.connection() as restarted:
            recovered = self.adapter(restarted).recover(owner)
            self.assertEqual(recovered["result_id"], str(candidate.result_id))
            self.assertEqual(recovered["content_digest"], candidate.content_digest)
            self.assertNotIn("rows", recovered)
        replayed_owner = self.reserve_request(request)
        with (
            self.population(request) as population,
            patch.object(
                self.executor,
                "_reader_factory",
                side_effect=AssertionError("repeat SELECT forbidden"),
            ),
        ):
            self.assertEqual(
                self.execute_request(request, replayed_owner, population).outcome,
                "settlement_pending",
            )

        def replay() -> str:
            with self.fixture.connection() as control:
                try:
                    return str(
                        self.adapter(control).commit(owner, candidate)["result_id"]
                    )
                except errors.SerializationFailure:
                    # The exact command can recover in a fresh snapshot; never
                    # take over the owner or retry SELECT after serialization.
                    return str(self.adapter(control).recover(owner)["result_id"])

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: replay(), range(2)))
        self.assertEqual(results, [str(candidate.result_id)] * 2)
        self.assertEqual(
            self.scalar("SELECT count(*) FROM memoriesql_query.invocations"), 1
        )
        self.assertEqual(
            self.scalar("SELECT count(*) FROM memoriesql.query_result_creations"), 1
        )

    def test_closed_source_epoch_refuses_commit_and_does_not_publish(self) -> None:
        self.fixture.assertion()
        request = self.request("SELECT relation_id FROM memory_v1.assessed_relations")
        owner = self.reserve_request(request)
        with self.population(request) as population:
            execution = self.execute_request(request, owner, population)
            candidate = self.candidate(request, population, execution)
        with self.fixture.connection() as control:
            with self.assertRaises(errors.InsufficientPrivilege):
                self.adapter(control).commit(owner, candidate)
            self.assertEqual(
                self.adapter(control).recover(owner), {"state": "settlement_pending"}
            )
        self.assertEqual(
            self.scalar("SELECT count(*) FROM memoriesql.result_preparation_artifacts"),
            0,
        )

    def test_pending_witness_shapes_have_no_fake_or_empty_witness_fallback(
        self,
    ) -> None:
        self.fixture.assertion()
        request = self.request("SELECT count(*) AS n FROM memory_v1.assessed_relations")
        owner = self.reserve_request(request)
        with (
            self.population(request) as population,
            patch.object(
                self.executor,
                "_reader_factory",
                side_effect=AssertionError("unqualified witness dispatch forbidden"),
            ),
        ):
            execution = self.execute_request(request, owner, population)
            self.assertEqual(execution.outcome, "unsupported_query")
            self.assertEqual(execution.error, "witness_qualification_pending")
            self.assertEqual(execution.rows, ())
            self.assertIsNone(execution.witnesses)
        self.assertEqual(
            self.scalar("SELECT count(*) FROM memoriesql_query.invocations"), 0
        )

    def test_native_composition_matrix_preserves_values_types_order_and_limits(
        self,
    ) -> None:
        self.fixture.assertion()
        queries = [
            'SELECT p.relation_id,p."why%" FROM (SELECT relation_id,rationale AS "why%" FROM memory_v1.assessed_relations) p ORDER BY p.relation_id',
            "WITH pins(id,explanation) AS (SELECT relation_id,rationale FROM memory_v1.assessed_relations) SELECT id,explanation FROM pins ORDER BY id",
            "SELECT a.statement_id AS first,b.statement_id AS second FROM memory_v1.relation_statements a JOIN memory_v1.assessed_relations r ON a.relation_id=r.relation_id JOIN memory_v1.relation_statements b ON b.relation_id=r.relation_id ORDER BY a.statement_id,b.statement_id",
            "SELECT relation_id FROM memory_v1.assessed_relations UNION ALL (SELECT relation_id FROM memory_v1.assessed_relations LIMIT 1)",
            "SELECT relation_id FROM memory_v1.assessed_relations ORDER BY relation_id LIMIT 0",
            "SELECT relation_id FROM memory_v1.assessed_relations ORDER BY relation_id LIMIT 1",
            'SELECT relation_id AS "_mq_native_witness" FROM memory_v1.assessed_relations ORDER BY "_mq_native_witness"',
            "SELECT type_key,type_revision FROM memory_v1.relation_types ORDER BY type_key,type_revision",
        ]
        for query in queries:
            with self.subTest(sql=query):
                request = self.request(query)
                owner = self.reserve_request(request)
                plain_owner = self.reserve_request(replace_request_step(request))
                with self.population(request) as population:
                    plain = self.execute_request(
                        request, plain_owner, population, witnesses=False
                    )
                    execution = self.execute_request(request, owner, population)
                    self.assertEqual(plain.outcome, "complete", plain)
                    self.assertEqual(execution.outcome, "complete", execution)
                    self.assertEqual(execution.rows, plain.rows)
                    self.assertEqual(execution.columns, plain.columns)
                    self.assertEqual(
                        len(execution.witnesses.row_provenance), len(execution.rows)
                    )
                for slot in (owner, plain_owner):
                    self.store.discard(
                        operation_ref=slot.operation_ref,
                        ownership_ref=slot.ownership_ref,
                    )

    def test_commit_rejects_changed_frame_fingerprint_and_manifest_atomically(
        self,
    ) -> None:
        self.fixture.assertion()
        request = self.request("SELECT relation_id FROM memory_v1.assessed_relations")
        owner = self.reserve_request(request)
        with self.population(request) as population:
            candidate = self.candidate(
                request, population, self.execute_request(request, owner, population)
            )
            with self.fixture.connection() as control:
                with self.assertRaises(errors.UniqueViolation):
                    self.adapter(control).commit(
                        owner, replace(candidate, fingerprint="b" * 64)
                    )
                for part in ("frame", "manifest"):
                    body = json.loads(candidate.content.content)
                    dependencies = json.loads(candidate.content.dependencies)
                    if part == "frame":
                        body["frame"]["frame_ref"] = str(uuid4())
                    else:
                        dependencies["manifest_base64"] = base64.b64encode(
                            b"[]"
                        ).decode("ascii")
                    content = PreparedContent.encode(
                        content=body,
                        witnesses=json.loads(candidate.content.witnesses),
                        dependencies=dependencies,
                    )
                    altered = replace(
                        candidate,
                        content=content,
                        content_digest=hashlib.sha256(content.content).hexdigest(),
                    )
                    with self.assertRaises(errors.InvalidParameterValue):
                        self.adapter(control).commit(owner, altered)
                    self.assertEqual(
                        control.execute(
                            "SELECT count(*) FROM memoriesql.result_preparation_artifacts"
                        ).fetchone(),
                        (0,),
                    )
                self.adapter(control).commit(owner, candidate)

    def test_original_database_deadline_cannot_be_reset_for_publication(self) -> None:
        self.fixture.assertion()
        request = self.request("SELECT relation_id FROM memory_v1.assessed_relations")
        owner = self.reserve_request(request)
        with self.population(request) as population:
            candidate = self.candidate(
                request, population, self.execute_request(request, owner, population)
            )
            with self.fixture.connection() as control:
                # Deterministic deadline injection; no sleep or larger allowance.
                control.execute(
                    "UPDATE memoriesql_query.invocations SET deadline=clock_timestamp() WHERE invocation_ref=%s",
                    (candidate.invocation_ref,),
                )
                with self.assertRaises(errors.ProgramLimitExceeded):
                    self.adapter(control).commit(owner, candidate)
                self.assertEqual(
                    control.execute(
                        "SELECT count(*) FROM memoriesql.query_result_creations"
                    ).fetchone(),
                    (0,),
                )
                self.assertEqual(
                    control.execute(
                        "SELECT reserved_ms FROM memoriesql_query.invocations"
                    ).fetchone(),
                    (30000,),
                )

    def test_private_creations_have_no_reader_grant_and_are_immutable(self) -> None:
        self.fixture.assertion()
        request = self.request("SELECT relation_id FROM memory_v1.assessed_relations")
        owner = self.reserve_request(request)
        with self.population(request) as population:
            candidate = self.candidate(
                request, population, self.execute_request(request, owner, population)
            )
            with self.fixture.connection() as control:
                receipt = self.adapter(control).commit(owner, candidate)
                self.assertGreaterEqual(receipt["publication_ms"], 0)
        with self.assertRaises(errors.ObjectNotInPrerequisiteState):
            self.db.execute(
                "UPDATE memoriesql.query_result_creations SET expires_at=expires_at WHERE result_id=%s",
                (candidate.result_id,),
            )
        with self.runtime.reader_connection() as reader:
            for query in (
                "SELECT * FROM memoriesql.query_result_creations",
                "SELECT memoriesql.recover_query_result_v1(%s,%s)",
            ):
                with self.assertRaises(errors.InsufficientPrivilege):
                    reader.execute(
                        query,
                        (owner.operation_ref, owner.ownership_ref)
                        if "%s" in query
                        else None,
                    )


def replace_request_step(request: QueryRequest) -> QueryRequest:
    return request.model_copy(update={"step_key": str(uuid4())})
