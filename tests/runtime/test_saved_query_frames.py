"""Fictional saved-frame races and whole-current-authority refusals.

The original seven-probe private reproduction is preserved unchanged. These
additive tests score saved frames separately from fresh conservative root
semantics (approved L4/AM-5; TG01–08). Run only in an assigned DB slot.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import unittest
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import patch
from uuid import UUID, uuid4

from psycopg import sql
from psycopg.types.json import Jsonb

if TYPE_CHECKING:
    from tests.runtime import agent_relation_fixtures as relation_agents
    from tests.runtime import test_agent_sql_results as fixtures
    from tests.runtime.test_postgres_runtime import migrate
else:
    import agent_relation_fixtures as relation_agents
    import test_agent_sql_results as fixtures
    from test_postgres_runtime import migrate

from memoriesql.application.assessed_relation_lifecycle import (
    AssessedRelationEventReceipt,
)
from memoriesql.application.relation_lifecycle import (
    RecordRelationEvent,
    RelationAction,
)
from memoriesql.infrastructure.postgres.authorization import PostgresAuthorizationPort


@unittest.skipUnless(
    os.environ.get("N1_TEST_DATABASE_URL"), "assigned disposable DB required"
)
class SavedQueryFrames(unittest.TestCase):
    def setUp(self) -> None:
        self.r = fixtures.AgentSqlResults(methodName="runTest")
        self.r.setUp()
        if self._testMethodName != "test_schema43_result_survives_schema44_upgrade":
            migrate(self.r.db, expected_current_version=43, target_version=44)
        self.addCleanup(self.r.doCleanups)
        self.record: dict[str, Any] = {
            "case": self.id(),
            "replies": {},
            "deliveries": {},
        }
        self.addCleanup(self.write_record)

    def write_record(self) -> None:
        path = os.environ.get("L1_PROBE_RECORD")
        if path:
            with Path(path).open("a") as stream:
                stream.write(
                    json.dumps(self.record, sort_keys=True, default=str) + "\n"
                )

    def deliveries(self, run: dict[str, Any], label: str) -> None:
        rows = self.r.db.execute(
            "SELECT delivery_number,state,reserved_db_ms,charged_db_ms,"
            "reserved_transport,charged_transport,outcome,response_sha256 "
            "FROM memoriesql.query_deliveries WHERE run_ref=%s ORDER BY admitted_at,delivery_ref",
            (UUID(run["run_ref"]),),
        ).fetchall()
        self.record["deliveries"][label] = rows

    def artifact(self, result: dict[str, Any]) -> dict[str, str]:
        row = self.r.db.execute(
            "SELECT content_bytes,witness_bytes,dependency_bytes "
            "FROM memoriesql.result_preparation_artifacts WHERE artifact_ref=%s",
            (UUID(result["result_id"]),),
        ).fetchone()
        assert row is not None, "saved artifact absent"
        return {
            name: hashlib.sha256(bytes(value)).hexdigest()
            for name, value in zip(
                ("content", "witness", "dependencies"), row, strict=True
            )
        }

    def saved(
        self, secret: str | None, text: str | None = None
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        run = self.r.start(secret)
        request, reply = self.r.query(
            run, text or self.r.HIDDEN_HISTORY_QUERY, page_size=50, secret=secret
        )
        self.record["run"] = run
        self.record["replies"]["saved"] = reply
        self.assertEqual(reply["outcome"], "available", reply)
        self.assertTrue(reply["page"]["rows"], reply)
        self.record["artifact_before"] = self.artifact(reply["result"])
        before = self.r.reuse(run, reply["result"], page_size=50, secret=secret)
        self.record["replies"]["reuse_before"] = before
        self.assertEqual(before["outcome"], "available", before)
        self.deliveries(run, "before")
        return run, request, reply

    def after(
        self,
        run: dict[str, Any],
        request: dict[str, Any],
        saved: dict[str, Any],
        secret: str | None,
        *,
        revoked: bool = False,
    ) -> None:
        # Capture every observable surface before assertions; failed reuse must
        # not hide the fresh-frame or budget evidence from the reproduction.
        invocations = self.r.invocations()
        again = self.r.send(request, secret=secret)
        reused = self.r.reuse(run, saved["result"], page_size=50, secret=secret)
        self.record["saved_access_selects"] = self.r.invocations() - invocations
        self.assertEqual(self.record["saved_access_selects"], 0)
        _, fresh = self.r.query(
            run, self.r.HIDDEN_HISTORY_QUERY, page_size=50, secret=secret
        )
        self.record["replies"].update(
            redelivery_after=again, reuse_after=reused, fresh_after=fresh
        )
        self.record["artifact_after"] = self.artifact(saved["result"])
        self.deliveries(run, "after")
        self.r.close(run, secret)
        self.assertEqual(self.record["artifact_after"], self.record["artifact_before"])
        for label, reply in (("redelivery", again), ("reuse", reused)):
            if revoked:
                self.assertEqual(reply["outcome"], "unavailable", (label, reply))
                self.assertNotIn("result", reply)
                self.assertNotIn("page", reply)
            else:
                self.assertEqual(reply["outcome"], "available", (label, reply))
                self.assertEqual(reply["result"], saved["result"], label)
                self.assertEqual(reply["page"]["rows"], saved["page"]["rows"], label)

    def held_event(
        self,
        action: RelationAction,
        *,
        commit: bool,
        visible: bool = False,
        protected_replacement: bool = False,
        same_source_bead: bool = False,
    ) -> None:
        agent, change = (
            self.same_bead_history()
            if same_source_bead
            else self.r.hidden_history(action)
        )
        commands: list[Any] = []
        with patch.object(
            self.r.fixture.lifecycle,
            "record_relation_event",
            side_effect=commands.append,
        ):
            change()
        self.assertEqual(len(commands), 1)
        command = commands[0]
        if protected_replacement:
            scope, source = relation_agents.explicit_scope(self.r.db, self.r.fixture)
            target = self.r.h.scalar(
                "SELECT target_bead_id FROM memoriesql.bead_relations WHERE relation_id=%s",
                (command.relation_id,),
            )
            replacement: dict[str, str] = {}
            with self.r.in_scope(scope):
                bead = self.r.fixture.author(
                    "Fictional replacement from an unreadable scope.",
                    "protected-replacement-" + uuid4().hex[:8],
                    candidates=(target,),
                    source=source,
                    plan=lambda extras, bead, found: replacement.setdefault(
                        "id",
                        self.r.fixture.relate(extras, bead, found[0], "derived_from"),
                    ),
                )
            command = command.model_copy(
                update={"replacement_relation_id": UUID(replacement["id"])}
            )
            self.record["new_protected_ids"] = [str(bead), replacement["id"]]
        if action == "supersede" and not protected_replacement:
            replacement_row = self.r.db.execute(
                "SELECT a.source_bead_id=b.source_bead_id, av.access_scope_id=bv.access_scope_id, "
                "ae.source_object_id=be.source_object_id FROM memoriesql.bead_relations a "
                "JOIN memoriesql.bead_relations b ON b.tenant_id=a.tenant_id AND b.relation_id=%s "
                "JOIN memoriesql.beads av ON av.tenant_id=a.tenant_id AND av.bead_id=a.source_bead_id "
                "JOIN memoriesql.beads bv ON bv.tenant_id=b.tenant_id AND bv.bead_id=b.source_bead_id "
                "JOIN memoriesql.source_events ae ON ae.tenant_id=av.tenant_id AND ae.event_id=av.event_id "
                "JOIN memoriesql.source_events be ON be.tenant_id=bv.tenant_id AND be.event_id=bv.event_id "
                "WHERE a.relation_id=%s",
                (command.replacement_relation_id, command.relation_id),
            ).fetchone()
            self.assertEqual(replacement_row, (same_source_bead, True, True))
        secret = None if visible else agent
        with self.r.fixture.connection() as writer:
            with writer.transaction(force_rollback=not commit):
                writer.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
                writer.execute("SET LOCAL statement_timeout='2s'")
                writer.execute("SET LOCAL lock_timeout='500ms'")
                writer.execute("SET LOCAL ROLE memoriesql_application")
                PostgresAuthorizationPort(writer).begin_context(
                    credential_sha256=self.r.fixture.secret_hash,
                    requested_workspace_id=self.r.fixture.workspace,
                )
                receipt = writer.execute(
                    "SELECT memoriesql.record_relation_event_v1(%s)",
                    (Jsonb(command.model_dump(mode="json")),),
                ).fetchone()[0]
                # Fixture admin audit only; the event itself used the canonical
                # authorized function and its real clock, with no extra grant.
                writer.execute("RESET ROLE")
                stamp = writer.execute(
                    "SELECT recorded_at FROM memoriesql.bead_relation_events WHERE relation_event_id=%s",
                    (UUID(receipt["relation_event_id"]),),
                ).fetchone()[0]
                self.record.update(
                    event=receipt,
                    event_recorded_at=stamp,
                    writer_backend=writer.info.backend_pid,
                    event_committed=commit,
                    observer="owner" if visible else "agent",
                )
                # An independent connection cannot see the still-open write.
                self.assertEqual(
                    self.r.h.scalar(
                        "SELECT count(*) FROM memoriesql.bead_relation_events WHERE relation_event_id=%s",
                        (UUID(receipt["relation_event_id"]),),
                    ),
                    0,
                )
                run, request, saved = self.saved(secret)
                if same_source_bead:
                    self.assertEqual(
                        saved["page"]["rows"][0]["values"][1:],
                        ["active", "qualified", "2"],
                    )
                self.assertLessEqual(
                    stamp,
                    datetime.fromisoformat(
                        run["default_known_at"].replace("Z", "+00:00")
                    ),
                )
                self.record["commit_boundary_utc"] = self.r.h.scalar(
                    "SELECT clock_timestamp()"
                )
        found = self.r.h.scalar(
            "SELECT count(*) FROM memoriesql.bead_relation_events WHERE relation_event_id=%s",
            (UUID(receipt["relation_event_id"]),),
        )
        self.assertEqual(found, int(commit))
        self.after(run, request, saved, secret)
        if protected_replacement:
            self.assertEqual(self.record["replies"]["fresh_after"]["page"]["rows"], [])
            self.fresh_family(
                agent, "protected_replacement", known_at=run["default_known_at"]
            )
            for identifier in self.record["new_protected_ids"]:
                for label in (
                    "saved",
                    "redelivery_after",
                    "reuse_after",
                    "fresh_after",
                ):
                    self.assertNotIn(
                        identifier, json.dumps(self.record["replies"][label])
                    )
        if commit and not protected_replacement and action in ("retract", "supersede"):
            fresh = self.record["replies"]["fresh_after"]
            self.assertEqual(fresh["outcome"], "available", fresh)
            self.assertEqual(len(fresh["page"]["rows"]), 1)
            values = fresh["page"]["rows"][0]["values"]
            self.assertEqual(values[0], saved["page"]["rows"][0]["values"][0])
            self.assertEqual(
                values[1:],
                ["active", "qualified", "1"]
                if same_source_bead
                else ["active", "indeterminate", None],
            )
        if not visible and (action == "confirm" or not commit):
            fresh = self.record["replies"]["fresh_after"]
            self.assertEqual(fresh["outcome"], "available", fresh)
            self.assertEqual(
                [row["values"] for row in fresh["page"]["rows"]],
                [row["values"] for row in saved["page"]["rows"]],
            )
            for key in ("snapshot_digest", "projection_manifest_sha256"):
                self.assertEqual(
                    fresh["result"]["frame"][key], saved["result"]["frame"][key], key
                )

    def same_bead_history(self) -> tuple[str, Callable[[], None]]:
        """TG04: B→C and B→D, then B→C replaced by B→D (the SAME B)."""
        f = self.r.fixture
        tag = uuid4().hex[:8]
        (sb, ob), (sc, oc), (sd, od), (st, ot) = [
            relation_agents.explicit_scope(self.r.db, f) for _ in range(4)
        ]
        c = f.remote_bead(
            "Fictional C: first count.", key="same-c-" + tag, scope=sc, source=oc
        )
        d = f.remote_bead(
            "Fictional D: independent count.", key="same-d-" + tag, scope=sd, source=od
        )
        ids: dict[str, str] = {}

        def plan(extras: Any, bead: Any, found: Any) -> None:
            for key, candidate in zip(("first", "replacement"), found, strict=True):
                ids[key] = f.relate(extras, bead, candidate, "derived_from")

        with self.r.in_scope(sb):
            b = f.author(
                "Fictional B: both counts informed the audit.",
                "same-b-" + tag,
                candidates=(c, d),
                plan=plan,
                source=ob,
            )
        t = f.remote_bead(
            "Fictional T: audit finding.", key="same-t-" + tag, scope=st, source=ot
        )
        self.r.relation_in(
            b,
            (t,),
            "same-r-" + tag,
            lambda packet: [
                self.r.citing(
                    f.proposal(
                        packet,
                        "supports",
                        f.endpoint(packet, b, 0),
                        f.endpoint(packet, t, 0),
                        basis="agent_inferred",
                    ),
                    f.endpoint(packet, b, 0)["statement_ids"],
                )
            ],
        )
        _, agent = self.r.pair_agent_over(
            [sb, sc, sd, st],
            ["memory.inspect", "memory.query", "source.read"],
            label="same-bead-" + tag,
        )
        self.record["same_bead_replacement"] = {"source_bead": str(b), **ids}

        def supersede() -> None:
            f.lifecycle.record_relation_event(
                RecordRelationEvent(
                    idempotency_key="orchard.same-bead." + tag,
                    relation_id=UUID(ids["first"]),
                    action="supersede",
                    replacement_relation_id=UUID(ids["replacement"]),
                    reason="Fictional same-source-bead replacement.",
                    expected_last_event_id=f.latest_relation_event(UUID(ids["first"])),
                )
            )

        return agent, supersede

    def fresh_family(
        self, secret: str, label: str, *, known_at: str | None = None
    ) -> dict[str, Any]:
        """TG06/07: all nine tables, their counts/type/head/gap metadata and charges."""
        run = self.r.start(secret)
        replies: dict[str, Any] = {}
        self.record.setdefault("family_reads", {})[label] = replies
        try:
            for table, columns in self.r.ALL_RELATION_COLUMNS.items():
                text = "SELECT " + ",".join(columns) + " FROM memory_v1." + table
                _, reply = self.r.query(
                    run, text, page_size=50, secret=secret, known_at=known_at
                )
                replies[table] = reply
                self.assertEqual(reply["outcome"], "available", (table, reply))
                self.assertEqual(reply["page"]["rows"], [], (table, reply))
                self.assertEqual(reply["result"]["total_rows"], "0", (table, reply))
            self.deliveries(run, label)
        finally:
            self.r.close(run, secret)
        return replies

    def material_history(self) -> dict[str, Any]:
        """One readable assessed R and a real canonical lifecycle evidence M."""
        f = self.r.fixture
        tag = uuid4().hex[:8]
        (ends, source), (hidden, material_source) = [
            relation_agents.explicit_scope(self.r.db, f) for _ in range(2)
        ]
        a = f.remote_bead(
            "Fictional A: count recorded.",
            key="material-a-" + tag,
            scope=ends,
            source=source,
        )
        b = f.remote_bead(
            "Fictional B: finding recorded.",
            key="material-b-" + tag,
            scope=ends,
            source=source,
        )
        m = f.remote_bead(
            "Fictional M: private material evidence.",
            key="material-m-" + tag,
            scope=hidden,
            source=material_source,
        )
        rid = self.r.relation_in(
            a,
            (b,),
            "material-r-" + tag,
            lambda packet: [
                f.proposal(
                    packet,
                    "supports",
                    f.endpoint(packet, a, 0),
                    f.endpoint(packet, b, 0),
                    basis="agent_inferred",
                )
            ],
        )
        case = dict(
            source_bead=a,
            relation=rid,
            ends=ends,
            hidden=hidden,
            material_bead=m,
            material_source=material_source,
            tag=tag,
        )
        self.material_event(case, "dispute", m)
        return case

    def material_event(self, case: dict[str, Any], action: str, bead: UUID) -> None:
        row = self.r.db.execute(
            "SELECT s.statement_id,e.evidence_source_unit_id,e.evidence_content_hash "
            "FROM memoriesql.bead_semantic_statements s JOIN memoriesql.bead_semantic_statement_evidence e "
            "ON e.tenant_id=s.tenant_id AND e.statement_id=s.statement_id "
            "WHERE s.bead_id=%s ORDER BY s.statement_id LIMIT 1",
            (bead,),
        ).fetchone()
        assert row is not None
        receipt = self.r.fixture.governance.record_event(
            self.r.fixture.lifecycle_command(
                case["source_bead"],
                case["relation"],
                action,
                "material-" + action + "-" + case["tag"],
                evidence=[
                    dict(
                        statement_id=row[0], source_unit_id=row[1], content_hash=row[2]
                    )
                ],
            )
        )
        self.assertIsInstance(receipt, AssessedRelationEventReceipt, receipt)
        self.record.setdefault("material_events", []).append(
            receipt.model_dump(mode="json")
        )

    def test_same_source_bead_replacement_closes_fresh_gap(self) -> None:
        self.held_event("supersede", commit=True, same_source_bead=True)

    def test_already_ungranted_material_changes_remain_withheld(self) -> None:
        case = self.material_history()
        _, agent = self.r.pair_agent_over(
            [case["ends"]],
            ["memory.inspect", "memory.query", "source.read"],
            label="ungranted-material",
        )
        before = self.fresh_family(agent, "ungranted_before")
        run, request, saved = self.saved(
            agent, "SELECT bead_id FROM memory_v1.observations ORDER BY bead_id"
        )
        m2 = self.r.fixture.remote_bead(
            "Fictional changed unreadable material.",
            key="material-m2-" + case["tag"],
            scope=case["hidden"],
            source=case["material_source"],
        )
        self.material_event(case, "confirm", m2)
        after = self.fresh_family(agent, "ungranted_after")
        for table in before:
            self.assertEqual(
                before[table]["result"]["coverage"], after[table]["result"]["coverage"]
            )
            for key in ("snapshot_digest", "projection_manifest_sha256"):
                self.assertEqual(
                    before[table]["result"]["frame"][key],
                    after[table]["result"]["frame"][key],
                )
            for identifier in (
                str(case["relation"]),
                str(case["material_bead"]),
                str(m2),
                str(case["material_source"]),
            ):
                self.assertNotIn(identifier, json.dumps(before[table]))
                self.assertNotIn(identifier, json.dumps(after[table]))
        self.after(run, request, saved, agent)

    def test_original_material_evidence_revocation_refuses_whole_frame(self) -> None:
        case = self.material_history()
        _, agent = self.r.pair_agent_over(
            [case["ends"], case["hidden"]],
            ["memory.inspect", "memory.query", "source.read"],
            label="original-material",
        )
        run, request, saved = self.saved(agent)
        self.r.db.execute(
            "UPDATE memoriesql.protected_resources SET status='revoked',revoked_at=clock_timestamp() "
            "WHERE resource_id=%s",
            (case["material_source"],),
        )
        self.after(run, request, saved, agent, revoked=True)
        self.fresh_family(agent, "revoked_material")
        denied = self.r.relations_of(case["source_bead"])
        missing = self.r.relations_of(uuid4())
        self.assertEqual(denied, missing)
        self.assertEqual(denied.outcome, "unavailable")

    def test_original_nonendpoint_derivation_root_revocation_refuses_whole_frame(
        self,
    ) -> None:
        agent, change = self.r.hidden_history()
        commands: list[Any] = []
        with patch.object(
            self.r.fixture.lifecycle,
            "record_relation_event",
            side_effect=commands.append,
        ):
            change()
        root = self.r.db.execute(
            "SELECT b.bead_id,e.source_object_id FROM memoriesql.bead_relations r "
            "JOIN memoriesql.beads b ON b.tenant_id=r.tenant_id AND b.bead_id=r.target_bead_id "
            "JOIN memoriesql.source_events e ON e.tenant_id=b.tenant_id AND e.event_id=b.event_id "
            "WHERE r.relation_id=%s",
            (commands[0].relation_id,),
        ).fetchone()
        assert root is not None
        endpoints = {
            row[0]
            for row in self.r.db.execute(
                "SELECT source_bead_id FROM memoriesql.assessed_relations UNION SELECT target_bead_id FROM memoriesql.assessed_relations"
            ).fetchall()
        }
        self.assertNotIn(root[0], endpoints)
        run, request, saved = self.saved(agent)
        self.r.db.execute(
            "UPDATE memoriesql.protected_resources SET status='revoked',revoked_at=clock_timestamp() "
            "WHERE resource_id=%s",
            (root[1],),
        )
        self.after(run, request, saved, agent, revoked=True)
        self.fresh_family(agent, "revoked_original_root")

    def test_schema43_result_survives_schema44_upgrade(self) -> None:
        agent, _ = self.r.hidden_history()
        run, request, saved = self.saved(agent)
        migrate(self.r.db, expected_current_version=43, target_version=44)
        self.after(run, request, saved, agent)

    def test_unchanged_control(self) -> None:
        agent, _ = self.r.hidden_history()
        run, request, saved = self.saved(agent)
        self.after(run, request, saved, agent)

    def test_rolled_back_hidden_retraction_control(self) -> None:
        self.held_event("retract", commit=False)

    def test_late_hidden_confirmation(self) -> None:
        self.held_event("confirm", commit=True)

    def test_late_hidden_retraction(self) -> None:
        self.held_event("retract", commit=True)

    def test_late_hidden_replacement(self) -> None:
        self.held_event("supersede", commit=True)

    def test_late_visible_retraction_preserves_original_frame(self) -> None:
        self.held_event("retract", commit=True, visible=True)

    def test_original_source_revocation_still_refuses(self) -> None:
        agent, _ = self.r.hidden_history()
        run, request, saved = self.saved(agent)
        self.r.db.execute(
            "UPDATE memoriesql.protected_resources SET status='revoked',revoked_at=clock_timestamp() "
            "WHERE resource_kind='source' AND workspace_id=%s",
            (self.r.fixture.workspace,),
        )
        self.after(run, request, saved, agent, revoked=True)

    def test_newly_revoked_nonendpoint_task_candidate_refuses_whole_result(
        self,
    ) -> None:
        case = self.r.relation_quoting_a_hidden_candidate()
        pairing, agent = self.r.pair_agent_over(
            [case["ends"], case["hidden_scope"]],
            ["memory.inspect", "memory.query", "source.read"],
            label="retained-task-candidate",
        )
        run, request, saved = self.saved(agent)
        grant = self.r.h.scalar(
            "SELECT grant_id FROM memoriesql.access_grants "
            "WHERE target_principal_id=%s AND access_scope_id=%s",
            (self.r.paired_principal(pairing), case["hidden_scope"]),
        )
        with self.r.db.transaction():
            self.r.fixture.begin()
            self.r.db.execute(
                "SELECT memoriesql.revise_access_grant(%s,1,%s,'revoked',%s,%s,%s)",
                (
                    grant,
                    ["read"],
                    self.r.fixture.now,
                    self.r.fixture.now + timedelta(hours=1),
                    self.r.fixture.now,
                ),
            )
        self.after(run, request, saved, agent, revoked=True)

    def assert_saved_refuses(
        self,
        run: dict[str, Any],
        request: dict[str, Any],
        saved: dict[str, Any],
        secret: str,
    ) -> None:
        for reply in (
            self.r.send(request, secret=secret),
            self.r.reuse(run, saved["result"], page_size=50, secret=secret),
        ):
            self.assertEqual(reply["outcome"], "unavailable", reply)
            self.assertNotIn("result", reply)
            self.assertNotIn("page", reply)

    def test_late_replacement_does_not_adopt_unreadable_new_facts(self) -> None:
        self.held_event("supersede", commit=True, protected_replacement=True)

    def test_corruption_of_each_sealed_partition_refuses_rows_and_metadata(
        self,
    ) -> None:
        agent, _ = self.r.hidden_history()
        run, request, saved = self.saved(agent)
        result_id = UUID(saved["result"]["result_id"])
        for column in ("content_bytes", "witness_bytes", "dependency_bytes"):
            with self.subTest(partition=column):
                original_row = self.r.db.execute(
                    sql.SQL(
                        "SELECT {c} FROM memoriesql.result_preparation_artifacts WHERE artifact_ref=%s"
                    ).format(c=sql.Identifier(column)),
                    (result_id,),
                ).fetchone()
                assert original_row is not None
                original = original_row[0]
                self.r.db.execute(
                    "ALTER TABLE memoriesql.result_preparation_artifacts "
                    "DISABLE TRIGGER result_preparation_artifacts_no_update"
                )
                try:
                    # Committed fixture storage fault, visible to the independent
                    # result connections. Exact original bytes restored in finally.
                    self.r.db.execute(
                        sql.SQL(
                            "UPDATE memoriesql.result_preparation_artifacts "
                            "SET {c}={c}||decode('20','hex') WHERE artifact_ref=%s"
                        ).format(c=sql.Identifier(column)),
                        (result_id,),
                    )
                    self.assert_saved_refuses(run, request, saved, agent)
                finally:
                    self.r.db.execute(
                        sql.SQL(
                            "UPDATE memoriesql.result_preparation_artifacts SET {c}=%s WHERE artifact_ref=%s"
                        ).format(c=sql.Identifier(column)),
                        (original, result_id),
                    )
                    self.r.db.execute(
                        "ALTER TABLE memoriesql.result_preparation_artifacts "
                        "ENABLE TRIGGER result_preparation_artifacts_no_update"
                    )
        self.after(run, request, saved, agent)

    def test_changed_original_statement_bytes_refuse_saved_frame(self) -> None:
        agent, _ = self.r.hidden_history()
        run, request, saved = self.saved(agent)
        dep_row = self.r.db.execute(
            "SELECT dependency_bytes FROM memoriesql.result_preparation_artifacts WHERE artifact_ref=%s",
            (UUID(saved["result"]["result_id"]),),
        ).fetchone()
        assert dep_row is not None
        dep_bytes = dep_row[0]
        records = json.loads(
            base64.b64decode(json.loads(bytes(dep_bytes))["records_base64"])
        )
        statement = next(record for record in records if record["kind"] == "statement")
        statement_id = UUID(statement["id"])
        self.r.db.execute(
            "ALTER TABLE memoriesql.bead_semantic_statements "
            "DISABLE TRIGGER bead_semantic_statements_immutable"
        )
        try:
            self.r.db.execute(
                "UPDATE memoriesql.bead_semantic_statements "
                "SET statement_text=statement_text||' fictional storage fault' WHERE statement_id=%s",
                (statement_id,),
            )
            self.assert_saved_refuses(run, request, saved, agent)
        finally:
            self.r.db.execute(
                "UPDATE memoriesql.bead_semantic_statements SET statement_text=%s WHERE statement_id=%s",
                (statement["row"]["text"], statement_id),
            )
            self.r.db.execute(
                "ALTER TABLE memoriesql.bead_semantic_statements "
                "ENABLE TRIGGER bead_semantic_statements_immutable"
            )
        self.after(run, request, saved, agent)


if __name__ == "__main__":
    unittest.main(verbosity=2)
