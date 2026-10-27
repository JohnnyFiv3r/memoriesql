"""Installed fictional Core Memory engine record intake against PostgreSQL."""

from __future__ import annotations

import contextlib
import copy
import hashlib
import os
import threading
import unittest
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Jsonb

from memoriesql.application.engine_intake import (
    AcceptEngineRecords,
    EngineRecordAccepted,
    EngineRecordAlreadyAccepted,
    EngineRecordConflict,
    EngineRecordRejected,
    EngineRecordRetryable,
    ReadEngineRecords,
)
from memoriesql.application.source_enrollment import EnrollExactSource, GrantExactSource
from memoriesql.infrastructure.postgres.engine_intake import (
    EngineIntakeBusy,
    PostgresEngineIntake,
)
from memoriesql.infrastructure.postgres.source_enrollment import (
    PostgresSourceEnrollment,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from tests.runtime.test_postgres_runtime import migrate
else:
    from test_postgres_runtime import migrate

OWNER_CREDENTIAL = "fictional orchard owner session credential"
ENGINE_CREDENTIAL = "fictional orchard engine delivery credential"
CATALOG = {"type": {"id": "memoriesql.bead_types", "hash": "ab" * 32}}


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def rule(rule_id: str = "opportunity-closed") -> dict[str, Any]:
    return {
        "decision_id": str(uuid.uuid4()),
        "method": "rule",
        "profile": {"id": "fictional.crm", "rev": 1},
        "rule_id": rule_id,
        "decided_at": "2026-10-26T15:00:01Z",
    }


def model() -> dict[str, Any]:
    return {
        "decision_id": str(uuid.uuid4()),
        "method": "model",
        "binding_ref": "classifier",
        "binding_rev": "cd" * 32,
        "decided_at": "2026-10-26T15:00:02Z",
    }


def assigned(key: str) -> dict[str, Any]:
    return {"status": "assigned", "type_ref": {"key": key, "rev": 1}}


def event_records(
    scope: str,
    external_id: str,
    texts: list[str],
    outcomes: list[tuple[dict[str, Any], dict[str, Any] | None]],
) -> list[dict[str, Any]]:
    """Chunk records for one event, shaped like the engine's."""
    content = "".join(texts)
    event: dict[str, Any] = {
        "event_id": str(uuid.uuid4()),
        "key": {
            "scope": scope,
            "source_ns": "chat/orchard",
            "external_id": external_id,
        },
        "event_hash": sha("event:" + external_id + content),
        "content_hash": sha(content),
        "content_kind": "text",
        "source_type": "transcript",
        "source_system": "fictional-chat",
        "media_type": "text/plain",
        "structure": {"thread": "orchard"},
        "parser_contract_version": "fictional/1",
        "captured_at": "2026-10-26T15:00:00Z",
        "chunk_count": len(texts),
    }
    records = []
    byte_start = char_start = 0
    for ordinal, (text, (outcome, decision)) in enumerate(
        zip(texts, outcomes, strict=True)
    ):
        chunk_id = str(uuid.uuid4())
        record: dict[str, Any] = {
            "contract": "core_memory.record.v1",
            "record_key": "chunk:" + chunk_id,
            "scope": scope,
            "record_kind": "chunk",
            "event": event,
            "memory": {
                "chunk": {
                    "chunk_id": chunk_id,
                    "ordinal": ordinal,
                    "byte_start": byte_start,
                    "byte_end": byte_start + len(text.encode("utf-8")),
                    "char_start": char_start,
                    "char_end": char_start + len(text),
                    "content_hash": sha(text),
                    "text": text,
                },
                "type": outcome,
            },
            "facets": {"lengths": {"bytes": len(text.encode("utf-8"))}},
            "source_profile": {"id": "core.chat_transcript", "rev": 1},
            "chunker": {"id": "text.structural", "rev": 1, "config_hash": "ef" * 32},
            "catalogs": CATALOG,
            "relationships": {"stage": "off"},
        }
        if decision is not None:
            record["decision"] = decision
        records.append(record)
        byte_start += len(text.encode("utf-8"))
        char_start += len(text)
    return records


def in_scope(record: dict[str, Any], scope: str) -> dict[str, Any]:
    """The same record, ids and all, delivered under another engine scope."""
    moved = copy.deepcopy(record)
    moved["scope"] = scope
    if "event" in moved:
        moved["event"]["key"]["scope"] = scope
    return moved


def type_decision(
    chunk: dict[str, Any], outcome: dict[str, Any], decision: dict[str, Any]
) -> dict[str, Any]:
    chunk_id = chunk["memory"]["chunk"]["chunk_id"]
    return {
        "contract": "core_memory.record.v1",
        "record_key": "type:" + chunk_id,
        "scope": chunk["scope"],
        "record_kind": "type_decision",
        "chunk_id": chunk_id,
        "event_id": chunk["event"]["event_id"],
        "outcome": outcome,
        "decision": decision,
        "catalogs": CATALOG,
    }


class EngineRecordIntake(unittest.TestCase):
    def setUp(self) -> None:
        self.admin_url = os.environ["N1_TEST_DATABASE_URL"]
        self.database = "pr_engine_" + uuid.uuid4().hex
        with psycopg.connect(self.admin_url, autocommit=True) as admin:
            admin.execute(
                sql.SQL("CREATE DATABASE {}").format(sql.Identifier(self.database))
            )
        self.url = make_conninfo(self.admin_url, dbname=self.database)
        self.db = psycopg.connect(self.url, autocommit=True)
        self.addCleanup(self.cleanup_database)
        migrate(self.db, expected_current_version=0, target_version=54)
        (
            self.tenant,
            user,
            identity,
            self.owner,
            self.workspace,
            default_scope,
            membership,
            credential,
            session,
        ) = (uuid.uuid4() for _ in range(9))
        self.owner_secret = sha(OWNER_CREDENTIAL)
        self.engine_secret = sha(ENGINE_CREDENTIAL)
        self.now = datetime.now(UTC)
        with self.db.transaction():
            self.db.execute("SET LOCAL ROLE memoriesql_application")
            self.db.execute(
                "SELECT memoriesql.bootstrap_personal_local(%s,%s,%s,%s,%s,%s,%s,%s,%s,'memoriesql.local',%s,'Fictional orchard',%s,%s,%s)",
                (
                    self.tenant,
                    user,
                    identity,
                    self.owner,
                    self.workspace,
                    default_scope,
                    membership,
                    credential,
                    session,
                    str(identity),
                    self.owner_secret,
                    self.now,
                    self.now + timedelta(hours=1),
                ),
            )
        self.acme = self.enroll("acme")
        self.engine = uuid.uuid4()
        self.pair(self.engine, self.engine_secret, [self.acme.access_scope_id])
        self.grant(self.acme.source_object_id, self.engine)

    def cleanup_database(self) -> None:
        self.db.close()
        with psycopg.connect(self.admin_url, autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                    sql.Identifier(self.database)
                )
            )

    def sources(self) -> PostgresSourceEnrollment:
        return PostgresSourceEnrollment(
            self.db, credential_sha256=self.owner_secret, workspace_id=self.workspace
        )

    def enroll(self, scope: str) -> Any:
        return self.sources().enroll(
            EnrollExactSource(
                request_id=uuid.uuid4(),
                source_system="core-memory",
                object_kind="engine_scope",
                external_object_id=scope,
                source_schema_version=1,
                exact_source_confirmed=True,
            )
        )

    def pair(
        self,
        principal: uuid.UUID,
        secret: str,
        scopes: list[uuid.UUID],
        capabilities: tuple[str, ...] = ("engine.records.deliver",),
    ) -> None:
        with self.db.transaction():
            self.db.execute("SET LOCAL ROLE memoriesql_application")
            self.db.execute(
                "SELECT memoriesql.begin_authorization_context(%s,%s)",
                (self.owner_secret, self.workspace),
            )
            self.db.execute(
                "SELECT memoriesql.pair_local_client(%s,%s,%s,%s,'service','background_service',%s,%s,%s,%s,%s)",
                (
                    principal,
                    uuid.uuid4(),
                    uuid.uuid4(),
                    uuid.uuid4(),
                    list(capabilities),
                    scopes,
                    secret,
                    self.now,
                    self.now + timedelta(hours=1),
                ),
            )

    def grant(
        self,
        source: uuid.UUID,
        principal: uuid.UUID,
        permissions: tuple[Literal["read", "write"], ...] = ("write",),
    ) -> None:
        self.sources().grant(
            GrantExactSource(
                request_id=uuid.uuid4(),
                source_object_id=source,
                target_principal_id=principal,
                permission_keys=permissions,
                valid_from=self.now,
                expires_at=self.now + timedelta(hours=1),
            )
        )

    def beta_engine(self) -> str:
        """Enrolls beta and pairs a second engine that may deliver only there."""
        beta = self.enroll("beta")
        principal = uuid.uuid4()
        secret = sha("fictional beta engine credential")
        self.pair(principal, secret, [beta.access_scope_id])
        self.grant(beta.source_object_id, principal)
        return secret

    def intake(
        self,
        secret: str | None = None,
        connection: psycopg.Connection[Any] | None = None,
    ) -> PostgresEngineIntake:
        return PostgresEngineIntake(
            connection or self.db,
            credential_sha256=secret or self.engine_secret,
            workspace_id=self.workspace,
        )

    def deliver(self, *records: dict[str, Any]) -> list[Any]:
        request = AcceptEngineRecords(records=records)
        return list(self.intake().accept(request).results)

    def held(self, scope: str = "acme") -> list[dict[str, Any]]:
        page = self.intake(self.owner_secret).read(
            ReadEngineRecords(engine_scope=scope)
        )
        return list(page.records)

    def count(self, table: str) -> int:
        row = self.db.execute(
            sql.SQL("SELECT count(*) FROM memoriesql.{}").format(sql.Identifier(table))
        ).fetchone()
        assert row is not None
        return int(row[0])

    def test_chunks_and_a_late_type_are_kept_exactly(self) -> None:
        chunks = event_records(
            "acme",
            "orchard/1",
            ["We decided to pick on Friday. ", "Sam will bring étagères."],
            [
                (assigned("decision"), rule()),
                ({"status": "pending", "reason": "timeout"}, None),
            ],
        )
        late = type_decision(chunks[1], assigned("action"), model())

        self.assertEqual(
            self.deliver(*chunks),
            [EngineRecordAccepted(outcome="accepted")] * 2,
        )
        self.assertEqual(self.deliver(late), [EngineRecordAccepted(outcome="accepted")])
        self.assertEqual(
            self.deliver(*chunks, late),
            [EngineRecordAlreadyAccepted(outcome="already_accepted")] * 3,
        )
        self.assertEqual(self.held(), [*chunks, late])

        self.assertEqual(self.count("engine_event_completions"), 1)
        statuses = self.db.execute(
            "SELECT d.decision_ordinal, d.status, t.stable_key, d.method "
            "FROM memoriesql.engine_type_decisions d "
            "LEFT JOIN memoriesql.bead_types t USING (bead_type_id) "
            "ORDER BY d.recorded_at, d.decision_ordinal"
        ).fetchall()
        self.assertEqual(
            statuses,
            [
                (0, "assigned", "decision", "rule"),
                (0, "pending", None, None),
                (1, "assigned", "action", "model"),
            ],
        )
        receipts = self.db.execute(
            "SELECT status, response_receipt ->> 'outcome' FROM memoriesql.idempotency_receipts "
            "WHERE operation_kind = 'engine_record.accept.v1'"
        ).fetchall()
        self.assertEqual(receipts, [("succeeded", "accepted")] * 3)
        kinds = self.db.execute(
            "SELECT event_kind FROM memoriesql.outbox_events "
            "WHERE aggregate_kind = 'engine_chunk' ORDER BY recorded_at"
        ).fetchall()
        self.assertEqual(
            kinds,
            [
                ("engine_chunk.accepted",),
                ("engine_chunk.accepted",),
                ("engine_type.decided",),
            ],
        )
        # Nothing authored: no bead, source event, statement or semantic task.
        for table in ("beads", "source_events", "bead_versions", "semantic_tasks"):
            self.assertEqual(self.count(table), 0, table)

    def test_first_writer_wins_and_a_final_type_is_never_replaced(self) -> None:
        (chunk,) = event_records(
            "acme",
            "orchard/2",
            ["Pending at first."],
            [({"status": "pending", "reason": "capacity"}, None)],
        )
        self.deliver(chunk)
        altered = copy.deepcopy(chunk)
        altered["facets"] = {"lengths": {"bytes": 1}}
        self.assertEqual(
            self.deliver(altered), [EngineRecordConflict(outcome="conflict")]
        )

        late = type_decision(
            chunk, {"status": "unassigned", "reason": "no_fit"}, model()
        )
        self.deliver(late)
        other = type_decision(chunk, assigned("outcome"), model())
        (result,) = self.deliver(other)
        self.assertEqual(result, EngineRecordConflict(outcome="conflict"))

        (final,) = event_records(
            "acme", "orchard/3", ["Already final."], [(assigned("observation"), rule())]
        )
        self.deliver(final)
        (result,) = self.deliver(type_decision(final, assigned("decision"), model()))
        self.assertEqual(
            result,
            EngineRecordRejected(
                outcome="rejected", reason="the chunk already has a final type"
            ),
        )
        self.assertEqual(self.held(), [chunk, late, final])

    def test_only_a_reader_is_shown_the_stored_record_in_a_conflict(self) -> None:
        (chunk,) = event_records(
            "acme", "orchard/14", ["Held text."], [(assigned("observation"), rule())]
        )
        self.deliver(chunk)
        altered = copy.deepcopy(chunk)
        altered["facets"] = {"lengths": {"bytes": 1}}
        request = AcceptEngineRecords(records=(altered,))
        writer: tuple[Literal["read", "write"], ...] = ("write",)
        reader: tuple[Literal["read", "write"], ...] = ("read", "write")
        engines = {}
        for name, capabilities, permissions in (
            ("capability only", ("engine.records.deliver", "source.read"), writer),
            ("grant only", ("engine.records.deliver",), reader),
            ("reader", ("engine.records.deliver", "source.read"), reader),
        ):
            principal = uuid.uuid4()
            secret = sha("fictional engine credential: " + name)
            self.pair(principal, secret, [self.acme.access_scope_id], capabilities)
            self.grant(self.acme.source_object_id, principal, permissions)
            engines[name] = secret
        # A writer who may not read the scope learns only that the key is held.
        for secret in (
            self.engine_secret,
            engines["capability only"],
            engines["grant only"],
        ):
            self.assertEqual(
                list(self.intake(secret).accept(request).results),
                [EngineRecordConflict(outcome="conflict")],
            )
        self.assertEqual(
            list(self.intake(engines["reader"]).accept(request).results),
            [EngineRecordConflict(outcome="conflict", stored=chunk)],
        )
        self.assertEqual(self.held(), [chunk])

    def test_ungoverned_types_and_malformed_records_are_rejected(self) -> None:
        def reason(record: dict[str, Any]) -> str:
            (result,) = self.deliver(record)
            self.assertIsInstance(result, EngineRecordRejected)
            return str(result.reason)

        (good,) = event_records(
            "acme", "orchard/4", ["Fine."], [(assigned("lesson"), rule())]
        )

        ungoverned = copy.deepcopy(good)
        ungoverned["memory"]["type"] = assigned("invented")
        self.assertIn("not a current memoriesQL bead type", reason(ungoverned))
        old_rev = copy.deepcopy(good)
        old_rev["memory"]["type"]["type_ref"]["rev"] = 2
        self.assertIn("revision 2", reason(old_rev))
        other_catalog = copy.deepcopy(good)
        other_catalog["catalogs"] = {"type": {"id": "acme.types", "hash": "ab" * 32}}
        self.assertIn("is not memoriesql.bead_types", reason(other_catalog))
        bad_hash = copy.deepcopy(good)
        bad_hash["memory"]["chunk"]["text"] = "Fine!"
        self.assertIn("does not match its hash", reason(bad_hash))
        no_decision = copy.deepcopy(good)
        del no_decision["decision"]
        self.assertIn("needs its decision provenance", reason(no_decision))
        newer = copy.deepcopy(good)
        newer["contract"] = "core_memory.record.v2"
        self.assertIn("only core_memory.record.v1", reason(newer))
        relationship = copy.deepcopy(good)
        relationship["record_kind"] = "relationship"
        self.assertIn("not accepted yet", reason(relationship))
        wrong_key = copy.deepcopy(good)
        wrong_key["record_key"] = "chunk:" + str(uuid.uuid4())
        self.assertIn("the chunk needs", reason(wrong_key))

        first, second = event_records(
            "acme",
            "orchard/5",
            ["One. ", "Two."],
            [(assigned("observation"), rule())] * 2,
        )
        second["memory"]["chunk"]["byte_start"] += 1
        second["memory"]["chunk"]["byte_end"] += 1
        self.deliver(first)
        self.assertIn("do not reproduce its content", reason(second))
        clashing = event_records(
            "acme", "orchard/5-other", ["Two."], [(assigned("observation"), rule())]
        )[0]
        clashing["event"] = copy.deepcopy(first["event"])
        clashing["event"]["chunk_count"] = 3
        self.assertIn("differs from the one its earlier chunks", reason(clashing))

        orphan = type_decision(good, assigned("decision"), model())
        self.assertIn("no accepted chunk", reason(orphan))
        self.deliver(good)
        self.assertEqual(self.held(), [first, good])
        self.assertEqual(self.count("engine_event_completions"), 1)

    def test_unenrolled_and_ungranted_scopes_retry_without_reading_later_records(
        self,
    ) -> None:
        (elsewhere,) = event_records(
            "beta",
            "orchard/6",
            ["Not enrolled yet."],
            [(assigned("observation"), rule())],
        )
        (here,) = event_records(
            "acme", "orchard/7", ["Enrolled."], [(assigned("observation"), rule())]
        )
        results = self.deliver(elsewhere, here)
        self.assertTrue(all(isinstance(r, EngineRecordRetryable) for r in results))
        self.assertIn("not enrolled", results[0].reason)
        self.assertEqual(results[0], results[1])
        self.assertEqual(self.count("engine_records"), 0)

        beta = self.enroll("beta")
        (result, _) = self.deliver(elsewhere, here)
        self.assertIsInstance(result, EngineRecordRetryable)
        self.assertIn("may not deliver", result.reason)

        self.grant(beta.source_object_id, self.engine)
        # The pairing still lists only acme's scope.
        (result, _) = self.deliver(elsewhere, here)
        self.assertIsInstance(result, EngineRecordRetryable)

        second = uuid.uuid4()
        second_secret = sha("fictional second engine credential")
        self.pair(
            second, second_secret, [self.acme.access_scope_id, beta.access_scope_id]
        )
        self.grant(self.acme.source_object_id, second)
        self.grant(beta.source_object_id, second)
        results = list(
            self.intake(second_secret)
            .accept(AcceptEngineRecords(records=(elsewhere, here)))
            .results
        )
        self.assertEqual(results, [EngineRecordAccepted(outcome="accepted")] * 2)
        self.assertEqual(self.held("beta"), [elsewhere])

    def test_delivery_and_reads_need_their_own_authority(self) -> None:
        (record,) = event_records(
            "acme", "orchard/8", ["Authority."], [(assigned("observation"), rule())]
        )
        reader = uuid.uuid4()
        reader_secret = sha("fictional reader without delivery")
        self.pair(reader, reader_secret, [self.acme.access_scope_id], ("source.read",))
        with self.assertRaises(PermissionError):
            self.intake(reader_secret).accept(AcceptEngineRecords(records=(record,)))
        self.deliver(record)
        with self.assertRaises(PermissionError):
            self.intake().read(ReadEngineRecords(engine_scope="acme"))
        with self.assertRaises(PermissionError):
            self.intake(self.owner_secret).read(ReadEngineRecords(engine_scope="nope"))
        page = self.intake(self.owner_secret).read(
            ReadEngineRecords(engine_scope="acme", after=0, limit=1)
        )
        self.assertEqual(page.records, (record,))
        empty = self.intake(self.owner_secret).read(
            ReadEngineRecords(engine_scope="acme", after=page.next)
        )
        self.assertEqual((empty.records, empty.next), ((), page.next))

    def test_the_vocabulary_is_the_governed_bead_types(self) -> None:
        vocabulary = self.intake().vocabulary()
        self.assertEqual(vocabulary.id, "memoriesql.bead_types")
        governed = self.db.execute(
            "SELECT stable_key, revision, definition FROM memoriesql.current_bead_type_revisions ORDER BY stable_key"
        ).fetchall()
        self.assertEqual(
            [(t.key, t.rev, t.definition) for t in vocabulary.types], governed
        )
        self.assertIn("decision", [t.key for t in vocabulary.types])

    def test_tables_are_immutable_and_reachable_only_through_functions(self) -> None:
        (record,) = event_records(
            "acme", "orchard/9", ["Immutable."], [(assigned("observation"), rule())]
        )
        self.deliver(record)
        for table in (
            "engine_records",
            "engine_chunks",
            "engine_events",
            "engine_type_decisions",
            "engine_event_completions",
        ):
            with self.assertRaises(
                psycopg.errors.ObjectNotInPrerequisiteState, msg=table
            ):
                self.db.execute(
                    sql.SQL("DELETE FROM memoriesql.{}").format(sql.Identifier(table))
                )
            with self.assertRaises(psycopg.errors.InsufficientPrivilege, msg=table):
                with self.db.transaction():
                    self.db.execute("SET LOCAL ROLE memoriesql_application")
                    self.db.execute(
                        sql.SQL("SELECT * FROM memoriesql.{}").format(
                            sql.Identifier(table)
                        )
                    )

    @contextlib.contextmanager
    def held_open(self, *records: dict[str, Any]) -> Iterator[None]:
        """Delivers `records` in a transaction left open until the block ends."""
        holding = threading.Event()
        release = threading.Event()
        failures: list[BaseException] = []

        def hold() -> None:
            try:
                with psycopg.connect(self.url) as connection:
                    with connection.transaction():
                        connection.execute("SET LOCAL ROLE memoriesql_application")
                        connection.execute(
                            "SELECT memoriesql.begin_authorization_context(%s,%s)",
                            (self.engine_secret, self.workspace),
                        )
                        connection.execute(
                            "SELECT memoriesql.accept_engine_records_v1(%s)",
                            (
                                Jsonb(
                                    AcceptEngineRecords(records=records).model_dump(
                                        mode="json"
                                    )
                                ),
                            ),
                        )
                        holding.set()
                        release.wait(10)
            except BaseException as error:  # noqa: BLE001
                failures.append(error)
                holding.set()

        thread = threading.Thread(target=hold)
        thread.start()
        self.assertTrue(holding.wait(10))
        try:
            yield
        finally:
            release.set()
            thread.join(10)
        self.assertEqual(failures, [])

    def test_concurrent_chunks_of_one_event_complete_it_once(self) -> None:
        first, second = event_records(
            "acme",
            "orchard/10",
            ["Concurrent one. ", "Concurrent two."],
            [(assigned("observation"), rule())] * 2,
        )
        with self.held_open(first):
            with psycopg.connect(self.url, autocommit=True) as other:
                with self.assertRaises(EngineIntakeBusy):
                    self.intake(connection=other).accept(
                        AcceptEngineRecords(records=(second,))
                    )
        self.assertEqual(
            self.deliver(second), [EngineRecordAccepted(outcome="accepted")]
        )
        self.assertEqual(self.count("engine_event_completions"), 1)

    def test_writers_to_one_scope_take_turns_so_reads_never_skip(self) -> None:
        (early,) = event_records(
            "acme", "orchard/11", ["Early."], [(assigned("observation"), rule())]
        )
        (late,) = event_records(
            "acme", "orchard/12", ["Late."], [(assigned("observation"), rule())]
        )
        (elsewhere,) = event_records(
            "beta", "orchard/13", ["Elsewhere."], [(assigned("observation"), rule())]
        )
        other_secret = self.beta_engine()
        with self.held_open(early):
            with psycopg.connect(self.url, autocommit=True) as other:
                # Another event in the same scope waits for the open batch
                # instead of committing a later record_seq before it, which
                # a reader's cursor would then pass.
                with self.assertRaises(EngineIntakeBusy):
                    self.intake(connection=other).accept(
                        AcceptEngineRecords(records=(late,))
                    )
                # Another scope does not wait.
                accepted = self.intake(other_secret, other).accept(
                    AcceptEngineRecords(records=(elsewhere,))
                )
                self.assertEqual(
                    list(accepted.results), [EngineRecordAccepted(outcome="accepted")]
                )
        self.assertEqual(self.deliver(late), [EngineRecordAccepted(outcome="accepted")])
        self.assertEqual(self.held(), [early, late])

    def test_a_batch_never_holds_back_a_scope_it_may_not_write(self) -> None:
        (here,) = event_records(
            "acme", "orchard/15", ["Mine."], [(assigned("observation"), rule())]
        )
        (named,) = event_records(
            "beta", "orchard/16", ["Not mine."], [(assigned("observation"), rule())]
        )
        (theirs,) = event_records(
            "beta", "orchard/17", ["Theirs."], [(assigned("observation"), rule())]
        )
        other_secret = self.beta_engine()
        # The open batch names beta, which its engine may not write.
        with self.held_open(here, named):
            with psycopg.connect(self.url, autocommit=True) as other:
                accepted = self.intake(other_secret, other).accept(
                    AcceptEngineRecords(records=(theirs,))
                )
                self.assertEqual(
                    list(accepted.results), [EngineRecordAccepted(outcome="accepted")]
                )
        self.assertEqual(self.held(), [here])

    def test_naming_another_scopes_ids_never_holds_it_back(self) -> None:
        first, second = event_records(
            "beta",
            "orchard/18",
            ["Theirs, first. ", "Theirs, second."],
            [({"status": "pending", "reason": "capacity"}, None)]
            + [(assigned("observation"), rule())],
        )
        other_secret = self.beta_engine()
        self.intake(other_secret).accept(AcceptEngineRecords(records=(first,)))
        # The open acme batch borrows beta's record key and beta's event id,
        # which within acme name acme's own record and event.
        borrowed_key = in_scope(first, "acme")
        (borrowed_event,) = event_records(
            "acme", "orchard/19", ["Borrowed."], [(assigned("observation"), rule())]
        )
        borrowed_event["event"]["event_id"] = first["event"]["event_id"]
        with self.held_open(borrowed_key, borrowed_event):
            with psycopg.connect(self.url, autocommit=True) as other:
                late = type_decision(first, assigned("observation"), model())
                accepted = self.intake(other_secret, other).accept(
                    AcceptEngineRecords(records=(second, late))
                )
                self.assertEqual(
                    list(accepted.results),
                    [EngineRecordAccepted(outcome="accepted")] * 2,
                )
        # The borrowed event id met acme's own event, which carried another
        # event object, so only the borrowed key was kept, as acme's record.
        self.assertEqual(self.held(), [borrowed_key])
        self.assertEqual(self.held("beta"), [first, second, late])
        self.assertEqual(self.count("engine_event_completions"), 1)

    def test_a_scope_cannot_take_another_scopes_keys_first(self) -> None:
        theirs = event_records(
            "beta",
            "orchard/30",
            ["Shared one. ", "Shared two."],
            [({"status": "pending", "reason": "capacity"}, None)]
            + [(assigned("observation"), rule())],
        )
        taken = [in_scope(record, "acme") for record in theirs]
        other_secret = self.beta_engine()
        # Acme delivers beta's future record keys, chunk ids and event id first.
        self.assertEqual(
            self.deliver(*taken), [EngineRecordAccepted(outcome="accepted")] * 2
        )
        self.assertEqual(
            list(
                self.intake(other_secret)
                .accept(AcceptEngineRecords(records=tuple(theirs)))
                .results
            ),
            [EngineRecordAccepted(outcome="accepted")] * 2,
        )
        self.assertEqual(self.held(), taken)
        self.assertEqual(self.held("beta"), theirs)
        self.assertEqual(self.count("engine_event_completions"), 2)

        # Replays, conflicts and late types stay inside their own scope.
        self.assertEqual(
            self.deliver(*taken),
            [EngineRecordAlreadyAccepted(outcome="already_accepted")] * 2,
        )
        altered = copy.deepcopy(taken[0])
        altered["facets"] = {"lengths": {"bytes": 1}}
        self.assertEqual(
            self.deliver(altered), [EngineRecordConflict(outcome="conflict")]
        )
        late = type_decision(taken[0], assigned("decision"), model())
        self.assertEqual(self.deliver(late), [EngineRecordAccepted(outcome="accepted")])
        # Beta's copy of the chunk is still pending and is decided on its own.
        theirs_late = type_decision(theirs[0], assigned("action"), model())
        self.assertEqual(
            list(
                self.intake(other_secret)
                .accept(AcceptEngineRecords(records=(theirs_late,)))
                .results
            ),
            [EngineRecordAccepted(outcome="accepted")],
        )
        self.assertEqual(self.held(), [*taken, late])
        self.assertEqual(self.held("beta"), [*theirs, theirs_late])
        # One ledger receipt per scope and record key.
        keys = self.db.execute(
            "SELECT idempotency_key FROM memoriesql.idempotency_receipts "
            "WHERE operation_kind = 'engine_record.accept.v1' "
            "AND idempotency_key LIKE %s ORDER BY idempotency_key",
            ("%:" + theirs[0]["record_key"],),
        ).fetchall()
        beta_source = self.db.execute(
            "SELECT source_object_id FROM memoriesql.source_objects "
            "WHERE external_object_id = 'beta'"
        ).fetchone()
        assert beta_source is not None
        self.assertEqual(
            sorted(row[0] for row in keys),
            sorted(
                f"{source}:{theirs[0]['record_key']}"
                for source in (self.acme.source_object_id, beta_source[0])
            ),
        )

    def test_an_impossible_offset_is_rejected_without_failing_the_batch(self) -> None:
        offset = rule()
        offset["decided_at"] = "2026-10-26T15:00:01+99:00"
        (bad,) = event_records(
            "acme", "orchard/20", ["Bad offset."], [(assigned("observation"), offset)]
        )
        (good,) = event_records(
            "acme", "orchard/21", ["Good offset."], [(assigned("observation"), rule())]
        )
        rejected, accepted = self.deliver(bad, good)
        self.assertIsInstance(rejected, EngineRecordRejected)
        self.assertEqual(accepted, EngineRecordAccepted(outcome="accepted"))
        self.assertEqual(self.held(), [good])


if __name__ == "__main__":
    unittest.main()
