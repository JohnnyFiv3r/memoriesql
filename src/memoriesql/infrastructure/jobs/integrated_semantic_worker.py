from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Never, Protocol
from uuid import UUID

from psycopg import Connection
from pydantic import BaseModel, ValidationError

from memoriesql.application.model_accounting import (
    AccountingPersistenceError,
    ModelUsageEvent,
    ProviderRequestIntent,
)
from memoriesql.application.module_registry import CompositionHealthReport
from memoriesql.application.semantic_task_contracts import (
    CancellationSignal,
    EvidenceAccessor,
    EvidenceReference,
    ModelRequestAccounting,
    ProcessLocalUsageRecorder,
    RetryClass,
    SemanticAuthorizationContext,
    SemanticExecutor,
    SemanticResultStatus,
    SemanticRunDeps,
    SemanticRunEvent,
    SemanticRunEventSink,
    SemanticTaskDefinition,
    SemanticTaskInput,
    SemanticTaskResult,
    UsageSummary,
    execute_semantic_task,
    retry_class_for_status,
)
from memoriesql.application.semantic_task_registry import (
    SemanticTaskRegistry,
    SemanticTaskResolutionError,
)
from memoriesql.infrastructure.jobs.postgres_semantic_queue import (
    ClaimedSemanticTask,
    EvidenceHydrationFailure,
    PostgresSemanticTaskQueue,
    ReauthorizationResult,
    SemanticAuthorizationSnapshot,
    SemanticTaskFence,
)
from memoriesql.infrastructure.postgres.authorization import (
    PostgresAuthorizationPort,
)
from memoriesql.infrastructure.postgres.model_accounting import (
    PostgresModelRequestAccounting,
)


class ProviderExecutionState(StrEnum):
    CONFIGURED = "configured"
    UNCONFIGURED = "unconfigured"
    POLICY_PAUSED = "policy_paused"


@dataclass(frozen=True, slots=True)
class ProviderExecutionReadiness:
    state: ProviderExecutionState
    reason_code: str


class WorkerCycleStatus(StrEnum):
    CLEANUP_PENDING = "cleanup_pending"
    IDLE = "idle"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    SETTLED = "settled"
    LATE_OUTPUT_DISCARDED = "late_output_discarded"
    AUTHORIZATION_UNAVAILABLE = "authorization_unavailable"


@dataclass(frozen=True, slots=True)
class SemanticWorkerCycleReceipt:
    cycle_status: WorkerCycleStatus
    provider_state: ProviderExecutionState
    provider_reason_code: str
    task_id: UUID | None = None
    attempt_id: UUID | None = None
    task_status: str | None = None
    result_status: SemanticResultStatus | None = None
    outcome_authority_class: str | None = None


@dataclass(frozen=True, slots=True)
class _DatabaseCallOutcome[T]:
    value: T | None = None
    error: Exception | None = None


@dataclass(frozen=True, slots=True)
class SemanticWorkerIdentity:
    worker_id: str
    worker_instance_id: str
    credential_sha256: str
    workspace_id: UUID


@dataclass(frozen=True, slots=True)
class SemanticWorkerConfig:
    lease_seconds: int = 120
    deadline_seconds: int = 300
    heartbeat_interval_seconds: float = 30.0
    cancellation_poll_interval_seconds: float = 0.25
    finalization_margin_seconds: float = 5.0
    executor_contract_version: int = 1
    jitter_basis_points: int = 0
    cancellation_return_timeout_seconds: float = 5.0

    def __post_init__(self) -> None:
        if (
            not math.isfinite(self.cancellation_return_timeout_seconds)
            or self.cancellation_return_timeout_seconds < 0
        ):
            raise ValueError(
                "cancellation return timeout must be finite and nonnegative"
            )
        if not 90 <= self.lease_seconds <= 3600:
            raise ValueError("lease_seconds must satisfy the SQL-01D lease contract")
        if not self.lease_seconds <= self.deadline_seconds <= 86400:
            raise ValueError("deadline_seconds must satisfy the SQL-01D contract")
        if not math.isfinite(self.heartbeat_interval_seconds):
            raise ValueError("heartbeat interval must be finite")
        if self.heartbeat_interval_seconds <= 0:
            raise ValueError("heartbeat interval must be positive")
        if self.heartbeat_interval_seconds * 3 > self.lease_seconds:
            raise ValueError("lease must span at least three heartbeat intervals")
        if not math.isfinite(self.cancellation_poll_interval_seconds):
            raise ValueError("cancellation poll interval must be finite")
        if not 0 < self.cancellation_poll_interval_seconds <= 1:
            raise ValueError(
                "cancellation poll interval must be between zero and one second"
            )
        if not math.isfinite(self.finalization_margin_seconds):
            raise ValueError("finalization margin must be finite")
        if self.finalization_margin_seconds <= 0:
            raise ValueError("finalization margin must be positive")
        if self.finalization_margin_seconds >= self.deadline_seconds:
            raise ValueError("finalization margin must leave a positive run window")
        if self.executor_contract_version <= 0:
            raise ValueError("executor contract version must be positive")
        if not 0 <= self.jitter_basis_points <= 2500:
            raise ValueError("jitter basis points must be between 0 and 2500")


class SemanticOutcomeSink(Protocol):
    target_kind: str
    authority_class: str

    def persist(
        self,
        queue: PostgresSemanticTaskQueue,
        fence: SemanticTaskFence,
        result: SemanticTaskResult[BaseModel],
        *,
        recorded_at: datetime,
    ) -> str: ...


class SyntheticNonAuthoritativeOutcomeSink:
    target_kind = "synthetic_receipt"
    authority_class = "non_authoritative"

    def persist(
        self,
        queue: PostgresSemanticTaskQueue,
        fence: SemanticTaskFence,
        result: SemanticTaskResult[BaseModel],
        *,
        recorded_at: datetime,
    ) -> str:
        return queue.record_synthetic_result(
            fence,
            result,
            recorded_at=recorded_at,
        )


class CanonicalSemanticOutcomeSink:
    target_kind = "canonical_semantics"
    authority_class = "canonical"

    def persist(
        self,
        queue: PostgresSemanticTaskQueue,
        fence: SemanticTaskFence,
        result: SemanticTaskResult[BaseModel],
        *,
        recorded_at: datetime,
    ) -> str:
        return queue.record_canonical_result(
            fence,
            result,
            recorded_at=recorded_at,
        )


