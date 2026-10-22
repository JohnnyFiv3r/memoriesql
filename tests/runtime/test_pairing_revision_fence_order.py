"""Pairing-grant revocation against outcome settlement on disposable PostgreSQL.

Outcome settlement holds the tenant's outcome-authority fence shared through its
writes, and its last write to the task rechecks the task's foreign key to its
origin pairing grant. A revocation that locked that grant's row and then waited
for the fence closed a cycle. The race is first reproduced on the schema before
the fix, then shown gone after it, in the same fictional database. Each step
waits for an observed lock state; nothing here measures timing.
"""

from __future__ import annotations

import hashlib
import os
import time
import unittest
import uuid
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import psycopg
from psycopg import errors, sql
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Jsonb

from memoriesql.application.authorization import uuid7
from memoriesql.infrastructure.postgres.migration_runner import discover_migrations

if TYPE_CHECKING:
    from tests.runtime.test_postgres_runtime import migrate
else:
    from test_postgres_runtime import migrate

FIX = next(
    migration.version
    for migration in discover_migrations()
    if migration.path.name.endswith("_pairing_revision_fence_order.sql")
)
START = "SELECT principal_id FROM memoriesql.begin_authorization_context(%s,%s)"
FENCE = "pg_catalog.hashtextextended(%s::text || ':semantic_outcome_authority:', 0)"
# A test-only lock that holds a settlement inside its writes, after it has
# taken the outcome-authority fence and updated its task once.
GATE = int.from_bytes(b"orchgate", "big")
# The deadlock detector must not end a cycle before the test has observed it.
QUIET = "-c deadlock_timeout=10min"
REGISTRY = "fictional-orchard-registry"
TASK_KIND = "fictional.orchard.settle"
MODULE = "memoriesql.fictional-orchard"
CONTRACT_HASH = hashlib.sha256(b"fictional orchard settle contract").hexdigest()


def secret(label: str) -> str:
    return hashlib.sha256(("fictional orchard " + label).encode()).hexdigest()


