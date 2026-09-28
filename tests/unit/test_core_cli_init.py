"""`memoriesql init` never prints its secret or leaves an unissued one behind."""

from __future__ import annotations

import io
import json
import os
import stat
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch
from uuid import UUID

from memoriesql.application.authorization import LocalCredential
from memoriesql.application.personal_local_initialization import (
    PersonalLocalInitialization,
)
from memoriesql.cli import main
from memoriesql.infrastructure.postgres.personal_local_initialization import (
    AlreadyInitialized,
    initialization_identities,
)

DATABASE = {"MEMORIESQL_DATABASE_URL": "postgresql://fictional.example/fictional"}
EXPIRES = datetime(2026, 9, 30, tzinfo=UTC)
REQUEST: dict[str, Any] = {
    "request_id": str(UUID(int=51)),
    "owner_display_name": "Fictional owner",
    "expires_at": EXPIRES.isoformat(),
    "exact_initialization_confirmed": True,
}


def receipt(replayed: bool) -> PersonalLocalInitialization:
    ids = initialization_identities(UUID(int=51))
    return PersonalLocalInitialization(
        tenant_id=ids["tenant"],
        user_id=ids["user"],
        principal_id=ids["principal"],
        workspace_id=ids["workspace"],
        access_scope_id=ids["access-scope"],
        credential_id=ids["credential"],
        expires_at=EXPIRES,
        replayed=replayed,
    )


class CoreCLIInitTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.request = self.root / "init.json"
        self.request.write_text(json.dumps(REQUEST), encoding="utf-8")
        self.secret = self.root / "owner.secret"

    def run_init(
        self, environment: dict[str, str], adapter: Any = None
    ) -> tuple[int, str]:
        connection = MagicMock()
        connection.__enter__.return_value = connection
        output = io.StringIO()
        with (
            patch.dict(os.environ, environment, clear=True),
            patch(
                "memoriesql.cli.psycopg.connect",
                return_value=connection
                if adapter is not None
                else MagicMock(side_effect=AssertionError),
            ),
            patch(
                "memoriesql.cli.PostgresPersonalLocalInitialization",
                return_value=adapter or MagicMock(side_effect=AssertionError),
            ),
            redirect_stdout(output),
        ):
            status = main(
                [
                    "init",
                    "--request-file",
                    str(self.request),
                    "--secret-file",
                    str(self.secret),
                    "--json",
                ]
            )
        return status, output.getvalue()

    def test_refusals_happen_before_secret_or_database_contact(self) -> None:
        status, output = self.run_init({})
        self.assertEqual(
            (status, json.loads(output)),
            (2, {"outcome": "unavailable", "reason": "database_not_configured"}),
        )
        self.assertFalse(self.secret.exists())
        self.request.write_text(
            json.dumps(REQUEST | {"owner_display_name": "   "}), encoding="utf-8"
        )
        status, output = self.run_init(DATABASE)
        self.assertEqual(json.loads(output)["reason"], "invalid_initialization_request")
        self.assertFalse(self.secret.exists())

    def test_existing_secret_file_is_stale_and_never_touched(self) -> None:
        self.secret.write_text("operator-owned bytes", encoding="utf-8")
        status, output = self.run_init(DATABASE)
        self.assertEqual(
            (status, json.loads(output)),
            (3, {"outcome": "failed", "reason": "stale_secret_file"}),
        )
        self.assertEqual(
            self.secret.read_text(encoding="utf-8"), "operator-owned bytes"
        )

    def test_owner_secret_is_owner_only_never_printed_and_only_hashed(self) -> None:
        adapter = MagicMock()
        adapter.initialize.return_value = receipt(replayed=False)
        status, output = self.run_init(DATABASE, adapter)
        self.assertEqual(status, 0, output)
        result = json.loads(output)
        self.assertTrue(result["secret_file_written"])
        self.assertFalse(result["receipt"]["replayed"])
        secret = self.secret.read_text(encoding="ascii")
        self.assertNotIn(secret, output)
        self.assertEqual(stat.S_IMODE(self.secret.stat().st_mode), 0o600)
        self.assertEqual(
            adapter.initialize.call_args.kwargs["session_secret_sha256"],
            LocalCredential(secret).sha256(),
        )

    def test_replay_refusal_and_failure_remove_the_unissued_secret(self) -> None:
        for outcome, expected in (
            (receipt(replayed=True), (0, "available")),
            (AlreadyInitialized("hidden"), (2, "unavailable")),
            (PermissionError("hidden"), (2, "unavailable")),
            (RuntimeError("postgresql://secret@fictional"), (3, "failed")),
        ):
            with self.subTest(outcome=type(outcome).__name__):
                adapter = MagicMock()
                if isinstance(outcome, Exception):
                    adapter.initialize.side_effect = outcome
                else:
                    adapter.initialize.return_value = outcome
                status, output = self.run_init(DATABASE, adapter)
                self.assertEqual((status, json.loads(output)["outcome"]), expected)
                self.assertNotIn("secret@", output)
                self.assertFalse(self.secret.exists())
        self.assertFalse(
            json.loads(
                self.run_init(
                    DATABASE,
                    MagicMock(initialize=MagicMock(return_value=receipt(True))),
                )[1]
            )["secret_file_written"]
        )


if __name__ == "__main__":
    unittest.main()