@dataclass(frozen=True, slots=True)
class RegisteredSemanticOutcomeSinks:
    """Closed, explicit outcome authorities at the worker persistence seam."""

    synthetic: SyntheticNonAuthoritativeOutcomeSink = (
        SyntheticNonAuthoritativeOutcomeSink()
    )
    canonical: CanonicalSemanticOutcomeSink = CanonicalSemanticOutcomeSink()

    def resolve(self, target_kind: str) -> SemanticOutcomeSink | None:
        if target_kind == self.synthetic.target_kind:
            return self.synthetic
        if target_kind == self.canonical.target_kind:
            return self.canonical
        return None


SYNTHETIC_OUTCOME_SINKS = RegisteredSemanticOutcomeSinks()


class _CancellationEvent:
    def __init__(self) -> None:
        self._event = asyncio.Event()
        self._lease_lost = False

    def cancel(self, *, lease_lost: bool = False) -> None:
        self._lease_lost = self._lease_lost or lease_lost
        self._event.set()

    def is_cancelled(self) -> bool:
        return self._event.is_set()

    async def wait(self) -> None:
        await self._event.wait()

    def provider_return_is_lease_lost(self) -> bool:
        """Expose only the accounting classification, never queue authority."""

        return self._lease_lost


class _ProcessUsage:
    def __init__(self) -> None:
        self._summary = UsageSummary()

    def snapshot(self) -> UsageSummary:
        return self._summary

    def replace(self, usage: UsageSummary) -> None:
        self._summary = usage

    def retain_after_failure(self, usage: UsageSummary) -> None:
        self._summary = usage


class _HydratedEvidence:
    def __init__(self, content_by_reference: dict[str, str]) -> None:
        self._content_by_reference = content_by_reference

    async def read_authorized_evidence(
        self,
        reference: EvidenceReference,
    ) -> str:
        try:
            return self._content_by_reference[reference.reference_id]
        except KeyError as error:
            raise PermissionError("evidence reference was not hydrated") from error


class _PostgresRunEventSink:
    def __init__(
        self,
        worker: IntegratedSemanticWorker,
        fence: SemanticTaskFence,
        cancellation: _CancellationEvent,
    ) -> None:
        self._worker = worker
        self._fence = fence
        self._cancellation = cancellation

    async def append(self, event: SemanticRunEvent) -> None:
        operation = asyncio.create_task(self._append(event))
        self._worker._event_writes.add(operation)
        operation.add_done_callback(self._worker._event_writes.discard)
        cancellation: asyncio.CancelledError | None = None
        while True:
            try:
                await asyncio.shield(operation)
            except asyncio.CancelledError as error:
                if operation.cancelled():
                    raise
                cancellation = error
                continue
            break
        if cancellation is not None:
            raise cancellation

    async def _append(self, event: SemanticRunEvent) -> None:
        accepted = await self._worker._await_database_call(
            lambda: self._worker._record_run_event(
                self._fence, event, recorded_at=datetime.now(UTC)
            ),
        )
        if not accepted:
            self._cancellation.cancel()
            raise RuntimeError("semantic run event lost its durable attempt fence")


# Own active cycles even when a cancelling caller drops its worker reference.
# These tasks only finish existing claims; they never schedule or claim work.
_ACTIVE_CYCLES: set[asyncio.Task[SemanticWorkerCycleReceipt]] = set()


class _CleanupAccounting:
    """Keep late ledger failures observable even when executor cleanup discards output."""

    def __init__(self, port: ModelRequestAccounting) -> None:
        self._port = port
        self.usage_failed = False

    async def record_intent(self, intent: ProviderRequestIntent) -> None:
        await self._port.record_intent(intent)

    async def append_usage(self, event: ModelUsageEvent) -> None:
        operation = asyncio.create_task(self._port.append_usage(event))
        cancellation: asyncio.CancelledError | None = None
        while True:
            try:
                await asyncio.shield(operation)
            except asyncio.CancelledError as error:
                if operation.cancelled():
                    self.usage_failed = True
                    raise
                cancellation = error
                continue
            except Exception:
                self.usage_failed = True
                if cancellation is not None:
                    raise cancellation from None
                raise
            break
        if cancellation is not None:
            raise cancellation


