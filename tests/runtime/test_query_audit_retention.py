"""Governed query audit retention (M0047); execute only in an assigned DB slot.

Fictional data. The established helper fixture authors its input at schema 43
and migrates to 46; these cases then install 47 and use the real executor.
"""

from __future__ import annotations

import json
import os
import unittest
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import psycopg
from psycopg import errors

from memoriesql.infrastructure.postgres.relation_projection import (
    relation_projection_frame,
)
from memoriesql.infrastructure.postgres.result_preparation import (
    PostgresResultPreparation,
)

if TYPE_CHECKING:
    from tests.runtime import test_agent_sql_helpers_runtime as helpers
    from tests.runtime.test_postgres_runtime import migrate
else:
    import test_agent_sql_helpers_runtime as helpers
    from test_postgres_runtime import migrate

ROW_BYTES = 2048
WORKSPACE_BYTES = 536870912
ATTRIBUTED = (
    "SELECT a.authorization_audit_event_id,a.resource_id,a.capability_key,a.decision,"
    "a.recorded_at,e.delivery_ref,e.workspace_id,e.expires_at "
    "FROM memoriesql.query_audit_events e "
    "JOIN memoriesql.authorization_audit_events a USING(tenant_id,authorization_audit_event_id)"
)
ORDER = " ORDER BY a.authorization_audit_event_id"


