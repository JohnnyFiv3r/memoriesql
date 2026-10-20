"""Owned fictional cutover schedules; native execution requires a DB admission.

The fixture has a closed roster of explicitly owned callers. Waiting for their
actual transaction/worker completion precedes the installation boundary. This
does not discover or stop a deployment's other ingress. In particular, replacing
functions alone does not drain a transaction that already returned from a call.
No live model, new product lock, privilege, or lifecycle switch is introduced.
"""

from __future__ import annotations

import asyncio
import threading
import time
import unittest
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import psycopg

from memoriesql.application.local_entity_mentions import ActivateAuthoredMentions
from memoriesql.application.model_accounting import AccountingPersistenceError
from memoriesql.infrastructure.jobs.postgres_semantic_queue import (
    PostgresSemanticTaskQueue,
)
from memoriesql.infrastructure.postgres.complete_input_execution import (
    PostgresCompleteInput,
)
from memoriesql.infrastructure.postgres.model_accounting import (
    PostgresModelRequestAccounting,
)

if TYPE_CHECKING:
    from tests.runtime import test_authored_statement_kinds as fixtures
    from tests.runtime.test_postgres_runtime import migrate
else:
    import test_authored_statement_kinds as fixtures
    from test_postgres_runtime import migrate


class OwnedCall:
    """Completion includes the target's real context exits, not just a reply."""

    def __init__(self, target: Callable[[], Any]) -> None:
        self.done = threading.Event()
        self.value: Any = None
        self.error: BaseException | None = None

        def run() -> None:
            try:
                self.value = target()
            except BaseException as error:
                self.error = error
            finally:
                self.done.set()

        self.thread = threading.Thread(target=run, name="legacy7-owned-call")
        self.thread.start()

    def finish(self) -> Any:
        self.thread.join(timeout=10)
        if self.thread.is_alive() or not self.done.is_set():
            raise AssertionError("owned caller has not drained")
        if self.error is not None:
            raise self.error
        return self.value


