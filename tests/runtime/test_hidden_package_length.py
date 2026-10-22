"""Fictional hidden-length invariance; execute only in an assigned DB slot.

Three worlds hold the same visible notes. Behind the first, every raw capture
is tens of bytes; behind the second, tens of kilobytes, so its raw-lineage
offsets are wider too; the third is the second with its identity package split
into many parts. A paired agent without source.raw.read must be charged the
same retained bytes, keep the same allocation and meet the same
max_result_bytes threshold in all three, through population, page reads,
reuse, inspection and hydration. Charged database time is only bounded here:
it no longer reads raw content (the TOAST block count below is the exact
check) but still tracks part count until per-materialization facts are sealed.
No model or provider is called; notes are authored at schema 43, as the helper
suite does.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import patch
from uuid import UUID, uuid4

if TYPE_CHECKING:
    from tests.runtime import agent_relation_fixtures as relation_agents
    from tests.runtime import test_agent_sql_helpers_runtime as helpers
    from tests.runtime import test_agent_sql_results as fixtures
    from tests.runtime.test_postgres_runtime import migrate
else:
    import agent_relation_fixtures as relation_agents
    import test_agent_sql_helpers_runtime as helpers
    import test_agent_sql_results as fixtures
    from test_postgres_runtime import migrate

MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations"
# The forward number is the next free one at landing; find it by name.
CLOSURE = int(
    sorted(MIGRATIONS.glob("[0-9][0-9][0-9][0-9]_hidden_package_length.sql"))[0].name[
        :4
    ]
)
QUERY = (
    "SELECT s.text,u.text_state,u.search_text FROM memory_v1.statements s "
    "JOIN memory_v1.statement_sources ss ON ss.statement_id=s.statement_id "
    "JOIN memory_v1.source_units u ON u.source_unit_id=ss.source_unit_id "
    "ORDER BY s.text"
)
# A bound, not a proof: equal work still varies with scheduling.
TIME_BOUND_MS = 250


def hidden(label: str, size: int) -> str:
    """Fictional raw text that does not compress, so a long one is stored out of line."""
    blocks: list[str] = []
    while sum(map(len, blocks)) < size:
        blocks.append(hashlib.sha256(f"{label}:{len(blocks)}".encode()).hexdigest())
    return ("Fictional RAW " + "".join(blocks))[:size]


@dataclass(frozen=True)
class World:
    scope: UUID
    source: UUID
    ledger: UUID
    normalized: UUID
    identity: UUID
    agent: str


@unittest.skipUnless(
    os.environ.get("N1_TEST_DATABASE_URL"), "assigned disposable PostgreSQL required"
)
class HiddenPackageLength(unittest.TestCase):
    LEDGER = "Fictional normalized ledger: the orchard gate log for the week."
    TEXT = "Fictional normalized harvest: pears ripen by the orchard gate."
    IDENTITY = "Fictional identity turn about the barn door."
    SMALL, LARGE = 40, 24000
    # Many parts of one component; still a single hidden identity unit.
    PARTS = 64

    def setUp(self) -> None:
        self.r = fixtures.AgentSqlResults(methodName="runTest")
        self.addCleanup(self.r.doCleanups)
        self.r.setUp()
        self.db, self.fixture = self.r.db, self.r.fixture
        # Equal-length labels: only hidden raw bytes and part counts differ.
        self.worlds = (
            self.world("w1", self.SMALL),
            self.world("w2", self.LARGE),
            self.world("w3", self.LARGE, parts=self.PARTS),
        )
        migrate(self.db, expected_current_version=43, target_version=CLOSURE - 1)

    def upgrade(self) -> None:
        migrate(self.db, expected_current_version=CLOSURE - 1, target_version=CLOSURE)

    def scalar(self, statement: str, values: tuple[Any, ...] = ()) -> Any:
        return self.r.h.scalar(statement, values)

    def identity_bead(
        self,
        text: str,
        key: str,
        *,
        raw: str,
        source: UUID,
        scope: UUID,
        parts: int = 1,
    ) -> UUID:
        """A bead whose identity (raw) package holds `raw`, not its statement.

        With several `parts`, the one captured range is packaged as that many
        ordered fragments of one component, each an exact lineage sub-slice.
        """
        original, create, append = (
            self.fixture.part,
            self.fixture.create,
            self.fixture.append,
        )
        fragments: dict[UUID, tuple[Any, ...]] = {}

        def captured(content: str, *args: Any, **kwargs: Any) -> Any:
            return original(raw, *args, **kwargs)

        def split(part: Any) -> tuple[Any, ...]:
            size = -(-len(part.content) // parts)
            pieces: list[Any] = []
            (whole,) = part.lineage
            offset = 0
            for ordinal in range(parts):
                chunk = part.content[ordinal * size : (ordinal + 1) * size]
                data = chunk.encode()
                pieces.append(
                    type(part).model_validate(
                        part.model_dump()
                        | {
                            "part_id": uuid4(),
                            "ordinal": ordinal,
                            "component_offset": part.component_offset + ordinal * size,
                            "content": chunk,
                            "content_sha256": hashlib.sha256(data).hexdigest(),
                            "lineage": [
                                whole.model_dump()
                                | {
                                    "byte_start": whole.byte_start + offset,
                                    "byte_end_exclusive": whole.byte_start
                                    + offset
                                    + len(data),
                                    "source_bytes_sha256": hashlib.sha256(
                                        data
                                    ).hexdigest(),
                                }
                            ],
                        }
                    )
                )
                offset += len(data)
            return tuple(pieces)

        def fragmented(declared: tuple[Any, ...], **updates: Any) -> Any:
            (part,) = declared
            fragments[part.part_id] = split(part)
            return create(fragments[part.part_id], **updates)

        def appended(package: Any, part: Any, key: str | None = None) -> Any:
            for piece in fragments[part.part_id]:
                result = append(package, piece)
            return result

        captured.__module__ = original.__module__
        with (
            self.r.in_scope(scope),
            patch.object(self.fixture, "part", captured),
            patch.object(self.fixture, "create", fragmented if parts > 1 else create),
            patch.object(self.fixture, "append", appended if parts > 1 else append),
        ):
            bead: UUID = self.fixture.bead(text, key=key, source=source)
        return bead

    def world(self, label: str, size: int, *, parts: int = 1) -> World:
        # remote_scope() may run only once per database; each world needs its own.
        scope, source = relation_agents.explicit_scope(self.db, self.fixture)
        # The ledger's raw capture comes first in the source, so it sets the
        # width of every later raw-lineage offset.
        ledger = self.r.normalized_bead(
            self.LEDGER,
            label + "-ledger",
            raw=hidden(label + "-ledger", size),
            source=source,
            scope=scope,
        )
        normalized = self.r.normalized_bead(
            self.TEXT,
            label + "-harvest",
            raw=hidden(label + "-harvest", size),
            source=source,
            scope=scope,
        )
        identity = self.identity_bead(
            self.IDENTITY,
            label + "-barn",
            raw=hidden(label + "-barn", size),
            source=source,
            scope=scope,
            parts=parts,
        )
        _, agent = self.r.pair_agent(
            scope, ["memory.inspect", "memory.query", "source.read"], label=label
        )
        return World(scope, source, ledger, normalized, identity, agent)

    def unit(self, bead: UUID) -> UUID:
        unit: UUID = self.scalar(
            "SELECT source_unit_id FROM memoriesql.logical_unit_materializations "
            "WHERE bead_id=%s",
            (bead,),
        )
        self.assertIsNotNone(unit)
        return unit

    def part_count(self, bead: UUID) -> int:
        count: int = self.scalar(
            "SELECT count(*) FROM memoriesql.logical_unit_materializations m "
            "JOIN memoriesql.evidence_package_parts pp "
            "ON pp.tenant_id=m.tenant_id AND pp.package_id=m.package_id "
            "WHERE m.bead_id=%s",
            (bead,),
        )
        return count

    def original_package_function(self) -> None:
        """0037's package value, as installed before the closure, in pg_temp."""
        text = (MIGRATIONS / "0037_observation_sql_population.sql").read_text()
        match = re.search(
            r"CREATE FUNCTION memoriesql\.query_unit_package_v1\(t uuid,id uuid\)"
            r".*? AS \$\$(.*?)\$\$;",
            text,
            re.S,
        )
        assert match is not None
        self.db.execute(
            "CREATE FUNCTION pg_temp.original_unit_package(t uuid,id uuid) "
            "RETURNS jsonb LANGUAGE sql STABLE AS $$" + match[1] + "$$"
        )

    def target(self, bead: UUID) -> dict[str, str]:
        version = self.scalar(
            "SELECT bead_version_id::text FROM memoriesql.accepted_bead_semantics "
            "WHERE bead_id=%s",
            (bead,),
        )
        self.assertIsNotNone(version)
        return {"bead_id": str(bead), "bead_version_id": str(version)}

    def threshold(
        self, request: dict[str, Any], secret: str, near: int
    ) -> tuple[int, list[Any]]:
        """The least max_result_bytes the query fits, and every step's charges."""
        seen: list[Any] = []

        def fits(limit: int) -> bool:
            reply = self.r.send(
                dict(request, step_key=str(uuid4()), max_result_bytes=limit),
                secret=secret,
            )
            remaining = reply.get("remaining") or {}
            seen.append((limit, reply["outcome"], remaining.get("allocation_bytes")))
            if reply["outcome"] == "available":
                return True
            self.assertEqual(
                (reply["outcome"], reply["error"]),
                ("budget_exhausted", {"code": "storage"}),
            )
            return False

        low, high = near - 4096, near + 4096
        self.assertFalse(fits(low))
        self.assertTrue(fits(high))
        while high - low > 1:
            middle = (low + high) // 2
            if fits(middle):
                high = middle
            else:
                low = middle
        return high, seen

    def measure(self, world: World) -> tuple[list[Any], list[int]]:
        """Charges a non-raw agent sees, exact ones apart from database time."""
        exact: list[Any] = []
        times: list[int] = []

        def record(step: str, reply: dict[str, Any]) -> None:
            work = reply.get("work") or {}
            remaining = reply.get("remaining") or {}
            exact.append(
                (
                    step,
                    reply["outcome"],
                    reply.get("error"),
                    work.get("retained_bytes"),
                    remaining.get("allocation_bytes"),
                    remaining.get("accesses"),
                )
            )
            if "charged_ms" in work:
                times.append(work["charged_ms"])

        run = self.r.start(world.agent)
        request, first = self.r.query(run, QUERY, page_size=1, secret=world.agent)
        self.assertEqual(first["outcome"], "available", first)
        record("population", first)
        exact.append([row["values"] for row in first["page"]["rows"]])
        cursor = first["page"]["next_cursor"]
        while cursor is not None:
            page = self.r.reuse(
                run, first["result"], cursor=cursor, page_size=1, secret=world.agent
            )
            self.assertEqual(page["outcome"], "available", page)
            record("page", page)
            exact.append([row["values"] for row in page["page"]["rows"]])
            cursor = page["page"]["next_cursor"]
        again = self.r.reuse(run, first["result"], page_size=50, secret=world.agent)
        self.assertEqual(again["outcome"], "available", again)
        record("reuse", again)
        inspection = self.r.send(
            {
                "contract_version": 1,
                "run_ref": run["run_ref"],
                "step_key": str(uuid4()),
                "kind": "inspect",
                "observation_refs": [
                    self.target(b)
                    for b in (world.ledger, world.normalized, world.identity)
                ],
                "relation_refs": [],
                "result": None,
                "provenance_ref": None,
                "facets": ["content"],
                "view": "historical",
                "known_at": run["default_known_at"],
                "cursor": None,
            },
            secret=world.agent,
        )
        self.assertEqual(inspection["outcome"], "available", inspection)
        record("inspect", inspection)
        source = next(
            ref
            for row in again["page"]["rows"]
            if row["values"][0] == self.TEXT
            for ref in row["evidence_refs"]
            if ref["kind"] == "source"
        )
        part = self.scalar(
            "SELECT pp.part_id FROM memoriesql.logical_unit_materializations m "
            "JOIN memoriesql.evidence_package_parts pp "
            "ON pp.tenant_id=m.tenant_id AND pp.package_id=m.package_id "
            "WHERE m.bead_id=%s",
            (world.normalized,),
        )
        hydration = self.r.send(
            {
                "contract_version": 1,
                "run_ref": run["run_ref"],
                "step_key": str(uuid4()),
                "kind": "hydrate_source",
                "selections": [
                    {
                        "evidence_ref": source["ref"],
                        "part_id": str(part),
                        "representation": "normalized_text",
                        "lineage_ordinal": None,
                        "start": 0,
                        "end": 32,
                        "origin": {
                            "result": {
                                k: first["result"][k]
                                for k in ("result_id", "content_digest")
                            },
                            "access": {"kind": "standalone"},
                        },
                    }
                ],
                "max_bytes": 65536,
                "cursor": None,
            },
            secret=world.agent,
        )
        # Hydration needs source.raw.read; the refusal must not vary either.
        self.assertNotEqual(hydration["outcome"], "available", hydration)
        self.assertNotIn("hydration", hydration)
        record("hydrate", hydration)
        least, steps = self.threshold(
            request, world.agent, int(first["work"]["retained_bytes"])
        )
        exact.append(("threshold", least, steps))
        # Two runs may be active per workspace; each world uses one in turn.
        self.r.close(run, secret=world.agent)
        return exact, times

    def test_a_non_raw_agent_is_charged_the_same_whatever_raw_is_hidden(
        self,
    ) -> None:
        self.upgrade()
        self.assertEqual(
            [self.part_count(world.identity) for world in self.worlds],
            [1, 1, self.PARTS],
        )
        (first, first_times), *others = map(self.measure, self.worlds)
        for exact, times in others:
            self.assertEqual(exact, first)
            self.assertEqual(len(times), len(first_times))
            for one, other in zip(first_times, times, strict=True):
                self.assertLessEqual(abs(one - other), TIME_BOUND_MS)

    def test_identity_raw_content_is_never_read_for_package_text(self) -> None:
        self.upgrade()
        self.original_package_function()
        unit = self.unit(self.worlds[1].identity)
        toast = self.scalar(
            "SELECT reltoastrelid FROM pg_class "
            "WHERE oid='memoriesql.evidence_package_parts'::regclass"
        )
        with self.db.transaction():

            def fetched() -> int:
                row = self.db.execute(
                    "SELECT pg_stat_get_xact_blocks_fetched(%s)", (toast,)
                ).fetchone()
                assert row is not None
                return int(row[0])

            before = fetched()
            package = self.db.execute(
                "SELECT memoriesql.query_unit_package_v1(%s,%s)",
                (self.fixture.tenant, unit),
            ).fetchone()
            assert package is not None
            self.assertEqual(
                (package[0]["normalized"], package[0]["text"]), (False, None)
            )
            self.assertEqual(fetched(), before)
            # 0037 read the same hidden raw content and discarded it.
            self.db.execute(
                "SELECT pg_temp.original_unit_package(%s,%s)",
                (self.fixture.tenant, unit),
            )
            self.assertGreater(fetched(), before)

    def test_package_values_are_unchanged_for_every_package(self) -> None:
        self.upgrade()
        self.original_package_function()
        tenant = self.fixture.tenant
        for world in self.worlds:
            for bead in (world.ledger, world.normalized, world.identity):
                with self.subTest(bead=bead):
                    unit = self.unit(bead)
                    original = self.scalar(
                        "SELECT pg_temp.original_unit_package(%s,%s)", (tenant, unit)
                    )
                    self.assertIsNotNone(original)
                    self.assertEqual(
                        self.scalar(
                            "SELECT memoriesql.query_unit_package_v1(%s,%s)",
                            (tenant, unit),
                        ),
                        original,
                    )
                    self.assertEqual(
                        self.scalar(
                            "SELECT memoriesql.query_unit_package_record_v1(%s,%s)",
                            (tenant, unit),
                        ),
                        {k: v for k, v in original.items() if k != "text"},
                    )
            self.assertEqual(
                self.scalar(
                    "SELECT memoriesql.query_unit_package_v1(%s,%s)",
                    (tenant, self.unit(world.normalized)),
                )["text"],
                self.TEXT,
            )
        missing = uuid4()
        self.assertIsNone(
            self.scalar(
                "SELECT memoriesql.query_unit_package_v1(%s,%s)", (tenant, missing)
            )
        )
        self.assertIsNone(
            self.scalar(
                "SELECT pg_temp.original_unit_package(%s,%s)", (tenant, missing)
            )
        )

    def test_saved_records_hold_only_a_digest_of_each_unit_package(self) -> None:
        self.upgrade()
        self.original_package_function()
        widths = set()
        for world in self.worlds:
            run = self.r.start(world.agent)
            _, reply = self.r.query(run, QUERY, page_size=50, secret=world.agent)
            self.assertEqual(reply["outcome"], "available", reply)
            deps = json.loads(
                bytes(
                    self.scalar(
                        "SELECT dependency_bytes FROM memoriesql.result_preparation_artifacts "
                        "WHERE artifact_ref=%s",
                        (reply["result"]["result_id"],),
                    )
                )
            )
            records = json.loads(base64.b64decode(deps["records_base64"]))
            packages = [r for r in records if r["kind"] == "unit_package"]
            self.assertEqual(len(packages), 3)
            self.r.close(run, secret=world.agent)
            for item in packages:
                unit = UUID(item["id"])
                # The digest binds exactly the full row that 0037 stored.
                expected = self.scalar(
                    "SELECT memoriesql.lifecycle_hash_v1("
                    "(pg_temp.original_unit_package(%s,%s)-'text')"
                    "||jsonb_build_object('source_unit_id',%s::uuid))",
                    (self.fixture.tenant, unit, unit),
                )
                self.assertEqual(item["row"], {"content_sha256": expected})
                widths.add(len(json.dumps(item, separators=(",", ":"))))
        self.assertEqual(len(widths), 1)

    def test_results_saved_before_the_closure_refuse_as_unavailable(self) -> None:
        world = self.worlds[1]
        run = self.r.start(world.agent)
        _, saved = self.r.query(run, QUERY, page_size=50, secret=world.agent)
        self.assertEqual(saved["outcome"], "available", saved)
        self.upgrade()
        # A saved full record no longer rebuilds equal: no dual acceptance.
        refused = self.r.reuse(run, saved["result"], page_size=50, secret=world.agent)
        self.assertEqual(
            (refused["outcome"], refused["error"]),
            ("unavailable", {"code": "unavailable"}),
        )
        self.assertNotIn("result", refused)
        _, fresh = self.r.query(run, QUERY, page_size=50, secret=world.agent)
        self.assertEqual(fresh["outcome"], "available", fresh)
        again = self.r.reuse(run, fresh["result"], page_size=50, secret=world.agent)
        self.assertEqual(again["outcome"], "available", again)
        self.assertEqual(again["page"]["rows"], fresh["page"]["rows"])
        self.assertEqual(
            [row["values"] for row in fresh["page"]["rows"]],
            [row["values"] for row in saved["page"]["rows"]],
        )


@unittest.skipUnless(
    os.environ.get("N1_TEST_DATABASE_URL"), "assigned disposable PostgreSQL required"
)
class HelpersWithClosure(helpers.AgentSqlHelperRuntime):
    """Every installed helper regression, rerun with the closure installed.

    The helper suite authors its notes before migrating, so its whole frame,
    replay and hydration behaviour also runs over digest unit_package records.
    """

    def setUp(self) -> None:
        super().setUp()
        migrate(self.db, expected_current_version=46, target_version=CLOSURE)


if __name__ == "__main__":
    unittest.main()
