"""Agent source text view (read_source_text); execute only in an assigned DB slot.

Fictional data, no model or provider. A paired agent holding only
memory.inspect, memory.query and source.read reads exactly the unit text the
query exception serves, for refs delivered to it in the same run.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import unittest
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from typing import TYPE_CHECKING, Any
from unittest.mock import patch
from uuid import UUID, uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

import memoriesql.infrastructure.postgres.agent_sql_results as results_module
from memoriesql.application.agent_sql_results import remaining_after
from memoriesql.application.evidence_packages import SourceQualification
from memoriesql.infrastructure.postgres.agent_sql_results import PostgresAgentSqlResults
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
SOURCES = (
    "SELECT ss.evidence_ref,u.source_unit_id,u.content_sha256,u.text_state,"
    "u.search_text FROM memory_v1.statement_sources ss "
    "JOIN memory_v1.source_units u ON u.source_unit_id=ss.source_unit_id "
    "ORDER BY ss.evidence_ref"
)
PROTECTED_KEYS = (
    "evidence_pin",
    "package",
    "part",
    "lineage",
    "native",
    "qualification",
    "bytes_base64",
    "required_characters",
    "byte_start",
    "inventory_sha256",
)
# A relation, its TOAST table and every index of either (the facts suite's own).
RELATIONS = (
    "SELECT array_agg(rel) FROM ("
    "SELECT %s::regclass::oid rel "
    "UNION SELECT reltoastrelid FROM pg_class WHERE oid=%s::regclass "
    "UNION SELECT indexrelid FROM pg_index WHERE indrelid IN ("
    "%s::regclass,(SELECT reltoastrelid FROM pg_class WHERE oid=%s::regclass))"
    ") r WHERE rel<>0"
)
PARTS = "memoriesql.evidence_package_parts"
# Query-path audit comes only from the owner-mode relation closure (a paired
# agent's checks write none), so these cases read as the owner over a
# workspace that holds an assessed relation.
AUDITED = {
    "test_source_text_audit_is_attributed_both_ways_and_charged_to_its_allowance",
    "test_entries_sharing_a_delivering_result_check_its_closure_once",
}


def filler(key: str, size: int) -> str:
    """Exactly `size` bytes of fictional ASCII raw capture."""
    line = "Fictional RAW orchard capture " + key + ". "
    return (line * (size // len(line) + 1))[:size]


@unittest.skipUnless(
    os.environ.get("N1_TEST_DATABASE_URL"), "assigned disposable PostgreSQL required"
)
class AgentSourceTextRuntime(unittest.TestCase):
    TEXT = "Fictional normalized harvest: α pears in the orchard. " * 32
    RAW = "Fictional RAW harvest [alpha pears]; original captured bytes. " * 32
    WIDE = (
        "Fictional astral pears: "
        + "🍐" * 4100
        + " Fictional accented pears: "
        + "é" * 8200
        + " Fictional CJK pears: "
        + "中" * 5462
        + " End of the fictional wide harvest."
    )
    WIDE_RAW = "Fictional RAW wide harvest; original captured bytes. " * 320

    def setUp(self) -> None:
        self.r = fixtures.AgentSqlResults(methodName="runTest")
        self.addCleanup(self.r.doCleanups)
        self.r.setUp()
        self.db, self.fixture = self.r.db, self.r.fixture
        self.scope, scope_source = relation_agents.explicit_scope(self.db, self.fixture)
        self.normalized = self.r.normalized_bead(
            self.TEXT,
            "text-normalized",
            raw=self.RAW,
            source=scope_source,
            scope=self.scope,
        )
        self.wide = self.r.normalized_bead(
            "Fictional wide harvest of astral and accented pears.",
            "text-wide",
            raw=self.WIDE_RAW,
            source=scope_source,
            scope=self.scope,
            retained=self.WIDE,
        )
        with self.r.in_scope(self.scope):
            # Default fixture parts are identity (raw) packages: no agent text.
            self.identity = self.fixture.bead(
                "Fictional identity-captured pears.",
                key="text-identity",
                source=scope_source,
            )
        # An owner-private bead no paired agent can see.
        self.private = self.fixture.bead(
            "Fictional private orchard note.", key="text-private"
        )
        self.scope_source = scope_source
        if self._testMethodName in AUDITED:
            self.fixture.assertion()
        self.grant, self.agent = self.r.pair_agent(self.scope, AGENT, label="text")
        # The view's number is the next free one at landing: ask the installed
        # stream rather than hard-coding it.
        self.view_version = next(
            migration.version
            for migration in discover_migrations()
            if migration.name == "agent_source_text_view"
        )
        migrate(self.db, expected_current_version=43, target_version=self.view_version)

    # -- helpers ---------------------------------------------------------------

    def scalar(self, statement: str, values: tuple[Any, ...] = ()) -> Any:
        return self.r.h.scalar(statement, values)

    def unit_of(self, bead: UUID) -> str:
        unit = self.scalar(
            "SELECT e.evidence_source_unit_id::text FROM memoriesql.bead_semantic_statements s "
            "JOIN memoriesql.bead_semantic_statement_evidence e "
            "ON e.tenant_id=s.tenant_id AND e.statement_id=s.statement_id "
            "WHERE s.bead_id=%s ORDER BY e.evidence_source_unit_id LIMIT 1",
            (bead,),
        )
        self.assertIsNotNone(unit)
        return str(unit)

    def cited(
        self,
        run: dict[str, Any],
        *,
        secret: str | None = None,
        view: str = "historical",
    ) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
        """Deliver source refs the only way an agent can: a statement_sources query."""
        _, reply = self.r.query(
            run, SOURCES, page_size=50, secret=secret or self.agent, view=view
        )
        self.assertEqual(reply["outcome"], "available", reply)
        self.assertFalse(reply["page"]["has_more"], reply)
        units: dict[str, dict[str, Any]] = {}
        for row in reply["page"]["rows"]:
            evidence, unit, digest, state, text = row["values"]
            units[unit] = {
                "source": {
                    "kind": "source",
                    "ref": evidence,
                    "unit_ref": unit,
                    "content_sha256": digest,
                },
                "text_state": state,
                "search_text": text,
            }
        return reply, units

    def request(
        self,
        run: dict[str, Any],
        *entries: dict[str, Any],
        max_bytes: int = 65536,
        step: str | None = None,
    ) -> dict[str, Any]:
        return {
            "contract_version": 1,
            "run_ref": run["run_ref"],
            "step_key": step or str(uuid4()),
            "kind": "read_source_text",
            "entries": list(entries),
            "max_bytes": max_bytes,
            "cursor": None,
        }

    @staticmethod
    def entry(
        unit: dict[str, Any], start: int, end: int, origin: Any = None
    ) -> dict[str, Any]:
        return {"source": unit["source"], "start": start, "end": end, "origin": origin}

    def send(
        self,
        request: dict[str, Any],
        *,
        secret: str | None = None,
        control: Any = None,
    ) -> tuple[bytes, dict[str, Any]]:
        raw = self.r.service(secret or self.agent, control=control).handle(
            json.dumps(request).encode()
        )
        return raw, json.loads(raw)

    def available(self, raw: bytes, reply: dict[str, Any]) -> None:
        self.assertEqual(reply["outcome"], "available", reply)
        self.assertIsNone(reply.get("error"))
        self.assertNotIn("result", reply)
        row = self.db.execute(
            "SELECT d.state,d.outcome,d.charged_db_ms,d.charged_transport,"
            "x.result_id,x.content_digest,x.response_sha256,x.delivered_refs,"
            "a.content_bytes FROM memoriesql.query_deliveries d "
            "JOIN memoriesql.query_disclosures x USING(tenant_id,delivery_ref) "
            "JOIN memoriesql.query_steps s USING(tenant_id,run_ref,step_key) "
            "JOIN memoriesql.result_preparation_artifacts a "
            "ON a.tenant_id=s.tenant_id AND a.artifact_ref=s.helper_artifact_ref "
            "WHERE d.delivery_ref=%s",
            (reply["access_receipt_ref"],),
        ).fetchone()
        assert row is not None
        self.assertEqual(row[:2], ("settled", "available"))
        self.assertEqual(row[2], reply["work"]["charged_ms"])
        self.assertEqual(row[3], len(raw))
        self.assertIsNone(row[4])
        self.assertEqual(row[5], hashlib.sha256(bytes(row[8])).hexdigest())
        self.assertEqual(row[6], hashlib.sha256(raw).hexdigest())
        self.assertEqual(reply["visibility"]["new_refs"], [])
        self.assertEqual(reply["visibility"]["hydration_required"], [])
        self.assertEqual(
            set(row[7]),
            {
                json.dumps(
                    ref, ensure_ascii=True, sort_keys=True, separators=(",", ":")
                )
                for ref in reply["visibility"]["previous_refs"]
            },
        )
        self.no_protected_metadata(reply)

    def no_protected_metadata(self, reply: dict[str, Any]) -> None:
        text = json.dumps(reply)
        # A delivered ref's content_sha256 is the agent-visible unit digest the
        # query exception already served; a single-part package's part digest
        # equals it. Only that field of a delivered ref is exempt.
        for ref in reply.get("visibility", {}).get("previous_refs", []):
            self.assertEqual(
                ref["content_sha256"],
                self.scalar(
                    "SELECT content_hash FROM memoriesql.source_units "
                    "WHERE source_unit_id=%s",
                    (ref["unit_ref"],),
                ),
            )
            text = text.replace(
                '"content_sha256": "' + ref["content_sha256"] + '"',
                '"content_sha256": "delivered"',
            )
        for key in PROTECTED_KEYS:
            self.assertNotIn('"' + key + '"', text)
        for value in (self.RAW, self.WIDE_RAW):
            self.assertNotIn(json.dumps(value[:40])[1:-1], text)
        hidden = self.db.execute(
            "SELECT p.package_id::text,p.inventory_hash,pp.part_id::text,"
            "pp.inventory->>'content_sha256' FROM memoriesql.evidence_packages p "
            "JOIN memoriesql.evidence_package_parts pp USING(tenant_id,package_id)"
        ).fetchall()
        for row in hidden:
            for value in row:
                if value:
                    self.assertNotIn(value, text)

    def unavailable(self, reply: dict[str, Any]) -> None:
        self.assertEqual(
            (reply["outcome"], reply["error"]),
            ("unavailable", {"code": "unavailable"}),
            reply,
        )
        for field in ("source_text", "visibility", "result", "hydration", "inspection"):
            self.assertNotIn(field, reply)
        self.assertNotIn("unit_characters", json.dumps(reply))
        self.assertNotIn(self.TEXT[:40], json.dumps(reply))

    def disclosures(self) -> int:
        return int(self.scalar("SELECT count(*) FROM memoriesql.query_disclosures"))

    def revoke_source(self, bead: UUID) -> None:
        self.db.execute(
            "UPDATE memoriesql.protected_resources SET status='revoked',revoked_at=clock_timestamp() "
            "WHERE resource_kind='source' AND resource_id=("
            "SELECT e.source_object_id FROM memoriesql.beads b JOIN memoriesql.source_events e "
            "ON e.tenant_id=b.tenant_id AND e.event_id=b.event_id WHERE b.bead_id=%s)",
            (bead,),
        )

    def expire_results(self, run: dict[str, Any]) -> None:
        self.db.execute(
            "ALTER TABLE memoriesql.query_result_creations DISABLE TRIGGER "
            "query_result_creations_no_update"
        )
        try:
            self.db.execute(
                "UPDATE memoriesql.query_result_creations c SET "
                "created_at=x.at-interval '720 hours',expires_at=x.at "
                "FROM memoriesql.result_preparation_operations o,"
                "(SELECT clock_timestamp()-interval '1 second' AS at) x "
                "WHERE o.tenant_id=c.tenant_id AND o.operation_ref=c.operation_ref "
                "AND o.run_ref=%s",
                (run["run_ref"],),
            )
        finally:
            self.db.execute(
                "ALTER TABLE memoriesql.query_result_creations ENABLE TRIGGER "
                "query_result_creations_no_update"
            )

    # -- positive ----------------------------------------------------------------

    def test_agent_reads_exactly_the_query_exceptions_unit_text(self) -> None:
        # Cases 1, 4 and 7's path: query, statement_sources, read; section 6.
        run = self.r.start(self.agent)
        delivering, units = self.cited(run)
        unit = units[self.unit_of(self.normalized)]
        self.assertEqual(unit["text_state"], "available")
        served = unit["search_text"]
        self.assertTrue(served.startswith("Fictional normalized"))
        raw, reply = self.send(self.request(run, self.entry(unit, 30, 90)))
        self.available(raw, reply)
        (piece,) = reply["source_text"]["slices"]
        self.assertEqual(piece["source"], unit["source"])
        self.assertEqual(piece["text_state"], "available")
        self.assertEqual(piece["text"], served[30:90])
        self.assertIn("α", piece["text"])
        self.assertEqual(piece["unit_characters"], len(served))
        self.assertEqual(
            piece["returned_sha256"], hashlib.sha256(served[30:90].encode()).hexdigest()
        )
        self.assertEqual(
            piece["frame"],
            {
                "known_at": run["default_known_at"],
                "view": "historical",
            },
        )
        version = self.scalar(
            "SELECT p.declaration->>'normalization_policy_version' "
            "FROM memoriesql.logical_unit_materializations m JOIN memoriesql.evidence_packages p "
            "USING(tenant_id,package_id) WHERE m.source_unit_id=%s",
            (unit["source"]["unit_ref"],),
        )
        self.assertIn(
            "normalized_projection:" + version,
            reply["source_text"]["source_text_labels"],
        )
        self.assertTrue(reply["source_text"]["selection_complete"])
        self.assertEqual(reply["visibility"]["previous_refs"], [unit["source"]])
        (basis,) = reply["access_bases"]
        self.assertEqual(basis["context"], {"kind": "standalone"})
        self.assertEqual(basis["end_condition"], "standalone_expiry")
        self.assertEqual(basis["valid_until"], delivering["result"]["expires_at"])

    def test_two_sources_from_two_frames_keep_their_own_frames(self) -> None:
        # Case 2: one artifact, request order, per-slice frames.
        run = self.r.start(self.agent)
        _, units = self.cited(run)
        resolved, again = self.cited(run, view="resolved")
        text, wide = (
            units[self.unit_of(self.normalized)],
            units[self.unit_of(self.wide)],
        )
        origin = {
            "result": {
                key: resolved["result"][key] for key in ("result_id", "content_digest")
            },
            "access": {"kind": "standalone"},
        }
        raw, reply = self.send(
            self.request(
                run,
                self.entry(wide, 0, 10),
                self.entry(again[text["source"]["unit_ref"]], 0, 10, origin),
            )
        )
        self.available(raw, reply)
        first, second = reply["source_text"]["slices"]
        self.assertEqual(first["source"], wide["source"])
        self.assertEqual(first["frame"]["view"], "historical")
        self.assertEqual(second["frame"]["view"], "resolved")
        self.assertEqual(second["text"], text["search_text"][:10])
        self.assertEqual(
            self.scalar(
                "SELECT count(DISTINCT helper_artifact_ref) FROM memoriesql.query_steps "
                "WHERE kind='read_source_text'"
            ),
            1,
        )

    def test_successive_intervals_page_the_whole_served_text(self) -> None:
        # Case 3: concatenation equals SQL search_text; unit_characters bounds it.
        run = self.r.start(self.agent)
        _, units = self.cited(run)
        unit = units[self.unit_of(self.normalized)]
        served = unit["search_text"]
        pieces: list[str] = []
        for start in range(0, len(served), 700):
            raw, reply = self.send(
                self.request(
                    run, self.entry(unit, start, min(start + 700, len(served)))
                )
            )
            self.available(raw, reply)
            (piece,) = reply["source_text"]["slices"]
            self.assertEqual(piece["unit_characters"], len(served))
            pieces.append(piece["text"])
        self.assertEqual("".join(pieces), served)

    def test_exact_replay_charges_canonically_and_never_reprepares(self) -> None:
        # Case 5.
        run = self.r.start(self.agent)
        _, units = self.cited(run)
        request = self.request(
            run, self.entry(units[self.unit_of(self.normalized)], 0, 40)
        )
        raw, first = self.send(request)
        self.available(raw, first)
        artifacts = self.scalar(
            "SELECT count(*) FROM memoriesql.result_preparation_artifacts"
        )
        trace: list[str] = []

        class Traced(psycopg.Connection[Any]):
            def execute(self, *args: Any, **kwargs: Any) -> Any:
                trace.append(str(args[0]))
                return super().execute(*args, **kwargs)

        def control() -> psycopg.Connection[Any]:
            return Traced.connect(
                make_conninfo(self.fixture.admin, dbname=self.fixture.database),
                autocommit=True,
            )

        raw, again = self.send(request, control=control)
        self.available(raw, again)
        self.assertEqual(again["source_text"], first["source_text"])
        self.assertEqual(again["receipt_ref"], first["receipt_ref"])
        self.assertNotEqual(again["access_receipt_ref"], first["access_receipt_ref"])
        self.assertEqual(
            again["remaining"]["accesses"], first["remaining"]["accesses"] - 1
        )
        self.assertEqual(
            again["remaining"],
            remaining_after(
                first["remaining"],
                db_ms=again["work"]["charged_ms"],
                transport_bytes=len(raw),
            ),
        )
        for function in (
            "prepare_query_helper_v1",
            "reserve_result_preparation_v1",
            "seal_result_preparation_v1",
        ):
            self.assertFalse(
                any(s.startswith("SELECT memoriesql." + function + "(") for s in trace),
                function,
            )
        self.assertEqual(
            self.scalar("SELECT count(*) FROM memoriesql.result_preparation_artifacts"),
            artifacts,
        )

    def test_lost_commit_ack_replays_the_original_reading(self) -> None:
        # Case 6.
        run = self.r.start(self.agent)
        _, units = self.cited(run)
        request = self.request(
            run, self.entry(units[self.unit_of(self.normalized)], 0, 40)
        )
        lost: list[int] = []

        class LostAck(psycopg.Connection[Any]):
            recorded = False

            def execute(self, *args: Any, **kwargs: Any) -> Any:
                result = super().execute(*args, **kwargs)
                if str(args[0]).startswith(
                    "SELECT memoriesql.record_query_helper_disclosure_v1("
                ):
                    self.recorded = True
                return result

            @contextmanager
            def transaction(self, *args: Any, **kwargs: Any) -> Iterator[Any]:
                with super().transaction(*args, **kwargs) as transaction:
                    yield transaction
                if self.recorded and not lost:
                    lost.append(self.info.backend_pid)
                    raise psycopg.OperationalError(
                        "fictional lost commit acknowledgement"
                    )

        def control() -> psycopg.Connection[Any]:
            return LostAck.connect(
                make_conninfo(self.fixture.admin, dbname=self.fixture.database),
                autocommit=True,
            )

        _, pending = self.send(request, control=control)
        self.assertEqual(
            (pending["outcome"], pending["error"]),
            ("settlement_pending", {"code": "settlement"}),
        )
        self.assertNotIn("source_text", pending)
        original = self.db.execute(
            "SELECT artifact_ref,artifact_sha256 FROM memoriesql.result_preparation_artifacts "
            "ORDER BY artifact_ref"
        ).fetchall()
        raw, replay = self.send(request)
        self.available(raw, replay)
        self.assertEqual(replay["receipt_ref"], pending["receipt_ref"])
        self.assertEqual(
            self.db.execute(
                "SELECT artifact_ref,artifact_sha256 FROM memoriesql.result_preparation_artifacts "
                "ORDER BY artifact_ref"
            ).fetchall(),
            original,
        )

    # -- refusals and non-disclosure ----------------------------------------------

    def test_only_refs_this_run_received_are_readable(self) -> None:
        # Cases 8, 9, 10, 13, 22 and 34: one unavailable shape, nothing disclosed.
        run = self.r.start(self.agent)
        _, observations = self.r.query(
            run,
            "SELECT o.bead_id FROM memory_v1.observations o ORDER BY o.bead_id",
            secret=self.agent,
            page_size=50,
        )
        _, units = self.cited(run)
        unit = units[self.unit_of(self.normalized)]
        owner = self.r.start()
        _, owner_units = self.cited(owner, secret=self.fixture.secret_hash)
        private = owner_units[self.unit_of(self.private)]
        # A workspace holds at most two open runs: every other run is closed
        # once it has served its part.
        self.r.close(owner, self.fixture.secret_hash)
        before = self.disclosures()
        fabricated = {
            "kind": "source",
            "ref": str(uuid4()),
            "unit_ref": unit["source"]["unit_ref"],
            "content_sha256": unit["source"]["content_sha256"],
        }
        wrong_hash = dict(unit["source"], content_sha256="0" * 64)
        not_delivering = {
            "result": {
                key: observations["result"][key]
                for key in ("result_id", "content_digest")
            },
            "access": {"kind": "standalone"},
        }
        refusals = []
        # Delivered to this agent, but read in another run; and the agent's ref
        # read by the owner in a run that never received it.
        for secret in (self.agent, self.fixture.secret_hash):
            other = self.r.start(secret)
            _, reply = self.send(
                self.request(other, self.entry(unit, 0, 4)), secret=secret
            )
            self.unavailable(reply)
            refusals.append(reply)
            self.r.close(other, secret)
        for entries, secret, target in (
            # Never delivered: a ref built from a unit and hash, not received.
            (
                [{"source": fabricated, "start": 0, "end": 4, "origin": None}],
                self.agent,
                run,
            ),
            # The delivered value's content hash, changed.
            (
                [{"source": wrong_hash, "start": 0, "end": 4, "origin": None}],
                self.agent,
                run,
            ),
            # Delivered to another principal only (owner-private bead).
            ([self.entry(private, 0, 4)], self.agent, run),
            # An explicit origin whose result did not deliver this ref.
            ([self.entry(unit, 0, 4, not_delivering)], self.agent, run),
            # One undelivered entry withholds the whole reply.
            (
                [
                    self.entry(unit, 0, 4),
                    {"source": fabricated, "start": 0, "end": 4, "origin": None},
                ],
                self.agent,
                run,
            ),
        ):
            _, reply = self.send(self.request(target, *entries), secret=secret)
            self.unavailable(reply)
            refusals.append(reply)
        self.assertEqual(len({frozenset(reply) for reply in refusals}), 1)
        self.assertEqual(self.disclosures(), before)

    def test_revocation_precedes_offsets_and_blocks_exact_replay(self) -> None:
        # Cases 11, 12 and 26.
        run = self.r.start(self.agent)
        _, units = self.cited(run)
        unit = units[self.unit_of(self.normalized)]
        request = self.request(run, self.entry(unit, 0, 40))
        raw, first = self.send(request)
        self.available(raw, first)
        before = self.disclosures()
        self.revoke_source(self.normalized)
        _, replay = self.send(request)
        _, offsets = self.send(self.request(run, self.entry(unit, 10**6, 10**6 + 4)))
        _, missing = self.send(
            self.request(
                run,
                {
                    "source": dict(unit["source"], ref=str(uuid4())),
                    "start": 0,
                    "end": 4,
                    "origin": None,
                },
            )
        )
        for reply in (replay, offsets, missing):
            self.unavailable(reply)
            self.assertEqual(set(reply), set(missing))
        self.assertEqual(self.disclosures(), before)

    def test_authority_is_the_full_source_text_predicate_now(self) -> None:
        # Cases 17 and 18: losing source.read refuses; raw authority never widens.
        run = self.r.start(self.agent)
        _, units = self.cited(run)
        unit = units[self.unit_of(self.normalized)]
        owner = self.r.start()
        _, owner_units = self.cited(owner, secret=self.fixture.secret_hash)
        raw, owners = self.send(
            self.request(
                owner, self.entry(owner_units[unit["source"]["unit_ref"]], 0, 40)
            ),
            secret=self.fixture.secret_hash,
        )
        self.available(raw, owners)
        raw, agents = self.send(self.request(run, self.entry(unit, 0, 40)))
        self.available(raw, agents)
        for field in ("text", "unit_characters", "returned_sha256", "text_state"):
            self.assertEqual(
                owners["source_text"]["slices"][0][field],
                agents["source_text"]["slices"][0][field],
            )
        self.assertEqual(set(owners), set(agents))
        self.assertEqual(
            set(owners["source_text"]["slices"][0]),
            set(agents["source_text"]["slices"][0]),
        )
        with self.db.transaction():
            self.fixture.begin()
            self.db.execute(
                "SELECT memoriesql.revise_pairing_grant(%s,1,%s,%s,'active',%s,%s,%s)",
                (
                    self.grant,
                    ["memory.inspect", "memory.query"],
                    [self.scope],
                    self.fixture.now,
                    self.fixture.now + timedelta(hours=1),
                    self.fixture.now,
                ),
            )
        _, reply = self.send(self.request(run, self.entry(unit, 0, 40)))
        self.unavailable(reply)

    def test_request_bounds_and_content_byte_limits(self) -> None:
        # Cases 19, 20 and 31.
        run = self.r.start(self.agent)
        _, units = self.cited(run)
        unit, wide = (
            units[self.unit_of(self.normalized)],
            units[self.unit_of(self.wide)],
        )
        served = wide["search_text"]
        astral, accented = served.index("🍐"), served.index("é")
        cjk = served.index("中")
        # Each at exactly 16384 UTF-8 bytes; the CJK one starts on an ASCII space.
        for start, end in (
            (astral, astral + 4096),
            (accented, accented + 8192),
            (cjk - 1, cjk + 5461),
        ):
            raw, reply = self.send(self.request(run, self.entry(wide, start, end)))
            self.available(raw, reply)
            self.assertEqual(
                len(reply["source_text"]["slices"][0]["text"].encode()), 16384
            )
        before = self.disclosures()
        for entries, max_bytes, expected in (
            (
                [self.entry(wide, astral, astral + 4097)],
                65536,
                ("invalid_request", "binding"),
            ),
            (
                [self.entry(wide, accented - 1, accented + 8192)],
                65536,
                ("invalid_request", "binding"),
            ),
            (
                [self.entry(wide, cjk - 2, cjk + 5461)],
                65536,
                ("invalid_request", "binding"),
            ),
            (
                [self.entry(unit, 0, len(unit["search_text"]) + 1)],
                65536,
                ("invalid_request", "binding"),
            ),
            ([self.entry(unit, 0, 40)], 39, ("budget_exhausted", "storage")),
        ):
            _, reply = self.send(self.request(run, *entries, max_bytes=max_bytes))
            self.assertEqual(
                (reply["outcome"], reply["error"]["code"]), expected, reply
            )
            self.assertNotIn("source_text", reply)
        # One available slice larger than the whole-reply cap: transport refusal.
        with patch.object(results_module, "MAX_RESPONSE_BYTES", 2048):
            _, reply = self.send(
                self.request(run, self.entry(wide, astral, astral + 1000))
            )
        self.assertEqual(
            (reply["outcome"], reply["error"]),
            ("budget_exhausted", {"code": "transport"}),
        )
        self.assertEqual(self.disclosures(), before)

    def test_identity_packages_are_unsupported_and_never_measured(self) -> None:
        # Cases 27 and 45 (N-5).
        run = self.r.start(self.agent)
        _, units = self.cited(run)
        identity, text = (
            units[self.unit_of(self.identity)],
            units[self.unit_of(self.normalized)],
        )
        self.assertEqual(
            (identity["text_state"], identity["search_text"]), ("unsupported", None)
        )
        # Exactly the shown slice's bytes: measuring the unsupported entry at
        # all would exceed it.
        budget = len(text["search_text"][:40].encode())
        replies = []
        for start, end in ((0, 4), (10**6, 10**6 + 16384)):
            raw, reply = self.send(
                self.request(
                    run,
                    self.entry(identity, start, end),
                    self.entry(text, 0, 40),
                    max_bytes=budget,
                )
            )
            self.available(raw, reply)
            hidden, shown = reply["source_text"]["slices"]
            self.assertEqual(
                {
                    k: hidden[k]
                    for k in (
                        "text_state",
                        "unit_characters",
                        "text",
                        "returned_sha256",
                    )
                },
                {
                    "text_state": "unsupported",
                    "unit_characters": None,
                    "text": None,
                    "returned_sha256": None,
                },
            )
            self.assertEqual(shown["text"], text["search_text"][:40])
            self.assertFalse(reply["source_text"]["selection_complete"])
            replies.append(reply)
        for reply in replies:
            for slice_ in reply["source_text"]["slices"]:
                slice_.pop("start")
                slice_.pop("end")
        self.assertEqual(replies[0]["source_text"], replies[1]["source_text"])

    def test_a_newer_representation_is_never_read(self) -> None:
        # Case 23 (decision 2): only the pinned package's text is served.
        run = self.r.start(self.agent)
        _, units = self.cited(run)
        unit = units[self.unit_of(self.normalized)]
        source = self.scalar(
            "SELECT e.source_object_id FROM memoriesql.beads b JOIN memoriesql.source_events e "
            "ON e.tenant_id=b.tenant_id AND e.event_id=b.event_id WHERE b.bead_id=%s",
            (self.normalized,),
        )
        self.r.newer_representation(
            "Fictional NEWER representation text.",
            "text-normalized",
            source,
            self.scope,
        )
        raw, reply = self.send(self.request(run, self.entry(unit, 0, 40)))
        self.available(raw, reply)
        self.assertEqual(
            reply["source_text"]["slices"][0]["text"], unit["search_text"][:40]
        )
        self.assertNotIn("NEWER", json.dumps(reply))

    def test_run_expiry_expired_results_and_checkpoint_origins(self) -> None:
        # Cases 24, 32 and 33.
        run = self.r.start(self.agent)
        delivering, units = self.cited(run)
        unit = units[self.unit_of(self.normalized)]
        checkpoint = {
            "result": {
                key: delivering["result"][key]
                for key in ("result_id", "content_digest")
            },
            "access": {
                "kind": "checkpoint",
                "checkpoint_id": str(uuid4()),
                "manifest_digest": "c" * 64,
                "root": {
                    key: delivering["result"][key]
                    for key in ("result_id", "content_digest")
                },
                "basis": {"kind": "automatic"},
            },
        }
        _, reply = self.send(self.request(run, self.entry(unit, 0, 4, checkpoint)))
        self.assertEqual(reply["outcome"], "unsupported_query", reply)
        self.assertNotIn("source_text", reply)
        self.expire_results(run)
        _, reply = self.send(self.request(run, self.entry(unit, 0, 4)))
        self.unavailable(reply)
        self.r.age(run["run_ref"], timedelta(minutes=31), results=False)
        _, reply = self.send(self.request(run, self.entry(unit, 0, 4)))
        self.assertEqual(
            (reply["outcome"], reply["error"]), ("budget_exhausted", {"code": "time"})
        )

    def test_losing_any_dependency_of_the_delivering_result_refuses_all(self) -> None:
        # Case 35: whole-closure recheck, no salvage of an unaffected source.
        run = self.r.start(self.agent)
        _, units = self.cited(run)
        unit = units[self.unit_of(self.normalized)]
        self.revoke_source(self.identity)
        _, reply = self.send(self.request(run, self.entry(unit, 0, 40)))
        self.unavailable(reply)

    def test_cursor_and_step_bindings(self) -> None:
        # Cases 25, 38 and 39.
        run = self.r.start(self.agent)
        _, units = self.cited(run)
        unit, wide = (
            units[self.unit_of(self.normalized)],
            units[self.unit_of(self.wide)],
        )
        request = self.request(run, self.entry(unit, 0, 40), self.entry(wide, 0, 40))
        # A one-slice reply is about 1.8-2 KiB; the second slice adds well over
        # 400 bytes (slice, ref, basis), a cursor far less.
        single = max(
            len(self.send(self.request(run, self.entry(each, 0, 40)))[0])
            for each in (unit, wide)
        )
        with patch.object(results_module, "MAX_RESPONSE_BYTES", single + 400):
            raw, first = self.send(request)
            self.available(raw, first)
            self.assertTrue(first["has_more"], first)
            cursor = first["next_cursor"]
            raw, page = self.send(dict(request, step_key=str(uuid4()), cursor=cursor))
            self.available(raw, page)
            self.assertEqual(page["source_text"]["slices"][0]["source"], wide["source"])
            _, changed = self.send(
                dict(request, step_key=str(uuid4()), cursor=cursor, max_bytes=65535)
            )
            self.assertEqual(
                (changed["outcome"], changed["error"]),
                ("invalid_request", {"code": "binding"}),
            )
            _, tampered = self.send(
                dict(request, step_key=str(uuid4()), cursor=str(uuid4()))
            )
            self.assertEqual(
                (tampered["outcome"], tampered["error"]),
                ("invalid_request", {"code": "cursor"}),
            )
        _, conflict = self.send(dict(request, entries=[self.entry(unit, 0, 41)]))
        self.assertEqual(
            (conflict["outcome"], conflict["error"]),
            ("idempotency_conflict", {"code": "idempotency"}),
        )

    def test_server_cancellation_rolls_back_and_cleanup_releases_everything(
        self,
    ) -> None:
        # Cases 40 and 41.
        run = self.r.start(self.agent)
        delivering, units = self.cited(run)
        request = self.request(
            run, self.entry(units[self.unit_of(self.normalized)], 0, 40)
        )
        fired: list[str] = []

        class CancelAfterRecord(psycopg.Connection[Any]):
            def execute(self, *args: Any, **kwargs: Any) -> Any:
                result = super().execute(*args, **kwargs)
                if (
                    not fired
                    and "SELECT memoriesql.record_query_helper_disclosure_v1("
                    in str(args[0])
                ):
                    fired.append("statement")
                    super().execute("SET LOCAL statement_timeout='100ms'")
                    super().execute("SELECT pg_sleep(1)")
                return result

        def control() -> psycopg.Connection[Any]:
            return CancelAfterRecord.connect(
                make_conninfo(self.fixture.admin, dbname=self.fixture.database),
                autocommit=True,
            )

        before = self.disclosures()
        _, cancelled = self.send(request, control=control)
        self.assertEqual(fired, ["statement"])
        self.assertEqual(
            (cancelled["outcome"], cancelled["error"]),
            ("budget_exhausted", {"code": "time"}),
        )
        self.assertEqual(self.disclosures(), before)
        raw, fresh = self.send(dict(request, step_key=str(uuid4())))
        self.available(raw, fresh)
        self.r.age(run["run_ref"], timedelta(minutes=31), results=False)
        cleanup = json.loads(self.r.service().cleanup_expired())
        self.assertEqual(cleanup["outcome"], "available", cleanup)
        for table in ("query_helper_cursors", "query_visible_refs"):
            self.assertEqual(
                self.scalar("SELECT count(*) FROM memoriesql." + table), 0, table
            )
        self.assertEqual(
            self.scalar(
                "SELECT count(*) FROM memoriesql.query_steps WHERE purged_at IS NULL"
            ),
            0,
        )
        # Only the delivering query's saved result remains, until its own
        # 720-hour expiry: its disclosure, its artifact and its sealed operation.
        result = delivering["result"]["result_id"]
        self.assertEqual(
            self.db.execute(
                "SELECT result_id::text FROM memoriesql.query_disclosures"
            ).fetchall(),
            [(result,)],
        )
        self.assertEqual(
            self.db.execute(
                "SELECT artifact_ref::text FROM memoriesql.result_preparation_artifacts"
            ).fetchall(),
            [(result,)],
        )
        self.assertEqual(
            self.scalar(
                "SELECT count(*) FROM memoriesql.result_preparation_operations o "
                "WHERE (o.state<>'discarded' OR o.allocation_bytes<>8192) "
                "AND NOT EXISTS(SELECT 1 FROM memoriesql.query_result_creations c "
                "WHERE c.tenant_id=o.tenant_id AND c.operation_ref=o.operation_ref "
                "AND c.result_id=%s)",
                (result,),
            ),
            0,
        )

    # -- fixtures for package shapes and other workspaces --------------------------

    @contextmanager
    def tampering(self) -> Iterator[None]:
        """Fixture-only edits of rows the product never mutates (triggers off)."""
        with self.db.transaction():
            self.db.execute("SET LOCAL session_replication_role=replica")
            yield

    def qualified_bead(self, text: str, key: str) -> UUID:
        """A producer-normalized bead whose package declares an exclusion.

        Materialization refuses a package with declared unresolved coverage, so
        a cited unit never carries one; its label is the population's own code.
        """
        original = self.fixture.create
        qualification = SourceQualification(
            qualification_ref="orchard.fictional-qualification.v1",
            boundary="qualified_native_unit",
            boundary_basis="Explicit fictional unit-end record.",
            physical_records="complete",
            topology="unknown",
            normalized_input="complete",
            source_completeness="producer_attested",
            exclusions=("orchard.attachments",),
        )

        def create(parts: Any, **updates: Any) -> Any:
            return original(parts, **(updates | {"qualification": qualification}))

        with patch.object(self.fixture, "create", create):
            return self.r.normalized_bead(
                text, key, raw=self.RAW, source=self.scope_source, scope=self.scope
            )

    def mixed_bead(self, text: str, key: str) -> UUID:
        """One unit whose package holds a normalized part and an identity part."""
        fixture = self.fixture
        original_part, original_create = fixture.part, fixture.create
        original_append = fixture.append
        tail: list[Any] = []

        def part(content: str, *args: Any, **kwargs: Any) -> Any:
            head = original_part(content, *args, **kwargs)
            tail.append(original_part("Fictional mixed identity tail.", 1))
            return head

        part.__module__ = original_part.__module__

        def create(parts: Any, **updates: Any) -> Any:
            (head,) = parts
            tail[0] = tail[0].model_copy(
                update={
                    "component_key": head.component_key,
                    "component_offset": len(head.content),
                }
            )
            return original_create((head, tail[0]), **updates)

        def append(package: Any, item: Any, key: str | None = None) -> Any:
            result = original_append(package, item, key)
            if tail and item.ordinal == 0:
                original_append(package, tail[0])
            return result

        with (
            patch.object(fixture, "part", part),
            patch.object(fixture, "create", create),
            patch.object(fixture, "append", append),
        ):
            return self.r.normalized_bead(
                text, key, raw=self.RAW, source=self.scope_source, scope=self.scope
            )

    def repoint(self, unit: str, package: UUID) -> None:
        """Bind a unit to another sealed package (fixture tamper of an immutable pin)."""
        with self.tampering():
            self.db.execute(
                "UPDATE memoriesql.logical_unit_materializations m SET package_id=p.package_id,"
                "package_pin=m.package_pin||jsonb_build_object('inventory_sha256',p.inventory_hash) "
                "FROM memoriesql.evidence_packages p WHERE p.package_id=%s AND m.source_unit_id=%s",
                (package, unit),
            )

    def elsewhere(self) -> PostgresAgentSqlResults:
        """The same owner, authenticated into a second workspace of the tenant."""
        fixture, workspace, scope, policy = self.fixture, uuid4(), uuid4(), uuid4()
        tenant, user, principal, now = (
            fixture.tenant,
            fixture.user,
            fixture.principal,
            fixture.now,
        )
        rows = (
            ("workspaces", (tenant, workspace, "personal_local", user, "active", now)),
            (
                "workspace_memberships",
                (
                    tenant,
                    workspace,
                    principal,
                    "personal_owner",
                    "active",
                    1,
                    now,
                    now,
                    None,
                ),
            ),
            (
                "access_scopes",
                (
                    tenant,
                    workspace,
                    scope,
                    user,
                    "owner_private",
                    policy,
                    "active",
                    now,
                ),
            ),
            (
                "access_policy_revisions",
                (
                    tenant,
                    workspace,
                    scope,
                    policy,
                    1,
                    "owner_private",
                    user,
                    "Fictional second workspace",
                    principal,
                    now,
                ),
            ),
        )
        with self.db.transaction():
            for table, values in rows:
                self.db.execute(
                    sql.SQL("INSERT INTO memoriesql.{} VALUES ({})").format(
                        sql.Identifier(table),
                        sql.SQL(",").join(sql.Placeholder() for _ in values),
                    ),
                    values,
                )
        return PostgresAgentSqlResults(
            control_factory=self.fixture.connection,
            reader_factory=self.r.reader_connection,
            authority_profile=self.r.profile,
            credential_sha256=self.fixture.secret_hash,
            workspace_id=workspace,
        )

    # -- package shapes, pins, withdrawal, workspaces, refusal shape -------------

    def test_a_mixed_package_is_unsupported(self) -> None:
        # Case 28: identity plus producer-normalized parts serve no text.
        mixed = self.mixed_bead("Fictional mixed harvest of pears.", "text-mixed")
        run = self.r.start(self.agent)
        _, units = self.cited(run)
        unit = units[self.unit_of(mixed)]
        self.assertEqual(
            (unit["text_state"], unit["search_text"]), ("unsupported", None)
        )
        raw, reply = self.send(self.request(run, self.entry(unit, 0, 4)))
        self.available(raw, reply)
        (piece,) = reply["source_text"]["slices"]
        self.assertEqual(
            (piece["text_state"], piece["text"], piece["unit_characters"]),
            ("unsupported", None, None),
        )
        self.assertEqual(reply["source_text"]["source_text_labels"], [])
        self.assertNotIn("identity tail", json.dumps(reply))

    def test_retained_unit_text_is_served_without_a_projection_label(self) -> None:
        # Case 29 (fixture tamper: retained text exists only on units older than
        # materialization; 0037 serves it first when present). A materialized
        # logical unit may not hold it, so this test's own database drops that
        # check before the edit.
        unit_ref = self.unit_of(self.identity)
        retained = "Fictional retained unit text: pears, α and 🍐."
        with self.tampering():
            self.db.execute(
                "ALTER TABLE memoriesql.source_units "
                "DROP CONSTRAINT source_units_kind_supported"
            )
            self.db.execute(
                "UPDATE memoriesql.source_units SET content_text=%s WHERE source_unit_id=%s",
                (retained, unit_ref),
            )
        run = self.r.start(self.agent)
        _, units = self.cited(run)
        unit = units[unit_ref]
        self.assertEqual(
            (unit["text_state"], unit["search_text"]), ("available", retained)
        )
        raw, reply = self.send(self.request(run, self.entry(unit, 0, len(retained))))
        self.available(raw, reply)
        self.assertEqual(reply["source_text"]["slices"][0]["text"], retained)
        self.assertEqual(reply["source_text"]["source_text_labels"], [])

    def test_declared_coverage_limits_are_labelled(self) -> None:
        # Case 30.
        qualified = self.qualified_bead(
            "Fictional qualified harvest of pears.", "text-qualified"
        )
        run = self.r.start(self.agent)
        _, units = self.cited(run)
        unit = units[self.unit_of(qualified)]
        raw, reply = self.send(self.request(run, self.entry(unit, 0, 10)))
        self.available(raw, reply)
        labels = reply["source_text"]["source_text_labels"]
        self.assertEqual(
            labels,
            [
                "normalized_projection:orchard.normalization.v1",
                "package_exclusion:orchard.attachments",
            ],
        )
        self.assertFalse(
            any("raw" in label or "sanitized" in label for label in labels)
        )

    def test_a_changed_package_pin_after_delivery_refuses_the_whole_reply(self) -> None:
        # Case 23 (fixture tamper: materialization pins are immutable in product).
        run = self.r.start(self.agent)
        _, units = self.cited(run)
        unit = units[self.unit_of(self.normalized)]
        request = self.request(run, self.entry(unit, 0, 40))
        raw, first = self.send(request)
        self.available(raw, first)
        newer = self.r.newer_representation(
            "Fictional NEWER pinned representation.",
            "text-normalized",
            self.scope_source,
            self.scope,
        )
        self.repoint(unit["source"]["unit_ref"], newer.package_id)
        before = self.disclosures()
        _, fresh = self.send(self.request(run, self.entry(unit, 0, 40)))
        _, replay = self.send(request)
        for reply in (fresh, replay):
            self.unavailable(reply)
            self.assertNotIn("NEWER", json.dumps(reply))
        self.assertEqual(self.disclosures(), before)

    def test_withdrawal_after_delivery_refuses_reads_and_replays(self) -> None:
        # Case 36 (withdrawal of the cited source).
        run = self.r.start(self.agent)
        _, units = self.cited(run)
        unit = units[self.unit_of(self.wide)]
        request = self.request(run, self.entry(unit, 0, 16))
        raw, first = self.send(request)
        self.available(raw, first)
        self.revoke_source(self.wide)
        _, replay = self.send(request)
        _, fresh = self.send(self.request(run, self.entry(unit, 0, 16)))
        for reply in (replay, fresh):
            self.unavailable(reply)

    def test_another_workspace_never_reads_this_workspaces_refs(self) -> None:
        # Case 37 (workspace): refs and runs are bound to their workspace.
        run = self.r.start()
        _, units = self.cited(run, secret=self.fixture.secret_hash)
        unit = units[self.unit_of(self.normalized)]
        raw, here = self.send(
            self.request(run, self.entry(unit, 0, 8)), secret=self.fixture.secret_hash
        )
        self.available(raw, here)
        elsewhere = self.elsewhere()
        started = json.loads(elsewhere.start_run())
        self.assertEqual(started["outcome"], "available", started)
        other = started["run"]
        replies = [
            json.loads(
                elsewhere.handle(
                    json.dumps(self.request(other, self.entry(unit, 0, 8))).encode()
                )
            ),
            json.loads(
                elsewhere.handle(
                    json.dumps(self.request(run, self.entry(unit, 0, 8))).encode()
                )
            ),
        ]
        for reply in replies:
            self.assertEqual(reply["outcome"], "unavailable", reply)
            self.assertNotIn("source_text", reply)
            self.assertNotIn(self.TEXT[:20], json.dumps(reply))
        # The other workspace's run is refused at admission, exactly as an
        # unknown run is: decided before any data lookup.
        unknown = dict(run, run_ref=str(uuid4()))
        standard = json.loads(
            elsewhere.handle(
                json.dumps(self.request(unknown, self.entry(unit, 0, 8))).encode()
            )
        )
        self.assertEqual(set(replies[1]), set(standard))
        self.assertEqual(
            (replies[1]["outcome"], replies[1]["error"]),
            (standard["outcome"], standard["error"]),
        )

    def test_every_refusal_cause_has_one_shape(self) -> None:
        # Case 44: one unavailable shape, no unit_characters, charged as sent.
        run = self.r.start(self.agent)
        _, units = self.cited(run)
        unit = units[self.unit_of(self.normalized)]
        owner = self.r.start()
        _, owner_units = self.cited(owner, secret=self.fixture.secret_hash)
        private = owner_units[self.unit_of(self.private)]
        # A workspace holds at most two open runs: every other run is closed
        # once it has served its part.
        self.r.close(owner, self.fixture.secret_hash)
        elsewhere = self.elsewhere()
        other = json.loads(elsewhere.start_run())["run"]
        refusals: list[tuple[str, bytes, dict[str, Any]]] = []

        def refuse(
            cause: str, request: dict[str, Any], secret: str | None = None
        ) -> None:
            raw, reply = self.send(request, secret=secret)
            refusals.append((cause, raw, reply))

        missing = dict(unit["source"], ref=str(uuid4()))
        refuse(
            "missing",
            self.request(
                run, {"source": missing, "start": 0, "end": 4, "origin": None}
            ),
        )
        refuse(
            "hash mismatch",
            self.request(
                run,
                {
                    "source": dict(unit["source"], content_sha256="0" * 64),
                    "start": 0,
                    "end": 4,
                    "origin": None,
                },
            ),
        )
        fresh = self.r.start(self.agent)
        refuse("another run", self.request(fresh, self.entry(unit, 0, 4)))
        self.r.close(fresh, self.agent)
        refuse("another principal", self.request(run, self.entry(private, 0, 4)))
        raw = elsewhere.handle(
            json.dumps(self.request(other, self.entry(unit, 0, 4))).encode()
        )
        refusals.append(("another workspace", raw, json.loads(raw)))
        expired = self.r.start(self.agent)
        _, expired_units = self.cited(expired)
        self.expire_results(expired)
        refuse(
            "standalone expired",
            self.request(
                expired, self.entry(expired_units[unit["source"]["unit_ref"]], 0, 4)
            ),
        )
        self.r.close(expired, self.agent)
        self.revoke_source(self.identity)
        refuse("lost dependency", self.request(run, self.entry(unit, 0, 4)))
        self.revoke_source(self.normalized)
        refuse("revoked", self.request(run, self.entry(unit, 10**6, 10**6 + 4)))
        with self.db.transaction():
            self.fixture.begin()
            self.db.execute(
                "SELECT memoriesql.revise_pairing_grant(%s,1,%s,%s,'active',%s,%s,%s)",
                (
                    self.grant,
                    ["memory.inspect", "memory.query"],
                    [self.scope],
                    self.fixture.now,
                    self.fixture.now + timedelta(hours=1),
                    self.fixture.now,
                ),
            )
        refuse("denied", self.request(run, self.entry(unit, 0, 4)))
        shapes = set()
        for cause, raw, reply in refusals:
            with self.subTest(cause=cause):
                self.assertEqual(reply["outcome"], "unavailable", reply)
                self.assertEqual(reply["error"], {"code": "unavailable"})
                self.assertNotIn("unit_characters", raw.decode())
                self.assertNotIn("source_text", reply)
                shapes.add(frozenset(reply))
                charged = self.scalar(
                    "SELECT charged_transport FROM memoriesql.query_deliveries "
                    "WHERE delivery_ref=%s",
                    (reply.get("access_receipt_ref"),),
                )
                if charged is not None:
                    self.assertEqual(charged, len(raw))
        # Every cause is refused after admission, in one shape.
        self.assertEqual(len(shapes), 1)

    def test_a_later_correction_follows_the_delivering_results_closure(self) -> None:
        # Case 36, v1 erratum 1: after a correction of the citing statement the
        # read's outcome equals reusing the delivering result; if served, it is
        # the original pinned unit text, never the correcting statement's.
        run = self.r.start(self.agent)
        delivering, units = self.cited(run)
        unit = units[self.unit_of(self.normalized)]
        local = self.fixture.source
        self.fixture.source = self.scope_source
        try:
            with self.r.in_scope(self.scope):
                corrected = self.fixture.correction(self.normalized)
        finally:
            self.fixture.source = local
        corrections = [
            str(text)
            for (text,) in self.db.execute(
                "SELECT statement_text FROM memoriesql.bead_semantic_statements "
                "WHERE bead_id=%s",
                (corrected,),
            ).fetchall()
        ]
        self.assertTrue(corrections)
        reused = self.r.reuse(
            run, delivering["result"], page_size=50, secret=self.agent
        )
        raw, reply = self.send(self.request(run, self.entry(unit, 0, 40)))
        self.assertEqual(reply["outcome"], reused["outcome"], (reply, reused))
        if reply["outcome"] == "available":
            self.available(raw, reply)
            self.assertEqual(
                reply["source_text"]["slices"][0]["text"], unit["search_text"][:40]
            )
            for text in corrections:
                self.assertNotIn(json.dumps(text)[1:-1], raw.decode())
        else:
            self.unavailable(reply)

    # -- authorization audit (owner option A; separate audit allowance) ------------

    ATTRIBUTED = (
        "SELECT a.authorization_audit_event_id,a.resource_id,a.capability_key,"
        "a.decision,a.recorded_at,e.delivery_ref,e.workspace_id,e.expires_at "
        "FROM memoriesql.query_audit_events e JOIN memoriesql.authorization_audit_events a "
        "USING(tenant_id,authorization_audit_event_id)"
    )

    def attributed(self, delivery: str | None = None) -> list[tuple[Any, ...]]:
        if delivery is None:
            return self.db.execute(self.ATTRIBUTED).fetchall()
        return self.db.execute(
            self.ATTRIBUTED + " WHERE e.delivery_ref=%s", (delivery,)
        ).fetchall()

    def audit_rows(self) -> int:
        return int(
            self.scalar("SELECT count(*) FROM memoriesql.authorization_audit_events")
        )

    def test_source_text_audit_is_attributed_both_ways_and_charged_to_its_allowance(
        self,
    ) -> None:
        # Case 42: every audit row a read writes names its own delivery, no row
        # outside a live delivery is attributed, rows are retained 30 days and
        # charged per row to the workspace's separate audit allowance. A paired
        # agent's checks write no audit; the owner's relation closure does.
        before = self.audit_rows()
        agent_run = self.r.start(self.agent)
        _, agent_units = self.cited(agent_run)
        raw, agent_read = self.send(
            self.request(
                agent_run, self.entry(agent_units[self.unit_of(self.normalized)], 0, 40)
            )
        )
        self.available(raw, agent_read)
        self.assertEqual(self.audit_rows(), before)
        self.r.close(agent_run, self.agent)
        owner = self.fixture.secret_hash
        run = self.r.start()
        delivering, units = self.cited(run, secret=owner)
        unit = units[self.unit_of(self.normalized)]
        raw, reply = self.send(self.request(run, self.entry(unit, 0, 40)), secret=owner)
        self.available(raw, reply)
        rows = self.attributed()
        per_delivery = Counter(str(row[5]) for row in rows)
        self.assertGreater(per_delivery[reply["access_receipt_ref"]], 0)
        self.assertEqual(
            set(per_delivery),
            {delivering["access_receipt_ref"], reply["access_receipt_ref"]},
        )
        self.assertEqual(self.audit_rows() - before, len(rows))
        for *_head, recorded, _ref, space, expires in rows:
            self.assertEqual(space, self.fixture.workspace)
            self.assertEqual(expires - recorded, timedelta(days=30))
        row_bytes = int(self.scalar("SELECT memoriesql.query_audit_row_bytes_v1()"))
        status = json.loads(self.r.service().cleanup_status())["audit_retention"]
        self.assertEqual(
            status["retained"],
            {"audit_rows": len(rows), "charged_bytes": len(rows) * row_bytes},
        )
        # The allowance, not the content allocation, bounds it: shrink it to what
        # is retained and the next read is refused before admission, writing
        # no audit and disclosing nothing.
        self.db.execute(
            "CREATE OR REPLACE FUNCTION memoriesql.query_audit_allowance_bytes_v1() "
            "RETURNS bigint LANGUAGE sql IMMUTABLE SET search_path=pg_catalog "
            f"AS $$ SELECT {len(rows) * row_bytes}::bigint $$"
        )
        audit = self.audit_rows()
        _, refused = self.send(self.request(run, self.entry(unit, 0, 40)), secret=owner)
        self.assertEqual(
            (refused["outcome"], refused["error"]),
            ("budget_exhausted", {"code": "storage"}),
            refused,
        )
        self.assertIsNone(refused["access_receipt_ref"])
        self.assertNotIn("source_text", refused)
        self.assertEqual(self.audit_rows(), audit)

    def test_entries_sharing_a_delivering_result_check_its_closure_once(self) -> None:
        # Case 43: eight entries from one delivering result write no more audit
        # than one entry: its closure is checked once per call (as the owner,
        # whose relation closure audits).
        owner = self.fixture.secret_hash
        run = self.r.start()
        _, units = self.cited(run, secret=owner)
        unit = units[self.unit_of(self.normalized)]
        raw, one = self.send(self.request(run, self.entry(unit, 0, 8)), secret=owner)
        self.available(raw, one)
        raw, eight = self.send(
            self.request(run, *(self.entry(unit, i * 8, i * 8 + 8) for i in range(8))),
            secret=owner,
        )
        self.available(raw, eight)
        single = len(self.attributed(one["access_receipt_ref"]))
        many = len(self.attributed(eight["access_receipt_ref"]))
        print(
            json.dumps(
                {"source_text_audit_rows": {"one_entry": single, "eight_entries": many}}
            )
        )
        self.assertGreater(single, 0)
        self.assertEqual(many, single)

    # -- hidden part count never reaches the read (N-2; case 45's charges) -------

    def part_relations(self) -> list[int]:
        found: list[int] = self.scalar(RELATIONS, (PARTS,) * 4)
        self.assertGreaterEqual(len(found), 3)
        return found

    def settled_blocks(self, relations: list[int]) -> int:
        """Blocks every finished backend fetched from these relations, once flushed.

        Service connections close after each request, and a backend flushes its
        statistics as it exits; this connection flushes on request.
        """
        deadline = time.monotonic() + 15
        while self.scalar(
            "SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() "
            "AND pid<>pg_backend_pid() AND backend_type='client backend'"
        ):
            if time.monotonic() > deadline:
                break
            time.sleep(0.05)
        self.db.execute("SELECT pg_stat_force_next_flush()")
        previous = None
        while True:
            self.db.execute("SELECT pg_stat_clear_snapshot()")
            current = int(
                self.scalar(
                    "SELECT sum(pg_stat_get_blocks_fetched(r)) FROM unnest(%s::oid[]) r",
                    (relations,),
                )
            )
            if current == previous:
                return current
            previous = current
            time.sleep(0.5)

    def fragmented_bead(
        self, statement: str, key: str, *, raw: str, parts: tuple[tuple[str, str], ...]
    ) -> UUID:
        """A cited bead whose unit's package holds `parts` over one capture of raw.

        Each part is (derivation, content): a producer_normalized part holds its
        content, an identity_utf8 part its own raw slice; every lineage is an
        exact sub-slice of the one capture (the facts suite's construction).
        """
        fixture = self.fixture
        original, create, append = fixture.part, fixture.create, fixture.append
        fragments: dict[UUID, tuple[Any, ...]] = {}

        def digest(data: bytes) -> str:
            return hashlib.sha256(data).hexdigest()

        def captured(content: str, *args: Any, **kwargs: Any) -> Any:
            return original(raw, *args, **kwargs)

        def fragmented(declared: tuple[Any, ...], **updates: Any) -> Any:
            (part,) = declared
            (whole,) = part.lineage
            size = -(-len(raw) // len(parts))
            built, characters = [], 0
            for ordinal, (derivation, content) in enumerate(parts):
                data = raw.encode()[ordinal * size : (ordinal + 1) * size]
                self.assertTrue(data)
                body = data.decode() if derivation == "identity_utf8" else content
                built.append(
                    type(part).model_validate(
                        part.model_dump()
                        | {
                            "part_id": uuid4(),
                            "ordinal": ordinal,
                            "component_offset": characters,
                            "derivation": derivation,
                            "content": body,
                            "content_sha256": digest(body.encode()),
                            "lineage": [
                                whole.model_dump()
                                | {
                                    "byte_start": whole.byte_start + ordinal * size,
                                    "byte_end_exclusive": whole.byte_start
                                    + ordinal * size
                                    + len(data),
                                    "source_bytes_sha256": digest(data),
                                }
                            ],
                        }
                    )
                )
                characters += len(body)
            fragments[part.part_id] = tuple(built)
            return create(fragments[part.part_id], **updates)

        def appended(package: Any, part: Any, key: str | None = None) -> Any:
            for piece in fragments[part.part_id]:
                result = append(package, piece)
            return result

        captured.__module__ = original.__module__
        with (
            self.r.in_scope(self.scope),
            patch.object(fixture, "part", captured),
            patch.object(fixture, "create", fragmented),
            patch.object(fixture, "append", appended),
        ):
            bead: UUID = fixture.bead(statement, key=key, source=self.scope_source)
        return bead

    def charges(self, reply: dict[str, Any]) -> tuple[Any, ...]:
        """The step's sealed bytes and allocation, as recorded (no time in them)."""
        row = self.db.execute(
            "SELECT o.encoded_bytes,o.allocation_bytes,"
            "octet_length(a.content_bytes) FROM memoriesql.query_deliveries d "
            "JOIN memoriesql.query_steps s USING(tenant_id,run_ref,step_key) "
            "JOIN memoriesql.result_preparation_artifacts a "
            "ON a.tenant_id=s.tenant_id AND a.artifact_ref=s.helper_artifact_ref "
            "JOIN memoriesql.result_preparation_operations o "
            "ON o.tenant_id=a.tenant_id AND o.operation_ref=a.operation_ref "
            "WHERE d.delivery_ref=%s",
            (reply["access_receipt_ref"],),
        ).fetchone()
        assert row is not None
        return tuple(row)

    def test_reads_touch_no_part_row_once_unit_facts_are_sealed(self) -> None:
        # N-2 (sealed-facts review) and case 45's charges. The view reads a unit
        # only through query_unit_package_v1 and the unit records, which sealed
        # unit facts answer from one row: the agent's whole read path then
        # fetches no block of the part heap, its TOAST or any index, whatever
        # the hidden part count. Two identity units of very different hidden
        # size give byte-identical slices and equal bytes and allocation (time
        # is the block count's proxy); two normalized units of equal text in 1
        # and 64 parts read identically.
        facts = next(
            (
                migration.version
                for migration in discover_migrations()
                if migration.name == "sealed_unit_query_facts"
            ),
            None,
        )
        if facts is None:
            self.skipTest("sealed unit facts are not in this stream")
        statement = "Fictional fragmented pears were counted."
        text = "Fictional sealed orchard ledger line. " * 8
        base, extra = divmod(len(text), 64)
        bounds = [i * base + min(i, extra) for i in range(65)]
        chunks = tuple(text[bounds[i] : bounds[i + 1]] for i in range(64))
        self.assertEqual("".join(chunks), text)
        beads = {
            "short": self.fragmented_bead(
                statement,
                "text-frag-a",
                raw=filler("text-frag-a", 64),
                parts=(("identity_utf8", ""),),
            ),
            "long": self.fragmented_bead(
                statement,
                "text-frag-b",
                raw=filler("text-frag-b", 64 * 512),
                parts=(("identity_utf8", ""),) * 64,
            ),
            "one": self.fragmented_bead(
                statement,
                "text-frag-c",
                raw=filler("text-frag-c", 640),
                parts=(("producer_normalized", text),),
            ),
            "many": self.fragmented_bead(
                statement,
                "text-frag-d",
                raw=filler("text-frag-d", 640),
                parts=tuple(("producer_normalized", chunk) for chunk in chunks),
            ),
        }
        self.assertEqual(
            [
                self.scalar(
                    "SELECT count(*) FROM memoriesql.logical_unit_materializations m "
                    "JOIN memoriesql.evidence_package_parts pp "
                    "ON pp.tenant_id=m.tenant_id AND pp.package_id=m.package_id "
                    "WHERE m.bead_id=%s",
                    (bead,),
                )
                for bead in beads.values()
            ],
            [1, 64, 1, 64],
        )
        units = {name: self.unit_of(bead) for name, bead in beads.items()}
        # Settled counters must not include maintenance reads of the parts.
        self.db.execute(
            f"ALTER TABLE {PARTS} SET (autovacuum_enabled=false,"
            "toast.autovacuum_enabled=false)"
        )
        relations = self.part_relations()
        # Sealed unit facts precede the view, so there is no view read without
        # them. Control: the same settled measure sees a finished backend's part
        # reads. Hidden length's record builder, which the facts writer still
        # uses, reads every part row of the 64-part unit.
        self.assertLess(facts, self.view_version)
        before = self.settled_blocks(relations)
        with psycopg.connect(
            make_conninfo(self.fixture.admin, dbname=self.fixture.database),
            autocommit=True,
        ) as probe:
            probe.execute(
                "SELECT memoriesql.query_unit_package_record_v1(%s,%s)",
                (self.fixture.tenant, units["long"]),
            )
        self.assertGreater(self.settled_blocks(relations), before)
        before = self.settled_blocks(relations)
        run = self.r.start(self.agent)
        _, cited = self.cited(run)
        replies: dict[str, tuple[bytes, dict[str, Any]]] = {}
        for name in beads:
            request = self.request(run, self.entry(cited[units[name]], 0, 16))
            replies[name] = self.send(request)
        replayed = self.send(request)
        self.r.close(run, self.agent)
        self.assertEqual(self.settled_blocks(relations), before)
        # Checked only after the window: these read part rows themselves.
        for raw, reply in (*replies.values(), replayed):
            self.available(raw, reply)
        self.assertEqual(replayed[1]["source_text"], replies["many"][1]["source_text"])

        def shown(reply: dict[str, Any]) -> dict[str, Any]:
            (piece,) = reply["source_text"]["slices"]
            return {key: value for key, value in piece.items() if key != "source"}

        def untimed(raw: bytes) -> int:
            """Reply bytes apart from the time-dependent work and remaining figures."""
            reply = json.loads(raw)
            for key in ("work", "remaining"):
                reply.pop(key)
            return len(json.dumps(reply, sort_keys=True))

        (short_raw, short), (long_raw, long_) = replies["short"], replies["long"]
        self.assertEqual(shown(short)["text_state"], "unsupported")
        self.assertEqual(shown(short), shown(long_))
        self.assertEqual(untimed(short_raw), untimed(long_raw))
        self.assertEqual(
            short["work"]["retained_bytes"], long_["work"]["retained_bytes"]
        )
        self.assertEqual(self.charges(short), self.charges(long_))
        one, many = replies["one"][1], replies["many"][1]
        self.assertEqual(shown(one), shown(many))
        self.assertEqual(shown(one)["text"], text[:16])
        self.assertEqual(shown(one)["unit_characters"], len(text))
        self.assertEqual(
            one["source_text"]["source_text_labels"],
            many["source_text"]["source_text_labels"],
        )
