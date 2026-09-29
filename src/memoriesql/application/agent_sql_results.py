"""Closed PR-05 query/reuse reply construction; no execution or authority.

This module derives wire metadata that the trusted executor seals into an
immutable result (frame, coverage, order basis) and assembles closed replies.
It grants nothing: authorization, admission and disclosure receipts are the
trusted Postgres executor's responsibility, established before any reply.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

from sqlglot import exp

from memoriesql.application.investigation_contracts import result_json_bytes

CONTRACT_ID = "memoriesql.agent-sql-results.v1"
CONTRACT_VERSION = 1

# The owner-approved baseline qualification targets (packet §5), not measured
# capacity. The database pins the same hash and enforces these counters.
BASELINE_POLICY: Mapping[str, Any] = {
    "contract": CONTRACT_ID,
    "policy": "baseline-qualification-target-v1",
    "run": {
        "lifetime_seconds": 1800,
        "accesses": 128,
        "db_ms": 300000,
        "transport_bytes": 16777216,
        "diagnostic_reserve_bytes": 16384,
        "allocation_bytes": 134217728,
    },
    "operation": {"db_ms": 30000, "lock_timeout_ms": 500},
    "workspace": {
        "active_runs": 2,
        "executing_operations": 1,
        "rolling_window_hours": 24,
        "rolling_db_ms": 1800000,
        "rolling_transport_bytes": 268435456,
        "retained_allocation_bytes": 536870912,
    },
    "result": {"allocation_bytes": 67108864, "standalone_retention_hours": 720},
    "delivery": {
        "default_page_rows": 20,
        "max_page_rows": 50,
        "max_response_bytes": 262144,
    },
    "query_settings": {
        "work_mem": "4MB",
        "hash_mem_multiplier": 1,
        "temp_file_limit": "64MB",
        "parallel_workers": 0,
        "jit": "off",
    },
}
POLICY_HASH = hashlib.sha256(result_json_bytes(dict(BASELINE_POLICY))).hexdigest()

# Charged retained bytes are M0031's encoded partitions plus fixed control and
# hold charges. This is a declared charge profile, NOT measured physical bytes.
ALLOCATION_PROFILE: Mapping[str, Any] = {
    "profile": "memoriesql-preparation-charge-v1",
    "encoded_partitions": True,
    "control_bytes": 8192,
    "parent_hold_bytes": 512,
    "physical_measurement": "unavailable",
}
ALLOCATION_PROFILE_HASH = hashlib.sha256(
    result_json_bytes(dict(ALLOCATION_PROFILE))
).hexdigest()

RELATION_TABLES = frozenset(
    "memory_v1." + name
    for name in (
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
)
OBSERVATION_TABLES = frozenset(
    "memory_v1." + name
    for name in (
        "observations",
        "statements",
        "statement_sources",
        "source_units",
        "corrections",
    )
)
# Only these relations are executable; the rest of the approved catalog stays
# explicitly unsupported (never an empty substitute) until prepared.
PREPARED_RELATIONS = RELATION_TABLES | OBSERVATION_TABLES
# AM-5 (docs/approvals/pr-05-agent-relation-reads.md): an agent holding
# memory.query and source.read over a relation's whole disclosed dependency
# closure reads it in these six tables. Lifecycle history and pair coverage stay
# owner-only and are disclosed as a coverage gap, never as silent absence.
RELATION_HISTORY_TABLES = frozenset(
    "memory_v1." + name
    for name in ("relation_events", "relation_event_evidence", "relation_pairs")
)
AGENT_RELATION_TABLES = RELATION_TABLES - RELATION_HISTORY_TABLES
RELATION_HISTORY_FACET = "relation_history"

# Facets of the source-unit projection that are always null because no
# canonical fact is represented yet; disclosed as coverage gaps when used.
UNSUPPORTED_SOURCE_FACETS = (
    "source_units.occurrence_ref",
    "source_units.trust_label",
)
# Unit text is retained or normalized projection text, never exact source bytes.
SEARCH_TEXT_FACET = "source_units.search_text"

MAX_RESPONSE_BYTES = 262144
MAX_PAGE_ROWS = 50


def wire_frame(
    *,
    frame_ref: str,
    known_at: str,
    snapshot_at: str,
    view: str,
    snapshot_digest: str,
    relation_manifest_sha256: str | None,
    relations: Sequence[str],
) -> dict[str, Any]:
    """Frame lifecycle fields are null unless a relation projection was used."""
    uses_relations = bool(set(relations) & RELATION_TABLES)
    return {
        "frame_ref": frame_ref,
        "known_at": known_at,
        "snapshot_at": snapshot_at,
        "snapshot_digest": snapshot_digest,
        "view": view,
        # Per-source capture watermarks are not computed by this executor;
        # coverage below reports source capture as unknown rather than complete.
        "source_watermarks": [],
        "lifecycle_projection_version": 1 if uses_relations else None,
        "projection_manifest_sha256": (
            relation_manifest_sha256 if uses_relations else None
        ),
    }


def wire_coverage(
    *,
    relations: Sequence[str],
    limited: bool,
    recursion: bool,
    relation_raw_authority: bool = True,
    source_read_authority: bool = True,
    source_text_labels: Sequence[str] = (),
    relation_read_mode: str | None = None,
) -> dict[str, Any]:
    """Complete query execution never proves source coverage or absence.

    Missing caller capabilities are gaps, not absence: without source.read the
    observation families and (in the AM-5 agent read mode) the assessed
    relations are withheld, and an agent's relation history and pair coverage
    stay owner-only. A frame without a read mode comes from a database before
    migration 0039, where relations still need raw source authority. The flags
    describe only the caller's own grants, never whether protected data exists.
    Served package text is labelled with its normalized projection version and
    the package's own declared coverage limits.
    """
    gaps = (
        [
            {"facet": facet, "reason": "unsupported"}
            for facet in UNSUPPORTED_SOURCE_FACETS
        ]
        + [{"facet": SEARCH_TEXT_FACET, "reason": "normalized_text_not_exact_source"}]
        + [
            {"facet": SEARCH_TEXT_FACET, "reason": label}
            for label in source_text_labels
        ]
        if "memory_v1.source_units" in relations
        else []
    )
    used = set(relations)
    if relation_read_mode == "agent":
        if not source_read_authority and used & RELATION_TABLES:
            gaps.append({"facet": "relation_tables", "reason": "source_read_required"})
        # History stays owner-only whatever else the agent holds.
        if used & RELATION_HISTORY_TABLES:
            gaps.append({"facet": RELATION_HISTORY_FACET, "reason": "owner_only"})
    elif (
        relation_read_mode is None
        and not relation_raw_authority
        and used & RELATION_TABLES
    ):
        gaps.append({"facet": "relation_tables", "reason": "source_raw_read_required"})
    if not source_read_authority and set(relations) & OBSERVATION_TABLES:
        gaps.append({"facet": "observation_tables", "reason": "source_read_required"})
    return {
        "query_result": "complete",
        "population_basis": "limited_query" if limited else "authorized_logical_scope",
        "source_capture": "unknown",
        "authored": "unknown",
        "search_ready": "unknown",
        "discovered": "unknown",
        "gaps": gaps,
        "truncation": (
            "explicit_limit" if limited else "explicit_depth" if recursion else None
        ),
    }


def order_basis(tree: Any, output_names: Sequence[str]) -> dict[str, Any]:
    """Outer ORDER BY keys that are projected columns, then sealed witness order.

    Keys after the first non-projected sort expression only order its ties, so
    they are not reported; the sealed witness ordinal decides remaining ties.
    """
    node = exp.Expression.load(tree)
    columns: list[dict[str, str]] = []
    order = node.args.get("order")
    names = set(output_names)
    # A qualified key names an output column only when that exact qualified
    # column is itself projected (directly or under an alias).
    projected: dict[tuple[str, str], str] = {}
    if isinstance(node, exp.Select):
        for projection in node.expressions:
            source = (
                projection.this if isinstance(projection, exp.Alias) else projection
            )
            if isinstance(source, exp.Column) and source.table:
                projected.setdefault(
                    (source.table, source.name), projection.alias_or_name
                )
    for item in order.expressions if order else ():
        key = item.this
        if not isinstance(key, exp.Column):
            break
        name = projected.get((key.table, key.name)) if key.table else key.name
        if name is None or name not in names:
            break
        columns.append(
            {
                "column": name,
                "direction": "desc" if item.args.get("desc") else "asc",
                "nulls": "first" if item.args.get("nulls_first") else "last",
            }
        )
    return {"columns": columns, "tie_basis": "witness_ordinal"}


class EvidenceIndex:
    """Exact accepted pins from a result's own sealed population.

    A delivered typed identifier becomes a visible evidence ref only when the
    result's protected population binds it; nothing is looked up live.
    """

    def __init__(
        self, logical_rows: Mapping[str, Any], bindings: Mapping[str, Any] | None
    ) -> None:
        from memoriesql.application.agent_sql_catalog import SqlCatalog

        catalog = SqlCatalog.installed()
        self.statement_versions: dict[str, str] = {}
        self.bead_versions: dict[str, set[str]] = {}
        self.version_beads: dict[str, str] = {}

        def rows(name: str) -> list[dict[str, Any]]:
            schema = catalog.relations[name]
            return [
                {c.name: value for c, value in zip(schema.columns, row, strict=True)}
                for row in logical_rows.get(name) or ()
            ]

        for row in rows("memory_v1.statements") + rows("memory_v1.relation_statements"):
            self.statement_versions[row["statement_id"]] = row["bead_version_id"]
            self._bead(row["bead_id"], row["bead_version_id"])
        for row in rows("memory_v1.observations"):
            self._bead(row["bead_id"], row["bead_version_id"])
        for row in rows("memory_v1.assessed_relations"):
            self._bead(row["source_bead_id"], row["source_bead_version_id"])
            self._bead(row["target_bead_id"], row["target_bead_version_id"])
        self.sources: dict[str, dict[str, str]] = {
            ref: {
                "unit_ref": pin["source_unit_id"],
                "content_sha256": pin["content_sha256"],
            }
            for ref, pin in (bindings or {}).items()
        }

    def _bead(self, bead: str, version: str) -> None:
        self.bead_versions.setdefault(bead, set()).add(version)
        self.version_beads[version] = bead

    def refs(
        self, columns: Sequence[Mapping[str, Any]], values: Sequence[Any]
    ) -> list[dict[str, Any]]:
        found: dict[bytes, dict[str, Any]] = {}
        for column, value in zip(columns, values, strict=True):
            kind = column.get("ref_type")
            if value is None or kind is None:
                continue
            ref: dict[str, Any] | None = None
            if kind == "statement_ref" and value in self.statement_versions:
                ref = {
                    "kind": "statement",
                    "ref": value,
                    "version_ref": self.statement_versions[value],
                }
            elif kind == "bead_ref" and len(self.bead_versions.get(value, ())) == 1:
                (version,) = self.bead_versions[value]
                ref = {"kind": "observation", "ref": value, "version_ref": version}
            elif kind == "bead_version_ref" and value in self.version_beads:
                ref = {
                    "kind": "observation",
                    "ref": self.version_beads[value],
                    "version_ref": value,
                }
            elif kind == "evidence_ref_ref" and value in self.sources:
                ref = {"kind": "source", "ref": value, **self.sources[value]}
            if ref is not None:
                found[result_json_bytes(ref)] = ref
        return [found[key] for key in sorted(found)]


def ref_key(ref: Mapping[str, Any]) -> str:
    return result_json_bytes(dict(ref)).decode("ascii")


def reply(
    *,
    run_ref: str | None,
    step_key: str | None,
    outcome: str,
    receipt_ref: str | None,
    access_receipt_ref: str | None,
    remaining: Mapping[str, Any] | None,
    error: Mapping[str, Any] | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The common closed envelope; non-available replies carry no facts."""
    value: dict[str, Any] = {
        "contract_version": CONTRACT_VERSION,
        "run_ref": run_ref,
        "step_key": step_key,
        "outcome": outcome,
        "receipt_ref": receipt_ref,
        "access_receipt_ref": access_receipt_ref,
        "remaining": dict(remaining) if remaining is not None else None,
    }
    if error is not None:
        value["error"] = dict(error)
    if extra:
        if outcome != "available":
            raise ValueError("only available replies carry result content")
        value.update(extra)
    return value


def stable_reply_bytes(
    build: Any, *, charge: Mapping[str, int], remaining_before: Mapping[str, Any]
) -> tuple[bytes, int]:
    """Size a reply whose own transport charge appears inside it.

    `build(transport_bytes, remaining)` returns the reply dict. Iterate until the
    encoded length equals the charged transport (digits can grow the reply).
    """
    transport = 0
    for _ in range(8):
        remaining = remaining_after(
            remaining_before,
            db_ms=charge["db_ms"],
            transport_bytes=transport,
        )
        data = result_json_bytes(build(transport, remaining))
        if len(data) == transport:
            return data, transport
        transport = len(data)
    raise ValueError("reply size did not converge")


def remaining_after(
    before: Mapping[str, Any], *, db_ms: int, transport_bytes: int
) -> dict[str, Any]:
    return {
        "accesses": max(0, int(before["accesses"]) - 1),
        "db_ms": max(0, int(before["db_ms"]) - db_ms),
        "transport_bytes": max(0, int(before["transport_bytes"]) - transport_bytes),
        "allocation_bytes": int(before["allocation_bytes"]),
        "run_expires_at": before["run_expires_at"],
    }


def reference_uuid(value: str) -> str:
    return str(UUID(value))
