"""Seen fictional closed-wire proofs; no database, provider or unseen material."""

from __future__ import annotations

import copy
import hashlib
import json
import unittest
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import ValidationError

from memoriesql.application.agent_sql_admission import admit_query
from memoriesql.application.agent_sql_catalog import SqlAdmissionError, SqlCatalog
from memoriesql.application.investigation_contracts import (
    CheckpointRequest,
    InvestigationRequestError,
    QueryRequest,
    encode_result_scalar,
    parse_investigation_request,
    request_fingerprint,
    result_json_bytes,
)

RUN = "11111111-1111-4111-8111-111111111111"
STEP = "22222222-2222-4222-8222-222222222222"
RESULT = "33333333-3333-4333-8333-333333333333"
CHECKPOINT = "44444444-4444-4444-8444-444444444444"
SAVE = "55555555-5555-4555-8555-555555555555"
PIN = {"result_id": RESULT, "content_digest": "a" * 64}
CP = {"checkpoint_id": CHECKPOINT, "manifest_digest": "b" * 64}
DIRECT = {"kind": "standalone"}
NAMED = {"kind": "named_save", "save_id": SAVE, "revision": "1"}
CONTEXT = {"kind": "checkpoint", **CP, "root": PIN, "basis": NAMED}
CP_ACCESS = {"checkpoint": CP, "basis": NAMED}


def envelope(kind: str, **payload: Any) -> dict[str, Any]:
    return {
        "contract_version": 1,
        "run_ref": RUN,
        "step_key": STEP,
        "kind": kind,
        **payload,
    }


def wire(document: Any) -> bytes:
    return json.dumps(document, ensure_ascii=True).encode()


def query(**updates: Any) -> dict[str, Any]:
    return envelope(
        "query",
        **{
            "catalog_hash": SqlCatalog.installed().hash,
            "sql": "SELECT o.bead_id AS id FROM memory_v1.observations o",
            "parameters": [],
            "inputs": [],
            "parents": [],
            "scope": {
                "source_refs": [],
                "known_at": "2026-09-01T00:00:00Z",
                "view": "historical",
            },
            "intent": "discover",
            "max_result_bytes": 1048576,
            "page_size": 20,
            **updates,
        },
    )


def checkpoint(**updates: Any) -> dict[str, Any]:
    return envelope(
        "checkpoint",
        **{
            "question": "Fictional fleet maintenance question",
            "progress": {
                "status": "investigating",
                "note": "Need dated source evidence",
            },
            "findings": [],
            "roots": [{"result": PIN, "access": CONTEXT}],
            **updates,
        },
    )


