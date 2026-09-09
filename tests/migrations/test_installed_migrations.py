"""Fictional migration acceptance, copied into an isolated test workspace."""

from __future__ import annotations

import base64
import hashlib
import importlib.metadata
import io
import json
import os
import sys
import unittest
import uuid
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any
from unittest.mock import patch

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from memoriesql.infrastructure.postgres import migration_runner as runner
from memoriesql.infrastructure.postgres.schema_inspection import inspect_schema


class InstalledMigrations(unittest.TestCase):
    def setUp(self) -> None:
        self.admin_url = os.environ["N1_TEST_DATABASE_URL"]
        self.name = "n1_fictional_" + uuid.uuid4().hex
        with psycopg.connect(self.admin_url, autocommit=True) as admin:
            admin.execute(
                sql.SQL("CREATE DATABASE {}").format(sql.Identifier(self.name))
            )
        self.connection = psycopg.connect(
            make_conninfo(self.admin_url, dbname=self.name), autocommit=True
        )
        self.addCleanup(self.cleanup_database)

    def cleanup_database(self) -> None:
        self.connection.close()
        with psycopg.connect(self.admin_url, autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                    sql.Identifier(self.name)
                )
            )

    def migrate(self, start: int, end: int) -> runner.MigrationReceipt:
        return runner.migrate(
            self.connection, expected_current_version=start, target_version=end
        )

    def history(self) -> list[Any]:
        return self.connection.execute(
            "SELECT version, name, sha256, runner_contract_version, applied_at FROM memoriesql.schema_migrations ORDER BY version"
        ).fetchall()

    def test_installed_ownership_and_complete_resources(self) -> None:
        distribution = importlib.metadata.distribution("memoriesql")
        owned = {
            Path(str(distribution.locate_file(p))).resolve()
            for p in distribution.files or []
        }
        self.assertIn(Path(runner.__file__).resolve(), owned)
        stream = runner.discover_migrations()
        self.assertEqual(len(stream), 14)
        for migration in stream:
            self.assertIn(Path(str(migration.path)).resolve(), owned)
            self.assertEqual(
                hashlib.sha256(migration.path.read_bytes()).hexdigest(),
                migration.sha256,
            )
        self.assertEqual(distribution.version, "0.0.2a1")
        for name, module in tuple(sys.modules.items()):
            if name == "memoriesql" or name.startswith("memoriesql."):
                assert module.__file__ is not None
                self.assertIn(Path(module.__file__).resolve(), owned)
        for record in distribution.files or []:
            if record.hash is not None and str(record).startswith("memoriesql/"):
                digest = hashlib.sha256(
                    Path(str(distribution.locate_file(record))).read_bytes()
                ).digest()
                self.assertEqual(
                    base64.urlsafe_b64encode(digest).decode().rstrip("="),
                    record.hash.value,
                )

    def test_published_payload_hashes_and_version_api(self) -> None:
        from memoriesql import __version__
        from memoriesql.contracts import iter_contracts

        self.assertEqual(__version__, "0.0.2a1")
        from memoriesql.cli import main

        output = io.StringIO()
        with redirect_stdout(output), self.assertRaises(SystemExit) as exit_result:
            main(["--version"])
        self.assertEqual(exit_result.exception.code, 0)
        self.assertEqual(output.getvalue().strip(), __version__)
        registry = json.loads(Path("public-registry.json").read_text())
        entries = {entry["id"]: entry for entry in iter_contracts()}
        self.assertEqual(len(entries), 49)
        for row in registry["records"]:
            payload = json.dumps(
                entries[row["id"]],
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode()
            self.assertEqual(
                hashlib.sha256(payload).hexdigest(), row["reviewed_spike_record_sha256"]
            )

    def test_clean_creation_schema_and_noop_receipts(self) -> None:
        receipt = self.migrate(0, 14)
        expected = json.loads(Path("schema_snapshot.json").read_text())
        self.assertEqual(inspect_schema(self.connection), expected)
        self.assertEqual(receipt.runner_contract_version, 1)
        self.assertEqual(
            (receipt.from_version, receipt.to_version, receipt.target_version),
            (0, 14, 14),
        )
        self.assertEqual(
            [r.version for r in receipt.applied_migrations], list(range(1, 15))
        )
        before = self.history()
        noop = self.migrate(14, 14)
        self.assertEqual(noop.applied_migrations, ())
        self.assertEqual(noop.schema_snapshot_sha256, receipt.schema_snapshot_sha256)
        self.assertEqual(self.history(), before)

    def test_representative_historical_prefixes(self) -> None:
        # Each prefix is applied directly from frozen SQL, independently of migrate().
        stream = runner.discover_migrations()
        for version in (4, 11, 13):
            with self.subTest(prefix=version):
                with self.connection.transaction():
                    self.connection.execute("DROP SCHEMA IF EXISTS memoriesql CASCADE")
                    self.connection.execute("SET LOCAL search_path = pg_catalog")
                    for migration in stream[:version]:
                        self.connection.execute(migration.sql, prepare=False)
                        self.connection.execute(
                            "INSERT INTO memoriesql.schema_migrations (version,name,sha256,runner_contract_version) VALUES (%s,%s,%s,1)",
                            (migration.version, migration.name, migration.sha256),
                        )
                before = self.history()
                receipt = self.migrate(version, 14)
                self.assertEqual(self.history()[:version], before)
                self.assertEqual(
                    [row.version for row in receipt.applied_migrations],
                    list(range(version + 1, 15)),
                )
                self.assertEqual(
                    inspect_schema(self.connection),
                    json.loads(Path("schema_snapshot.json").read_text()),
                )
                self.assertEqual(
                    self.grants(), json.loads(Path("schema_grants.json").read_text())
                )

    def grants(self) -> list[Any]:
        # ACLs are independently checked because snapshot contract 2 omits them.
        return [
            list(row)
            for row in self.connection.execute("""
            SELECT 'relation', c.relname, coalesce(c.relacl::text, '') FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='memoriesql'
            UNION ALL SELECT 'function', p.proname || '(' || pg_get_function_identity_arguments(p.oid) || ')', coalesce(p.proacl::text, '') FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='memoriesql'
            UNION ALL SELECT 'schema', nspname, coalesce(nspacl::text, '') FROM pg_namespace WHERE nspname='memoriesql'
            ORDER BY 1,2
        """).fetchall()
        ]

    def test_wrong_bound_downgrade_and_out_of_range_are_atomic(self) -> None:
        self.migrate(0, 14)
        before = self.history()
        for start, end in ((13, 14), (14, 13), (14, 15), (14, -1)):
            with (
                self.subTest(start=start, end=end),
                self.assertRaises(runner.MigrationError),
            ):
                self.migrate(start, end)
            self.assertEqual(self.history(), before)
        with self.connection.transaction():
            with self.assertRaises(runner.MigrationError):
                self.migrate(14, 14)

    def test_invalid_database_histories_are_rejected(self) -> None:
        self.migrate(0, 14)
        for mutation in (
            "UPDATE memoriesql.schema_migrations SET name='fictional_drift' WHERE version=11",
            "UPDATE memoriesql.schema_migrations SET sha256=repeat('0',64) WHERE version=11",
            "DELETE FROM memoriesql.schema_migrations WHERE version=11",
            "INSERT INTO memoriesql.schema_migrations(version,name,sha256,runner_contract_version) VALUES(15,'future',repeat('0',64),1)",
        ):
            with self.subTest(mutation=mutation):
                # Test-only corruption bypasses the immutable-history trigger.
                self.connection.execute("BEGIN")
                self.connection.execute(
                    "ALTER TABLE memoriesql.schema_migrations DISABLE TRIGGER USER"
                )
                self.connection.execute(mutation)
                self.connection.execute("COMMIT")
                try:
                    with self.assertRaises(runner.MigrationError):
                        self.migrate(14, 14)
                finally:
                    self.connection.execute("DROP SCHEMA memoriesql CASCADE")
                    self.migrate(0, 14)

    def test_missing_changed_extra_and_symlink_resources_fail_closed(self) -> None:
        import shutil
        import tempfile

        package = Path(runner.__file__).parent
        for mutation in ("missing", "changed", "extra", "symlink"):
            with (
                self.subTest(mutation=mutation),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                shutil.copytree(package / "_migrations", root / "_migrations")
                shutil.copyfile(
                    package / "_migration_inventory.json",
                    root / "_migration_inventory.json",
                )
                target = next((root / "_migrations").iterdir())
                if mutation == "missing":
                    target.unlink()
                elif mutation == "changed":
                    target.write_text("SELECT 1;")
                elif mutation == "extra":
                    (root / "_migrations/0015_extra.sql").write_text("SELECT 1;")
                else:
                    target.unlink()
                    target.symlink_to(package / "_migrations" / target.name)
                with (
                    patch.object(runner, "files", return_value=root),
                    self.assertRaises(runner.MigrationError),
                ):
                    self.migrate(0, 14)
                result = self.connection.execute(
                    "SELECT to_regclass('memoriesql.schema_migrations')"
                ).fetchone()
                assert result is not None
                self.assertIsNone(result[0])
