"""The CLI's run store reuses, rotates and forgets runs without retrying; only an
admission refusal for work in flight is resent, identically and boundedly."""

from __future__ import annotations

import json
import stat
import tempfile
import unittest
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from memoriesql.application.agent_sql_results import (
    AGENT_RELATION_TABLES,
    RELATION_HISTORY_TABLES,
    RELATION_TABLES,
    wire_coverage,
)
from memoriesql.query_client import (
    RETRY_DELAYS,
    RUN_ROTATION_MARGIN,
    UNAVAILABLE_REPLY,
    RunStore,
    TrustedHostTransport,
    coverage_gaps,
    handle_with_retry,
    query_request,
    schema_description,
    wire_time,
)

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


REMAINING = {
    "accesses": 120,
    "db_ms": 250000,
    "transport_bytes": 1048576,
    "allocation_bytes": 1048576,
    "run_expires_at": wire_time(NOW + timedelta(minutes=20)),
}


def refusal(
    outcome: str = "budget_exhausted",
    code: str = "database",
    *,
    receipt: str | None = None,
    remaining: dict[str, Any] | None = REMAINING,
) -> dict[str, Any]:
    reply: dict[str, Any] = {
        "contract_version": 1,
        "outcome": outcome,
        "receipt_ref": None,
        "access_receipt_ref": receipt,
        "error": {"code": code},
    }
    if remaining is not None:
        reply["remaining"] = remaining
    return reply


class ReplyTransport:
    def __init__(self, *replies: dict[str, Any]) -> None:
        self.replies = list(replies)
        self.sent: list[bytes] = []

    def start_run(self) -> bytes:  # pragma: no cover - unused here
        raise AssertionError("retries never start a run")

    def handle(self, request: bytes) -> bytes:
        self.sent.append(request)
        return json.dumps(self.replies.pop(0)).encode()


class AdmissionRetryTests(unittest.TestCase):
    AVAILABLE = {"contract_version": 1, "outcome": "available"}

    def deliver(
        self, *replies: dict[str, Any], jitter: float = 1.0
    ) -> tuple[dict[str, Any], list[bytes], list[float]]:
        transport = ReplyTransport(*replies)
        waits: list[float] = []
        reply = handle_with_retry(
            transport,
            b"identical request bytes",
            now=lambda: NOW,
            sleep=waits.append,
            jitter=lambda: jitter,
        )
        return json.loads(reply), transport.sent, waits

    def test_work_in_flight_is_resent_identically_until_admitted(self) -> None:
        reply, sent, waits = self.deliver(
            refusal(),
            refusal(code="settlement", remaining=None),
            refusal("settlement_pending", "settlement"),
            self.AVAILABLE,
        )
        self.assertEqual(reply, self.AVAILABLE)
        # The same bytes, so the same step key: never a second, different step.
        self.assertEqual(sent, [b"identical request bytes"] * 4)
        self.assertEqual(waits, list(RETRY_DELAYS[:3]))

    def test_resends_stop_after_the_bounded_schedule(self) -> None:
        busy = refusal()
        reply, sent, waits = self.deliver(*[busy] * (len(RETRY_DELAYS) + 3))
        self.assertEqual(reply, busy)
        self.assertEqual(len(sent), 1 + len(RETRY_DELAYS))
        self.assertEqual(waits, list(RETRY_DELAYS))
        self.assertLessEqual(sum(waits), 32.0)

    def test_jitter_shortens_each_wait_to_at_least_half(self) -> None:
        _, _, waits = self.deliver(refusal(), self.AVAILABLE, jitter=0.0)
        self.assertEqual(waits, [RETRY_DELAYS[0] / 2])

    def test_admitted_spent_or_other_refusals_are_final(self) -> None:
        soon = dict(REMAINING, run_expires_at=wire_time(NOW + timedelta(seconds=0.1)))
        for final in (
            refusal(receipt=str(uuid4())),
            refusal("settlement_pending", "settlement", receipt=str(uuid4())),
            refusal(code="time"),
            refusal(code="transport"),
            refusal(remaining=dict(REMAINING, accesses=0)),
            refusal(remaining=dict(REMAINING, db_ms=0)),
            refusal(remaining=soon),
            refusal(remaining={"accesses": "many"}),
            refusal("unavailable", "unavailable"),
            refusal("execution_error", "database"),
            self.AVAILABLE,
        ):
            with self.subTest(final=final):
                reply, sent, waits = self.deliver(final, self.AVAILABLE)
                self.assertEqual(reply, final)
                self.assertEqual(len(sent), 1)
                self.assertEqual(waits, [])

    def test_a_malformed_reply_is_returned_unchanged(self) -> None:
        class Raw(ReplyTransport):
            def handle(self, request: bytes) -> bytes:
                self.sent.append(request)
                return b"not json"

        transport = Raw()
        reply = handle_with_retry(transport, b"x", now=lambda: NOW, sleep=self.fail)
        self.assertEqual((reply, transport.sent), (b"not json", [b"x"]))


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


