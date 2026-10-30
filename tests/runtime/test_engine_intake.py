"""Installed fictional Core Memory engine record intake against PostgreSQL."""

from __future__ import annotations

import contextlib
import copy
import hashlib
import json
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
# The one answer for every scope a caller may not both deliver to and read.
UNAVAILABLE = EngineRecordRetryable(
    outcome="retryable",
    retry_after_ms=60000,
    reason=(
        "the engine scope is not enrolled, or this credential may not deliver to "
        "and read it"
    ),
)
Permissions = tuple[Literal["read", "write"], ...]
DELIVER_AND_READ = ("engine.records.deliver", "source.read")
READ_AND_WRITE: Permissions = ("read", "write")
# Everything an accepted record writes.
WRITTEN = (
    "engine_events",
    "engine_records",
    "engine_chunks",
    "engine_event_completions",
    "engine_type_decisions",
    "idempotency_receipts",
    "outbox_events",
)
# A second tenant's owner: its credential secret and workspace.
Owner = tuple[str, uuid.UUID]


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


def one(scope: str, external_id: str) -> dict[str, Any]:
    """A one-chunk event with a final type."""
    (record,) = event_records(
        scope,
        external_id,
        [f"Fictional {external_id}."],
        [(assigned("observation"), rule())],
    )
    return record


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


