"""The CLI's run store reuses, rotates and forgets runs without retrying."""

from __future__ import annotations

import json
import stat
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from memoriesql.query_client import RUN_ROTATION_MARGIN, RunStore

NOW = datetime(2026, 9, 28, 20, 0, tzinfo=UTC)


class FakeTransport:
    def __init__(self, *replies: dict[str, Any]) -> None:
        self.replies = list(replies)
        self.started = 0

    def start_run(self) -> bytes:
        self.started += 1
        return json.dumps(self.replies.pop(0)).encode()

    def handle(self, request: bytes) -> bytes:  # pragma: no cover - unused here
        raise AssertionError("the run store never handles requests")


def run(expires: datetime) -> dict[str, Any]:
    return {
        "outcome": "available",
        "run": {
            "run_ref": str(uuid4()),
            "expires_at": expires.isoformat(),
            "default_known_at": NOW.isoformat(),
        },
    }


class RunStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.store = RunStore(
            self.root, credential_sha256="a" * 64, workspace_id=UUID(int=7)
        )

    def test_reuses_until_margin_then_rotates_and_honors_new_run(self) -> None:
        first, second, third = (run(NOW + timedelta(minutes=30)) for _ in range(3))
        transport = FakeTransport(first, second, third)
        current = self.store.current(transport, now=NOW)
        assert isinstance(current, dict)
        self.assertEqual(current["run_ref"], first["run"]["run_ref"])
        again = self.store.current(transport, now=NOW + timedelta(minutes=10))
        assert isinstance(again, dict)
        self.assertEqual(again["run_ref"], first["run"]["run_ref"])
        self.assertEqual(transport.started, 1)
        near_end = NOW + timedelta(minutes=30) - RUN_ROTATION_MARGIN
        rotated = self.store.current(transport, now=near_end)
        assert isinstance(rotated, dict)
        self.assertEqual(rotated["run_ref"], second["run"]["run_ref"])
        fresh = self.store.current(transport, now=near_end, fresh=True)
        assert isinstance(fresh, dict)
        self.assertEqual(fresh["run_ref"], third["run"]["run_ref"])
        (state,) = (self.root / "runs").glob("*.json")
        self.assertEqual(stat.S_IMODE(state.stat().st_mode), 0o600)
        self.assertNotIn("a" * 64, state.name)

    def test_refusal_is_returned_unchanged_and_never_stored(self) -> None:
        refusal = {
            "contract_version": 1,
            "outcome": "budget_exhausted",
            "error": {"code": "database"},
        }
        transport = FakeTransport(refusal)
        reply = self.store.current(transport, now=NOW)
        self.assertEqual(
            json.loads(reply) if isinstance(reply, bytes) else reply, refusal
        )
        self.assertEqual(list((self.root / "runs").glob("*.json")), [])

    def test_forgets_only_its_own_ended_run_after_a_refusal(self) -> None:
        stored = run(NOW + timedelta(minutes=30))
        transport = FakeTransport(stored)
        current = self.store.current(transport, now=NOW)
        assert isinstance(current, dict)
        ended = {
            "outcome": "budget_exhausted",
            "run_ref": current["run_ref"],
            "remaining": {"run_expires_at": (NOW - timedelta(seconds=1)).isoformat()},
        }
        other = ended | {"run_ref": str(uuid4())}
        available = ended | {"outcome": "available"}
        for reply in (other, available):
            self.store.forget_ended(json.dumps(reply).encode(), now=NOW)
            self.assertEqual(len(list((self.root / "runs").glob("*.json"))), 1)
        self.store.forget_ended(json.dumps(ended).encode(), now=NOW)
        self.assertEqual(list((self.root / "runs").glob("*.json")), [])
        self.store.forget_ended(b"not json", now=NOW)


if __name__ == "__main__":
    unittest.main()
