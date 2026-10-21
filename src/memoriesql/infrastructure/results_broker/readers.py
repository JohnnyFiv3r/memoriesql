"""The existing authorized readers (inspect, source, relations) as closed bytes.

`read_core` is the single implementation behind both the in-process CLI path
(`DirectReadTransport`) and the trusted host, so both return identical bytes:
the reader's typed result as JSON, or exactly the refusal the CLI has always
reported. It adds no semantics to the readers it calls.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any
from uuid import UUID

import psycopg
from psycopg import Connection
from psycopg.errors import (
    InsufficientPrivilege,
    InvalidAuthorizationSpecification,
    NoDataFound,
)
from pydantic import BaseModel

from memoriesql.application.relation_inspection import InspectBeadRelationsV2
from memoriesql.application.stored_bead_inspection import (
    InspectStoredBead,
    ReadStoredBeadEvidence,
)
from memoriesql.infrastructure.postgres.relation_assessment import (
    PostgresRelationAssessments,
)
from memoriesql.infrastructure.postgres.stored_bead_inspection import (
    PostgresStoredBeadInspection,
)
from memoriesql.infrastructure.results_broker.wire import (
    READ_FAILED_REPLY,
    READ_OPERATIONS,
    READ_UNAVAILABLE_REPLY,
    read_reply,
)

ConnectionFactory = Callable[[], Connection[Any]]
_REQUESTS: dict[str, type[BaseModel]] = {
    "inspect": InspectStoredBead,
    "source": ReadStoredBeadEvidence,
    "relations": InspectBeadRelationsV2,
}


def read_core(
    command: str,
    request: bytes,
    connect: ConnectionFactory,
    *,
    credential_sha256: str,
    workspace_id: UUID,
) -> bytes:
    """Run one existing reader for the authenticated principal; never raises."""
    model = _REQUESTS.get(command)
    if command not in READ_OPERATIONS or model is None:
        return READ_FAILED_REPLY
    try:
        parsed = model.model_validate_json(request)
    except ValueError:
        return READ_FAILED_REPLY
    result: BaseModel
    try:
        with connect() as connection:
            if isinstance(parsed, InspectBeadRelationsV2):
                result = PostgresRelationAssessments(
                    connection,
                    credential_sha256=credential_sha256,
                    workspace_id=workspace_id,
                ).inspect_relations(parsed)
            else:
                reader = PostgresStoredBeadInspection(
                    connection,
                    credential_sha256=credential_sha256,
                    workspace_id=workspace_id,
                )
                if isinstance(parsed, InspectStoredBead):
                    result = reader.inspect(parsed)
                elif isinstance(parsed, ReadStoredBeadEvidence):
                    result = reader.read(parsed)
                else:
                    return READ_FAILED_REPLY
    except (
        PermissionError,
        InsufficientPrivilege,
        InvalidAuthorizationSpecification,
        NoDataFound,
    ):
        return READ_UNAVAILABLE_REPLY
    except Exception:
        # A failure is never a successful empty result. Hide connection strings,
        # secrets, protected IDs, SQL diagnostics and partial content.
        return READ_FAILED_REPLY
    payload = result.model_dump(mode="json")
    return json.dumps(
        payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("ascii")


class DirectReadTransport:
    """In-process twin of the host's read operation, for a trusted host only."""

    def __init__(
        self, database_url: str, credential_sha256: str, workspace_id: UUID
    ) -> None:
        self._database_url = database_url
        self._credential = credential_sha256
        self._workspace = workspace_id

    def read(self, command: str, request: bytes) -> bytes:
        return read_core(
            command,
            request,
            lambda: psycopg.connect(self._database_url, autocommit=True),
            credential_sha256=self._credential,
            workspace_id=self._workspace,
        )


__all__ = [
    "DirectReadTransport",
    "READ_FAILED_REPLY",
    "READ_UNAVAILABLE_REPLY",
    "read_core",
    "read_reply",
]
