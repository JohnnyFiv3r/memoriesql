"""Short authorized transactions for claim/relation lifecycle, vocabulary and reads."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.errors import InsufficientPrivilege, LockNotAvailable, QueryCanceled
from psycopg.pq import TransactionStatus
from psycopg.types.json import Jsonb
from pydantic import BaseModel

from memoriesql.application.relation_lifecycle import (
    BeadRelationsInspection,
    ClaimEventReceipt,
    DecideRelationType,
    InspectBeadRelations,
    ProposeRelationType,
    RecordClaimEvent,
    RecordRelationEvent,
    RelationEventReceipt,
    RelationTypeDecisionReceipt,
    RelationTypeProposalReceipt,
)
from memoriesql.application.semantic_task_contracts import canonical_json_bytes
from memoriesql.infrastructure.postgres.authorization import PostgresAuthorizationPort

INSPECTION_RESPONSE_BYTES = 262144
COMMAND_RESPONSE_BYTES = 16384


class PostgresRelationLifecycle:
    """Every call is one fresh application-role transaction under the caller's context."""

    def __init__(
        self, connection: Connection[Any], *, credential_sha256: str, workspace_id: UUID
    ) -> None:
        self._connection = connection
        self._credential = credential_sha256
        self._workspace = workspace_id

    def record_claim_event(self, command: RecordClaimEvent) -> ClaimEventReceipt:
        return self._command(
            "SELECT memoriesql.record_claim_event_v1(%s)", command, ClaimEventReceipt
        )

    def record_relation_event(self, command: RecordRelationEvent) -> RelationEventReceipt:
        return self._command(
            "SELECT memoriesql.record_relation_event_v1(%s)", command, RelationEventReceipt
        )

    def propose_relation_type(
        self, command: ProposeRelationType
    ) -> RelationTypeProposalReceipt:
        return self._command(
            "SELECT memoriesql.propose_relation_type_v1(%s)",
            command,
            RelationTypeProposalReceipt,
        )

    def decide_relation_type(
        self, command: DecideRelationType
    ) -> RelationTypeDecisionReceipt:
        return self._command(
            "SELECT memoriesql.decide_relation_type_v1(%s)",
            command,
            RelationTypeDecisionReceipt,
        )

    def inspect_relations(self, request: InspectBeadRelations) -> BeadRelationsInspection:
        try:
            return self._transaction(
                "SELECT memoriesql.inspect_bead_relations_v1(%s)",
                request,
                BeadRelationsInspection,
                bound=INSPECTION_RESPONSE_BYTES,
                statement_timeout_ms=2500,
            )
        except InsufficientPrivilege:
            return BeadRelationsInspection(outcome="unavailable")
        except (QueryCanceled, LockNotAvailable):
            return BeadRelationsInspection(outcome="budget_exhausted")

    def _command[ResultT: BaseModel](
        self, query: str, command: BaseModel, result_type: type[ResultT]
    ) -> ResultT:
        return self._transaction(
            query,
            command,
            result_type,
            bound=COMMAND_RESPONSE_BYTES,
            statement_timeout_ms=2000,
        )

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
            raise RuntimeError("relation lifecycle requires transaction ownership")
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
                raise PermissionError("relation lifecycle unavailable")
            if len(canonical_json_bytes(row[0])) > bound:
                raise ValueError("relation lifecycle response exceeds bound")
            result = result_type.model_validate(row[0])
        return result
