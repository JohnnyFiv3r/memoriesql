"""Real automatic checkpoint oracles; run only in a separately assigned DB slot.

These cases use canonical fictional producers and the public executor. Source
authoring/compilation is not evidence that any SQL, authority or cleanup passed.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import psycopg
from psycopg.conninfo import make_conninfo

from memoriesql.application.investigation_contracts import CheckpointManifest
from memoriesql.infrastructure.postgres.relation_projection import (
    relation_projection_frame,
)

if TYPE_CHECKING:
    from tests.runtime import test_agent_sql_helpers_runtime as helpers
    from tests.runtime.test_postgres_runtime import migrate
else:
    import test_agent_sql_helpers_runtime as helpers
    from test_postgres_runtime import migrate


@unittest.skipUnless(
    os.environ.get("N1_TEST_DATABASE_URL"), "assigned disposable PostgreSQL required"
)
class AutomaticCheckpointRuntime(unittest.TestCase):
    def setUp(self) -> None:
        self.h = helpers.AgentSqlHelperRuntime(methodName="runTest")
        self.addCleanup(self.h.doCleanups)
        self.h.setUp()
        self.r, self.db, self.fixture = self.h.r, self.h.db, self.h.fixture
        migrate(self.db, expected_current_version=46, target_version=47)

    def query_root(
        self, secret: str | None = None
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        run = self.r.start(secret)
        _, result = self.r.query(
            run,
            "SELECT bead_id, bead_version_id, source_unit_id FROM memory_v1.observations ORDER BY bead_id",
            page_size=1,
            secret=secret,
        )
        self.assertEqual(result["outcome"], "available", result)
        self.assertTrue(result["page"]["rows"])
        return run, result

    def pin(self, result: dict[str, Any]) -> dict[str, Any]:
        return {k: result["result"][k] for k in ("result_id", "content_digest")}

    def append(
        self,
        run: dict[str, Any],
        result: dict[str, Any],
        previous: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "contract_version": 1,
            "run_ref": run["run_ref"],
            "step_key": str(uuid4()),
            "kind": "checkpoint",
            "investigation_id": previous["checkpoint"]["investigation_id"]
            if previous
            else None,
            "branch_id": previous["checkpoint"]["branch_id"] if previous else None,
            "expected_head": previous["branch"]["head"] if previous else None,
            "question": "fictional retained α investigation",
            "progress": {"status": "paused", "note": "explicit continuation"},
            "findings": [],
            "roots": [{"result": self.pin(result), "access": {"kind": "standalone"}}],
        }

    def read(self, run: dict[str, Any], pin: dict[str, Any]) -> dict[str, Any]:
        return {
            "contract_version": 1,
            "run_ref": run["run_ref"],
            "step_key": str(uuid4()),
            "kind": "read_checkpoint",
            "access": {"checkpoint": pin, "basis": {"kind": "automatic"}},
        }

    def assert_checkpoint(self, reply: dict[str, Any]) -> None:
        self.assertEqual(reply["outcome"], "available", reply)
        manifest = CheckpointManifest.model_validate(reply["checkpoint"])
        row = self.db.execute(
            "SELECT a.content_bytes,cp.manifest_digest FROM memoriesql.query_checkpoints cp JOIN memoriesql.result_preparation_artifacts a ON a.tenant_id=cp.tenant_id AND a.artifact_ref=cp.checkpoint_id WHERE cp.checkpoint_id=%s",
            (manifest.checkpoint_id,),
        ).fetchone()
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(
            hashlib.sha256(bytes(row[0])).hexdigest(), manifest.content_digest()
        )
        self.assertEqual(row[1], manifest.content_digest())
        for field in ("result", "page", "visibility", "inspection", "hydration"):
            self.assertNotIn(field, reply)

    def test_nonempty_append_read_and_contextual_reuse_deliver_only_explicit_rows(
        self,
    ) -> None:
        run, result = self.query_root()
        request = self.append(run, result)
        before = self.h.scalar("SELECT count(*) FROM memoriesql.query_visible_refs")
        first = self.r.send(request)
        self.assert_checkpoint(first)
        self.assertEqual(
            self.h.scalar("SELECT count(*) FROM memoriesql.query_visible_refs"), before
        )
        new = self.r.start()
        restored = self.r.send(self.read(new, first["branch"]["head"]))
        self.assert_checkpoint(restored)
        context = restored["root_contexts"][0]
        invocations = self.r.invocations()
        page = self.r.send(
            {
                "contract_version": 1,
                "run_ref": new["run_ref"],
                "step_key": str(uuid4()),
                "kind": "reuse_result",
                "result": self.pin(result),
                "access": context,
                "page_size": 1,
            }
        )
        self.assertEqual(page["outcome"], "available", page)
        self.assertEqual(page["page"]["rows"], result["page"]["rows"])
        self.assertEqual(page["access_bases"][0]["context"], context)
        self.assertEqual(self.r.invocations(), invocations)

    def test_root_expiry_does_not_end_checkpoint_or_allow_standalone_fallback(
        self,
    ) -> None:
        run, result = self.query_root()
        first = self.r.send(self.append(run, result))
        self.assert_checkpoint(first)
        self.r.age(run["run_ref"], timedelta(days=31))
        new = self.r.start()
        self.assertEqual(self.r.reuse(new, result["result"])["outcome"], "unavailable")
        restored = self.r.send(self.read(new, first["branch"]["head"]))
        self.assert_checkpoint(restored)
        context = restored["root_contexts"][0]
        page = self.r.send(
            {
                "contract_version": 1,
                "run_ref": new["run_ref"],
                "step_key": str(uuid4()),
                "kind": "reuse_result",
                "result": self.pin(result),
                "access": context,
                "page_size": 1,
            }
        )
        self.assertEqual(page["outcome"], "available", page)
        self.assertEqual(
            page["result"]["expires_at"],
            self.h.scalar(
                "SELECT memoriesql.relation_packet_time(expires_at) FROM memoriesql.query_result_creations WHERE result_id=%s",
                (result["result"]["result_id"],),
            ),
        )
        self.assertEqual(
            self.h.scalar("SELECT count(*) FROM memoriesql.query_checkpoint_holds"), 1
        )
        self.r.service().cleanup_expired()
        self.assertEqual(
            self.h.scalar(
                "SELECT count(*) FROM memoriesql.result_preparation_artifacts WHERE artifact_ref=%s",
                (result["result"]["result_id"],),
            ),
            1,
        )

    def test_paired_agent_context_cannot_be_consumed_by_the_other_principal(
        self,
    ) -> None:
        run, result = self.query_root(self.h.agent)
        first = self.r.send(self.append(run, result), secret=self.h.agent)
        self.assert_checkpoint(first)
        own = self.r.send(self.read(run, first["branch"]["head"]), secret=self.h.agent)
        self.assert_checkpoint(own)
        other_run = self.r.start()
        refused = self.r.send(self.read(other_run, first["branch"]["head"]))
        self.assertEqual(
            (refused["outcome"], refused["error"]),
            ("unavailable", {"code": "unavailable"}),
        )
        self.assertNotIn("checkpoint", refused)
        self.assertNotIn(
            first["branch"]["head"]["manifest_digest"], json.dumps(refused)
        )

    def test_expired_automatic_context_is_removed_without_ending_live_standalone_access(
        self,
    ) -> None:
        run, result = self.query_root()
        first = self.r.send(self.append(run, result))
        self.assert_checkpoint(first)
        # Boundary-only fixture clock control: an expired control row must refuse
        # BEFORE reading/returning manifest bytes. Never accept this aged control
        # row as a newly valid immutable manifest or a changed manifest digest.
        self.db.execute(
            "UPDATE memoriesql.query_checkpoints SET created_at=created_at-interval '31 days',expires_at=expires_at-interval '31 days' WHERE checkpoint_id=%s",
            (first["checkpoint"]["checkpoint_id"],),
        )
        refused = self.r.send(self.read(run, first["branch"]["head"]))
        self.assertEqual(refused["outcome"], "unavailable", refused)
        self.r.service().cleanup_expired()
        self.assertEqual(
            self.h.scalar("SELECT count(*) FROM memoriesql.query_checkpoint_holds"), 0
        )
        self.assertEqual(
            self.h.scalar(
                "SELECT count(*) FROM memoriesql.result_preparation_artifacts WHERE artifact_ref=%s",
                (first["checkpoint"]["checkpoint_id"],),
            ),
            0,
        )
        direct = self.r.reuse(run, result["result"])
        self.assertEqual(direct["outcome"], "available", direct)

    def test_checkpoint_context_inspection_and_normalized_hydration_use_original_frame(
        self,
    ) -> None:
        run, result = self.query_root()
        first = self.r.send(self.append(run, result))
        self.assert_checkpoint(first)
        read = self.r.send(self.read(run, first["branch"]["head"]))
        context = read["root_contexts"][0]
        inspection = self.h.inspection(
            run, beads=(self.h.normalized,), facets=("content", "evidence_metadata")
        )
        inspection.update(
            result={"pin": self.pin(result), "access": context},
            known_at=result["result"]["frame"]["known_at"],
        )
        raw, observed = self.h.send(inspection)
        self.h.available(raw, observed)
        pin = observed["inspection"]["evidence_pins"][0]
        hydration = self.h.hydration(run, pin)
        hydration["selections"][0]["origin"] = {
            "result": self.pin(result),
            "access": context,
        }
        raw, hydrated = self.h.send(hydration)
        self.h.available(raw, hydrated)
        self.assertEqual(
            hydrated["hydration"]["slices"][0]["content"], self.h.TEXT[:512]
        )
        self.assertEqual(hydrated["access_bases"][0]["context"], context)

    def test_replay_is_one_manifest_and_stale_head_is_content_free_conflict(
        self,
    ) -> None:
        run, result = self.query_root()
        request = self.append(run, result)
        first = self.r.send(request)
        again = self.r.send(request)
        self.assert_checkpoint(first)
        self.assert_checkpoint(again)
        self.assertEqual(first["checkpoint"], again["checkpoint"])
        self.assertEqual(first["receipt_ref"], again["receipt_ref"])
        self.assertNotEqual(first["access_receipt_ref"], again["access_receipt_ref"])
        next_request = self.append(run, result, first)
        second = self.r.send(next_request)
        self.assert_checkpoint(second)
        stale = copy.deepcopy(next_request)
        stale["step_key"] = str(uuid4())
        refused = self.r.send(stale)
        self.assertEqual(
            (refused["outcome"], refused["error"]),
            ("invalid_request", {"code": "branch_conflict"}),
        )
        self.assertNotIn(second["branch"]["head"]["checkpoint_id"], json.dumps(refused))
        self.assertEqual(
            self.h.scalar("SELECT count(*) FROM memoriesql.query_checkpoints"), 2
        )

    def test_twenty_first_append_prunes_old_context_without_releasing_shared_root(
        self,
    ) -> None:
        run, result = self.query_root()
        first = self.r.send(self.append(run, result))
        current = first
        for _ in range(20):
            current = self.r.send(self.append(run, result, current))
            self.assert_checkpoint(current)
        self.assertEqual(current["checkpoint"]["investigation_sequence"], "21")
        self.assertEqual(
            self.h.scalar(
                "SELECT count(*) FROM memoriesql.query_checkpoints WHERE automatic_retained"
            ),
            20,
        )
        self.assertEqual(
            self.r.send(self.read(run, first["branch"]["head"]))["outcome"],
            "unavailable",
        )
        self.assertEqual(
            self.h.scalar(
                "SELECT count(DISTINCT artifact_ref) FROM memoriesql.query_checkpoint_holds"
            ),
            1,
        )
        self.assertEqual(
            self.h.scalar(
                "SELECT count(*) FROM memoriesql.result_preparation_artifacts WHERE artifact_ref=%s",
                (result["result"]["result_id"],),
            ),
            1,
        )

    def test_finding_reference_requires_delivery_from_the_selected_root(self) -> None:
        run, result = self.query_root()
        request = self.append(run, result)
        fact = {"result_id": result["result"]["result_id"], "row_ref": "1"}
        request["findings"] = [
            {"text": "receipted result", "facts": [fact], "evidence_refs": []}
        ]
        good = self.r.send(request)
        self.assert_checkpoint(good)
        request["step_key"] = str(uuid4())
        request["findings"][0]["facts"][0]["row_ref"] = "999999"
        bad = self.r.send(request)
        self.assertEqual(bad["outcome"], "unavailable")
        self.assertEqual(
            self.h.scalar("SELECT count(*) FROM memoriesql.query_checkpoints"), 1
        )

    def test_cursor_cannot_switch_from_checkpoint_to_standalone(self) -> None:
        run, result = self.query_root()
        first = self.r.send(self.append(run, result))
        restored = self.r.send(self.read(run, first["branch"]["head"]))
        context = restored["root_contexts"][0]
        request = {
            "contract_version": 1,
            "run_ref": run["run_ref"],
            "step_key": str(uuid4()),
            "kind": "reuse_result",
            "result": self.pin(result),
            "access": context,
            "page_size": 1,
        }
        page = self.r.send(request)
        self.assertEqual(page["outcome"], "available", page)
        self.assertTrue(page["page"]["has_more"])
        request.update(
            step_key=str(uuid4()),
            access={"kind": "standalone"},
            cursor=page["page"]["next_cursor"],
        )
        refused = self.r.send(request)
        self.assertEqual(
            (refused["outcome"], refused["error"]),
            ("invalid_request", {"code": "cursor"}),
        )

    def test_workspace_reservation_failure_publishes_no_partial_checkpoint(
        self,
    ) -> None:
        run, result = self.query_root()
        owned: list[dict[str, Any]] = []

        def reserve_or_discard(discard: bool = False) -> None:
            with (
                self.fixture.connection() as connection,
                relation_projection_frame(
                    connection,
                    credential_sha256=self.fixture.secret_hash,
                    workspace_id=self.fixture.workspace,
                    statement_timeout_ms=30000,
                ) as frame,
            ):
                if discard:
                    for receipt in owned:
                        frame.execute(
                            "SELECT memoriesql.discard_result_preparation_v1(%s,%s)",
                            (receipt["operation_ref"], receipt["ownership_ref"]),
                        )
                    return
                # Real existing journal reservations exhaust the workspace ledger;
                # no fabricated result/manifest/disclosure or large byte body.
                used = self.h.scalar(
                    "SELECT COALESCE(sum(CASE WHEN state='reserved' THEN reservation_bytes ELSE allocation_bytes END),0) FROM memoriesql.result_preparation_operations WHERE workspace_id=%s",
                    (self.fixture.workspace,),
                )
                remaining = 536870912 - used
                while remaining >= 8192:
                    capacity = min(67108864, remaining)
                    row = frame.execute(
                        "SELECT memoriesql.reserve_result_preparation_v1(%s,%s,%s,%s)",
                        (uuid4(), uuid4(), "e" * 64, capacity),
                    ).fetchone()
                    assert row is not None
                    owned.append(row[0])
                    remaining -= capacity

        try:
            reserve_or_discard()
            refused = self.r.send(self.append(run, result))
            self.assertEqual(refused["outcome"], "budget_exhausted", refused)
            self.assertEqual(
                self.h.scalar("SELECT count(*) FROM memoriesql.query_checkpoints"), 0
            )
            self.assertEqual(
                self.h.scalar(
                    "SELECT count(*) FROM memoriesql.query_investigation_branches"
                ),
                0,
            )
            self.assertEqual(
                self.h.scalar("SELECT count(*) FROM memoriesql.query_checkpoint_holds"),
                0,
            )
        finally:
            reserve_or_discard(discard=True)

    def test_revoked_and_unknown_checkpoint_withhold_all_protected_metadata(
        self,
    ) -> None:
        run, result = self.query_root()
        first = self.r.send(self.append(run, result))
        self.assert_checkpoint(first)
        self.db.execute(
            "UPDATE memoriesql.protected_resources SET status='revoked',revoked_at=clock_timestamp() WHERE resource_kind='source' AND resource_id=(SELECT e.source_object_id FROM memoriesql.beads b JOIN memoriesql.source_events e ON e.tenant_id=b.tenant_id AND e.event_id=b.event_id WHERE b.bead_id=%s)",
            (self.h.normalized,),
        )
        denied = self.r.send(self.read(run, first["branch"]["head"]))
        unknown = self.r.send(
            self.read(run, {"checkpoint_id": str(uuid4()), "manifest_digest": "f" * 64})
        )
        for reply in (denied, unknown):
            self.assertEqual(
                (reply["outcome"], reply["error"]),
                ("unavailable", {"code": "unavailable"}),
            )
            self.assertEqual(set(reply), set(unknown))
            for field in ("checkpoint", "retention", "root_contexts", "work"):
                self.assertNotIn(field, reply)
            self.assertNotIn(first["checkpoint"]["question"], json.dumps(reply))
            self.assertNotIn(
                first["branch"]["head"]["manifest_digest"], json.dumps(reply)
            )

    def test_server_cancellation_rolls_back_manifest_head_holds_and_disclosure(
        self,
    ) -> None:
        run, result = self.query_root()
        request = self.append(run, result)
        fired: list[str] = []

        class CancelAfterRecord(psycopg.Connection[Any]):
            def execute(self, *args: Any, **kwargs: Any) -> Any:
                outcome = super().execute(*args, **kwargs)
                if (
                    not fired
                    and "SELECT memoriesql.record_checkpoint_disclosure_v1("
                    in str(args[0])
                ):
                    fired.append("statement")
                    super().execute("SET LOCAL statement_timeout='100ms'")
                    super().execute("SELECT pg_sleep(1)")
                return outcome

        def control() -> psycopg.Connection[Any]:
            return CancelAfterRecord.connect(
                make_conninfo(self.fixture.admin, dbname=self.fixture.database),
                autocommit=True,
            )

        _, refused = self.h.send(request, control=control)
        self.assertEqual(fired, ["statement"])
        self.assertEqual(refused["outcome"], "budget_exhausted", refused)
        self.assertEqual(
            self.h.scalar("SELECT count(*) FROM memoriesql.query_checkpoints"), 0
        )
        self.assertEqual(
            self.h.scalar("SELECT count(*) FROM memoriesql.query_checkpoint_holds"), 0
        )
        self.assertEqual(
            self.h.scalar("SELECT count(*) FROM memoriesql.query_investigations"), 0
        )

    def test_actual_committed_manifest_survives_lost_ack_and_replays_without_new_write(
        self,
    ) -> None:
        run, result = self.query_root()
        request = self.append(run, result)
        lost: list[int] = []

        class LostAck(psycopg.Connection[Any]):
            recorded = False

            def execute(self, *args: Any, **kwargs: Any) -> Any:
                outcome = super().execute(*args, **kwargs)
                if "record_checkpoint_disclosure_v1" in str(args[0]):
                    self.recorded = True
                return outcome

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

        _, pending = self.h.send(request, control=control)
        self.assertEqual(len(lost), 1)
        self.assertEqual(pending["outcome"], "settlement_pending", pending)
        self.assertNotIn("checkpoint", pending)
        self.assertEqual(
            self.h.scalar("SELECT count(*) FROM memoriesql.query_checkpoints"), 1
        )
        again = self.r.send(request)
        self.assert_checkpoint(again)
        self.assertEqual(
            self.h.scalar("SELECT count(*) FROM memoriesql.query_checkpoints"), 1
        )
