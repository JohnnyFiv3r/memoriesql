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
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import timedelta
from typing import TYPE_CHECKING, Any
from unittest.mock import patch
from uuid import UUID, uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from memoriesql.application.agent_sql_catalog import SqlCatalog
from memoriesql.application.agent_sql_results import POLICY_HASH
from memoriesql.application.investigation_contracts import CheckpointManifest
from memoriesql.infrastructure.postgres.agent_sql_results import (
    CheckpointAction,
    PostgresAgentSqlResults,
)
from memoriesql.infrastructure.postgres.relation_projection import (
    relation_projection_frame,
)
from memoriesql.infrastructure.postgres.result_preparation import (
    PostgresResultPreparation,
    PreparationPin,
    PreparedContent,
)

if TYPE_CHECKING:
    from tests.runtime import agent_relation_fixtures as relation_agents
    from tests.runtime import test_agent_sql_helpers_runtime as helpers
    from tests.runtime import test_query_audit_retention as audit
    from tests.runtime.test_postgres_runtime import migrate
else:
    import agent_relation_fixtures as relation_agents
    import test_agent_sql_helpers_runtime as helpers
    import test_query_audit_retention as audit
    from test_postgres_runtime import migrate

STATEMENTS = (
    "SELECT s.statement_id,s.bead_id,e.evidence_ref FROM memory_v1.statements s "
    "JOIN memory_v1.statement_sources e ON e.statement_id=s.statement_id "
    "ORDER BY s.statement_id,e.evidence_ref"
)


