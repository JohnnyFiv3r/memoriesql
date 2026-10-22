"""Agent source text view (read_source_text), checked without a database.

Authorization, disclosure, accounting and cleanup run against an assigned
database in tests/runtime/test_agent_source_text_runtime.py.
"""

from __future__ import annotations

import hashlib
import json
import re
import unittest
from pathlib import Path
from typing import Any
from uuid import uuid4

from memoriesql.application.agent_sql_helpers import (
    helper_content_page,
    helper_item_count,
    validate_helper_content,
)
from memoriesql.application.investigation_contracts import (
    InvestigationRequestError,
    ReadSourceTextRequest,
    parse_investigation_request,
)

ROOT = Path(__file__).resolve().parents[2]
(MIGRATION,) = sorted(
    (ROOT / "migrations").glob("[0-9][0-9][0-9][0-9]_agent_source_text_view.sql")
)
SQL = MIGRATION.read_text()
EARLIER = sorted(
    path
    for path in (ROOT / "migrations").glob("[0-9][0-9][0-9][0-9]_*.sql")
    if path.name < MIGRATION.name
)
POPULATION = (ROOT / "migrations/0039_agent_relation_reads.sql").read_text()
KNOWN_AT = "2026-10-22T12:00:00.000000Z"
STANDALONE = {"kind": "standalone"}


def source() -> dict[str, str]:
    return {
        "kind": "source",
        "ref": str(uuid4()),
        "unit_ref": str(uuid4()),
        "content_sha256": hashlib.sha256(uuid4().bytes).hexdigest(),
    }


def entry(
    ref: dict[str, str], start: int = 0, end: int = 4, origin: Any = None
) -> dict[str, Any]:
    return {"source": ref, "start": start, "end": end, "origin": origin}


def request(*entries: dict[str, Any], max_bytes: int = 65536) -> dict[str, Any]:
    return {
        "contract_version": 1,
        "run_ref": str(uuid4()),
        "step_key": str(uuid4()),
        "kind": "read_source_text",
        "entries": list(entries),
        "max_bytes": max_bytes,
        "cursor": None,
    }


def parse(value: dict[str, Any]) -> ReadSourceTextRequest:
    parsed = parse_investigation_request(json.dumps(value).encode())
    assert isinstance(parsed, ReadSourceTextRequest)
    return parsed


def available(item: dict[str, Any], text: str, units: int) -> dict[str, Any]:
    return {
        "source": item["source"],
        "frame": {"known_at": KNOWN_AT, "view": "historical"},
        "text_state": "available",
        "start": item["start"],
        "end": item["end"],
        "unit_characters": units,
        "text": text,
        "returned_sha256": hashlib.sha256(text.encode()).hexdigest(),
    }


def unsupported(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": item["source"],
        "frame": {"known_at": KNOWN_AT, "view": "historical"},
        "text_state": "unsupported",
        "start": item["start"],
        "end": item["end"],
        "unit_characters": None,
        "text": None,
        "returned_sha256": None,
    }


def content(
    slices: list[dict[str, Any]],
    labels: list[str] | None = None,
    *,
    complete: bool | None = None,
    bases: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "kind": "read_source_text",
        "payload": {
            "source_text": {
                "slices": slices,
                "source_text_labels": labels or [],
                "selection_complete": all(
                    item["text_state"] == "available" for item in slices
                )
                if complete is None
                else complete,
            }
        },
        "refs": [],
        "access_bases": [
            {
                "context": STANDALONE,
                "checked_at": KNOWN_AT,
                "valid_until": "2026-10-22T12:30:00.000000Z",
                "end_condition": "standalone_expiry",
                "hold_ref": None,
            }
        ]
        if bases is None
        else bases,
    }


def function(sql: str, name: str) -> str:
    start = re.search(r"CREATE (OR REPLACE )?FUNCTION memoriesql\." + name + r"\(", sql)
    assert start is not None, name
    end = sql.index("END $$;", start.start()) + len("END $$;")
    return sql[start.start() : end]