class EngineIntakeFixture(unittest.TestCase):
    """A fictional orchard workspace whose engine may deliver to and read acme."""

    schema_version = 56

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
        migrate(self.db, expected_current_version=0, target_version=self.schema_version)
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

    def sources(self, owner: Owner | None = None) -> PostgresSourceEnrollment:
        secret, workspace = owner or (self.owner_secret, self.workspace)
        return PostgresSourceEnrollment(
            self.db, credential_sha256=secret, workspace_id=workspace
        )

    def enroll(self, scope: str, owner: Owner | None = None) -> Any:
        return self.sources(owner).enroll(
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
        capabilities: tuple[str, ...] = DELIVER_AND_READ,
        owner: Owner | None = None,
    ) -> None:
        owner_secret, workspace = owner or (self.owner_secret, self.workspace)
        with self.db.transaction():
            self.db.execute("SET LOCAL ROLE memoriesql_application")
            self.db.execute(
                "SELECT memoriesql.begin_authorization_context(%s,%s)",
                (owner_secret, workspace),
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
        permissions: Permissions = READ_AND_WRITE,
        owner: Owner | None = None,
        valid_from: datetime | None = None,
    ) -> None:
        self.sources(owner).grant(
            GrantExactSource(
                request_id=uuid.uuid4(),
                source_object_id=source,
                target_principal_id=principal,
                permission_keys=permissions,
                valid_from=valid_from or self.now,
                expires_at=self.now + timedelta(hours=1),
            )
        )

    def engine_for(
        self,
        *sources: Any,
        capabilities: tuple[str, ...] = DELIVER_AND_READ,
        permissions: Permissions = READ_AND_WRITE,
        owner: Owner | None = None,
    ) -> str:
        """Pairs a new engine for `sources`, granted `permissions` on each."""
        principal = uuid.uuid4()
        secret = sha(f"fictional engine credential {principal}")
        self.pair(
            principal,
            secret,
            [source.access_scope_id for source in sources],
            capabilities,
            owner,
        )
        for source in sources:
            self.grant(source.source_object_id, principal, permissions, owner)
        return secret

    def beta_engine(self) -> str:
        """Enrolls beta and pairs a second engine that may deliver only there."""
        return self.engine_for(self.enroll("beta"))

    def other_tenant(self) -> Owner:
        """A second fictional tenant's owner. Public fixture pattern
        (test_agent_sql_results, test_authored_relations)."""
        secret = sha("fictional other tenant owner credential")
        tenant, user, identity, principal, workspace, scope, policy = (
            uuid.uuid4() for _ in range(7)
        )
        credential, session, now = uuid.uuid4(), uuid.uuid4(), self.now
        rows: tuple[tuple[str, tuple[Any, ...]], ...] = (
            ("users", (tenant, user, "active", "Other fictional tenant", now, None)),
            (
                "auth_identities",
                (
                    tenant,
                    identity,
                    user,
                    "memoriesql.local",
                    str(identity),
                    "local_interactive",
                    "active",
                    now,
                    None,
                ),
            ),
            (
                "principals",
                (tenant, principal, "human", user, user, "active", now, None),
            ),
            ("workspaces", (tenant, workspace, "personal_local", user, "active", now)),
            (
                "workspace_memberships",
                (
                    tenant,
                    workspace,
                    principal,
                    "personal_owner",
                    "active",
                    1,
                    now,
                    now,
                    None,
                ),
            ),
            (
                "access_scopes",
                (
                    tenant,
                    workspace,
                    scope,
                    user,
                    "owner_private",
                    policy,
                    "active",
                    now,
                ),
            ),
            (
                "access_policy_revisions",
                (
                    tenant,
                    workspace,
                    scope,
                    policy,
                    1,
                    "owner_private",
                    user,
                    "Fictional default scope",
                    principal,
                    now,
                ),
            ),
            (
                "authentication_credentials",
                (
                    tenant,
                    credential,
                    principal,
                    "local_session",
                    secret,
                    "active",
                    now,
                    now + timedelta(hours=1),
                    None,
                ),
            ),
            ("local_auth_sessions", (tenant, session, credential, identity, now)),
        )
        with self.db.transaction():
            for table, values in rows:
                self.db.execute(
                    sql.SQL("INSERT INTO memoriesql.{} VALUES ({})").format(
                        sql.Identifier(table),
                        sql.SQL(",").join(sql.Placeholder() for _ in values),
                    ),
                    values,
                )
        return secret, workspace

    def intake(
        self,
        secret: str | None = None,
        connection: psycopg.Connection[Any] | None = None,
        workspace: uuid.UUID | None = None,
    ) -> PostgresEngineIntake:
        return PostgresEngineIntake(
            connection or self.db,
            credential_sha256=secret or self.engine_secret,
            workspace_id=workspace or self.workspace,
        )

    def deliver(self, *records: dict[str, Any], secret: str | None = None) -> list[Any]:
        request = AcceptEngineRecords(records=records)
        return list(self.intake(secret).accept(request).results)

    def held(
        self, scope: str = "acme", owner: Owner | None = None
    ) -> list[dict[str, Any]]:
        secret, workspace = owner or (self.owner_secret, self.workspace)
        page = self.intake(secret, workspace=workspace).read(
            ReadEngineRecords(engine_scope=scope)
        )
        return list(page.records)

    def pages(
        self, scope: str, owner: Owner | None = None
    ) -> list[tuple[tuple[dict[str, Any], ...], str | None]]:
        """Every page of `scope` one record at a time, with each page's cursor,
        through the first empty page."""
        secret, workspace = owner or (self.owner_secret, self.workspace)
        reader = self.intake(secret, workspace=workspace)
        pages = []
        after = None
        while True:
            page = reader.read(
                ReadEngineRecords(engine_scope=scope, after=after, limit=1)
            )
            pages.append((page.records, page.next))
            if not page.records:
                return pages
            after = page.next

    def call(
        self,
        function: str,
        request: Any,
        secret: str | None = None,
        workspace: uuid.UUID | None = None,
    ) -> str:
        """One raw entry-point call: its exact JSON text, or its database error."""
        with self.db.transaction():
            self.db.execute("SET LOCAL ROLE memoriesql_application")
            self.db.execute(
                "SELECT memoriesql.begin_authorization_context(%s,%s)",
                (secret or self.engine_secret, workspace or self.workspace),
            )
            row = self.db.execute(
                sql.SQL("SELECT memoriesql.{}(%s)::text").format(
                    sql.Identifier(function)
                ),
                (Jsonb(request),),
            ).fetchone()
        assert row is not None
        return str(row[0])

    def answer(self, record: dict[str, Any], secret: str | None = None) -> str:
        """The exact JSON text of one record's batch."""
        return self.call(
            "accept_engine_records_v1",
            AcceptEngineRecords(records=(record,)).model_dump(mode="json"),
            secret,
        )

    def count(self, table: str) -> int:
        row = self.db.execute(
            sql.SQL("SELECT count(*) FROM memoriesql.{}").format(sql.Identifier(table))
        ).fetchone()
        assert row is not None
        return int(row[0])

    def written(self) -> dict[str, int]:
        return {table: self.count(table) for table in WRITTEN}

    @contextlib.contextmanager
    def held_open(
        self, *records: dict[str, Any], secret: str | None = None
    ) -> Iterator[None]:
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
                            (secret or self.engine_secret, self.workspace),
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


