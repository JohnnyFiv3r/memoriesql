"""Public-owned fictional stream tests; no database or acquisition fixtures."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from memoriesql.infrastructure.postgres import migration_runner as runner


class MigrationStreamTests(unittest.TestCase):
    def test_explicit_stream_rejects_invalid_entries(self) -> None:
        for name, data in [
            ("0002_gap.sql", "SELECT 1;"),
            ("notes.txt", "text"),
            ("0001_empty.sql", ""),
        ]:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / name).write_text(data)
                with self.assertRaises(runner.MigrationError):
                    runner.discover_migrations(root)

    def test_history_rejects_changed_name_hash_gap_and_ahead(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "0001_fictional.sql").write_text("SELECT 1;\n")
            stream = runner.discover_migrations(root)
            valid = runner.AppliedMigration(1, "fictional", stream[0].sha256)
            runner.validate_migration_history(stream, [valid])
            for history in (
                [runner.AppliedMigration(1, "changed", valid.sha256)],
                [runner.AppliedMigration(1, "fictional", "0" * 64)],
                [runner.AppliedMigration(2, "fictional", valid.sha256)],
                [valid, valid],
            ):
                with (
                    self.subTest(history=history),
                    self.assertRaises(runner.MigrationError),
                ):
                    runner.validate_migration_history(stream, history)

    def test_missing_package_never_uses_working_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(runner, "files", return_value=Path(directory)):
                with self.assertRaises(runner.MigrationError):
                    runner.discover_migrations()
