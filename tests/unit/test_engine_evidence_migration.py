"""DB-free pins for binding Core Memory events to accepted evidence (0061); no
SQL execution proof.

The runtime proof is EngineEventCitationRuntime in
tests/runtime/test_agent_source_text_runtime.py.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import ValidationError

from memoriesql.application.engine_intake import (
    BindEngineEventEvidence,
    EngineChunkEvidence,
    EngineChunkSpan,
)

ROOT = Path(__file__).resolve().parents[2]
SQL = (ROOT / "migrations/0061_engine_event_evidence.sql").read_text()
MAX_OFFSET = 16 * 1024 * 1024
COLUMNS = {
    "engine_event_evidence": [
        "tenant_id",
        "workspace_id",
        "access_scope_id",
        "source_object_id",
        "engine_workspace",
        "engine_event_id",
        "event_sha256",
        "event_id",
        "source_unit_id",
    ],
    "engine_chunk_evidence": [
        "tenant_id",
        "source_object_id",
        "engine_workspace",
        "engine_event_id",
        "chunk_id",
        "ordinal",
        "span",
    ],
}
FUNCTIONS = (
    "engine_evidence_source_v1",
    "bind_engine_event_evidence_v1",
    "resolve_engine_chunk_evidence_v1",
)
REFUSALS = {
    ("engine evidence is unavailable", "42501"),
    ("engine evidence requires read committed", "25001"),
    ("invalid engine event evidence", "22023"),
    ("engine event evidence does not match normalized source", "22023"),
    ("invalid engine chunk span", "22023"),
    ("engine chunk span does not match normalized source", "22023"),
    ("invalid UTF-8 engine chunk span", "22023"),
    ("incomplete engine event evidence", "22023"),
    ("engine event evidence conflict", "23505"),
}


def chunk(**changes: Any) -> dict[str, Any]:
    return {
        "chunk_id": UUID(int=5),
        "byte_start": 0,
        "byte_end": 2,
        "char_start": 0,
        "char_end": 1,
        "chunk_sha256": "b" * 64,
    } | changes


def binding(**changes: Any) -> dict[str, Any]:
    return {
        "source_object_id": UUID(int=1),
        "engine_workspace": "w1",
        "engine_event_id": UUID(int=2),
        "event_sha256": "a" * 64,
        "source_unit_id": UUID(int=3),
        "statement_id": UUID(int=4),
        "chunks": (chunk(),),
    } | changes


class EngineEvidenceContracts(unittest.TestCase):
    def test_request_bounds_are_refused_before_any_database_call(self) -> None:
        refused = {
            "no chunks": binding(chunks=()),
            "257 chunks": binding(chunks=(chunk(),) * 257),
            "unknown field": binding(contract_version=1),
            "unknown chunk field": binding(chunks=(chunk(text="pear"),)),
            "negative offset": binding(chunks=(chunk(byte_start=-1),)),
            "byte offset above 16 MiB": binding(
                chunks=(chunk(byte_end=MAX_OFFSET + 1),)
            ),
            "character offset above 16 MiB": binding(
                chunks=(chunk(char_end=MAX_OFFSET + 1),)
            ),
            "boolean offset": binding(chunks=(chunk(byte_start=True),)),
            "uppercase digest": binding(event_sha256="A" * 64),
            "63-character digest": binding(chunks=(chunk(chunk_sha256="b" * 63),)),
            "blank workspace": binding(engine_workspace=" "),
            "129-character workspace": binding(engine_workspace="w" * 129),
        }
        for name, request in refused.items():
            with self.subTest(name), self.assertRaises(ValidationError):
                BindEngineEventEvidence.model_validate(request)
        accepted = {
            "one chunk": binding(),
            "256 chunks": binding(chunks=(chunk(),) * 256),
            "offsets of exactly 16 MiB": binding(
                chunks=(chunk(byte_end=MAX_OFFSET, char_end=MAX_OFFSET),)
            ),
            "128-character workspace": binding(engine_workspace="w" * 128),
        }
        for name, request in accepted.items():
            with self.subTest(name):
                BindEngineEventEvidence.model_validate(request)

    def test_resolved_evidence_has_exactly_the_nine_keys(self) -> None:
        self.assertEqual(
            set(EngineChunkEvidence.model_fields),
            set(EngineChunkSpan.model_fields)
            | {"event_id", "source_unit_id", "content_sha256"},
        )
        self.assertEqual(len(EngineChunkEvidence.model_fields), 9)
        resolved = chunk(
            event_id=UUID(int=6), source_unit_id=UUID(int=3), content_sha256="c" * 64
        )
        EngineChunkEvidence.model_validate(resolved)
        with self.assertRaises(ValidationError):
            EngineChunkEvidence.model_validate(resolved | {"text": "pear"})


class EngineEvidenceMigration(unittest.TestCase):
    def test_tables_are_immutable_forced_rls_and_ungranted(self) -> None:
        created = re.findall(r"CREATE TABLE memoriesql\.(\w+) \((.*?)\n\);", SQL, re.S)
        self.assertEqual([table for table, _ in created], list(COLUMNS))
        for table, body in created:
            # Offsets and digests only: no column can hold a copy of source text.
            self.assertEqual(
                re.findall(
                    r"^    (\w+) (?:uuid|text|integer|jsonb) NOT NULL", body, re.M
                ),
                COLUMNS[table],
            )
            self.assertIn(
                f"ALTER TABLE memoriesql.{table} ENABLE ROW LEVEL SECURITY;", SQL
            )
            self.assertIn(
                f"ALTER TABLE memoriesql.{table} FORCE ROW LEVEL SECURITY;", SQL
            )
            self.assertIn(
                f"CREATE TRIGGER {table}_immutable\nBEFORE UPDATE OR DELETE "
                f"ON memoriesql.{table}\nFOR EACH ROW EXECUTE FUNCTION "
                "memoriesql.reject_immutable_change();",
                SQL,
            )
        self.assertIn(
            "REVOKE ALL ON memoriesql.engine_event_evidence, "
            "memoriesql.engine_chunk_evidence FROM PUBLIC;",
            SQL,
        )
        self.assertNotRegex(SQL, r"GRANT\b[^;]* ON memoriesql\.engine_\w+_evidence\b")

    def test_only_binding_is_granted_to_the_application_role(self) -> None:
        self.assertEqual(
            re.findall(r"^GRANT\b.*$", SQL, re.M),
            [
                "GRANT EXECUTE ON FUNCTION "
                "memoriesql.bind_engine_event_evidence_v1(jsonb) "
                "TO memoriesql_application;"
            ],
        )
        created = re.findall(r"CREATE FUNCTION memoriesql\.(\w+)\(", SQL)
        self.assertEqual(created, list(FUNCTIONS))
        for name in FUNCTIONS:
            self.assertRegex(
                SQL, rf"REVOKE ALL ON FUNCTION memoriesql\.{name}\([^;]*\) FROM PUBLIC;"
            )

    def test_refusal_messages_are_the_nine_designed_ones(self) -> None:
        raised = re.findall(r"RAISE EXCEPTION '([^']+)'\s+USING ERRCODE = '(\w+)'", SQL)
        self.assertEqual(len(raised), SQL.count("RAISE EXCEPTION"))
        self.assertEqual(set(raised), REFUSALS)
        # A refusal never carries an ID, offset, digest or stored content.
        self.assertNotIn("DETAIL", SQL)
        self.assertNotIn("HINT", SQL)

    def test_migration_changes_nothing_earlier(self) -> None:
        for forbidden in (
            "CREATE OR REPLACE",
            "ALTER FUNCTION",
            "DROP",
            "INSERT INTO memoriesql.capabilit",
            "CREATE ROLE",
        ):
            self.assertNotIn(forbidden, SQL)
