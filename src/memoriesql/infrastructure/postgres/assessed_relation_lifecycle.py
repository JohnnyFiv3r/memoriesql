"""Short human-governance transactions; no queue, model or provider work."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.errors import (
    InsufficientPrivilege,
    InvalidAuthorizationSpecification,
    InvalidTextRepresentation,
    LockNotAvailable,
    NoDataFound,
    QueryCanceled,
    UndefinedFunction,
    UntranslatableCharacter,
)
from psycopg.pq import TransactionStatus
from psycopg.types.json import Jsonb
from pydantic import TypeAdapter

from memoriesql.application.assessed_relation_lifecycle import (
    AssessedRelationEventRefusal,
    EventResult,
    InspectBeadRelationsV3,
    InspectionResult,
    RecordAssessedRelationEvent,
    RelationInspectionRefusal,
    RelationInspectionUnavailable,
)
from memoriesql.application.semantic_task_contracts import canonical_json_bytes
from memoriesql.infrastructure.postgres.authorization import PostgresAuthorizationPort
from memoriesql.infrastructure.postgres.relation_projection import (
    read_relation_projection,
)


class PostgresAssessedRelationLifecycle:
    def __init__(
        self, connection: Connection[Any], *, credential_sha256: str, workspace_id: UUID
    ) -> None:
        self._connection = connection
        self._credential = credential_sha256
        self._workspace = workspace_id

    def record_event(self, command: RecordAssessedRelationEvent) -> EventResult:
        payload = RecordAssessedRelationEvent.model_validate(
            command.model_dump(mode="json")
        )
        if self._connection.info.transaction_status != TransactionStatus.IDLE:
            raise RuntimeError("assessed lifecycle requires transaction ownership")
        try:
            with self._connection.transaction():
                self._connection.execute(
                    "SET TRANSACTION ISOLATION LEVEL READ COMMITTED"
                )
                self._connection.execute("SET LOCAL statement_timeout='2500ms'")
                self._connection.execute("SET LOCAL lock_timeout='500ms'")
                self._connection.execute("SET LOCAL ROLE memoriesql_application")
                PostgresAuthorizationPort(self._connection).begin_context(
                    credential_sha256=self._credential,
                    requested_workspace_id=self._workspace,
                )
                row = self._connection.execute(
                    "SELECT memoriesql.record_assessed_relation_event_v1(%s)",
                    (Jsonb(payload.model_dump(mode="json")),),
                ).fetchone()
                if row is None:
                    return AssessedRelationEventRefusal(error="unavailable")
                if len(canonical_json_bytes(row[0])) > 16384:
                    raise ValueError("lifecycle response exceeds bound")
                result: EventResult = TypeAdapter(EventResult).validate_python(row[0])
            return result
        except (InsufficientPrivilege, InvalidAuthorizationSpecification, NoDataFound):
            return AssessedRelationEventRefusal(error="unavailable")
        except (InvalidTextRepresentation, UntranslatableCharacter):
            return AssessedRelationEventRefusal(error="invalid_request")
        except (QueryCanceled, LockNotAvailable):
            return AssessedRelationEventRefusal(error="budget_exhausted")
        except UndefinedFunction as error:
            if "record_assessed_relation_event_v1" not in str(error):
                raise
            return AssessedRelationEventRefusal(error="schema_mismatch")

    def inspect_relations(self, request: InspectBeadRelationsV3) -> InspectionResult:
        payload = InspectBeadRelationsV3.model_validate(request.model_dump(mode="json"))
        try:
            row = read_relation_projection(
                self._connection,
                credential_sha256=self._credential,
                workspace_id=self._workspace,
                query="SELECT memoriesql.inspect_bead_relations_v3(%s)",
                payload=payload.model_dump(mode="json"),
            )
            if len(canonical_json_bytes(row)) > 524288:
                return RelationInspectionUnavailable(outcome="budget_exhausted")
            return TypeAdapter(InspectionResult).validate_python(row)
        except (InsufficientPrivilege, InvalidAuthorizationSpecification, NoDataFound):
            return RelationInspectionUnavailable(outcome="unavailable")
        except (QueryCanceled, LockNotAvailable):
            return RelationInspectionUnavailable(outcome="budget_exhausted")
        except UndefinedFunction as error:
            if "inspect_bead_relations_v3(" not in str(error).split("CONTEXT:")[0]:
                raise
            return RelationInspectionRefusal(error="schema_mismatch")
