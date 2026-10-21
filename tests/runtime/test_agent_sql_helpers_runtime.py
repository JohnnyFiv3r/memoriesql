"""Fictional installed helper regressions; execute only in an assigned DB slot.

No model/provider is called. Existing canonical fixture producers create accepted
historical input at schema43; the tested executor runs after migrations44–46.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from typing import TYPE_CHECKING, Any
from unittest.mock import patch
from uuid import UUID, uuid4

import psycopg
from psycopg.conninfo import make_conninfo

import memoriesql.infrastructure.postgres.agent_sql_results as results_module

if TYPE_CHECKING:
    from tests.runtime import agent_relation_fixtures as relation_agents
    from tests.runtime import test_agent_sql_results as fixtures
    from tests.runtime.test_postgres_runtime import migrate
else:
    import agent_relation_fixtures as relation_agents
    import test_agent_sql_results as fixtures
    from test_postgres_runtime import migrate


@unittest.skipUnless(
    os.environ.get("N1_TEST_DATABASE_URL"), "assigned disposable PostgreSQL required"
)
class AgentSqlHelperRuntime(unittest.TestCase):
    TEXT = "Fictional normalized harvest: α pears in the orchard. " * 32
    RAW = "Fictional RAW harvest [alpha pears]; original captured bytes. " * 32
    # Retained as one part, newline-joined; each statement is one <=2048-character
    # render clause. Runs of 4-byte and 2-byte characters let a span reach
    # 16 KiB of UTF-8 in far fewer than 16384 characters.
    WIDE = (
        "Fictional astral pears: " + "🍐" * 2024,
        "🍐" * 2048,
        "🍐" * 2048,
        "Fictional accented pears: " + "é" * 2022,
        *("é" * 2048,) * 4,
    )
    WIDE_RAW = "Fictional RAW wide harvest; original captured bytes. " * 320

    def setUp(self) -> None:
        # Compose the established real fixture; do not inherit its entire suite.
        self.r = fixtures.AgentSqlResults(methodName="runTest")
        self.addCleanup(self.r.doCleanups)
        self.r.setUp()
        self.db, self.fixture = self.r.db, self.r.fixture
        self.source, self.target_bead, _ = self.fixture.assertion()
        normalized_scope, normalized_source = relation_agents.explicit_scope(
            self.db, self.fixture
        )
        self.normalized = self.r.normalized_bead(
            self.TEXT,
            "helper-normalized",
            raw=self.RAW,
            source=normalized_source,
            scope=normalized_scope,
        )
        self.wide = self.r.normalized_bead(
            self.WIDE[0],
            "helper-wide",
            raw=self.WIDE_RAW,
            source=normalized_source,
            scope=normalized_scope,
            more=self.WIDE[1:],
        )
        capabilities = ["memory.inspect", "memory.query", "source.read"]
        grant, self.agent = self.r.home_agent(capabilities)
        self.relation = relation_agents.assessed_relation_for_agent(
            self.db, self.fixture, self.r.paired_principal(grant)
        )
        # No legacy7 activation is permitted after this point. Accepted old
        # meaning and its authorized inspection/hydration remain test inputs.
        migrate(self.db, expected_current_version=43, target_version=46)

    def scalar(self, statement: str, values: tuple[Any, ...] = ()) -> Any:
        return self.r.h.scalar(statement, values)

    def target(self, bead: UUID) -> dict[str, str]:
        version = self.scalar(
            "SELECT bead_version_id::text FROM memoriesql.accepted_bead_semantics "
            "WHERE bead_id=%s",
            (bead,),
        )
        self.assertIsNotNone(version)
        return {"bead_id": str(bead), "bead_version_id": str(version)}

    def inspection(
        self,
        run: dict[str, Any],
        *,
        beads: tuple[UUID, ...] | None = None,
        facets: tuple[str, ...] = ("content",),
        relations: tuple[UUID, ...] = (),
    ) -> dict[str, Any]:
        return {
            "contract_version": 1,
            "run_ref": run["run_ref"],
            "step_key": str(uuid4()),
            "kind": "inspect",
            "observation_refs": [
                self.target(bead)
                for bead in ((self.source,) if beads is None else beads)
            ],
            "relation_refs": [str(relation) for relation in relations],
            "result": None,
            "provenance_ref": None,
            "facets": list(facets),
            "view": "historical",
            "known_at": run["default_known_at"],
            "cursor": None,
        }

    def send(
        self, request: dict[str, Any], *, secret: str | None = None, control: Any = None
    ) -> tuple[bytes, dict[str, Any]]:
        raw = self.r.service(secret, control=control).handle(
            json.dumps(request).encode()
        )
        return raw, json.loads(raw)

    def available(self, raw: bytes, reply: dict[str, Any]) -> None:
        self.assertEqual(reply["outcome"], "available", reply)
        self.assertIsNone(reply.get("error"))
        self.assertIsNotNone(reply["receipt_ref"])
        self.assertIsNotNone(reply["access_receipt_ref"])
        # Inspect/hydrate have a helper artifact; no invented saved result ID.
        self.assertNotIn("result", reply)
        row = self.db.execute(
            "SELECT d.state,d.outcome,d.charged_db_ms,d.charged_transport,"
            "x.result_id,x.content_digest,x.response_sha256,x.delivered_refs,"
            "s.helper_artifact_ref,a.content_bytes FROM memoriesql.query_deliveries d "
            "JOIN memoriesql.query_disclosures x USING(tenant_id,delivery_ref) "
            "JOIN memoriesql.query_steps s USING(tenant_id,run_ref,step_key) "
            "JOIN memoriesql.result_preparation_artifacts a "
            "ON a.tenant_id=s.tenant_id AND a.artifact_ref=s.helper_artifact_ref "
            "WHERE d.delivery_ref=%s",
            (reply["access_receipt_ref"],),
        ).fetchone()
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row[:2], ("settled", "available"))
        self.assertEqual(row[2], reply["work"]["charged_ms"])
        self.assertEqual(row[3], len(raw))
        self.assertIsNone(row[4])
        self.assertEqual(row[5], hashlib.sha256(bytes(row[9])).hexdigest())
        self.assertEqual(row[6], hashlib.sha256(raw).hexdigest())
        self.assertEqual(
            set(row[7]),
            {
                json.dumps(
                    ref, ensure_ascii=True, sort_keys=True, separators=(",", ":")
                )
                for ref in reply["visibility"]["new_refs"]
                + reply["visibility"]["previous_refs"]
            },
        )

    def pins(
        self, run: dict[str, Any], bead: UUID | None = None
    ) -> list[dict[str, Any]]:
        request = self.inspection(
            run, beads=(bead or self.normalized,), facets=("evidence_metadata",)
        )
        raw, reply = self.send(request)
        self.available(raw, reply)
        pins: list[dict[str, Any]] = reply["inspection"]["evidence_pins"]
        self.assertTrue(pins)
        self.assertTrue(all(pin["part"] is not None for pin in pins))
        return pins

    def hydration(
        self, run: dict[str, Any], pin: dict[str, Any], *, pages: bool = False
    ) -> dict[str, Any]:
        return {
            "contract_version": 1,
            "run_ref": run["run_ref"],
            "step_key": str(uuid4()),
            "kind": "hydrate_source",
            "selections": [
                {
                    "evidence_ref": pin["ref"],
                    "part_id": pin["part"]["part_id"],
                    "representation": "normalized_text",
                    "lineage_ordinal": None,
                    "start": ordinal * 32,
                    "end": ordinal * 32 + 512,
                    "origin": None,
                }
                for ordinal in range(8 if pages else 1)
            ],
            "max_bytes": 65536,
            "cursor": None,
        }

    def span(self, text: str, size: int, *, after: int = 0) -> tuple[int, int]:
        """The first character interval from `after` of exactly `size` UTF-8 bytes."""
        for start in range(after, len(text)):
            end, used = start, 0
            while used < size and end < len(text):
                used += len(text[end].encode())
                end += 1
            if used == size:
                return start, end
        raise AssertionError("fixture text has no such span")

    def test_inspection_receipt_and_exact_replay_reuse_one_original_artifact(
        self,
    ) -> None:
        run = self.r.start()
        request = self.inspection(run)
        raw, first = self.send(request)
        self.available(raw, first)
        self.assertEqual(len(first["inspection"]["observations"]), 1)
        self.assertEqual(
            first["inspection"]["observations"][0]["observation"]["bead_id"],
            str(self.source),
        )
        artifacts = self.scalar(
            "SELECT count(*) FROM memoriesql.result_preparation_artifacts"
        )
        invocations = self.r.invocations()
        raw, again = self.send(request)
        self.available(raw, again)
        self.assertEqual(again["inspection"], first["inspection"])
        self.assertEqual(again["receipt_ref"], first["receipt_ref"])
        self.assertNotEqual(again["access_receipt_ref"], first["access_receipt_ref"])
        self.assertEqual(again["next_cursor"], first["next_cursor"])
        self.assertEqual(
            again["remaining"]["accesses"], first["remaining"]["accesses"] - 1
        )
        self.assertEqual(
            self.scalar("SELECT count(*) FROM memoriesql.result_preparation_artifacts"),
            artifacts,
        )
        self.assertEqual(self.r.invocations(), invocations)
        self.assertEqual(
            self.scalar(
                "SELECT count(DISTINCT helper_artifact_ref) FROM memoriesql.query_steps"
            ),
            1,
        )

    def test_am5_content_reads_use_agent_mode_and_denied_history_is_nondisclosing(
        self,
    ) -> None:
        run = self.r.start(self.agent)
        request = self.inspection(run, beads=(), relations=(self.relation.relation_id,))
        raw, shown = self.send(request, secret=self.agent)
        self.available(raw, shown)
        relations = shown["inspection"]["relations"]
        self.assertEqual(
            [relation["relation_id"] for relation in relations],
            [str(self.relation.relation_id)],
        )
        self.assertTrue(all(relation["events"] == [] for relation in relations))
        request["step_key"] = str(uuid4())
        request["facets"] = ["content", "lifecycle"]
        _, refused = self.send(request, secret=self.agent)
        self.assertEqual(
            (refused["outcome"], refused["error"]),
            ("unavailable", {"code": "unavailable"}),
        )
        for field in ("inspection", "hydration", "visibility", "result"):
            self.assertNotIn(field, refused)
        self.assertNotIn(str(self.relation.relation_id), json.dumps(refused))
        self.assertEqual(
            self.scalar("SELECT count(*) FROM memoriesql.query_disclosures"), 1
        )

    def test_revoked_closure_matches_missing_target_and_never_partially_discloses(
        self,
    ) -> None:
        run = self.r.start()
        request = self.inspection(run, beads=(self.source, self.normalized))
        raw, first = self.send(request)
        self.available(raw, first)
        before = self.scalar("SELECT count(*) FROM memoriesql.query_disclosures")
        artifact_hashes = self.db.execute(
            "SELECT artifact_ref,artifact_sha256 FROM memoriesql.result_preparation_artifacts ORDER BY artifact_ref"
        ).fetchall()
        self.db.execute(
            "UPDATE memoriesql.protected_resources SET status='revoked',revoked_at=clock_timestamp() "
            "WHERE resource_kind='source' AND resource_id=("
            "SELECT e.source_object_id FROM memoriesql.beads b JOIN memoriesql.source_events e "
            "ON e.tenant_id=b.tenant_id AND e.event_id=b.event_id WHERE b.bead_id=%s)",
            (self.normalized,),
        )
        _, retained = self.send(request)
        missing = self.inspection(run)
        missing["observation_refs"] = [
            {"bead_id": str(uuid4()), "bead_version_id": str(uuid4())}
        ]
        _, unknown = self.send(missing)
        fresh = self.inspection(run, beads=(self.source, self.normalized))
        _, denied = self.send(fresh)
        for reply in (retained, denied, unknown):
            self.assertEqual(
                (reply["outcome"], reply["error"]),
                ("unavailable", {"code": "unavailable"}),
            )
            self.assertEqual(set(reply), set(unknown))
            for field in ("inspection", "hydration", "visibility", "result"):
                self.assertNotIn(field, reply)
            text = json.dumps(reply)
            self.assertNotIn(str(self.source), text)
            self.assertNotIn(str(self.normalized), text)
            self.assertNotIn(
                first["inspection"]["observations"][0]["observation"][
                    "bead_version_id"
                ],
                text,
            )
            self.assertNotIn(self.TEXT, text)
            self.assertNotIn(self.RAW, text)
        self.assertEqual(
            self.scalar("SELECT count(*) FROM memoriesql.query_disclosures"), before
        )
        self.assertEqual(
            self.db.execute(
                "SELECT artifact_ref,artifact_sha256 FROM memoriesql.result_preparation_artifacts ORDER BY artifact_ref"
            ).fetchall(),
            artifact_hashes,
        )

    def test_hydration_reads_pinned_normalized_characters_and_original_raw_bytes(
        self,
    ) -> None:
        run = self.r.start()
        pin = self.pins(run)[0]
        request = self.hydration(run, pin)
        raw, normalized = self.send(request)
        self.available(raw, normalized)
        selection = normalized["hydration"]["slices"][0]
        self.assertEqual(selection["content"], self.TEXT[:512])
        self.assertIsNone(selection["bytes_base64"])
        self.assertEqual(
            selection["returned_sha256"],
            hashlib.sha256(self.TEXT[:512].encode()).hexdigest(),
        )
        request["step_key"] = str(uuid4())
        request["selections"][0].update(
            representation="raw_bytes", lineage_ordinal=0, start=3, end=43
        )
        raw, original = self.send(request)
        self.available(raw, original)
        selection = original["hydration"]["slices"][0]
        returned = base64.b64decode(selection["bytes_base64"], validate=True)
        self.assertEqual(returned, self.RAW.encode()[3:43])
        self.assertEqual(
            selection["returned_sha256"], hashlib.sha256(returned).hexdigest()
        )
        self.assertIsNone(selection["content"])

    def test_hydration_through_a_result_carries_that_results_one_access_basis(
        self,
    ) -> None:
        run = self.r.start()
        _, result = self.r.query(
            run,
            "SELECT s.statement_id,s.bead_id,e.evidence_ref FROM memory_v1.statements s "
            "JOIN memory_v1.statement_sources e ON e.statement_id=s.statement_id "
            "ORDER BY s.statement_id,e.evidence_ref",
            page_size=50,
        )
        self.assertEqual(result["outcome"], "available", result)
        columns = [c["name"] for c in result["result"]["result_schema"]]
        row = next(
            r
            for r in result["page"]["rows"]
            if r["values"][columns.index("bead_id")] == str(self.normalized)
        )
        statement = row["values"][columns.index("statement_id")]
        source_ref = next(
            ref for ref in row["evidence_refs"] if ref["kind"] == "source"
        )
        # Associate the part by exact statement/unit/content pins, never by
        # guessing that a direct evidence UUID was delivered with this result.
        pin = next(
            p
            for p in self.pins(run)
            if p["statement_id"] == statement
            and p["unit"]["source_unit_id"] == source_ref["unit_ref"]
            and p["unit"]["content_sha256"] == source_ref["content_sha256"]
        )
        origin = {
            "result": {k: result["result"][k] for k in ("result_id", "content_digest")},
            "access": {"kind": "standalone"},
        }
        request = self.hydration(run, pin)
        request["selections"] = [
            dict(
                request["selections"][0],
                evidence_ref=source_ref["ref"],
                origin=origin,
                start=start,
                end=start + 64,
            )
            for start in (0, 128)
        ]
        raw, hydrated = self.send(request)
        self.available(raw, hydrated)
        self.assertEqual(
            [part["content"] for part in hydrated["hydration"]["slices"]],
            [self.TEXT[0:64], self.TEXT[128:192]],
        )
        # Exactly the basis the result itself reported, once for both spans;
        # only its check time is this reply's.
        self.assertEqual(
            [
                {k: v for k, v in basis.items() if k != "checked_at"}
                for basis in hydrated["access_bases"]
            ],
            [
                {k: v for k, v in basis.items() if k != "checked_at"}
                for basis in result["access_bases"]
            ],
        )
        self.assertEqual(len(hydrated["access_bases"]), 1)

    def test_normalized_spans_are_at_most_16_kib_of_utf8_not_only_characters(
        self,
    ) -> None:
        run = self.r.start()
        pin = self.pins(run, self.wide)[0]
        text = "\n".join(self.WIDE)
        astral = self.span(text, 16384)
        accented = self.span(text, 16384, after=text.index("é"))
        over = self.span(text, 16385, after=text.index("é"))
        self.assertIn("🍐", text[slice(*astral)])
        self.assertNotIn("🍐", text[slice(*accented)] + text[slice(*over)])
        for start, end in (astral, accented):
            request = self.hydration(run, pin)
            request["selections"][0].update(start=start, end=end)
            raw, reply = self.send(request)
            self.available(raw, reply)
            (selection,) = reply["hydration"]["slices"]
            self.assertEqual(selection["content"], text[start:end])
            self.assertEqual(len(selection["content"].encode()), 16384)
            self.assertLess(end - start, 16384)
            self.assertLessEqual(len(raw), 65536)
        disclosures = self.scalar("SELECT count(*) FROM memoriesql.query_disclosures")
        # Within the character interval, but one character or byte past 16 KiB.
        for start, end in (over, (astral[0], astral[1] + 1)):
            request = self.hydration(run, pin)
            request["selections"][0].update(start=start, end=end)
            _, refused = self.send(request)
            self.assertEqual(
                (refused["outcome"], refused["error"]),
                ("invalid_request", {"code": "binding"}),
                refused,
            )
            self.assertNotIn("hydration", refused)
        self.assertEqual(
            self.scalar("SELECT count(*) FROM memoriesql.query_disclosures"),
            disclosures,
        )
        # Raw spans keep their byte interval: 16384 bytes, never 16385.
        request = self.hydration(run, pin)
        request["selections"][0].update(
            representation="raw_bytes", lineage_ordinal=0, start=0, end=16384
        )
        raw, original = self.send(request)
        self.available(raw, original)
        returned = base64.b64decode(
            original["hydration"]["slices"][0]["bytes_base64"], validate=True
        )
        self.assertEqual(returned, self.WIDE_RAW.encode()[:16384])
        request = dict(request, step_key=str(uuid4()))
        request["selections"] = [dict(request["selections"][0], end=16385)]
        _, refused = self.send(request)
        self.assertEqual(refused["outcome"], "invalid_request", refused)

    def test_hydration_replies_are_at_most_64_kib_encoded_and_page_whole_spans(
        self,
    ) -> None:
        run = self.r.start()
        pin = self.pins(run, self.wide)[0]
        request = self.hydration(run, pin)
        # 64 KiB of raw content is about 85 KiB as base64: under the general
        # 256 KiB cap it would be one reply.
        request["selections"] = [
            dict(
                request["selections"][0],
                representation="raw_bytes",
                lineage_ordinal=0,
                start=ordinal * 64,
                end=ordinal * 64 + 16384,
            )
            for ordinal in range(4)
        ]
        original = dict(request)
        raw, page = self.send(request)
        self.available(raw, page)
        sizes = [len(raw)]
        slices = list(page["hydration"]["slices"])
        self.assertTrue(page["has_more"], page["work"])
        for _ in range(4):
            if not page["has_more"]:
                break
            request = dict(original, step_key=str(uuid4()), cursor=page["next_cursor"])
            raw, page = self.send(request)
            self.available(raw, page)
            sizes.append(len(raw))
            slices.extend(page["hydration"]["slices"])
        self.assertFalse(page["has_more"])
        self.assertTrue(all(size <= 65536 for size in sizes), sizes)
        self.assertEqual([part["start"] for part in slices], [0, 64, 128, 192])
        self.assertEqual(
            [base64.b64decode(part["bytes_base64"]) for part in slices],
            [self.WIDE_RAW.encode()[i * 64 : i * 64 + 16384] for i in range(4)],
        )
        self.assertEqual(
            self.scalar(
                "SELECT array_agg(charged_transport ORDER BY delivery_ref) "
                "FROM memoriesql.query_deliveries d JOIN memoriesql.query_steps s "
                "USING(tenant_id,run_ref,step_key) WHERE s.kind='hydrate_source'"
            ),
            sizes,
        )

    def test_hydration_revocation_precedes_offsets_and_withholds_every_selection(
        self,
    ) -> None:
        run = self.r.start()
        private = self.pins(run)[0]
        raw, reply = self.send(self.inspection(run, facets=("evidence_metadata",)))
        self.available(raw, reply)
        visible = reply["inspection"]["evidence_pins"][0]
        request = self.hydration(run, visible)
        request["selections"][0]["end"] = 20
        second = dict(self.hydration(run, private)["selections"][0], end=20)
        request["selections"].append(second)
        raw, first = self.send(request)
        self.available(raw, first)
        self.assertEqual(len(first["hydration"]["slices"]), 2)
        receipts = self.scalar("SELECT count(*) FROM memoriesql.query_disclosures")
        artifacts = self.scalar(
            "SELECT count(*) FROM memoriesql.result_preparation_artifacts"
        )
        self.db.execute(
            "UPDATE memoriesql.protected_resources SET status='revoked',revoked_at=clock_timestamp() "
            "WHERE resource_kind='source' AND resource_id=("
            "SELECT e.source_object_id FROM memoriesql.beads b JOIN memoriesql.source_events e "
            "ON e.tenant_id=b.tenant_id AND e.event_id=b.event_id WHERE b.bead_id=%s)",
            (self.normalized,),
        )
        _, retained = self.send(request)
        invalid = dict(request, step_key=str(uuid4()))
        invalid["selections"] = [
            request["selections"][0],
            dict(second, start=100000, end=100001),
        ]
        _, denied = self.send(invalid)
        missing = dict(invalid, step_key=str(uuid4()))
        missing["selections"] = [
            request["selections"][0],
            dict(second, evidence_ref=str(uuid4())),
        ]
        _, unknown = self.send(missing)
        for refusal in (retained, denied, unknown):
            self.assertEqual(
                (refusal["outcome"], refusal["error"]),
                ("unavailable", {"code": "unavailable"}),
            )
            self.assertEqual(set(refusal), set(unknown))
            for field in ("hydration", "inspection", "visibility", "result"):
                self.assertNotIn(field, refusal)
            self.assertNotIn(private["ref"], json.dumps(refusal))
            self.assertNotIn(visible["ref"], json.dumps(refusal))
        self.assertEqual(
            self.scalar("SELECT count(*) FROM memoriesql.query_disclosures"), receipts
        )
        self.assertEqual(
            self.scalar("SELECT count(*) FROM memoriesql.result_preparation_artifacts"),
            artifacts,
        )

    def test_cursor_pages_share_one_artifact_and_expiry_purges_all_step_pins(
        self,
    ) -> None:
        run = self.r.start()
        pin = self.pins(run)[0]
        request = self.hydration(run, pin, pages=True)
        original = dict(request)
        before = self.scalar(
            "SELECT count(*) FROM memoriesql.result_preparation_artifacts"
        )
        slices: list[dict[str, Any]] = []
        with patch.object(results_module, "MAX_RESPONSE_BYTES", 8192):
            raw, first = self.send(request)
            self.available(raw, first)
            self.assertTrue(first["has_more"], first)
            self.assertIsNotNone(first["next_cursor"])
            raw, replay = self.send(original)
            self.available(raw, replay)
            self.assertEqual(replay["hydration"], first["hydration"])
            self.assertEqual(replay["next_cursor"], first["next_cursor"])
            page = first
            slices.extend(page["hydration"]["slices"])
            cursors: set[str] = set()
            for _ in range(8):
                if not page["has_more"]:
                    break
                self.assertNotIn(page["next_cursor"], cursors)
                cursors.add(page["next_cursor"])
                request = dict(
                    original, step_key=str(uuid4()), cursor=page["next_cursor"]
                )
                raw, page = self.send(request)
                self.available(raw, page)
                slices.extend(page["hydration"]["slices"])
            self.assertFalse(page["has_more"], page)
        self.assertEqual(len(slices), 8)
        self.assertEqual([part["start"] for part in slices], [i * 32 for i in range(8)])
        self.assertEqual(
            self.scalar("SELECT count(*) FROM memoriesql.result_preparation_artifacts"),
            before + 1,
        )
        self.assertEqual(
            self.scalar(
                "SELECT count(DISTINCT helper_artifact_ref) FROM memoriesql.query_steps WHERE kind='hydrate_source'"
            ),
            1,
        )
        self.assertGreater(
            self.scalar("SELECT count(*) FROM memoriesql.query_helper_cursors"), 0
        )
        self.r.age(run["run_ref"], timedelta(minutes=31), results=False)
        cleanup = json.loads(self.r.service().cleanup_expired())
        self.assertEqual(cleanup["outcome"], "available", cleanup)
        self.assertEqual(cleanup["cleanup"]["purged"]["runs"], 1)
        for table in (
            "query_helper_cursors",
            "query_disclosures",
            "query_visible_refs",
            "result_preparation_artifacts",
        ):
            self.assertEqual(
                self.scalar("SELECT count(*) FROM memoriesql." + table), 0, table
            )
        self.assertEqual(
            self.scalar(
                "SELECT count(helper_artifact_ref) FROM memoriesql.query_steps"
            ),
            0,
        )
        self.assertEqual(
            self.scalar(
                "SELECT count(*) FROM memoriesql.query_steps WHERE purged_at IS NULL"
            ),
            0,
        )
        self.assertEqual(
            self.scalar(
                "SELECT count(*) FROM memoriesql.result_preparation_operations WHERE state<>'discarded' OR allocation_bytes<>8192"
            ),
            0,
        )

    def test_server_statement_cancellation_rolls_back_helper_publication_and_settles(
        self,
    ) -> None:
        run = self.r.start()
        request = self.inspection(run)
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
                    # The real disclosure/ref/accounting writes happened, but
                    # are still uncommitted. A REAL server cancellation must
                    # roll them back together with the preparation/artifact.
                    super().execute("SET LOCAL statement_timeout='100ms'")
                    super().execute("SELECT pg_sleep(1)")
                return result

        def control() -> psycopg.Connection[Any]:
            return CancelAfterRecord.connect(
                make_conninfo(self.fixture.admin, dbname=self.fixture.database),
                autocommit=True,
            )

        _, cancelled = self.send(request, control=control)
        self.assertEqual(fired, ["statement"])
        self.assertEqual(
            (cancelled["outcome"], cancelled["error"]),
            ("budget_exhausted", {"code": "time"}),
        )
        for table in (
            "query_disclosures",
            "query_visible_refs",
            "result_preparation_artifacts",
            "result_preparation_operations",
        ):
            self.assertEqual(
                self.scalar("SELECT count(*) FROM memoriesql." + table), 0, table
            )
        self.assertEqual(
            self.scalar(
                "SELECT count(*) FROM memoriesql.query_deliveries WHERE state<>'settled'"
            ),
            0,
        )
        self.assertEqual(
            self.scalar(
                "SELECT state FROM memoriesql.query_steps WHERE run_ref=%s AND step_key=%s",
                (run["run_ref"], request["step_key"]),
            ),
            "failed",
        )
        _, again = self.send(request)
        self.assertEqual(again["outcome"], cancelled["outcome"])
        self.assertEqual(again["error"], cancelled["error"])
        self.assertEqual(self.r.service().recover_abandoned(), 0)
        raw, fresh = self.send(self.inspection(run))
        self.available(raw, fresh)
        self.assertEqual(self.r.invocations(), 0)

    def test_lost_commit_ack_keeps_actual_disclosure_and_replays_without_repreparing(
        self,
    ) -> None:
        run = self.r.start()
        request = self.inspection(run)
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
        self.assertEqual(len(lost), 1)
        self.assertEqual(
            (pending["outcome"], pending["error"]),
            ("settlement_pending", {"code": "settlement"}),
        )
        self.assertNotIn("inspection", pending)
        self.assertEqual(
            self.scalar("SELECT count(*) FROM memoriesql.query_disclosures"), 1
        )
        self.assertEqual(
            self.scalar("SELECT state FROM memoriesql.query_steps"), "complete"
        )
        self.assertEqual(
            self.scalar(
                "SELECT count(*) FROM memoriesql.query_deliveries WHERE state<>'settled'"
            ),
            0,
        )
        original = self.db.execute(
            "SELECT artifact_ref,artifact_sha256,content_bytes,witness_bytes,dependency_bytes "
            "FROM memoriesql.result_preparation_artifacts"
        ).fetchall()
        trace: list[str] = []

        class Traced(psycopg.Connection[Any]):
            def execute(self, *args: Any, **kwargs: Any) -> Any:
                trace.append(str(args[0]))
                return super().execute(*args, **kwargs)

        def replay_control() -> psycopg.Connection[Any]:
            return Traced.connect(
                make_conninfo(self.fixture.admin, dbname=self.fixture.database),
                autocommit=True,
            )

        raw, replay = self.send(request, control=replay_control)
        self.available(raw, replay)
        self.assertTrue(
            any(
                s.startswith("SELECT memoriesql.read_query_helper_artifact_v1(")
                for s in trace
            )
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
        self.assertEqual(replay["receipt_ref"], pending["receipt_ref"])
        self.assertNotEqual(replay["access_receipt_ref"], pending["access_receipt_ref"])
        self.assertEqual(
            self.db.execute(
                "SELECT artifact_ref,artifact_sha256,content_bytes,witness_bytes,dependency_bytes "
                "FROM memoriesql.result_preparation_artifacts"
            ).fetchall(),
            original,
        )
        self.assertEqual(
            self.scalar("SELECT count(*) FROM memoriesql.query_disclosures"), 2
        )
        self.assertEqual(self.r.invocations(), 0)
