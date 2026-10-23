"""A derivation lineage over the root budget never fails a whole population.

Execute only in an assigned DB slot. Fictional data, no model or provider.
One accepted assessed relation cites a bead whose derived_from lineage holds
129 beads, one more than qualified_bead_roots_v2 admits (0039: root_budget,
54000). Every query in the workspace must still answer, for the owner and for
a paired agent alike: that one relation family is withheld exactly as an
unreadable family is, and every other family is disclosed.
"""

from __future__ import annotations

import json
import os
import unittest
from typing import TYPE_CHECKING, Any

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
        # A readable control family in the same scope.
        _, _, self.control = fixture.assertion(scope=scope, source_object=source)
        with self.r.in_scope(scope):
            chain = [
                fixture.bead(
                    "Fictional lineage step 0.", key="lineage-0", source=source
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
                        source=source,
                    )
                )
            target = fixture.bead(
                "Fictional deep target.", key="deep-target", source=source
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
        fixture.activate_relations(head, (target,))
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
        self.grant, self.agent = self.r.pair_agent(scope, AGENT, label="budget")
        latest = max(m.version for m in discover_migrations())
        migrate(self.db, expected_current_version=43, target_version=latest)

    def ask(self, text: str, secret: str | None) -> dict[str, Any]:
        run = self.r.start(secret)
        _, reply = self.r.query(run, text, page_size=50, secret=secret)
        self.r.close(run, secret)
        return reply

    def test_a_lineage_over_the_root_budget_withholds_only_its_family(self) -> None:
        observed = {}
        for label, secret in (("owner", None), ("agent", self.agent)):
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
        print(json.dumps({"population_family_budget": observed}, sort_keys=True))
