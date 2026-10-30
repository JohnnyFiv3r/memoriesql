"""Owner re-approval of a supervised task after its approval failed, at head schema.

Fictional data in a disposable database, composing the managed-dispatch fixture.
A failed turn stays charged to its approval; an administrator revokes it and
provisions a replacement that supersedes it; the owner resumes the task; the
replacement allows one fresh dispatch, and only it can authorize meaning.
"""

from __future__ import annotations

import asyncio
import unittest
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import psycopg
from psycopg.types.json import Jsonb

from memoriesql.application.managed_dispatch import SupervisedQualification
from memoriesql.infrastructure.jobs.postgres_semantic_queue import (
    PostgresSemanticTaskQueue,
)
from memoriesql.infrastructure.postgres.migration_runner import discover_migrations

if TYPE_CHECKING:
    from tests.runtime import test_managed_dispatch as managed
    from tests.runtime.test_postgres_runtime import migrate
else:
    import test_managed_dispatch as managed
    from test_postgres_runtime import migrate

REFUSED = psycopg.errors.ObjectNotInPrerequisiteState


class SupervisedReapproval(unittest.TestCase):
    def setUp(self) -> None:
        self.h = managed.ManagedDispatchTests(methodName="runTest")
        self.addCleanup(self.h.doCleanups)
        self.h.setUp()
        migrate(
            self.h.db,
            expected_current_version=26,
            target_version=discover_migrations()[-1].version,
        )
        self.task = self.h.activation.execution_task_id

    def replacement(self) -> SupervisedQualification:
        return self.h.approval.model_copy(update={"qualification_id": uuid.uuid4()})

    def install(
        self, approval: SupervisedQualification, supersedes: SupervisedQualification | None
    ) -> None:
        h = self.h
        h.db.execute(
            "INSERT INTO memoriesql.model_supervised_qualifications(tenant_id,workspace_id,"
            "access_scope_id,qualification_id,task_id,origin_principal_id,configuration,"
            "approved_by_principal_id,supersedes_qualification_id) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                h.tenant,
                h.workspace,
                h.scope,
                approval.qualification_id,
                approval.semantic_task_id,
                h.principal,
                Jsonb(approval.model_dump(mode="json")),
                h.principal,
                supersedes.qualification_id if supersedes else None,
            ),
        )

    def revoke(self, approval: SupervisedQualification) -> None:
        self.h.db.execute(
            "UPDATE memoriesql.model_supervised_qualifications SET status='revoked' "
            "WHERE qualification_id=%s",
            (approval.qualification_id,),
        )

    def task_status(self) -> tuple[Any, ...]:
        return self.h.row(
            "SELECT status,pause_reason_code FROM memoriesql.semantic_tasks WHERE task_id=%s",
            (self.task,),
        )

    def intents_by_approval(self) -> dict[Any, int]:
        rows = self.h.db.execute(
            "SELECT supervised_qualification_id,count(*) FROM "
            "memoriesql.model_provider_request_intents GROUP BY 1"
        ).fetchall()
        return {row[0]: row[1] for row in rows}

    def reap(self, attempt_id: Any) -> None:
        # Test-only clock fixture, as the managed-dispatch restart case uses it.
        h = self.h
        with h.db.transaction():
            h.db.execute(
                "ALTER TABLE memoriesql.semantic_task_attempts DISABLE TRIGGER "
                "semantic_task_attempts_protect_state"
            )
            h.db.execute(
                "UPDATE memoriesql.semantic_tasks SET heartbeat_at=clock_timestamp()-interval "
                "'2 seconds',lease_expires_at=clock_timestamp()-interval '1 second' WHERE task_id=%s",
                (self.task,),
            )
            h.db.execute(
                "UPDATE memoriesql.semantic_task_attempts SET claimed_at=clock_timestamp()-"
                "interval '10 seconds',started_at=clock_timestamp()-interval '5 seconds',"
                "heartbeat_at=clock_timestamp()-interval '2 seconds',lease_expires_at="
                "clock_timestamp()-interval '1 second' WHERE attempt_id=%s",
                (attempt_id,),
            )
            h.db.execute(
                "ALTER TABLE memoriesql.semantic_task_attempts ENABLE TRIGGER "
                "semantic_task_attempts_protect_state"
            )
        with h.db.transaction():
            self.assertEqual(
                h.worker_queue().reap_expired(
                    worker_id="orchard.reaper", limit=1, reaped_at=datetime.now(UTC)
                ),
                1,
            )
        h.db.execute(
            "UPDATE memoriesql.semantic_tasks SET available_at=clock_timestamp() WHERE task_id=%s",
            (self.task,),
        )

    def resume(self) -> bool:
        with self.h.db.transaction():
            self.h.begin()
            return PostgresSemanticTaskQueue(self.h.db).resume(
                tenant_id=self.h.tenant, task_id=self.task, resumed_at=datetime.now(UTC)
            )

    def test_a_failed_turn_can_be_reapproved_and_only_the_replacement_accepts(
        self,
    ) -> None:
        h, first, second = self.h, self.h.approval, self.replacement()
        self.install(first, None)
        worker = h.managed_worker()
        saved: dict[str, Any] = {}

        def crash(claimed: Any, result: Any, sink: Any, **kwargs: Any) -> Any:
            saved.update(claimed=claimed)
            # While the attempt runs, no replacement is possible, even revoked.
            self.revoke(first)
            with self.assertRaises(REFUSED):
                self.install(second, first)
            raise RuntimeError("fictional crash before canonical apply")

        worker._persist_result = crash
        with self.assertRaisesRegex(RuntimeError, "fictional crash"):
            asyncio.run(worker.run_once())
        self.assertEqual(worker.fictional_model.calls, 1)
        h.assert_no_meaning()
        self.reap(saved["claimed"].fence.attempt_id)

        # The consumed, revoked approval cannot dispatch again: the task waits
        # for its owner instead of failing.
        restarted = h.managed_worker()
        self.assertNotEqual(asyncio.run(restarted.run_once()).task_status, "succeeded")
        self.assertEqual(restarted.fictional_model.calls, 0)
        self.assertEqual(self.task_status()[0], "policy_paused")

        # Only a replacement of the head is accepted; it is the one active link.
        stray = self.replacement()
        with self.assertRaises(REFUSED):
            self.install(stray, None)
        self.install(second, first)
        with self.assertRaises(psycopg.Error):
            self.install(stray, first)
        with self.assertRaises(psycopg.Error):
            self.install(stray, second)
        self.assertEqual(
            h.row("SELECT memoriesql.supervised_task_open(%s,%s)", (h.tenant, self.task)),
            (True,),
        )
        # The first approval's accounted turn cannot authorize meaning now.
        self.assertEqual(
            h.row(
                "SELECT memoriesql.supervised_acceptance_valid(%s,%s)", (h.tenant, self.task)
            ),
            (False,),
        )

        self.assertTrue(self.resume())
        final = h.managed_worker(approval=second)
        result = asyncio.run(final.run_once())
        self.assertEqual(result.task_status, "succeeded", result)
        self.assertEqual(final.fictional_model.calls, 1)
        self.assertEqual(
            self.intents_by_approval(),
            {first.qualification_id: 1, second.qualification_id: 1},
        )
        self.assertEqual(h.row("SELECT count(*) FROM memoriesql.accepted_bead_semantics"), (1,))

        # A settled task takes no further approval.
        self.revoke(second)
        with self.assertRaises(REFUSED):
            self.install(self.replacement(), second)

    def test_an_active_approval_is_never_refreshed(self) -> None:
        first = self.h.approval
        self.install(first, None)
        for supersedes in (None, first):
            with self.subTest(supersedes=supersedes), self.assertRaises(psycopg.Error):
                self.install(self.replacement(), supersedes)
        self.assertEqual(
            self.h.row("SELECT count(*) FROM memoriesql.model_supervised_qualifications"),
            (1,),
        )
        # Approvals stay immutable apart from revocation, including the new link.
        with self.assertRaises(psycopg.Error):
            self.h.db.execute(
                "UPDATE memoriesql.model_supervised_qualifications "
                "SET supersedes_qualification_id=qualification_id"
            )

    def test_the_first_approval_names_nothing(self) -> None:
        with self.assertRaises(REFUSED):
            self.install(self.h.approval, self.replacement())


if __name__ == "__main__":
    unittest.main()
