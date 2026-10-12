"""CPU-only owned-query fault injection; no backend-stop qualification claim."""

from __future__ import annotations

import threading
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock, patch
from uuid import uuid4

from psycopg.pq import TransactionStatus

from memoriesql.application.agent_sql_catalog import SqlCatalog
from memoriesql.infrastructure.postgres import restricted_query as module
from memoriesql.infrastructure.postgres.relation_sql_population import (
    PreparedRelationPopulation,
)


class RestrictedQueryFailures(unittest.TestCase):
    def execute_fault(
        self, fault: str
    ) -> tuple[module.NativeQueryExecution, MagicMock]:
        catalog = SqlCatalog.installed()
        table = "memory_v1.observations"
        stamp = datetime.now(UTC)
        population = PreparedRelationPopulation(
            known_at=stamp,
            snapshot_at=stamp,
            catalog_hash=catalog.hash,
            schemas={table: catalog.relations[table]},
            rows={table: ()},
            evidence_bindings={},
            dependency_records=b"[]",
            dependency_manifest=b"[]",
            dependency_manifest_sha256="a" * 64,
            preparation_bytes=0,
            source_context=b"{}",
            revision=2,
        )
        source, control, reader = MagicMock(), MagicMock(), MagicMock()
        for index, connection in enumerate((source, control, reader), 1):
            connection.autocommit = True
            connection.closed = False
            connection.info.host = "fictional"
            connection.info.port = 5432
            connection.info.dbname = "fictional"
            connection.info.backend_pid = index
            connection.info.transaction_status = (
                TransactionStatus.INTRANS
                if connection is source
                else TransactionStatus.IDLE
            )
            connection.__enter__.return_value = connection
        cursor = reader.cursor.return_value.__enter__.return_value
        cursor.fetchmany.return_value = []
        if fault == "fetch":
            cursor.fetchmany.side_effect = [
                [(uuid4(),)],
                RuntimeError("fictional inaccessible detail"),
            ]
        cancellation = threading.Event()
        authority_checks = 0

        def source_execute(statement: str, *args: Any) -> MagicMock:
            nonlocal authority_checks
            if "check_relation_sql_population_authority" in statement:
                authority_checks += 1
                if authority_checks == 2 and fault == "late_cancel":
                    cancellation.set()
            result = MagicMock()
            result.fetchone.return_value = ("{}",)
            return result

        source.execute.side_effect = source_execute
        control.execute.return_value.fetchall.return_value = [(table,)]
        invocation = uuid4()
        stage = MagicMock()
        stage.execute.return_value.fetchone.return_value = (
            {"invocation_ref": str(invocation)},
        )

        @contextmanager
        def staging(*args: Any, **kwargs: Any) -> Iterator[MagicMock]:
            yield stage
            if fault == "stage_exit":
                raise RuntimeError("fictional uncertain commit acknowledgement")

        def backend(connection: Any, pid: int) -> module.BackendTransaction:
            return module.BackendTransaction(pid, "fictional-start", "1/1", 123)

        def owned_call(connection: Any, statement: str, values: Any) -> Any:
            return (
                {"state": "settled"} if "settle_relation_query" in statement else True
            )

        owned = MagicMock(side_effect=owned_call)
        # Model a supervisor not scheduled before final validation. The explicit
        # event must still be observed by the query thread itself.
        delayed_supervisor = MagicMock()
        delayed_supervisor.is_alive.return_value = False
        profile = MagicMock()
        profile.views = ()
        authority = MagicMock()
        authority.profile_sha256 = "b" * 64
        ownership = MagicMock()
        ownership.replayed = False
        ownership.state = "reserved"
        ownership.operation_ref = uuid4()
        ownership.ownership_ref = uuid4()
        executor = module.PostgresRestrictedQuery(
            reader_factory=lambda: reader,
            control_factory=lambda: control,
            authority_profile=profile,
            credential_sha256="c" * 64,
            workspace_id=uuid4(),
            policy_hash="d" * 64,
        )
        with (
            patch.object(module, "qualify_query_authority", return_value=authority),
            patch.object(module, "verify_query_login"),
            patch.object(module, "_configure_reader"),
            patch.object(module, "backend_transaction", side_effect=backend),
            patch.object(module, "relation_projection_frame", staging),
            patch.object(module, "_control_call", owned),
            patch(
                "memoriesql.infrastructure.postgres.restricted_query.threading.Thread",
                return_value=delayed_supervisor,
            ),
        ):
            result = executor.execute(
                source,
                population,
                ownership,
                "SELECT bead_id FROM memory_v1.observations",
                cancellation=cancellation,
            )
        reader.rollback.assert_called_once()
        reader.close.assert_called_once()
        self.assertEqual(result.invocation_ref, invocation)
        return result, owned

    def test_cancellation_after_last_row_is_observed_before_success(self) -> None:
        result, owned = self.execute_fault("late_cancel")
        self.assertEqual(result.outcome, "cancelled")
        self.assertEqual(result.rows, ())
        self.assertEqual(result.settlement_state, "settled")
        settlements = [
            c for c in owned.call_args_list if "settle_relation_query" in c.args[1]
        ]
        self.assertEqual(len(settlements), 1)
        self.assertEqual(settlements[0].args[2][2], "cancelled")

    def test_unexpected_local_failure_still_settles_the_owned_invocation(self) -> None:
        result, owned = self.execute_fault("fetch")
        self.assertEqual(result.outcome, "execution_error")
        self.assertEqual(result.error, "database")
        self.assertEqual(result.rows, ())
        self.assertEqual(result.columns, ())
        self.assertIsNone(result.query_metadata)
        self.assertEqual(result.settlement_state, "settled")
        settlements = [
            c for c in owned.call_args_list if "settle_relation_query" in c.args[1]
        ]
        self.assertEqual(len(settlements), 1)
        self.assertEqual(settlements[0].args[2][2], "execution_error")

    def test_unexpected_staging_exit_keeps_uncertain_ownership(self) -> None:
        result, _ = self.execute_fault("stage_exit")
        self.assertEqual(result.outcome, "settlement_pending")
        self.assertEqual(result.rows, ())
        self.assertEqual(result.columns, ())
        self.assertIsNone(result.query_metadata)

    def test_unmodified_success_path_still_completes(self) -> None:
        result, _ = self.execute_fault("none")
        self.assertEqual(result.outcome, "complete")
        self.assertEqual(result.settlement_state, "settled")
