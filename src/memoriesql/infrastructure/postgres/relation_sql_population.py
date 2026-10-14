"""Trusted typed preparation from PR-03; never an agent disclosure/execution API.

The caller owns admission/accounting and must consume this within the yielded
authority fence. Persist every partition and binding before publishing a result.
This object is not permission to disclose, replay or reauthorize a saved result.
"""

from __future__ import annotations

import hashlib
import json
import re
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
# Population revision 2 adds the accepted-observation family in the same frame.
# Entity, alias, mention and topic relations remain unprepared (unsupported).
OBSERVATION_RELATIONS = (
    "observations",
    "statements",
    "statement_sources",
    "source_units",
    "corrections",
)
_ENUMS: dict[tuple[str, str], frozenset[str]] = {
    ("observations", "render_state"): frozenset({"present", "unsupported"}),
    ("observations", "effective_basis"): frozenset({"authored", "source", "unknown"}),
    ("observations", "correction_state"): frozenset(
        {"unsuperseded", "superseded", "branched"}
    ),
    ("statements", "kind"): frozenset(
        {"observation", "context", "qualification", "correction"}
    ),
    ("source_units", "source_kind"): frozenset(
        {"transcript", "document", "media", "relational", "operational"}
    ),
    ("source_units", "text_state"): frozenset({"available", "unsupported"}),
    ("source_units", "source_precision"): frozenset(
        {
            "instant",
            "second",
            "minute",
            "hour",
            "day",
            "month",
            "year",
            "interval",
            "unknown",
        }
    ),
}
_HASHES = frozenset(
    {("statement_sources", "content_sha256"), ("source_units", "content_sha256")}
)


class RelationPopulationError(ValueError):
    def __init__(self, code: str, *, limit: str | None = None) -> None:
        self.code = code
        # Which budget a `budget_exhausted` population ran out of: `time` or
        # `storage`.
        self.limit = limit
        super().__init__(code)