def latest(name: str) -> str:
    """The last definition of name in the stream before this migration."""
    found = ""
    for path in EARLIER:
        sql = path.read_text()
        for start in re.finditer(
            r"CREATE (OR REPLACE )?FUNCTION memoriesql\." + name + r"\(", sql
        ):
            # plpgsql bodies end with "END $$;", SQL-language bodies with "$$;".
            stop = sql.index("$$;", sql.index("AS $$", start.start()) + 5)
            found = sql[start.start() : stop + 3]
    assert found, name
    return found


def constraint(sql: str, name: str) -> str:
    """The one ADD CONSTRAINT name ... ; statement of sql."""
    found: list[str] = re.findall(r"ADD CONSTRAINT " + name + r"\b[^;]*;", sql)
    assert len(found) == 1, name
    return found[0]


def latest_constraint(name: str) -> str:
    """The last ADD CONSTRAINT name statement in the stream before this migration."""
    found = ""
    for path in EARLIER:
        for statement in re.findall(
            r"ADD CONSTRAINT " + name + r"\b[^;]*;", path.read_text()
        ):
            found = statement
    assert found, name
    return found


def installed_stream() -> dict[str, str]:
    """Every function's last body across the whole migration stream."""
    found: dict[str, str] = {}
    for path in sorted((ROOT / "migrations").glob("[0-9][0-9][0-9][0-9]_*.sql")):
        sql = path.read_text()
        for start in re.finditer(
            r"CREATE (?:OR REPLACE )?FUNCTION memoriesql\.(\w+)\(", sql
        ):
            body = sql.index("AS $$", start.start()) + 5
            found[start.group(1)] = sql[body : sql.index("$$", body)]
    return found


class SourceTextRequestContract(unittest.TestCase):
    def test_a_bounded_closed_request_parses(self) -> None:
        parsed = parse(request(entry(source(), 0, 16384)))
        self.assertEqual(parsed.kind, "read_source_text")
        self.assertEqual(len(parsed.entries), 1)
        origin = {
            "result": {"result_id": str(uuid4()), "content_digest": "a" * 64},
            "access": STANDALONE,
        }
        self.assertIsNotNone(parse(request(entry(source(), origin=origin))).entries[0])

    def test_owner_fields_and_out_of_bound_shapes_are_binding_errors(self) -> None:
        # Case 14: no part, lineage or representation exists on this kind.
        for extra in (
            {"part_id": str(uuid4())},
            {"lineage_ordinal": 0},
            {"representation": "normalized_text"},
        ):
            with self.subTest(extra=extra):
                self.refused(request(entry(source()) | extra))
        # Case 20: request-side bounds are refused before any read.
        for value in (
            request(entry(source(), 0, 16385)),
            request(entry(source(), 5, 4)),
            request(entry(source()), max_bytes=0),
            request(entry(source()), max_bytes=65537),
            request(*(entry(source()) for _ in range(9))),
            request(),
            request(entry(dict(source(), kind="observation"))),
            request(entry({k: v for k, v in source().items() if k != "unit_ref"})),
        ):
            self.refused(value)

    def refused(self, value: dict[str, Any]) -> None:
        with self.assertRaises(InvestigationRequestError) as caught:
            parse_investigation_request(json.dumps(value).encode())
        self.assertEqual(caught.exception.code, "binding")


