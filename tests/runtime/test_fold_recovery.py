"""Recovery only: fictional evidence, service authority, no semantic side effects."""

from __future__ import annotations

import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import psycopg
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from memoriesql.application.capture.transcript_fold import (
    DurableTranscriptReadRequest,
    TranscriptFoldOutcomeKind,
)
from memoriesql.application.fold_recovery import (
    DiscoverFoldOutcomes,
    FoldDiscoveryCursor,
    FoldOutcomeKey,
    InspectFoldOutcome,
    PageFoldLineage,
    ReadFoldEvidence,
)
from memoriesql.infrastructure.postgres.fold_recovery import PostgresFoldRecovery
from memoriesql.infrastructure.postgres.migration_runner import migrate

if TYPE_CHECKING:
    from tests.runtime.fold_recovery_fixture import digest, insert, seed, service
    from tests.runtime.test_postgres_runtime import PostgresRuntime
else:
    from fold_recovery_fixture import digest, insert, seed, service
    from test_postgres_runtime import PostgresRuntime


class FoldRecovery(PostgresRuntime):
    def setUp(self) -> None:
        super().setUp()
        migrate(self.db, expected_current_version=14, target_version=19)
        self.secret, self.grant = service(self)
        self.api = self.reader()

    def reader(
        self, connection: Any = None, secret: str | None = None
    ) -> PostgresFoldRecovery:
        return PostgresFoldRecovery(
            connection or self.db,
            credential_sha256=secret or self.secret,
            workspace_id=self.workspace,
        )

    def connect(self) -> Any:
        return psycopg.connect(
            make_conninfo(self.admin, dbname=self.database), autocommit=True
        )

    def state(self) -> dict[str, Any]:
        tables = (
            "capture_checkpoints",
            "source_range_capture_receipts",
            "captured_source_ranges",
            "transcript_fold_receipts",
            "transcript_fold_outcomes",
            "transcript_fold_outcome_ranges",
            "transcript_fold_exact_turns",
            "source_events",
            "source_units",
            "beads",
            "semantic_tasks",
            "outbox_events",
            "idempotency_receipts",
            "evidence_packages",
        )
        return {
            table: self.db.execute(
                "SELECT jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text) FROM memoriesql."
                + table
                + " t"
            ).fetchone()[0]  # type: ignore[index]
            for table in tables
        }

    def read_all(self, key: FoldOutcomeKey, **selection: Any) -> bytes:
        offset = 0
        result = b""
        while True:
            page = self.api.read(
                ReadFoldEvidence(key=key, byte_offset=offset, **selection)
            )
            result += page.content
            if page.next_byte_offset is None:
                break
            offset = page.next_byte_offset
        return result

    def test_all_facts_unicode_windows_pending_tail_and_no_side_effects(self) -> None:
        keys = seed(self)
        before = self.state()
        # Discovery has no revision/file context. Each call owns a new transaction.
        with self.connect() as fresh:
            page = self.reader(fresh).discover(
                DiscoverFoldOutcomes(source_object_id=self.source)
            )
        self.assertEqual([v.key for v in page.outcomes], keys)
        for key in keys:
            detail = self.api.inspect(InspectFoldOutcome(key=key))
            self.assertEqual(detail.source_revision_key, "orchard.revision.1")
            self.assertEqual(detail.file_identity.inode, 1)
            self.assertEqual(detail.connector_id, "orchard.synthetic")
            self.assertEqual(detail.source_qualification, "not_established")
            ranges: list[Any] = []
            ordinal = 0
            while True:
                p = self.api.lineage(
                    PageFoldLineage(key=key, next_ordinal=ordinal, limit=1)
                )
                ranges.extend(p.ranges)
                if p.next_ordinal is None:
                    break
                ordinal = p.next_ordinal
            raw = b"".join(
                self.read_all(
                    key,
                    evidence="raw_lineage",
                    lineage_ordinal=r.lineage_ordinal,
                    expected_sha256=r.source_bytes_sha256,
                    max_bytes=1,
                )
                for r in ranges
            )
            self.assertEqual(raw, '{"text":"orchard 🌳 café"}\n'.encode())
            self.assertEqual(digest(raw), detail.source_bytes_sha256)
            self.assertGreater(
                ranges[-1].receipt_byte_end_exclusive, detail.byte_end_exclusive
            )
            if detail.outcome_kind == "exact_turn":
                data = self.read_all(
                    key,
                    evidence="exact_envelope",
                    expected_sha256=detail.exact_envelope_sha256,
                )
                self.assertEqual(digest(data), detail.exact_envelope_sha256)
                self.assertEqual(json.loads(data)["parent_native_id"], "orchard.parent")
                self.assertEqual(json.loads(data)["padding"], "é" * 70000)
                self.assertNotEqual(digest(raw), digest(data))
            elif detail.outcome_kind == "transcript_span":
                assert detail.transcript_span is not None
                self.assertEqual(detail.transcript_span.topology_status, "unknown")
            else:
                assert detail.policy_disposition is not None
                self.assertEqual(detail.policy_disposition.scope_reason, "off_date")
        self.assertEqual(self.state(), before)

    def test_revision_identity_snapshot_restart_append_and_point_selection(
        self,
    ) -> None:
        a = seed(self)
        b = seed(self, 2)
        first = self.api.discover(
            DiscoverFoldOutcomes(source_object_id=self.source, limit=2)
        )
        c = seed(self, 3)
        cursor = first.continuation
        found = [x.key for x in first.outcomes]
        while cursor:
            with self.connect() as db:
                page = self.reader(db).discover(
                    DiscoverFoldOutcomes(
                        source_object_id=self.source,
                        continuation=FoldDiscoveryCursor.model_validate_json(
                            cursor.model_dump_json()
                        ),
                        limit=2,
                    )
                )
            found.extend(x.key for x in page.outcomes)
            cursor = page.continuation
        self.assertEqual(found, a + b)
        self.assertEqual(
            [
                x.key
                for x in self.api.discover(
                    DiscoverFoldOutcomes(
                        source_object_id=self.source, after=page.resume_after
                    )
                ).outcomes
            ],
            c,
        )
        for key in [a[0], b[0], c[0]]:
            self.assertEqual(self.api.inspect(InspectFoldOutcome(key=key)).key, key)
        self.assertEqual(
            self.api.discover(
                DiscoverFoldOutcomes(source_object_id=self.source, after=c[-1])
            ).outcomes,
            (),
        )
        with self.assertRaises(ValidationError):
            DiscoverFoldOutcomes(source_object_id=uuid.uuid4(), after=a[0])
        with self.assertRaises(psycopg.Error):
            self.api.discover(
                DiscoverFoldOutcomes(
                    source_object_id=self.source,
                    continuation=FoldDiscoveryCursor(
                        through_receipt_id=uuid.uuid4(), after=a[0]
                    ),
                )
            )
        with self.assertRaises(psycopg.Error):
            self.api.inspect(
                InspectFoldOutcome(key=a[0].model_copy(update={"outcome_ordinal": 250}))
            )
        with self.assertRaises(psycopg.Error):
            self.api.read(
                ReadFoldEvidence(
                    key=a[0], evidence="exact_envelope", expected_sha256="0" * 64
                )
            )
        before = self.state()
        with self.assertRaises(psycopg.Error):
            self.api.inspect(
                InspectFoldOutcome(
                    key=a[0].model_copy(update={"source_object_id": uuid.uuid4()})
                )
            )
        self.assertEqual(before, self.state())

    def revoke(self, connection: Any) -> None:
        from memoriesql.infrastructure.postgres.authorization import (
            PostgresAuthorizationPort,
        )

        connection.execute("SET LOCAL ROLE memoriesql_application")
        PostgresAuthorizationPort(connection).begin_context(
            credential_sha256=self.secret_hash, requested_workspace_id=self.workspace
        )
        connection.execute(
            "SELECT memoriesql.revise_pairing_grant(%s,1,%s,%s,'revoked',%s,%s,%s)",
            (
                self.grant,
                ["source.raw.read"],
                [self.scope],
                self.now,
                self.now + timedelta(hours=1),
                datetime.now(UTC),
            ),
        )

    def test_missing_grant_revocation_and_legacy_human_fence(self) -> None:
        from memoriesql.infrastructure.postgres.transcript_fold import (
            PostgresAuthorizedTranscriptFoldSession,
        )

        key = seed(self)[0]
        denied, _ = service(self, ["source.read"])
        with self.assertRaises(psycopg.Error):
            self.reader(secret=denied).inspect(InspectFoldOutcome(key=key))
        self.api.inspect(InspectFoldOutcome(key=key))
        with self.assertRaisesRegex(
            RuntimeError, "did not return a transcript fold window"
        ):
            PostgresAuthorizedTranscriptFoldSession(
                self.db, credential_sha256=self.secret, workspace_id=self.workspace
            ).read(
                DurableTranscriptReadRequest(
                    source_object_id=self.source,
                    source_revision_key="orchard.revision.1",
                    file_identity_key="fictional:7:1:unknown",
                    byte_start=0,
                    max_bytes=1,
                )
            )
        with self.db.transaction():
            self.revoke(self.db)
        with self.assertRaises((psycopg.Error, PermissionError)):
            self.api.inspect(InspectFoldOutcome(key=key))

    def wait_for_lock(self, pid: int, event: str = "advisory") -> None:
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            row = self.db.execute(
                "SELECT wait_event FROM pg_stat_activity WHERE pid=%s", (pid,)
            ).fetchone()
            if row and row[0] == event:
                return
            time.sleep(0.005)
        self.fail("operation never demonstrably waited on the expected lock")

    def test_revocation_wins_during_authority_wait(self) -> None:
        key = seed(self)[0]
        before = self.state()
        with (
            self.connect() as blocker,
            self.connect() as reader,
            ThreadPoolExecutor(1) as pool,
        ):
            with blocker.transaction():
                blocker.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                    (str(self.tenant) + ":semantic_outcome_authority:",),
                )
                future = pool.submit(
                    self.reader(reader).inspect, InspectFoldOutcome(key=key)
                )
                self.wait_for_lock(reader.info.backend_pid)
                self.revoke(blocker)
            with self.assertRaises((psycopg.Error, PermissionError)):
                future.result(timeout=3)
        self.assertEqual(before, self.state())

    def test_lock_timeout_and_strong_snapshot_fail_closed(self) -> None:
        key = seed(self)[0]
        with self.connect() as blocker, blocker.transaction():
            blocker.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                (str(self.tenant) + ":semantic_outcome_authority:",),
            )
            start = time.monotonic()
            with self.assertRaises(psycopg.errors.LockNotAvailable):
                self.api.inspect(InspectFoldOutcome(key=key))
            self.assertLess(time.monotonic() - start, 1.5)
        with (
            self.assertRaises(psycopg.errors.ActiveSqlTransaction),
            self.db.transaction(),
        ):
            self.db.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            self.begin()
            self.db.execute(
                "SELECT memoriesql.recover_transcript_fold_v1(%s)",
                (Jsonb(InspectFoldOutcome(key=key).model_dump(mode="json")),),
            )

    def test_concurrent_append_commit_order_and_rollback(self) -> None:
        initial = seed(self)
        with (
            self.connect() as blocker,
            self.connect() as a,
            self.connect() as b,
            ThreadPoolExecutor(2) as pool,
        ):
            with blocker.transaction():
                blocker.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                    (str(self.tenant) + ":fold-recovery:" + str(self.source),),
                )
                first = pool.submit(seed, self, 2, db=a)
                self.wait_for_lock(a.info.backend_pid)
                second = pool.submit(seed, self, 3, db=b)
                self.wait_for_lock(b.info.backend_pid)
                page = self.api.discover(
                    DiscoverFoldOutcomes(source_object_id=self.source)
                )
                self.assertEqual([v.key for v in page.outcomes], initial)
            expected = set(first.result(timeout=4) + second.result(timeout=4))
        page = self.api.discover(
            DiscoverFoldOutcomes(source_object_id=self.source, after=page.resume_after)
        )
        self.assertEqual(set(v.key for v in page.outcomes), expected)
        self.assertEqual(len(page.outcomes), 6)
        before = self.db.execute(
            "SELECT count(*) FROM memoriesql.transcript_fold_receipts"
        ).fetchone()
        with self.assertRaises(psycopg.Error):
            seed(self, 4, kinds=("invalid_kind",))
        self.assertEqual(
            before,
            self.db.execute(
                "SELECT count(*) FROM memoriesql.transcript_fold_receipts"
            ).fetchone(),
        )
        self.assertEqual(
            self.api.discover(
                DiscoverFoldOutcomes(
                    source_object_id=self.source, after=page.resume_after
                )
            ).outcomes,
            (),
        )

    def test_missing_lineage_or_chunks_fails_explicitly(self) -> None:
        key = seed(self)[0]
        for table, where in [
            ("transcript_fold_outcome_ranges", "lineage_ordinal=2"),
            ("captured_source_ranges", "true"),
            ("transcript_fold_exact_turns", "true"),
        ]:
            with (
                self.assertRaisesRegex(psycopg.Error, "fold_recovery_unavailable"),
                self.db.transaction(),
            ):
                self.db.execute(
                    "ALTER TABLE memoriesql." + table + " DISABLE TRIGGER USER"
                )
                self.db.execute("DELETE FROM memoriesql." + table + " WHERE " + where)
                self.begin()
                request: Any
                if table == "transcript_fold_outcome_ranges":
                    request = PageFoldLineage(key=key)
                elif table == "transcript_fold_exact_turns":
                    request = InspectFoldOutcome(key=key)
                else:
                    request = ReadFoldEvidence(
                        key=key,
                        evidence="raw_lineage",
                        lineage_ordinal=0,
                        expected_sha256="0" * 64,
                    )
                self.db.execute(
                    "SELECT memoriesql.recover_transcript_fold_v1(%s)",
                    (Jsonb(request.model_dump(mode="json")),),
                )
        self.api.inspect(InspectFoldOutcome(key=key))

    def test_public_fold_command_and_replay_preserve_receipt(self) -> None:
        from memoriesql.application.capture.contracts import KernelCaptureBinding
        from memoriesql.application.capture.host_protocol import FileIdentity
        from memoriesql.application.capture.transcript_fold import (
            DurableTranscriptReadRequest,
            TranscriptFoldOutcome,
            TranscriptSpanEvidence,
            build_transcript_fold_command,
        )
        from memoriesql.infrastructure.postgres.transcript_fold import (
            PostgresAuthorizedTranscriptFoldSession,
        )

        seed(self, 1, kinds=())
        content = '{"text":"orchard 🌳 café"}\n'.encode()
        identity = FileIdentity(platform="fictional", device=7, inode=1)
        session = PostgresAuthorizedTranscriptFoldSession(
            self.db, credential_sha256=self.secret_hash, workspace_id=self.workspace
        )
        retained = session.read(
            DurableTranscriptReadRequest(
                source_object_id=self.source,
                source_revision_key="orchard.revision.1",
                file_identity_key=identity.stable_key,
                byte_start=0,
                max_bytes=len(content),
            )
        )
        facts: dict[str, Any] = dict(
            byte_start=0,
            byte_end_exclusive=len(content),
            start_record_index=0,
            end_record_index=1,
            source_bytes_sha256=digest(content),
        )
        command = build_transcript_fold_command(
            binding=KernelCaptureBinding(
                tenant_id=self.tenant,
                workspace_id=self.workspace,
                access_scope_id=self.scope,
                source_object_id=self.source,
                expected_source_object_schema_version=1,
            ),
            capability_id="orchard.capture",
            connector_id="orchard.synthetic",
            adapter_profile_version="orchard.v1",
            observed_source_format_version="orchard.v1",
            source_revision_key="orchard.revision.1",
            file_identity=identity,
            outcomes=(
                TranscriptFoldOutcome(
                    outcome_kind=TranscriptFoldOutcomeKind.TRANSCRIPT_SPAN,
                    **facts,
                    lineage=retained.ranges,
                    transcript_span=TranscriptSpanEvidence(
                        source_object_id=self.source,
                        source_revision_key="orchard.revision.1",
                        file_identity_key=identity.stable_key,
                        **facts,
                    ),
                ),
            ),
            source_bytes=content,
            checkpoint_key="orchard.fold.public",
            expected_checkpoint_sequence=0,
        )
        receipt = session.commit(command, recorded_at=self.now)
        before = self.state()
        self.assertEqual(
            session.commit(command, recorded_at=self.now).transcript_fold_receipt_id,
            receipt.transcript_fold_receipt_id,
        )
        page = self.api.discover(DiscoverFoldOutcomes(source_object_id=self.source))
        self.assertEqual(len(page.outcomes), 1)
        self.assertEqual(
            page.outcomes[0].key.fold_receipt_id, receipt.transcript_fold_receipt_id
        )
        self.api.inspect(InspectFoldOutcome(key=page.outcomes[0].key))
        self.assertEqual(self.state(), before)

    def test_many_part_reader_and_indexed_deep_discovery_work(self) -> None:
        import tracemalloc

        from psycopg import sql

        database: Any = self.db
        content = ("🌳é漢字" * 8000 + "\n").encode()
        key = seed(self, 1, kinds=("transcript_span",), payload=content, windows=64)[0]

        # Count adapter execute calls independently from SQL internal row work.
        class Counted:
            def __init__(self, db: Any) -> None:
                self.db = db
                self.calls = 0

            @property
            def info(self) -> Any:
                return self.db.info

            def transaction(self) -> Any:
                return self.db.transaction()

            def execute(self, *args: Any, **kwargs: Any) -> Any:
                self.calls += 1
                return database.execute(*args, **kwargs)

        counted: Any = Counted(self.db)
        reader = self.reader(counted)
        started = time.monotonic()
        tracemalloc.start()
        parts: list[Any] = []
        page: Any
        ordinal = 0
        operations = 0
        recovered = b""
        while True:
            page = reader.lineage(PageFoldLineage(key=key, next_ordinal=ordinal))
            operations += 1
            parts.extend(page.ranges)
            if page.next_ordinal is None:
                break
            ordinal = page.next_ordinal
        for part in parts:
            page = reader.read(
                ReadFoldEvidence(
                    key=key,
                    evidence="raw_lineage",
                    lineage_ordinal=part.lineage_ordinal,
                    expected_sha256=part.source_bytes_sha256,
                )
            )
            operations += 1
            recovered += page.content
        self.assertEqual(recovered, content)
        self.assertEqual(counted.calls, operations * 6)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        print(
            json.dumps(
                dict(
                    recovery_benchmark=True,
                    raw_bytes=len(content),
                    lineage_parts=len(parts),
                    operations=operations,
                    db_execute_calls=counted.calls,
                    provider_calls=0,
                    elapsed_seconds=round(time.monotonic() - started, 3),
                    peak_python_bytes=peak,
                )
            ),
            flush=True,
        )
        # Administrative load fixture only: 4096 additional acknowledged metadata
        # rows prove the actual index plan at a deep seek. No qualification claim.
        ledger = database.execute(
            "SELECT to_jsonb(r) FROM memoriesql.idempotency_receipts r WHERE operation_kind='transcript_fold'"
        ).fetchone()[0]
        receipt = database.execute(
            "SELECT to_jsonb(r) FROM memoriesql.transcript_fold_receipts r"
        ).fetchone()[0]
        outcome = database.execute(
            "SELECT to_jsonb(r) FROM memoriesql.transcript_fold_outcomes r"
        ).fetchone()[0]
        with self.db.transaction():
            database.execute(
                "CREATE TEMP TABLE recovery_load AS SELECT i,uuidv7() ledger,uuidv7() receipt FROM generate_series(1,4096) i"
            )
            for table, template, updates in [
                (
                    "idempotency_receipts",
                    ledger,
                    "jsonb_build_object('idempotency_receipt_id',x.ledger,'idempotency_key',x.ledger::text)",
                ),
                (
                    "transcript_fold_receipts",
                    receipt,
                    "jsonb_build_object('transcript_fold_receipt_id',x.receipt,'idempotency_receipt_id',x.ledger,'source_revision_key','load.'||x.i,'checkpoint_key','load.'||x.i,'recovery_position',null)",
                ),
                (
                    "transcript_fold_outcomes",
                    outcome,
                    "jsonb_build_object('transcript_fold_receipt_id',x.receipt)",
                ),
            ]:
                database.execute(
                    sql.SQL(
                        "INSERT INTO memoriesql.{} SELECT (jsonb_populate_record(NULL::memoriesql.{},%s::jsonb || "
                        + updates
                        + ")).* FROM recovery_load x"
                    ).format(sql.Identifier(table), sql.Identifier(table)),
                    (Jsonb(template),),
                )
        database.execute("ANALYZE memoriesql.transcript_fold_receipts")
        database.execute("ANALYZE memoriesql.transcript_fold_outcomes")
        plan = database.execute(
            "EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) SELECT * FROM memoriesql.transcript_fold_receipts WHERE tenant_id=%s AND source_object_id=%s AND recovery_position>=4000 AND recovery_position<=4097 ORDER BY recovery_position LIMIT 34",
            (self.tenant, self.source),
        ).fetchone()[0][0]
        self.assertIn("transcript_fold_recovery_order_idx", json.dumps(plan))
        self.assertEqual(plan["Plan"]["Actual Rows"], 34)
        self.assertLess(
            plan["Plan"]["Shared Hit Blocks"]
            + plan["Plan"].get("Shared Read Blocks", 0),
            100,
        )
        print(json.dumps(dict(discovery_plan=plan)), flush=True)
        anchor = FoldOutcomeKey(
            source_object_id=self.source,
            fold_receipt_id=database.execute(
                "SELECT transcript_fold_receipt_id FROM memoriesql.transcript_fold_receipts WHERE recovery_position=4000"
            ).fetchone()[0],
            outcome_ordinal=0,
        )
        page = self.api.discover(
            DiscoverFoldOutcomes(source_object_id=self.source, after=anchor)
        )
        self.assertEqual(len(page.outcomes), 32)
        self.assertIsNotNone(page.continuation)

    def test_other_scope_is_not_in_service_grant_and_revoked_source_denies_pages(
        self,
    ) -> None:
        from psycopg import sql

        key = seed(self)[0]
        cursor = self.api.discover(
            DiscoverFoldOutcomes(source_object_id=self.source, limit=1)
        ).continuation
        other_scope, other_policy, other_source = [uuid.uuid4() for _ in range(3)]
        database: Any = self.db
        with database.transaction():
            specs: list[tuple[str, str, dict[str, Any]]] = [
                (
                    "access_scopes",
                    "access_scope_id=%s",
                    dict(
                        access_scope_id=other_scope,
                        current_policy_revision_id=other_policy,
                    ),
                ),
                (
                    "access_policy_revisions",
                    "access_scope_id=%s",
                    dict(access_scope_id=other_scope, policy_revision_id=other_policy),
                ),
                (
                    "protected_resources",
                    "resource_id=%s",
                    dict(access_scope_id=other_scope, resource_id=other_source),
                ),
                (
                    "source_objects",
                    "source_object_id=%s",
                    dict(
                        access_scope_id=other_scope,
                        source_object_id=other_source,
                        external_object_id="orchard.other",
                    ),
                ),
            ]
            for table, where, updates in specs:
                old_key = (
                    self.scope
                    if table in ("access_scopes", "access_policy_revisions")
                    else self.source
                )
                values = database.execute(
                    sql.SQL(
                        "SELECT to_jsonb(t) FROM memoriesql.{} t WHERE " + where
                    ).format(sql.Identifier(table)),
                    (old_key,),
                ).fetchone()[0]
                if table == "source_objects":
                    values.pop("installation_key")
                    values.pop("resource_kind")
                insert(database, table, values | updates)
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            self.api.discover(DiscoverFoldOutcomes(source_object_id=other_source))
        database.execute(
            "UPDATE memoriesql.protected_resources SET status='revoked',revoked_at=clock_timestamp() WHERE resource_id=%s",
            (self.source,),
        )
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            self.api.discover(
                DiscoverFoldOutcomes(source_object_id=self.source, continuation=cursor)
            )
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            self.api.inspect(InspectFoldOutcome(key=key))

    def expiry_during_wait(self, kind: str, *, alternative: bool = False) -> None:
        key = seed(self)[0]
        database: Any = self.db
        principal = database.execute(
            "SELECT principal_id FROM memoriesql.authentication_credentials WHERE secret_sha256=%s",
            (self.secret,),
        ).fetchone()[0]
        # Configure explicit-scope policy before starting the short expiry clock.
        if kind == "access_grant":
            policy = uuid.uuid4()
            with database.transaction():
                database.execute(
                    "INSERT INTO memoriesql.access_policy_revisions SELECT tenant_id,workspace_id,access_scope_id,%s,2,'explicit',owner_user_id,'Fictional expiry proof',actor_principal_id,clock_timestamp() FROM memoriesql.access_policy_revisions WHERE access_scope_id=%s AND revision=1",
                    (policy, self.scope),
                )
                database.execute(
                    "UPDATE memoriesql.access_scopes SET mode='explicit',current_policy_revision_id=%s WHERE access_scope_id=%s",
                    (policy, self.scope),
                )
        expires = datetime.now(UTC) + timedelta(milliseconds=250)
        if kind == "credential":
            database.execute(
                "UPDATE memoriesql.authentication_credentials SET expires_at=%s WHERE secret_sha256=%s",
                (expires, self.secret),
            )
        else:
            with database.transaction():
                self.begin()
                if kind == "pairing":
                    database.execute(
                        "SELECT memoriesql.revise_pairing_grant(%s,1,%s,%s,'active',%s,%s,%s)",
                        (
                            self.grant,
                            ["source.raw.read"],
                            [self.scope],
                            self.now,
                            expires,
                            datetime.now(UTC),
                        ),
                    )
                else:
                    database.execute(
                        "SELECT memoriesql.create_access_grant(%s,%s,%s,%s,%s,%s)",
                        (
                            uuid.uuid4(),
                            principal,
                            self.scope,
                            ["read"],
                            self.now,
                            expires,
                        ),
                    )
        if alternative:
            with database.transaction():
                self.begin()
                database.execute(
                    "SELECT memoriesql.create_access_grant(%s,%s,%s,%s,%s,%s)",
                    (uuid.uuid4(), principal, self.scope, ["read"], self.now, None),
                )
        with (
            self.connect() as blocker,
            self.connect() as reader,
            ThreadPoolExecutor(1) as pool,
        ):
            with blocker.transaction():
                blocker.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                    (str(self.tenant) + ":semantic_outcome_authority:",),
                )
                future = pool.submit(
                    self.reader(reader).inspect, InspectFoldOutcome(key=key)
                )
                self.wait_for_lock(reader.info.backend_pid)
                time.sleep(
                    max(0, (expires - datetime.now(UTC)).total_seconds()) + 0.015
                )
            if alternative:
                self.assertEqual(future.result(timeout=3).key, key)
            else:
                with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                    future.result(timeout=3)

    def test_credential_expiry_during_wait_denies_delivery(self) -> None:
        self.expiry_during_wait("credential")

    def test_pairing_expiry_during_wait_denies_delivery(self) -> None:
        self.expiry_during_wait("pairing")

    def test_access_grant_expiry_during_wait_denies_delivery(self) -> None:
        self.expiry_during_wait("access_grant")

    def test_role_policy_deletion_waits_through_delivery_transaction(self) -> None:
        from memoriesql.infrastructure.postgres.authorization import (
            PostgresAuthorizationPort,
        )

        key = seed(self)[0]
        with (
            self.connect() as reader,
            self.connect() as mutation,
            ThreadPoolExecutor(1) as pool,
        ):
            with reader.transaction():
                reader.execute("SET LOCAL ROLE memoriesql_application")
                PostgresAuthorizationPort(reader).begin_context(
                    credential_sha256=self.secret, requested_workspace_id=self.workspace
                )
                reader.execute(
                    "SELECT memoriesql.recover_transcript_fold_v1(%s)",
                    (Jsonb(InspectFoldOutcome(key=key).model_dump(mode="json")),),
                ).fetchone()
                future = pool.submit(
                    mutation.execute,
                    "DELETE FROM memoriesql.role_capabilities WHERE role_key='background_service' AND capability_key='source.raw.read'",
                )
                self.wait_for_lock(mutation.info.backend_pid, event="transactionid")
                self.assertFalse(future.done())
            future.result(timeout=3)
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            self.api.inspect(InspectFoldOutcome(key=key))

    def test_role_policy_deletion_that_wins_denies_after_wait(self) -> None:
        key = seed(self)[0]
        with (
            self.connect() as mutation,
            self.connect() as reader,
            ThreadPoolExecutor(1) as pool,
        ):
            with mutation.transaction():
                mutation.execute(
                    "DELETE FROM memoriesql.role_capabilities WHERE role_key='background_service' AND capability_key='source.raw.read'"
                )
                future = pool.submit(
                    self.reader(reader).inspect, InspectFoldOutcome(key=key)
                )
                self.wait_for_lock(reader.info.backend_pid, event="transactionid")
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                future.result(timeout=3)

    def test_unexpired_alternative_access_grant_still_permits_delivery(self) -> None:
        self.expiry_during_wait("access_grant", alternative=True)