class WireFormatTests(unittest.TestCase):
    def test_known_at_is_sent_as_utc_with_a_literal_z(self) -> None:
        central = timezone(timedelta(hours=-5))
        pinned = datetime(2026, 9, 28, 16, 26, 24, 818000, tzinfo=central)
        self.assertEqual(wire_time(pinned), "2026-09-28T21:26:24.818000Z")
        stored = run(NOW + timedelta(minutes=30))["run"]
        request = json.loads(
            query_request(
                stored,
                sql="SELECT o.bead_id FROM memory_v1.observations o",
                parameters=[],
                intent="enumerate",
                view="resolved",
                known_at=pinned,
                page_size=10,
            )
        )
        self.assertEqual(request["scope"]["known_at"], "2026-09-28T21:26:24.818000Z")
        default = json.loads(
            query_request(
                stored,
                sql="SELECT o.bead_id FROM memory_v1.observations o",
                parameters=[],
                intent="enumerate",
                view="resolved",
                known_at=None,
                page_size=10,
            )
        )
        self.assertEqual(default["scope"]["known_at"], stored["default_known_at"])

    def test_coverage_gaps_are_named_and_malformed_replies_name_none(self) -> None:
        reply = {
            "outcome": "available",
            "result": {
                "coverage": {
                    "gaps": [
                        {
                            "facet": "relation_tables",
                            "reason": "source_raw_read_required",
                        }
                    ]
                }
            },
        }
        self.assertEqual(
            coverage_gaps(reply), ["relation_tables (source_raw_read_required)"]
        )
        malformed: tuple[dict[str, Any], ...] = (
            {},
            {"result": []},
            {"result": {"coverage": {"gaps": {}}}},
        )
        for payload in malformed:
            self.assertEqual(coverage_gaps(payload), [])

    def test_schema_names_the_grants_each_table_family_needs(self) -> None:
        authority = schema_description()["authority"]
        self.assertEqual(
            authority["observation_tables"]["requires"], ["memory.query", "source.read"]
        )
        self.assertIn(
            "memory_v1.observations", authority["observation_tables"]["relations"]
        )
        self.assertEqual(
            authority["relation_tables"]["requires"], ["memory.query", "source.read"]
        )
        self.assertEqual(
            authority["relation_tables"]["caller"], "eligible_paired_agent"
        )
        self.assertEqual(
            authority["relation_tables"]["gap_reason"], "source_read_required"
        )
        self.assertEqual(
            set(authority["relation_tables"]["relations"]), AGENT_RELATION_TABLES
        )
        self.assertEqual(
            set(authority["relation_history"]["relations"]), RELATION_HISTORY_TABLES
        )
        self.assertEqual(authority["relation_history"]["gap_reason"], "owner_only")
        self.assertEqual(
            authority["relation_history"]["requires"],
            ["memory.query", "source.raw.read"],
        )
        self.assertIn(
            "memory_v1.assessed_relations", authority["relation_tables"]["relations"]
        )

    def test_am5_schema_matches_actual_coverage_oracle_for_all_nine_tables(
        self,
    ) -> None:
        authority = schema_description()["authority"]
        self.assertEqual(len(AGENT_RELATION_TABLES), 6)
        self.assertEqual(len(RELATION_HISTORY_TABLES), 3)
        self.assertFalse(AGENT_RELATION_TABLES & RELATION_HISTORY_TABLES)
        self.assertEqual(
            AGENT_RELATION_TABLES | RELATION_HISTORY_TABLES, RELATION_TABLES
        )
        for table in RELATION_TABLES:
            with self.subTest(table=table):
                agent = wire_coverage(
                    relations=[table],
                    limited=False,
                    recursion=False,
                    relation_raw_authority=False,
                    source_read_authority=True,
                    relation_read_mode="agent",
                )["gaps"]
                expected = (
                    [
                        {
                            "facet": "relation_history",
                            "reason": authority["relation_history"]["gap_reason"],
                        }
                    ]
                    if table in RELATION_HISTORY_TABLES
                    else []
                )
                self.assertEqual(agent, expected)
                no_read = wire_coverage(
                    relations=[table],
                    limited=False,
                    recursion=False,
                    relation_raw_authority=False,
                    source_read_authority=False,
                    relation_read_mode="agent",
                )["gaps"]
                self.assertIn(
                    {
                        "facet": "relation_tables",
                        "reason": authority["relation_tables"]["gap_reason"],
                    },
                    no_read,
                )
                # source.read alone is not the paired-agent read mode.
                other = wire_coverage(
                    relations=[table],
                    limited=False,
                    recursion=False,
                    relation_raw_authority=False,
                    source_read_authority=True,
                )["gaps"]
                self.assertEqual(
                    other,
                    [
                        {
                            "facet": "relation_tables",
                            "reason": authority[
                                "relation_history"
                                if table in RELATION_HISTORY_TABLES
                                else "relation_tables"
                            ]["other_callers"]["gap_reason"],
                        }
                    ],
                )
                raw = wire_coverage(
                    relations=[table],
                    limited=False,
                    recursion=False,
                    relation_raw_authority=True,
                    source_read_authority=True,
                )["gaps"]
                self.assertEqual(raw, [])


