"""Fictional saved-frame races and whole-current-authority refusals.

The original seven-probe private reproduction is preserved unchanged. These
saved-frame regressions do not redefine the still-open fresh-root invariance
obligation for hidden retract/supersede. Run only in an assigned DB slot.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import unittest
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

from memoriesql.application.relation_lifecycle import RelationAction
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
        self, secret: str | None
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        run = self.r.start(secret)
        request, reply = self.r.query(
            run, self.r.HIDDEN_HISTORY_QUERY, page_size=50, secret=secret
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
        again = self.r.send(request, secret=secret)
        reused = self.r.reuse(run, saved["result"], page_size=50, secret=secret)
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
    ) -> None:
        agent, change = self.r.hidden_history(action)
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
