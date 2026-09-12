"""Fictional reader workload measurement, separate from model dispatch counts."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import tracemalloc
import unittest
from typing import TYPE_CHECKING, Any

import psycopg
from psycopg.conninfo import make_conninfo

from memoriesql.application.complete_input_execution import (
    ActivateCompleteInput,
    ReadCompleteEvidence,
)
from memoriesql.application.evidence_packages import (
    PageEvidenceInventory,
    ReadEvidencePart,
)
from memoriesql.infrastructure.postgres.complete_input_execution import (
    PostgresCompleteInput,
)
from memoriesql.infrastructure.postgres.evidence_packages import (
    PostgresEvidencePackages,
)

if TYPE_CHECKING:
    from tests.runtime.test_complete_input_execution import CompleteInputExecution
else:
    from test_complete_input_execution import CompleteInputExecution


class CountingConnection(psycopg.Connection[Any]):
    operations = 0

    def execute(self, *args: Any, **kwargs: Any) -> Any:
        self.operations += 1
        return super().execute(*args, **kwargs)


class CompleteInputThroughput(CompleteInputExecution):
    def qualify(self, texts: list[str]) -> dict[str, Any]:
        parts = tuple(self.part(text, i) for i, text in enumerate(texts))
        package = self.create(parts)
        for part in parts:
            self.append(package, part)
        self.seal(package)
        pin = self.status(package).declaration.expected_inventory_sha256
        expected = hashlib.sha256("".join(texts).encode()).hexdigest()
        measurements: dict[str, Any] = {
            "parts": len(texts),
            "characters": sum(map(len, texts)),
            "utf8_bytes": sum(len(s.encode()) for s in texts),
        }
        with CountingConnection.connect(
            make_conninfo(self.admin, dbname=self.database), autocommit=True
        ) as connection:
            old = PostgresEvidencePackages(
                connection,
                credential_sha256=self.secret_hash,
                workspace_id=self.workspace,
            )
            new = PostgresCompleteInput(
                connection,
                credential_sha256=self.secret_hash,
                workspace_id=self.workspace,
            )
            for version in (1, 2):
                connection.operations = 0
                operations = 0
                digest = hashlib.sha256()
                tracemalloc.start()
                start = time.perf_counter()
                if version == 1:
                    cursor = None
                    while True:
                        inventory = old.inventory(
                            PageEvidenceInventory(
                                package_id=package.package_id, continuation=cursor
                            )
                        )
                        operations += 1
                        for entry in inventory.entries:
                            content_cursor = None
                            while True:
                                page = old.read(
                                    ReadEvidencePart(
                                        package_id=package.package_id,
                                        part_id=entry.part_id,
                                        continuation=content_cursor,
                                    )
                                )
                                operations += 1
                                digest.update(page.content.encode())
                                content_cursor = page.continuation
                                if content_cursor is None:
                                    break
                        cursor = inventory.continuation
                        if cursor is None:
                            break
                else:
                    ordinal = 0
                    while True:
                        batch = new.read(
                            ReadCompleteEvidence(
                                package_id=package.package_id,
                                inventory_sha256=pin,
                                next_ordinal=ordinal,
                            )
                        )
                        operations += 1
                        for retained in batch.parts:
                            digest.update(retained.content.encode())
                        if batch.next_ordinal is None:
                            break
                        ordinal = batch.next_ordinal
                elapsed = time.perf_counter() - start
                _, peak = tracemalloc.get_traced_memory()
                tracemalloc.stop()
                self.assertEqual(digest.hexdigest(), expected)
                measurements[f"reader_v{version}"] = {
                    "authorized_operations": operations,
                    "client_sql_execute_calls": connection.operations,
                    "elapsed_ms": round(elapsed * 1000, 3),
                    "peak_traced_bytes": peak,
                    "provider_interactions": 0,
                }
        self.bound = self.materializer.materialize(self.materialization(package))
        self.activation = self.complete.activate(
            ActivateCompleteInput(
                idempotency_key="throughput.activate",
                binding_task_id=self.bound.task_id,
                dispatch_policy_id=self.dispatch_policy,
            )
        )
        interactions = 0
        max_prompt_bytes = 0
        supplied_hash = hashlib.sha256()

        def respond(messages: Any, info: Any) -> Any:
            nonlocal interactions, max_prompt_bytes
            response = self.response(messages, info)
            frame = self.received.pop()
            for item in frame["complete_input_window"]["slices"]:
                supplied_hash.update(item["content"].encode())
            interactions += 1
            max_prompt_bytes = max(
                max_prompt_bytes,
                len(
                    json.dumps(frame, ensure_ascii=True, separators=(",", ":")).encode()
                ),
            )
            return response

        start = time.perf_counter()
        result = asyncio.run(self.worker(respond).run_once())
        self.assertEqual(result.task_status, "succeeded", result)
        self.assertEqual(supplied_hash.hexdigest(), expected)
        measurements["execution"] = {
            "fake_model_interactions": interactions,
            "elapsed_ms": round((time.perf_counter() - start) * 1000, 3),
            "max_prompt_json_bytes": max_prompt_bytes,
            "canonical_beads": self.row(
                "SELECT count(*) FROM memoriesql.accepted_bead_semantics"
            )[0],
        }
        self.assertEqual(measurements["execution"]["canonical_beads"], 1)
        self.assertLess(
            interactions, measurements["reader_v1"]["authorized_operations"]
        )
        self.assertLess(
            measurements["reader_v2"]["authorized_operations"],
            measurements["reader_v1"]["authorized_operations"],
        )
        plan = self.db.execute(
            "EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) SELECT ordinal,inventory,content FROM memoriesql.evidence_package_parts WHERE tenant_id=%s AND package_id=%s AND ordinal>=0 ORDER BY ordinal LIMIT 8",
            (self.tenant, package.package_id),
        ).fetchone()
        assert plan is not None
        measurements["bounded_query_plan"] = plan[0]
        print("COMPLETE_INPUT_THROUGHPUT " + json.dumps(measurements, sort_keys=True))
        return measurements

    def test_large_unicode_reader_throughput(self) -> None:
        self.qualify(["🌳e\u0301雪" * 4096 for _ in range(8)])

    def test_many_part_reader_throughput(self) -> None:
        self.qualify(["tree🌳" * 10 for _ in range(256)])

    def test_astral_part_boundaries_fit_eight_requests(self) -> None:
        measured = self.qualify(["🚀" * 16384 for _ in range(5)])
        self.assertEqual(measured["reader_v2"]["authorized_operations"], 1)
        self.assertEqual(measured["execution"]["fake_model_interactions"], 8)


def load_tests(
    loader: unittest.TestLoader, tests: unittest.TestSuite, pattern: str | None
) -> unittest.TestSuite:
    return unittest.TestSuite(
        CompleteInputThroughput(name)
        for name in CompleteInputThroughput.__dict__
        if name.startswith("test_")
    )
