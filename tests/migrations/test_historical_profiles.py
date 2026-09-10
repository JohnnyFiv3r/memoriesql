"""Fictional compatibility check for the narrow historical SQL exception."""

import re
import unittest

from memoriesql.infrastructure.postgres.migration_runner import discover_migrations


class HistoricalProfiles(unittest.TestCase):
    def test_exact_historical_allowlist_is_preserved(self) -> None:
        stream = discover_migrations()
        historical = [migration for migration in stream if migration.version == 14]
        self.assertEqual(len(historical), 1)
        sql = historical[0].sql
        identifiers = re.findall(
            r"'((?:memoriesql\.)(?:codex-passive|claude-code)[^']*)'", sql
        )
        self.assertEqual(
            identifiers,
            [
                "memoriesql.codex-passive@2:codex.rollout.paginated.v1",
                "memoriesql.codex-passive@2:codex.rollout.legacy-vscode.v1",
                "memoriesql.claude-code@1:claude-code.session-jsonl.v2.1.231",
            ],
        )
        self.assertIn(
            "RAISE EXCEPTION 'transcript fold exact turn is unqualified'", sql
        )

        for migration in stream:
            if migration.version > 14:
                self.assertEqual(
                    re.findall(
                        r"'((?:memoriesql\.)(?:codex-passive|claude-code)[^']*)'",
                        migration.sql,
                    ),
                    [],
                )
