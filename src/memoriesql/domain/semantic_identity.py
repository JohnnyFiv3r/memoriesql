from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class EntityResolutionStatus(StrEnum):
    UNRESOLVED = "unresolved"
    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    REJECTED = "rejected"


class EntityStatus(StrEnum):
    PROVISIONAL = "provisional"
    ACTIVE = "active"
    MERGED = "merged"
    SPLIT = "split"
    DEPRECATED = "deprecated"


@dataclass(frozen=True, slots=True)
class BeadTypeRevisionReference:
    bead_type_id: UUID
    bead_type_revision_id: UUID


@dataclass(frozen=True, slots=True)
class BeadVersionReference:
    bead_id: UUID
    bead_version_id: UUID


@dataclass(frozen=True, slots=True)
class EntityRevisionReference:
    entity_id: UUID
    entity_revision_id: UUID


@dataclass(frozen=True, slots=True)
class TopicRevisionReference:
    topic_id: UUID
    topic_revision_id: UUID


@dataclass(frozen=True, slots=True)
class EntityRead:
    entity_id: UUID
    entity_revision_id: UUID
    revision: int
    entity_type_id: UUID | None
    entity_type_revision_id: UUID | None
    canonical_label: str
    resolved_entity_id: UUID
    as_of: datetime


@dataclass(frozen=True, slots=True)
class TopicRead:
    topic_id: UUID
    topic_revision_id: UUID
    revision: int
    label: str
    resolved_topic_id: UUID
    as_of: datetime
