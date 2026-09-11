"""Installed-runtime bytes and module ownership are independently checked."""

from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import json
import unittest
from pathlib import Path


class RuntimeOwnership(unittest.TestCase):
    def test_all_runtime_imports_are_owned_by_the_installed_distribution(self) -> None:
        distribution = importlib.metadata.distribution("memoriesql")
        owned = {
            Path(str(distribution.locate_file(p))).resolve()
            for p in distribution.files or []
        }
        rows = json.loads(Path("runtime-inventory.json").read_text())["runtime"]
        for row in rows:
            module_name = (
                row["path"]
                .removeprefix("src/")
                .removesuffix(".py")
                .replace("/", ".")
                .removesuffix(".__init__")
            )
            module = importlib.import_module(module_name)
            assert module.__file__ is not None
            origin = Path(module.__file__).resolve()
            self.assertIn(origin, owned)
            self.assertEqual(
                hashlib.sha256(origin.read_bytes()).hexdigest(), row["sha256"]
            )
        self.assertEqual(len(rows), 39)
        self.assertEqual(
            distribution.requires,
            ["psycopg[binary]==3.3.3", "pydantic==2.13.3", "pydantic-ai-slim==2.27.0"],
        )

    def test_frozen_canonical_task_and_executor_behavior_hashes(self) -> None:
        from memoriesql.application.builtin_semantic_tasks import (
            CANONICAL_AUTHORING_TASK,
        )
        from memoriesql.infrastructure.models.pydanticai_executor import (
            behavior_freeze_hash,
        )

        self.assertEqual(
            CANONICAL_AUTHORING_TASK.contract_hash,
            "df6b7094bdc407ea10464a1b9ff9020cbbc99d57160a3b86d7702d57db89f92e",
        )
        self.assertEqual(
            behavior_freeze_hash(),
            "e5855a25e398a025dfb0a98fc9f23d823aa04a096df3bf0b1bb2ff3565aab929",
        )