class LostConnectionExecutor:
    """A composed executor whose control connection fails after admission."""

    def start_run(self) -> bytes:
        import psycopg

        raise psycopg.OperationalError("server closed the connection unexpectedly")

    def handle(self, request: bytes) -> bytes:
        import psycopg

        raise psycopg.OperationalError("server closed the connection unexpectedly")


class LostConnectionHost(TrustedHostTransport):
    def _service(self) -> Any:
        return LostConnectionExecutor()


class TrustedHostTransportTests(unittest.TestCase):
    def test_executor_call_failures_are_the_canonical_unavailable_reply(self) -> None:
        transport = LostConnectionHost(
            control_url="postgresql:///unused",
            reader_url="postgresql:///unused",
            reader_role="unused",
            pinned_profile_sha256=None,
            credential_sha256="a" * 64,
            workspace_id=UUID(int=7),
        )
        # Never a traceback or a connection detail: the closed reply only.
        self.assertEqual(transport.start_run(), UNAVAILABLE_REPLY)
        self.assertEqual(transport.handle(b"{}"), UNAVAILABLE_REPLY)


class PortableImportTests(unittest.TestCase):
    def test_cli_imports_without_a_unix_only_lock_module(self) -> None:
        import subprocess
        import sys

        # Import the CLI in a fresh interpreter that cannot import fcntl, as on
        # Windows; the run-store lock is imported only when a run is stored.
        code = (
            "import sys; sys.modules['fcntl'] = None; "
            "import memoriesql.cli, memoriesql.query_client; print('imported')"
        )
        completed = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, check=False
        )
        self.assertEqual(completed.stdout.strip(), "imported", completed.stderr)


if __name__ == "__main__":
    unittest.main()
