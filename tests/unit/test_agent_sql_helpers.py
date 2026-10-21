"""Pure body/provenance checks from f5; no executor or SQL availability claim.

Fictional payload fixtures are validation inputs, never authenticated evidence.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import unittest
from typing import Any, cast
from unittest.mock import Mock
from uuid import uuid4

from memoriesql.application.agent_sql_catalog import SqlCatalog
from memoriesql.application.agent_sql_helpers import (
    UnsupportedHelperProvenance,
    helper_content_page,
    project_helper_provenance,
    validate_helper_content,
)
from memoriesql.application.investigation_contracts import (
    HydrateRequest,
    InspectRequest,
    parse_investigation_request,
)


class _HelperFixture:
    def request(self, kind: str) -> dict[str, Any]:
        common: dict[str, Any] = {
            "contract_version": 1,
            "run_ref": str(uuid4()),
            "step_key": str(uuid4()),
            "kind": kind,
        }
        if kind == "inspect":
            common.update(
                observation_refs=[
                    {"bead_id": str(uuid4()), "bead_version_id": str(uuid4())}
                ],
                relation_refs=[],
                facets=["content"],
                view="historical",
                known_at="2026-10-19T00:00:00Z",
            )
        else:
            common.update(
                selections=[
                    {
                        "evidence_ref": str(uuid4()),
                        "part_id": str(uuid4()),
                        "representation": "normalized_text",
                        "start": 0,
                        "end": 3,
                    }
                ],
                max_bytes=64,
            )
        return common

    def setUp(self) -> None:
        payload = self.request("inspect")
        self.request_value = cast(
            InspectRequest, parse_investigation_request(json.dumps(payload).encode())
        )
        bead = payload["observation_refs"][0]
        self.observation = {
            "bead_id": bead["bead_id"],
            "bead_version_id": bead["bead_version_id"],
            "event_id": str(uuid4()),
            "source_unit_id": str(uuid4()),
            "bead_type_key": "fictional",
            "bead_type_revision_id": str(uuid4()),
            "title": None,
            "summary": None,
            "detail": None,
            "render_state": "unsupported",
            "recorded_at": "2026-10-19T00:00:00Z",
            "effective_at": None,
            "effective_basis": "unknown",
            "correction_state": "unsuperseded",
        }
        self.inspection: dict[str, Any] = {
            "observations": [
                {
                    "observation": self.observation,
                    "statements": [],
                    "render": None,
                    "type": {
                        "key": "fictional",
                        "revision_id": self.observation["bead_type_revision_id"],
                        "definition": "Fictional fixture.",
                    },
                    "authorship": {
                        "receipt_id": str(uuid4()),
                        "task_id": str(uuid4()),
                        "attempt_id": str(uuid4()),
                        "run_ref": "fixture.author",
                        "task_contract_key": "fixture",
                        "task_contract_version": 1,
                    },
                    "mentions": [],
                    "entities": [],
                    "topics": [],
                }
            ],
            "corrections": [],
            "source_units": [],
            "result": None,
            "relations": [],
            "relation_types": [],
            "relation_pairs": [],
            "evidence_pins": [],
            "provenance": None,
        }
        self.packet: dict[str, Any] = {
            "content": {
                "kind": "inspect",
                "payload": {"inspection": self.inspection},
                "refs": [],
                "access_bases": [],
            },
            "witnesses": {"evidence_pins": []},
            "dependencies": {
                "frames": [],
                "raw_sources": [],
                "parts": [],
                "result_pins": [],
            },
            "checked_at": "2026-10-19T00:00:00.000000Z",
        }


def hydration_fixture(
    representation: str = "normalized_text",
) -> tuple[HydrateRequest, dict[str, Any]]:
    request = _HelperFixture.request(_HelperFixture(), "hydrate_source")
    selection = request["selections"][0]
    selection["representation"] = representation
    if representation == "raw_bytes":
        selection["lineage_ordinal"] = 0
    data = (
        "é🙂a".encode() if representation == "normalized_text" else bytes([0, 255, 128])
    )
    package_id = str(uuid4())
    pin = {
        "ref": selection["evidence_ref"],
        "bead_id": str(uuid4()),
        "bead_version_id": str(uuid4()),
        "statement_id": str(uuid4()),
        "unit": {
            "event_id": str(uuid4()),
            "source_unit_id": str(uuid4()),
            "content_sha256": "a" * 64,
        },
        "package": {
            "package_id": package_id,
            "sealed_receipt_id": str(uuid4()),
            "inventory_sha256": "b" * 64,
            "required_parts": 1,
            "required_characters": 3,
            "required_utf8_bytes": 7,
            "source_revision_key": "fixture.revision",
            "occurrence_key": "fixture.occurrence",
            "occurrence_identity_basis": "producer_assigned",
            "package_revision": 1,
        },
        "part": {
            "part_id": selection["part_id"],
            "ordinal": 0,
            "component_key": "fixture",
            "component_offset": 0,
            "parent_component_key": None,
            "kind": "text",
            "content_sha256": "c" * 64,
            "characters": 3,
            "utf8_bytes": 7,
            "derivation": "producer_normalized",
            "lineage": [
                {
                    "source_range_receipt_id": str(uuid4()),
                    "byte_start": 0,
                    "byte_end_exclusive": 3,
                    "source_bytes_sha256": hashlib.sha256(
                        bytes([0, 255, 128])
                    ).hexdigest(),
                    "fold_receipt_id": None,
                    "fold_outcome_ordinal": None,
                }
            ],
        },
        "native": {
            key: None
            for key in (
                "native_id",
                "parent_native_id",
                "session_native_id",
                "branch_native_id",
                "participant_native_id",
                "role",
                "source_order",
                "occurred_at",
                "occurred_at_raw",
                "time_precision",
            )
        },
        "qualification": None,
        "normalization_policy_version": "fictional.normalization.v1",
    }
    content = {
        "kind": "hydrate_source",
        "payload": {
            "hydration": {
                "slices": [
                    {
                        "evidence_pin": pin,
                        "representation": representation,
                        "start": 0,
                        "end": 3,
                        "content": data.decode()
                        if representation == "normalized_text"
                        else None,
                        "bytes_base64": base64.b64encode(data).decode()
                        if representation == "raw_bytes"
                        else None,
                        "returned_sha256": hashlib.sha256(data).hexdigest(),
                    }
                ],
                "selection_complete": True,
            }
        },
        "refs": [],
        "access_bases": [],
    }
    return (
        cast(HydrateRequest, parse_investigation_request(json.dumps(request).encode())),
        content,
    )


class HelperPayloadChecks(unittest.TestCase):
    def test_normalized_unicode_uses_characters_and_actual_utf8_byte_budget(
        self,
    ) -> None:
        request, content = hydration_fixture()
        refs = validate_helper_content(request, content)
        self.assertEqual(refs[0]["kind"], "source")
        self.assertEqual(
            content["payload"]["hydration"]["slices"][0]["content"], "é🙂a"
        )
        too_small = request.model_copy(update={"max_bytes": 6})
        with self.assertRaises(ValueError):
            validate_helper_content(too_small, content)

    def test_raw_hydration_preserves_binary_bytes_and_exact_returned_digest(
        self,
    ) -> None:
        request, content = hydration_fixture("raw_bytes")
        self.assertEqual(len(validate_helper_content(request, content)), 1)
        item = content["payload"]["hydration"]["slices"][0]
        self.assertEqual(base64.b64decode(item["bytes_base64"]), bytes([0, 255, 128]))
        self.assertIsNone(item["content"])

    def test_wrong_pin_representation_range_or_digest_refuses_whole_payload(
        self,
    ) -> None:
        for field, replacement in (
            ("returned_sha256", "d" * 64),
            ("start", 1),
            ("end", 4),
            ("representation", "raw_bytes"),
        ):
            with self.subTest(field=field):
                request, content = hydration_fixture()
                content["payload"]["hydration"]["slices"][0][field] = replacement
                with self.assertRaises(ValueError):
                    validate_helper_content(request, content)
        request, content = hydration_fixture()
        content["payload"]["hydration"]["slices"][0]["evidence_pin"]["part"][
            "part_id"
        ] = str(uuid4())
        with self.assertRaises(ValueError):
            validate_helper_content(request, content)

    def test_private_pin_metadata_and_partial_selection_are_not_an_available_payload(
        self,
    ) -> None:
        request, content = hydration_fixture()
        item = content["payload"]["hydration"]["slices"][0]
        item["evidence_pin"]["native"]["private_locator"] = "fictional-protected"
        with self.assertRaises(ValueError):
            validate_helper_content(request, content)
        request, content = hydration_fixture()
        content["payload"]["hydration"]["selection_complete"] = False
        with self.assertRaises(ValueError):
            validate_helper_content(request, content)


class HelperPagingAndProvenance(unittest.TestCase):
    def native_graph(self) -> tuple[dict[str, Any], str]:
        frame, population, member, scan, root = [str(uuid4()) for _ in range(5)]
        keys = SqlCatalog.installed().relations["memory_v1.observations"].unique_keys[0]
        base = {
            "qualification": "native-bag-v1",
            "frame_ref": frame,
            "population_ref": population,
            "members": [
                {
                    "member_ref": member,
                    "relation": "memory_v1.observations",
                    "key_values": [str(uuid4()) for _ in keys],
                    "frame_ref": frame,
                    "evidence_refs": [],
                }
            ],
            "nodes": [
                {
                    "node_ref": scan,
                    "stage": 0,
                    "operation": "scan",
                    "input_refs": [],
                    "member_refs": [member],
                    "multiplicities": [2],
                    "absent_inputs": [],
                    "population_ref": population,
                },
                {
                    "node_ref": root,
                    "stage": 1,
                    "operation": "project",
                    "input_refs": [scan],
                    "member_refs": [],
                    "multiplicities": [],
                    "absent_inputs": [],
                    "population_ref": population,
                },
            ],
            "row_provenance": [root],
        }
        return (base, root)

    def test_provenance_preserves_root_identity_edges_and_canonical_bag_counts(
        self,
    ) -> None:
        native, root = self.native_graph()
        calls = Mock()
        graph = project_helper_provenance(native, root, check_work=calls)
        self.assertEqual(graph["nodes"][-1]["node_ref"], root)
        self.assertEqual(graph["nodes"][0]["multiplicities"], ["2"])
        self.assertEqual(graph["members"][0]["parent_fact_ref"], None)
        self.assertNotIn("projection_manifest_sha256", graph)
        self.assertEqual(calls.call_count, 2)

    def test_returned_member_identity_uses_the_existing_provenance_selector(
        self,
    ) -> None:
        native, root = self.native_graph()
        member = native["members"][0]
        result = project_helper_provenance(native, member["member_ref"])
        self.assertEqual(result["nodes"], [])
        self.assertEqual(result["members"], [{**member, "parent_fact_ref": None}])
        self.assertFalse(result["has_more"])
        self.assertNotIn("body", result["members"][0])

    def test_returned_internal_node_can_be_selected_without_inventing_a_row_root(
        self,
    ) -> None:
        native, root = self.native_graph()
        scan = native["nodes"][0]
        result = project_helper_provenance(native, scan["node_ref"])
        self.assertEqual(
            [node["node_ref"] for node in result["nodes"]], [scan["node_ref"]]
        )
        self.assertEqual(
            result["members"][0]["member_ref"], native["members"][0]["member_ref"]
        )

    def test_input_multiplicity_is_not_silently_deduplicated(self) -> None:
        native, root = self.native_graph()
        native["nodes"][-1]["input_multiplicities"] = [3]
        graph = project_helper_provenance(native, root)
        self.assertEqual(
            graph["nodes"][-1]["input_refs"], [native["nodes"][0]["node_ref"]] * 3
        )

    def test_unrepresented_negative_or_window_detail_is_explicitly_unsupported(
        self,
    ) -> None:
        for extra in (
            {"absent_inputs": [1]},
            {"operation": "window_partition"},
            {"native_values": [3]},
        ):
            with self.subTest(extra=extra):
                native, root = self.native_graph()
                native["nodes"][-1].update(extra)
                with self.assertRaises(UnsupportedHelperProvenance):
                    project_helper_provenance(native, root)

    def test_wrong_root_frame_dangling_edge_and_cycle_refuse(self) -> None:
        for mutation in ("root", "frame", "missing", "cycle"):
            with self.subTest(mutation=mutation):
                native, root = self.native_graph()
                if mutation == "root":
                    root = str(uuid4())
                elif mutation == "frame":
                    native["members"][0]["frame_ref"] = str(uuid4())
                elif mutation == "missing":
                    native["nodes"][-1]["input_refs"] = [str(uuid4())]
                else:
                    native["nodes"][0]["input_refs"] = [root]
                with self.assertRaises(ValueError):
                    project_helper_provenance(native, root)

    def test_no_unbounded_input_expansion_and_work_callback_is_observed(self) -> None:
        native, root = self.native_graph()
        native["nodes"][-1]["input_multiplicities"] = [2**63 - 1]
        with self.assertRaises(UnsupportedHelperProvenance):
            project_helper_provenance(native, root)
        with self.assertRaisesRegex(RuntimeError, "work ended"):
            project_helper_provenance(
                native, root, check_work=Mock(side_effect=RuntimeError("work ended"))
            )

    def test_hydration_pages_preserve_order_whole_spans_and_page_only_refs(
        self,
    ) -> None:
        request, content = hydration_fixture()
        second = copy.deepcopy(content["payload"]["hydration"]["slices"][0])
        second["evidence_pin"]["ref"] = str(uuid4())
        second["evidence_pin"]["part"]["part_id"] = str(uuid4())
        second_selection = request.selections[0].model_copy(
            update={
                "evidence_ref": second["evidence_pin"]["ref"],
                "part_id": second["evidence_pin"]["part"]["part_id"],
            }
        )
        request = request.model_copy(
            update={"selections": (*request.selections, second_selection)}
        )
        content["payload"]["hydration"]["slices"].append(second)
        cursor = str(uuid4())
        first = helper_content_page(request, content, first=1, count=1, cursor=cursor)
        last = helper_content_page(request, content, first=2, count=1, cursor=None)
        self.assertFalse(first["payload"]["hydration"]["selection_complete"])
        self.assertTrue(last["payload"]["hydration"]["selection_complete"])
        self.assertEqual(last["payload"]["hydration"]["slices"], [second])
        self.assertEqual(last["refs"][0]["ref"], second["evidence_pin"]["ref"])
        self.assertNotEqual(first["refs"], last["refs"])


class HelperBodyBindings(unittest.TestCase):
    def fixture(self) -> tuple[InspectRequest, dict[str, Any]]:
        example = _HelperFixture()
        example.setUp()
        content = copy.deepcopy(example.packet["content"])
        inspection = content["payload"]["inspection"]["observations"][0]
        inspection["type"]["revision_id"] = inspection["observation"][
            "bead_type_revision_id"
        ]
        return (example.request_value, content)

    def result_fixture(self) -> tuple[InspectRequest, dict[str, Any]]:
        request, content = self.fixture()
        pin = {"result_id": str(uuid4()), "content_digest": "a" * 64}
        request = cast(
            InspectRequest,
            parse_investigation_request(
                json.dumps(
                    {
                        **request.model_dump(mode="json"),
                        "observation_refs": [],
                        "result": {"pin": pin, "access": {"kind": "standalone"}},
                    }
                ).encode()
            ),
        )
        content["payload"]["inspection"].update(
            observations=[],
            result={
                **pin,
                "originating_run_ref": str(uuid4()),
                "created_at": "2026-10-19T00:00:00Z",
                "expires_at": "2026-11-18T00:00:00Z",
                "catalog_hash": "b" * 64,
                "policy_hash": "c" * 64,
                "result_schema": [],
                "parents": [],
                "query_ref": str(uuid4()),
                "lineage_ref": str(uuid4()),
                "coverage": {
                    "query_result": "complete",
                    "population_basis": "authorized_logical_scope",
                    "source_capture": "unknown",
                    "authored": "unknown",
                    "search_ready": "unknown",
                    "discovered": "unknown",
                    "gaps": [],
                    "truncation": None,
                },
                "order_basis": {"columns": [], "tie_basis": "witness_ordinal"},
                "total_rows": "0",
                "frame": {
                    "frame_ref": str(uuid4()),
                    "known_at": request.known_at,
                    "snapshot_at": "2026-10-19T00:00:00Z",
                    "snapshot_digest": "d" * 64,
                    "view": request.view,
                    "source_watermarks": [],
                    "lifecycle_projection_version": None,
                    "projection_manifest_sha256": None,
                },
            },
        )
        content["access_bases"] = [
            {
                "context": {"kind": "standalone"},
                "checked_at": request.known_at,
                "valid_until": "2026-11-18T00:00:00Z",
                "end_condition": "standalone_expiry",
                "hold_ref": None,
            }
        ]
        return (request, content)

    def test_nested_result_metadata_cannot_carry_private_fields(self) -> None:
        for field in ("coverage", "schema", "parent", "watermark", "order"):
            with self.subTest(field=field):
                request, content = self.result_fixture()
                result = content["payload"]["inspection"]["result"]
                if field == "coverage":
                    result["coverage"]["private_count"] = 7
                elif field == "schema":
                    result["result_schema"] = [
                        {
                            "name": "x",
                            "pg_type": "uuid",
                            "ref_type": None,
                            "nullable": False,
                            "private_label": "not public",
                        }
                    ]
                elif field == "parent":
                    result["parents"] = [
                        {
                            "result_id": str(uuid4()),
                            "content_digest": "f" * 64,
                            "edge": "refine",
                            "private_metadata": "not public",
                        }
                    ]
                elif field == "watermark":
                    result["frame"]["source_watermarks"] = [
                        {
                            "source_ref": str(uuid4()),
                            "known_through": None,
                            "coverage": "unknown",
                            "private_tail_count": 7,
                        }
                    ]
                else:
                    result["order_basis"]["private_plan"] = "not public"
                with self.assertRaises(ValueError):
                    validate_helper_content(request, content)

    def test_result_access_basis_is_required_and_cannot_be_relabelled(self) -> None:
        for mutation in ("missing", "context", "end", "clock"):
            with self.subTest(mutation=mutation):
                request, content = self.result_fixture()
                if mutation == "missing":
                    content["access_bases"] = []
                elif mutation == "context":
                    content["access_bases"][0]["context"] = {
                        "kind": "standalone",
                        "private": 7,
                    }
                elif mutation == "end":
                    content["access_bases"][0]["end_condition"] = "save_release"
                else:
                    content["access_bases"][0]["checked_at"] = None
                with self.assertRaises(ValueError):
                    validate_helper_content(request, content)

    def test_result_metadata_matches_the_explicit_pin_and_frame(self) -> None:
        request, content = self.result_fixture()
        self.assertEqual(validate_helper_content(request, content), [])
        for field, value in (("result_id", str(uuid4())), ("content_digest", "f" * 64)):
            with self.subTest(field=field):
                mutated = copy.deepcopy(content)
                mutated["payload"]["inspection"]["result"][field] = value
                with self.assertRaises(ValueError):
                    validate_helper_content(request, mutated)
        for field, value in (
            ("known_at", "2026-10-18T00:00:00Z"),
            ("view", "resolved"),
        ):
            with self.subTest(field=field):
                mutated = copy.deepcopy(content)
                mutated["payload"]["inspection"]["result"]["frame"][field] = value
                with self.assertRaises(ValueError):
                    validate_helper_content(request, mutated)

    def test_complete_render_binds_actual_statements_and_omission(self) -> None:
        request, content = self.fixture()
        inspection = content["payload"]["inspection"]["observations"][0]
        observation = inspection["observation"]
        first, second = (str(uuid4()), str(uuid4()))
        inspection["statements"] = [
            {
                "statement_id": ref,
                "bead_id": observation["bead_id"],
                "bead_version_id": observation["bead_version_id"],
                "sequence": str(ordinal),
                "kind": "observation",
                "text": "A fictional accepted statement.",
                "supersedes_statement_id": None,
                "correction_reason": None,
                "recorded_at": observation["recorded_at"],
            }
            for ordinal, ref in enumerate((first, second), 1)
        ]
        observation.update(
            render_state="present", title="A fictional title.", summary="A summary."
        )
        inspection["render"] = {
            "revision": 2,
            "title": {"text": observation["title"], "statement_ids": [first]},
            "summary": [{"text": "A summary.", "statement_ids": [first]}],
            "detail": [],
            "omissions": [{"statement_id": second, "reason": "Not in this rendering."}],
        }
        self.assertEqual(
            {item["ref"] for item in validate_helper_content(request, content)},
            {observation["bead_id"], first, second},
        )

    def test_type_pin_must_be_the_returned_observations_actual_type(self) -> None:
        for field, value in (("key", "another-type"), ("revision_id", str(uuid4()))):
            with self.subTest(field=field):
                request, content = self.fixture()
                content["payload"]["inspection"]["observations"][0]["type"][field] = (
                    value
                )
                with self.assertRaises(ValueError):
                    validate_helper_content(request, content)

    def test_unrequested_content_is_not_disclosed_under_a_lifecycle_facet(self) -> None:
        request, content = self.fixture()
        request = request.model_copy(update={"facets": ("lifecycle",)})
        with self.assertRaises(ValueError):
            validate_helper_content(request, content)

    def test_render_cannot_cite_or_omit_an_unreturned_statement(self) -> None:
        for target in ("clause", "omission"):
            with self.subTest(target=target):
                request, content = self.fixture()
                inspection = content["payload"]["inspection"]["observations"][0]
                inspection["observation"].update(
                    render_state="present", title="A fictional title."
                )
                inspection["render"] = {
                    "revision": 2,
                    "title": {
                        "text": "A fictional title.",
                        "statement_ids": [str(uuid4())] if target == "clause" else [],
                    },
                    "summary": [],
                    "detail": [],
                    "omissions": [{"statement_id": str(uuid4()), "reason": "fixture"}]
                    if target == "omission"
                    else [],
                }
                with self.assertRaises(ValueError):
                    validate_helper_content(request, content)

    def test_render_state_and_required_text_controls_cannot_be_forged(self) -> None:
        request, content = self.fixture()
        inspection = content["payload"]["inspection"]["observations"][0]
        inspection["render"] = {
            "revision": True,
            "title": {"text": None, "statement_ids": []},
            "summary": [],
            "detail": [],
            "omissions": [],
        }
        with self.assertRaises(ValueError):
            validate_helper_content(request, content)

    def test_inspection_cannot_omit_its_explicit_result_target(self) -> None:
        request, content = self.fixture()
        request = cast(
            InspectRequest,
            parse_investigation_request(
                json.dumps(
                    {
                        **request.model_dump(mode="json"),
                        "observation_refs": [],
                        "result": {
                            "pin": {
                                "result_id": str(uuid4()),
                                "content_digest": "a" * 64,
                            },
                            "access": {"kind": "standalone"},
                        },
                    }
                ).encode()
            ),
        )
        content["payload"]["inspection"]["observations"] = []
        with self.assertRaises(ValueError):
            validate_helper_content(request, content)

    def test_provenance_logical_key_cannot_be_null(self) -> None:
        request, content = self.fixture()
        graph, root = HelperPagingAndProvenance().native_graph()
        graph["members"][0]["key_values"][0] = None
        with self.assertRaises(ValueError):
            project_helper_provenance(graph, root)
