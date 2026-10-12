"""Database-free caller-visible frame digests (owner decision 6)."""

from __future__ import annotations

import unittest
from typing import Any
from uuid import UUID, uuid4

from memoriesql.application.agent_sql_catalog import SqlCatalog
from memoriesql.infrastructure.postgres.relation_sql_population import (
    OBSERVATION_RELATIONS,
    RELATIONS,
    _visible_digests,
    _visible_manifest_sha256,
)

SCHEMAS = {
    "memory_v1." + name: SqlCatalog.installed().relations["memory_v1." + name]
    for name in RELATIONS + OBSERVATION_RELATIONS
}
NAMES = RELATIONS + OBSERVATION_RELATIONS
FULL = "f" * 64
FULL_RELATIONS = "e" * 64


def native(**tables: tuple[tuple[Any, ...], ...]) -> dict[str, tuple[tuple[Any, ...], ...]]:
    rows: dict[str, tuple[tuple[Any, ...], ...]] = {
        "memory_v1." + name: () for name in NAMES
    }
    rows.update({"memory_v1." + name: value for name, value in tables.items()})
    return rows


def evidence(relation: UUID, statement: UUID, unit: UUID, ref: UUID) -> tuple[Any, ...]:
    return (relation, statement, unit, "a" * 64, ref, "qualified")


class VisibleFrameDigest(unittest.TestCase):
    def setUp(self) -> None:
        self.relation, self.statement, self.unit = uuid4(), uuid4(), uuid4()
        self.replaced = (self.relation, uuid4())

    def digest(self, *rows: tuple[Any, ...], replacements: Any = ()) -> str:
        return _visible_manifest_sha256(
            SCHEMAS,
            native(relation_evidence=rows, relation_replacements=replacements),
            NAMES,
        )

    def test_the_digest_is_of_disclosed_values_only(self) -> None:
        one = evidence(self.relation, self.statement, self.unit, uuid4())
        two = evidence(self.relation, uuid4(), self.unit, uuid4())
        base = self.digest(one, two)
        # Private evidence references are minted per preparation; row order is
        # not content. Neither moves the digest.
        again = (one[:4] + (uuid4(),) + one[5:], two[:4] + (uuid4(),) + two[5:])
        self.assertEqual(self.digest(*reversed(again)), base)
        # Any disclosed value does.
        changed = one[:5] + ("unqualified",)
        self.assertNotEqual(self.digest(changed, two), base)
        self.assertNotEqual(self.digest(one), base)
        self.assertNotEqual(self.digest(one, two, replacements=(self.replaced,)), base)

    def test_a_raw_read_holder_keeps_the_full_digests(self) -> None:
        rows = native(relation_replacements=(self.replaced,))
        for revision, raw, mode in ((1, True, None), (2, True, "owner"), (2, True, None)):
            with self.subTest(revision=revision, mode=mode):
                self.assertEqual(
                    _visible_digests(
                        revision=revision,
                        raw_authority=raw,
                        read_mode=mode,
                        digest=FULL,
                        relation_manifest=FULL_RELATIONS,
                        schemas=SCHEMAS,
                        native=rows,
                        names=NAMES,
                    ),
                    (FULL, FULL_RELATIONS),
                )

    def test_any_other_caller_gets_digests_of_its_rows(self) -> None:
        rows = native(relation_replacements=(self.replaced,))
        for raw, mode in ((False, "agent"), (False, None)):
            with self.subTest(mode=mode):
                visible, relations = _visible_digests(
                    revision=2,
                    raw_authority=raw,
                    read_mode=mode,
                    digest=FULL,
                    relation_manifest=FULL_RELATIONS,
                    schemas=SCHEMAS,
                    native=rows,
                    names=NAMES,
                )
                self.assertEqual(visible, _visible_manifest_sha256(SCHEMAS, rows, NAMES))
                self.assertEqual(
                    relations, _visible_manifest_sha256(SCHEMAS, rows, RELATIONS)
                )
                self.assertNotIn(FULL, (visible, relations))
                self.assertNotIn(FULL_RELATIONS, (visible, relations))


if __name__ == "__main__":
    unittest.main()