@unittest.skipUnless(
    os.environ.get("N1_TEST_DATABASE_URL"), "assigned disposable PostgreSQL required"
)
class QueryAuditRetention(unittest.TestCase):
    def setUp(self) -> None:
        self.h = helpers.AgentSqlHelperRuntime(methodName="runTest")
        self.addCleanup(self.h.doCleanups)
        self.h.setUp()
        self.r, self.db, self.fixture = self.h.r, self.h.db, self.h.fixture
        migrate(self.db, expected_current_version=46, target_version=47)

    # -- helpers ---------------------------------------------------------

    def scalar(self, statement: str, values: tuple[Any, ...] = ()) -> Any:
        row = self.db.execute(statement, values).fetchone()
        return row[0] if row else None

    def attributed(self, delivery: str | None = None) -> list[tuple[Any, ...]]:
        if delivery is None:
            return self.db.execute(ATTRIBUTED + ORDER).fetchall()
        return self.db.execute(
            ATTRIBUTED + " WHERE e.delivery_ref=%s" + ORDER, (delivery,)
        ).fetchall()

    def audit_rows(self) -> int:
        count: int = self.scalar(
            "SELECT count(*) FROM memoriesql.authorization_audit_events"
        )
        return count

    def source_of(self, pin: dict[str, Any]) -> UUID:
        source: UUID = self.scalar(
            "SELECT source_object_id FROM memoriesql.evidence_packages WHERE package_id=%s",
            (pin["package"]["package_id"],),
        )
        return source

    @contextmanager
    def frame(self) -> Iterator[psycopg.Connection[Any]]:
        with self.fixture.connection() as connection:
            with relation_projection_frame(
                connection,
                credential_sha256=self.fixture.secret_hash,
                workspace_id=self.fixture.workspace,
            ) as frame:
                yield frame

    def authorize_outside_query_work(self, source: UUID, claim: str) -> None:
        """The same principal's own decision, while naming no live delivery."""
        with self.frame() as frame:
            frame.execute(
                "SELECT set_config('memoriesql.query_audit_delivery',%s,true)", (claim,)
            )
            frame.execute(
                "SELECT memoriesql.application_authorize_resource("
                "'source',%s,'source.raw.read','read',uuidv7())",
                (source,),
            )

    def inspect(self, run: dict[str, Any], bead: UUID) -> dict[str, Any]:
        raw, reply = self.h.send(
            self.h.inspection(run, beads=(bead,), facets=("evidence_metadata",))
        )
        self.h.available(raw, reply)
        return reply

    def retention(self) -> dict[str, Any]:
        reply = json.loads(self.r.service().cleanup_status())
        self.assertEqual(reply["outcome"], "available", reply)
        status: dict[str, Any] = reply["audit_retention"]
        return status

    def age(self, delivery: str) -> list[UUID]:
        """Move one delivery's attributed rows past their 30-day window."""
        rows = self.db.execute(
            "UPDATE memoriesql.query_audit_events SET expires_at=clock_timestamp()-interval '1 second' "
            "WHERE delivery_ref=%s RETURNING authorization_audit_event_id",
            (delivery,),
        ).fetchall()
        self.assertTrue(rows)
        return [row[0] for row in rows]

    def refused(self, statement: str, values: tuple[Any, ...], *, marker: bool) -> None:
        with self.db.transaction():
            if marker:
                self.db.execute(
                    "SELECT set_config('memoriesql.query_audit_expiry','on',true)"
                )
            with self.assertRaises(errors.ObjectNotInPrerequisiteState) as caught:
                with self.db.transaction():
                    self.db.execute(statement, values)
        self.assertIn("is append-only", str(caught.exception))

    # -- cases -------------------------------------------------------------

    def test_every_query_operation_attributes_all_its_audit_and_nothing_else_does(
        self,
    ) -> None:
        # Both ways: every audit row a query (population, commit and its page),
        # a page re-read (reuse), an inspection and a hydration write is
        # attributed to its own delivery, and no row outside a live one is.
        before = self.audit_rows()
        run = self.r.start()
        _, result = self.r.query(
            run,
            "SELECT s.statement_id,s.bead_id,e.evidence_ref FROM memory_v1.statements s "
            "JOIN memory_v1.statement_sources e ON e.statement_id=s.statement_id "
            "ORDER BY s.statement_id,e.evidence_ref",
            page_size=50,
        )
        self.assertEqual(result["outcome"], "available", result)
        reused = self.r.reuse(run, result["result"], page_size=50)
        self.assertEqual(reused["outcome"], "available", reused)
        inspected = self.inspect(run, self.h.normalized)
        pin = inspected["inspection"]["evidence_pins"][0]
        raw, hydrated = self.h.send(self.h.hydration(run, pin))
        self.h.available(raw, hydrated)
        operations = {
            name: reply["access_receipt_ref"]
            for name, reply in (
                ("query", result),
                ("reuse", reused),
                ("inspect", inspected),
                ("hydrate", hydrated),
            )
        }
        rows = self.attributed()
        per_delivery = Counter(str(row[5]) for row in rows)
        for name, delivery in operations.items():
            self.assertGreater(per_delivery[delivery], 0, name)
        self.assertEqual(set(per_delivery), set(operations.values()))
        for *_head, recorded, _ref, space, expires in rows:
            self.assertEqual(space, self.fixture.workspace)
            self.assertEqual(expires - recorded, timedelta(days=30))
        self.assertEqual(self.audit_rows() - before, len(rows))
        # The same principal's own decisions are kept as before: a claim that
        # names no live, reserved delivery attributes nothing.
        source = self.source_of(pin)
        for claim in ("not-a-delivery", str(uuid4()), operations["hydrate"]):
            self.authorize_outside_query_work(source, claim)
        self.assertEqual(self.audit_rows() - before, len(rows) + 3)
        self.assertEqual(len(self.attributed()), len(rows))
        status = self.retention()
        self.assertEqual(
            status["retained"],
            {"audit_rows": len(rows), "charged_bytes": len(rows) * ROW_BYTES},
        )
        self.assertEqual((status["due_rows"], status["overdue_rows"]), (0, 0))

    def test_one_statement_records_each_decision_of_its_delivery_once(self) -> None:
        run = self.r.start()
        private = self.inspect(run, self.h.normalized)["inspection"]["evidence_pins"][0]
        visible = self.inspect(run, self.h.source)["inspection"]["evidence_pins"][0]
        request = self.h.hydration(run, private, pages=True)
        request["selections"][-1] = dict(
            self.h.hydration(run, visible)["selections"][0], end=20
        )
        raw, hydrated = self.h.send(request)
        self.h.available(raw, hydrated)
        rows = self.attributed(hydrated["access_receipt_ref"])
        # Seven selections re-check one source in the same statement: one row
        # per decision and statement, and both distinct sources are kept.
        per_statement = Counter(
            (resource, capability, decision, recorded)
            for _e, resource, capability, decision, recorded, *_rest in rows
        )
        self.assertEqual(set(per_statement.values()), {1})
        self.assertEqual(
            {resource for _e, resource, *_rest in rows},
            {self.source_of(private), self.source_of(visible)},
        )

    def test_governed_expiry_removes_only_rows_past_their_window_and_releases_them(
        self,
    ) -> None:
        run = self.r.start()
        first = self.inspect(run, self.h.normalized)["access_receipt_ref"]
        second = self.inspect(run, self.h.source)["access_receipt_ref"]
        kept = self.attributed(second)
        unattributed = self.audit_rows() - len(self.attributed())
        expired = self.age(first)
        before = self.retention()["retained"]
        cleanup = json.loads(self.r.service().cleanup_expired())
        self.assertEqual(cleanup["outcome"], "available", cleanup)
        self.assertEqual(
            cleanup["cleanup"]["audits"],
            {"audit_rows": len(expired), "released_bytes": len(expired) * ROW_BYTES},
        )
        self.assertEqual(
            self.scalar(
                "SELECT count(*) FROM memoriesql.authorization_audit_events "
                "WHERE authorization_audit_event_id=ANY(%s)",
                (expired,),
            ),
            0,
        )
        self.assertEqual(self.attributed(first), [])
        self.assertEqual(self.attributed(second), kept)
        self.assertEqual(self.audit_rows() - len(self.attributed()), unattributed)
        self.assertEqual(
            self.retention()["retained"],
            {
                "audit_rows": before["audit_rows"] - len(expired),
                "charged_bytes": before["charged_bytes"] - len(expired) * ROW_BYTES,
            },
        )
        again = json.loads(self.r.service().cleanup_expired())
        self.assertEqual(
            again["cleanup"]["audits"], {"audit_rows": 0, "released_bytes": 0}
        )

    def test_append_only_audit_refuses_everything_but_the_governed_expiry(
        self,
    ) -> None:
        run = self.r.start()
        reply = self.inspect(run, self.h.normalized)
        delivery = reply["access_receipt_ref"]
        source = self.source_of(reply["inspection"]["evidence_pins"][0])
        self.authorize_outside_query_work(source, "none")
        plain = self.scalar(
            "SELECT authorization_audit_event_id FROM memoriesql.authorization_audit_events a "
            "WHERE NOT EXISTS(SELECT 1 FROM memoriesql.query_audit_events e "
            "WHERE e.tenant_id=a.tenant_id AND e.authorization_audit_event_id=a.authorization_audit_event_id) "
            "ORDER BY recorded_at DESC LIMIT 1"
        )
        live = self.attributed(delivery)[0][0]
        delete = "DELETE FROM memoriesql.authorization_audit_events WHERE authorization_audit_event_id=%s"
        update = (
            "UPDATE memoriesql.authorization_audit_events SET decision_reason=decision_reason "
            "WHERE authorization_audit_event_id=%s"
        )
        # Unattributed audit and attributed audit inside its window: refused.
        for event in (plain, live):
            for marker in (False, True):
                self.refused(delete, (event,), marker=marker)
                self.refused(update, (event,), marker=marker)
        expired = self.age(delivery)
        # Past its window: still never updated, and never deleted unmarked.
        for marker in (False, True):
            self.refused(update, (expired[0],), marker=marker)
        self.refused(delete, (expired[0],), marker=False)
        # The application role holds no delete privilege at all.
        with self.frame() as frame:
            frame.execute(
                "SELECT set_config('memoriesql.query_audit_expiry','on',true)"
            )
            with self.assertRaises(errors.InsufficientPrivilege):
                with frame.transaction():
                    frame.execute(delete, (expired[0],))
        cleanup = json.loads(self.r.service().cleanup_expired())
        self.assertEqual(cleanup["cleanup"]["audits"]["audit_rows"], len(expired))
        self.assertEqual(
            self.scalar(
                "SELECT count(*) FROM memoriesql.authorization_audit_events "
                "WHERE authorization_audit_event_id=%s",
                (plain,),
            ),
            1,
        )

    def test_retained_audit_counts_against_the_workspace_allocation_until_expiry(
        self,
    ) -> None:
        run = self.r.start()
        delivery = self.inspect(run, self.h.normalized)["access_receipt_ref"]
        charged = len(self.attributed()) * ROW_BYTES
        self.assertGreater(charged, 0)
        used = self.scalar(
            "SELECT COALESCE(sum(CASE WHEN state='reserved' THEN reservation_bytes "
            "ELSE allocation_bytes END),0) FROM memoriesql.result_preparation_operations "
            "WHERE workspace_id=%s",
            (self.fixture.workspace,),
        )
        # Fill the workspace allocation so that a minimal reservation fits only
        # without the retained audit charge.
        padding = WORKSPACE_BYTES - 8192 - charged + ROW_BYTES - used
        while padding > 0:
            amount = min(padding, 67108864)
            self.db.execute(
                "INSERT INTO memoriesql.result_preparation_operations(tenant_id,workspace_id,"
                "principal_id,owner_user_id,run_ref,step_key,operation_ref,ownership_ref,"
                "request_fingerprint,state,reservation_bytes,encoded_bytes,allocation_bytes) "
                "VALUES(%s,%s,%s,NULL,%s,%s,%s,%s,%s,'sealed',%s,0,%s)",
                (
                    self.fixture.tenant,
                    self.fixture.workspace,
                    self.fixture.principal,
                    uuid4(),
                    uuid4(),
                    uuid4(),
                    uuid4(),
                    "f" * 64,
                    max(8192, amount),
                    amount,
                ),
            )
            padding -= amount

        def reserve() -> None:
            with self.fixture.connection() as connection:
                PostgresResultPreparation(
                    connection,
                    credential_sha256=self.fixture.secret_hash,
                    workspace_id=self.fixture.workspace,
                ).reserve(
                    run_ref=UUID(run["run_ref"]),
                    step_key=uuid4(),
                    request_fingerprint="e" * 64,
                    reservation_bytes=8192,
                )

        with self.assertRaises(psycopg.Error) as caught:
            reserve()
        self.assertEqual(caught.exception.sqlstate, "54000")
        self.age(delivery)
        cleanup = json.loads(self.r.service().cleanup_expired())
        self.assertEqual(cleanup["cleanup"]["audits"]["released_bytes"], charged)
        reserve()

    def test_audit_growth_is_charged_at_least_its_physical_size_and_released(
        self,
    ) -> None:
        size = (
            "SELECT pg_total_relation_size('memoriesql.authorization_audit_events')"
            "+pg_total_relation_size('memoriesql.query_audit_events')"
        )
        relations = (
            "SELECT c.relname,pg_relation_size(c.oid) FROM pg_class c "
            "WHERE c.oid=ANY(%(tables)s::regclass[]) OR c.oid IN "
            "(SELECT indexrelid FROM pg_index WHERE indrelid=ANY(%(tables)s::regclass[])) "
            "ORDER BY c.relname"
        )
        tables = [
            "memoriesql.authorization_audit_events",
            "memoriesql.query_audit_events",
        ]

        def breakdown() -> dict[str, int]:
            return dict(self.db.execute(relations, {"tables": tables}).fetchall())

        def pins(run: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
            private = self.inspect(run, self.h.normalized)
            visible = self.inspect(run, self.h.source)
            return (
                private["inspection"]["evidence_pins"][0],
                visible["inspection"]["evidence_pins"][0],
            )

        def wave(target: int) -> int:
            """Hydrate two sources per delivery until `target` rows are added."""
            start, runs, accesses = len(self.attributed()), 0, 0
            run = self.r.start()
            private, visible = pins(run)
            while len(self.attributed()) - start < target and runs < 3:
                if accesses >= 120:
                    # Stay inside one run's 128 accesses.
                    self.r.close(run)
                    run, accesses, runs = self.r.start(), 0, runs + 1
                    private, visible = pins(run)
                request = self.h.hydration(run, private)
                request["selections"].append(
                    dict(self.h.hydration(run, visible)["selections"][0], end=20)
                )
                raw, hydrated = self.h.send(request)
                self.h.available(raw, hydrated)
                accesses += 1
            self.r.close(run)
            return len(self.attributed()) - start

        def expire_all() -> int:
            self.db.execute(
                "UPDATE memoriesql.query_audit_events "
                "SET expires_at=clock_timestamp()-interval '1 second'"
            )
            cleanup = json.loads(self.r.service().cleanup_expired())
            self.assertEqual(cleanup["outcome"], "available", cleanup)
            released: int = cleanup["cleanup"]["audits"]["released_bytes"]
            self.assertEqual(
                self.retention()["retained"], {"audit_rows": 0, "charged_bytes": 0}
            )
            return released

        # Warm the attribution table's and its indexes' first pages.
        wave(1)
        expire_all()
        base, base_parts = self.scalar(size), breakdown()
        first = wave(256)
        peak, peak_parts = self.scalar(size) - base, breakdown()
        released = expire_all()
        # Expired rows' space is reused once vacuumed (twice, so emptied index
        # pages can be recycled too); the whole footprint since the baseline
        # must stay within what the rows retained now are charged.
        for _ in range(2):
            self.db.execute(
                "VACUUM memoriesql.authorization_audit_events,memoriesql.query_audit_events"
            )
        second = wave(256)
        steady, steady_parts = self.scalar(size) - base, breakdown()
        self.capacity = {
            "row_charge_bytes": ROW_BYTES,
            "relations_bytes": {
                "baseline": base_parts,
                "after_first_wave": peak_parts,
                "after_expiry_vacuum_and_second_wave": steady_parts,
            },
            "first_wave": {"rows": first, "physical_bytes": peak},
            "second_wave_after_expiry_and_vacuum": {
                "rows": second,
                "physical_bytes_since_baseline": steady,
            },
        }
        self.assertGreaterEqual(first, 256)
        self.assertGreaterEqual(second, 256)
        self.assertLessEqual(peak, first * ROW_BYTES, (peak, first))
        self.assertEqual(released, first * ROW_BYTES)
        self.assertLessEqual(steady, second * ROW_BYTES, (steady, second))


if __name__ == "__main__":
    unittest.main()
