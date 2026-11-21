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


def project(data: bytes, *, workspace: str, scope: str) -> Record:
    """Check a trusted producer's scoped fixture. Never decides who may read it."""
    require(bool(workspace and scope) and len(data) <= MAX_BYTES)
    events: dict[str, Record] = {}
    chunks: dict[str, list[Record]] = defaultdict(list)
    edges: list[Record] = []
    erased: set[str] = set()
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
            erased.add(event_id)
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
    for event_id, event in sorted(events.items()):
        verified = source_chunks(event, chunks.get(event_id, []))
        if event_id not in erased and event["content_kind"] in {"text", "json"}:
            material.extend(verified)
    eligible_ids = {chunk["chunk_id"] for chunk in material}
    corrections = [
        {
            field: edge[field]
            for field in ("record_key", "arrow", "from_bead", "to_bead")
        }
        for edge in sorted(edges, key=lambda edge: edge["record_key"])
        if edge["from_bead"] in eligible_ids and edge["to_bead"] in eligible_ids
    ]
    return {"currentness": "unassessed", "chunks": material, "corrections": corrections}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--scope", required=True)
    args = parser.parse_args()
    try:
        result = project(
            sys.stdin.buffer.read(MAX_BYTES + 1),
            workspace=args.workspace,
            scope=args.scope,
        )
    except (TypeError, ValueError, RecursionError):
        print("Invalid or incompatible fictional export.", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
