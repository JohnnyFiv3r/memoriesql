"""The public executable exposes only explicit, authorized core read paths."""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch
from uuid import UUID

from memoriesql.application.source_enrollment import ExactSourceEnrollment
from memoriesql.cli import main
from memoriesql.infrastructure.postgres.source_enrollment import SourceAuthorityBusy

BEAD = UUID(int=11)
WORKSPACE = UUID(int=12)
SOURCE_REQUEST = {
    "request_id": str(UUID(int=13)),
    "source_system": "fictional-orchard",
    "object_kind": "transcript",
    "external_object_id": "exact-selected-session",
    "source_schema_version": 1,
    "exact_source_confirmed": True,
}
LOCAL_ENV = {
    "MEMORIESQL_DATABASE_URL": "postgresql://fictional.example/fictional",
    "MEMORIESQL_LOCAL_CREDENTIAL": "fictional-local-credential-with-ample-length",
    "MEMORIESQL_WORKSPACE_ID": str(WORKSPACE),
}


def invoke(arguments: list[str]) -> tuple[int, dict[str, object]]:
    output = io.StringIO()
    with redirect_stdout(output):
        status = main(arguments)
    return status, json.loads(output.getvalue())


class CoreCLIReadTests(unittest.TestCase):
    def test_doctor_and_capabilities_never_connect_or_discover_private_code(
        self,
    ) -> None:
        with (
            patch.dict(os.environ, LOCAL_ENV),
            patch("memoriesql.cli.psycopg.connect", side_effect=AssertionError),
        ):
            status, doctor = invoke(["doctor", "--json"])
            self.assertEqual(status, 0)
            self.assertTrue(doctor["configuration_only"])
            self.assertTrue(doctor["local_read_identity_configured"])
            self.assertFalse(doctor["database_contacted"])
            self.assertNotIn(LOCAL_ENV["MEMORIESQL_LOCAL_CREDENTIAL"], str(doctor))
            self.assertNotIn(LOCAL_ENV["MEMORIESQL_DATABASE_URL"], str(doctor))
            status, capabilities = invoke(["capabilities", "--json"])
        self.assertEqual(status, 0)
        commands = capabilities["core_commands"]
        self.assertIsInstance(commands, list)
        assert isinstance(commands, list)
        self.assertIn("inspect", commands)
        unavailable = capabilities["unavailable"]
        self.assertIsInstance(unavailable, dict)
        assert isinstance(unavailable, dict)
        self.assertEqual(unavailable["query"], "pr05_query_result_not_released")
        self.assertIn("sources enroll", commands)
        self.assertEqual(unavailable["sources connect"], "provider_adapter_not_routed")
        self.assertEqual(unavailable["sources"], "source_inventory_not_released")
        status, inventory = invoke(["sources", "--json"])
        self.assertEqual(status, 2)
        self.assertEqual(
            inventory,
            {"outcome": "unavailable", "reason": "source_inventory_not_released"},
        )

    def test_read_without_identity_is_unavailable_before_database_contact(self) -> None:
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("memoriesql.cli.psycopg.connect", side_effect=AssertionError),
        ):
            for command in ("inspect", "relations"):
                status, result = invoke([command, str(BEAD), "--json"])
                self.assertEqual(status, 2)
                self.assertEqual(
                    result,
                    {
                        "outcome": "unavailable",
                        "reason": "local_read_identity_required",
                    },
                )

    def test_inspect_dispatches_typed_request_with_derived_credential(self) -> None:
        connection = MagicMock()
        connection.__enter__.return_value = connection
        reader = MagicMock()
        reader.inspect.return_value = {"outcome": "unavailable"}
        with (
            patch.dict(os.environ, LOCAL_ENV),
            patch("memoriesql.cli.psycopg.connect", return_value=connection) as connect,
            patch(
                "memoriesql.cli.PostgresStoredBeadInspection", return_value=reader
            ) as reader_type,
        ):
            status, result = invoke(["inspect", str(BEAD), "--json"])
        self.assertEqual(status, 2)
        self.assertEqual(result, {"outcome": "unavailable"})
        connect.assert_called_once_with(
            LOCAL_ENV["MEMORIESQL_DATABASE_URL"], autocommit=True
        )
        self.assertEqual(reader.inspect.call_args.args[0].bead_id, BEAD)
        self.assertEqual(reader_type.call_args.kwargs["workspace_id"], WORKSPACE)
        self.assertEqual(len(reader_type.call_args.kwargs["credential_sha256"]), 64)
        self.assertNotEqual(
            reader_type.call_args.kwargs["credential_sha256"],
            LOCAL_ENV["MEMORIESQL_LOCAL_CREDENTIAL"],
        )

    def test_invalid_source_selection_never_contacts_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "selection.json"
            path.write_text('{"package_id":"invalid"}', encoding="utf-8")
            with (
                patch.dict(os.environ, LOCAL_ENV),
                patch("memoriesql.cli.psycopg.connect", side_effect=AssertionError),
            ):
                status, result = invoke(
                    ["source", str(BEAD), "--selection-file", str(path), "--json"]
                )
        self.assertEqual(status, 3)
        self.assertEqual(
            result, {"outcome": "failed", "reason": "invalid_source_selection"}
        )

    def test_historical_relation_cutoff_requires_explicit_offset(self) -> None:
        with (
            patch.dict(os.environ, LOCAL_ENV),
            patch("memoriesql.cli.psycopg.connect", side_effect=AssertionError),
            redirect_stderr(io.StringIO()),
            self.assertRaises(SystemExit) as rejected,
        ):
            main(["relations", str(BEAD), "--known-at", "2026-09-28T08:00:00"])
        self.assertEqual(rejected.exception.code, 2)

    def test_bad_connection_is_failed_without_leaking_secret_or_dsn(self) -> None:
        with (
            patch.dict(os.environ, LOCAL_ENV),
            patch(
                "memoriesql.cli.psycopg.connect",
                side_effect=RuntimeError(LOCAL_ENV["MEMORIESQL_LOCAL_CREDENTIAL"]),
            ),
        ):
            status, result = invoke(["inspect", str(BEAD), "--json"])
        self.assertEqual(status, 3)
        self.assertEqual(result, {"outcome": "failed", "reason": "core_read_failed"})
        self.assertNotIn(LOCAL_ENV["MEMORIESQL_LOCAL_CREDENTIAL"], str(result))

    def test_source_authority_requires_valid_exact_request_and_local_identity(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "request.json"
            path.write_text(json.dumps(SOURCE_REQUEST), encoding="utf-8")
            with (
                patch.dict(os.environ, {}, clear=True),
                patch("memoriesql.cli.psycopg.connect", side_effect=AssertionError),
            ):
                status, result = invoke(
                    ["sources", "enroll", "--request-file", str(path), "--json"]
                )
            self.assertEqual(status, 2)
            self.assertEqual(
                result,
                {"outcome": "unavailable", "reason": "local_source_authority_required"},
            )

            path.write_text(
                json.dumps(SOURCE_REQUEST | {"exact_source_confirmed": False}),
                encoding="utf-8",
            )
            with (
                patch.dict(os.environ, LOCAL_ENV),
                patch("memoriesql.cli.psycopg.connect", side_effect=AssertionError),
            ):
                status, result = invoke(
                    ["sources", "enroll", "--request-file", str(path), "--json"]
                )
            self.assertEqual(status, 3)
            self.assertEqual(result, {"outcome": "failed", "reason": "invalid_source_request"})

    def test_source_authority_returns_typed_receipt_without_exposing_secret(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "request.json"
            path.write_text(json.dumps(SOURCE_REQUEST), encoding="utf-8")
            connection = MagicMock()
            connection.__enter__.return_value = connection
            authority = MagicMock()
            authority.enroll.return_value = ExactSourceEnrollment(
                source_object_id=UUID(int=13),
                access_scope_id=UUID(int=14),
                policy_revision_id=UUID(int=15),
                replayed=False,
            )
            with (
                patch.dict(os.environ, LOCAL_ENV),
                patch("memoriesql.cli.psycopg.connect", return_value=connection),
                patch(
                    "memoriesql.cli.PostgresSourceEnrollment", return_value=authority
                ) as adapter,
            ):
                status, result = invoke(
                    ["sources", "enroll", "--request-file", str(path), "--json"]
                )
            self.assertEqual(status, 0)
            self.assertEqual(result["outcome"], "available")
            receipt = result["receipt"]
            self.assertIsInstance(receipt, dict)
            assert isinstance(receipt, dict)
            self.assertEqual(receipt["source_object_id"], str(UUID(int=13)))
            self.assertEqual(authority.enroll.call_args.args[0].source_system, "fictional-orchard")
            self.assertEqual(adapter.call_args.kwargs["workspace_id"], WORKSPACE)
            self.assertNotIn(LOCAL_ENV["MEMORIESQL_LOCAL_CREDENTIAL"], str(result))

    def test_source_authority_denial_is_indistinguishable_and_sanitized(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "request.json"
            path.write_text(json.dumps(SOURCE_REQUEST), encoding="utf-8")
            connection = MagicMock()
            connection.__enter__.return_value = connection
            authority = MagicMock()
            authority.enroll.side_effect = PermissionError(SOURCE_REQUEST["external_object_id"])
            with (
                patch.dict(os.environ, LOCAL_ENV),
                patch("memoriesql.cli.psycopg.connect", return_value=connection),
                patch("memoriesql.cli.PostgresSourceEnrollment", return_value=authority),
            ):
                status, result = invoke(
                    ["sources", "enroll", "--request-file", str(path), "--json"]
                )
            self.assertEqual(status, 2)
            self.assertEqual(result, {"outcome": "unavailable", "reason": "resource_unavailable"})
            self.assertNotIn(SOURCE_REQUEST["external_object_id"], str(result))

    def test_source_authority_time_limit_is_a_distinct_retryable_failure(self) -> None:
        source = {"request_id": str(UUID(int=13)), "source_object_id": str(UUID(int=14))}
        requests: dict[str, dict[str, Any]] = {
            "enroll": SOURCE_REQUEST,
            "grant": source
            | {
                "target_principal_id": str(UUID(int=15)),
                "permission_keys": ["read"],
                "valid_from": "2026-01-01T00:00:00Z",
                "expires_at": "2026-01-02T00:00:00Z",
            },
            "revoke": source | {"reason": "fictional orchard closed"},
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "request.json"
            connection = MagicMock()
            connection.__enter__.return_value = connection
            for action, request in requests.items():
                path.write_text(json.dumps(request), encoding="utf-8")
                authority = MagicMock()
                getattr(authority, action).side_effect = SourceAuthorityBusy(
                    request["request_id"]
                )
                with (
                    self.subTest(action=action),
                    patch.dict(os.environ, LOCAL_ENV),
                    patch("memoriesql.cli.psycopg.connect", return_value=connection),
                    patch(
                        "memoriesql.cli.PostgresSourceEnrollment",
                        return_value=authority,
                    ),
                ):
                    status, result = invoke(
                        ["sources", action, "--request-file", str(path), "--json"]
                    )
                    # Exhausted work, not a denial: exit 3. Nothing was written,
                    # so the identical request may be retried.
                    self.assertEqual(status, 3)
                    self.assertEqual(
                        result,
                        {"outcome": "failed", "reason": "source_authority_busy"},
                    )


if __name__ == "__main__":
    unittest.main()