class EngineRecordIntake(EngineIntakeFixture):
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
            self.deliver(altered),
            [EngineRecordConflict(outcome="conflict", stored=chunk)],
        )

        late = type_decision(
            chunk, {"status": "unassigned", "reason": "no_fit"}, model()
        )
        self.deliver(late)
        other = type_decision(chunk, assigned("outcome"), model())
        (result,) = self.deliver(other)
        self.assertEqual(result, EngineRecordConflict(outcome="conflict", stored=late))

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

    def test_a_caller_who_may_not_read_learns_nothing_about_held_records(
        self,
    ) -> None:
        pending, undelivered = event_records(
            "acme",
            "orchard/14",
            ["Held one. ", "Held two."],
            [({"status": "pending", "reason": "capacity"}, None)]
            + [(assigned("observation"), rule())],
        )
        final = one("acme", "orchard/15")
        self.deliver(pending, final)
        conflicting = copy.deepcopy(pending)
        conflicting["facets"] = {"lengths": {"bytes": 1}}
        ordinal_taken = copy.deepcopy(pending)
        other_id = str(uuid.uuid4())
        ordinal_taken["record_key"] = "chunk:" + other_id
        ordinal_taken["memory"]["chunk"]["chunk_id"] = other_id
        event_differs = copy.deepcopy(undelivered)
        event_differs["event"]["chunk_count"] = 3
        # Each probe's answer to a caller who may read acme.
        probes = {
            "identical": (
                pending,
                EngineRecordAlreadyAccepted(outcome="already_accepted"),
            ),
            "conflicting": (
                conflicting,
                EngineRecordConflict(outcome="conflict", stored=pending),
            ),
            "ordinal taken": (
                ordinal_taken,
                EngineRecordRejected(
                    outcome="rejected",
                    reason="another chunk already holds ordinal 0 of its event",
                ),
            ),
            "event differs": (
                event_differs,
                EngineRecordRejected(
                    outcome="rejected",
                    reason="the event differs from the one its earlier chunks carried",
                ),
            ),
            "absent chunk": (
                type_decision(undelivered, assigned("decision"), model()),
                EngineRecordRejected(
                    outcome="rejected",
                    reason="no accepted chunk has this chunk_id in this engine scope",
                ),
            ),
            "final chunk": (
                type_decision(final, assigned("decision"), model()),
                EngineRecordRejected(
                    outcome="rejected", reason="the chunk already has a final type"
                ),
            ),
        }
        deliver_only: tuple[str, ...] = ("engine.records.deliver",)
        write: Permissions = ("write",)
        read: Permissions = ("read",)
        callers = {
            "deliver only": self.engine_for(
                self.acme, capabilities=deliver_only, permissions=write
            ),
            "capability only": self.engine_for(self.acme, permissions=write),
            "grant only": self.engine_for(self.acme, capabilities=deliver_only),
            "read only": self.engine_for(self.acme, permissions=read),
        }
        before = self.written()
        nowhere = {
            name: self.answer(in_scope(record, "nowhere"))
            for name, (record, _) in probes.items()
        }
        self.assertEqual(set(nowhere.values()), {self.answer(one("nowhere", "x"))})
        for caller, secret in callers.items():
            for name, (record, _) in probes.items():
                self.assertEqual(
                    self.answer(record, secret), nowhere[name], (caller, name)
                )
            self.assertEqual(
                self.deliver(*(record for record, _ in probes.values()), secret=secret),
                [UNAVAILABLE] * len(probes),
                caller,
            )
        self.assertEqual(self.written(), before)
        # A caller who delivers and reads is told what it may read anyway.
        for name, (record, expected) in probes.items():
            self.assertEqual(self.deliver(record), [expected], name)
        self.assertEqual(self.written(), before)
        self.assertEqual(self.held(), [pending, final])

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

    def test_every_scope_a_caller_may_not_deliver_to_and_read_gets_one_answer(
        self,
    ) -> None:
        elsewhere = one("beta", "orchard/6")
        here = one("acme", "orchard/7")
        later = self.now + timedelta(minutes=30)
        write: Permissions = ("write",)
        read: Permissions = ("read",)
        # Each case's answer to [elsewhere, here], where here alone is deliverable.
        answers = {
            "not enrolled": self.call(
                "accept_engine_records_v1",
                AcceptEngineRecords(records=(elsewhere, here)).model_dump(mode="json"),
            )
        }
        self.assertEqual(self.count("engine_records"), 0)
        beta = self.enroll("beta")
        cases: dict[str, list[tuple[Permissions, datetime | None]]] = {
            "no grant": [],
            "write only": [(write, None)],
            "read only": [(read, None)],
            "write window not open": [(read, None), (write, later)],
            "read window not open": [(write, None), (read, later)],
        }
        for case, grants in cases.items():
            principal = uuid.uuid4()
            secret = sha("fictional engine credential: " + case)
            self.pair(
                principal, secret, [self.acme.access_scope_id, beta.access_scope_id]
            )
            self.grant(self.acme.source_object_id, principal)
            for permissions, valid_from in grants:
                self.grant(
                    beta.source_object_id, principal, permissions, valid_from=valid_from
                )
            answers[case] = self.call(
                "accept_engine_records_v1",
                AcceptEngineRecords(records=(elsewhere, here)).model_dump(mode="json"),
                secret,
            )
        # Granted beta but not paired for its scope.
        self.grant(beta.source_object_id, self.engine)
        answers["not paired"] = self.call(
            "accept_engine_records_v1",
            AcceptEngineRecords(records=(elsewhere, here)).model_dump(mode="json"),
        )
        self.assertEqual(len(set(answers.values())), 1, answers)
        (answer,) = set(answers.values())
        self.assertEqual(
            json.loads(answer)["results"], [UNAVAILABLE.model_dump(mode="json")] * 2
        )
        self.assertEqual(self.count("engine_records"), 0)

        allowed = self.engine_for(self.acme, beta)
        self.assertEqual(
            self.deliver(elsewhere, here, secret=allowed),
            [EngineRecordAccepted(outcome="accepted")] * 2,
        )
        self.assertEqual(self.held("beta"), [elsewhere])

    def test_an_undeliverable_scope_answers_without_waiting_on_its_lock(
        self,
    ) -> None:
        held_record = one("acme", "orchard/40")
        probe = one("acme", "orchard/41")
        deliver_only: tuple[str, ...] = ("engine.records.deliver",)
        write: Permissions = ("write",)
        read: Permissions = ("read",)
        writer = self.engine_for(
            self.acme, capabilities=deliver_only, permissions=write
        )
        reader = self.engine_for(self.acme, permissions=read)
        request = AcceptEngineRecords(records=(probe,))
        with self.held_open(held_record):
            with psycopg.connect(self.url, autocommit=True) as other:
                # Neither waits on acme's writer lock, nor does a scope that
                # does not exist; all three answers are the same.
                for secret, delivered in (
                    (writer, request),
                    (reader, request),
                    (
                        self.engine_secret,
                        AcceptEngineRecords(records=(in_scope(probe, "nowhere"),)),
                    ),
                ):
                    self.assertEqual(
                        list(self.intake(secret, other).accept(delivered).results),
                        [UNAVAILABLE],
                    )
                # A caller who may deliver to and read acme does wait for it.
                with self.assertRaises(EngineIntakeBusy) as busy:
                    self.intake(connection=other).accept(request)
                self.assertIsInstance(
                    busy.exception.__cause__, psycopg.errors.LockNotAvailable
                )
        self.assertEqual(self.held(), [held_record])

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
        write: Permissions = ("write",)
        writer = self.engine_for(
            self.acme, capabilities=("engine.records.deliver",), permissions=write
        )
        with self.assertRaises(PermissionError):
            self.intake(writer).read(ReadEngineRecords(engine_scope="acme"))
        with self.assertRaises(PermissionError):
            self.intake(self.owner_secret).read(ReadEngineRecords(engine_scope="nope"))
        page = self.intake().read(ReadEngineRecords(engine_scope="acme", limit=1))
        self.assertEqual(page.records, (record,))
        empty = self.intake(self.owner_secret).read(
            ReadEngineRecords(engine_scope="acme", after=page.next)
        )
        self.assertEqual((empty.records, empty.next), ((), page.next))
        self.enroll("beta")
        nothing = self.intake(self.owner_secret).read(
            ReadEngineRecords(engine_scope="beta")
        )
        self.assertEqual((nothing.records, nothing.next), ((), None))

    def test_cursors_are_examined_only_after_read_authorization(self) -> None:
        self.deliver(one("acme", "orchard/42"))
        beta_engine = self.beta_engine()
        first, second = one("beta", "orchard/43"), one("beta", "orchard/44")
        self.deliver(first, second, secret=beta_engine)
        ((_, mine), _) = self.pages("acme")
        ((_, theirs), *_) = self.pages("beta")
        # The owner reads both scopes. Beta's cursor continues beta only.
        page = self.intake(self.owner_secret).read(
            ReadEngineRecords(engine_scope="beta", after=theirs, limit=1)
        )
        self.assertEqual(page.records, (second,))
        write: Permissions = ("write",)
        writer = self.engine_for(
            self.acme, capabilities=("engine.records.deliver",), permissions=write
        )
        cursors: list[Any] = [None, mine, theirs, "nope", " ", "x" * 129, 7, {}]
        # A scope that does not exist and one the caller may not read: every
        # cursor gets the same refusal.
        for scope, secret in (("nowhere", self.owner_secret), ("acme", writer)):
            for cursor in cursors:
                with self.subTest(scope=scope, cursor=cursor):
                    with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                        self.call(
                            "read_engine_records_v1",
                            {
                                "contract_version": 1,
                                "engine_scope": scope,
                                "after": cursor,
                            },
                            secret,
                        )
        # A readable scope: only its own cursors continue a read.
        for cursor in cursors:
            request = {"contract_version": 1, "engine_scope": "acme", "after": cursor}
            with self.subTest(cursor=cursor):
                if cursor in (None, mine):
                    page = json.loads(
                        self.call("read_engine_records_v1", request, self.owner_secret)
                    )
                    self.assertEqual(set(page), {"contract_version", "records", "next"})
                else:
                    with self.assertRaises(psycopg.errors.InvalidParameterValue):
                        self.call("read_engine_records_v1", request, self.owner_secret)

    def test_other_scopes_and_tenants_never_change_a_readers_cursors(self) -> None:
        quiet, busy, beta = (self.enroll(name) for name in ("quiet", "busy", "beta"))
        ours = self.engine_for(quiet, busy, beta)
        other_owner = self.other_tenant()
        other_busy = self.enroll("busy", other_owner)
        theirs = self.engine_for(other_busy, owner=other_owner)
        records = [one("quiet", f"orchard/5{n}") for n in range(3)]
        for record in records:
            self.deliver(record, secret=ours)

        def rolled_back(record: dict[str, Any]) -> None:
            with self.db.transaction():
                self.db.execute("SET LOCAL ROLE memoriesql_application")
                self.db.execute(
                    "SELECT memoriesql.begin_authorization_context(%s,%s)",
                    (ours, self.workspace),
                )
                self.db.execute(
                    "SELECT memoriesql.accept_engine_records_v1(%s)",
                    (
                        Jsonb(
                            AcceptEngineRecords(records=(record,)).model_dump(
                                mode="json"
                            )
                        ),
                    ),
                )
                raise psycopg.Rollback()

        # The same records in busy, each after foreign acceptances: another
        # scope, another tenant's scope of the same name, and a rollback.
        foreign = []
        for n, record in enumerate(records):
            self.deliver(one("beta", f"orchard/6{n}"), secret=ours)
            foreign.append(one("busy", f"orchard/7{n}"))
            accepted = self.intake(theirs, workspace=other_owner[1]).accept(
                AcceptEngineRecords(records=(foreign[-1],))
            )
            self.assertEqual(
                list(accepted.results), [EngineRecordAccepted(outcome="accepted")]
            )
            rolled_back(one("beta", f"orchard/8{n}"))
            self.deliver(in_scope(record, "busy"), secret=ours)

        quiet_pages = self.pages("quiet")
        busy_pages = self.pages("busy")
        self.assertEqual(
            [cursor for _, cursor in busy_pages], [cursor for _, cursor in quiet_pages]
        )
        self.assertEqual(
            [page for page, _ in busy_pages],
            [
                tuple(in_scope(record, "busy") for record in page)
                for page, _ in quiet_pages
            ],
        )
        # No gaps: one record per page, every record once, in acceptance order.
        self.assertEqual(
            [page for page, _ in quiet_pages], [(r,) for r in records] + [()]
        )
        self.assertEqual(quiet_pages[-1][1], quiet_pages[-2][1])
        for _, cursor in quiet_pages:
            assert cursor is not None
            self.assertFalse(cursor.isdigit(), cursor)
        # The foreign acceptances did consume global sequence numbers between
        # busy's records, which a global cursor would have shown.
        sequence = [
            row[0]
            for row in self.db.execute(
                "SELECT record_seq FROM memoriesql.engine_records "
                "WHERE source_object_id = %s ORDER BY record_seq",
                (busy.source_object_id,),
            ).fetchall()
        ]
        self.assertTrue(
            all(b - a > 1 for a, b in zip(sequence, sequence[1:])), sequence
        )
        self.assertEqual(self.held("busy", other_owner), foreign)

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
        executable = self.db.execute(
            "SELECT p.proname FROM pg_proc p "
            "JOIN pg_namespace n ON n.oid = p.pronamespace "
            "WHERE n.nspname = 'memoriesql' "
            "AND (p.proname LIKE 'engine%%' OR p.proname LIKE '%%engine_records_v1') "
            "AND has_function_privilege('memoriesql_application', p.oid, 'EXECUTE') "
            "ORDER BY 1"
        ).fetchall()
        self.assertEqual(
            executable,
            [
                ("accept_engine_records_v1",),
                ("engine_vocabulary_v1",),
                ("read_engine_records_v1",),
            ],
        )
        # Replacing a function keeps only its owner and grants, so 0056 states
        # each one's definer, search path, volatility and lock timeout again.
        attributes = self.db.execute(
            "SELECT p.proname, p.prosecdef, p.provolatile, p.proconfig, EXISTS ("
            " SELECT 1 FROM aclexplode(COALESCE(p.proacl, acldefault('f', p.proowner)))"
            " AS entry WHERE entry.grantee = 0 AND entry.privilege_type = 'EXECUTE') "
            "FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
            "WHERE n.nspname = 'memoriesql' AND p.proname = ANY(%s) ORDER BY 1",
            (
                [
                    "engine_deliverable_source_v1",
                    "engine_accept_record_v1",
                    "accept_engine_records_v1",
                    "read_engine_records_v1",
                ],
            ),
        ).fetchall()
        path = "search_path=pg_catalog, memoriesql"
        self.assertEqual(
            attributes,
            [
                (
                    "accept_engine_records_v1",
                    True,
                    "v",
                    [path, "lock_timeout=500ms"],
                    False,
                ),
                ("engine_accept_record_v1", True, "v", [path], False),
                ("engine_deliverable_source_v1", True, "v", [path], False),
                ("read_engine_records_v1", True, "s", [path], False),
            ],
        )

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
                # instead of committing a later record before it, which a
                # reader's cursor would then pass.
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
            self.deliver(altered),
            [EngineRecordConflict(outcome="conflict", stored=taken[0])],
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


