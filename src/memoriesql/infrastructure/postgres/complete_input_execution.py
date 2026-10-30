"""Short authorized transactions for explicit activation, reader v2 and attestation."""

from __future__ import annotations

import asyncio
import contextvars
import threading
from collections.abc import Callable
from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.pq import TransactionStatus
from psycopg.types.json import Jsonb

from memoriesql.application.authored_relations import (
    ActivateRelatedAuthorship,
    RelatedActivationReceipt,
)
from memoriesql.application.bead_classification import (
    ActivateClassifiedAuthorship,
    ClassificationActivationReceipt,
    ClassificationDecision,
    ClassificationPacket,
)
from memoriesql.application.complete_input_execution import (
    READER_RESPONSE_BYTES,
    ActivateCompleteInput,
    ActivateRepairableCompleteInput,
    CompleteEvidenceBatch,
    CompleteInputActivationReceipt,
    EvidenceExecutionWindow,
    ReadCompleteEvidence,
    RepairableActivationReceipt,
)
from memoriesql.application.local_entity_mentions import (
    ActivateAuthoredMentions,
    ActivateMentionAuthorship,
    AuthoredMentionActivationReceipt,
    MentionActivationReceipt,
)
from memoriesql.application.semantic_task_contracts import canonical_json_bytes
from memoriesql.application.source_revisiting import (
    ActivateSourceRevisiting,
    SourceDelivery,
    SourceRevisitingActivationReceipt,
)
from memoriesql.infrastructure.postgres.authorization import PostgresAuthorizationPort
from memoriesql.infrastructure.postgres.transient_retry import run_owned


async def _await_owned_write(write: Callable[[], None]) -> None:
    """Drain the actual write/context, even if its observer or Future is cancelled."""
    loop = asyncio.get_running_loop()
    completed = asyncio.Event()
    failure: BaseException | None = None
    transition = threading.Lock()
    started = False
    aborted = False

    def capture() -> None:
        nonlocal failure, started
        with transition:
            if aborted:
                return
            started = True
        try:
            write()
        except BaseException as error:
            failure = error
        finally:
            # Publish only after write() returned/raised, including context exit.
            loop.call_soon_threadsafe(completed.set)

    context = contextvars.copy_context()
    # No inner Task for loop-wide Task cancellation to destroy. The independent
    # outcome/event also survives cancellation of this Future after write-start.
    _operation = loop.run_in_executor(None, context.run, capture)

    def cancel_unstarted(operation: asyncio.Future[None]) -> None:
        nonlocal aborted, failure
        if not operation.cancelled():
            return
        with transition:
            if started:
                return  # The independent completion still owns this real write.
            aborted = True  # Fence capture before reporting definitely unstarted.
            failure = asyncio.CancelledError()
        completed.set()

    _operation.add_done_callback(cancel_unstarted)
    cancelled: asyncio.CancelledError | None = None
    while not completed.is_set():
        try:
            # Cancelling a waiter removes that waiter, never the completion event.
            await completed.wait()
        except asyncio.CancelledError as error:
            cancelled = error
    if failure is not None:
        raise failure
    if cancelled is not None:
        raise cancelled