@unittest.skipUnless(
    os.environ.get("N1_TEST_DATABASE_URL"), "disposable PostgreSQL required"
)
class PairingRevisionFenceOrder(unittest.TestCase):
    def setUp(self) -> None:
        self.admin_url = os.environ["N1_TEST_DATABASE_URL"]
        self.database = "pairing_fence_" + uuid.uuid4().hex
        with psycopg.connect(self.admin_url, autocommit=True) as admin:
            admin.execute(
                sql.SQL("CREATE DATABASE {}").format(sql.Identifier(self.database))
            )
        self.url = make_conninfo(self.admin_url, dbname=self.database)
        self.db = psycopg.connect(self.url, autocommit=True)
        self.addCleanup(self.cleanup_database)
        migrate(self.db, expected_current_version=0, target_version=FIX - 1)
        (
            self.tenant,
            user,
            identity,
            principal,
            self.workspace,
            self.scope,
            membership,
            credential,
            session,
        ) = (uuid.uuid4() for _ in range(9))
        self.owner = secret("owner session")
        self.now = self.scalar("SELECT clock_timestamp()")
        with self.db.transaction():
            self.db.execute("SET LOCAL ROLE memoriesql_application")
            self.db.execute(
                "SELECT memoriesql.bootstrap_personal_local(%s,%s,%s,%s,%s,%s,%s,%s,%s,"
                "'memoriesql.local',%s,'Fictional orchard',%s,%s,%s)",
                (
                    self.tenant,
                    user,
                    identity,
                    principal,
                    self.workspace,
                    self.scope,
                    membership,
                    credential,
                    session,
                    str(identity),
                    self.owner,
                    self.now,
                    self.now + timedelta(hours=1),
                ),
            )
        self.worker, worker_grant = self.pair(
            "service", "background_service", "memory.maintain"
        )
        worker_principal = self.scalar(
            "SELECT paired_principal_id FROM memoriesql.pairing_grants "
            "WHERE pairing_grant_id=%s",
            (worker_grant,),
        )
        with self.db.transaction():
            # Test-only admission of a fictional task kind that a paired agent
            # may enqueue, and the worker's claim of it.
            self.db.execute(
                "INSERT INTO memoriesql.semantic_task_admission_policies"
                "(semantic_registry_hash,task_kind,contract_revision,owning_module,"
                "task_contract_hash,target_kind,required_capability,queue_name,"
                "base_priority,max_attempts) VALUES"
                "(%s,%s,1,%s,%s,'fictional_receipt','agent.run','interactive',100,3)",
                (REGISTRY, TASK_KIND, MODULE, CONTRACT_HASH),
            )
            self.db.execute(
                "INSERT INTO memoriesql.semantic_worker_claim_policies"
                "(tenant_id,workspace_id,principal_id,pairing_grant_id,"
                "semantic_registry_hash,task_kind,contract_revision,queue_name)"
                " VALUES(%s,%s,%s,%s,%s,%s,1,'interactive')",
                (
                    self.tenant,
                    self.workspace,
                    worker_principal,
                    worker_grant,
                    REGISTRY,
                    TASK_KIND,
                ),
            )
        self.fence = self.scalar("SELECT " + FENCE, (self.tenant,))

    def cleanup_database(self) -> None:
        self.db.close()
        with psycopg.connect(self.admin_url, autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                    sql.Identifier(self.database)
                )
            )

    def connect(self) -> psycopg.Connection[Any]:
        connection = psycopg.connect(self.url, autocommit=True, options=QUIET)
        self.addCleanup(connection.close)
        return connection

    def scalar(self, statement: str, values: tuple[Any, ...] = ()) -> Any:
        row = self.db.execute(statement, values).fetchone()
        assert row is not None
        return row[0]

    def begin(self, connection: psycopg.Connection[Any], role: str, key: str) -> None:
        connection.execute("SET LOCAL ROLE " + role)
        connection.execute(START, (key, self.workspace))

    def pair(self, kind: str, role: str, capability: str) -> tuple[str, uuid.UUID]:
        """Pairs a client of the owner's; returns its credential and grant."""
        key = secret(kind)
        grant = uuid.uuid4()
        with self.db.transaction():
            self.begin(self.db, "memoriesql_application", self.owner)
            self.db.execute(
                "SELECT memoriesql.pair_local_client(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    uuid.uuid4(),
                    uuid.uuid4(),
                    grant,
                    uuid.uuid4(),
                    kind,
                    role,
                    [capability],
                    [self.scope],
                    key,
                    self.now,
                    self.now + timedelta(hours=1),
                ),
            )
        return key, grant

    def claimed_task(self, agent: str, label: str) -> tuple[Any, ...]:
        """Enqueues a task as the paired agent; the worker claims it."""
        task = uuid7()
        manifest = "orchard.manifest." + label
        task_input = {
            "task_id": str(task),
            "task_kind": TASK_KIND,
            "contract_revision": 1,
            "target_reference": "orchard.receipt",
            "expected_target_revision": 0,
            "evidence_manifest": {
                "manifest_id": manifest,
                "revision": 1,
                "references": [],
            },
            "requested_effort_key": None,
            "requested_budget": None,
            "payload": {"instruction_key": "orchard.settle"},
        }
        with self.db.transaction():
            self.begin(self.db, "memoriesql_application", agent)
            self.db.execute(
                "SELECT * FROM memoriesql.enqueue_semantic_task(%s,%s,%s,%s,1,%s,%s,"
                "'orchard.receipt',0,%s,%s,%s,statement_timestamp(),NULL,"
                "statement_timestamp())",
                (
                    task,
                    "orchard." + label,
                    MODULE,
                    TASK_KIND,
                    CONTRACT_HASH,
                    REGISTRY,
                    Jsonb(task_input),
                    manifest,
                    self.scope,
                ),
            )
        with self.db.transaction():
            self.begin(self.db, "memoriesql_worker", self.worker)
            claimed = self.db.execute(
                "SELECT tenant_id, task_id, attempt_id, lease_generation"
                " FROM memoriesql.claim_semantic_task("
                "'orchard.worker','orchard.instance',120,300,1,statement_timestamp())"
            ).fetchone()
        assert claimed is not None
        self.assertEqual(claimed[1], task)
        return tuple(claimed)

    def settle(self, connection: psycopg.Connection[Any], claimed: Any) -> str:
        with connection.transaction():
            self.begin(connection, "memoriesql_worker", self.worker)
            return str(
                connection.execute(
                    "SELECT memoriesql.record_semantic_task_outcome("
                    "%s,%s,%s,%s,'orchard.worker','orchard.instance','succeeded',"
                    "%s,'orchard-receipt',NULL,NULL,NULL,NULL,0,statement_timestamp())",
                    (*claimed, "e" * 64),
                ).fetchone()[0]  # type: ignore[index]
            )

    def revoke(self, connection: psycopg.Connection[Any], grant: uuid.UUID) -> int:
        with connection.transaction():
            self.begin(connection, "memoriesql_application", self.owner)
            return int(
                connection.execute(
                    "SELECT memoriesql.revise_pairing_grant(%s,1,%s,%s,'revoked',"
                    "statement_timestamp(),statement_timestamp() + interval '1 hour',"
                    "statement_timestamp())",
                    (grant, ["agent.run"], [self.scope]),
                ).fetchone()[0]  # type: ignore[index]
            )

    def until(self, observed: Callable[[], bool], what: str) -> None:
        deadline = time.monotonic() + 60
        while not observed():
            if time.monotonic() > deadline:
                self.fail(what + " was never observed")
            time.sleep(0.01)

    def blockers(self, pid: int) -> list[int]:
        return list(self.scalar("SELECT pg_blocking_pids(%s)", (pid,)))

    def awaited(self, pid: int) -> tuple[Any, ...] | None:
        """The one lock a backend waits for: its type, mode and identity."""
        row = self.db.execute(
            "SELECT locktype, mode, CASE locktype"
            " WHEN 'advisory' THEN ((classid::bigint << 32) | objid::bigint)::text"
            " WHEN 'transactionid' THEN transactionid::text END"
            " FROM pg_locks WHERE pid=%s AND NOT granted",
            (pid,),
        ).fetchone()
        return None if row is None else tuple(row)

    def holds_fence_shared(self, pid: int) -> bool:
        return bool(
            self.scalar(
                "SELECT count(*) FROM pg_locks WHERE pid=%s AND granted"
                " AND locktype='advisory' AND mode='ShareLock'"
                " AND (classid::bigint << 32) | objid::bigint = " + FENCE,
                (pid, self.tenant),
            )
        )

    def grant_row_locked(self, grant: uuid.UUID) -> bool:
        """Whether a settlement's foreign-key check on the grant would wait."""
        with psycopg.connect(self.url, autocommit=True) as probe:
            probe.execute("BEGIN")
            try:
                probe.execute(
                    "SELECT 1 FROM memoriesql.pairing_grants WHERE tenant_id=%s"
                    " AND pairing_grant_id=%s FOR KEY SHARE NOWAIT",
                    (self.tenant, grant),
                )
            except errors.LockNotAvailable:
                return True
            finally:
                probe.execute("ROLLBACK")
        return False

    def race(
        self, grant: uuid.UUID, claimed: Any
    ) -> tuple[Future[str], Future[int], int, int, bool]:
        """Holds a settlement inside its writes, then starts the revocation.

        Returns both calls, both backends and whether the revocation held the
        grant's row while it waited for the settlement's fence. The gate is
        released when this returns.
        """
        gate, settling, revoking = self.connect(), self.connect(), self.connect()
        settler = settling.info.backend_pid
        revoker = revoking.info.backend_pid
        gate.execute("BEGIN")
        gate.execute("SELECT pg_advisory_xact_lock(%s)", (GATE,))
        pool = ThreadPoolExecutor(max_workers=2)
        self.addCleanup(pool.shutdown)
        self.addCleanup(self.release, gate, settler, revoker)
        settled = pool.submit(self.settle, settling, claimed)
        self.until(
            lambda: self.blockers(settler) == [gate.info.backend_pid],
            "the settlement at its gate",
        )
        self.assertTrue(self.holds_fence_shared(settler))
        revoked = pool.submit(self.revoke, revoking, grant)
        self.until(
            lambda: self.blockers(revoker) == [settler],
            "the revocation waiting for the settlement",
        )
        self.assertEqual(
            self.awaited(revoker), ("advisory", "ExclusiveLock", str(self.fence))
        )
        holding = self.grant_row_locked(grant)
        self.assertFalse(settled.done() or revoked.done())
        gate.execute("COMMIT")
        return settled, revoked, settler, revoker, holding

    def release(self, holder: psycopg.Connection[Any], *waiters: int) -> None:
        """Ends whatever a failed step left waiting, so its thread can finish."""
        for pid in waiters:
            self.db.execute("SELECT pg_cancel_backend(%s)", (pid,))
        holder.execute("ROLLBACK")

    def outcome(self, claimed: Any) -> tuple[Any, ...]:
        row = self.db.execute(
            "SELECT task.status, attempt.status,"
            " memoriesql.semantic_task_origin_authorized(task.tenant_id, task.task_id)"
            " FROM memoriesql.semantic_tasks AS task"
            " JOIN memoriesql.semantic_task_attempts AS attempt USING (tenant_id, task_id)"
            " WHERE task.tenant_id=%s AND task.task_id=%s",
            (self.tenant, claimed[1]),
        ).fetchone()
        assert row is not None
        return tuple(row)

    def revisions(self, grant: uuid.UUID) -> list[tuple[int, str]]:
        return [
            (int(row[0]), str(row[1]))
            for row in self.db.execute(
                "SELECT revision, status FROM memoriesql.pairing_grant_revisions"
                " WHERE pairing_grant_id=%s ORDER BY revision",
                (grant,),
            ).fetchall()
        ]

    def gate_settlements(self) -> None:
        self.db.execute(
            "CREATE FUNCTION public.fictional_settlement_gate() RETURNS trigger"
            " LANGUAGE plpgsql AS $$ BEGIN"
            " PERFORM pg_catalog.pg_advisory_xact_lock(" + str(GATE) + ");"
            " RETURN NEW; END $$"
        )
        self.db.execute(
            "CREATE TRIGGER fictional_settlement_gate BEFORE INSERT"
            " ON memoriesql.semantic_task_events FOR EACH ROW"
            " WHEN (NEW.event_kind = 'succeeded')"
            " EXECUTE FUNCTION public.fictional_settlement_gate()"
        )

    def test_revocation_waits_for_a_settlement_without_a_deadlock(self) -> None:
        self.gate_settlements()
        agent, grant = self.pair("agent", "paired_agent", "agent.run")

        # Before the fix: the revocation locks the grant's row, then waits for
        # the settlement's fence. Released, the settlement's last task write
        # rechecks the origin grant's foreign key and waits for the revocation:
        # a cycle, which PostgreSQL would end with 40P01.
        claimed = self.claimed_task(agent, "before")
        settled, revoked, settler, revoker, holding = self.race(grant, claimed)
        self.assertTrue(holding)
        self.until(
            lambda: self.blockers(settler) == [revoker],
            "the settlement waiting for the revocation",
        )
        revoker_xid = self.scalar(
            "SELECT backend_xid::text FROM pg_stat_activity WHERE pid=%s", (revoker,)
        )
        self.assertEqual(
            self.awaited(settler), ("transactionid", "ShareLock", revoker_xid)
        )
        self.assertEqual(self.blockers(revoker), [settler])
        # End the cycle from outside; the settlement then completes alone.
        self.assertTrue(self.scalar("SELECT pg_cancel_backend(%s)", (revoker,)))
        with self.assertRaises(errors.QueryCanceled):
            revoked.result(timeout=60)
        self.assertEqual(settled.result(timeout=60), "succeeded")
        self.assertEqual(self.revisions(grant), [(1, "active")])

        migrate(self.db, expected_current_version=FIX - 1, target_version=FIX)
        # After the fix: the revocation waits for the fence before it locks the
        # grant, so the settlement completes and the revocation follows it.
        claimed = self.claimed_task(agent, "after")
        settled, revoked, settler, revoker, holding = self.race(grant, claimed)
        self.assertFalse(holding)
        self.assertEqual(settled.result(timeout=60), "succeeded")
        self.assertEqual(revoked.result(timeout=60), 2)
        self.assertEqual(self.outcome(claimed), ("succeeded", "succeeded", False))
        self.assertEqual(self.revisions(grant), [(1, "active"), (2, "revoked")])

    def test_a_settlement_after_a_revocation_never_succeeds(self) -> None:
        migrate(self.db, expected_current_version=FIX - 1, target_version=FIX)
        agent, grant = self.pair("agent", "paired_agent", "agent.run")
        claimed = self.claimed_task(agent, "revoked-first")
        revoking, settling = self.connect(), self.connect()
        settler = settling.info.backend_pid
        # The revocation takes the fence and holds it, uncommitted.
        revoking.execute("BEGIN")
        self.begin(revoking, "memoriesql_application", self.owner)
        self.assertEqual(
            revoking.execute(
                "SELECT memoriesql.revise_pairing_grant(%s,1,%s,%s,'revoked',"
                "statement_timestamp(),statement_timestamp() + interval '1 hour',"
                "statement_timestamp())",
                (grant, ["agent.run"], [self.scope]),
            ).fetchone(),
            (2,),
        )
        pool = ThreadPoolExecutor(max_workers=1)
        self.addCleanup(pool.shutdown)
        self.addCleanup(self.release, revoking, settler)
        settled = pool.submit(self.settle, settling, claimed)
        self.until(
            lambda: self.blockers(settler) == [revoking.info.backend_pid],
            "the settlement waiting for the revocation",
        )
        self.assertEqual(
            self.awaited(settler), ("advisory", "ShareLock", str(self.fence))
        )
        revoking.execute("COMMIT")
        # Its final check runs after the revocation commits.
        self.assertNotEqual(settled.result(timeout=60), "succeeded")
        task_status, attempt_status, authorized = self.outcome(claimed)
        self.assertNotEqual((task_status, attempt_status), ("succeeded", "succeeded"))
        self.assertFalse(authorized)
        self.assertEqual(self.revisions(grant), [(1, "active"), (2, "revoked")])


if __name__ == "__main__":
    unittest.main()
