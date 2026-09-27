"""Installed schema-31 preparation on fictional data; no agent disclosure API."""

from __future__ import annotations

import concurrent.futures
import hashlib
import os
import threading
import unittest
from datetime import timedelta
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from memoriesql.infrastructure.postgres.authorization import PostgresAuthorizationPort
from memoriesql.infrastructure.postgres.relation_projection import (
    relation_projection_frame,
)
from memoriesql.infrastructure.postgres.result_preparation import (
    PostgresResultPreparation,
    PreparationPin,
    PreparedContent,
)

if TYPE_CHECKING:
    from tests.runtime.test_postgres_runtime import PostgresRuntime, migrate
else:
    from test_postgres_runtime import PostgresRuntime, migrate


@unittest.skipUnless(
    os.environ.get("N1_TEST_DATABASE_URL"), "disposable PostgreSQL required"
)
class ResultPreparation(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = PostgresRuntime()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.db = self.fixture.db
        self.tenant, self.workspace = self.fixture.tenant, self.fixture.workspace
        migrate(self.db, expected_current_version=14, target_version=31)
        self.store = self.adapter(self.db)
        self.run_ref = uuid4()
        self.content = PreparedContent.encode(
            content={"rows": [["fictional orchard", "2", None]], "limited": False},
            witnesses={
                "joined_members": ["one", "one"],
                "negative_population": ["other"],
            },
            dependencies=[
                {
                    "kind": "fictional",
                    "id": str(uuid4()),
                    "row": {"text": "\u00e9\U0001f680"},
                }
            ],
        )

    def adapter(self, connection: Any) -> PostgresResultPreparation:
        return PostgresResultPreparation(
            connection,
            credential_sha256=self.fixture.secret_hash,
            workspace_id=self.workspace,
        )

    def reserve(
        self, *, step: Any = None, run: Any = None, capacity: int = 16384
    ) -> Any:
        return self.store.reserve(
            run_ref=run or self.run_ref,
            step_key=step or uuid4(),
            request_fingerprint=hashlib.sha256(b"fictional request").hexdigest(),
            reservation_bytes=capacity,
        )

    def seal(
        self,
        slot: Any,
        *,
        artifact: Any = None,
        parents: tuple[PreparationPin, ...] = (),
    ) -> Any:
        ref = artifact or uuid4()
        return ref, self.store.seal(
            operation_ref=slot.operation_ref,
            ownership_ref=slot.ownership_ref,
            artifact_ref=ref,
            content=self.content,
            parents=parents,
        )

    def scalar(self, query: str, values: tuple[Any, ...] = ()) -> Any:
        row = self.db.execute(query, values).fetchone()
        assert row is not None
        return row[0]

    def test_restart_and_lost_response_recover_original_private_identity_without_query(
        self,
    ) -> None:
        step = uuid4()
        slot = self.reserve(step=step)
        artifact, receipt = self.seal(slot)
        self.assertEqual(receipt.state, "sealed")
        with psycopg.connect(self.db.info.dsn, autocommit=True) as restarted:
            recovered = self.adapter(restarted).reserve(
                run_ref=self.run_ref,
                step_key=step,
                request_fingerprint=hashlib.sha256(b"fictional request").hexdigest(),
                reservation_bytes=16384,
            )
            self.assertTrue(recovered.replayed)
            self.assertEqual(recovered.operation_ref, slot.operation_ref)
            self.assertEqual(recovered.ownership_ref, slot.ownership_ref)
            exact = self.adapter(restarted).seal(
                operation_ref=slot.operation_ref,
                ownership_ref=slot.ownership_ref,
                artifact_ref=artifact,
                content=self.content,
            )
            self.assertTrue(exact.replayed)
        row = self.db.execute(
            "SELECT content_bytes,witness_bytes,dependency_bytes,artifact_sha256 FROM memoriesql.result_preparation_artifacts"
        ).fetchone()
        assert row is not None
        self.assertEqual(
            tuple(bytes(value) for value in row[:3]),
            (self.content.content, self.content.witnesses, self.content.dependencies),
        )
        self.assertEqual(row[3], self.content.artifact_sha256)
        self.assertEqual(
            self.scalar(
                "SELECT count(*) FROM memoriesql.result_preparation_operations"
            ),
            1,
        )
        self.assertFalse(hasattr(self.store, "read"))

    def test_request_content_or_parent_substitution_never_overwrites_sealed_bytes(
        self,
    ) -> None:
        step = uuid4()
        slot = self.reserve(step=step)
        artifact, _ = self.seal(slot)
        with self.assertRaises(psycopg.errors.UniqueViolation):
            self.store.reserve(
                run_ref=self.run_ref,
                step_key=step,
                request_fingerprint="f" * 64,
                reservation_bytes=16384,
            )
        with self.assertRaises(psycopg.errors.UniqueViolation):
            self.store.seal(
                operation_ref=slot.operation_ref,
                ownership_ref=slot.ownership_ref,
                artifact_ref=artifact,
                content=PreparedContent.encode(
                    content=[], witnesses=[], dependencies=[]
                ),
            )
        with self.assertRaises(psycopg.errors.UniqueViolation):
            self.seal(slot, artifact=uuid4())
        self.assertEqual(
            self.scalar(
                "SELECT artifact_sha256 FROM memoriesql.result_preparation_artifacts"
            ),
            self.content.artifact_sha256,
        )

    def test_crashes_between_body_holds_and_receipt_never_publish_partial_preparation(
        self,
    ) -> None:
        parent_slot = self.reserve()
        parent, _ = self.seal(parent_slot)
        pin = PreparationPin(parent, self.content.artifact_sha256)
        for table, timing, condition in (
            ("result_preparation_artifacts", "AFTER INSERT", "true"),
            ("result_preparation_parent_holds", "BEFORE INSERT", "true"),
            ("result_preparation_operations", "BEFORE UPDATE", "NEW.state='sealed'"),
        ):
            with self.subTest(stage=table):
                slot = self.reserve()
                artifact = uuid4()
                self.db.execute(
                    "CREATE OR REPLACE FUNCTION memoriesql.fixture_preparation_crash() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'fictional crash'; END $$"
                )
                self.db.execute(
                    sql.SQL(
                        "CREATE TRIGGER fixture_crash {} ON memoriesql.{} FOR EACH ROW WHEN ({}) EXECUTE FUNCTION memoriesql.fixture_preparation_crash()"
                    ).format(sql.SQL(timing), sql.Identifier(table), sql.SQL(condition))
                )
                try:
                    with self.assertRaises(psycopg.errors.RaiseException):
                        self.seal(slot, artifact=artifact, parents=(pin,))
                finally:
                    self.db.execute(
                        sql.SQL("DROP TRIGGER fixture_crash ON memoriesql.{}").format(
                            sql.Identifier(table)
                        )
                    )
                self.assertEqual(
                    self.scalar(
                        "SELECT count(*) FROM memoriesql.result_preparation_artifacts WHERE artifact_ref=%s",
                        (artifact,),
                    ),
                    0,
                )
                self.assertEqual(
                    self.scalar(
                        "SELECT count(*) FROM memoriesql.result_preparation_parent_holds WHERE child_ref=%s",
                        (artifact,),
                    ),
                    0,
                )
                self.assertEqual(
                    self.scalar(
                        "SELECT state FROM memoriesql.result_preparation_operations WHERE operation_ref=%s",
                        (slot.operation_ref,),
                    ),
                    "reserved",
                )
                _, fixed = self.seal(slot, artifact=artifact, parents=(pin,))
                self.assertEqual(fixed.state, "sealed")

    def test_parent_holds_and_deep_continuation_preserve_original_bodies(self) -> None:
        parent_slot = self.reserve()
        root, _ = self.seal(parent_slot)
        previous = root
        slots = []
        for _ in range(24):
            slot = self.reserve()
            previous, _ = self.seal(
                slot, parents=(PreparationPin(previous, self.content.artifact_sha256),)
            )
            slots.append(slot)
        with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState):
            self.store.discard(
                operation_ref=parent_slot.operation_ref,
                ownership_ref=parent_slot.ownership_ref,
            )
        self.assertEqual(
            self.scalar(
                "SELECT content_bytes FROM memoriesql.result_preparation_artifacts WHERE artifact_ref=%s",
                (root,),
            ),
            self.content.content,
        )
        for slot in reversed(slots):
            self.store.discard(
                operation_ref=slot.operation_ref, ownership_ref=slot.ownership_ref
            )
        released = self.store.discard(
            operation_ref=parent_slot.operation_ref,
            ownership_ref=parent_slot.ownership_ref,
        )
        self.assertEqual(released.state, "discarded")
        self.assertEqual(released.allocation_bytes, 8192)
        self.assertEqual(
            self.scalar("SELECT count(*) FROM memoriesql.result_preparation_artifacts"),
            0,
        )
        self.assertEqual(
            self.scalar(
                "SELECT count(*) FROM memoriesql.result_preparation_parent_holds"
            ),
            0,
        )

    def test_invalid_parent_digest_or_ownership_and_null_cannot_seal_or_discard(
        self,
    ) -> None:
        p = self.reserve()
        parent, _ = self.seal(p)
        child = self.reserve()
        for pin in (
            PreparationPin(parent, "0" * 64),
            PreparationPin(uuid4(), self.content.artifact_sha256),
        ):
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                self.seal(child, parents=(pin,))
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            self.store.discard(operation_ref=p.operation_ref, ownership_ref=uuid4())
        with relation_projection_frame(
            self.db,
            credential_sha256=self.fixture.secret_hash,
            workspace_id=self.workspace,
        ) as frame:
            with self.assertRaises(psycopg.errors.InvalidParameterValue):
                with frame.transaction():
                    frame.execute(
                        "SELECT memoriesql.discard_result_preparation_v1(%s,NULL)",
                        (p.operation_ref,),
                    )

    def test_overflow_retains_owned_reservation_and_discard_never_resets_new_allocation(
        self,
    ) -> None:
        slot = self.reserve(capacity=8192)
        with self.assertRaises(psycopg.errors.ProgramLimitExceeded):
            self.seal(slot)
        self.assertEqual(
            self.scalar("SELECT state FROM memoriesql.result_preparation_operations"),
            "reserved",
        )
        self.assertEqual(
            self.scalar("SELECT count(*) FROM memoriesql.result_preparation_artifacts"),
            0,
        )
        self.store.discard(
            operation_ref=slot.operation_ref, ownership_ref=slot.ownership_ref
        )
        successful = self.reserve()
        _, sealed = self.seal(successful)
        self.store.discard(
            operation_ref=successful.operation_ref,
            ownership_ref=successful.ownership_ref,
        )
        self.assertEqual(
            self.scalar(
                "SELECT new_allocation_bytes FROM memoriesql.result_preparation_operations WHERE operation_ref=%s",
                (successful.operation_ref,),
            ),
            sealed.allocation_bytes,
        )
        self.assertIsNone(
            self.scalar(
                "SELECT request_fingerprint FROM memoriesql.result_preparation_operations WHERE operation_ref=%s",
                (successful.operation_ref,),
            )
        )
        self.assertTrue(
            self.store.discard(
                operation_ref=successful.operation_ref,
                ownership_ref=successful.ownership_ref,
            ).replayed
        )

    def test_stale_snapshot_cannot_overadmit_after_allocation_lock_wait(self) -> None:
        self.reserve(capacity=64 * 1024 * 1024)
        with psycopg.connect(self.db.info.dsn, autocommit=True) as stale:
            with self.assertRaises(psycopg.errors.SerializationFailure):
                with relation_projection_frame(
                    stale,
                    credential_sha256=self.fixture.secret_hash,
                    workspace_id=self.workspace,
                ) as frame:
                    self.reserve(capacity=64 * 1024 * 1024)
                    frame.execute(
                        "SELECT memoriesql.reserve_result_preparation_v1(%s,%s,%s,%s)",
                        (self.run_ref, uuid4(), "f" * 64, 8192),
                    )
        with self.assertRaises(psycopg.errors.ProgramLimitExceeded):
            self.reserve(capacity=8192)
        self.assertEqual(
            self.scalar(
                "SELECT count(*) FROM memoriesql.result_preparation_operations"
            ),
            2,
        )

    def test_concurrent_identical_reservations_have_one_owned_journal_entry(
        self,
    ) -> None:
        barrier = threading.Barrier(2)
        step = uuid4()

        def reserve() -> Any:
            with psycopg.connect(self.db.info.dsn, autocommit=True) as db:
                store = self.adapter(db)
                barrier.wait(timeout=5)
                for _ in range(3):
                    try:
                        return store.reserve(
                            run_ref=self.run_ref,
                            step_key=step,
                            request_fingerprint="f" * 64,
                            reservation_bytes=16384,
                        )
                    except psycopg.errors.SerializationFailure:
                        pass
                raise AssertionError("private recovery did not converge")

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(reserve) for _ in range(2)]
            receipts = [f.result(timeout=10) for f in futures]
        self.assertEqual(len({r.operation_ref for r in receipts}), 1)
        self.assertEqual(len({r.ownership_ref for r in receipts}), 1)
        self.assertEqual(sorted(r.replayed for r in receipts), [False, True])
        self.assertEqual(
            self.scalar(
                "SELECT count(*) FROM memoriesql.result_preparation_operations"
            ),
            1,
        )

    def test_private_storage_needs_authentication_and_qualified_shared_frame(
        self,
    ) -> None:
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            with self.db.transaction():
                self.db.execute("SET LOCAL ROLE memoriesql_application")
                PostgresAuthorizationPort(self.db).begin_context(
                    credential_sha256=self.fixture.secret_hash,
                    requested_workspace_id=self.workspace,
                )
                self.db.execute(
                    "SELECT memoriesql.reserve_result_preparation_v1(%s,%s,%s,%s)",
                    (self.run_ref, uuid4(), "f" * 64, 16384),
                )
        with self.assertRaises(RuntimeError):
            with self.db.transaction():
                self.reserve()
        self.assertEqual(
            self.scalar(
                "SELECT count(*) FROM memoriesql.result_preparation_operations"
            ),
            0,
        )

    def test_second_tenant_cannot_guess_owned_reservation_or_reuse_foreign_parent(
        self,
    ) -> None:
        slot = self.reserve()
        parent, _ = self.seal(slot)
        ids = [uuid4() for _ in range(9)]
        secret = hashlib.sha256(b"other fictional tenant").hexdigest()
        with self.db.transaction():
            self.db.execute("SET LOCAL ROLE memoriesql_application")
            self.db.execute(
                "SELECT memoriesql.bootstrap_personal_local(%s,%s,%s,%s,%s,%s,%s,%s,%s,'memoriesql.local',%s,'Other fictional tenant',%s,%s,%s)",
                (
                    *ids,
                    str(ids[2]),
                    secret,
                    self.fixture.now,
                    self.fixture.now + timedelta(hours=1),
                ),
            )
        other = PostgresResultPreparation(
            self.db, credential_sha256=secret, workspace_id=ids[4]
        )
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            other.discard(
                operation_ref=slot.operation_ref, ownership_ref=slot.ownership_ref
            )
        child = other.reserve(
            run_ref=uuid4(),
            step_key=uuid4(),
            request_fingerprint="f" * 64,
            reservation_bytes=16384,
        )
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            other.seal(
                operation_ref=child.operation_ref,
                ownership_ref=child.ownership_ref,
                artifact_ref=uuid4(),
                content=self.content,
                parents=(PreparationPin(parent, self.content.artifact_sha256),),
            )

    def test_application_cannot_read_tables_or_update_sealed_content(self) -> None:
        slot = self.reserve()
        self.seal(slot)
        for table in (
            "result_preparation_operations",
            "result_preparation_artifacts",
            "result_preparation_parent_holds",
            "result_preparation_allocators",
        ):
            with self.subTest(table=table):
                with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                    with self.db.transaction():
                        self.db.execute("SET LOCAL ROLE memoriesql_application")
                        self.db.execute(
                            sql.SQL("SELECT * FROM memoriesql.{}").format(
                                sql.Identifier(table)
                            )
                        )
        with self.assertRaises(psycopg.errors.RaiseException):
            self.db.execute(
                "UPDATE memoriesql.result_preparation_artifacts SET content_bytes='changed'::bytea"
            )
        public_grants = self.scalar(
            "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a WHERE n.nspname='memoriesql' AND p.proname LIKE '%result_preparation%v1' AND a.grantee=0 AND a.privilege_type='EXECUTE'"
        )
        self.assertEqual(public_grants, 0)

    def test_raw_unprivileged_login_cannot_call_bookkeeping_or_elevate(self) -> None:
        reader = "fictional_preparation_reader_" + uuid4().hex
        self.db.execute(
            sql.SQL(
                "CREATE ROLE {} LOGIN PASSWORD 'fictional-only' NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION"
            ).format(sql.Identifier(reader))
        )
        try:
            self.db.execute(
                sql.SQL("GRANT USAGE ON SCHEMA memoriesql TO {}").format(
                    sql.Identifier(reader)
                )
            )
            with psycopg.connect(
                make_conninfo(self.db.info.dsn, user=reader, password="fictional-only"),
                autocommit=True,
            ) as unprivileged:
                for statement, params in (
                    (
                        "SELECT memoriesql.reserve_result_preparation_v1(%s,%s,%s,%s)",
                        (self.run_ref, uuid4(), "f" * 64, 16384),
                    ),
                    ("SELECT * FROM memoriesql.result_preparation_artifacts", ()),
                    ("SET ROLE memoriesql_application", ()),
                ):
                    with self.subTest(statement=statement):
                        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                            unprivileged.execute(statement, params)
        finally:
            self.db.execute(
                sql.SQL("REVOKE ALL ON SCHEMA memoriesql FROM {}").format(
                    sql.Identifier(reader)
                )
            )
            self.db.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(reader)))

    def test_raw_digest_mismatch_rolls_back_before_any_artifact_or_receipt(
        self,
    ) -> None:
        slot = self.reserve()
        with self.assertRaises(psycopg.errors.InvalidParameterValue):
            with relation_projection_frame(
                self.db,
                credential_sha256=self.fixture.secret_hash,
                workspace_id=self.workspace,
            ) as frame:
                frame.execute(
                    "SELECT memoriesql.seal_result_preparation_v1(%s,%s,%s,%s,%s,%s,%s,'[]')",
                    (
                        slot.operation_ref,
                        slot.ownership_ref,
                        uuid4(),
                        self.content.content,
                        self.content.witnesses,
                        self.content.dependencies,
                        "0" * 64,
                    ),
                )
        self.assertEqual(
            self.scalar("SELECT count(*) FROM memoriesql.result_preparation_artifacts"),
            0,
        )
        self.assertEqual(
            self.scalar("SELECT state FROM memoriesql.result_preparation_operations"),
            "reserved",
        )