class SourceTextPayload(unittest.TestCase):
    def setUp(self) -> None:
        self.first, self.second = entry(source(), 2, 6), entry(source(), 0, 3)
        self.request = parse(request(self.first, self.second))
        self.text = "orchard α pears"

    def test_available_and_unsupported_slices_disclose_only_their_refs(self) -> None:
        body = content(
            [available(self.first, "char", 15), unsupported(self.second)],
            ["normalized_projection:orchard.v1"],
        )
        refs = validate_helper_content(self.request, body)
        self.assertEqual(
            refs,
            sorted(
                [self.first["source"], self.second["source"]],
                key=lambda ref: json.dumps(ref, sort_keys=True, separators=(",", ":")),
            ),
        )
        self.assertFalse(body["payload"]["source_text"]["selection_complete"])

    def test_unsupported_slices_are_never_measured(self) -> None:
        # N-5: no length, size or digest exists for unsupported (raw/mixed) text.
        for field, value in (
            ("unit_characters", 0),
            ("text", ""),
            ("returned_sha256", hashlib.sha256(b"").hexdigest()),
        ):
            with self.subTest(field=field):
                item = unsupported(self.second) | {field: value}
                with self.assertRaises(ValueError):
                    validate_helper_content(
                        self.request,
                        content([available(self.first, "char", 15), item]),
                    )

    def test_limits_are_utf8_content_bytes(self) -> None:
        astral = "🍐" * 4096
        exact = entry(source(), 0, 4096)
        refs = validate_helper_content(
            parse(request(exact)), content([available(exact, astral, 5000)])
        )
        self.assertEqual(len(refs), 1)
        over = entry(source(), 0, 4097)
        with self.assertRaises(ValueError):
            validate_helper_content(
                parse(request(over)), content([available(over, astral + "🍐", 5000)])
            )
        small = parse(request(self.first, max_bytes=3))
        with self.assertRaises(ValueError):
            validate_helper_content(small, content([available(self.first, "char", 15)]))

    def test_slices_must_bind_their_exact_entry_and_text(self) -> None:
        good = available(self.first, "char", 15)
        for broken in (
            good | {"source": source()},
            good | {"start": 3},
            good | {"end": 7},
            good | {"text": "cha"},
            good | {"returned_sha256": "0" * 64},
            good | {"unit_characters": 5},
            good | {"frame": {"known_at": KNOWN_AT, "view": "current"}},
            good | {"text_state": "partial"},
            {k: v for k, v in good.items() if k != "frame"},
        ):
            with self.subTest(broken=broken), self.assertRaises(ValueError):
                validate_helper_content(
                    self.request, content([broken, unsupported(self.second)])
                )

    def test_labels_and_completeness_are_closed(self) -> None:
        slices = [available(self.first, "char", 15), available(self.second, "orc", 9)]
        for labels, complete in (
            (["package_unresolved:b", "normalized_projection:a"], True),
            (["package_unresolved:b", "package_unresolved:b"], True),
            (["raw_bytes:anything"], True),
            ([], False),
        ):
            with self.subTest(labels=labels), self.assertRaises(ValueError):
                validate_helper_content(
                    self.request, content(slices, labels, complete=complete)
                )
        # Labels describe served text only, and unsupported text never completes.
        none_served = [unsupported(self.first), unsupported(self.second)]
        for labels, complete in ((["normalized_projection:a"], False), ([], True)):
            with self.subTest(labels=labels), self.assertRaises(ValueError):
                validate_helper_content(
                    self.request, content(none_served, labels, complete=complete)
                )

    def test_explicit_origins_require_their_access_basis(self) -> None:
        origin = {
            "result": {"result_id": str(uuid4()), "content_digest": "b" * 64},
            "access": STANDALONE,
        }
        bound = entry(source(), origin=origin)
        parsed = parse(request(bound))
        with self.assertRaises(ValueError):
            validate_helper_content(
                parsed, content([available(bound, "char", 9)], bases=[])
            )
        self.assertEqual(
            len(
                validate_helper_content(parsed, content([available(bound, "char", 9)]))
            ),
            1,
        )

    def test_pages_carry_only_their_own_units_labels(self) -> None:
        body = content(
            [available(self.first, "char", 15), available(self.second, "orc", 9)],
            ["normalized_projection:a", "package_exclusion:x"],
        )
        witnesses = {
            "slice_labels": [["normalized_projection:a", "package_exclusion:x"], []]
        }
        self.assertEqual(helper_item_count(self.request, body), 2)
        first = helper_content_page(
            self.request, body, first=1, count=1, cursor="c", witnesses=witnesses
        )
        second = helper_content_page(
            self.request, body, first=2, count=1, cursor=None, witnesses=witnesses
        )
        whole = helper_content_page(
            self.request, body, first=1, count=2, cursor=None, witnesses=witnesses
        )
        view = first["payload"]["source_text"]
        self.assertEqual(
            view["source_text_labels"],
            ["normalized_projection:a", "package_exclusion:x"],
        )
        self.assertFalse(view["selection_complete"])
        self.assertEqual(second["payload"]["source_text"]["source_text_labels"], [])
        self.assertTrue(second["payload"]["source_text"]["selection_complete"])
        self.assertEqual(whole["payload"], body["payload"])
        self.assertEqual(
            [first["refs"], second["refs"]],
            [[self.first["source"]], [self.second["source"]]],
        )
        brokens: tuple[dict[str, Any], ...] = (
            {},
            {"slice_labels": [[]]},
            {"slice_labels": [["x"], []]},
        )
        for broken in brokens:
            with self.subTest(witnesses=broken), self.assertRaises(ValueError):
                helper_content_page(
                    self.request, body, first=1, count=2, cursor=None, witnesses=broken
                )


