from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from memoriesql.domain.semantic_identity import EntityRead, TopicRead


class SemanticIdentityReadPort(Protocol):
    """ID-first reads; visible labels are returned data, never lookup authority."""

    def read_entity_as_of(
        self, *, entity_id: UUID, as_of: datetime
    ) -> EntityRead | None: ...

    def read_topic_as_of(
        self, *, topic_id: UUID, as_of: datetime
    ) -> TopicRead | None: ...
