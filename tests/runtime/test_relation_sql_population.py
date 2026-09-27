"""Installed canonical population fixtures, never unseen evaluation material."""

from __future__ import annotations

import hashlib
import json
import os
import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.errors import InsufficientPrivilege

from memoriesql.application.agent_sql_admission import admit_query
from memoriesql.application.agent_sql_catalog import SqlParameter
from memoriesql.infrastructure.postgres.relation_sql_population import (
    RELATIONS,
    RelationPopulationError,
    prepare_relation_population,
)

if TYPE_CHECKING:
    from tests.runtime import test_assessed_relation_lifecycle as fixtures
    from tests.runtime.test_postgres_runtime import migrate
else:
    import test_assessed_relation_lifecycle as fixtures
    from test_postgres_runtime import migrate


@unittest.skipUnless(
    os.environ.get("N1_TEST_DATABASE_URL"), "disposable PostgreSQL required"
)
class RelationSqlPopulation(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = fixtures.AssessedLifecycle()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.db = self.fixture.db
        migrate(self.db, expected_current_version=30, target_version=32)

    def prepare(self, **extra: Any) -> Any:
        return prepare_relation_population(
            extra.pop("connection", self.db),
            credential_sha256=extra.pop("credential_sha256", self.fixture.secret_hash),
            workspace_id=extra.pop("workspace_id", self.fixture.workspace),
            known_at=extra.pop("known_at", None),
            byte_budget=extra.pop("byte_budget", 64 * 1024 * 1024),
            **extra,
        )

    @staticmethod
    def dictionaries(population: Any, name: str) -> list[dict[str, Any]]:
        qualified = "memory_v1." + name
        names = [c.name for c in population.schemas[qualified].columns]
        return [
            dict(zip(names, row, strict=True)) for row in population.rows[qualified]
        ]

    def test_complete_closed_relations_pins_bindings_and_native_composition(
        self,
    ) -> None:
        source, target, relation = self.fixture.assertion()
        with self.prepare() as p:
            self.assertEqual(set(p.rows), {"memory_v1." + name for name in RELATIONS})
            assertion = self.dictionaries(p, "assessed_relations")[0]
            self.assertEqual(assertion["source_bead_id"], source)
            self.assertEqual(assertion["target_bead_id"], target)
            self.assertEqual(assertion["relation_id"], relation)
            self.assertIsInstance(assertion["author_confidence"], Decimal)
            self.assertTrue(assertion["support_eligible"])
            self.assertGreater(assertion["independent_root_count"], 0)
            records = json.loads(p.dependency_records)
            self.assertTrue(
                {
                    "assertion_projection",
                    "relation_head",
                    "source_object",
                    "specialist_judgment",
                    "pair_task_population",
                }
                <= {r["kind"] for r in records}
            )
            self.assertEqual(
                hashlib.sha256(p.dependency_manifest).hexdigest(),
                p.dependency_manifest_sha256,
            )
            for item in self.dictionaries(p, "relation_evidence"):
                binding = p.evidence_bindings[item["evidence_ref"]]
                self.assertEqual(binding.statement_id, item["statement_id"])
                self.assertEqual(binding.source_unit_id, item["source_unit_id"])
                self.assertEqual(binding.content_sha256, item["content_sha256"])
            with self.assertRaises(TypeError):
                p.rows["new"] = ()
            # A disposable privileged test connection loads the prepared native
            # rows for differential composition only. This is NOT the deployed
            # restricted executor or a provenance-construction proof.
            with self.fixture.connection() as native:
                native.execute("CREATE SCHEMA memory_v1")
                for name, schema in p.schemas.items():
                    fields = [
                        sql.SQL("{} {}{}").format(
                            sql.Identifier(c.name),
                            sql.SQL(c.type.pg_type),
                            sql.SQL(' COLLATE "C"' if c.type.pg_type == "text" else ""),
                        )
                        for c in schema.columns
                    ]
                    native.execute(
                        sql.SQL("CREATE TABLE {} ({})").format(
                            sql.Identifier(*name.split(".")), sql.SQL(",").join(fields)
                        )
                    )
                    if p.rows[name]:
                        with native.cursor() as cursor:
                            cursor.executemany(
                                sql.SQL("INSERT INTO {} VALUES ({})").format(
                                    sql.Identifier(*name.split(".")),
                                    sql.SQL(",").join(
                                        sql.Placeholder() for _ in fields
                                    ),
                                ),
                                p.rows[name],
                            )
                text = """WITH pins AS (
                  SELECT r.relation_id, s.statement_id FROM memory_v1.assessed_relations r
                  JOIN memory_v1.relation_statements s ON r.relation_id=s.relation_id
                  WHERE r.support_eligible AND r.roots_status=$1),
                unioned AS (SELECT relation_id, statement_id FROM pins
                  UNION ALL SELECT relation_id, statement_id FROM pins)
                SELECT relation_id, count(*) AS multiplicity,
                  sum(count(*)) OVER (ORDER BY relation_id ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS running
                FROM unioned GROUP BY relation_id ORDER BY relation_id"""
                query = admit_query(text, (SqlParameter(1, "text", "qualified"),))
                result = native.execute(query.sql, query.parameters).fetchall()
                pins = self.dictionaries(p, "relation_statements")
                self.assertEqual(
                    result, [(relation, len(pins) * 2, Decimal(len(pins) * 2))]
                )

    def test_current_asof_and_restart_keep_original_state_and_manifest(self) -> None:
        source, _, relation = self.fixture.assertion(key="derived_from")
        with self.prepare() as old:
            historical = old.known_at
            before = self.dictionaries(old, "assessed_relations")[0]
            manifest = old.dependency_manifest
        self.fixture.governance.record_event(
            self.fixture.lifecycle_command(source, relation, "dispute", "sql-dispute")
        )
        with self.prepare() as current:
            row = self.dictionaries(current, "assessed_relations")[0]
            self.assertEqual(
                (row["state"], row["roots_status"], row["independent_root_count"]),
                ("disputed", "indeterminate", None),
            )
        with (
            self.fixture.connection() as restarted,
            self.prepare(connection=restarted, known_at=historical) as replay,
        ):
            self.assertEqual(self.dictionaries(replay, "assessed_relations")[0], before)
            self.assertEqual(replay.dependency_manifest, manifest)
        self.assertEqual(before["state"], "active")

    def test_event_evidence_has_original_statement_pin_and_separate_support(
        self,
    ) -> None:
        source, _, relation = self.fixture.assertion()
        other = self.fixture.bead(
            "Fictional separate human event evidence.", key="event-evidence"
        )
        statement, unit, content_hash = self.fixture.row(
            "SELECT s.statement_id,e.evidence_source_unit_id,e.evidence_content_hash FROM memoriesql.bead_semantic_statements s JOIN memoriesql.bead_semantic_statement_evidence e USING(tenant_id,statement_id) WHERE s.bead_id=%s",
            (other,),
        )
        command = self.fixture.lifecycle_command(
            source,
            relation,
            "confirm",
            "sql-confirm",
            evidence=[
                dict(
                    statement_id=statement,
                    source_unit_id=unit,
                    content_hash=content_hash,
                )
            ],
        )
        receipt = self.fixture.governance.record_event(command)
        with self.prepare() as p:
            events = self.dictionaries(p, "relation_events")
            self.assertEqual(events[0]["event_id"], receipt.relation_event_id)
            self.assertEqual(
                events[0]["recorded_by_principal_id"], self.fixture.principal
            )
            evidence = self.dictionaries(p, "relation_event_evidence")[0]
            self.assertEqual(evidence["bead_id"], other)
            self.assertEqual(
                evidence["statement_text"], "Fictional separate human event evidence."
            )
            self.assertEqual(evidence["statement_id"], statement)
            self.assertNotIn(
                statement,
                {r["statement_id"] for r in self.dictionaries(p, "relation_evidence")},
            )

    def test_corrections_and_replacement_keep_accepted_endpoint_text(self) -> None:
        f = self.fixture
        source, target, relation = f.assertion()
        successor = f.correction(source)
        f.activate_relations(source, (target,), key="sql-replacement")
        f.propose(
            lambda packet: [
                f.proposal(
                    packet,
                    "supports",
                    f.endpoint(packet, source, 0),
                    f.endpoint(packet, target, 0),
                    basis="agent_inferred",
                    retires=dict(
                        relation_kind="assessed",
                        relation_id=str(relation),
                        reason="Fictional explicit replacement.",
                    ),
                )
            ]
        )
        f.run_relations()
        with self.prepare() as p:
            old = next(
                r
                for r in self.dictionaries(p, "assessed_relations")
                if r["relation_id"] == relation
            )
            self.assertEqual(old["state"], "superseded")
            self.assertTrue(old["correction_pending"])
            self.assertFalse(old["support_eligible"])
            corrected = self.dictionaries(p, "relation_corrections")
            self.assertIn(successor, {r["correcting_bead_id"] for r in corrected})
            pins = self.dictionaries(p, "relation_statements")
            self.assertIn(
                "Fictional source: observations agree.",
                {r["text"] for r in pins if r["relation_id"] == relation},
            )
            self.assertEqual(len(self.dictionaries(p, "relation_replacements")), 1)
            self.assertEqual(len(self.dictionaries(p, "relation_events")), 1)

    def test_unaccepted_proposals_remain_absent_but_pair_coverage_is_retained(
        self,
    ) -> None:
        self.fixture.assertion(accepted=False)
        with self.prepare() as p:
            self.assertEqual(p.rows["memory_v1.assessed_relations"], ())
            self.assertEqual(p.rows["memory_v1.relation_types"], ())
            self.assertTrue(p.rows["memory_v1.relation_pairs"])
            self.assertIn(
                "specialist_judgment",
                {r["kind"] for r in json.loads(p.dependency_records)},
            )

    def test_unsupported_roots_never_become_a_zero_independent_count(self) -> None:
        self.fixture.assertion(key="derived_from", same=True)
        with self.prepare() as p:
            assertion = self.dictionaries(p, "assessed_relations")[0]
            self.assertEqual(assertion["roots_status"], "unsupported")
            self.assertIsNone(assertion["independent_root_count"])
            records = p.dependency_records.decode()
            self.assertIn("statement_roots_unsupported", records)

    def test_real_unprivileged_login_cannot_call_preparation_or_private_hooks(
        self,
    ) -> None:
        role = "pr05_population_" + uuid4().hex
        self.db.execute(
            sql.SQL(
                "CREATE ROLE {} LOGIN NOINHERIT PASSWORD 'fictional-population-reader'"
            ).format(sql.Identifier(role))
        )
        self.db.execute(
            sql.SQL("GRANT USAGE ON SCHEMA memoriesql TO {}").format(
                sql.Identifier(role)
            )
        )

        def cleanup() -> None:
            self.db.execute(
                sql.SQL("REVOKE USAGE ON SCHEMA memoriesql FROM {}").format(
                    sql.Identifier(role)
                )
            )
            self.db.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))

        self.addCleanup(cleanup)
        with psycopg.connect(
            make_conninfo(
                self.fixture.admin,
                dbname=self.fixture.database,
                user=role,
                password="fictional-population-reader",
            ),
            autocommit=True,
        ) as reader:
            self.assertEqual(
                reader.execute("SELECT session_user,current_user").fetchone(),
                (role, role),
            )
            for query in (
                "SELECT memoriesql.prepare_relation_sql_population_v1(NULL,67108864)",
                "SELECT memoriesql.check_relation_sql_population_authority_v1()",
                "SELECT memoriesql.relation_assertions_v1(NULL,statement_timestamp())",
                "SET ROLE memoriesql_application",
            ):
                with self.assertRaises(InsufficientPrivilege):
                    reader.execute(query)

    def test_revoked_credential_and_cross_tenant_never_disclose_old_rows(self) -> None:
        self.fixture.assertion()
        workspace, _, secret, _ = self.fixture.second_tenant()
        with self.prepare(credential_sha256=secret, workspace_id=workspace) as other:
            self.assertTrue(all(not rows for rows in other.rows.values()))
        self.db.execute(
            "UPDATE memoriesql.authentication_credentials SET status='revoked',revoked_at=clock_timestamp() WHERE principal_id=%s",
            (self.fixture.principal,),
        )
        with self.assertRaises(RelationPopulationError) as refused:
            with self.prepare():
                self.fail("revoked credential disclosed a population")
        self.assertEqual(refused.exception.code, "unavailable")

    def test_protected_source_withholds_the_whole_family_without_metadata(self) -> None:
        self.fixture.assertion()
        self.db.execute(
            "UPDATE memoriesql.protected_resources SET status='revoked',revoked_at=clock_timestamp() WHERE resource_kind='source'"
        )
        with self.prepare() as p:
            self.assertTrue(all(not rows for rows in p.rows.values()))
            self.assertEqual(p.dependency_records, b"[]")

    def test_budget_and_future_time_refuse_wholly_and_release_owned_fence(self) -> None:
        self.fixture.assertion()
        cases: list[tuple[dict[str, Any], str]] = [
            ({"byte_budget": 8192}, "budget_exhausted"),
            ({"known_at": datetime.now(UTC) + timedelta(days=1)}, "invalid_request"),
        ]
        for kwargs, expected in cases:
            with self.assertRaises(RelationPopulationError) as refused:
                with self.prepare(**kwargs):
                    self.fail("invalid or incomplete preparation was returned")
            self.assertEqual(refused.exception.code, expected)
            lock_row = self.db.execute(
                "SELECT count(*) FROM pg_locks WHERE pid=pg_backend_pid() AND locktype='advisory'"
            ).fetchone()
            assert lock_row is not None
            self.assertEqual(lock_row[0], 0)
        with self.assertRaises(InsufficientPrivilege), self.db.transaction():
            self.fixture.begin()
            self.db.execute(
                "SELECT memoriesql.relation_assertion_row_v1(%s,'assessed',%s,NULL,statement_timestamp())",
                (self.fixture.tenant, UUID(int=1)),
            )
        with self.prepare() as p:
            self.assertEqual(len(p.rows["memory_v1.assessed_relations"]), 1)
