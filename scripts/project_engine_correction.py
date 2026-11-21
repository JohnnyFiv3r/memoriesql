"""Project direct correction evidence from a complete fictional Core Memory v2 export."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from typing import Any, Never
from uuid import UUID

# Local diagnostic input bound. This script is outside the product read/write APIs.
MAX_BYTES = 16 * 1024 * 1024
SCOPED_KINDS = {"event", "chunk", "chunk_read", "tool_outcome", "erasure"}
JUDGE_KINDS = {"edge", "verdict", "bead", "name_link"}
CORRECTION_TYPES = {"repl": "leadsto", "withdraws": "leadsto", "!contradicts": "near"}
type Record = dict[str, Any]


def require(condition: bool) -> None:
    if not condition:
        raise ValueError("invalid or incompatible fictional export")


def object_fields(pairs: list[tuple[str, Any]]) -> Record:
    result: Record = {}
    for key, value in pairs:
        require(key not in result)
        result[key] = value
    return result


def reject_non_json_constant(_: str) -> Never:
    raise ValueError("invalid JSON number")


def identity(record: Record, field: str) -> str:
    value = record.get(field)
    require(isinstance(value, str) and str(UUID(value)) == value)
    return str(value)


def natural(record: Record, field: str) -> int:
    value = record.get(field)
    if type(value) is not int or value < 0:
        raise ValueError("invalid count or offset")
    return value


def source_chunks(event: Record, chunks: list[Record]) -> list[dict[str, str]]:
    require(len(chunks) == natural(event, "chunk_count"))
    ordered = sorted(chunks, key=lambda chunk: natural(chunk, "ordinal"))
    result = []
    digest = hashlib.sha256()
    byte_offset = char_offset = 0
    for ordinal, chunk in enumerate(ordered):
        text = chunk.get("text")
        if not isinstance(text, str):
            raise ValueError("invalid chunk text")
        encoded = text.encode("utf-8")
        require(natural(chunk, "ordinal") == ordinal)
        require(natural(chunk, "byte_start") == byte_offset)
        require(natural(chunk, "char_start") == char_offset)
        byte_offset += len(encoded)
        char_offset += len(text)
        require(natural(chunk, "byte_end") == byte_offset)
        require(natural(chunk, "char_end") == char_offset)
        require(hashlib.sha256(encoded).hexdigest() == chunk.get("content_hash"))
        digest.update(encoded)
        result.append(
            {"event_id": event["event_id"], "chunk_id": chunk["chunk_id"], "text": text}
        )
    require(digest.hexdigest() == event.get("content_hash"))
    return result


def named_strings(row: Record, *fields: str) -> dict[str, str]:
    values = {}
    for field in fields:
        value = row.get(field)
        if not isinstance(value, str):
            raise ValueError("missing or invalid source provenance")
        values[field] = value
    return values


def event_provenance(event: Record) -> Record:
    fields: Record = named_strings(
        event, "record_key", "event_id", "captured_at", "time_at", "time_basis"
    )
    require(fields["time_basis"] in {"source", "source_bucket", "captured"})
    key = event["key"]
    fields["key"] = named_strings(key, "scope", "source_ns", "external_id")
    if "revision" in key:
        fields["key"].update(named_strings(key, "revision"))
    if "engine_assigned_revision" in event:
        require(type(event["engine_assigned_revision"]) is bool)
        fields["engine_assigned_revision"] = event["engine_assigned_revision"]
    if "occurred_at" in event:
        source_time = event["occurred_at"]
        require(isinstance(source_time, dict))
        fields["occurred_at"] = named_strings(
            source_time,
            "precision",
            *(name for name in ("at", "end", "timezone") if name in source_time),
        )
    fields["seq"] = natural(event, "seq")
    return fields


def project(
    data: bytes, *, workspace: str, scope: str, handoff: bool = False
) -> Record:
    """Check a trusted producer's scoped fixture. Never decides who may read it."""
    require(bool(workspace and scope) and len(data) <= MAX_BYTES)
    events: dict[str, Record] = {}
    chunks: dict[str, list[Record]] = defaultdict(list)
    edges: list[Record] = []
    erased: dict[str, Record] = {}
    seen: dict[str, bytes] = {}
    for line in data.split(b"\n"):
        if not line.strip():
            continue
        row = json.loads(
            line.decode("utf-8"),
            object_pairs_hook=object_fields,
            parse_constant=reject_non_json_constant,
        )
        require(
            isinstance(row, dict) and row.get("contract") == "core_memory.record.v2"
        )
        kind = row.get("record_kind")
        require(isinstance(kind, str) and kind in SCOPED_KINDS | JUDGE_KINDS)
        if kind in SCOPED_KINDS:
            require(row.get("scope") == scope)
        else:
            require("scope" not in row)
        if kind != "chunk":
            require(row.get("workspace") == workspace)
        if kind == "verdict":
            require(
                row.get("earlier_scope") == scope and row.get("later_scope") == scope
            )
        if kind == "name_link":
            require(row.get("a_scope") == scope and row.get("b_scope") == scope)

        key = row.get("record_key")
        require(isinstance(key, str) and key.startswith(f"{kind}:"))
        if key in seen:
            require(seen[key] == line)
            continue
        seen[key] = line
        if kind == "event":
            event_id = identity(row, "event_id")
            require(key == f"event:{event_id}")
            event_key = row.get("key")
            require(isinstance(event_key, dict) and event_key.get("scope") == scope)
            require(row.get("content_kind") in {"text", "json", "tool_call"})
            events[event_id] = row
        elif kind == "chunk":
            chunk_id = identity(row, "chunk_id")
            require(key == f"chunk:{chunk_id}")
            chunks[identity(row, "event_id")].append(row)
        elif kind == "erasure":
            event_id = identity(row, "event_id")
            require(key == f"erasure:{event_id}")
            erased[event_id] = row
        elif kind == "edge":
            arrow = row.get("arrow")
            start = identity(row, "from_bead")
            end = identity(row, "to_bead")
            require(isinstance(arrow, str) and key == f"edge:{arrow}:{start}:{end}")
            if arrow in CORRECTION_TYPES:
                require(
                    start != end and row.get("edge_type") == CORRECTION_TYPES[arrow]
                )
                edges.append(row)

    require(chunks.keys() <= events.keys())
    material = []
    eligible_events = []
    for event_id, event in sorted(events.items()):
        verified = source_chunks(event, chunks.get(event_id, []))
        if event_id not in erased and event["content_kind"] in {"text", "json"}:
            material.extend(verified)
            eligible_events.append(event)
    eligible_ids = {chunk["chunk_id"] for chunk in material}
    eligible_edges = [
        edge
        for edge in edges
        if edge["from_bead"] in eligible_ids and edge["to_bead"] in eligible_ids
    ]
    edge_fields = ("record_key", "arrow", "from_bead", "to_bead")
    corrections = [
        named_strings(edge, *edge_fields)
        for edge in sorted(eligible_edges, key=lambda row: row["record_key"])
    ]
    result: Record = {
        "currentness": "unassessed",
        "chunks": material,
        "corrections": corrections,
    }
    if handoff:
        ordered_events = sorted(eligible_events, key=lambda row: natural(row, "seq"))
        chunk_refs = [
            {
                k: row[k]
                for k in (
                    "record_key",
                    "event_id",
                    "chunk_id",
                    "ordinal",
                    "byte_start",
                    "byte_end",
                    "char_start",
                    "char_end",
                    "content_hash",
                )
            }
            for event in ordered_events
            for row in sorted(
                chunks.get(event["event_id"], []), key=lambda item: item["ordinal"]
            )
        ]
        positions = [
            natural(row, "seq")
            for row in (*eligible_events, *eligible_edges, *erased.values())
        ]
        require(
            len(set(positions)) == len(positions)
            and all(0 < seq < 2**64 for seq in positions)
        )
        result["handoff"] = {
            "producer_contract": "core_memory.record.v2",
            "workspace": workspace,
            "scope": scope,
            "events": [event_provenance(event) for event in ordered_events],
            "chunks": chunk_refs,
            "corrections": [
                {
                    **named_strings(edge, *edge_fields, "created_at"),
                    "seq": natural(edge, "seq"),
                }
                for edge in sorted(eligible_edges, key=lambda row: natural(row, "seq"))
            ],
            "erasures": [
                {
                    **named_strings(row, "record_key", "event_id", "erased_at"),
                    "seq": natural(row, "seq"),
                }
                for row in sorted(erased.values(), key=lambda row: natural(row, "seq"))
            ],
        }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--scope", required=True)
    parser.add_argument(
        "--handoff",
        action="store_true",
        help="include scoped source and erasure provenance",
    )
    args = parser.parse_args()
    try:
        result = project(
            sys.stdin.buffer.read(MAX_BYTES + 1),
            workspace=args.workspace,
            scope=args.scope,
            handoff=args.handoff,
        )
    except (TypeError, ValueError, RecursionError):
        print("Invalid or incompatible fictional export.", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
