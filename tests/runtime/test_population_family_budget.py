"""A derivation lineage over the root budget never fails a whole population.

Execute only in an assigned DB slot. Fictional data, no model or provider.
One accepted assessed relation cites a bead whose derived_from lineage holds
129 beads, one more than qualified_bead_roots_v2 admits (0039: root_budget,
54000). Every query in the workspace must still answer, for the owner and for
a paired agent alike: that one relation family is withheld exactly as an
unreadable family is, and every other family is disclosed. Then the deep
family is made unreadable as well, so it is withheld for privilege instead of
depth, and no relation row either reader receives may change by a byte (evidence
refs, minted afresh for every population, are compared by the rows they bind). A
second read of the deep state is the control for that comparison: two reads of
one state may differ only in evidence_ref.
"""

from __future__ import annotations

import json
import os
import unittest
from typing import TYPE_CHECKING, Any
from uuid import UUID

import psycopg

from memoriesql.infrastructure.postgres.migration_runner import discover_migrations
from memoriesql.infrastructure.postgres.relation_projection import (
    relation_projection_frame,
)

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
# The assertion row's first gate: the repeatable-read frame holding the read fence.
FRAME_AUTHORIZED = "SELECT memoriesql.relation_read_frame_authorized_v1(%s)"
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

    def relation_rows(self, secret: str | None) -> dict[str, dict[str, str]]:
        """Every relation table read whole, every column, as JSON three ways: as
        read ("raw"), without evidence_ref ("other"), and with each evidence_ref
        replaced by the order it first appears in ("bound"), which still compares
        which rows share one. Each population mints its evidence refs afresh
        (uuid4, relation_sql_population.py). No reply names the deep relation."""
        run = self.r.start(secret)
        seen = self.r.every_relation_row(run, secret=secret)
        self.r.close(run, secret)
        refs: dict[Any, str] = {}
        tables = {}
        for table, (rows, reply) in seen.items():
            self.assertNotIn(self.deep, reply, table)
            columns = self.r.ALL_RELATION_COLUMNS[table]
            at = columns.index("evidence_ref") if "evidence_ref" in columns else -1
            other = [[v for i, v in enumerate(row) if i != at] for row in rows]
            bound = [
                [
                    refs.setdefault(v, f"ref-{len(refs)}")
                    if i == at and v is not None
                    else v
                    for i, v in enumerate(row)
                ]
                for row in rows
            ]
            tables[table] = {
                "raw": json.dumps(rows),
                "other": json.dumps(other),
                "bound": json.dumps(bound),
            }
        return tables

    def withheld_on(self) -> tuple[bool, list[str]]:
        """In the owner's canonical relation frame, where the population calls
        it: whether the frame passes the assertion row's frame gate, then the
        SQLSTATE and message that row raises for the deep family. The hooks are
        private, so the calls leave the frame's application role."""
        framed = False
        with relation_projection_frame(
            self.db,
            credential_sha256=self.fixture.secret_hash,
            workspace_id=self.fixture.workspace,
            statement_timeout_ms=30000,
        ) as frame:
            try:
                with frame.transaction(force_rollback=True):
                    frame.execute("SET LOCAL ROLE NONE")
                    row = frame.execute(
                        FRAME_AUTHORIZED, (self.fixture.tenant,)
                    ).fetchone()
                    framed = row is not None and row[0] is True
                    frame.execute(ASSERTION_ROW, (self.fixture.tenant, UUID(self.deep)))
            except psycopg.Error as error:
                return framed, [str(error.sqlstate), str(error.diag.message_primary)]
        return framed, ["readable"]

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
        # The control: the same state read again. Two reads may differ only in
        # evidence_ref, and not at all once refs are compared by what they bind.
        again = {label: self.relation_rows(secret) for label, secret in readers}
        checks = [self.withheld_on()]
        self.db.execute(REVOKE_SOURCE, (self.deep_source,))
        checks.append(self.withheld_on())
        reasons = [reason for _, reason in checks]
        unreadable = {label: self.relation_rows(secret) for label, secret in readers}
        raw_equal = {}
        for label, tables in deep.items():
            raw_equal[label] = {
                table: [
                    again[label][table]["raw"] == forms["raw"],
                    unreadable[label][table]["raw"] == forms["raw"],
                ]
                for table, forms in tables.items()
            }
            for table, forms in tables.items():
                for read, later in (("same state", again), ("revoked", unreadable)):
                    with self.subTest(reader=label, table=table, read=read):
                        self.assertEqual(later[label][table]["other"], forms["other"])
                        self.assertEqual(later[label][table]["bound"], forms["bound"])
        with self.subTest(check="withheld_on"):
            # Both calls ran inside the population's own frame, so each refusal
            # comes from past the frame gate.
            self.assertEqual([framed for framed, _ in checks], [True, True])
            self.assertEqual(reasons[0], ["54000", "root_budget"])
            self.assertEqual(reasons[1], ["42501", "assertion_unavailable"])
        print(
            json.dumps(
                {
                    "population_family_budget": observed,
                    "withheld_on": reasons,
                    "frame_authorized": [framed for framed, _ in checks],
                    # Per table: raw bytes equal to the first read, for the
                    # same-state read and for the revoked read.
                    "raw_equal": raw_equal,
                    "relation_evidence_raw": {
                        label: [
                            reads[label]["relation_evidence"]["raw"]
                            for reads in (deep, again, unreadable)
                        ]
                        for label in deep
                    },
                },
                sort_keys=True,
            )
        )