class InvestigationContractTests(unittest.TestCase):
    def assert_refused(self, document: Any) -> InvestigationRequestError:
        with self.assertRaises(InvestigationRequestError) as caught:
            parse_investigation_request(wire(document))
        self.assertIn(caught.exception.code, {"syntax", "binding", "type", "feature"})
        return caught.exception

    def test_every_action_roundtrips_without_default_context_or_hidden_fields(
        self,
    ) -> None:
        examples = [
            query(),
            envelope("reuse_result", result=PIN, access=CONTEXT, page_size=20),
            envelope(
                "inspect",
                observation_refs=[],
                relation_refs=[],
                result={"pin": PIN, "access": CONTEXT},
                provenance_ref=STEP,
                facets=["provenance"],
                view="historical",
                known_at="2026-09-01T00:00:00Z",
            ),
            envelope(
                "hydrate_source",
                selections=[
                    {
                        "evidence_ref": STEP,
                        "part_id": RESULT,
                        "lineage_ordinal": 0,
                        "representation": "raw_bytes",
                        "start": 0,
                        "end": 12,
                        "origin": {"result": PIN, "access": CONTEXT},
                    }
                ],
                max_bytes=65536,
            ),
            checkpoint(),
            envelope("read_checkpoint", access=CP_ACCESS),
            envelope("save_checkpoint", access=CP_ACCESS, name="maintenance-2026"),
            envelope("resolve_save", investigation_id=RUN, name="maintenance-2026"),
            envelope(
                "release_save",
                investigation_id=RUN,
                name="maintenance-2026",
                expected_save_revision="1",
            ),
            envelope("restore_checkpoint", access=CP_ACCESS, selected_roots=[PIN]),
            envelope("branch_investigation", access=CP_ACCESS, selected_roots=[]),
        ]
        for document in examples:
            with self.subTest(kind=document["kind"]):
                parsed = parse_investigation_request(wire(document))
                self.assertEqual(
                    parsed.model_dump(mode="json", exclude_unset=True), document
                )
                with self.assertRaises(ValidationError):
                    parsed.step_key = RUN

    def test_context_basis_cursor_and_root_digest_are_fingerprint_inputs(self) -> None:
        base = envelope("reuse_result", result=PIN, access=CONTEXT, page_size=20)
        alternatives = [copy.deepcopy(base) for _ in range(6)]
        alternatives[0]["access"] = DIRECT
        alternatives[1]["access"]["basis"] = {"kind": "automatic"}
        alternatives[2]["access"]["basis"]["revision"] = "2"
        alternatives[3]["access"]["manifest_digest"] = "c" * 64
        alternatives[4]["cursor"] = "fictional-pinned-cursor"
        alternatives[5]["access"]["root"]["content_digest"] = "d" * 64
        alternatives[5]["result"]["content_digest"] = "d" * 64
        fingerprints = {
            request_fingerprint(parse_investigation_request(wire(d)))
            for d in [base, *alternatives]
        }
        self.assertEqual(len(fingerprints), 7)
        self.assertEqual(
            request_fingerprint(parse_investigation_request(wire(base))),
            hashlib.sha256(result_json_bytes(base)).hexdigest(),
        )

    def test_held_ancestor_cannot_be_substituted_for_selected_context_root(
        self,
    ) -> None:
        document = envelope("reuse_result", result=PIN, access=CONTEXT, page_size=20)
        for field, value in (("result_id", RUN), ("content_digest", "c" * 64)):
            bad = copy.deepcopy(document)
            bad["access"]["root"] = {**bad["access"]["root"], field: value}
            self.assert_refused(bad)
        no_context = copy.deepcopy(document)
        del no_context["access"]
        self.assert_refused(no_context)
        hidden_hold = copy.deepcopy(document)
        hidden_hold["access"] = {"kind": "dependency_only"}
        self.assert_refused(hidden_hold)

    def test_unknown_nested_fields_and_owner_scope_injection_are_refused(self) -> None:
        marker = "fictional-protected-marker"
        for document in (
            query(tenant_id=marker),
            query(allowances=marker),
            checkpoint(hidden_reasoning=marker),
        ):
            error = self.assert_refused(document)
            self.assertNotIn(marker, repr(error))
            self.assertNotIn(marker, str(error))
            self.assertEqual(set(error.__dict__), {"code"})
        bad = checkpoint()
        bad["roots"][0]["access"]["basis"]["expires_at"] = marker
        self.assert_refused(bad)

    def test_duplicate_json_keys_nonfinite_numbers_and_invalid_utf8_are_safe(
        self,
    ) -> None:
        for data in (
            b'{"kind":"query","kind":"reuse_result"}',
            b'{"value":NaN}',
            b'{"value":Infinity}',
            b'{"value":1.5}',
            b"\xff",
            b"[]",
            b"null",
            b"\xef\xbb\xbf{}",
            b'{"kind":',
        ):
            with self.subTest(data=data), self.assertRaises(InvestigationRequestError):
                parse_investigation_request(data)
        bad = checkpoint(question="\ud800")
        self.assert_refused(bad)
        self.assert_refused(checkpoint(question="before\x00after"))

    def test_no_bool_integer_coercion_or_noncanonical_reference_encoding(self) -> None:
        for document in (
            query(contract_version=True),
            query(contract_version="1"),
            query(page_size=True),
            query(page_size="20"),
            query(page_size=0),
            query(page_size=51),
            query(run_ref=RUN.replace("-", "")),
            query(step_key=STEP + "\n"),
        ):
            self.assert_refused(document)
        for revision in (1, True, "0", "-1", "01", str(2**63)):
            bad = envelope("read_checkpoint", access=copy.deepcopy(CP_ACCESS))
            bad["access"]["basis"]["revision"] = revision
            self.assert_refused(bad)

    def test_query_intents_preserve_live_and_saved_frame_separation(self) -> None:
        binding = {"result": PIN, "access": CONTEXT}
        input_ = {**binding, "alias": "retained"}
        valid = query(
            intent="refine",
            inputs=[input_],
            parents=[binding],
            sql="SELECT i.id FROM input.retained i",
        )
        self.assertIsInstance(parse_investigation_request(wire(valid)), QueryRequest)
        for intent in ("expand", "refresh"):
            parse_investigation_request(wire(query(intent=intent, parents=[binding])))
            self.assert_refused(
                query(intent=intent, inputs=[input_], parents=[binding])
            )
            self.assert_refused(query(intent=intent))
        self.assert_refused(query(intent="refine"))
        self.assert_refused(query(intent="refine", inputs=[input_]))
        self.assert_refused(query(inputs=[input_], parents=[binding]))
        self.assert_refused(
            query(intent="refine", inputs=[input_, input_], parents=[binding])
        )
        self.assert_refused(
            query(intent="refine", inputs=[input_], parents=[binding, binding])
        )

    def test_parameters_decode_without_claiming_anchor_authority(self) -> None:
        catalog = SqlCatalog.installed()
        parsed = parse_investigation_request(
            wire(
                query(
                    sql="SELECT o.bead_id AS id FROM memory_v1.observations o WHERE o.bead_id=$1",
                    parameters=[{"position": 1, "type": "bead_ref", "value": RESULT}],
                )
            )
        )
        assert isinstance(parsed, QueryRequest)
        parameter = parsed.parameters[0].sql_parameter(catalog)
        with self.assertRaises(SqlAdmissionError) as denied:
            catalog.parameters((parameter,))
        self.assertEqual(denied.exception.code, "unavailable")
        admitted = admit_query(
            parsed.sql, (parameter,), admitted_anchors=frozenset({("bead_ref", RESULT)})
        )
        self.assertEqual(admitted.parameters["p1"], UUID(RESULT))
        self.assertNotIn(RESULT, admitted.sql)

    def test_parameter_types_arrays_and_byte_limits_are_enforced(self) -> None:
        examples = [
            ("text", "fictional"),
            ("bool", True),
            ("int8", "-1"),
            ("numeric", "1.25"),
            ("numeric", "1.00"),
            ("numeric", "1e0"),
            ("int8", "-0"),
            ("float8", "-0x0.0p+0"),
            ("timestamptz", "2026-09-01T00:00:00Z"),
            ("uuid[]", [RESULT]),
            ("text[]", []),
            ("text", None),
        ]
        for type_, value in examples:
            parse_investigation_request(
                wire(query(parameters=[{"position": 1, "type": type_, "value": value}]))
            )
        invalid = [
            ("text", 1),
            ("bool", "true"),
            ("int8", str(2**63)),
            ("numeric", "NaN"),
            ("float8", "NaN"),
            ("float8", "0.0"),
            ("unknown", "x"),
            ("text[]", None),
            ("text[]", [None]),
            ("text[]", ["x"] * 65),
        ]
        for type_, value in invalid:
            self.assert_refused(
                query(parameters=[{"position": 1, "type": type_, "value": value}])
            )
        self.assert_refused(
            query(parameters=[{"position": 1, "type": "text", "value": "x" * 65537}])
        )
        p = {"position": 1, "type": "int8", "value": "1"}
        self.assert_refused(query(parameters=[p, p]))
        self.assert_refused(query(sql="é" * 16385))

    def test_candidate_profiles_are_explicit_without_default_or_fallback(self) -> None:
        candidate = {
            "surface": "both",
            "query": "fictional fleet",
            "max_candidates": 2000,
        }
        parse_investigation_request(
            wire(query(candidate_profile_ref=RESULT, candidate_request=candidate))
        )
        self.assert_refused(query(candidate_profile_ref=RESULT))
        self.assert_refused(query(candidate_request=candidate))
        self.assert_refused(
            query(
                candidate_profile_ref=RESULT,
                candidate_request={**candidate, "max_candidates": 2001},
            )
        )

    def test_unknown_catalog_is_unsupported_without_silent_downgrade(self) -> None:
        error = self.assert_refused(query(catalog_hash="c" * 64))
        self.assertEqual(error.code, "feature")
        self.assertEqual(error.outcome, "unsupported_query")
        invalid = self.assert_refused(query(page_size=True))
        self.assertEqual(invalid.outcome, "invalid_request")

    def test_recursion_wire_bound_preserves_quoted_identifiers(self) -> None:
        recursion = {
            "cte": "quoted investigation walk",
            "depth_column": "depth level",
            "node_column": "node ref",
            "max_depth": 8,
        }
        parse_investigation_request(wire(query(recursion=recursion)))
        self.assert_refused(query(recursion={**recursion, "max_depth": 9}))
        self.assert_refused(query(recursion={**recursion, "max_depth": True}))

    def test_inspection_needs_visible_result_for_provenance_and_combined_target_bound(
        self,
    ) -> None:
        base = envelope(
            "inspect",
            observation_refs=[],
            relation_refs=[RESULT],
            facets=["lifecycle"],
            view="resolved",
            known_at="2026-09-01T00:00:00Z",
        )
        parse_investigation_request(wire(base))
        self.assert_refused({**base, "relation_refs": []})
        self.assert_refused({**base, "provenance_ref": STEP})
        self.assert_refused(
            {
                **base,
                "relation_refs": [RESULT] * 16,
                "observation_refs": [{"bead_id": RUN, "bead_version_id": STEP}],
            }
        )

    def test_hydration_units_lineage_span_and_context_are_explicit(self) -> None:
        selection = {
            "evidence_ref": STEP,
            "part_id": RESULT,
            "representation": "normalized_text",
            "start": 0,
            "end": 16384,
        }
        base = envelope("hydrate_source", selections=[selection], max_bytes=65536)
        parse_investigation_request(wire(base))
        for change in (
            {"end": 16385},
            {"start": 10, "end": 9},
            {"representation": "raw_bytes"},
            {"start": True},
        ):
            self.assert_refused({**base, "selections": [{**selection, **change}]})
        self.assert_refused({**base, "selections": [selection] * 9})
        self.assert_refused({**base, "max_bytes": 65537})

    def test_checkpoint_first_append_and_text_byte_bounds(self) -> None:
        parsed = parse_investigation_request(wire(checkpoint()))
        self.assertIsInstance(parsed, CheckpointRequest)
        parse_investigation_request(
            wire(checkpoint(investigation_id=RUN, branch_id=STEP, expected_head=CP))
        )
        self.assert_refused(checkpoint(investigation_id=RUN))
        self.assert_refused(checkpoint(question="é" * 2049))
        self.assert_refused(
            checkpoint(progress={"status": "paused", "note": "é" * 1025})
        )
        finding = {"text": "x" * 4097, "facts": [], "evidence_refs": []}
        self.assert_refused(checkpoint(findings=[finding, finding]))
        self.assert_refused(checkpoint(roots=[{"result": PIN, "access": DIRECT}] * 2))
        self.assert_refused(
            checkpoint(findings=[{"text": "x", "facts": [], "evidence_refs": []}] * 17)
        )

    def test_save_and_restore_never_infer_a_hold_or_competing_revision(self) -> None:
        save = envelope("save_checkpoint", access=CP_ACCESS, name="fleet-2026")
        parse_investigation_request(wire(save))
        parse_investigation_request(wire({**save, "expected_save_revision": "2"}))
        for name in ("Fleet", "a/b", "a" * 65):
            self.assert_refused({**save, "name": name})
        self.assert_refused(
            envelope("release_save", investigation_id=RUN, name="fleet-2026")
        )
        self.assert_refused(
            envelope("restore_checkpoint", access=CP_ACCESS, selected_roots=[PIN, PIN])
        )
        self.assert_refused(envelope("restore_checkpoint", selected_roots=[]))

    def test_native_scalar_profile_preserves_exact_types_and_clock_precision(
        self,
    ) -> None:
        samples: list[tuple[Any, Any]] = [
            (None, None),
            (True, True),
            (2**63 - 1, "9223372036854775807"),
            (UUID(RESULT), RESULT),
            (Decimal("-0.000"), "0"),
            (Decimal("123.45000"), "123.45"),
            (Decimal("1E+3"), "1000"),
            (-0.0, "-0x0.0p+0"),
            (0.1, "0x1.999999999999ap-4"),
            (b"\x00\xff", "AP8="),
            ("é😀\x7f", "é😀\x7f"),
            (
                datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=2))),
                "2025-12-31T22:00:00.000000Z",
            ),
            (datetime(1, 1, 1, tzinfo=UTC), "0001-01-01T00:00:00.000000Z"),
        ]
        for value, expected in samples:
            with self.subTest(value=value):
                self.assertEqual(encode_result_scalar(value), expected)
        for value in (
            2**63,
            float("nan"),
            float("inf"),
            Decimal("NaN"),
            datetime(2026, 1, 1),
            object(),
        ):
            with self.assertRaises(ValueError):
                encode_result_scalar(value)

    def test_canonical_bytes_golden_unicode_controls_and_no_implicit_numbers(
        self,
    ) -> None:
        value = {"z": ["é😀\x7f", "e\u0301", "\b\f\n\r\t", "/"], "a": True, "n": None}
        expected = b'{"a":true,"n":null,"z":["\\u00e9\\ud83d\\ude00\\u007f","e\\u0301","\\b\\f\\n\\r\\t","/"]}'
        self.assertEqual(result_json_bytes(value), expected)
        for bad in (
            {"score": 1.0},
            {"count": Decimal("1")},
            {"ref": UUID(RESULT)},
            {1: "not a text key"},
            {"text": "\udfff"},
        ):
            with self.assertRaises((ValueError, TypeError, UnicodeError)):
                result_json_bytes(bad)
        cyclic: list[Any] = []
        cyclic.append(cyclic)
        with self.assertRaises(ValueError):
            result_json_bytes(cyclic)


if __name__ == "__main__":
    unittest.main()
