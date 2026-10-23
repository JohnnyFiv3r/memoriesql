"""A derivation lineage over the root budget never fails a whole population.

Execute only in an assigned DB slot. Fictional data, no model or provider.
One accepted assessed relation cites a bead whose derived_from lineage holds
129 beads, one more than qualified_bead_roots_v2 admits (0039: root_budget,
54000). Every query in the workspace must still answer, for the owner and for
a paired agent alike: that one relation family is withheld exactly as an
unreadable family is, and every other family is disclosed. Then the deep
family is made unreadable as well, so it is withheld for privilege instead of
depth, and no relation row either reader receives may change by a byte.
"""

from __future__ import annotations

import json
import os
import unittest
from typing import TYPE_CHECKING, Any
from uuid import UUID

import psycopg

from memoriesql.infrastructure.postgres.migration_runner import discover_migrations

if TYPE_CHECKING:
    from tests.runtime import agent_relation_fixtures as relation_agents
    from tests.runtime import test_agent_sql_results as fixtures
    from tests.runtime.test_postgres_runtime import migrate
else:
    import agent_relation_fixtures as relation_agents
    import test_agent_sql_results as fixtures
    from test_postgres_runtime import migrate

AGENT = ["memory.inspect", "memory.query", "source.read"]
LINEAGE = 129
RELATIONS = "SELECT relation_id FROM memory_v1.assessed_relations ORDER BY relation_id"
OBSERVATIONS = "SELECT bead_id FROM memory_v1.observations ORDER BY bead_id"
# The first call of the population's family block, so the error the block
# withholds the deep family on.
ASSERTION_ROW = (
    "SELECT memoriesql.relation_assertion_row_v2("
    "%s,'assessed',%s,NULL,statement_timestamp(),'owner')"
)
REVOKE_SOURCE = (
    "UPDATE memoriesql.protected_resources "
    "SET status='revoked',revoked_at=clock_timestamp() WHERE resource_id=%s"
)


