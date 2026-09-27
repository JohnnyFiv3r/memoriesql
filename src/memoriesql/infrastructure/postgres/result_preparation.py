"""Private durable preparation; deliberately has no result-disclosure operation.

This store is a trusted executor dependency, not an agent capability. It seals
exact bytes and owns reservations; it does not certify provenance completeness,
resource authorization, physical allocation, or an available query response.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.pq import TransactionStatus
from psycopg.types.json import Jsonb

from memoriesql.application.investigation_contracts import result_json_bytes
from memoriesql.infrastructure.postgres.relation_projection import (
    relation_projection_frame,
)


@dataclass(frozen=True, slots=True)
class PreparationPin:
    artifact_ref: UUID
    artifact_sha256: str

    def as_dict(self) -> dict[str, str]:
        return {
            "artifact_ref": str(self.artifact_ref),
            "artifact_sha256": self.artifact_sha256,
        }


@dataclass(frozen=True, slots=True)
class PreparationReceipt:
    operation_ref: UUID
    ownership_ref: UUID
    state: str
    reservation_bytes: int
    encoded_bytes: int
    allocation_bytes: int
    replayed: bool


@dataclass(frozen=True, slots=True)
class PreparedContent:
    """Canonical private partitions, not a public ResultPin/content digest.

    The executor must populate complete content, query/frame, witnesses and the
    protected population before this can support a result. No reconstruction or
    independent evidence admission happens here. Partition identity is explicit.
    """

    content: bytes
    witnesses: bytes
    dependencies: bytes

    @classmethod
    def encode(
        cls, *, content: Any, witnesses: Any, dependencies: Any
    ) -> PreparedContent:
        return cls(
            content=result_json_bytes(content),
            witnesses=result_json_bytes(witnesses),
            dependencies=result_json_bytes(dependencies),
        )

    def validate(self) -> None:
        # Revalidate manually constructed/copied values before the private SQL
        # boundary. Strict decoding preserves duplicate-key rejection and typed
        # controls; the byte comparison refuses alternate canonical spellings.
        import json

        def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in items:
                if key in result:
                    raise ValueError("invalid preparation encoding")
                result[key] = value
            return result

        def invalid(value: str) -> Any:
            raise ValueError("invalid preparation encoding")

        partitions = (self.content, self.witnesses, self.dependencies)
        if any(type(data) is not bytes for data in partitions):
            raise ValueError("invalid preparation encoding")
        if sum(map(len, partitions)) + 8192 > 64 * 1024 * 1024:
            raise ValueError("preparation exceeds byte bound")
        for data in partitions:
            if type(data) is not bytes:
                raise ValueError("invalid preparation encoding")
            try:
                value = json.loads(
                    data.decode("utf-8"),
                    object_pairs_hook=pairs,
                    parse_float=invalid,
                    parse_constant=invalid,
                )
                if result_json_bytes(value) != data:
                    raise ValueError("invalid preparation encoding")
            except (UnicodeError, ValueError, TypeError, RecursionError) as error:
                raise ValueError("invalid preparation encoding") from error

    @property
    def encoded_bytes(self) -> int:
        return len(self.content) + len(self.witnesses) + len(self.dependencies)

    @property
    def artifact_sha256(self) -> str:
        # Domain separation plus lengths: different partition boundaries cannot
        # collide by concatenating identical bytes. This is NOT result-json-v1's
        # public content_digest; no wire digest is selected by this private store.
        digest = hashlib.sha256(b"memoriesql-result-preparation-v1\0")
        for data in (self.content, self.witnesses, self.dependencies):
            digest.update(len(data).to_bytes(8, "big"))
            digest.update(data)
        return digest.hexdigest()


class PostgresResultPreparation:
    """Own short authority-fenced transactions; never deliver stored bytes.

    A reservation commits before execution can begin. Identical concurrent keys
    do not mint new ownership. Recovery returns only private journal state and
    cannot take over an unfinished owner or repeat the original query.
    """

    def __init__(
        self,
        connection: Connection[Any],
        *,
        credential_sha256: str,
        workspace_id: UUID,
    ) -> None:
        self._connection = connection
        self._credential = credential_sha256
        self._workspace = workspace_id

    def reserve(
        self,
        *,
        run_ref: UUID,
        step_key: UUID,
        request_fingerprint: str,
        reservation_bytes: int,
    ) -> PreparationReceipt:
        if (
            len(request_fingerprint) != 64
            or any(c not in "0123456789abcdef" for c in request_fingerprint)
            or type(reservation_bytes) is not int
            or not 8192 <= reservation_bytes <= 64 * 1024 * 1024
        ):
            raise ValueError("invalid preparation reservation")
        return self._call(
            "SELECT memoriesql.reserve_result_preparation_v1(%s,%s,%s,%s)",
            (run_ref, step_key, request_fingerprint, reservation_bytes),
        )

    def seal(
        self,
        *,
        operation_ref: UUID,
        ownership_ref: UUID,
        artifact_ref: UUID,
        content: PreparedContent,
        parents: tuple[PreparationPin, ...] = (),
    ) -> PreparationReceipt:
        content.validate()
        if len(parents) > 8 or len({p.artifact_ref for p in parents}) != len(parents):
            raise ValueError("invalid preparation parents")
        for parent in parents:
            if len(parent.artifact_sha256) != 64 or any(
                c not in "0123456789abcdef" for c in parent.artifact_sha256
            ):
                raise ValueError("invalid preparation parents")
        return self._call(
            "SELECT memoriesql.seal_result_preparation_v1(%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                operation_ref,
                ownership_ref,
                artifact_ref,
                content.content,
                content.witnesses,
                content.dependencies,
                content.artifact_sha256,
                Jsonb([p.as_dict() for p in parents]),
            ),
        )

    def discard(
        self, *, operation_ref: UUID, ownership_ref: UUID
    ) -> PreparationReceipt:
        return self._call(
            "SELECT memoriesql.discard_result_preparation_v1(%s,%s)",
            (operation_ref, ownership_ref),
        )

    def _call(self, query: str, values: tuple[Any, ...]) -> PreparationReceipt:
        if self._connection.info.transaction_status != TransactionStatus.IDLE:
            raise RuntimeError("preparation requires transaction ownership")
        with relation_projection_frame(
            self._connection,
            credential_sha256=self._credential,
            workspace_id=self._workspace,
        ) as frame:
            row = frame.execute(query, values).fetchone()
            if row is None or row[0] is None:
                raise PermissionError("preparation unavailable")
            result = row[0]
        return PreparationReceipt(
            operation_ref=UUID(result["operation_ref"]),
            ownership_ref=UUID(result["ownership_ref"]),
            state=result["state"],
            reservation_bytes=int(result["reservation_bytes"]),
            encoded_bytes=int(result["encoded_bytes"]),
            allocation_bytes=int(result["allocation_bytes"]),
            replayed=bool(result["replayed"]),
        )
