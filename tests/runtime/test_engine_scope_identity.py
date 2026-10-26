"""Installed fictional engine identity within its engine scope (schema 55)."""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any

import psycopg

from memoriesql.application.engine_intake import (
    AcceptEngineRecords,
    EngineRecordAccepted,
    EngineRecordAlreadyAccepted,
    EngineRecordConflict,
)

# The intake module is imported whole, so its schema-54 test class is not
# collected a second time from here.
if TYPE_CHECKING:
    from tests.runtime import test_engine_intake as intake
    from tests.runtime.test_postgres_runtime import migrate
else:
    import test_engine_intake as intake
    from test_postgres_runtime import migrate

PENDING = {"status": "pending", "reason": "capacity"}


def in_scope(record: dict[str, Any], scope: str) -> dict[str, Any]:
    """The same record, ids and all, delivered under another engine scope."""
    moved = copy.deepcopy(record)
    moved["scope"] = scope
    if "event" in moved:
        moved["event"]["key"]["scope"] = scope
    return moved


class EngineRecordIntakeAtSchema55(intake.EngineRecordIntake):
    """Every schema-54 intake behavior still holds, except where overridden."""

    schema_version = 55
    receipt_kind = "engine_record.accept.v2"

    def test_naming_another_scopes_ids_never_holds_it_back(self) -> None:
        first, second = intake.event_records(
            "beta",
            "orchard/18",
            ["Theirs, first. ", "Theirs, second."],
            [(PENDING, None), (intake.assigned("observation"), intake.rule())],
        )
        other_secret = self.beta_engine()
        self.intake(other_secret).accept(AcceptEngineRecords(records=(first,)))
        # Beta's record key and event id now name acme's own record and event.
        borrowed_key = in_scope(first, "acme")
        (borrowed_event,) = intake.event_records(
            "acme",
            "orchard/19",
            ["Borrowed."],
            [(intake.assigned("observation"), intake.rule())],
        )
        borrowed_event["event"]["event_id"] = first["event"]["event_id"]
        with self.held_open(borrowed_key, borrowed_event):
            with psycopg.connect(self.url, autocommit=True) as other:
                late = intake.type_decision(
                    first, intake.assigned("observation"), intake.model()
                )
                accepted = self.intake(other_secret, other).accept(
                    AcceptEngineRecords(records=(second, late))
                )
                self.assertEqual(
                    list(accepted.results),
                    [EngineRecordAccepted(outcome="accepted")] * 2,
                )
        # The borrowed event id met acme's own event, which carried another
        # event object, so only the borrowed key was kept.
        self.assertEqual(self.held(), [borrowed_key])
        self.assertEqual(self.held("beta"), [first, second, late])
        self.assertEqual(self.count("engine_event_completions"), 1)

    def test_a_scope_cannot_take_another_scopes_keys_first(self) -> None:
        theirs = intake.event_records(
            "beta",
            "orchard/30",
            ["Shared one. ", "Shared two."],
            [(PENDING, None), (intake.assigned("observation"), intake.rule())],
        )
        taken = [in_scope(record, "acme") for record in theirs]
        other_secret = self.beta_engine()
        # Acme delivers beta's future keys, chunk ids and event id first.
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
        late = intake.type_decision(
            taken[0], intake.assigned("decision"), intake.model()
        )
        self.assertEqual(self.deliver(late), [EngineRecordAccepted(outcome="accepted")])
        # A second type for the chunk reuses its record key: first writer wins.
        (again,) = self.deliver(
            intake.type_decision(taken[0], intake.assigned("action"), intake.model())
        )
        self.assertEqual(again, EngineRecordConflict(outcome="conflict"))
        # Beta's copy of the chunk is still pending and is decided on its own.
        theirs_late = intake.type_decision(
            theirs[0], intake.assigned("action"), intake.model()
        )
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


class EngineScopeIdentityUpgrade(intake.EngineIntakeFixture):
    """Records accepted at schema 54 keep working after migration 0055."""

    def test_records_accepted_at_54_are_kept_and_continue_at_55(self) -> None:
        complete = intake.event_records(
            "acme",
            "orchard/40",
            ["Kept one. ", "Kept two."],
            [(PENDING, None), (intake.assigned("observation"), intake.rule())],
        )
        first, rest = intake.event_records(
            "acme",
            "orchard/41",
            ["Started at 54. ", "Finished at 55."],
            [(intake.assigned("observation"), intake.rule())] * 2,
        )
        self.assertEqual(
            self.deliver(*complete, first),
            [EngineRecordAccepted(outcome="accepted")] * 3,
        )
        migrate(self.db, expected_current_version=54, target_version=55)

        self.assertEqual(self.held(), [*complete, first])
        self.assertEqual(
            self.deliver(*complete, first),
            [EngineRecordAlreadyAccepted(outcome="already_accepted")] * 3,
        )
        # The rest of an event begun at 54 completes it at 55, and the chunk
        # left pending at 54 takes its one late type.
        late = intake.type_decision(
            complete[0], intake.assigned("decision"), intake.model()
        )
        self.assertEqual(
            self.deliver(rest, late), [EngineRecordAccepted(outcome="accepted")] * 2
        )
        self.assertEqual(self.held(), [*complete, first, rest, late])
        self.assertEqual(self.count("engine_event_completions"), 2)

        for table in (
            "engine_chunks",
            "engine_event_completions",
            "engine_type_decisions",
        ):
            row = self.db.execute(
                f"SELECT count(*) FILTER (WHERE source_object_id IS DISTINCT FROM %s), "
                f"count(*) FROM memoriesql.{table}",
                (self.acme.source_object_id,),
            ).fetchone()
            assert row is not None
            self.assertEqual(row[0], 0, table)
            self.assertGreater(row[1], 0, table)
            with self.assertRaises(
                psycopg.errors.ObjectNotInPrerequisiteState, msg=table
            ):
                self.db.execute(f"DELETE FROM memoriesql.{table}")
        kinds = self.db.execute(
            "SELECT operation_kind, count(*) FROM memoriesql.idempotency_receipts "
            "WHERE operation_kind LIKE 'engine_record.accept.%' "
            "GROUP BY operation_kind ORDER BY operation_kind"
        ).fetchall()
        self.assertEqual(
            kinds,
            [("engine_record.accept.v1", 3), ("engine_record.accept.v2", 2)],
        )


if __name__ == "__main__":
    import unittest

    unittest.main()
