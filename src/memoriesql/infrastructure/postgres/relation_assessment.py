"""Short authorized transactions for relation assessment activation, reads and attestation."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.errors import InsufficientPrivilege, LockNotAvailable, QueryCanceled
from psycopg.pq import TransactionStatus
from psycopg.types.json import Jsonb
from pydantic import BaseModel

from memoriesql.application.relation_assessment import (
    ActivateRelationAssessment,
    RelationAssessmentActivationReceipt,
    RelationAuthorPacket,
    RelationSpecialistDecision,
    RelationSpecialistPacket,
)
from memoriesql.application.relation_inspection import (
    BeadRelationsInspectionV2,
    InspectBeadRelationsV2,
    InspectRelationVocabulary,
    RelationVocabularyInspection,
)
from memoriesql.application.semantic_task_contracts import canonical_json_bytes
from memoriesql.infrastructure.postgres.authorization import PostgresAuthorizationPort

INSPECTION_RESPONSE_BYTES = 524288
COMMAND_RESPONSE_BYTES = 16384


class PostgresRelationAssessments:
    """Every call is one fresh application-role transaction under the caller's context."""

    def __init__(
        self, connection: Connection[Any], *, credential_sha256: str, workspace_id: UUID
    ) -> None:
        self._connection = connection
        self._credential = credential_sha256
        self._workspace = workspace_id

    def activate(
        self, command: ActivateRelationAssessment
    ) -> RelationAssessmentActivationReceipt:
        return self._transaction(
            "SELECT memoriesql.activate_relation_assessment_v1(%s)",
            command,
            RelationAssessmentActivationReceipt,
            bound=COMMAND_RESPONSE_BYTES,
            statement_timeout_ms=5000,
        )

    def inspect_relations(self, request: InspectBeadRelationsV2) -> BeadRelationsInspectionV2:
        try:
            return self._transaction(
                "SELECT memoriesql.inspect_bead_relations_v2(%s)",
                request,
                BeadRelationsInspectionV2,
                bound=INSPECTION_RESPONSE_BYTES,
                statement_timeout_ms=2500,
            )
        except InsufficientPrivilege:
            return BeadRelationsInspectionV2(outcome="unavailable")
        except (QueryCanceled, LockNotAvailable):
            return BeadRelationsInspectionV2(outcome="budget_exhausted")

    def inspect_vocabulary(
        self, request: InspectRelationVocabulary
    ) -> RelationVocabularyInspection:
        try:
            return self._transaction(
                "SELECT memoriesql.inspect_relation_vocabulary_v1(%s)",
                request,
                RelationVocabularyInspection,
                bound=INSPECTION_RESPONSE_BYTES,
                statement_timeout_ms=2500,
            )
        except InsufficientPrivilege:
            return RelationVocabularyInspection(outcome="unavailable")

    def _transaction[ResultT: BaseModel](
        self,
        query: str,
        request: BaseModel,
        result_type: type[ResultT],
        *,
        bound: int,
        statement_timeout_ms: int,
    ) -> ResultT:
        payload = type(request).model_validate(request.model_dump(mode="json"))
        if self._connection.info.transaction_status != TransactionStatus.IDLE:
            raise RuntimeError("relation assessment requires transaction ownership")
        with self._connection.transaction():
            self._connection.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
            self._connection.execute(
                "SELECT set_config('statement_timeout',%s,true), "
                "set_config('lock_timeout','500',true)",
                (str(statement_timeout_ms),),
            )
            self._connection.execute("SET LOCAL ROLE memoriesql_application")
            PostgresAuthorizationPort(self._connection).begin_context(
                credential_sha256=self._credential,
                requested_workspace_id=self._workspace,
            )
            row = self._connection.execute(
                query, (Jsonb(payload.model_dump(mode="json")),)
            ).fetchone()
            if row is None or row[0] is None:
                raise PermissionError("relation assessment unavailable")
            if len(canonical_json_bytes(row[0])) > bound:
                raise ValueError("relation assessment response exceeds bound")
            result = result_type.model_validate(row[0])
        return result


class PostgresRelationDeliveryRecorder:
    """Use a separately trusted attestor credential, never the input or model.

    The attestor re-derives every delivered excerpt from storage under its own
    authority. Started writes are owned through cancellation, so the caller's
    cleanup cannot abandon a database thread.
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

    async def record_relation_delivery(
        self,
        *,
        request_id: UUID,
        request_payload_hash: str,
        packet: RelationAuthorPacket | RelationSpecialistPacket,
        decision: RelationSpecialistDecision | None,
    ) -> None:
        body = type(packet).model_validate(packet.model_dump(mode="json")).model_dump(mode="json")
        judged = None if decision is None else decision.model_dump(mode="json")

        def write() -> None:
            with self.connection_factory() as connection:
                if connection.info.transaction_status != TransactionStatus.IDLE:
                    raise RuntimeError("relation attestation requires transaction ownership")
                with connection.transaction():
                    connection.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
                    connection.execute("SET LOCAL statement_timeout='5s'")
                    connection.execute("SET LOCAL lock_timeout='500ms'")
                    connection.execute("SET LOCAL ROLE memoriesql_worker")
                    PostgresAuthorizationPort(connection).begin_context(
                        credential_sha256=self.credential, requested_workspace_id=self.workspace
                    )
                    row = connection.execute(
                        "SELECT memoriesql.record_relation_delivery_v1(%s,%s,%s,%s)",
                        (
                            request_id,
                            request_payload_hash,
                            Jsonb(body),
                            None if judged is None else Jsonb(judged),
                        ),
                    ).fetchone()
                    if row is None or row[0] is not True:
                        raise PermissionError("relation delivery was not recorded")

        operation = asyncio.create_task(asyncio.to_thread(write))
        cancelled: asyncio.CancelledError | None = None
        while True:
            try:
                await asyncio.shield(operation)
                break
            except asyncio.CancelledError as error:
                cancelled = error
        if cancelled is not None:
            raise cancelled
