"""Separate installed processes prove fenced recovery after producer-clock refusal.

Only the disposable fictional database's timestamp producers are controlled.
No host clock, production migration, model call or canonical timestamp is edited.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo


def main() -> None:
    mode = sys.argv[1]
    path = Path("fictional-database-time-session.json")
    sys.path.insert(0, str(Path.cwd()))
    if mode == "prepare":
        if TYPE_CHECKING:
            from tests.runtime.test_database_time_refusal import DatabaseTimeRefusal
        else:
            from test_database_time_refusal import DatabaseTimeRefusal

        fixture = DatabaseTimeRefusal()
        fixture.setUp()
        try:
            fixture.setup_execution()
            worker = fixture.active_worker()
            fixture.refuse_start(worker)

            async def run() -> None:
                try:
                    observed = fixture.observe_settlement(
                        worker, asyncio.get_running_loop()
                    )
                    receipt = await asyncio.wait_for(worker.run_once(), 3)
                    assert str(receipt.cycle_status) == "cleanup_pending", receipt
                    await asyncio.wait_for(observed.wait(), 3)
                    # Exercise the elapsed-budget boundary without waiting for a
                    # production-sized lease or changing any stored fence timestamp.
                    worker._attempt_deadline_ns = time.monotonic_ns() - 1
                    try:
                        await worker.wait_for_cleanup()
                    except psycopg.errors.CheckViolation as error:
                        assert (
                            error.diag.constraint_name
                            == "semantic_task_attempts_time_order"
                        )
                    else:
                        raise AssertionError("refused settlement falsely succeeded")
                    assert not worker.cleanup_pending
                except BaseException:
                    fixture.clock(fixture.safe_time())
                    if worker._cycle_task is not None:
                        await asyncio.gather(worker._cycle_task, return_exceptions=True)
                    raise

            asyncio.run(run())
            fixture.assert_not_dispatched()
            path.write_text(
                json.dumps(
                    dict(
                        database=fixture.database,
                        workspace_id=str(fixture.workspace),
                        credential_sha256=fixture.worker_secret,
                        reaper_credential_sha256=fixture.attestor_secret,
                        fence=asdict(fixture.fence),
                        witness=fixture.attempt(),
                    ),
                    default=str,
                )
            )
        except BaseException:
            fixture.doCleanups()
            raise
        fixture.db.close()
        print(
            "prepare exited after rolled-back refusals; no active database thread or dispatch"
        )
    elif mode == "recover":
        if TYPE_CHECKING:
            from scripts.run_installed_acceptance import install_guard
        else:
            from run_installed_acceptance import install_guard
        install_guard()
        from memoriesql.infrastructure.jobs.postgres_semantic_queue import (
            PostgresSemanticTaskQueue,
            SemanticTaskFence,
        )
        from memoriesql.infrastructure.postgres.authorization import (
            PostgresAuthorizationPort,
        )

        context: dict[str, Any] = json.loads(path.read_text())
        fields = context["fence"]
        for key in (
            "tenant_id",
            "workspace_id",
            "access_scope_id",
            "task_id",
            "attempt_id",
        ):
            fields[key] = UUID(fields[key])
        fields["deadline_at"] = datetime.fromisoformat(fields["deadline_at"])
        fence = SemanticTaskFence(**fields)
        conninfo = make_conninfo(
            os.environ["N1_TEST_DATABASE_URL"], dbname=context["database"]
        )
        with psycopg.connect(conninfo, autocommit=True) as db:

            def attempt() -> list[str | int | None]:
                row = db.execute("""SELECT status,claimed_at,started_at,heartbeat_at,
                    lease_expires_at,deadline_at,finished_at,lease_generation,attempt_id
                    FROM memoriesql.semantic_task_attempts""").fetchone()
                assert row is not None
                return [str(v) if isinstance(v, datetime | UUID) else v for v in row]

            before = attempt()
            assert before == context["witness"], (before, context["witness"])
            row = db.execute("SELECT public.fictional_queue_clock()").fetchone()
            assert row is not None
            with db.transaction():
                db.execute("SET LOCAL ROLE memoriesql_worker")
                PostgresAuthorizationPort(db).begin_context(
                    credential_sha256=context["reaper_credential_sha256"],
                    requested_workspace_id=UUID(context["workspace_id"]),
                )
                assert (
                    PostgresSemanticTaskQueue(db).reap_expired(
                        worker_id="orchard.reaper", limit=1, reaped_at=row[0]
                    )
                    == 0
                )
            assert attempt() == before
            at = datetime.fromisoformat(before[4]) + timedelta(seconds=1)  # type: ignore[arg-type]
            # This producer control exists only in the task-owned test database;
            # accepted data, claimed_at, leases and deadlines remain untouched.
            db.execute("UPDATE public.fictional_queue_clock SET at=%s", (at,))
            with db.transaction():
                db.execute("SET LOCAL ROLE memoriesql_worker")
                PostgresAuthorizationPort(db).begin_context(
                    credential_sha256=context["reaper_credential_sha256"],
                    requested_workspace_id=UUID(context["workspace_id"]),
                )
                queue = PostgresSemanticTaskQueue(db)
                assert (
                    queue.reap_expired(
                        worker_id="orchard.reaper", limit=1, reaped_at=at
                    )
                    == 1
                )
                assert (
                    queue.reap_expired(
                        worker_id="orchard.reaper", limit=1, reaped_at=at
                    )
                    == 0
                )
            after = attempt()
            assert after[0] == "lease_lost" and after[6] == str(at), after
            assert after[1:6] == before[1:6] and after[7:] == before[7:], (
                before,
                after,
            )
            with db.transaction():
                db.execute("SET LOCAL ROLE memoriesql_worker")
                PostgresAuthorizationPort(db).begin_context(
                    credential_sha256=context["credential_sha256"],
                    requested_workspace_id=UUID(context["workspace_id"]),
                )
                queue = PostgresSemanticTaskQueue(db)
                assert not queue.start(fence, started_at=at)
                assert db.execute(
                    """SELECT memoriesql.record_semantic_task_outcome(
                    %s,%s,%s,%s,%s,%s,'failed',NULL,NULL,
                    'worker.database_time_order_refused','worker','never',NULL,0,%s)""",
                    queue._fence_parameters(fence) + (at,),
                ).fetchone() == ("stale_fence",)
            assert attempt() == after
            for table, count in (
                ("semantic_task_attempts", 1),
                ("model_provider_request_intents", 0),
                ("accepted_bead_semantics", 0),
                ("bead_versions", 0),
            ):
                row = db.execute(
                    sql.SQL("SELECT count(*) FROM memoriesql.{}").format(
                        sql.Identifier(table)
                    )
                ).fetchone()
                assert row == (count,), (table, row, count)
        print(
            "fresh installed process reaped exactly once; stale writes refused, original fences preserved, zero dispatch"
        )
    elif mode == "cleanup":
        context = json.loads(path.read_text())
        with psycopg.connect(os.environ["N1_TEST_DATABASE_URL"], autocommit=True) as db:
            db.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                    sql.Identifier(context["database"])
                )
            )
        path.unlink()
        print("task-owned fictional clock-recovery database removed")
    else:
        raise SystemExit("mode must be prepare, recover or cleanup")


if __name__ == "__main__":
    main()