_TEXT_LABEL = re.compile(
    r"(normalized_projection|package_exclusion|package_unresolved):[^\x00-\x1f]{1,256}"
)


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
    # Revision 1 is the nine assessed relations; revision 2 is all fourteen
    # prepared relations, with the observation view recorded in the frame.
    revision: int = 1
    view: str | None = None
    relation_manifest_sha256: str | None = None
    # The caller's own capabilities, disclosed as coverage gaps when missing.
    relation_raw_authority: bool = True
    source_read_authority: bool = True
    # Labels of served package text: normalized projection versions and the
    # packages' declared coverage limits, disclosed wherever units are used.
    source_text_labels: tuple[str, ...] = ()
    # The relation read mode the caller's authority selected (M0039): "owner"
    # with raw source authority, "agent" under AM-5. None means a database
    # before migration 0039, where relations still need raw source authority.
    relation_read_mode: str | None = None
    # The caller-visible frame digests (owner decision 6): built only from
    # records the caller may see, so they never change when inaccessible
    # history changes. The full manifest above stays internal, for binding and
    # invalidation. None only for populations never committed as a result.
    visible_manifest_sha256: str | None = None
    visible_relation_manifest_sha256: str | None = None


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


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _plain(value: Any) -> Any:
    if isinstance(value, UUID | Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _visible_manifest_sha256(
    schemas: Mapping[str, SqlRelation],
    native: Mapping[str, tuple[tuple[Any, ...], ...]],
    names: tuple[str, ...],
) -> str:
    """The digest of the rows a caller actually receives (owner decision 6).

    One entry per distinct disclosed row: its relation, its unique key and the
    hash of its values, sorted. Private evidence references are handles minted
    for each preparation, not disclosed content, so they are left out.
    """
    entries: set[tuple[str, str, str]] = set()
    for name in names:
        relation = "memory_v1." + name
        columns = [c.name for c in schemas[relation].columns]
        keyed = [
            columns.index(k)
            for k in schemas[relation].unique_keys[0]
            if k != "evidence_ref"
        ]
        for row in native[relation]:
            values = {
                c: _plain(v) for c, v in zip(columns, row, strict=True) if c != "evidence_ref"
            }
            entries.add(
                (
                    relation,
                    _canonical([_plain(row[i]) for i in keyed]),
                    hashlib.sha256(_canonical(values).encode("ascii")).hexdigest(),
                )
            )
    manifest = [
        {"kind": kind, "id": key, "content_sha256": content}
        for kind, key, content in sorted(entries)
    ]
    return hashlib.sha256(_canonical(manifest).encode("ascii")).hexdigest()


def _visible_digests(
    *,
    revision: int,
    raw_authority: bool,
    read_mode: str | None,
    digest: str,
    relation_manifest: str | None,
    schemas: Mapping[str, SqlRelation],
    native: Mapping[str, tuple[tuple[Any, ...], ...]],
    names: tuple[str, ...],
) -> tuple[str, str | None]:
    """The caller-visible frame digests for one population.

    A raw-read holder may see every record its own population holds, so its
    visible digests are the full ones, byte for byte. Any other caller, an AM-5
    agent or a caller without raw authority, gets digests of the rows it
    receives, never of records it cannot see. A revision-1 population is
    relation-only and reaches no reply (the query service prepares revision 2),
    so it keeps the full ones too.
    """
    if revision == 1 or (raw_authority and read_mode in {None, "owner"}):
        return digest, relation_manifest
    return (
        _visible_manifest_sha256(schemas, native, names),
        _visible_manifest_sha256(schemas, native, RELATIONS),
    )


def _prepare(
    data: str, byte_budget: int, *, revision: int = 1
) -> PreparedRelationPopulation:
    raw = json.loads(data, parse_float=Decimal)
    catalog = SqlCatalog.installed()
    names = RELATIONS if revision == 1 else RELATIONS + OBSERVATION_RELATIONS
    view: str | None = None
    relation_manifest: str | None = None
    raw_authority = source_authority = True
    labels: tuple[str, ...] = ()
    read_mode: str | None = None
    if revision == 2:
        view = raw["frame"].get("view")
        relation_manifest = raw["frame"].get("relation_manifest_sha256")
        raw_authority = raw["frame"].get("relation_raw_authority")
        source_authority = raw["frame"].get("source_read_authority")
        read_mode = raw["frame"].get("relation_read_mode")
        served = raw["frame"].get("source_text_labels")
        if (
            raw.get("population_revision") != 2
            or view not in {"resolved", "historical"}
            or type(relation_manifest) is not str
            or len(relation_manifest) != 64
            or type(raw_authority) is not bool
            or type(source_authority) is not bool
            # The mode is selected by raw source authority, never independently.
            or read_mode not in {None, "owner", "agent"}
            or (read_mode is not None and (read_mode == "owner") != raw_authority)
            or type(served) is not list
            or not all(
                type(label) is str and _TEXT_LABEL.fullmatch(label) for label in served
            )
            or served != sorted(set(served))
        ):
            raise RelationPopulationError("projection_mismatch")
        labels = tuple(served)
    elif revision != 1 or "population_revision" in raw:
        raise RelationPopulationError("projection_mismatch")
    rows: dict[str, list[dict[str, Any]]] = {name: [] for name in names}
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
    if revision == 2:
        # Accepted-observation family rows are copied exactly; the same evidence
        # binding shares one private evidence_ref with relation evidence rows.
        rows["observations"] = list(raw["observations"])
        rows["statements"] = list(raw["statements"])
        rows["statement_sources"] = [
            item | {"evidence_ref": str(evidence_ref(item, "content_sha256"))}
            for item in raw["statement_sources"]
        ]
        rows["source_units"] = list(raw["source_units"])
        rows["corrections"] = list(raw["observation_corrections"])
    schemas = {
        "memory_v1." + name: catalog.relations["memory_v1." + name] for name in names
    }
    native: dict[str, tuple[tuple[Any, ...], ...]] = {}
    for name, items in rows.items():
        schema = schemas["memory_v1." + name]
        expected = {c.name for c in schema.columns}
        unique: dict[tuple[Any, ...], tuple[Any, ...]] = {}
        for item in items:
            if set(item) != expected:
                raise RelationPopulationError("projection_mismatch")
            for column, value in item.items():
                allowed = _ENUMS.get((name, column))
                if allowed is not None and value not in allowed:
                    raise RelationPopulationError("projection_mismatch")
                if (name, column) in _HASHES and (
                    type(value) is not str
                    or len(value) != 64
                    or any(ch not in "0123456789abcdef" for ch in value)
                ):
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
        raise RelationPopulationError("budget_exhausted", limit="storage")
    visible, visible_relations = _visible_digests(
        revision=revision,
        raw_authority=raw_authority,
        read_mode=read_mode,
        digest=digest,
        relation_manifest=relation_manifest,
        schemas=schemas,
        native=native,
        names=names,
    )
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
        revision=revision,
        view=view,
        relation_manifest_sha256=relation_manifest,
        relation_raw_authority=raw_authority,
        source_read_authority=source_authority,
        source_text_labels=labels,
        relation_read_mode=read_mode,
        visible_manifest_sha256=visible,
        visible_relation_manifest_sha256=visible_relations,
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
    with _population_frame(
        connection,
        credential_sha256=credential_sha256,
        workspace_id=workspace_id,
        statement="SELECT memoriesql.prepare_relation_sql_population_v1(%s,%s)::text",
        values=(known_at, byte_budget),
        byte_budget=byte_budget,
        revision=1,
    ) as population:
        yield population


@contextmanager
def prepare_query_population(
    connection: Connection[Any],
    *,
    credential_sha256: str,
    workspace_id: UUID,
    known_at: datetime | None,
    view: str,
    byte_budget: int,
    statement_timeout_ms: int = 2500,
) -> Iterator[PreparedRelationPopulation]:
    """All fourteen prepared relations in one frame (population revision 2).

    Observation families are withheld whole when any record or visible
    correction neighbour is unreadable; `view` affects only `observations`.
    """
    if (
        type(byte_budget) is not int
        or not 8192 <= byte_budget <= 64 * 1024 * 1024
        or view not in {"resolved", "historical"}
        or (
            known_at is not None
            and (not isinstance(known_at, datetime) or known_at.utcoffset() is None)
        )
    ):
        raise RelationPopulationError("invalid_request")
    with _population_frame(
        connection,
        credential_sha256=credential_sha256,
        workspace_id=workspace_id,
        statement="SELECT memoriesql.prepare_query_sql_population_v2(%s,%s,%s)::text",
        values=(known_at, view, byte_budget),
        byte_budget=byte_budget,
        revision=2,
        statement_timeout_ms=statement_timeout_ms,
    ) as population:
        yield population


@contextmanager
def _population_frame(
    connection: Connection[Any],
    *,
    credential_sha256: str,
    workspace_id: UUID,
    statement: str,
    values: tuple[Any, ...],
    byte_budget: int,
    revision: int,
    statement_timeout_ms: int = 2500,
) -> Iterator[PreparedRelationPopulation]:
    try:
        with relation_projection_frame(
            connection,
            credential_sha256=credential_sha256,
            workspace_id=workspace_id,
            statement_timeout_ms=statement_timeout_ms,
        ) as frame:
            fetched = frame.execute(statement, values).fetchone()
            if fetched is None or fetched[0] is None:
                raise RelationPopulationError("unavailable")
            population = _prepare(fetched[0], byte_budget, revision=revision)
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
    except (QueryCanceled, LockNotAvailable):
        raise RelationPopulationError("budget_exhausted", limit="time") from None
    except ProgramLimitExceeded:
        raise RelationPopulationError("budget_exhausted", limit="storage") from None
    except InvalidParameterValue:
        raise RelationPopulationError("invalid_request") from None