@unittest.skipUnless(
    os.environ.get("N1_TEST_DATABASE_URL"), "assigned disposable PostgreSQL required"
)
class PopulationFamilyBudget(unittest.TestCase):
    def setUp(self) -> None:
        self.r = fixtures.AgentSqlResults(methodName="runTest")
        self.addCleanup(self.r.doCleanups)
        self.r.setUp()
        self.db, fixture = self.r.db, self.r.fixture
        self.fixture = fixture
        scope, source = relation_agents.explicit_scope(self.db, fixture)
        # The lineage and its target, so the deep relation's whole closure, have
        # their own scope and source: revoking that source makes only the deep
        # family unreadable.
        deep_scope, self.deep_source = relation_agents.explicit_scope(self.db, fixture)
        # A readable control family.
        _, _, self.control = fixture.assertion(scope=scope, source_object=source)
        with self.r.in_scope(deep_scope):
            chain = [
                fixture.bead(
                    "Fictional lineage step 0.",
                    key="lineage-0",
                    source=self.deep_source,
                )
            ]
            for step in range(1, LINEAGE):
                chain.append(
                    fixture.author(
                        f"Fictional lineage step {step}.",
                        f"lineage-{step}",
                        candidates=(chain[-1],),
                        plan=lambda extras, bead, found: fixture.relate(
                            extras, bead, found[0], "derived_from"
                        ),
                        source=self.deep_source,
                    )
                )
            target = fixture.bead(
                "Fictional deep target.", key="deep-target", source=self.deep_source
            )
        head = chain[-1]
        self.assertEqual(
            self.r.h.scalar(
                "SELECT count(*) FROM memoriesql.bead_relations r "
                "JOIN memoriesql.relation_type_revisions tr "
                "ON tr.relation_type_revision_id=r.relation_type_revision_id "
                "WHERE tr.relation_type_id='30000000-0000-4000-8000-000000000009' "
                "AND r.source_bead_id=ANY(%s)",
                (chain,),
            ),
            LINEAGE - 1,
        )
        # An accepted assessed relation whose evidence is the lineage head's.
        # Its own activation key: assertion() already used the default one.
        fixture.activate_relations(head, (target,), key="orchard.assess.deep")
        fixture.propose(
            lambda packet: [
                fixture.proposal(
                    packet,
                    "supports",
                    fixture.endpoint(packet, head, 0),
                    fixture.endpoint(packet, target, 0),
                    basis="agent_inferred",
                )
            ]
        )
        fixture.run_relations()
        self.deep = self.r.h.scalar(
            "SELECT relation_id::text FROM memoriesql.assessed_relations "
            "WHERE relation_id<>%s",
            (self.control,),
        )
        self.assertIsNotNone(self.deep)
        self.grant, self.agent = self.r.pair_agent_over(
            [scope, deep_scope], AGENT, label="budget"
        )
        latest = max(m.version for m in discover_migrations())
        migrate(self.db, expected_current_version=43, target_version=latest)

    def ask(self, text: str, secret: str | None) -> dict[str, Any]:
        run = self.r.start(secret)
        _, reply = self.r.query(run, text, page_size=50, secret=secret)
        self.r.close(run, secret)
        return reply

    def relation_rows(self, secret: str | None) -> dict[str, str]:
        """Every relation table read whole, every column, as JSON. No reply
        names the deep relation."""
        run = self.r.start(secret)
        seen = self.r.every_relation_row(run, secret=secret)
        self.r.close(run, secret)
        for table, (_, reply) in seen.items():
            self.assertNotIn(self.deep, reply, table)
        return {table: json.dumps(rows) for table, (rows, _) in seen.items()}

    def withheld_on(self) -> list[str]:
        """The SQLSTATE and message the deep family's assertion row raises in the
        owner's context. The hook is private, so it runs as the test's own role."""
        try:
            with self.db.transaction():
                self.fixture.begin()
                self.db.execute("RESET ROLE")
                self.db.execute(ASSERTION_ROW, (self.fixture.tenant, UUID(self.deep)))
        except psycopg.Error as error:
            return [str(error.sqlstate), str(error.diag.message_primary)]
        return ["readable"]

    def test_a_lineage_over_the_root_budget_withholds_only_its_family(self) -> None:
        readers = (("owner", None), ("agent", self.agent))
        observed = {}
        for label, secret in readers:
            replies = {
                name: self.ask(text, secret)
                for name, text in (
                    ("observations", OBSERVATIONS),
                    ("relations", RELATIONS),
                )
            }
            observed[label] = {
                name: [reply["outcome"], reply.get("error")]
                for name, reply in replies.items()
            }
            for name, reply in replies.items():
                with self.subTest(reader=label, query=name):
                    self.assertEqual(reply["outcome"], "available", reply)
            if replies["relations"]["outcome"] == "available":
                ids = {row["values"][0] for row in replies["relations"]["page"]["rows"]}
                with self.subTest(reader=label, check="families"):
                    # Only the oversized family is withheld, as an unreadable one is.
                    self.assertIn(str(self.control), ids)
                    self.assertNotIn(self.deep, ids)
        # In the same world (the lineage alone takes minutes to author), the deep
        # family withheld for depth and then for privilege: its source revoked,
        # its closure fails authorization before any root is walked.
        deep = {label: self.relation_rows(secret) for label, secret in readers}
        reasons = [self.withheld_on()]
        self.db.execute(REVOKE_SOURCE, (self.deep_source,))
        reasons.append(self.withheld_on())
        unreadable = {label: self.relation_rows(secret) for label, secret in readers}
        identical = {}
        for label, tables in deep.items():
            identical[label] = sorted(
                table
                for table, rows in tables.items()
                if unreadable[label][table] == rows
            )
            for table, rows in tables.items():
                with self.subTest(reader=label, table=table):
                    self.assertEqual(unreadable[label][table], rows)
        with self.subTest(check="withheld_on"):
            self.assertEqual(reasons[0], ["54000", "root_budget"])
            self.assertEqual(reasons[1], ["42501", "assertion_unavailable"])
        print(
            json.dumps(
                {
                    "population_family_budget": observed,
                    "withheld_on": reasons,
                    "identical_relation_tables": identical,
                },
                sort_keys=True,
            )
        )
