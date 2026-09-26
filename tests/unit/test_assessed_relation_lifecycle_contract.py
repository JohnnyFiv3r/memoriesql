"""Exact approved boundaries of the new contract; prior payloads remain unchanged."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from memoriesql.application.assessed_relation_lifecycle import (
    RecordAssessedRelationEvent,
)


class Contract(unittest.TestCase):
    def test_closed_required_fields_and_canonical_request(self) -> None:
        data: dict[str, Any] = dict(
            contract_version=1,
            expected_schema_version=30,
            idempotency_key="key",
            relation_id=str(uuid4()),
            action="confirm",
            reason=" human β ",
            evidence=[
                dict(
                    statement_id=str(uuid4()),
                    source_unit_id=str(uuid4()),
                    content_hash="a" * 64,
                )
            ],
            effective_at="2026-09-26T01:30:00+01:30",
            expected_head_token="b" * 64,
        )
        command = RecordAssessedRelationEvent.model_validate(data)
        self.assertEqual(command.effective_at, datetime(2026, 9, 26, tzinfo=UTC))
        self.assertEqual(command.reason, " human β ")
        for field in data:
            with self.subTest(missing=field), self.assertRaises(ValidationError):
                RecordAssessedRelationEvent.model_validate(
                    {k: v for k, v in data.items() if k != field}
                )
        changes: list[dict[str, Any]] = [
            dict(extra=1),
            dict(reason=" "),
            dict(idempotency_key=" "),
            dict(evidence=[]),
            dict(effective_at="2026-09-26T00:00:00"),
            dict(effective_at="2026-09-26T00:00:00.0000001Z"),
            dict(expected_head_token=None),
            dict(evidence=data["evidence"] * 2),
        ]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValidationError):
                RecordAssessedRelationEvent.model_validate(data | change)