class EngineIntakeUpgrade(EngineIntakeFixture):
    schema_version = 55

    def test_records_accepted_under_0055_page_by_scope_after_0056(self) -> None:
        beta_engine = self.beta_engine()
        other_owner = self.other_tenant()
        other_acme = self.enroll("acme", other_owner)
        theirs = self.engine_for(other_acme, owner=other_owner)
        delivered: dict[tuple[str, int], list[dict[str, Any]]] = {
            ("acme", 1): [],
            ("beta", 1): [],
            ("acme", 2): [],
        }
        # Interleaved across two scopes of one tenant and a second tenant.
        for n in range(2):
            for scope, tenant, secret, workspace in (
                ("acme", 1, self.engine_secret, self.workspace),
                ("beta", 1, beta_engine, self.workspace),
                ("acme", 2, theirs, other_owner[1]),
            ):
                record = one(scope, f"orchard/9{n}-{scope}-{tenant}")
                accepted = self.intake(secret, workspace=workspace).accept(
                    AcceptEngineRecords(records=(record,))
                )
                self.assertEqual(
                    list(accepted.results), [EngineRecordAccepted(outcome="accepted")]
                )
                delivered[(scope, tenant)].append(record)

        migrate(self.db, expected_current_version=55, target_version=56)

        owners: dict[int, Owner] = {
            1: (self.owner_secret, self.workspace),
            2: other_owner,
        }
        for (scope, tenant), records in delivered.items():
            pages = self.pages(scope, owners[tenant])
            self.assertEqual(
                [page for page, _ in pages], [(r,) for r in records] + [()]
            )
        # A cursor taken over records from 0055 continues into later ones.
        last = self.pages("acme")[-1][1]
        newer = one("acme", "orchard/99")
        self.assertEqual(
            self.deliver(newer), [EngineRecordAccepted(outcome="accepted")]
        )
        page = self.intake(self.owner_secret).read(
            ReadEngineRecords(engine_scope="acme", after=last)
        )
        self.assertEqual(page.records, (newer,))


if __name__ == "__main__":
    unittest.main()