@unittest.skipUnless(
    os.environ.get("N1_TEST_DATABASE_URL"), "assigned disposable PostgreSQL required"
)
class AutomaticCheckpointRuntime(unittest.TestCase):
    def setUp(self) -> None:
        self.h = helpers.AgentSqlHelperRuntime(methodName="runTest")
        self.addCleanup(self.h.doCleanups)
        self.h.setUp()
        self.r, self.db, self.fixture = self.h.r, self.h.db, self.h.fixture
        migrate(self.db, expected_current_version=46, target_version=51)

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
        # Exactly the explicit checkpoint basis, as inspection reports it.
        self.assertEqual(
            [
                (b["context"], b["end_condition"], b["hold_ref"])
                for b in hydrated["access_bases"]
            ],
            [(context, "checkpoint_expiry_or_prune", context["checkpoint_id"])],
        )
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

    def contextual_page(
        self, run: dict[str, Any], result: dict[str, Any], context: dict[str, Any]
    ) -> dict[str, Any]:
        return self.r.send(
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

    def normalized_evidence(
        self, run: dict[str, Any], result: dict[str, Any], page: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        """The normalized bead's delivered source ref, its exact pin, and the
        direct inspection that reported the pin."""
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
        raw, direct = self.h.send(
            self.h.inspection(
                run, beads=(self.h.normalized,), facets=("evidence_metadata",)
            )
        )
        self.h.available(raw, direct)
        pin = next(
            p
            for p in direct["inspection"]["evidence_pins"]
            if p["statement_id"] == statement
            and p["unit"]["source_unit_id"] == source_ref["unit_ref"]
            and p["unit"]["content_sha256"] == source_ref["content_sha256"]
            and p["part"] is not None
        )
        return source_ref, pin, direct

    def test_named_save_basis_refuses_like_an_unknown_checkpoint(self) -> None:
        # Named saves do not exist in this slice. A named_save basis is refused
        # as unavailable, content-free, and is never read as the automatic one.
        run, result = self.query_root()
        first = self.r.send(self.append(run, result))
        self.assert_checkpoint(first)
        head = first["branch"]["head"]
        named = {"kind": "named_save", "save_id": str(uuid4()), "revision": "1"}
        restored = self.r.send(self.read(run, head))
        self.assert_checkpoint(restored)
        context = restored["root_contexts"][0]
        request = self.read(run, head)
        request["access"]["basis"] = named
        refused = self.r.send(request)
        unknown = self.r.send(
            self.read(run, {"checkpoint_id": str(uuid4()), "manifest_digest": "f" * 64})
        )
        for reply in (refused, unknown):
            self.assertEqual(
                (reply["outcome"], reply["error"]),
                ("unavailable", {"code": "unavailable"}),
            )
            for field in ("checkpoint", "retention", "root_contexts", "work"):
                self.assertNotIn(field, reply)
        self.assertEqual(set(refused), set(unknown))
        self.assertNotIn(head["manifest_digest"], json.dumps(refused))
        # A root context naming a named_save basis is refused like one naming
        # an unknown checkpoint; the same context with its automatic basis reads.
        named_page = self.contextual_page(run, result, {**context, "basis": named})
        missing_page = self.contextual_page(
            run, result, {**context, "checkpoint_id": str(uuid4())}
        )
        for reply in (named_page, missing_page):
            self.assertEqual(reply["outcome"], "unavailable", reply)
            for field in ("page", "result", "visibility"):
                self.assertNotIn(field, reply)
        self.assertEqual(set(named_page), set(missing_page))
        page = self.contextual_page(run, result, context)
        self.assertEqual(page["outcome"], "available", page)
        self.assertEqual(page["access_bases"][0]["context"], context)

    def test_null_origin_hydration_rechecks_its_first_checkpoint_delivery_only(
        self,
    ) -> None:
        run = self.r.start()
        _, result = self.r.query(run, STATEMENTS, page_size=50)
        self.assertEqual(result["outcome"], "available", result)
        first = self.r.send(self.append(run, result))
        self.assert_checkpoint(first)
        new = self.r.start()
        restored = self.r.send(self.read(new, first["branch"]["head"]))
        self.assert_checkpoint(restored)
        context = restored["root_contexts"][0]
        # This run's first delivery of the evidence is the checkpoint-context page.
        page = self.contextual_page(new, result, context)
        self.assertEqual(page["outcome"], "available", page)
        source_ref, pin, _ = self.normalized_evidence(new, result, page)
        hydration = self.h.hydration(new, pin)
        hydration["selections"][0]["evidence_ref"] = source_ref["ref"]
        self.assertIsNone(hydration["selections"][0]["origin"])
        raw, hydrated = self.h.send(hydration)
        self.h.available(raw, hydrated)
        self.assertEqual(
            hydrated["hydration"]["slices"][0]["content"], self.h.TEXT[:512]
        )
        self.assertEqual(
            [
                (b["context"], b["end_condition"], b["hold_ref"])
                for b in hydrated["access_bases"]
            ],
            [(context, "checkpoint_expiry_or_prune", context["checkpoint_id"])],
        )
        # Prune that checkpoint with the 21st append. The root stays live
        # standalone and is delivered again in this run, yet the null origin
        # still re-checks only its first delivery's context: no fallback.
        current = first
        for _ in range(20):
            current = self.r.send(self.append(run, result, current))
            self.assert_checkpoint(current)
        self.assertEqual(
            self.r.send(self.read(new, first["branch"]["head"]))["outcome"],
            "unavailable",
        )
        standalone = self.r.reuse(new, result["result"], page_size=50)
        self.assertEqual(standalone["outcome"], "available", standalone)
        again = copy.deepcopy(hydration)
        again["step_key"] = str(uuid4())
        _, refused = self.h.send(again)
        self.assertEqual(refused["outcome"], "unavailable", refused)
        for field in ("hydration", "inspection", "result", "page", "visibility"):
            self.assertNotIn(field, refused)
        self.assertNotIn(self.h.TEXT[:64], json.dumps(refused))

    def test_checkpoint_deliveries_attribute_all_their_audit_to_its_allowance(
        self,
    ) -> None:
        # Modeled on #91's attribution case. Both ways: every audit row that a
        # checkpoint append, a read, and contextual reuse, inspection and
        # hydration write is attributed to its own delivery, and no row outside
        # a live delivery is. Retained audit is charged to the audit allowance,
        # never to the 512 MiB content allocation.
        before = self.h.scalar(
            "SELECT count(*) FROM memoriesql.authorization_audit_events"
        )
        run = self.r.start()
        _, result = self.r.query(run, STATEMENTS, page_size=50)
        self.assertEqual(result["outcome"], "available", result)
        appended = self.r.send(self.append(run, result))
        self.assert_checkpoint(appended)
        new = self.r.start()
        restored = self.r.send(self.read(new, appended["branch"]["head"]))
        self.assert_checkpoint(restored)
        context = restored["root_contexts"][0]
        page = self.contextual_page(new, result, context)
        self.assertEqual(page["outcome"], "available", page)
        inspection = self.h.inspection(new, beads=(), facets=("content",))
        inspection.update(
            result={"pin": self.pin(result), "access": context},
            known_at=result["result"]["frame"]["known_at"],
        )
        raw, inspected = self.h.send(inspection)
        self.h.available(raw, inspected)
        self.assertEqual(inspected["access_bases"][0]["context"], context)
        source_ref, pin, direct = self.normalized_evidence(new, result, page)
        hydration = self.h.hydration(new, pin)
        hydration["selections"][0]["evidence_ref"] = source_ref["ref"]
        hydration["selections"][0]["origin"] = {
            "result": self.pin(result),
            "access": context,
        }
        raw, hydrated = self.h.send(hydration)
        self.h.available(raw, hydrated)
        operations = {
            name: reply["access_receipt_ref"]
            for name, reply in (
                ("query", result),
                ("checkpoint", appended),
                ("read_checkpoint", restored),
                ("reuse", page),
                ("inspect", inspected),
                ("evidence_metadata", direct),
                ("hydrate", hydrated),
            )
        }
        rows = self.db.execute(audit.ATTRIBUTED + audit.ORDER).fetchall()
        per_delivery = Counter(str(row[5]) for row in rows)
        for name, delivery in operations.items():
            self.assertGreater(per_delivery[delivery], 0, name)
        self.assertEqual(set(per_delivery), set(operations.values()))
        for *_head, recorded, _ref, space, expires in rows:
            self.assertEqual(space, self.fixture.workspace)
            self.assertEqual(expires - recorded, timedelta(days=30))
        written = self.h.scalar(
            "SELECT count(*) FROM memoriesql.authorization_audit_events"
        )
        self.assertEqual(written - before, len(rows))
        status = json.loads(self.r.service().cleanup_status())["audit_retention"]
        self.assertEqual(
            status["retained"],
            {"audit_rows": len(rows), "charged_bytes": len(rows) * audit.ROW_BYTES},
        )
        self.assertEqual(status["allowance_bytes"], 2147483648)
        self.assertEqual((status["due_rows"], status["overdue_rows"]), (0, 0))
        # The content allocation does not count that retained audit: a
        # reservation that fills the 512 MiB exactly is still admitted.
        used = self.h.scalar(
            "SELECT COALESCE(sum(CASE WHEN state='reserved' THEN reservation_bytes "
            "ELSE allocation_bytes END),0) FROM memoriesql.result_preparation_operations "
            "WHERE workspace_id=%s",
            (self.fixture.workspace,),
        )
        padding = audit.WORKSPACE_BYTES - 8192 - used
        while padding > 0:
            amount = min(padding, 67108864)
            self.db.execute(
                "INSERT INTO memoriesql.result_preparation_operations(tenant_id,workspace_id,"
                "principal_id,owner_user_id,run_ref,step_key,operation_ref,ownership_ref,"
                "request_fingerprint,state,reservation_bytes,encoded_bytes,allocation_bytes) "
                "VALUES(%s,%s,%s,NULL,%s,%s,%s,%s,%s,'sealed',%s,0,%s)",
                (
                    self.fixture.tenant,
                    self.fixture.workspace,
                    self.fixture.principal,
                    uuid4(),
                    uuid4(),
                    uuid4(),
                    uuid4(),
                    "f" * 64,
                    max(8192, amount),
                    amount,
                ),
            )
            padding -= amount
        with self.fixture.connection() as connection:
            PostgresResultPreparation(
                connection,
                credential_sha256=self.fixture.secret_hash,
                workspace_id=self.fixture.workspace,
            ).reserve(
                run_ref=UUID(new["run_ref"]),
                step_key=uuid4(),
                request_fingerprint="e" * 64,
                reservation_bytes=8192,
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
                    # sum(bigint) is numeric; the reserve call takes bigint.
                    "SELECT COALESCE(sum(CASE WHEN state='reserved' THEN reservation_bytes ELSE allocation_bytes END),0)::bigint FROM memoriesql.result_preparation_operations WHERE workspace_id=%s",
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

    def assert_checkpoint_refusal_at_disclosure(
        self, request: dict[str, Any], arm: Callable[[], None],
        expires: sql.Composable, valid_at: Callable[[sql.SQL], sql.Composable],
    ) -> None:
        step = UUID(request["step_key"])
        marker = "fictional checkpoint authority crossed receipt fence"
        self.db.execute(sql.SQL("""
            CREATE FUNCTION memoriesql.fixture_checkpoint_receipt_fence() RETURNS trigger
            LANGUAGE plpgsql SET search_path=pg_catalog,memoriesql AS $fixture$
            BEGIN
              IF NOT EXISTS(SELECT 1 FROM memoriesql.query_deliveries d
                 WHERE d.tenant_id=NEW.tenant_id AND d.delivery_ref=NEW.delivery_ref
                  AND d.step_key={step}::uuid) THEN RETURN NEW; END IF;
              IF ({live_now}) IS DISTINCT FROM true THEN
                RAISE EXCEPTION 'fictional authority ended before the receipt';
              END IF;
              PERFORM pg_sleep_until({expires} + interval '10 milliseconds');
              IF ({live_at_statement}) IS DISTINCT FROM true
                 OR ({live_now}) IS DISTINCT FROM false THEN
                RAISE EXCEPTION 'fictional authority did not cross at the receipt';
              END IF;
              RAISE NOTICE USING MESSAGE={marker};
              RETURN NEW;
            END $fixture$
        """).format(
            step=sql.Literal(step), expires=expires, marker=sql.Literal(marker),
            live_now=valid_at(sql.SQL("clock_timestamp()")),
            live_at_statement=valid_at(sql.SQL("statement_timestamp()")),
        ))
        self.addCleanup(self.db.execute, "DROP FUNCTION IF EXISTS memoriesql.fixture_checkpoint_receipt_fence()")
        checkpoint = PostgresAgentSqlResults._checkpoint
        notices: list[str] = []

        def collect(notice: Any) -> None:
            if notice.message_primary == marker:
                notices.append(marker)

        def at_disclosure(
            service: PostgresAgentSqlResults, action: CheckpointAction, admission: dict[str, Any],
            start: float, deadline: float, owner: psycopg.Connection[Any],
        ) -> bytes:
            self.assertEqual(action.step_key, str(step))
            arm()
            owner.add_notice_handler(collect)
            try:
                return checkpoint(service, action, admission, start, deadline, owner)
            finally:
                owner.remove_notice_handler(collect)

        self.db.execute(
            "CREATE TRIGGER fixture_checkpoint_receipt_fence AFTER INSERT ON memoriesql.query_disclosures "
            "FOR EACH ROW EXECUTE FUNCTION memoriesql.fixture_checkpoint_receipt_fence()"
        )
        try:
            with patch.object(PostgresAgentSqlResults, "_checkpoint", at_disclosure):
                refused = self.r.send(request)
        finally:
            self.db.execute("DROP TRIGGER fixture_checkpoint_receipt_fence ON memoriesql.query_disclosures")
            self.db.execute("DROP FUNCTION memoriesql.fixture_checkpoint_receipt_fence()")
        self.assertEqual(notices, [marker], refused)
        self.assertEqual((refused["outcome"], refused["error"]), ("unavailable", {"code": "unavailable"}))
        self.assertEqual(
            self.db.execute(
                "SELECT state,named_save_ref,named_save_revision,checkpoint_pending,checkpoint_ref "
                "FROM memoriesql.query_steps WHERE step_key=%s", (step,),
            ).fetchone(),
            ("failed", None, None, None, None),
        )
        self.assertEqual(
            self.db.execute(
                "SELECT (SELECT count(*) FROM memoriesql.result_preparation_operations WHERE step_key=%s),"
                " (SELECT count(*) FROM memoriesql.query_disclosures q JOIN memoriesql.query_deliveries d"
                " USING(tenant_id,delivery_ref) WHERE d.step_key=%s)", (step, step),
            ).fetchone(),
            (0, 0),
        )

    def test_named_checkpoint_resolves_fresh_revisions_and_releases_after_revocation(self) -> None:
        scope, source = relation_agents.explicit_scope(self.db, self.fixture)
        text = "Fictional orchard observation under a leased scope."
        self.r.normalized_bead(text, "named-orchard", raw=text, source=source, scope=scope)
        statement = "SELECT s.text FROM memory_v1.statements s WHERE s.text=$1"
        parameters = [{"position": 1, "type": "text", "value": text}]
        run = self.r.start()
        _, result = self.r.query(run, statement, parameters=parameters)
        self.assertEqual(result["outcome"], "available", result)
        self.assertEqual([row["values"] for row in result["page"]["rows"]], [[text]])
        # Use real pre-upgrade artifacts, including a separate automatic
        # checkpoint whose expiry must prevent conversion at the receipt.
        first = self.r.send(self.append(run, result))
        expiring = self.r.send(self.append(run, result))
        self.assert_checkpoint(first)
        self.assert_checkpoint(expiring)
        migrate(self.db, expected_current_version=51, target_version=66)
        first_pin = first["branch"]["head"]
        expiring_pin = expiring["branch"]["head"]
        expiring_id = UUID(expiring_pin["checkpoint_id"])
        cutoff = sql.SQL("(SELECT expires_at FROM memoriesql.query_checkpoints WHERE checkpoint_id={}::uuid)").format(
            sql.Literal(expiring_id)
        )

        def arm_automatic() -> None:
            updated = self.db.execute(
                "WITH cutoff AS (SELECT clock_timestamp()+interval '3 seconds' AS at) "
                "UPDATE memoriesql.query_checkpoints SET expires_at=cutoff.at,"
                " created_at=cutoff.at-interval '720 hours' FROM cutoff "
                "WHERE checkpoint_id=%s RETURNING checkpoint_id", (expiring_id,),
            ).fetchone()
            self.assertEqual(updated, (expiring_id,))

        self.assert_checkpoint_refusal_at_disclosure(
            self.read(run, expiring_pin) | {"kind": "save_checkpoint", "name": "fictional-expiring-auto"},
            arm_automatic, cutoff, lambda at: sql.SQL("{} < {}").format(at, cutoff),
        )
        self.assertIsNone(self.db.execute(
            "SELECT save_id FROM memoriesql.query_named_saves WHERE name='fictional-expiring-auto'"
        ).fetchone())
        save = self.read(run, first_pin) | {"kind": "save_checkpoint", "name": "fictional-orchard"}
        saved = self.r.send(save)
        self.assertEqual(saved["outcome"], "available", saved)
        binding = saved["save"]
        self.assertEqual((binding["revision"], binding["checkpoint"]), ("1", first_pin))
        self.assertEqual(saved["retention"]["named_holds"], [{"save_id": binding["save_id"], "revision": "1"}])
        replayed = self.r.send(save)
        self.assertEqual(replayed["save"], binding)
        self.assertEqual(replayed["receipt_ref"], saved["receipt_ref"])
        self.assertNotEqual(replayed["access_receipt_ref"], saved["access_receipt_ref"])
        self.assertEqual(
            self.db.execute(
                "SELECT allocation_bytes,new_allocation_bytes FROM memoriesql.result_preparation_operations "
                "WHERE run_ref=%s AND step_key=%s", (run["run_ref"], save["step_key"]),
            ).fetchall(), [(8192, 8192)],
        )
        conflict = self.r.send(save | {"step_key": str(uuid4())})
        self.assertEqual((conflict["outcome"], conflict["error"]["code"]), ("invalid_request", "save_conflict"))

        _, other_secret = self.fixture.second_human(scope)
        outsider = self.r.start(other_secret)
        try:
            _, own_rows = self.r.query(outsider, statement, parameters=parameters, secret=other_secret)
            self.assertEqual(own_rows["outcome"], "available", own_rows)
            self.assertEqual([row["values"] for row in own_rows["page"]["rows"]], [[text]])
            foreign_name = {
                "contract_version": 1, "run_ref": outsider["run_ref"],
                "investigation_id": binding["investigation_id"], "name": binding["name"],
            }
            foreign_context = {
                "checkpoint": first_pin,
                "basis": {"kind": "named_save", "save_id": binding["save_id"], "revision": "1"},
            }
            for action in (
                self.read(outsider, first_pin) | {"access": foreign_context},
                self.read(outsider, first_pin) | {"kind": "save_checkpoint", "name": binding["name"]},
                foreign_name | {"kind": "resolve_save", "step_key": str(uuid4())},
                foreign_name | {
                    "kind": "release_save", "step_key": str(uuid4()), "expected_save_revision": "1",
                },
            ):
                denied = self.r.send(action, secret=other_secret)
                self.assertEqual((denied["outcome"], denied["error"]), ("unavailable", {"code": "unavailable"}))
                self.assertTrue({"save", "checkpoint", "retention", "released"}.isdisjoint(denied))
        finally:
            self.r.close(outsider, other_secret)

        fresh = self.r.start()
        self.assertNotEqual(fresh["run_ref"], run["run_ref"])
        resolve = {
            "contract_version": 1, "kind": "resolve_save",
            "run_ref": fresh["run_ref"], "step_key": str(uuid4()),
            "investigation_id": first["checkpoint"]["investigation_id"], "name": "fictional-orchard",
        }
        resolved = self.r.send(resolve)
        self.assertEqual(resolved["outcome"], "available", resolved)
        self.assertEqual(resolved["save"], binding)
        self.assertEqual(resolved["access"], {
            "checkpoint": first_pin,
            "basis": {"kind": "named_save", "save_id": binding["save_id"], "revision": "1"},
        })
        for field in ("page", "result", "root_contexts", "checkpoint"):
            self.assertNotIn(field, resolved)

        second = self.r.send(self.append(run, result, previous=first))
        self.assert_checkpoint(second)
        second_pin = second["branch"]["head"]
        retarget = self.r.send(self.read(fresh, second_pin) | {
            "kind": "save_checkpoint", "name": binding["name"], "expected_save_revision": "1",
        })
        self.assertEqual(retarget["outcome"], "available", retarget)
        self.assertEqual(
            (retarget["save"]["save_id"], retarget["save"]["revision"], retarget["save"]["checkpoint"]),
            (binding["save_id"], "2", second_pin),
        )
        stale = self.r.send(self.read(fresh, first_pin) | {"access": resolved["access"]})
        self.assertEqual((stale["outcome"], stale["error"]), ("unavailable", {"code": "unavailable"}))
        self.assertEqual(self.r.send(resolve)["outcome"], "unavailable")
        self.assertEqual(self.r.send(self.read(fresh, first_pin))["outcome"], "available")

        # Exercise the actual cleanup path after the target's automatic slot
        # ends. Its named revision alone must preserve the original manifest.
        self.db.execute(
            "UPDATE memoriesql.query_checkpoints SET created_at=created_at-interval '31 days',"
            " expires_at=expires_at-interval '31 days' WHERE checkpoint_id=%s",
            (second_pin["checkpoint_id"],),
        )
        self.r.service().cleanup_expired()
        self.assertEqual(self.r.send(self.read(fresh, second_pin))["outcome"], "unavailable")
        current = self.r.send(resolve | {"step_key": str(uuid4())})
        self.assertEqual(current["outcome"], "available", current)
        self.assertEqual(current["save"], retarget["save"])
        self.assertEqual(current["access"]["basis"], {
            "kind": "named_save", "save_id": binding["save_id"], "revision": "2",
        })
        self.assertFalse(current["retention"]["automatic_retained"])
        retained = self.r.send(self.read(fresh, second_pin) | {"access": current["access"]})
        self.assertEqual(retained["outcome"], "available", retained)
        self.assertEqual(retained["checkpoint"], second["checkpoint"])
        context, = retained["root_contexts"]
        self.assertEqual(context["basis"], current["access"]["basis"])

        lease_cutoff = sql.SQL("""(SELECT v.expires_at FROM memoriesql.access_grants g
            JOIN memoriesql.access_grant_revisions v USING(tenant_id,workspace_id,access_scope_id,grant_id)
            WHERE g.tenant_id={}::uuid AND g.access_scope_id={}::uuid AND g.target_principal_id={}::uuid
            ORDER BY v.revision DESC LIMIT 1)""").format(
            sql.Literal(self.fixture.tenant), sql.Literal(scope), sql.Literal(self.fixture.principal),
        )

        def arm_scope() -> None:
            self.r.set_refinement_scope_lease(scope, 1, timedelta(seconds=3))

        self.assert_checkpoint_refusal_at_disclosure(
            self.read(fresh, second_pin) | {"access": current["access"]},
            arm_scope, lease_cutoff,
            lambda at: sql.SQL("memoriesql.current_context_scope_time_authorized({}::uuid,'read',{})").format(
                sql.Literal(scope), at,
            ),
        )
        self.r.set_refinement_scope_lease(scope, 2, timedelta(hours=1))
        self.r.expire_refinement_parent(self.pin(result)["result_id"])
        self.assertEqual(self.r.reuse(fresh, result["result"])["outcome"], "unavailable")
        reopened = self.r.send(self.read(fresh, second_pin) | {
            "kind": "reuse_result", "result": self.pin(result), "access": context, "page_size": 1,
        })
        self.assertEqual(reopened["outcome"], "available", reopened)
        self.assertEqual(reopened["access_bases"][0]["end_condition"], "save_release")
        self.assertIsNone(reopened["access_bases"][0]["valid_until"])
        self.assertEqual(reopened["access_bases"][0]["hold_ref"], binding["save_id"])

        self.db.execute(
            "UPDATE memoriesql.protected_resources SET status='revoked',revoked_at=clock_timestamp() "
            "WHERE resource_kind='source' AND resource_id=%s",
            (source,),
        )
        denied = self.r.send(resolve | {"step_key": str(uuid4())})
        unknown = self.r.send(resolve | {"step_key": str(uuid4()), "name": "fictional-unknown"})
        for refusal in (denied, unknown):
            self.assertEqual((refusal["outcome"], refusal["error"]), ("unavailable", {"code": "unavailable"}))
            self.assertEqual(set(refusal), set(unknown))
            for field in ("save", "access", "retention", "checkpoint"):
                self.assertNotIn(field, refusal)
        release = resolve | {"step_key": str(uuid4()), "kind": "release_save", "expected_save_revision": "2"}
        released = self.r.send(release)
        self.assertEqual(released["outcome"], "available", released)
        self.assertEqual(released["released"], {"save_id": binding["save_id"], "revision": "3", "state": "released"})
        for field in ("save", "access", "retention", "checkpoint"):
            self.assertNotIn(field, released)
        self.assertEqual(released["work"]["retained_bytes"], 0)
        self.assertEqual(
            self.db.execute(
                "SELECT manifest_digest,(SELECT count(*) FROM memoriesql.query_checkpoint_holds h "
                "WHERE h.tenant_id=cp.tenant_id AND h.checkpoint_id=cp.checkpoint_id),"
                " EXISTS(SELECT 1 FROM memoriesql.result_preparation_artifacts a "
                "WHERE a.tenant_id=cp.tenant_id AND a.artifact_ref=cp.checkpoint_id) "
                "FROM memoriesql.query_checkpoints cp WHERE cp.checkpoint_id=%s",
                (second_pin["checkpoint_id"],),
            ).fetchone(), (None, 0, False),
        )
        self.assertEqual(self.r.send(resolve | {"step_key": str(uuid4())})["outcome"], "unavailable")
        again = self.r.send(release)
        self.assertEqual(again["released"], released["released"])
        self.assertEqual(again["receipt_ref"], released["receipt_ref"])
        self.assertNotEqual(again["access_receipt_ref"], released["access_receipt_ref"])

    def test_restore_and_branch_pin_only_selected_roots_and_keep_working_state_authorized(
        self,
    ) -> None:
        # Deliver the witness root in a separate run. The authored checkpoint
        # learns this source handle only through an exact-origin hydration.
        origin_run = self.r.start()
        _, witness_root = self.r.query(origin_run, STATEMENTS, page_size=50)
        self.assertEqual(witness_root["outcome"], "available", witness_root)
        self.r.close(origin_run)

        run = self.r.start()
        _, first = self.r.query(
            run, "SELECT bead_id,bead_version_id FROM memory_v1.statements WHERE text=$1",
            parameters=[{"position": 1, "type": "text", "value": self.h.TEXT}],
        )
        self.assertEqual(first["outcome"], "available", first)
        first_row, = first["page"]["rows"]
        selected_observation, = first_row["evidence_refs"]

        witness_pin = self.pin(witness_root)
        witness_ref, part, _ = self.normalized_evidence(run, witness_root, witness_root)
        hydration = self.h.hydration(run, part)
        hydration["selections"][0].update(
            evidence_ref=witness_ref["ref"],
            origin={"result": witness_pin, "access": {"kind": "standalone"}},
        )
        raw, hydrated = self.h.send(hydration)
        self.h.available(raw, hydrated)
        self.assertEqual(hydrated["hydration"]["slices"][0]["content"], self.h.TEXT[:512])

        scope, source = relation_agents.explicit_scope(self.db, self.fixture)
        observation = "Fictional orchard observation available only to the later root."
        self.r.normalized_bead(observation, "restore-orchard", raw=observation, source=source, scope=scope)
        wide_statement = "Fictional wide harvest of astral and accented pears."
        _, second = self.r.query(
            run, "SELECT bead_id,bead_version_id,text FROM memory_v1.statements WHERE text=$1 OR text=$2",
            parameters=[
                {"position": 1, "type": "text", "value": observation},
                {"position": 2, "type": "text", "value": wide_statement},
            ],
            known_at=self.h.scalar("SELECT memoriesql.relation_packet_time(clock_timestamp())"),
            page_size=50,
        )
        self.assertEqual(second["outcome"], "available", second)
        self.assertIn(observation, [r["values"][2] for r in second["page"]["rows"]])
        wide = next(row for row in second["page"]["rows"] if row["values"][0] == str(self.h.wide))
        unselected_observation, = wide["evidence_refs"]
        first_pin, second_pin = self.pin(first), self.pin(second)
        self.assertTrue(self.h.scalar(
            "SELECT EXISTS(SELECT 1 FROM memoriesql.result_preparation_artifacts a"
            " CROSS JOIN LATERAL jsonb_array_elements(convert_from(decode("
            " (convert_from(a.dependency_bytes,'UTF8')::jsonb)->>'records_base64','base64'),'UTF8')::jsonb) r(record)"
            " WHERE a.artifact_ref=%s AND record->>'kind'='accepted_bead' AND record->>'id'=%s)",
            (first_pin["result_id"], unselected_observation["version_ref"]),
        ))
        append = self.append(run, first)
        append["roots"].extend([
            {"result": witness_pin, "access": {"kind": "standalone"}},
            {"result": second_pin, "access": {"kind": "standalone"}},
        ])
        append["findings"] = [
            {"text": "selected delivered fact", "facts": [first_row["fact_ref"]], "evidence_refs": []},
            {"text": "selected delivered evidence", "facts": [], "evidence_refs": [selected_observation]},
            {"text": "selected exact-origin witness", "facts": [], "evidence_refs": [witness_ref]},
            {"text": "unselected fact", "facts": [wide["fact_ref"]], "evidence_refs": []},
            {"text": "unselected searched evidence", "facts": [], "evidence_refs": [unselected_observation]},
            {"text": "mixed finding", "facts": [first_row["fact_ref"]], "evidence_refs": [unselected_observation]},
            {"text": "unattributed narrative", "facts": [], "evidence_refs": []},
        ]
        original = self.r.send(append)
        self.assert_checkpoint(original)
        original_pin = original["branch"]["head"]
        original_id = original_pin["checkpoint_id"]
        migrate(self.db, expected_current_version=51, target_version=67)
        fresh = self.r.start()
        restored_request = self.read(fresh, original_pin) | {
            "kind": "restore_checkpoint", "selected_roots": [first_pin, witness_pin],
        }
        # Arm the original automatic route after admission. The shared
        # fixture marker proves expiry crossed inside the settlement statement
        # and that the staged branch, allocation, and receipt all roll back.
        predicate = sql.SQL("(SELECT expires_at FROM memoriesql.query_checkpoints WHERE checkpoint_id={}::uuid)").format(
            sql.Literal(UUID(original_id))
        )

        def arm_original() -> None:
            self.db.execute(
                "WITH cutoff AS (SELECT clock_timestamp()+interval '3 seconds' AS at)"
                " UPDATE memoriesql.query_checkpoints SET expires_at=cutoff.at,"
                " created_at=cutoff.at-interval '720 hours' FROM cutoff WHERE checkpoint_id=%s",
                (original_id,),
            )

        self.assert_checkpoint_refusal_at_disclosure(
            restored_request | {"step_key": str(uuid4())}, arm_original, predicate,
            lambda checked_at: sql.SQL("{} < {}").format(checked_at, predicate),
        )
        self.assertEqual(self.h.scalar("SELECT count(*) FROM memoriesql.query_investigation_branches"), 1)
        self.assertEqual(self.h.scalar("SELECT count(*) FROM memoriesql.result_preparation_parent_holds"), 0)
        self.db.execute(
            "UPDATE memoriesql.query_checkpoints SET created_at=%s,expires_at=%s WHERE checkpoint_id=%s",
            (original["checkpoint"]["created_at"], original["checkpoint"]["automatic_expires_at"], original_id),
        )

        # The later root's source lease expires at the receipt while both
        # selected roots and the source checkpoint's automatic slot stay live.
        scope_cutoff = sql.SQL("""(SELECT v.expires_at FROM memoriesql.access_grants g
            JOIN memoriesql.access_grant_revisions v USING(tenant_id,workspace_id,access_scope_id,grant_id)
            WHERE g.tenant_id={}::uuid AND g.access_scope_id={}::uuid AND g.target_principal_id={}::uuid
            ORDER BY v.revision DESC LIMIT 1)""").format(
            sql.Literal(self.fixture.tenant), sql.Literal(scope), sql.Literal(self.fixture.principal),
        )
        def arm_unselected_scope() -> None:
            self.r.set_refinement_scope_lease(scope, 1, timedelta(seconds=3))

        self.assert_checkpoint_refusal_at_disclosure(
            restored_request | {"step_key": str(uuid4())}, arm_unselected_scope, scope_cutoff,
            lambda checked_at: sql.SQL("memoriesql.current_context_scope_time_authorized({}::uuid,'read',{})").format(
                sql.Literal(scope), checked_at,
            ),
        )
        self.assertEqual(self.h.scalar("SELECT count(*) FROM memoriesql.query_investigation_branches"), 1)
        self.assertEqual(self.h.scalar("SELECT count(*) FROM memoriesql.result_preparation_parent_holds"), 0)
        self.assertTrue(self.h.scalar(
            "SELECT automatic_retained AND expires_at>clock_timestamp() FROM memoriesql.query_checkpoints "
            "WHERE checkpoint_id=%s", (original_id,),
        ))
        self.assertEqual(self.r.reuse(fresh, first["result"], page_size=1)["outcome"], "available")
        self.assertEqual(self.r.reuse(fresh, witness_root["result"], page_size=1)["outcome"], "available")
        self.assertEqual(self.r.reuse(fresh, second["result"], page_size=1)["outcome"], "unavailable")
        self.r.set_refinement_scope_lease(scope, 2, timedelta(hours=1))

        visible = self.h.scalar("SELECT count(*) FROM memoriesql.query_visible_refs")
        restored = self.r.send(restored_request)
        self.assert_checkpoint(restored)
        self.assertEqual(self.h.scalar("SELECT count(*) FROM memoriesql.query_visible_refs"), visible)
        head = restored["branch"]["head"]
        manifest = restored["checkpoint"]
        self.assertNotEqual(manifest["branch_id"], original["checkpoint"]["branch_id"])
        self.assertEqual(restored["branch"], {
            "branch_id": manifest["branch_id"], "investigation_id": original["checkpoint"]["investigation_id"],
            "head": head, "fork_origin": original_pin, "created_at": manifest["created_at"],
        })
        self.assertEqual((manifest["sequence"], manifest["predecessor"]), ("1", None))
        self.assertEqual((manifest["fork_origin"], manifest["working_state_source"]), (original_pin, original_pin))
        for field in ("question", "progress"):
            self.assertEqual(manifest[field], original["checkpoint"][field])
        self.assertEqual(manifest["findings"], append["findings"][:3])
        self.assertEqual(manifest["roots"], original["checkpoint"]["roots"][:2])
        context = {"kind": "checkpoint", **head, "basis": {"kind": "automatic"}, "root": first_pin}
        self.assertEqual(restored["root_contexts"], [context, context | {"root": witness_pin}])
        cp_id = head["checkpoint_id"]
        op = self.db.execute(
            "SELECT o.encoded_bytes,o.allocation_bytes,o.new_allocation_bytes,"
            " (SELECT count(*) FROM memoriesql.query_checkpoint_holds h WHERE h.checkpoint_id=cp.checkpoint_id),"
            " (SELECT parent_ref::text FROM memoriesql.result_preparation_parent_holds h WHERE h.child_ref=cp.checkpoint_id)"
            " FROM memoriesql.query_checkpoints cp JOIN memoriesql.result_preparation_operations o USING(tenant_id,operation_ref)"
            " WHERE cp.checkpoint_id=%s", (cp_id,),
        ).fetchone()
        self.assertIsNotNone(op)
        assert op is not None
        encoded, charged, new_charge, holds, parent = op
        expected_charge = encoded + 8192 + 512 + 32768 + 2 * 1024
        self.assertEqual((charged, new_charge, holds, parent), (expected_charge, expected_charge, 2, original_id))

        reuse = self.read(fresh, head) | {
            "kind": "reuse_result", "result": first_pin, "access": context, "page_size": 1,
        }
        reused = self.r.send(reuse)
        self.assertEqual(reused["outcome"], "available", reused)
        self.assertEqual(reused["page"]["rows"], first["page"]["rows"])
        self.assertEqual(reused["access_bases"][0]["context"], context)
        excluded = self.r.send(reuse | {
            "step_key": str(uuid4()), "result": second_pin, "access": context | {"root": second_pin},
        })
        self.assertEqual((excluded["outcome"], excluded["error"]), ("unavailable", {"code": "unavailable"}))
        conflict = self.r.send(restored_request | {"selected_roots": []})
        self.assertEqual(conflict["outcome"], "idempotency_conflict", conflict)
        invalid_selection = self.r.send(restored_request | {
            "step_key": str(uuid4()), "selected_roots": [second_pin | {"content_digest": "f" * 64}],
        })
        self.assertEqual((invalid_selection["outcome"], invalid_selection["error"]),
                         ("unavailable", {"code": "unavailable"}))

        name = "fictional-restore-origin"
        saved = self.r.send(self.read(run, original_pin) | {"kind": "save_checkpoint", "name": name})
        self.assertEqual(saved["outcome"], "available", saved)

        def expire(checkpoint_id: str) -> None:
            # Fixture clock control. Artifact and its exact pin stay untouched.
            self.db.execute(
                "UPDATE memoriesql.query_checkpoints SET created_at=created_at-interval '744 hours',"
                " expires_at=expires_at-interval '744 hours' WHERE checkpoint_id=%s", (checkpoint_id,),
            )

        expire(original_id)
        self.r.service().cleanup_expired()
        source_read = self.r.send(self.read(fresh, original_pin))
        self.assertEqual(source_read["outcome"], "unavailable", source_read)
        resolve = {
            "contract_version": 1, "kind": "resolve_save", "run_ref": fresh["run_ref"],
            "step_key": str(uuid4()), "investigation_id": manifest["investigation_id"], "name": name,
        }
        named = self.r.send(resolve)
        self.assertEqual(named["outcome"], "available", named)
        self.assertEqual(named["access"]["checkpoint"], original_pin)
        fork_request = restored_request | {
            "kind": "branch_investigation", "step_key": str(uuid4()), "access": named["access"],
            "selected_roots": [],
        }
        empty = self.r.send(fork_request)
        self.assert_checkpoint(empty)
        self.assertEqual((empty["root_contexts"], empty["checkpoint"]["findings"], empty["checkpoint"]["roots"]), ([], [], []))
        self.assertEqual(empty["checkpoint"]["working_state_source"], original_pin)
        self.assertEqual(empty["checkpoint"]["question"], original["checkpoint"]["question"])
        self.assertNotIn(empty["branch"]["branch_id"], {manifest["branch_id"], original["checkpoint"]["branch_id"]})
        self.assertEqual(empty["checkpoint"]["investigation_sequence"], "3")

        released = self.r.send(resolve | {
            "kind": "release_save", "step_key": str(uuid4()), "expected_save_revision": "1",
        })
        self.assertEqual(released["outcome"], "available", released)
        stale = self.r.send(fork_request | {"step_key": str(uuid4())})
        self.assertEqual((stale["outcome"], stale["error"]), ("unavailable", {"code": "unavailable"}))
        for original_reply, request in ((restored, restored_request), (empty, fork_request)):
            replay = self.r.send(request)
            self.assertEqual(replay["checkpoint"], original_reply["checkpoint"])
            self.assertEqual(replay["receipt_ref"], original_reply["receipt_ref"])
            self.assertNotEqual(replay["access_receipt_ref"], original_reply["access_receipt_ref"])
        self.assertEqual(self.h.scalar("SELECT count(*) FROM memoriesql.query_investigation_branches"), 3)
        self.assert_checkpoint(original)  # The working holds still pin the pre-upgrade bytes.

        self.db.execute(
            "UPDATE memoriesql.protected_resources SET status='revoked',revoked_at=clock_timestamp()"
            " WHERE resource_kind='source' AND resource_id=%s", (source,),
        )
        standalone = self.r.reuse(fresh, first["result"], page_size=1)
        self.assertEqual(standalone["outcome"], "available", standalone)
        empty_head = empty["branch"]["head"]
        for request in (self.read(fresh, head), self.read(fresh, empty_head), restored_request, fork_request,
                        reuse | {"step_key": str(uuid4())}):
            denied = self.r.send(request)
            self.assertEqual((denied["outcome"], denied["error"]), ("unavailable", {"code": "unavailable"}))
            self.assertTrue({"checkpoint", "branch", "root_contexts", "page"}.isdisjoint(denied))

        expire(cp_id)
        self.r.service().cleanup_expired()
        self.assert_checkpoint(original)  # The empty branch is the final holder.
        expire(empty_head["checkpoint_id"])
        self.r.service().cleanup_expired()
        self.assertEqual(self.h.scalar(
            "SELECT count(*) FROM memoriesql.result_preparation_artifacts WHERE artifact_ref=ANY(%s)",
            ([UUID(pin) for pin in (original_id, cp_id, empty_head["checkpoint_id"])],),
        ), 0)
        self.assertEqual(self.h.scalar(
            "SELECT count(*) FROM memoriesql.result_preparation_parent_holds WHERE parent_ref=%s", (original_id,),
        ), 0)
        self.assertEqual(self.h.scalar(
            "SELECT count(*) FROM memoriesql.query_checkpoint_holds WHERE checkpoint_id=%s", (original_id,),
        ), 0)

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