class IntegratedSemanticWorker:
    """One short-transaction worker over SQL-01D and the SQL-01E executor port."""

    def __init__(
        self,
        *,
        connection_factory: Callable[[], Connection[Any]],
        identity: SemanticWorkerIdentity,
        semantic_registry: SemanticTaskRegistry,
        composition_provider: Callable[[], CompositionHealthReport],
        executor: SemanticExecutor[BaseModel, BaseModel],
        readiness_provider: Callable[[], ProviderExecutionReadiness],
        outcome_sinks: RegisteredSemanticOutcomeSinks = SYNTHETIC_OUTCOME_SINKS,
        config: SemanticWorkerConfig = SemanticWorkerConfig(),
    ) -> None:
        self._connection_factory = connection_factory
        self._identity = identity
        self._semantic_registry = semantic_registry
        self._composition_provider = composition_provider
        self._executor = executor
        self._readiness_provider = readiness_provider
        self._outcome_sinks = outcome_sinks
        self._config = config
        self._cycle_task: asyncio.Task[SemanticWorkerCycleReceipt] | None = None
        self._foreground_active = False
        self._cleanup_started = asyncio.Event()
        self._cleanup_deadline = 0.0
        self._cleanup_heartbeat: asyncio.Task[None] | None = None
        self._pending_receipt: SemanticWorkerCycleReceipt | None = None
        self._cancellation_receipt: SemanticWorkerCycleReceipt | None = None
        self._cycle_accounting: _CleanupAccounting | None = None
        self._event_writes: set[asyncio.Task[None]] = set()
        self._model_accounting = PostgresModelRequestAccounting(
            connection_factory=connection_factory,
            credential_sha256=identity.credential_sha256,
            workspace_id=identity.workspace_id,
            worker_id=identity.worker_id,
            worker_instance_id=identity.worker_instance_id,
        )

    @staticmethod
    async def _run_database_call[T](
        operation: Callable[[], T],
    ) -> tuple[_DatabaseCallOutcome[T], asyncio.CancelledError | None]:
        def capture() -> _DatabaseCallOutcome[T]:
            try:
                return _DatabaseCallOutcome(value=operation())
            except Exception as error:
                return _DatabaseCallOutcome(error=error)

        task = asyncio.create_task(asyncio.to_thread(capture))
        delayed_cancellation: asyncio.CancelledError | None = None
        while True:
            try:
                outcome = await asyncio.shield(task)
            except asyncio.CancelledError as error:
                delayed_cancellation = error
                continue
            return outcome, delayed_cancellation

    async def _settle_preflight_failure_off_loop(
        self,
        claimed: ClaimedSemanticTask,
        readiness: ProviderExecutionReadiness,
        *,
        error_code: str,
        output_contract_hash: str,
        status: SemanticResultStatus = SemanticResultStatus.FAILED,
    ) -> SemanticWorkerCycleReceipt:
        outcome, delayed_cancellation = await self._run_database_call(
            lambda: self._settle_preflight_failure(
                claimed,
                readiness,
                error_code=error_code,
                output_contract_hash=output_contract_hash,
                status=status,
            )
        )
        if delayed_cancellation is not None:
            raise delayed_cancellation
        if outcome.error is not None:
            raise outcome.error
        assert outcome.value is not None
        return outcome.value

    async def _settle_cancellation_before_propagation(
        self,
        claimed: ClaimedSemanticTask,
        readiness: ProviderExecutionReadiness,
        cancellation_error: asyncio.CancelledError,
        *,
        output_contract_hash: str,
    ) -> Never:
        self._cancellation_receipt = await self._settle_preflight_failure_off_loop(
            claimed,
            readiness,
            error_code="worker.cancelled",
            output_contract_hash=output_contract_hash,
            status=SemanticResultStatus.CANCELLED,
        )
        raise cancellation_error

    async def run_once(self) -> SemanticWorkerCycleReceipt:
        """Run one cycle; cancellation may leave explicitly owned cleanup pending.

        A responsive event loop bounds foreground waiting after cancellation,
        not underlying work, database writes, settlement or process shutdown.
        """
        if self._foreground_active:
            raise RuntimeError("worker cycle already has a foreground caller")
        if self._cycle_task is not None:
            if not self._cycle_task.done():
                assert self._pending_receipt is not None
                return self._pending_receipt
            # Fail closed on an uncompleted cancellation or failed settlement.
            # wait_for_cleanup exposes the same outcome; never silently retry it.
            self._cycle_task.result()
        self._foreground_active = True
        self._cleanup_started = asyncio.Event()
        self._pending_receipt = None
        self._cancellation_receipt = None
        self._cycle_accounting = None
        cycle = asyncio.create_task(self._owned_cycle())
        self._cycle_task = cycle
        _ACTIVE_CYCLES.add(cycle)
        cycle.add_done_callback(self._observe_cycle)
        signal = asyncio.create_task(self._cleanup_started.wait())
        delayed_cancellation: asyncio.CancelledError | None = None
        try:
            while not cycle.done():
                timeout = (
                    max(0.0, self._cleanup_deadline - time.monotonic())
                    if self._cleanup_started.is_set()
                    else None
                )
                if timeout == 0:
                    break
                try:
                    await asyncio.wait(
                        (cycle,) if self._cleanup_started.is_set() else (cycle, signal),
                        timeout=timeout,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                except asyncio.CancelledError as error:
                    if delayed_cancellation is None:
                        delayed_cancellation = error
                        self._start_cleanup_wait()
                        cycle.cancel()
                    # Further caller cancellation cannot restart the deadline or
                    # repeatedly interrupt the owned cleanup/accounting path.
            if delayed_cancellation is not None:
                raise delayed_cancellation
            if cycle.done():
                return cycle.result()
            assert self._pending_receipt is not None
            return self._pending_receipt
        finally:
            signal.cancel()
            self._foreground_active = False

    @property
    def cleanup_pending(self) -> bool:
        return bool(
            self._cycle_task is not None
            and not self._cycle_task.done()
            and self._cleanup_started.is_set()
        )

    async def wait_for_cleanup(self) -> SemanticWorkerCycleReceipt | None:
        """Observe the owned cycle, including cancellation/settlement errors.

        This wait is intentionally unbounded. Cancelling this observer does not
        cancel the cycle. Keep its event loop alive while cleanup is pending.
        """
        if self._cycle_task is None:
            return None
        return await asyncio.shield(self._cycle_task)

    @staticmethod
    def _observe_cycle(cycle: asyncio.Task[SemanticWorkerCycleReceipt]) -> None:
        _ACTIVE_CYCLES.discard(cycle)
        if not cycle.cancelled():
            # Retrieve to prevent an unobserved-task warning, but preserve the
            # exception on the task for wait_for_cleanup and later run_once.
            cycle.exception()

    def _start_cleanup_wait(self) -> None:
        if not self._cleanup_started.is_set():
            self._cleanup_deadline = (
                time.monotonic() + self._config.cancellation_return_timeout_seconds
            )
            self._cleanup_started.set()

    async def _owned_cycle(self) -> SemanticWorkerCycleReceipt:
        try:
            try:
                receipt = await self._run_once()
            except asyncio.CancelledError:
                if self._cancellation_receipt is None:
                    raise
                receipt = self._cancellation_receipt
            if (
                self._cleanup_started.is_set()
                and self._cycle_accounting is not None
                and self._cycle_accounting.usage_failed
            ):
                raise AccountingPersistenceError("late model usage persistence failed")
            return receipt
        finally:
            # The existing cycle settles before releasing cleanup retention.
            # On failure, leave recovery to the existing database reaper.
            heartbeat = self._cleanup_heartbeat

            async def stop_retention() -> None:
                while self._event_writes:
                    await asyncio.gather(*self._event_writes, return_exceptions=True)
                if heartbeat is not None:
                    heartbeat.cancel()
                    await asyncio.gather(heartbeat, return_exceptions=True)

            operation = asyncio.create_task(stop_retention())
            while True:
                try:
                    await asyncio.shield(operation)
                except asyncio.CancelledError:
                    continue
                break
            self._cleanup_heartbeat = None

    async def _await_database_call[T](self, operation: Callable[[], T]) -> T:
        outcome, cancellation = await self._run_database_call(operation)
        if cancellation is not None:
            raise cancellation
        if outcome.error is not None:
            raise outcome.error
        return outcome.value  # type: ignore[return-value]

    async def _run_once(self) -> SemanticWorkerCycleReceipt:
        readiness = self._readiness_provider()
        self._pending_receipt = SemanticWorkerCycleReceipt(
            cycle_status=WorkerCycleStatus.CLEANUP_PENDING,
            provider_state=readiness.state,
            provider_reason_code=readiness.reason_code,
        )
        if readiness.state is not ProviderExecutionState.CONFIGURED:
            return SemanticWorkerCycleReceipt(
                cycle_status=WorkerCycleStatus.PROVIDER_UNAVAILABLE,
                provider_state=readiness.state,
                provider_reason_code=readiness.reason_code,
            )

        claim_outcome, claim_cancellation = await self._run_database_call(
            lambda: self._claim(datetime.now(UTC))
        )
        if claim_cancellation is not None:
            if claim_outcome.value is not None:
                await self._settle_cancellation_before_propagation(
                    claim_outcome.value,
                    readiness,
                    claim_cancellation,
                    output_contract_hash=claim_outcome.value.task_contract_hash,
                )
            raise claim_cancellation
        if claim_outcome.error is not None:
            raise claim_outcome.error
        claimed = claim_outcome.value
        if claimed is None:
            return SemanticWorkerCycleReceipt(
                cycle_status=WorkerCycleStatus.IDLE,
                provider_state=readiness.state,
                provider_reason_code=readiness.reason_code,
            )
        self._pending_receipt = replace(
            self._pending_receipt,
            task_id=claimed.fence.task_id,
            attempt_id=claimed.fence.attempt_id,
        )
        definition = self._definition_for(claimed)
        sink = self._outcome_sinks.resolve(claimed.target_kind)
        if definition is None:
            return await self._settle_preflight_failure_off_loop(
                claimed,
                readiness,
                error_code="worker.registry_mismatch",
                output_contract_hash=claimed.task_contract_hash,
            )
        if sink is None:
            return await self._settle_preflight_failure_off_loop(
                claimed,
                readiness,
                error_code="worker.outcome_sink_unregistered",
                output_contract_hash=definition.output_contract.schema_hash,
            )
        start_outcome, start_cancellation = await self._run_database_call(
            lambda: self._start(claimed.fence, datetime.now(UTC))
        )
        if start_cancellation is not None:
            await self._settle_cancellation_before_propagation(
                claimed,
                readiness,
                start_cancellation,
                output_contract_hash=definition.output_contract.schema_hash,
            )
        if start_outcome.error is not None:
            raise start_outcome.error
        started = start_outcome.value
        if not started:
            return await self._settle_preflight_failure_off_loop(
                claimed,
                readiness,
                error_code="worker.start_rejected",
                output_contract_hash=definition.output_contract.schema_hash,
                status=SemanticResultStatus.CANCELLED,
            )

        hydration_outcome, hydration_cancellation = await self._run_database_call(
            lambda: self._hydrate(
                claimed,
                definition,
                datetime.now(UTC),
            )
        )
        if hydration_cancellation is not None:
            await self._settle_cancellation_before_propagation(
                claimed,
                readiness,
                hydration_cancellation,
                output_contract_hash=definition.output_contract.schema_hash,
            )
        if hydration_outcome.error is not None:
            raise hydration_outcome.error
        assert hydration_outcome.value is not None
        prepared = hydration_outcome.value
        if isinstance(prepared, _PreparationFailure):
            return await self._settle_preflight_failure_off_loop(
                claimed,
                readiness,
                error_code=prepared.error_code,
                output_contract_hash=definition.output_contract.schema_hash,
                status=prepared.status,
            )
        task_input, authorization, evidence_content = prepared
        cancellation = _CancellationEvent()
        usage = _ProcessUsage()
        evidence_accessor: EvidenceAccessor = _HydratedEvidence(evidence_content)
        event_sink: SemanticRunEventSink = _PostgresRunEventSink(
            self,
            claimed.fence,
            cancellation,
        )
        cancellation_signal: CancellationSignal = cancellation
        usage_recorder: ProcessLocalUsageRecorder = usage
        finalization_deadline = claimed.fence.deadline_at.timestamp() - (
            self._config.finalization_margin_seconds
        )
        monotonic_deadline_ns = time.monotonic_ns() + max(
            0,
            int((finalization_deadline - datetime.now(UTC).timestamp()) * 1e9),
        )
        self._cycle_accounting = _CleanupAccounting(self._model_accounting)
        deps = SemanticRunDeps(
            task_id=str(claimed.fence.task_id),
            task_kind=claimed.task_kind,
            contract_revision=claimed.contract_revision,
            task_contract_hash=claimed.task_contract_hash,
            semantic_registry_hash=claimed.semantic_registry_hash,
            attempt_id=str(claimed.fence.attempt_id),
            lease_generation=claimed.fence.lease_generation,
            authorization=SemanticAuthorizationContext(
                principal_id=str(authorization.principal_id),
                delegation_id=(
                    str(authorization.delegation_id)
                    if authorization.delegation_id is not None
                    else None
                ),
                tenant_id=str(claimed.fence.tenant_id),
                workspace_id=str(claimed.fence.workspace_id),
                policy_revision=authorization.policy_revision,
                allowed_access_scope_ids=frozenset(
                    {str(authorization.access_scope_id)}
                ),
            ),
            authorized_evidence_manifest=task_input.evidence_manifest,
            evidence_accessor=evidence_accessor,
            cancellation=cancellation_signal,
            usage=usage_recorder,
            event_sink=event_sink,
            model_accounting=self._cycle_accounting,
            monotonic_deadline_ns=monotonic_deadline_ns,
        )
        try:
            resolved = self._semantic_registry.resolve(
                task_input,
                self._composition_provider(),
                deps,
            )
        except SemanticTaskResolutionError:
            return await self._settle_preflight_failure_off_loop(
                claimed,
                readiness,
                error_code="worker.task_resolution_failed",
                output_contract_hash=definition.output_contract.schema_hash,
            )

        try:
            cancellation_grace_seconds = self._executor.cancellation_grace_seconds(
                max_delegate_calls=resolved.effective_budget.max_delegate_calls
            )
        except Exception:
            return await self._settle_preflight_failure_off_loop(
                claimed,
                readiness,
                error_code="worker.executor_cancellation_grace_invalid",
                output_contract_hash=definition.output_contract.schema_hash,
            )
        if (
            not math.isfinite(cancellation_grace_seconds)
            or cancellation_grace_seconds < 0
        ):
            return await self._settle_preflight_failure_off_loop(
                claimed,
                readiness,
                error_code="worker.executor_cancellation_grace_invalid",
                output_contract_hash=definition.output_contract.schema_hash,
            )
        executor_deadline = finalization_deadline - cancellation_grace_seconds
        try:
            pre_dispatch_fence_available = await self._await_database_call(
                lambda: self._heartbeat(claimed.fence, datetime.now(UTC)),
            )
        except asyncio.CancelledError as error:
            cancellation.cancel()
            await self._settle_cancellation_before_propagation(
                claimed,
                readiness,
                error,
                output_contract_hash=definition.output_contract.schema_hash,
            )
        if not pre_dispatch_fence_available:
            return await self._settle_preflight_failure_off_loop(
                claimed,
                readiness,
                error_code="worker.pre_dispatch_fence_unavailable",
                output_contract_hash=definition.output_contract.schema_hash,
                status=SemanticResultStatus.CANCELLED,
            )

        execution_started_ns = time.monotonic_ns()
        queue_deadline_ns = execution_started_ns + max(
            0,
            int((executor_deadline - datetime.now(UTC).timestamp()) * 1e9),
        )
        task_deadline_ns = execution_started_ns + (
            resolved.effective_budget.wall_clock_seconds * 1_000_000_000
        )
        deps = replace(
            deps,
            monotonic_deadline_ns=min(queue_deadline_ns, task_deadline_ns),
        )

        watcher_database_unavailable = asyncio.Event()
        heartbeat = asyncio.create_task(
            self._watch_attempt(
                claimed.fence,
                cancellation,
                watcher_database_unavailable,
            )
        )
        executor_task = asyncio.create_task(
            execute_semantic_task(
                self._executor,
                resolved,
                deps,
            )
        )
        cancellation_waiter = asyncio.create_task(cancellation.wait())
        result: SemanticTaskResult[BaseModel] | None = None
        cancellation_error: asyncio.CancelledError | None = None
        deadline_exhausted = False
        execution_cancelled = False
        cleanup_heartbeat: asyncio.Task[None] | None = None
        try:
            remaining_seconds = max(
                0,
                (deps.monotonic_deadline_ns - time.monotonic_ns()) / 1e9,
            )
            done, _ = await asyncio.wait(
                (executor_task, cancellation_waiter),
                timeout=remaining_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if executor_task in done:
                result = executor_task.result()
            else:
                deadline_exhausted = cancellation_waiter not in done
                execution_cancelled = not deadline_exhausted
                cancellation.cancel()
                (
                    cleanup_heartbeat,
                    drain_cancellation,
                ) = await self._retain_cleanup_and_drain_executor(
                    claimed.fence,
                    heartbeat,
                    watcher_database_unavailable,
                    executor_task,
                    cancellation,
                    cancellation_grace_seconds=cancellation_grace_seconds,
                )
                if drain_cancellation is not None:
                    cancellation_error = drain_cancellation
        except asyncio.CancelledError as error:
            current_task = asyncio.current_task()
            if current_task is not None and current_task.cancelling():
                cancellation.cancel()
                cancellation_error = error
                if cleanup_heartbeat is None:
                    (
                        cleanup_heartbeat,
                        drain_cancellation,
                    ) = await self._retain_cleanup_and_drain_executor(
                        claimed.fence,
                        heartbeat,
                        watcher_database_unavailable,
                        executor_task,
                        cancellation,
                        cancellation_grace_seconds=cancellation_grace_seconds,
                    )
                    if drain_cancellation is not None:
                        cancellation_error = drain_cancellation
            else:
                execution_cancelled = True
        except Exception:
            # Only the bounded worker code crosses the durable failure boundary.
            pass
        finally:
            teardown_cancellation = await self._teardown_execution_tasks(
                cancellation_waiter,
                heartbeat,
                None,
                cancellation,
            )
            if teardown_cancellation is not None:
                cancellation_error = teardown_cancellation
        if cancellation_error is not None:
            await self._settle_cancellation_before_propagation(
                claimed,
                readiness,
                cancellation_error,
                output_contract_hash=definition.output_contract.schema_hash,
            )
        if deadline_exhausted:
            return await self._settle_preflight_failure_off_loop(
                claimed,
                readiness,
                error_code="runtime.wall_clock_limit",
                output_contract_hash=definition.output_contract.schema_hash,
                status=SemanticResultStatus.BUDGET_EXHAUSTED,
            )
        if watcher_database_unavailable.is_set():
            return await self._settle_preflight_failure_off_loop(
                claimed,
                readiness,
                error_code="worker.watch_database_unavailable",
                output_contract_hash=definition.output_contract.schema_hash,
                status=SemanticResultStatus.UNAVAILABLE,
            )
        if execution_cancelled or (result is None and cancellation.is_cancelled()):
            return await self._settle_preflight_failure_off_loop(
                claimed,
                readiness,
                error_code="worker.late_output_discarded",
                output_contract_hash=definition.output_contract.schema_hash,
                status=SemanticResultStatus.CANCELLED,
            )
        if result is None:
            return await self._settle_preflight_failure_off_loop(
                claimed,
                readiness,
                error_code="worker.executor_failed",
                output_contract_hash=definition.output_contract.schema_hash,
            )

        late_output_discarded = False
        if result.status == SemanticResultStatus.SUCCEEDED and (
            cancellation.is_cancelled()
            or time.monotonic_ns() >= deps.monotonic_deadline_ns
        ):
            late_output_discarded = True
            deadline_only = (
                not cancellation.is_cancelled()
                and time.monotonic_ns() >= deps.monotonic_deadline_ns
            )
            result = SemanticTaskResult[BaseModel](
                status=(
                    SemanticResultStatus.BUDGET_EXHAUSTED
                    if deadline_only
                    else SemanticResultStatus.CANCELLED
                ),
                task_id=result.task_id,
                attempt_id=result.attempt_id,
                task_kind=result.task_kind,
                contract_revision=result.contract_revision,
                output_contract_hash=result.output_contract_hash,
                used_evidence_refs=result.used_evidence_refs,
                model_run_refs=result.model_run_refs,
                validation_results=result.validation_results,
                usage=result.usage,
                error_code=(
                    "runtime.wall_clock_limit"
                    if deadline_only
                    else "worker.late_output_discarded"
                ),
                retry_class=RetryClass.NEVER,
            )
        persist_outcome, persist_cancellation = await self._run_database_call(
            lambda: self._persist_result(
                claimed,
                result,
                sink,
                recorded_at=datetime.now(UTC),
            )
        )
        if persist_cancellation is not None:
            raise persist_cancellation
        if persist_outcome.error is not None:
            raise persist_outcome.error
        assert persist_outcome.value is not None
        persisted = persist_outcome.value
        outcome_status, authorization_available = persisted
        if not authorization_available:
            return SemanticWorkerCycleReceipt(
                cycle_status=WorkerCycleStatus.AUTHORIZATION_UNAVAILABLE,
                provider_state=readiness.state,
                provider_reason_code=readiness.reason_code,
                task_id=claimed.fence.task_id,
                attempt_id=claimed.fence.attempt_id,
                result_status=SemanticResultStatus(result.status),
            )
        if (
            late_output_discarded
            or outcome_status == "stale_fence"
            or (
                result.status == SemanticResultStatus.SUCCEEDED
                and outcome_status != "succeeded"
            )
        ):
            return SemanticWorkerCycleReceipt(
                cycle_status=WorkerCycleStatus.LATE_OUTPUT_DISCARDED,
                provider_state=readiness.state,
                provider_reason_code=readiness.reason_code,
                task_id=claimed.fence.task_id,
                attempt_id=claimed.fence.attempt_id,
                task_status=outcome_status,
                result_status=SemanticResultStatus(result.status),
            )
        return SemanticWorkerCycleReceipt(
            cycle_status=WorkerCycleStatus.SETTLED,
            provider_state=readiness.state,
            provider_reason_code=readiness.reason_code,
            task_id=claimed.fence.task_id,
            attempt_id=claimed.fence.attempt_id,
            task_status=outcome_status,
            result_status=SemanticResultStatus(result.status),
            outcome_authority_class=(
                sink.authority_class
                if result.status == SemanticResultStatus.SUCCEEDED
                and outcome_status == "succeeded"
                else None
            ),
        )

    async def _watch_attempt(
        self,
        fence: SemanticTaskFence,
        cancellation: _CancellationEvent,
        database_unavailable: asyncio.Event,
    ) -> None:
        next_heartbeat_at = time.monotonic() + self._config.heartbeat_interval_seconds
        while True:
            await asyncio.sleep(
                min(
                    self._config.cancellation_poll_interval_seconds,
                    max(0, next_heartbeat_at - time.monotonic()),
                )
            )
            try:
                reauthorization: ReauthorizationResult | None = None
                if time.monotonic() >= next_heartbeat_at:
                    alive = await self._await_database_call(
                        lambda: self._heartbeat(fence, datetime.now(UTC)),
                    )
                    next_heartbeat_at = (
                        time.monotonic() + self._config.heartbeat_interval_seconds
                    )
                    if not alive:
                        reauthorization = await self._await_database_call(
                            lambda: self._attempt_watch_reauthorization(fence),
                        )
                else:
                    alive = await self._await_database_call(
                        lambda: self._attempt_is_live(fence),
                    )
                    if not alive:
                        reauthorization = await self._await_database_call(
                            lambda: self._attempt_watch_reauthorization(fence),
                        )
            except Exception:
                database_unavailable.set()
                cancellation.cancel()
                return
            if not alive:
                cancellation.cancel(
                    lease_lost=(reauthorization is ReauthorizationResult.STALE_FENCE)
                )
                return

    async def _begin_cleanup_retention(
        self,
        fence: SemanticTaskFence,
        heartbeat: asyncio.Task[None],
        database_unavailable: asyncio.Event,
    ) -> asyncio.Task[None] | None:
        heartbeat.cancel()
        await asyncio.gather(heartbeat, return_exceptions=True)
        try:
            retained = await self._await_database_call(
                lambda: self._retain_cleanup_lease(fence, datetime.now(UTC)),
            )
        except Exception:
            database_unavailable.set()
            return asyncio.create_task(
                self._watch_cleanup_lease(
                    fence,
                    database_unavailable,
                    retry_immediately=True,
                )
            )
        if not retained:
            return None
        return asyncio.create_task(
            self._watch_cleanup_lease(fence, database_unavailable)
        )

    async def _watch_cleanup_lease(
        self,
        fence: SemanticTaskFence,
        database_unavailable: asyncio.Event,
        *,
        retry_immediately: bool = False,
    ) -> None:
        delay_seconds = (
            self._config.cancellation_poll_interval_seconds
            if retry_immediately
            else self._config.heartbeat_interval_seconds
        )
        while True:
            await asyncio.sleep(delay_seconds)
            try:
                retained = await self._await_database_call(
                    lambda: self._retain_cleanup_lease(fence, datetime.now(UTC)),
                )
            except Exception:
                database_unavailable.set()
                delay_seconds = self._config.cancellation_poll_interval_seconds
                continue
            if not retained:
                return
            delay_seconds = self._config.heartbeat_interval_seconds

    async def _retain_cleanup_and_drain_executor(
        self,
        fence: SemanticTaskFence,
        heartbeat: asyncio.Task[None],
        database_unavailable: asyncio.Event,
        executor_task: asyncio.Task[SemanticTaskResult[BaseModel]],
        cancellation: _CancellationEvent,
        *,
        cancellation_grace_seconds: float,
    ) -> tuple[asyncio.Task[None] | None, asyncio.CancelledError | None]:
        self._start_cleanup_wait()

        async def retain_and_drain() -> asyncio.Task[None] | None:
            cleanup_heartbeat = await self._begin_cleanup_retention(
                fence,
                heartbeat,
                database_unavailable,
            )
            self._cleanup_heartbeat = cleanup_heartbeat
            await self._cancel_and_drain_executor(
                executor_task,
                cancellation_grace_seconds=cancellation_grace_seconds,
            )
            return cleanup_heartbeat

        operation = asyncio.create_task(retain_and_drain())
        delayed_cancellation: asyncio.CancelledError | None = None
        while True:
            try:
                cleanup_heartbeat = await asyncio.shield(operation)
            except asyncio.CancelledError as error:
                delayed_cancellation = error
                cancellation.cancel()
                if not executor_task.done():
                    executor_task.cancel()
                continue
            return cleanup_heartbeat, delayed_cancellation

    @staticmethod
    async def _teardown_execution_tasks(
        cancellation_waiter: asyncio.Task[None],
        heartbeat: asyncio.Task[None],
        cleanup_heartbeat: asyncio.Task[None] | None,
        cancellation: _CancellationEvent,
    ) -> asyncio.CancelledError | None:
        async def teardown() -> None:
            cancellation_waiter.cancel()
            await asyncio.gather(cancellation_waiter, return_exceptions=True)
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
            if cleanup_heartbeat is not None:
                cleanup_heartbeat.cancel()
                await asyncio.gather(cleanup_heartbeat, return_exceptions=True)

        operation = asyncio.create_task(teardown())
        delayed_cancellation: asyncio.CancelledError | None = None
        while True:
            try:
                await asyncio.shield(operation)
            except asyncio.CancelledError as error:
                delayed_cancellation = error
                cancellation.cancel()
                continue
            return delayed_cancellation

    @staticmethod
    async def _cancel_and_drain_executor(
        executor_task: asyncio.Task[SemanticTaskResult[BaseModel]],
        *,
        cancellation_grace_seconds: float,
    ) -> None:
        executor_task.cancel()
        done, _ = await asyncio.wait(
            (executor_task,),
            timeout=cancellation_grace_seconds,
        )
        if executor_task in done:
            await asyncio.gather(executor_task, return_exceptions=True)
            return
        executor_task.cancel()
        await asyncio.gather(executor_task, return_exceptions=True)

    def _claim(self, claimed_at: datetime) -> ClaimedSemanticTask | None:
        def operation(
            queue: PostgresSemanticTaskQueue,
            _context: object,
        ) -> ClaimedSemanticTask | None:
            return queue.claim(
                worker_id=self._identity.worker_id,
                worker_instance_id=self._identity.worker_instance_id,
                lease_seconds=self._config.lease_seconds,
                deadline_seconds=self._config.deadline_seconds,
                executor_contract_version=self._config.executor_contract_version,
                claimed_at=claimed_at,
            )

        return self._transaction(operation)

    def _start(self, fence: SemanticTaskFence, started_at: datetime) -> bool:
        return self._transaction(
            lambda queue, _context: queue.start(fence, started_at=started_at)
        )

    def _heartbeat(self, fence: SemanticTaskFence, heartbeat_at: datetime) -> bool:
        return self._transaction(
            lambda queue, _context: queue.heartbeat(
                fence,
                lease_seconds=self._config.lease_seconds,
                heartbeat_at=heartbeat_at,
            )
        )

    def _retain_cleanup_lease(
        self,
        fence: SemanticTaskFence,
        retained_at: datetime,
    ) -> bool:
        return self._transaction(
            lambda queue, _context: queue.retain_cleanup_lease(
                fence,
                lease_seconds=self._config.lease_seconds,
                retained_at=retained_at,
            )
        )

    def _attempt_reauthorization(
        self,
        fence: SemanticTaskFence,
    ) -> ReauthorizationResult:
        return self._transaction(
            lambda queue, _context: queue.reauthorize(
                fence,
                phase="hydrate",
                checked_at=datetime.now(UTC),
            )
        )

    def _attempt_watch_reauthorization(
        self,
        fence: SemanticTaskFence,
    ) -> ReauthorizationResult:
        return self._transaction(
            lambda queue, _context: queue.reauthorize_for_watch(
                fence,
                checked_at=datetime.now(UTC),
            )
        )

    def _attempt_is_live(self, fence: SemanticTaskFence) -> bool:
        return (
            self._attempt_reauthorization(fence)
            is ReauthorizationResult.AUTHORIZED
        )

    def _record_run_event(
        self,
        fence: SemanticTaskFence,
        event: SemanticRunEvent,
        *,
        recorded_at: datetime,
    ) -> bool:
        return self._transaction(
            lambda queue, _context: queue.record_run_event(
                fence,
                event,
                recorded_at=recorded_at,
            )
        )

    def _hydrate(
        self,
        claimed: ClaimedSemanticTask,
        definition: SemanticTaskDefinition[BaseModel, BaseModel],
        hydrated_at: datetime,
    ) -> (
        tuple[
            SemanticTaskInput[BaseModel],
            SemanticAuthorizationSnapshot,
            dict[str, str],
        ]
        | _PreparationFailure
    ):
        def operation(
            queue: PostgresSemanticTaskQueue,
            _context: object,
        ) -> (
            tuple[
                SemanticTaskInput[BaseModel],
                SemanticAuthorizationSnapshot,
                dict[str, str],
            ]
            | _PreparationFailure
        ):
            authorization_result = queue.reauthorize(
                claimed.fence,
                phase="hydrate",
                checked_at=hydrated_at,
            )
            if authorization_result is not ReauthorizationResult.AUTHORIZED:
                return _preparation_failure(authorization_result)
            payload = queue.hydrate_input(
                claimed.fence,
                hydrated_at=hydrated_at,
            )
            if payload is None:
                return _PreparationFailure(
                    SemanticResultStatus.UNAVAILABLE,
                    "worker.input_unavailable",
                )
            try:
                parsed = definition.input_contract.model_type.model_validate(payload)
            except ValidationError:
                return _PreparationFailure(
                    SemanticResultStatus.STALE_INPUT,
                    "worker.input_contract_mismatch",
                )
            task_input = parsed
            effective_budget = definition.run_budget
            if (
                task_input.requested_budget is not None
                and task_input.requested_budget.is_not_wider_than(
                    definition.run_budget
                )
            ):
                effective_budget = task_input.requested_budget
            if (
                len(task_input.evidence_manifest.references)
                > effective_budget.evidence_item_limit
                or sum(
                    reference.declared_characters
                    for reference in task_input.evidence_manifest.references
                )
                > effective_budget.hydrated_character_limit
            ):
                return _PreparationFailure(
                    SemanticResultStatus.BUDGET_EXHAUSTED,
                    "evidence.budget_exhausted",
                )
            authorization = queue.authorization_snapshot(
                claimed.fence,
                checked_at=hydrated_at,
            )
            if authorization is None:
                return _PreparationFailure(
                    SemanticResultStatus.POLICY_PAUSED,
                    "authorization.snapshot_unavailable",
                )
            evidence_content = queue.hydrate_evidence(
                claimed.fence,
                task_input.evidence_manifest,
                character_limit=effective_budget.hydrated_character_limit,
                hydrated_at=hydrated_at,
            )
            if evidence_content is EvidenceHydrationFailure.BUDGET_EXHAUSTED:
                return _PreparationFailure(
                    SemanticResultStatus.BUDGET_EXHAUSTED,
                    "evidence.budget_exhausted",
                )
            if evidence_content is EvidenceHydrationFailure.STALE_INPUT:
                return _PreparationFailure(
                    SemanticResultStatus.STALE_INPUT,
                    "evidence.stale",
                )
            if evidence_content is EvidenceHydrationFailure.UNAVAILABLE:
                return _PreparationFailure(
                    SemanticResultStatus.UNAVAILABLE,
                    "evidence.manifest_unavailable",
                )
            return task_input, authorization, evidence_content

        return self._transaction(operation)

    def _persist_result(
        self,
        claimed: ClaimedSemanticTask,
        result: SemanticTaskResult[BaseModel],
        sink: SemanticOutcomeSink,
        *,
        recorded_at: datetime,
    ) -> tuple[str, bool]:
        def operation(
            queue: PostgresSemanticTaskQueue,
            _context: object,
        ) -> str:
            settlement_result = result
            reauthorization = queue.reauthorize(
                claimed.fence,
                phase="outcome",
                checked_at=recorded_at,
            )
            if reauthorization is not ReauthorizationResult.AUTHORIZED:
                if reauthorization in (
                    ReauthorizationResult.STALE_FENCE,
                    ReauthorizationResult.INVALID_PHASE,
                ):
                    late_retained_settlement = (
                        settlement_result.status
                        == SemanticResultStatus.BUDGET_EXHAUSTED
                        and settlement_result.error_code
                        == "runtime.wall_clock_limit"
                    ) or (
                        settlement_result.status == SemanticResultStatus.CANCELLED
                        and settlement_result.error_code == "worker.cancelled"
                    )
                    if not late_retained_settlement:
                        return "stale_fence"
                else:
                    settlement_result = _reauthorization_failure_result(
                        settlement_result,
                        reauthorization,
                    )
            if settlement_result.status == SemanticResultStatus.SUCCEEDED:
                return sink.persist(
                    queue,
                    claimed.fence,
                    settlement_result,
                    recorded_at=recorded_at,
                )
            return queue.record_integrated_failure(
                claimed.fence,
                settlement_result,
                error_class=_error_class(settlement_result.error_code),
                retry_after_seconds=None,
                jitter_basis_points=self._config.jitter_basis_points,
                recorded_at=recorded_at,
            )

        try:
            return self._transaction(operation), True
        except PermissionError:
            return "stale_fence", False

    def _settle_preflight_failure(
        self,
        claimed: ClaimedSemanticTask,
        readiness: ProviderExecutionReadiness,
        *,
        error_code: str,
        output_contract_hash: str,
        status: SemanticResultStatus = SemanticResultStatus.FAILED,
    ) -> SemanticWorkerCycleReceipt:
        retry_class = retry_class_for_status(status)
        assert retry_class is not None
        result = SemanticTaskResult[BaseModel](
            status=status,
            task_id=str(claimed.fence.task_id),
            attempt_id=str(claimed.fence.attempt_id),
            task_kind=claimed.task_kind,
            contract_revision=claimed.contract_revision,
            output_contract_hash=output_contract_hash,
            error_code=error_code,
            retry_class=retry_class,
        )
        outcome_status, authorization_available = self._persist_result(
            claimed,
            result,
            SyntheticNonAuthoritativeOutcomeSink(),
            recorded_at=datetime.now(UTC),
        )
        return SemanticWorkerCycleReceipt(
            cycle_status=(
                WorkerCycleStatus.SETTLED
                if authorization_available and outcome_status != "stale_fence"
                else WorkerCycleStatus.LATE_OUTPUT_DISCARDED
                if authorization_available
                else WorkerCycleStatus.AUTHORIZATION_UNAVAILABLE
            ),
            provider_state=readiness.state,
            provider_reason_code=readiness.reason_code,
            task_id=claimed.fence.task_id,
            attempt_id=claimed.fence.attempt_id,
            task_status=outcome_status,
            result_status=status,
        )

    def _discarded_receipt(
        self,
        claimed: ClaimedSemanticTask,
        readiness: ProviderExecutionReadiness,
    ) -> SemanticWorkerCycleReceipt:
        return SemanticWorkerCycleReceipt(
            cycle_status=WorkerCycleStatus.LATE_OUTPUT_DISCARDED,
            provider_state=readiness.state,
            provider_reason_code=readiness.reason_code,
            task_id=claimed.fence.task_id,
            attempt_id=claimed.fence.attempt_id,
            task_status="stale_fence",
        )

    def _definition_for(
        self,
        claimed: ClaimedSemanticTask,
    ) -> SemanticTaskDefinition[BaseModel, BaseModel] | None:
        if claimed.semantic_registry_hash != self._semantic_registry.registry_hash:
            return None
        return next(
            (
                definition
                for definition in self._semantic_registry.definitions
                if definition.task_kind == claimed.task_kind
                and definition.contract_revision == claimed.contract_revision
                and definition.contract_hash == claimed.task_contract_hash
            ),
            None,
        )

    def _transaction[T](
        self,
        operation: Callable[[PostgresSemanticTaskQueue, object], T],
    ) -> T:
        with self._connection_factory() as connection:
            with connection.transaction():
                connection.execute("SET LOCAL ROLE memoriesql_worker")
                context = PostgresAuthorizationPort(connection).begin_context(
                    credential_sha256=self._identity.credential_sha256,
                    requested_workspace_id=self._identity.workspace_id,
                )
                return operation(PostgresSemanticTaskQueue(connection), context)


@dataclass(frozen=True, slots=True)
class _PreparationFailure:
    status: SemanticResultStatus
    error_code: str


def _preparation_failure(
    result: ReauthorizationResult,
) -> _PreparationFailure:
    if result is ReauthorizationResult.POLICY_PAUSED:
        return _PreparationFailure(
            SemanticResultStatus.POLICY_PAUSED,
            "authorization.policy_paused",
        )
    if result is ReauthorizationResult.STALE_EVIDENCE:
        return _PreparationFailure(
            SemanticResultStatus.STALE_INPUT,
            "evidence.stale",
        )
    if result is ReauthorizationResult.EVIDENCE_UNAVAILABLE:
        return _PreparationFailure(
            SemanticResultStatus.UNAVAILABLE,
            "evidence.unavailable",
        )
    return _PreparationFailure(
        SemanticResultStatus.FAILED,
        "worker.fence_unavailable",
    )


def _reauthorization_failure_result(
    result: SemanticTaskResult[BaseModel],
    authorization: ReauthorizationResult,
) -> SemanticTaskResult[BaseModel]:
    failure = _preparation_failure(authorization)
    retry_class = retry_class_for_status(failure.status)
    assert retry_class is not None
    return SemanticTaskResult[BaseModel](
        status=failure.status,
        task_id=result.task_id,
        attempt_id=result.attempt_id,
        task_kind=result.task_kind,
        contract_revision=result.contract_revision,
        output_contract_hash=result.output_contract_hash,
        usage=result.usage,
        error_code=failure.error_code,
        retry_class=retry_class,
    )


def _error_class(error_code: str | None) -> str:
    if error_code is None:
        return "semantic"
    prefix = error_code.partition(".")[0]
    return prefix if prefix else "semantic"
