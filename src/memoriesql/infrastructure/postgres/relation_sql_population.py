"""Trusted typed preparation from PR-03; never an agent disclosure/execution API.

The caller owns admission/accounting and must consume this within the yielded
authority fence. Persist every partition and binding before publishing a result.
This object is not permission to disclose, replay or reauthorize a saved result.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Any
from uuid import UUID, uuid4

from psycopg import Connection
from psycopg.errors import (
    InsufficientPrivilege,
    InvalidAuthorizationSpecification,
    InvalidParameterValue,
    LockNotAvailable,
    NoDataFound,
    ProgramLimitExceeded,
    QueryCanceled,
)

from memoriesql.application.agent_sql_catalog import SqlCatalog, SqlRelation
from memoriesql.infrastructure.postgres.relation_projection import (
    relation_projection_frame,
)

RELATIONS = (
    "assessed_relations",
    "relation_statements",
    "relation_evidence",
    "relation_events",
    "relation_event_evidence",
    "relation_types",
    "relation_pairs",
    "relation_corrections",
    "relation_replacements",
)


class RelationPopulationError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class EvidenceBinding:
    statement_id: UUID
    source_unit_id: UUID
    content_sha256: str


@dataclass(frozen=True, slots=True)
class PreparedRelationPopulation:
    known_at: datetime
    snapshot_at: datetime
    catalog_hash: str
    schemas: Mapping[str, SqlRelation]
    rows: Mapping[str, tuple[tuple[Any, ...], ...]]
    evidence_bindings: Mapping[UUID, EvidenceBinding]
    dependency_records: bytes
    dependency_manifest: bytes
    dependency_manifest_sha256: str
    preparation_bytes: int
    source_context: bytes | None = None


def _time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _native(value: Any, pg_type: str, nullable: bool) -> Any:
    if value is None:
        if not nullable:
            raise RelationPopulationError("projection_mismatch")
        return None
    if pg_type == "uuid":
        return UUID(value)
    if pg_type == "timestamptz":
        return _time(value)
    if pg_type == "numeric":
        result = Decimal(value)
        if not result.is_finite():
            raise RelationPopulationError("projection_mismatch")
        return result
    if pg_type == "int8" and type(value) is int and -(2**63) <= value < 2**63:
        return value
    if pg_type == "bool" and type(value) is bool:
        return value
    if pg_type == "text" and type(value) is str:
        return value
    raise RelationPopulationError("projection_mismatch")


def _prepare(data: str, byte_budget: int) -> PreparedRelationPopulation:
    raw = json.loads(data, parse_float=Decimal)
    catalog = SqlCatalog.installed()
    rows: dict[str, list[dict[str, Any]]] = {name: [] for name in RELATIONS}
    records = raw["dependency_records_json"].encode("utf-8")
    manifest = raw["dependency_manifest_json"].encode("utf-8")
    digest = hashlib.sha256(manifest).hexdigest()
    if digest != raw["frame"]["dependency_manifest_sha256"]:
        raise RelationPopulationError("projection_mismatch")
    # Event evidence receives the exact accepted pin from the canonical protected
    # records; it never borrows current observation text or assertion support.
    statements = {
        d["id"]: d["row"]
        for d in json.loads(records, parse_float=Decimal)
        if d["kind"] == "statement"
    }
    references: dict[EvidenceBinding, UUID] = {}

    def evidence_ref(item: dict[str, Any], hash_field: str) -> UUID:
        binding = EvidenceBinding(
            UUID(item["statement_id"]), UUID(item["source_unit_id"]), item[hash_field]
        )
        return references.setdefault(binding, uuid4())

    for assertion in raw["assertions"]:
        if assertion["kind"] != "assessed" or assertion["acceptance"] != "accepted":
            raise RelationPopulationError("projection_mismatch")
        schema = catalog.relations["memory_v1.assessed_relations"]
        main = {
            c.name: assertion[c.name]
            for c in schema.columns
            if c.name not in {"type_key", "type_revision"}
        }
        main.update(
            type_key=assertion["relation_type"]["key"],
            type_revision=assertion["relation_type"]["revision"],
        )
        rows["assessed_relations"].append(main)
        rid = assertion["relation_id"]
        for role in ("source", "target", "basis"):
            rows["relation_statements"].extend(
                {"relation_id": rid, "role": role, **pin}
                for pin in assertion[role + "_statements"]
            )
        for item in assertion["evidence"]:
            rows["relation_evidence"].append(
                {
                    "relation_id": rid,
                    "statement_id": item["statement_id"],
                    "source_unit_id": item["source_unit_id"],
                    "content_sha256": item["content_sha256"],
                    "evidence_ref": str(evidence_ref(item, "content_sha256")),
                    "roots_status": item["roots_status"],
                }
            )
        for event in assertion["events"]:
            schema = catalog.relations["memory_v1.relation_events"]
            projected = {
                c.name: event[c.name]
                for c in schema.columns
                if c.name not in {"relation_id", "related_relation_id"}
            }
            projected.update(
                relation_id=event["target_id"], related_relation_id=event["related_id"]
            )
            rows["relation_events"].append(projected)
            for item in event["evidence"]:
                pin = statements[item["statement_id"]]
                rows["relation_event_evidence"].append(
                    {
                        "event_id": event["event_id"],
                        "statement_id": item["statement_id"],
                        "source_unit_id": item["source_unit_id"],
                        "bead_id": pin["bead_id"],
                        "bead_version_id": pin["bead_version_id"],
                        "statement_text": pin["text"],
                        "content_sha256": item["content_hash"],
                        "evidence_ref": str(evidence_ref(item, "content_hash")),
                    }
                )
        rows["relation_replacements"].extend(
            {"relation_id": rid, "replacement_relation_id": replacement}
            for replacement in assertion["superseded_by"]
        )
    for definition in raw["types"]:
        rows["relation_types"].append(
            {"type_key": definition["key"], "type_revision": definition["revision"]}
            | {k: v for k, v in definition.items() if k not in {"key", "revision"}}
        )
    rows["relation_pairs"] = raw["pairs"]
    for correction in raw["corrections"]:
        rows["relation_corrections"].extend(
            {
                "relation_id": correction["relation_id"],
                "role": pin["role"],
                "correcting_bead_id": pin["successor_bead_id"],
                "correcting_bead_version_id": pin["successor_bead_version_id"],
            }
            for pin in correction["pins"]
        )
    schemas = {
        "memory_v1." + name: catalog.relations["memory_v1." + name]
        for name in RELATIONS
    }
    native: dict[str, tuple[tuple[Any, ...], ...]] = {}
    for name, items in rows.items():
        schema = schemas["memory_v1." + name]
        expected = {c.name for c in schema.columns}
        unique: dict[tuple[Any, ...], tuple[Any, ...]] = {}
        for item in items:
            if set(item) != expected:
                raise RelationPopulationError("projection_mismatch")
            row = tuple(
                _native(item[c.name], c.type.pg_type, c.type.nullable)
                for c in schema.columns
            )
            key = tuple(
                row[next(i for i, c in enumerate(schema.columns) if c.name == k)]
                for k in schema.unique_keys[0]
            )
            if key in unique and unique[key] != row:
                raise RelationPopulationError("projection_mismatch")
            unique[key] = row
        native["memory_v1." + name] = tuple(unique.values())
    # Explicit conservative encoded preparation accounting, NOT heap/index/WAL
    # or Python RSS. Includes bindings and typed-row transport before handoff.
    size = len(data.encode("utf-8")) + len(manifest) + 8192 + len(references) * 512
    if size > byte_budget:
        raise RelationPopulationError("budget_exhausted")
    frame = raw["frame"]
    return PreparedRelationPopulation(
        _time(frame["known_at"]),
        _time(frame["snapshot_at"]),
        catalog.hash,
        MappingProxyType(schemas),
        MappingProxyType(native),
        MappingProxyType({ref: binding for binding, ref in references.items()}),
        records,
        manifest,
        digest,
        size,
    )


@contextmanager
def prepare_relation_population(
    connection: Connection[Any],
    *,
    credential_sha256: str,
    workspace_id: UUID,
    known_at: datetime | None,
    byte_budget: int,
) -> Iterator[PreparedRelationPopulation]:
    """Own one canonical frame; pass only a fully prepared buffer to trusted code.

    Newly minted evidence refs are private preparation bindings. They have no
    hydration capability until an executor persists them with the immutable
    result, current authorization and an explicit disclosure context.
    """
    if (
        type(byte_budget) is not int
        or not 8192 <= byte_budget <= 64 * 1024 * 1024
        or (
            known_at is not None
            and (not isinstance(known_at, datetime) or known_at.utcoffset() is None)
        )
    ):
        raise RelationPopulationError("invalid_request")
    try:
        with relation_projection_frame(
            connection, credential_sha256=credential_sha256, workspace_id=workspace_id
        ) as frame:
            fetched = frame.execute(
                "SELECT memoriesql.prepare_relation_sql_population_v1(%s,%s)::text",
                (known_at, byte_budget),
            ).fetchone()
            if fetched is None or fetched[0] is None:
                raise RelationPopulationError("unavailable")
            population = _prepare(fetched[0], byte_budget)
            context = frame.execute(
                "SELECT to_jsonb(c)::text FROM memoriesql.current_authorization_context() c"
            ).fetchone()
            if context is None:
                raise RelationPopulationError("unavailable")
            population = replace(population, source_context=context[0].encode("utf-8"))
            frame.execute(
                "SELECT memoriesql.check_relation_sql_population_authority_v1()"
            )
            yield population
    except (InsufficientPrivilege, InvalidAuthorizationSpecification, NoDataFound):
        raise RelationPopulationError("unavailable") from None
    except (QueryCanceled, LockNotAvailable, ProgramLimitExceeded):
        raise RelationPopulationError("budget_exhausted") from None
    except InvalidParameterValue:
        raise RelationPopulationError("invalid_request") from None
