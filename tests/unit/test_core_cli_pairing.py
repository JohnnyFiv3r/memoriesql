"""Local agent client pairing never prints its secret or leaves an unpaired one."""

from __future__ import annotations

import io
import json
import os
import stat
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch
from uuid import UUID

import psycopg
from psycopg.errors import QueryCanceled

from memoriesql.application.authorization import LocalCredential
from memoriesql.application.local_client_pairing import (
    LocalClientPairing,
    LocalClientRevocation,
)
from memoriesql.cli import main
from memoriesql.infrastructure.postgres.local_client_pairing import (
    PairingRevisionConflict,
    pairing_identities,
)

WORKSPACE = UUID(int=12)
SCOPE = UUID(int=21)
LOCAL_ENV = {
    "MEMORIESQL_DATABASE_URL": "postgresql://fictional.example/fictional",
    "MEMORIESQL_LOCAL_CREDENTIAL": "fictional-local-credential-with-ample-length",
    "MEMORIESQL_WORKSPACE_ID": str(WORKSPACE),
}
EXPIRES = datetime(2026, 9, 30, tzinfo=UTC)
PAIR_REQUEST: dict[str, Any] = {
    "request_id": str(UUID(int=31)),
    "capabilities": ["memory.inspect", "memory.query", "source.read"],
    "access_scope_ids": [str(SCOPE)],
    "expires_at": EXPIRES.isoformat(),
    "exact_pairing_confirmed": True,
}


def invoke(arguments: list[str]) -> tuple[int, str]:
    output = io.StringIO()
    with redirect_stdout(output):
        status = main(arguments)
    return status, output.getvalue()


class CoreCLIPairingTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.request = self.root / "pair.json"
        self.request.write_text(json.dumps(PAIR_REQUEST), encoding="utf-8")
        self.secret = self.root / "agent.secret"

    def pair(self) -> tuple[int, str]:
        return invoke(
            [
                "clients",
                "pair",
                "--request-file",
                str(self.request),
                "--secret-file",
                str(self.secret),
                "--json",
            ]
        )

    def test_refusals_happen_before_secret_or_database_contact(self) -> None:
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("memoriesql.cli.psycopg.connect", side_effect=AssertionError),
        ):
            status, output = self.pair()
        self.assertEqual(status, 2)
        self.assertEqual(
            json.loads(output),
            {"outcome": "unavailable", "reason": "local_client_authority_required"},
        )
        self.assertFalse(self.secret.exists())
        self.request.write_text(
            json.dumps(PAIR_REQUEST | {"capabilities": ["memory.query"] * 2}),
            encoding="utf-8",
        )
        with (
            patch.dict(os.environ, LOCAL_ENV),
            patch("memoriesql.cli.psycopg.connect", side_effect=AssertionError),
        ):
            status, output = self.pair()
        self.assertEqual(status, 3)
        self.assertEqual(
            json.loads(output),
            {"outcome": "failed", "reason": "invalid_client_request"},
        )
        self.assertFalse(self.secret.exists())

    def test_secret_is_owner_only_never_printed_and_only_hashed(self) -> None:
        connection = MagicMock()
        connection.__enter__.return_value = connection
        adapter = MagicMock()
        principal, pairing, grant, credential = pairing_identities(UUID(int=31))
        adapter.pair.return_value = LocalClientPairing(
            principal_id=principal,
            pairing_id=pairing,
            pairing_grant_id=grant,
            credential_id=credential,
            capabilities=tuple(PAIR_REQUEST["capabilities"]),
            access_scope_ids=(SCOPE,),
            expires_at=EXPIRES,
        )
        with (
            patch.dict(os.environ, LOCAL_ENV),
            patch("memoriesql.cli.psycopg.connect", return_value=connection),
            patch(
                "memoriesql.cli.PostgresLocalClientPairing", return_value=adapter
            ) as adapter_type,
        ):
            status, output = self.pair()
        self.assertEqual(status, 0)
        result = json.loads(output)
        self.assertEqual(result["outcome"], "available")
        self.assertTrue(result["secret_file_written"])
        self.assertEqual(result["receipt"]["principal_id"], str(principal))
        secret = self.secret.read_text(encoding="ascii")
        self.assertGreaterEqual(len(secret), 32)
        self.assertNotIn(secret, output)
        self.assertEqual(stat.S_IMODE(self.secret.stat().st_mode), 0o600)
        self.assertEqual(
            adapter.pair.call_args.kwargs["client_secret_sha256"],
            LocalCredential(secret).sha256(),
        )
        self.assertNotEqual(
            adapter_type.call_args.kwargs["credential_sha256"],
            LOCAL_ENV["MEMORIESQL_LOCAL_CREDENTIAL"],
        )

    def test_existing_or_linked_secret_path_is_never_overwritten(self) -> None:
        for existing in ("file", "symlink"):
            with self.subTest(existing=existing):
                if existing == "file":
                    self.secret.write_text("operator-owned bytes", encoding="utf-8")
                else:
                    self.secret.unlink()
                    self.secret.symlink_to(self.root / "elsewhere")
                with (
                    patch.dict(os.environ, LOCAL_ENV),
                    patch("memoriesql.cli.psycopg.connect", side_effect=AssertionError),
                ):
                    status, output = self.pair()
                self.assertEqual(status, 3)
                self.assertEqual(
                    json.loads(output),
                    {"outcome": "failed", "reason": "secret_file_unavailable"},
                )
                self.assertFalse((self.root / "elsewhere").exists())
        self.secret.unlink()
        self.secret.write_text("operator-owned bytes", encoding="utf-8")
        with patch.dict(os.environ, LOCAL_ENV):
            self.pair()
        self.assertEqual(
            self.secret.read_text(encoding="utf-8"), "operator-owned bytes"
        )

    def test_refused_or_failed_pairing_removes_the_unpaired_secret(self) -> None:
        connection = MagicMock()
        connection.__enter__.return_value = connection
        for error, expected in (
            (
                PermissionError("hidden"),
                (2, {"outcome": "unavailable", "reason": "resource_unavailable"}),
            ),
            (
                RuntimeError("postgresql://secret@fictional"),
                (3, {"outcome": "failed", "reason": "client_pairing_failed"}),
            ),
            (
                # A server-reported error means the transaction rolled back.
                QueryCanceled("postgresql://secret@fictional"),
                (3, {"outcome": "failed", "reason": "client_pairing_failed"}),
            ),
        ):
            with self.subTest(error=type(error).__name__):
                adapter = MagicMock()
                adapter.pair.side_effect = error
                with (
                    patch.dict(os.environ, LOCAL_ENV),
                    patch("memoriesql.cli.psycopg.connect", return_value=connection),
                    patch(
                        "memoriesql.cli.PostgresLocalClientPairing",
                        return_value=adapter,
                    ),
                ):
                    status, output = self.pair()
                self.assertEqual((status, json.loads(output)), expected)
                self.assertNotIn("secret@", output)
                self.assertFalse(self.secret.exists())

    def test_unreachable_database_removes_the_unpaired_secret(self) -> None:
        with (
            patch.dict(os.environ, LOCAL_ENV),
            patch(
                "memoriesql.cli.psycopg.connect",
                side_effect=psycopg.OperationalError("postgresql://secret@fictional"),
            ),
        ):
            status, output = self.pair()
        self.assertEqual(
            (status, json.loads(output)),
            (3, {"outcome": "failed", "reason": "client_pairing_failed"}),
        )
        self.assertFalse(self.secret.exists())

    def test_lost_commit_keeps_the_secret_and_names_the_grant(self) -> None:
        connection = MagicMock()
        connection.__enter__.return_value = connection
        adapter = MagicMock()
        adapter.pair.side_effect = psycopg.OperationalError(
            "server closed the connection unexpectedly"
        )
        with (
            patch.dict(os.environ, LOCAL_ENV),
            patch("memoriesql.cli.psycopg.connect", return_value=connection),
            patch("memoriesql.cli.PostgresLocalClientPairing", return_value=adapter),
        ):
            status, output = self.pair()
        principal, _pairing, grant, _credential = pairing_identities(
            UUID(PAIR_REQUEST["request_id"])
        )
        self.assertEqual(status, 3)
        self.assertEqual(
            json.loads(output),
            {
                "outcome": "failed",
                "reason": "pairing_outcome_unknown",
                "secret_file_retained": True,
                "principal_id": str(principal),
                "pairing_grant_id": str(grant),
            },
        )
        secret = self.secret.read_text(encoding="ascii")
        self.assertNotIn(secret, output)
        self.assertEqual(stat.S_IMODE(self.secret.stat().st_mode), 0o600)
        (hashed,) = adapter.pair.call_args.kwargs.values()
        self.assertEqual(hashed, LocalCredential(secret).sha256())

    def test_revocation_maps_conflict_and_hides_refusal(self) -> None:
        request = self.root / "revoke.json"
        request.write_text(
            json.dumps(
                {
                    "pairing_grant_id": str(UUID(int=41)),
                    "expected_revision": 1,
                    "capabilities": PAIR_REQUEST["capabilities"],
                    "access_scope_ids": [str(SCOPE)],
                    "exact_revocation_confirmed": True,
                }
            ),
            encoding="utf-8",
        )
        connection = MagicMock()
        connection.__enter__.return_value = connection
        outcomes: tuple[tuple[object, tuple[int, str]], ...] = (
            (
                LocalClientRevocation(
                    pairing_grant_id=UUID(int=41),
                    revision=2,
                    revoked_at=EXPIRES - timedelta(days=1),
                ),
                (0, "available"),
            ),
            (PairingRevisionConflict("changed"), (3, "failed")),
            (PermissionError("hidden"), (2, "unavailable")),
            (psycopg.OperationalError("connection lost"), (3, "failed")),
        )
        for outcome, (expected_status, expected_outcome) in outcomes:
            with self.subTest(outcome=type(outcome).__name__):
                adapter = MagicMock()
                if isinstance(outcome, Exception):
                    adapter.revoke.side_effect = outcome
                else:
                    adapter.revoke.return_value = outcome
                with (
                    patch.dict(os.environ, LOCAL_ENV),
                    patch("memoriesql.cli.psycopg.connect", return_value=connection),
                    patch(
                        "memoriesql.cli.PostgresLocalClientPairing",
                        return_value=adapter,
                    ),
                ):
                    status, output = invoke(
                        ["clients", "revoke", "--request-file", str(request), "--json"]
                    )
                self.assertEqual(status, expected_status)
                self.assertEqual(json.loads(output)["outcome"], expected_outcome)
                if isinstance(outcome, psycopg.OperationalError):
                    self.assertEqual(
                        json.loads(output)["reason"], "revocation_outcome_unknown"
                    )


if __name__ == "__main__":
    unittest.main()
