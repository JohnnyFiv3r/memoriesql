"""Trusted relation assessment evidence access; never a model tool or input field."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import UUID

from memoriesql.application.relation_assessment import (
    EvidenceExcerpt,
    RelationAuthorPacket,
    RelationSpecialistPacket,
)


class RelationAssessmentEvidenceAccess:
    """Fenced evidence reads and the pre-dispatch recheck for one attempt."""

    def __init__(
        self,
        *,
        task_id: UUID,
        attempt_id: UUID,
        read: Callable[[], Awaitable[tuple[EvidenceExcerpt, ...]]],
        authorize: Callable[[], Awaitable[None]],
    ) -> None:
        self.task_id = task_id
        self.attempt_id = attempt_id
        self._read = read
        self._authorize = authorize

    async def evidence(self) -> tuple[EvidenceExcerpt, ...]:
        return await self._read()

    async def authorize_delivery(
        self, packet: RelationAuthorPacket | RelationSpecialistPacket
    ) -> None:
        if packet.task_id != self.task_id or packet.attempt_id != self.attempt_id:
            raise PermissionError("relation packet belongs to a different attempt")
        await self._authorize()
