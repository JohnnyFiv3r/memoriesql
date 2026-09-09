from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.types.json import Jsonb

from memoriesql.application.model_accounting import (
    AccountingPersistenceError,
    CostSafetyCeilingExceeded,
    ModelUsageEvent,
    ProviderRequestIntent,
    RequiredModelAllowanceUnavailable,
)
from memoriesql.infrastructure.postgres.authorization import (
    PostgresAuthorizationPort,
)


class PostgresModelRequestAccounting:
    """Short-transaction adapter for the authoritative request ledger."""

    def __init__(
        self,
        *,
        connection_factory: Callable[[], Connection[Any]],
        credential_sha256: str,
        workspace_id: UUID,
        worker_id: str,
        worker_instance_id: str,
    ) -> None:
        self._connection_factory = connection_factory
        self._credential_sha256 = credential_sha256
        self._workspace_id = workspace_id
        self._worker_id = worker_id
        self._worker_instance_id = worker_instance_id

    async def record_intent(self, intent: ProviderRequestIntent) -> None:
        try:
            await _finish_database_write(self._record_intent, intent)
        except Exception as error:
            _raise_accounting_error(error)

    async def append_usage(self, event: ModelUsageEvent) -> None:
        try:
            await _finish_database_write(self._append_usage, event)
        except Exception as error:
            _raise_accounting_error(error)

    def _record_intent(self, intent: ProviderRequestIntent) -> None:
        with self._connection_factory() as connection:
            with connection.transaction():
                self._begin_worker_context(connection)
                connection.execute(
                    """
                    SELECT memoriesql.record_model_provider_request_intent(
                        %s, %s, %s
                    )
                    """,
                    (
                        Jsonb(intent.model_dump(mode="json", warnings="error")),
                        self._worker_id,
                        self._worker_instance_id,
                    ),
                ).fetchone()

    def _append_usage(self, event: ModelUsageEvent) -> None:
        with self._connection_factory() as connection:
            with connection.transaction():
                self._begin_worker_context(connection)
                connection.execute(
                    "SELECT memoriesql.append_model_usage_event(%s, %s, %s)",
                    (
                        Jsonb(event.model_dump(mode="json", warnings="error")),
                        self._worker_id,
                        self._worker_instance_id,
                    ),
                ).fetchone()

    def _begin_worker_context(self, connection: Connection[Any]) -> None:
        connection.execute("SET LOCAL ROLE memoriesql_worker")
        PostgresAuthorizationPort(connection).begin_context(
            credential_sha256=self._credential_sha256,
            requested_workspace_id=self._workspace_id,
        )


async def _finish_database_write[T](operation: Callable[[T], None], value: T) -> None:
    """Finish one started ledger write before propagating task cancellation."""

    def capture() -> Exception | None:
        try:
            operation(value)
        except Exception as error:
            return error
        return None

    task = asyncio.create_task(asyncio.to_thread(capture))
    delayed_cancellation: asyncio.CancelledError | None = None
    while True:
        try:
            write_error = await asyncio.shield(task)
        except asyncio.CancelledError as error:
            delayed_cancellation = error
            continue
        break
    if delayed_cancellation is not None:
        raise delayed_cancellation
    if write_error is not None:
        raise write_error


def _raise_accounting_error(error: Exception) -> None:
    detail = str(error)
    if "accounting.cost_safety_ceiling" in detail:
        raise CostSafetyCeilingExceeded(detail) from error
    if "accounting.required_model_allowance_unavailable" in detail:
        raise RequiredModelAllowanceUnavailable(detail) from error
    raise AccountingPersistenceError("durable model accounting failed") from error
