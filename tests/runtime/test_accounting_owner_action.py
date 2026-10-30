"""Deterministic accounting misconfigurations pause the task for owner action.

Fictional data in a disposable database. The authoritative intent function
refuses a configuration it cannot enforce, the adapter names the setting, and
the queue holds the task paused, never retried, until an owner resumes it.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import unittest
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import psycopg
from psycopg.conninfo import make_conninfo

from memoriesql.application.builtin_semantic_tasks import CANONICAL_AUTHORING_TASK
from memoriesql.application.model_accounting import (
    AccountingPersistenceError,
    ModelAccountingConfigurationError,
    ProviderRequestTarget,
)
from memoriesql.application.semantic_task_contracts import (
    RetryClass,
    SemanticResultStatus,
    SemanticTaskResult,
)
from memoriesql.infrastructure.jobs.postgres_semantic_queue import (
    PostgresSemanticTaskQueue,
)
from memoriesql.infrastructure.models.pydanticai_executor import (
    CHARACTERIZED_TEST_REQUEST_TARGET,
)
from memoriesql.infrastructure.postgres.model_accounting import (
    PostgresModelRequestAccounting,
)

if TYPE_CHECKING:
    from tests.runtime import test_postgres_runtime as runtime
    from tests.runtime.test_executor import fixture
else:
    import test_postgres_runtime as runtime
    from test_executor import fixture


@unittest.skipUnless(
    os.environ.get("N1_TEST_DATABASE_URL"), "disposable PostgreSQL required"
)
class AccountingOwnerAction(unittest.TestCase):
    def setUp(self) -> None:
        self.h = runtime.PostgresRuntime(methodName="runTest")
        self.addCleanup(self.h.doCleanups)
        self.h.setUp()
        command = self.h.command()
        accepted = self.h.accept(command)
        self.h.pair_worker()
        self.claimed = self.h.claim()
        # Records the attempt's durable run, which every intent must name.
        self.result = self.h.authored_result(accepted, command, self.claimed)

    def metered(self, **changes: Any) -> ProviderRequestTarget:
        return ProviderRequestTarget.model_validate(
            CHARACTERIZED_TEST_REQUEST_TARGET.model_dump()
            | {"billing_basis": "metered", "allowance_state": "not_applicable"}
            | changes
        )

    def intent(self, target: ProviderRequestTarget, ceiling: int | None = None) -> Any:
        executor, task, deps, ports = fixture()
        asyncio.run(executor.execute(task, deps))
        with self.h.db.transaction():
            authorization = self.h.worker_queue().authorization_snapshot(
                self.claimed.fence, checked_at=datetime.now(UTC)
            )
        fence = self.claimed.fence
        return ports.intents[0].model_copy(
            update={
                "request_id": uuid.uuid4(),
                "tenant_id": str(self.h.tenant),
                "workspace_id": str(self.h.workspace),
                "access_scope_id": str(self.h.scope),
                "semantic_task_id": str(fence.task_id),
                "semantic_attempt_id": str(fence.attempt_id),
                "run_id": "orchard.run",
                "task_kind": self.claimed.task_kind,
                "module_key": CANONICAL_AUTHORING_TASK.owning_module,
                "origin_principal_id": str(authorization.principal_id),
                "origin_delegation_id": (
                    str(authorization.delegation_id)
                    if authorization.delegation_id
                    else None
                ),
                "policy_revision": authorization.policy_revision,
                "model_profile_key": CANONICAL_AUTHORING_TASK.model_profile.profile_key,
                "model_profile_revision": (
                    CANONICAL_AUTHORING_TASK.model_profile.revision
                ),
                "target": target,
                "safety_ceiling_microunits": ceiling,
            }
        )

    def accounting(self, instance: str = "orchard.instance") -> PostgresModelRequestAccounting:
        return PostgresModelRequestAccounting(
            connection_factory=lambda: psycopg.connect(
                make_conninfo(self.h.admin, dbname=self.h.database)
            ),
            credential_sha256=self.h.worker_secret,
            workspace_id=self.h.workspace,
            worker_id="orchard.worker",
            worker_instance_id=instance,
        )

    def refused(self, intent: Any, instance: str = "orchard.instance") -> Exception:
        with self.assertRaises(Exception) as raised:
            asyncio.run(self.accounting(instance).record_intent(intent))
        self.assertEqual(self.intents(), 0)
        return raised.exception

    def intents(self) -> int:
        row = self.h.db.execute(
            "SELECT count(*) FROM memoriesql.model_provider_request_intents"
        ).fetchone()
        assert row is not None
        return int(row[0])

    def expired_pricing(self) -> uuid.UUID:
        revision = uuid.uuid4()
        clock = self.h.db.execute("SELECT clock_timestamp()").fetchone()
        assert clock is not None
        now = clock[0]
        self.h.db.execute(
            "INSERT INTO memoriesql.model_pricing_revisions(pricing_revision_id,"
            "provider_key,model_id,currency,effective_from,effective_until,"
            "input_microunits_per_million_tokens,cached_input_microunits_per_million_tokens,"
            "cache_write_microunits_per_million_tokens,output_microunits_per_million_tokens,"
            "source_hash) VALUES(%s,%s,%s,'USD',%s,%s,1,1,1,1,%s)",
            (
                revision,
                CHARACTERIZED_TEST_REQUEST_TARGET.provider_key,
                CHARACTERIZED_TEST_REQUEST_TARGET.model_id,
                now - timedelta(days=60),
                now - timedelta(days=30),
                hashlib.sha256(b"fictional expired price list").hexdigest(),
            ),
        )
        return revision

    def pause(self, code: str) -> str:
        fence = self.claimed.fence
        failure = SemanticTaskResult[Any](
            status=SemanticResultStatus.POLICY_PAUSED,
            task_id=str(fence.task_id),
            attempt_id=str(fence.attempt_id),
            task_kind=self.claimed.task_kind,
            contract_revision=1,
            output_contract_hash=self.result.output_contract_hash,
            error_code=code,
            retry_class=RetryClass.POLICY,
        )
        with self.h.db.transaction():
            return str(
                self.h.worker_queue().record_integrated_failure(
                    fence,
                    failure,
                    error_class="accounting",
                    retry_after_seconds=None,
                    jitter_basis_points=0,
                    recorded_at=datetime.now(UTC),
                )
            )

    def task_state(self) -> tuple[Any, ...]:
        row = self.h.db.execute(
            "SELECT t.status, t.pause_reason_code, a.status, a.retry_class, a.retry_after "
            "FROM memoriesql.semantic_tasks t JOIN memoriesql.semantic_task_attempts a "
            "USING (tenant_id, task_id) WHERE t.task_id=%s AND a.attempt_id=%s",
            (self.claimed.fence.task_id, self.claimed.fence.attempt_id),
        ).fetchone()
        assert row is not None
        return tuple(row)

    def claim_again(self) -> Any:
        with self.h.db.transaction():
            return self.h.worker_queue().claim(
                worker_id="orchard.worker",
                worker_instance_id="orchard.instance",
                lease_seconds=120,
                deadline_seconds=300,
                executor_contract_version=1,
                claimed_at=datetime.now(UTC),
            )

    def test_expired_pricing_pauses_until_the_owner_resumes(self) -> None:
        raised = self.refused(
            self.intent(self.metered(pricing_revision_id=self.expired_pricing()))
        )
        self.assertIsInstance(raised, ModelAccountingConfigurationError)
        assert isinstance(raised, ModelAccountingConfigurationError)
        self.assertEqual(raised.code, "accounting.pricing_revision_unavailable")
        self.assertIn("pricing_revision_id", raised.setting)
        self.assertIsInstance(raised.__cause__, psycopg.Error)

        self.assertEqual(self.pause(raised.code), "policy_paused")
        self.assertEqual(
            self.task_state(),
            (
                "policy_paused",
                "accounting.pricing_revision_unavailable",
                "terminal_failure",
                "policy",
                None,
            ),
        )
        # Never retried on its own: no worker can claim it.
        self.assertIsNone(self.claim_again())
        # The owner corrects the setting and resumes the task.
        with self.h.db.transaction():
            self.h.begin()
            self.assertTrue(
                PostgresSemanticTaskQueue(self.h.db).resume(
                    tenant_id=self.h.tenant,
                    task_id=self.claimed.fence.task_id,
                    resumed_at=datetime.now(UTC),
                )
            )
        self.assertIsNotNone(self.claim_again())

    def test_a_ceiling_without_a_cash_basis_names_its_setting(self) -> None:
        raised = self.refused(self.intent(self.metered(), ceiling=1000))
        self.assertIsInstance(raised, ModelAccountingConfigurationError)
        assert isinstance(raised, ModelAccountingConfigurationError)
        self.assertEqual(raised.code, "accounting.ceiling_cash_basis_missing")

    def test_a_lease_refusal_is_still_a_transient_persistence_failure(self) -> None:
        raised = self.refused(
            self.intent(CHARACTERIZED_TEST_REQUEST_TARGET), instance="orchard.other"
        )
        self.assertIsInstance(raised, AccountingPersistenceError)


if __name__ == "__main__":
    unittest.main()