class SourceTextMigration(unittest.TestCase):
    def test_migration_is_the_next_inventoried_resource(self) -> None:
        inventory = json.loads(
            (ROOT / "contracts/migration-inventory.json").read_text()
        )
        rows = inventory["migrations"]
        # The view follows every earlier migration; later forward ones (sealed
        # unit facts) may follow it, contiguously.
        version = int(MIGRATION.name[:4])
        self.assertEqual(
            [int(row["filename"][:4]) for row in rows], list(range(1, len(rows) + 1))
        )
        self.assertEqual(
            rows[version - 1],
            {
                "filename": MIGRATION.name,
                "sha256": hashlib.sha256(MIGRATION.read_bytes()).hexdigest(),
            },
        )
        self.assertEqual(
            [row["filename"] for row in rows[: version - 1]],
            [path.name for path in EARLIER],
        )

    def test_redefinitions_are_the_merged_helpers_with_only_the_new_kind(self) -> None:
        # Each is the stream's latest definition (whichever migration last
        # defined it, checkpoints included) with the kind appended to its one
        # kind list.
        kind = ",'read_source_text'"
        for name in (
            "admit_query_delivery_v1",
            "query_helper_delivery_v1",
            "purge_query_helper_step_v1",
        ):
            with self.subTest(name=name):
                redefined = function(SQL, name)
                self.assertEqual(redefined.count(kind), 1)
                self.assertEqual(
                    redefined.replace(kind + ")", ")"),
                    latest(name).replace(
                        "CREATE FUNCTION", "CREATE OR REPLACE FUNCTION"
                    ),
                )
        dispatch = function(SQL, "prepare_query_helper_v1")
        merged = latest("prepare_query_helper_v1")
        added = re.compile(
            r"\n[^\n]*(read_source_text|IF x\.cursor_ref IS NOT NULL AND x\.run_ref=d\.run_ref THEN RAISE)[^\n]*"
        )
        self.assertEqual(
            added.sub("", dispatch),
            merged.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION"),
        )
        self.assertEqual(dispatch.count("prepare_query_source_text_v1("), 2)
        # Case 25: this run's own cursor with a changed request is a binding error.
        self.assertIn(
            "IF x.cursor_ref IS NOT NULL AND x.run_ref=d.run_ref THEN RAISE EXCEPTION "
            "'invalid_query_helper_binding' USING ERRCODE='22023'; END IF;",
            dispatch,
        )
        for name in (
            "query_steps_helper_kind_check",
            "query_steps_helper_complete_check",
        ):
            with self.subTest(constraint=name):
                redefined = constraint(SQL, name)
                self.assertEqual(redefined.count(kind), 1)
                self.assertEqual(
                    redefined.replace(kind + ")", ")"), latest_constraint(name)
                )
        self.assertIn(
            "OR (kind IN ('inspect','hydrate_source','read_source_text') AND state='complete'",
            SQL,
        )

    def test_new_functions_never_reach_raw_or_package_metadata(self) -> None:
        names = (
            "query_unit_source_text_v1",
            "resolve_query_source_text_v1",
            "bind_query_source_text_v1",
            "prepare_query_source_text_v1",
        )
        empty = "'raw_sources','[]'::jsonb,'parts','[]'::jsonb"
        # No raw source or package part is ever a dependency of this kind.
        self.assertIn(empty, function(SQL, "prepare_query_source_text_v1"))
        for name in names:
            body = function(SQL, name).replace(empty, "")
            with self.subTest(name=name):
                for forbidden in (
                    "evidence_package_parts",
                    "revisiting_source_authorize",
                    "query_helper_evidence_pin_v1",
                    "source.raw.read",
                    "stored_bead_evidence_content",
                    "bytes_base64",
                    "lineage",
                    "required_characters",
                    "'parts'",
                    "->'part'",
                ):
                    self.assertNotIn(forbidden, body)
        unit = function(SQL, "query_unit_source_text_v1")
        # Only the gate, its text and its declared labels are read from the package.
        self.assertEqual(
            sorted(set(re.findall(r"pkg->>?'(\w+)'", unit))),
            [
                "exclusions",
                "normalization_policy_version",
                "normalized",
                "text",
                "unresolved_coverage",
            ],
        )

    def test_unit_text_is_the_query_exceptions_own_expression(self) -> None:
        def squash(text: str) -> str:
            return re.sub(r"\s+", "", text)

        population = squash(function(POPULATION, "prepare_query_sql_population_v2"))
        unit = squash(function(SQL, "query_unit_source_text_v1"))
        for fragment in (
            "CASEWHEN(pkg->>'normalized')::booleanTHENpkg->>'text'END",
            "||('normalized_projection:'||(pkg->>'normalization_policy_version'))",
            "||ARRAY(SELECT'package_exclusion:'||xFROMjsonb_array_elements_text(pkg->'exclusions')x)",
            "||ARRAY(SELECT'package_unresolved:'||xFROMjsonb_array_elements_text(pkg->'unresolved_coverage')x)",
        ):
            self.assertIn(fragment, population)
            self.assertIn(fragment, unit)
        self.assertIn(
            "memoriesql.query_unit_package_v1(c.tenant_id,unit_id)",
            function(POPULATION, "prepare_query_sql_population_v2"),
        )
        # The same package read as the population, only when no text is retained.
        self.assertIn(
            "IFretainedISNULLTHENpkg:=memoriesql.query_unit_package_v1(t,unit);ENDIF;",
            unit,
        )

    def test_unsupported_text_is_never_aggregated(self) -> None:
        # N-5 timing (C-3) and N-2: the view reads a unit's package only through
        # the stream's own query_unit_package_v1, which sealed unit facts define
        # over one facts row, the pin and the package row: no part row at all.
        # The normalized text was sealed once, by the writer, only for a
        # producer-normalized package.
        unit = function(SQL, "query_unit_source_text_v1")
        self.assertNotIn("pp.", unit)
        self.assertNotIn("evidence_package_parts", unit)
        package = re.sub(r"\s+", "", latest("query_unit_package_v1"))
        for absent in ("evidence_package_parts", "query_unit_package_record_v1"):
            self.assertNotIn(absent, package)
        self.assertIn("FROMmemoriesql.logical_unit_query_factsf", package)
        self.assertIn("'normalized',f.normalized,'text',f.normalized_text", package)
        # Bound to the package the facts were sealed from: a pin that no longer
        # names it refuses, in the package view and in the unit's records (the
        # view's bind and closure checks read those).
        self.assertIn("p.package_id=f.package_id", package)
        records = re.sub(r"\s+", "", latest("query_unit_records_v1"))
        for check in (package, records):
            self.assertIn("f.package_id=m.package_idINTO", check)
            self.assertIn(
                "ISNOTNULLANDpinnedISNOTTRUETHENRAISEEXCEPTION'dependency_unavailable'",
                check,
            )
        self.assertNotIn("evidence_package_parts", records)
        sealed = re.sub(r"\s+", "", latest("unit_query_facts_v1"))
        self.assertIn(
            "CASEWHEN(q.pkg->>'normalized')::booleanTHEN(SELECTstring_agg(pp.content,''ORDERBYpp.ordinal)",
            sealed,
        )
        self.assertEqual(sealed.count("pp.content"), 1)
        record = latest("query_unit_package_record_v1")
        self.assertNotIn("content", record.replace("content_sha256", ""))
        self.assertIn(
            "'normalized',p.sealed_receipt_idISNOTNULLANDm.package_pin->>'inventory_sha256'=p.inventory_hashANDcount(pp.part_id)=(m.package_pin->>'required_parts')::integerANDbool_and(pp.inventory->>'derivation'='producer_normalized')",
            re.sub(r"\s+", "", record),
        )

    def test_every_new_lookup_is_tenant_qualified(self) -> None:
        # Case 37 (tenant half): no new function reads a table row outside the
        # delivery's or caller's tenant.
        for name in (
            "query_unit_source_text_v1",
            "resolve_query_source_text_v1",
            "bind_query_source_text_v1",
            "prepare_query_source_text_v1",
        ):
            body = function(SQL, name)
            for statement in body.split(";"):
                tables = re.findall(r"(?:FROM|JOIN) memoriesql\.(\w+)", statement)
                with self.subTest(name=name, tables=tables):
                    for _ in tables:
                        self.assertRegex(statement, r"tenant_id=(?:t|d\.tenant_id)\b")
                    self.assertGreaterEqual(
                        len(re.findall(r"tenant_id=", statement)), len(tables)
                    )

    def test_every_entry_is_authorized_before_any_measurement(self) -> None:
        body = function(SQL, "prepare_query_source_text_v1")
        authorized = max(
            body.index("memoriesql.resolve_query_source_text_v1("),
            body.index("memoriesql.check_query_result_closure_v2("),
            body.index("memoriesql.bind_query_source_text_v1("),
        )
        for measurement in (
            "invalid_query_helper_interval",
            "char_length(",
            "octet_length(",
            "query_unit_source_text_v1(",
            "query_helper_transport_exhausted",
        ):
            self.assertLess(authorized, body.index(measurement), measurement)
        # Case 43: each distinct delivering result's closure once per call.
        self.assertIn(
            "FOR item IN SELECT DISTINCT value FROM jsonb_array_elements(resolved) LOOP\n"
            "  PERFORM memoriesql.check_query_result_closure_v2(",
            body,
        )
        self.assertEqual(body.count("check_query_result_closure_v2("), 1)
        # Unsupported text branches off before anything is measured (N-5).
        self.assertLess(
            body.index("IF served IS NULL THEN"), body.index("char_length(")
        )
        self.assertIn("octet_length(piece_bytes)>16384", body)
        self.assertIn(
            "RAISE EXCEPTION 'query_helper_transport_exhausted' USING ERRCODE='54000'",
            body,
        )
        bind = function(SQL, "bind_query_source_text_v1")
        for check in (
            "current_context_semantic_statement_authorized",
            "current_context_bead_version_authorized",
            "memoriesql.query_bead_records_v1(t,st.bead_id,known)",
            "saved_package IS DISTINCT FROM current_package",
        ):
            self.assertIn(check, bind)
        resolve = function(SQL, "resolve_query_source_text_v1")
        # Delivered in THIS run only; checkpoint contexts are refused here.
        self.assertIn("run_ref=d.run_ref", resolve)
        self.assertIn("zd.run_ref=d.run_ref", resolve)
        self.assertEqual(resolve.count("helper_checkpoint_access_unimplemented"), 2)

    def test_no_part_row_is_reachable_once_unit_facts_are_sealed(self) -> None:
        # N-2 (sealed-facts review): every function the view's prepare reaches,
        # in the stream's last definitions. With sealed unit facts installed,
        # none reads a part relation; before them, only hidden length's record
        # builder and package view do. Redelivery re-reads frames through the
        # helper artifact read, whose part loop walks this kind's always-empty
        # 'parts' dependencies (pinned above).
        stream = installed_stream()
        seen: set[str] = set()
        todo = ["prepare_query_source_text_v1"]
        while todo:
            name = todo.pop()
            if name in seen or name not in stream:
                continue
            seen.add(name)
            todo.extend(re.findall(r"memoriesql\.(\w+)\(", stream[name]))
        self.assertIn("query_unit_source_text_v1", seen)
        readers = {name for name in seen if "evidence_package_parts" in stream[name]}
        sealed = any(
            (ROOT / "migrations").glob(
                "[0-9][0-9][0-9][0-9]_sealed_unit_query_facts.sql"
            )
        )
        self.assertEqual(
            readers,
            set()
            if sealed
            else {"query_unit_package_record_v1", "query_unit_package_v1"},
        )
