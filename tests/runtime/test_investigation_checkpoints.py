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
from uuid import UUID, uuid4

import psycopg
from psycopg.conninfo import make_conninfo

from memoriesql.application.agent_sql_catalog import SqlCatalog
from memoriesql.application.agent_sql_results import POLICY_HASH
from memoriesql.application.investigation_contracts import CheckpointManifest
from memoriesql.infrastructure.postgres.relation_projection import (
    relation_projection_frame,
)
from memoriesql.infrastructure.postgres.result_preparation import (
    PostgresResultPreparation,
    PreparationPin,
    PreparedContent,
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
        self.r.age(run["run_ref"], timedelta(days=32))
        new = self.r.start()
        other = self.r.start(self.h.agent)
        hidden = self.r.send(
            self.read(other, first["branch"]["head"]), secret=self.h.agent
        )
        self.assertEqual(hidden["outcome"], "unavailable", hidden)
        self.assertNotIn("checkpoint", hidden)
        status = json.loads(self.r.service().cleanup_status())["cleanup"]
        self.assertEqual(status["overdue"]["results"], 0)
        self.assertEqual(status["due"]["results"], 0)
        self.assertEqual(status["admission"], "open")
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

    def test_final_holder_prune_starts_cleanup_clock_and_real_overdue_gate(
        self,
    ) -> None:
        run, result = self.query_root()
        first = self.r.send(self.append(run, result))
        self.assert_checkpoint(first)
        self.r.age(run["run_ref"], timedelta(days=32))
        current_run = self.r.start()
        result_id = result["result"]["result_id"]
        standalone_end = self.h.scalar(
            "SELECT expires_at FROM memoriesql.query_result_creations WHERE result_id=%s",
            (result_id,),
        )
        before = self.h.scalar("SELECT clock_timestamp()")
        current = first
        # Twenty authored, root-free checkpoints prune the sole root holder.
        for _ in range(20):
            request = self.append(current_run, result, current)
            request["roots"] = []
            current = self.r.send(request)
            self.assert_checkpoint(current)
        eligible = self.h.scalar(
            "SELECT memoriesql.query_result_cleanup_eligible_at_v1(%s,%s)",
            (self.fixture.tenant, result_id),
        )
        self.assertGreaterEqual(eligible, before)
        self.assertLessEqual(eligible, self.h.scalar("SELECT clock_timestamp()"))
        self.assertEqual(
            self.h.scalar(
                "SELECT expires_at FROM memoriesql.query_result_creations WHERE result_id=%s",
                (result_id,),
            ),
            standalone_end,
        )
        status = json.loads(self.r.service().cleanup_status())["cleanup"]
        self.assertEqual(status["due"]["results"], 1)
        self.assertEqual(status["overdue"]["results"], 0)
        self.assertEqual(status["admission"], "open")
        # Private fixture clock control simulates missed ordinary cleanup. It
        # does not alter standalone expiry, content/digests or any valid route.
        self.db.execute(
            "UPDATE memoriesql.query_result_cleanup_hold_ends SET last_hold_end=last_hold_end-interval '25 hours' WHERE artifact_ref=%s",
            (result_id,),
        )
        status = json.loads(self.r.service().cleanup_status())["cleanup"]
        self.assertEqual(status["overdue"]["results"], 1)
        self.assertEqual(status["admission"], "blocked")
        # Exercise the actual inherited admission gate before host cleanup,
        # with the same public function/authority frame as start_run().
        with (
            self.fixture.connection() as connection,
            relation_projection_frame(
                connection,
                credential_sha256=self.fixture.secret_hash,
                workspace_id=self.fixture.workspace,
                query_access=True,
            ) as frame,
        ):
            row = frame.execute(
                "SELECT memoriesql.start_query_run_v1(%s,%s)",
                (SqlCatalog.installed().hash, POLICY_HASH),
            ).fetchone()
            assert row is not None
            self.assertEqual(row[0], {"refused": "cleanup"})
        self.r.service().cleanup_expired()
        self.assertEqual(
            self.h.scalar(
                "SELECT count(*) FROM memoriesql.result_preparation_artifacts WHERE artifact_ref=%s",
                (result_id,),
            ),
            0,
        )
        self.r.start()

    def test_delayed_checkpoint_gc_preserves_natural_hold_end_and_debt(self) -> None:
        run, result = self.query_root()
        first = self.r.send(self.append(run, result))
        self.assert_checkpoint(first)
        self.r.age(run["run_ref"], timedelta(days=32))
        checkpoint_id = first["checkpoint"]["checkpoint_id"]
        # Expired control boundary only; never accept a rewritten manifest.
        self.db.execute(
            "UPDATE memoriesql.query_checkpoints SET created_at=created_at-interval '32 days',expires_at=expires_at-interval '32 days' WHERE checkpoint_id=%s",
            (checkpoint_id,),
        )
        natural_end = self.h.scalar(
            "SELECT expires_at FROM memoriesql.query_checkpoints WHERE checkpoint_id=%s",
            (checkpoint_id,),
        )
        self.db.execute(
            "SELECT memoriesql.expire_automatic_checkpoints_v1(16,%s,%s,clock_timestamp()+interval '5 seconds')",
            (self.fixture.tenant, self.fixture.workspace),
        )
        result_id = result["result"]["result_id"]
        self.assertEqual(
            self.h.scalar(
                "SELECT last_hold_end FROM memoriesql.query_result_cleanup_hold_ends WHERE artifact_ref=%s",
                (result_id,),
            ),
            natural_end,
        )
        self.assertEqual(
            self.h.scalar(
                "SELECT memoriesql.query_result_cleanup_eligible_at_v1(%s,%s)",
                (self.fixture.tenant, result_id),
            ),
            natural_end,
        )
        status = json.loads(self.r.service().cleanup_status())["cleanup"]
        self.assertEqual(status["overdue"]["results"], 1)
        self.assertEqual(status["admission"], "blocked")
        self.r.service().cleanup_expired()
        self.assertEqual(
            self.h.scalar(
                "SELECT count(*) FROM memoriesql.result_preparation_artifacts WHERE artifact_ref=%s",
                (result_id,),
            ),
            0,
        )

    def test_private_child_hold_defers_debt_until_owned_release(self) -> None:
        run, result = self.query_root()
        result_id = result["result"]["result_id"]
        parent_hash = self.h.scalar(
            "SELECT artifact_sha256 FROM memoriesql.result_preparation_artifacts WHERE artifact_ref=%s",
            (result_id,),
        )
        with self.fixture.connection() as connection:
            store = PostgresResultPreparation(
                connection,
                credential_sha256=self.fixture.secret_hash,
                workspace_id=self.fixture.workspace,
            )
            owned = store.reserve(
                run_ref=uuid4(),
                step_key=uuid4(),
                request_fingerprint="e" * 64,
                reservation_bytes=16384,
            )
            try:
                # Actual private M31 preparation/hold, not a fabricated public
                # saved-input result, receipt or supported refinement feature.
                store.seal(
                    operation_ref=owned.operation_ref,
                    ownership_ref=owned.ownership_ref,
                    artifact_ref=uuid4(),
                    content=PreparedContent.encode(
                        content={"fictional": "child"}, witnesses={}, dependencies={}
                    ),
                    parents=(PreparationPin(UUID(result_id), parent_hash),),
                )
                self.r.age(run["run_ref"], timedelta(days=32))
                current = self.r.start()
                self.r.start(self.h.agent)
                self.assertIsNone(
                    self.h.scalar(
                        "SELECT memoriesql.query_result_cleanup_eligible_at_v1(%s,%s)",
                        (self.fixture.tenant, result_id),
                    )
                )
                status = json.loads(self.r.service().cleanup_status())["cleanup"]
                self.assertEqual(status["due"]["results"], 0)
                self.assertEqual(status["admission"], "open")
                self.assertEqual(
                    self.r.reuse(current, result["result"])["outcome"], "unavailable"
                )
                before = self.h.scalar("SELECT clock_timestamp()")
                store.discard(
                    operation_ref=owned.operation_ref, ownership_ref=owned.ownership_ref
                )
                eligible = self.h.scalar(
                    "SELECT memoriesql.query_result_cleanup_eligible_at_v1(%s,%s)",
                    (self.fixture.tenant, result_id),
                )
                self.assertGreaterEqual(eligible, before)
                self.assertLessEqual(
                    eligible, self.h.scalar("SELECT clock_timestamp()")
                )
                self.r.service().cleanup_expired()
                self.assertEqual(
                    self.h.scalar(
                        "SELECT count(*) FROM memoriesql.result_preparation_artifacts WHERE artifact_ref=%s",
                        (result_id,),
                    ),
                    0,
                )
            finally:
                store.discard(
                    operation_ref=owned.operation_ref, ownership_ref=owned.ownership_ref
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
        run = self.r.start()
        _, result = self.r.query(
            run,
            "SELECT s.statement_id,s.bead_id,e.evidence_ref FROM memory_v1.statements s "
            "JOIN memory_v1.statement_sources e ON e.statement_id=s.statement_id "
            "ORDER BY s.statement_id,e.evidence_ref",
            page_size=50,
        )
        self.assertEqual(result["outcome"], "available", result)
        first = self.r.send(self.append(run, result))
        self.assert_checkpoint(first)
        self.r.age(run["run_ref"], timedelta(days=32))
        run = self.r.start()
        self.assertEqual(self.r.reuse(run, result["result"])["outcome"], "unavailable")
        read = self.r.send(self.read(run, first["branch"]["head"]))
        context = read["root_contexts"][0]
        # Re-deliver real result refs in this run after old-run cleanup, using
        # the explicit CP context. Direct-inspection refs are a different route.
        page = self.r.send(
            {
                "contract_version": 1,
                "run_ref": run["run_ref"],
                "step_key": str(uuid4()),
                "kind": "reuse_result",
                "result": self.pin(result),
                "access": context,
                "page_size": 50,
            }
        )
        self.assertEqual(page["outcome"], "available", page)
        columns = [c["name"] for c in result["result"]["result_schema"]]
        row = next(
            r
            for r in page["page"]["rows"]
            if r["values"][columns.index("bead_id")] == str(self.h.normalized)
        )
        statement = row["values"][columns.index("statement_id")]
        source_ref = next(
            ref for ref in row["evidence_refs"] if ref["kind"] == "source"
        )
        inspection = self.h.inspection(run, beads=(), facets=("content",))
        inspection.update(
            result={"pin": self.pin(result), "access": context},
            known_at=result["result"]["frame"]["known_at"],
        )
        raw, observed = self.h.send(inspection)
        self.h.available(raw, observed)
        self.assertEqual(
            observed["inspection"]["result"]["frame"], result["result"]["frame"]
        )
        self.assertEqual(
            observed["inspection"]["result"]["content_digest"],
            result["result"]["content_digest"],
        )
        self.assertEqual(observed["access_bases"][0]["context"], context)
        metadata = self.h.inspection(
            run, beads=(self.h.normalized,), facets=("evidence_metadata",)
        )
        raw, direct = self.h.send(metadata)
        self.h.available(raw, direct)
        # Associate the part by exact statement/unit/content pins, never by
        # guessing that a direct evidence UUID was delivered with this result.
        pin = next(
            p
            for p in direct["inspection"]["evidence_pins"]
            if p["statement_id"] == statement
            and p["unit"]["source_unit_id"] == source_ref["unit_ref"]
            and p["unit"]["content_sha256"] == source_ref["content_sha256"]
            and p["part"] is not None
        )
        hydration = self.h.hydration(run, pin)
        hydration["selections"][0]["evidence_ref"] = source_ref["ref"]
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
        mixed = copy.deepcopy(inspection)
        mixed.update(
            step_key=str(uuid4()), observation_refs=[self.h.target(self.h.normalized)]
        )
        _, denied = self.h.send(mixed)
        self.assertEqual(denied["outcome"], "unsupported_query", denied)
        unbound = copy.deepcopy(hydration)
        unbound["step_key"] = str(uuid4())
        unbound["selections"][0]["evidence_ref"] = str(uuid4())
        self.assertNotEqual(unbound["selections"][0]["evidence_ref"], source_ref["ref"])
        _, missing = self.h.send(unbound)
        self.assertEqual(missing["outcome"], "unavailable", missing)
        for refused in (denied, missing):
            for field in ("inspection", "hydration", "result", "page", "visibility"):
                self.assertNotIn(field, refused)

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
                if str(args[0]).startswith(
                    "SELECT memoriesql.record_checkpoint_disclosure_v1("
                ):
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

    def expire_investigation(self, investigation: str) -> None:
        # Boundary-only fixture clock control, as in the expiry oracles above.
        self.db.execute(
            "UPDATE memoriesql.query_checkpoints SET created_at=created_at-interval "
            "'31 days',expires_at=expires_at-interval '31 days' WHERE investigation_id=%s",
            (investigation,),
        )

    def age_stubs(self, run: dict[str, Any], by: timedelta) -> None:
        # Fixture clock control of prune time only; no manifest, digest or route.
        self.db.execute(
            "UPDATE memoriesql.query_checkpoints cp SET pruned_at=cp.pruned_at-%s::interval "
            "FROM memoriesql.result_preparation_operations o WHERE o.tenant_id=cp.tenant_id "
            "AND o.operation_ref=cp.operation_ref AND o.run_ref=%s AND NOT cp.automatic_retained",
            (f"{int(by.total_seconds())} seconds", run["run_ref"]),
        )

    def residue(self, investigation: str, *runs: dict[str, Any]) -> dict[str, int]:
        refs = [run["run_ref"] for run in runs]
        counts = {
            "checkpoints": (
                "SELECT count(*) FROM memoriesql.query_checkpoints WHERE investigation_id=%s",
                investigation,
            ),
            "branches": (
                "SELECT count(*) FROM memoriesql.query_investigation_branches "
                "WHERE investigation_id=%s",
                investigation,
            ),
            "investigations": (
                "SELECT count(*) FROM memoriesql.query_investigations "
                "WHERE investigation_id=%s",
                investigation,
            ),
            "runs": (
                "SELECT count(*) FROM memoriesql.query_runs WHERE run_ref=ANY(%s::uuid[])",
                refs,
            ),
            "operations": (
                "SELECT count(*) FROM memoriesql.result_preparation_operations "
                "WHERE run_ref=ANY(%s::uuid[])",
                refs,
            ),
            "receipts": (
                "SELECT count(*) FROM memoriesql.query_disclosures x "
                "JOIN memoriesql.query_deliveries d USING(tenant_id,delivery_ref) "
                "WHERE d.run_ref=ANY(%s::uuid[])",
                refs,
            ),
        }
        return {k: self.h.scalar(sql, (value,)) for k, (sql, value) in counts.items()}

    def test_expired_checkpoint_tombstones_leave_no_rows_receipts_or_charge(
        self,
    ) -> None:
        baseline = self.r.retained()
        run, result = self.query_root()
        first = self.r.send(self.append(run, result))
        self.assert_checkpoint(first)
        reader = self.r.start()
        self.assert_checkpoint(self.r.send(self.read(reader, first["branch"]["head"])))
        investigation = first["checkpoint"]["investigation_id"]
        self.expire_investigation(investigation)
        self.r.service().cleanup_expired()
        # The expired slot is a charged noncontent stub; no digest remains in it
        # or in the ended investigation's head, which cannot be continued.
        self.assertEqual(
            self.db.execute(
                "SELECT cp.automatic_retained,cp.manifest_digest,cp.pruned_at IS NOT NULL,"
                "o.allocation_bytes FROM memoriesql.query_checkpoints cp "
                "JOIN memoriesql.result_preparation_operations o USING(tenant_id,operation_ref) "
                "WHERE cp.checkpoint_id=%s",
                (first["checkpoint"]["checkpoint_id"],),
            ).fetchone(),
            (False, None, True, 40960),
        )
        self.assertEqual(
            self.db.execute(
                "SELECT head_ref,head_digest FROM memoriesql.query_investigation_branches "
                "WHERE investigation_id=%s",
                (investigation,),
            ).fetchone(),
            (None, None),
        )
        ended = self.r.send(self.append(reader, result, first))
        self.assertEqual(ended["outcome"], "unavailable", ended)
        for owner in (run, reader):
            self.r.age(owner["run_ref"], timedelta(days=32))
        self.r.service().cleanup_expired()
        self.assertEqual(self.residue(investigation, run, reader)["receipts"], 0)
        status = json.loads(self.r.service().cleanup_status())["cleanup"]
        self.assertEqual(status["due"]["tombstones"], 0)
        self.assertEqual(status["overdue"]["tombstones"], 0)
        for owner in (run, reader):
            self.r.age_tombstone(owner["run_ref"], timedelta(days=31))
        self.age_stubs(run, timedelta(days=31))
        status = json.loads(self.r.service().cleanup_status())["cleanup"]
        self.assertEqual(status["due"]["tombstones"], 2)
        expired = json.loads(self.r.service().cleanup_expired())
        self.assertEqual(expired["cleanup"]["purged"]["tombstones"], 2)
        self.assertEqual(
            self.residue(investigation, run, reader),
            {
                "checkpoints": 0,
                "branches": 0,
                "investigations": 0,
                "runs": 0,
                "operations": 0,
                "receipts": 0,
            },
        )
        self.assertEqual(self.r.retained(), baseline)

    def test_pruned_stub_keeps_only_noncontent_lineage_until_its_tombstone_ends(
        self,
    ) -> None:
        run, result = self.query_root()
        first_run = self.r.start()
        first = self.r.send(self.append(first_run, result))
        self.assert_checkpoint(first)
        # The root's own run ends; only the first checkpoint holds the root now.
        self.r.age(run["run_ref"], timedelta(days=32))
        later = self.r.start()
        current = first
        for _ in range(20):
            request = self.append(later, result, current)
            request["roots"] = []
            current = self.r.send(request)
            self.assert_checkpoint(current)
        investigation = first["checkpoint"]["investigation_id"]
        self.assertEqual(
            self.db.execute(
                "SELECT cp.manifest_digest,o.allocation_bytes,"
                "(SELECT count(*) FROM memoriesql.query_checkpoint_roots r "
                "WHERE r.checkpoint_id=cp.checkpoint_id),"
                "(SELECT count(*) FROM memoriesql.query_checkpoint_holds h "
                "WHERE h.checkpoint_id=cp.checkpoint_id),"
                "(SELECT count(*) FROM memoriesql.result_preparation_artifacts a "
                "WHERE a.artifact_ref=cp.checkpoint_id) "
                "FROM memoriesql.query_checkpoints cp "
                "JOIN memoriesql.result_preparation_operations o USING(tenant_id,operation_ref) "
                "WHERE cp.checkpoint_id=%s AND NOT cp.automatic_retained",
                (first["checkpoint"]["checkpoint_id"],),
            ).fetchone(),
            (None, 40960, 0, 0, 0),
        )
        self.r.age(first_run["run_ref"], timedelta(days=32))
        self.r.service().cleanup_expired()
        # The run's tombstone counts from the prune, not from the run purge.
        self.r.age_tombstone(first_run["run_ref"], timedelta(days=31))
        status = json.loads(self.r.service().cleanup_status())["cleanup"]
        self.assertEqual(status["due"]["tombstones"], 0)
        self.assertEqual(status["overdue"]["tombstones"], 0)
        self.assertEqual(self.residue(investigation, first_run)["checkpoints"], 21)
        # Successors retained beside the stub (expired, not yet swept) hold it.
        self.expire_investigation(investigation)
        self.age_stubs(first_run, timedelta(days=31))
        self.assertEqual(
            self.h.scalar(
                "SELECT memoriesql.expire_query_tombstone_v1(%s,%s)",
                (self.fixture.tenant, first_run["run_ref"]),
            ),
            "pending",
        )
        self.r.service().cleanup_expired()
        self.assertEqual(
            self.residue(investigation, first_run),
            {
                "checkpoints": 20,
                "branches": 1,
                "investigations": 1,
                "runs": 0,
                "operations": 0,
                "receipts": 0,
            },
        )
        self.r.age(later["run_ref"], timedelta(days=32))
        self.r.service().cleanup_expired()
        self.r.age_tombstone(later["run_ref"], timedelta(days=31))
        self.age_stubs(later, timedelta(days=31))
        self.r.service().cleanup_expired()
        self.assertEqual(
            self.residue(investigation, first_run, later),
            {
                "checkpoints": 0,
                "branches": 0,
                "investigations": 0,
                "runs": 0,
                "operations": 0,
                "receipts": 0,
            },
        )