class PostgresCompleteInput:
    def __init__(
        self, connection: Connection[Any], *, credential_sha256: str, workspace_id: UUID
    ) -> None:
        self.connection = connection
        self.credential = credential_sha256
        self.workspace = workspace_id

    def activate_classified(self, request: ActivateClassifiedAuthorship) -> ClassificationActivationReceipt:
        return ClassificationActivationReceipt.model_validate(self._call(
            "SELECT memoriesql.activate_complete_input_v4(%s)",
            (Jsonb(request.model_dump(mode="json")),), role="memoriesql_application"))

    def activate_related(
        self, request: ActivateRelatedAuthorship
    ) -> RelatedActivationReceipt:
        request = ActivateRelatedAuthorship.model_validate(
            request.model_dump(mode="json")
        )
        return RelatedActivationReceipt.model_validate(
            self._call(
                "SELECT memoriesql.activate_complete_input_v5(%s)",
                (Jsonb(request.model_dump(mode="json")),),
                role="memoriesql_application",
            )
        )

    def activate_mentions(
        self, request: ActivateMentionAuthorship
    ) -> MentionActivationReceipt:
        request = ActivateMentionAuthorship.model_validate(
            request.model_dump(mode="json")
        )
        return MentionActivationReceipt.model_validate(
            self._call(
                "SELECT memoriesql.activate_complete_input_v3(%s)",
                (Jsonb(request.model_dump(mode="json")),),
                role="memoriesql_application",
            )
        )

    def activate_authored_mentions(
        self, request: ActivateAuthoredMentions
    ) -> AuthoredMentionActivationReceipt:
        request = ActivateAuthoredMentions.model_validate(
            request.model_dump(mode="json")
        )
        return AuthoredMentionActivationReceipt.model_validate(
            self._call(
                "SELECT memoriesql.activate_complete_input_v6(%s)",
                (Jsonb(request.model_dump(mode="json")),),
                role="memoriesql_application",
            )
        )

    def activate_revisiting(
        self, request: ActivateSourceRevisiting
    ) -> SourceRevisitingActivationReceipt:
        request = ActivateSourceRevisiting.model_validate(
            request.model_dump(mode="json")
        )
        return SourceRevisitingActivationReceipt.model_validate(
            self._call(
                "SELECT memoriesql.activate_complete_input_v2(%s)",
                (Jsonb(request.model_dump(mode="json")),),
                role="memoriesql_application",
            )
        )

    def activate(
        self, request: ActivateCompleteInput
    ) -> CompleteInputActivationReceipt:
        request = ActivateCompleteInput.model_validate(request.model_dump(mode="json"))
        return CompleteInputActivationReceipt.model_validate(
            self._call(
                "SELECT memoriesql.activate_complete_input_v1(%s)",
                (Jsonb(request.model_dump(mode="json")),),
                role="memoriesql_application",
            )
        )

    def activate_repairable(
        self, request: ActivateRepairableCompleteInput
    ) -> RepairableActivationReceipt:
        request = ActivateRepairableCompleteInput.model_validate(
            request.model_dump(mode="json")
        )
        return RepairableActivationReceipt.model_validate(
            self._call(
                "SELECT memoriesql.activate_complete_input_v7(%s)",
                (Jsonb(request.model_dump(mode="json")),),
                role="memoriesql_application",
            )
        )

    def read(self, request: ReadCompleteEvidence) -> CompleteEvidenceBatch:
        request = ReadCompleteEvidence.model_validate(request.model_dump(mode="json"))
        return CompleteEvidenceBatch.model_validate(
            self._call(
                "SELECT memoriesql.read_complete_evidence_v2(%s)",
                (Jsonb(request.model_dump(mode="json")),),
                role="memoriesql_application",
            )
        )

    def _call(self, query: str, parameters: tuple[Any, ...], *, role: str) -> Any:
        if self.connection.info.transaction_status != TransactionStatus.IDLE:
            raise RuntimeError("complete input session requires transaction ownership")

        def attempt() -> Any:
            with self.connection.transaction():
                self.connection.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
                self.connection.execute("SET LOCAL statement_timeout='2s'")
                self.connection.execute("SET LOCAL lock_timeout='500ms'")
                if role == "memoriesql_worker":
                    self.connection.execute("SET LOCAL ROLE memoriesql_worker")
                else:
                    self.connection.execute("SET LOCAL ROLE memoriesql_application")
                PostgresAuthorizationPort(self.connection).begin_context(
                    credential_sha256=self.credential, requested_workspace_id=self.workspace
                )
                row = self.connection.execute(query, parameters).fetchone()
                if row is None or row[0] is None:
                    raise PermissionError("complete input unavailable")
                if len(canonical_json_bytes(row[0])) > READER_RESPONSE_BYTES:
                    raise ValueError("complete input response bound")
                return row[0]

        return run_owned(attempt)


class PostgresEvidenceExposureRecorder:
    """Use a separately trusted attestor credential, never the input or model.

    Provisioning and review of this identity is deliberately external. Reads and
    ordinary worker credentials cannot certify dispatch. Started writes are owned
    through cancellation; the caller's cleanup cannot abandon a database thread.
    """

    def __init__(
        self,
        *,
        connection_factory: Callable[[], Connection[Any]],
        credential_sha256: str,
        workspace_id: UUID,
    ) -> None:
        self.connection_factory = connection_factory
        self.credential = credential_sha256
        self.workspace = workspace_id

    async def record_classification(self, *, request_id: UUID, request_payload_hash: str,
                                    packet: ClassificationPacket, decision: ClassificationDecision) -> None:
        def write() -> None:
            with self.connection_factory() as connection:
                PostgresCompleteInput(connection, credential_sha256=self.credential,
                    workspace_id=self.workspace)._call(
                    "SELECT memoriesql.record_classification_v1(%s,%s,%s,%s)",
                    (request_id,request_payload_hash,Jsonb(packet.model_dump(mode="json")),Jsonb(decision.model_dump(mode="json"))),
                    role="memoriesql_worker")
        await _await_owned_write(write)

    async def record_delivery(
        self, *, request_id: UUID, request_payload_hash: str, delivery: SourceDelivery
    ) -> None:
        payload = SourceDelivery.model_validate(
            delivery.model_dump(mode="json")
        ).model_dump(mode="json")

        def write() -> None:
            with self.connection_factory() as connection:
                PostgresCompleteInput(
                    connection,
                    credential_sha256=self.credential,
                    workspace_id=self.workspace,
                )._call(
                    "SELECT memoriesql.record_source_delivery_v1(%s,%s,%s)",
                    (request_id, request_payload_hash, Jsonb(payload)),
                    role="memoriesql_worker",
                )

        await _await_owned_write(write)

    async def record_received(
        self,
        *,
        request_id: UUID,
        request_payload_hash: str,
        window: EvidenceExecutionWindow,
    ) -> None:
        payload = EvidenceExecutionWindow.model_validate(
            window.model_dump(mode="json")
        ).model_dump(mode="json")

        def write() -> None:
            with self.connection_factory() as connection:
                PostgresCompleteInput(
                    connection,
                    credential_sha256=self.credential,
                    workspace_id=self.workspace,
                )._call(
                    "SELECT memoriesql.record_complete_input_exposure_v1(%s,%s,%s)",
                    (request_id, request_payload_hash, Jsonb(payload)),
                    role="memoriesql_worker",
                )

        await _await_owned_write(write)