class Legacy7Cutover(unittest.TestCase):
    def fixture(self) -> fixtures.AuthoredStatementKinds:
        fixture = fixtures.AuthoredStatementKinds(methodName="runTest")
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        return fixture

    def command(self, fixture: Any, key: str) -> ActivateAuthoredMentions:
        return ActivateAuthoredMentions(
            idempotency_key=key,
            binding_task_id=fixture.bound.task_id,
            dispatch_policy_id=fixture.dispatch_policy,
        )

    def adapter(self, fixture: Any, connection: Any) -> PostgresCompleteInput:
        return PostgresCompleteInput(
            connection,
            credential_sha256=fixture.secret_hash,
            workspace_id=fixture.workspace,
        )

    def wait_for(self, predicate: Callable[[], bool]) -> None:
        deadline = time.monotonic() + 5
        while not predicate():
            if time.monotonic() >= deadline:
                self.fail("native cutover observation deadline")
            time.sleep(0.005)

    def install_after(self, fixture: Any, calls: tuple[OwnedCall, ...]) -> None:
        # The entire finite fixture roster is supplied explicitly. A completion
        # marker without a joined caller is insufficient, even on a lost reply.
        for call in calls:
            self.assertFalse(call.thread.is_alive())
            self.assertTrue(call.done.is_set())
            self.assertIsNone(call.error)
        migrate(fixture.db, expected_current_version=43, target_version=45)
        self.assertEqual(fixture.row("SELECT max(version) FROM memoriesql.schema_migrations"), (45,))

    def assert_activation_closed(self, fixture: Any, key: str) -> None:
        tables = ("semantic_tasks", "complete_input_executions", "idempotency_receipts")
        def state() -> list[Any]:
            return [fixture.row(
                "SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),'[]') "
                "FROM memoriesql." + name + " t"
            )[0] for name in tables]
        before = state()
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            fixture.complete.activate_authored_mentions(self.command(fixture, key))
        self.assertEqual(state(), before)

    def test_entered_activation_wait_drains_before_atomic_install(self) -> None:
        fixture = self.fixture()
        fixture.setup_revisiting("Fictional held activation.", activate=False)
        key = "legacy7.entered"
        saved: dict[str, Any] = {}

        def activate() -> Any:
            with fixture.connection() as connection:
                saved["connection"] = connection
                saved["pid"] = connection.info.backend_pid
                return self.adapter(fixture, connection).activate_authored_mentions(
                    self.command(fixture, key)
                )

        with fixture.connection() as holder:
            expression = "hashtextextended(%s,0)"
            lock_key = str(fixture.tenant) + ":complete-input-activate:" + key
            holder.execute("SELECT pg_advisory_lock(" + expression + ")", (lock_key,))
            call = OwnedCall(activate)
            try:
                # This lock is reached inside the old activation body after its
                # first authorization. Observe the real backend waiting there.
                self.wait_for(lambda: "pid" in saved and bool(fixture.row(
                    "SELECT %s=ANY(pg_blocking_pids(%s))",
                    (holder.info.backend_pid, saved["pid"]),
                )[0]))
                self.assertFalse(call.done.is_set())
            finally:
                holder.execute("SELECT pg_advisory_unlock(" + expression + ")", (lock_key,))
                call.finish()
        self.install_after(fixture, (call,))
        self.assertEqual(fixture.row(
            "SELECT count(*) FROM memoriesql.complete_input_executions "
            "WHERE execution_task_id=%s", (call.value.execution_task_id,),
        ), (1,))
        self.assert_activation_closed(fixture, key)
        self.assertEqual(fixture.row("SELECT count(*) FROM memoriesql.model_provider_request_intents"), (0,))

    def test_function_replacement_alone_does_not_drain_old_transaction(self) -> None:
        fixture = self.fixture()
        fixture.setup_revisiting("Fictional uncommitted activation.", activate=False)
        prepared = threading.Event()
        release = threading.Event()
        saved: dict[str, Any] = {}

        def activate_before_commit() -> Any:
            with fixture.connection() as connection, connection.transaction():
                receipt = self.adapter(fixture, connection).activate_authored_mentions(
                    self.command(fixture, "legacy7.not-a-drain")
                )
                saved.update(receipt=receipt, pid=connection.info.backend_pid)
                prepared.set()
                if not release.wait(timeout=10):
                    raise AssertionError("uncommitted activation release deadline")
                return receipt

        call = OwnedCall(activate_before_commit)
        try:
            self.assertTrue(prepared.wait(timeout=5))
            self.assertEqual(fixture.row(
                "SELECT state,backend_xid IS NOT NULL FROM pg_stat_activity WHERE pid=%s",
                (saved["pid"],),
            ), ("idle in transaction", True))
            self.assertEqual(fixture.row("SELECT count(*) FROM memoriesql.complete_input_executions"), (0,))
            # Deliberately omit the drain. This negative control must never be
            # cited as an effective installation/cutover procedure.
            migrate(fixture.db, expected_current_version=43, target_version=45)
            self.assertFalse(call.done.is_set())
        finally:
            release.set()
            call.finish()
        self.assertEqual(fixture.row(
            "SELECT count(*) FROM memoriesql.complete_input_executions "
            "WHERE execution_task_id=%s", (saved["receipt"].execution_task_id,),
        ), (1,))
        self.assert_activation_closed(fixture, "legacy7.not-a-drain")
        fixture.assert_no_meaning()

    def test_checked_apply_commits_before_owned_cutover_and_history_survives(self) -> None:
        fixture = self.fixture()
        fixture.setup_mentions()
        prepared = threading.Event()
        release = threading.Event()
        original = PostgresSemanticTaskQueue.record_canonical_result
        saved: dict[str, Any] = {}

        def hold_checked_apply(queue: Any, fence: Any, result: Any, **kwargs: Any) -> str:
            status = original(queue, fence, result, **kwargs)
            self.assertEqual(status, "succeeded")
            saved.update(fence=fence, result=result, pid=queue._connection.info.backend_pid)
            prepared.set()
            if not release.wait(timeout=10):
                raise AssertionError("checked apply release deadline")
            return status

        worker = fixture.worker()
        async def run_owned_worker() -> Any:
            result = await worker.run_once()
            self.assertEqual(await worker.wait_for_cleanup(), result)
            self.assertFalse(worker.cleanup_pending)
            return result

        with patch.object(PostgresSemanticTaskQueue, "record_canonical_result", hold_checked_apply):
            call = OwnedCall(lambda: asyncio.run(run_owned_worker()))
            try:
                self.assertTrue(prepared.wait(timeout=5))
                self.assertEqual(fixture.row(
                    "SELECT state,backend_xid IS NOT NULL FROM pg_stat_activity WHERE pid=%s",
                    (saved["pid"],),
                ), ("idle in transaction", True))
                fixture.assert_no_meaning()  # Canonical write is not committed.
                self.assertFalse(call.done.is_set())
            finally:
                release.set()
                receipt = call.finish()
        self.assertEqual(receipt.task_status, "succeeded", receipt)
        history = fixture.row(
            "SELECT jsonb_agg(to_jsonb(v) ORDER BY bead_version_id) FROM memoriesql.bead_versions v"
        )
        self.assertIsNotNone(history[0])
        self.install_after(fixture, (call,))
        with self.assertRaises(psycopg.errors.InsufficientPrivilege), fixture.db.transaction():
            fixture.worker_queue().record_canonical_result(
                saved["fence"], saved["result"], recorded_at=datetime.now(UTC)
            )
        self.assertEqual(fixture.row(
            "SELECT jsonb_agg(to_jsonb(v) ORDER BY bead_version_id) FROM memoriesql.bead_versions v"
        ), history)

    def test_old_intent_wait_and_cancellation_cleanup_drain_without_model(self) -> None:
        fixture = self.fixture()
        fixture.setup_mentions()
        original = PostgresModelRequestAccounting.record_intent
        saved: dict[str, Any] = {"provider_calls": 0}

        def forbidden_provider(messages: Any, info: Any) -> Any:
            saved["provider_calls"] += 1
            raise AssertionError("cancelled pending intent must not dispatch a model")

        worker = fixture.worker(forbidden_provider)

        async def scenario() -> None:
            entered = asyncio.Event()
            with fixture.connection() as holder:
                async def held_intent(port: Any, intent: Any) -> None:
                    saved.update(port=port, intent=intent)
                    holder.execute(
                        "SELECT pg_advisory_lock(hashtextextended('model-request:'||%s,0))",
                        (str(intent.request_id),),
                    )
                    entered.set()
                    await original(port, intent)

                with patch.object(PostgresModelRequestAccounting, "record_intent", held_intent):
                    foreground = asyncio.create_task(worker.run_once())
                    try:
                        await asyncio.wait_for(entered.wait(), timeout=5)
                        deadline = time.monotonic() + 5
                        while True:
                            row = fixture.row(
                                "SELECT count(*) FROM pg_stat_activity "
                                "WHERE datname=current_database() AND %s=ANY(pg_blocking_pids(pid)) "
                                "AND query LIKE '%%record_model_provider_request_intent%%'",
                                (holder.info.backend_pid,),
                            )
                            if row[0] == 1:
                                break
                            self.assertLess(time.monotonic(), deadline)
                            await asyncio.sleep(0.005)
                        self.assertFalse(foreground.done())
                        self.assertEqual(fixture.row(
                            "SELECT count(*) FROM memoriesql.model_provider_request_intents"
                        ), (0,))
                        foreground.cancel()
                        with self.assertRaises(asyncio.CancelledError):
                            await asyncio.wait_for(asyncio.shield(foreground), timeout=5)
                        self.assertTrue(worker.cleanup_pending)
                    finally:
                        if "intent" in saved:
                            holder.execute(
                                "SELECT pg_advisory_unlock(hashtextextended('model-request:'||%s,0))",
                                (str(saved["intent"].request_id),),
                            )
                        # Always observe the original owned cycle. A failed
                        # coordination assertion must not strand a DB writer.
                        if not foreground.done():
                            foreground.cancel()
                            try:
                                await foreground
                            except asyncio.CancelledError:
                                pass
                        await asyncio.wait_for(worker.wait_for_cleanup(), timeout=10)
                        self.assertFalse(worker.cleanup_pending)
                self.assertEqual(saved["provider_calls"], 0)
                self.assertEqual(fixture.row(
                    "SELECT count(*) FROM memoriesql.model_provider_request_intents WHERE request_id=%s",
                    (saved["intent"].request_id,),
                ), (1,))
                self.assertEqual(fixture.row("SELECT count(*) FROM memoriesql.model_usage_events"), (0,))
                fixture.assert_no_meaning()
                # The finite caller roster and its owned ledger writer have
                # actually completed. Only now may this fixture install45.
                migrate(fixture.db, expected_current_version=43, target_version=45)
                before = fixture.row(
                    "SELECT jsonb_agg(to_jsonb(i) ORDER BY request_id) "
                    "FROM memoriesql.model_provider_request_intents i"
                )
                with self.assertRaises(AccountingPersistenceError):
                    await saved["port"].record_intent(saved["intent"])
                self.assertEqual(fixture.row(
                    "SELECT jsonb_agg(to_jsonb(i) ORDER BY request_id) "
                    "FROM memoriesql.model_provider_request_intents i"
                ), before)
                self.assertEqual(saved["provider_calls"], 0)
                fixture.assert_no_meaning()

        asyncio.run(scenario())

    def test_new_policy_cannot_reopen_fresh_revision7_activation(self) -> None:
        fixture = self.fixture()
        fixture.setup_revisiting("Fictional new policy after cutover.", activate=False)
        migrate(fixture.db, expected_current_version=43, target_version=45)
        fresh_policy = uuid.uuid4()
        fixture.db.execute(
            "INSERT INTO memoriesql.complete_input_dispatch_policies "
            "SELECT tenant_id,%s,workspace_id,access_scope_id,source_object_id,"
            "attestor_principal_id,approved_by_principal_id,qualification_evidence_sha256,"
            "created_at,expires_at,status,execution_contract_revision "
            "FROM memoriesql.complete_input_dispatch_policies WHERE dispatch_policy_id=%s",
            (fresh_policy, fixture.dispatch_policy),
        )
        fixture.dispatch_policy = fresh_policy
        self.assert_activation_closed(fixture, "legacy7.new-policy")
        self.assertEqual(fixture.row("SELECT count(*) FROM memoriesql.complete_input_executions"), (0,))
        self.assertEqual(fixture.row("SELECT count(*) FROM memoriesql.model_provider_request_intents"), (0,))
        fixture.assert_no_meaning()

    def test_original_unissued_intent_rechecks_gate_before_any_model_dispatch(self) -> None:
        fixture = self.fixture()
        fixture.setup_mentions()
        original = PostgresModelRequestAccounting.record_intent
        saved: dict[str, Any] = {"provider_calls": 0}

        def forbidden_provider(messages: Any, info: Any) -> Any:
            saved["provider_calls"] += 1
            raise AssertionError("unissued revision7 request must not dispatch after45")

        worker = fixture.worker(forbidden_provider)

        async def scenario() -> None:
            entered = asyncio.Event()
            release = asyncio.Event()

            async def pause_before_sql(port: Any, intent: Any) -> None:
                # Capture the actual worker-produced request after its canonical
                # claim/run setup, before any intent SQL or provider call starts.
                self.assertNotIn("intent", saved)
                saved["intent"] = intent
                entered.set()
                await release.wait()
                await original(port, intent)

            with patch.object(PostgresModelRequestAccounting, "record_intent", pause_before_sql):
                foreground = asyncio.create_task(worker.run_once())
                try:
                    await asyncio.wait_for(entered.wait(), timeout=5)
                    self.assertFalse(foreground.done())
                    self.assertEqual(saved["provider_calls"], 0)
                    self.assertEqual(fixture.row(
                        "SELECT count(*) FROM memoriesql.model_provider_request_intents"
                    ), (0,))
                    # This exact fixture ingress is parked outside SQL. No old
                    # authorizing function or provider is executing behind it.
                    migrate(fixture.db, expected_current_version=43, target_version=45)
                    release.set()
                    receipt = await asyncio.wait_for(asyncio.shield(foreground), timeout=10)
                    self.assertEqual(receipt.task_status, "policy_paused", receipt)
                finally:
                    release.set()
                    if not foreground.done():
                        foreground.cancel()
                        try:
                            await foreground
                        except asyncio.CancelledError:
                            pass
                    await asyncio.wait_for(worker.wait_for_cleanup(), timeout=10)
                    self.assertFalse(worker.cleanup_pending)
            self.assertEqual(saved["provider_calls"], 0)
            self.assertEqual(fixture.row(
                "SELECT count(*) FROM memoriesql.model_provider_request_intents WHERE request_id=%s",
                (saved["intent"].request_id,),
            ), (0,))
            self.assertEqual(fixture.row("SELECT count(*) FROM memoriesql.model_usage_events"), (0,))
            fixture.assert_no_meaning()

        asyncio.run(scenario())

    def test_direct_caller_with_original_authority_cannot_reopen_revision7(self) -> None:
        fixture = self.fixture()
        fixture.setup_mentions()
        migrate(fixture.db, expected_current_version=43, target_version=45)
        with self.assertRaises(psycopg.errors.InsufficientPrivilege) as refused, fixture.db.transaction():
            fixture.worker_queue()
            fixture.db.execute(
                "SELECT memoriesql.source_revisiting_authorize(%s,%s)",
                (fixture.tenant, fixture.activation.execution_task_id),
            )
        self.assertEqual(refused.exception.diag.message_primary, "source_revisiting_qualification_required")
        self.assertEqual(fixture.row("SELECT count(*) FROM memoriesql.model_provider_request_intents"), (0,))
        fixture.assert_no_meaning()
