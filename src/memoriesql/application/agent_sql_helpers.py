"""Closed helper payload checks and delivery-derived visibility; no authority.

SQL owns authorization and immutable facts. These checks refuse malformed
payloads and bind hydrated bytes to the exact requested representation/range.
They never turn a normalized projection into raw-source evidence.
"""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Callable
from typing import Any

from pydantic import TypeAdapter

from memoriesql.application.agent_sql_catalog import SqlCatalog, _value
from memoriesql.application.assessed_relation_lifecycle import ProjectedAssertion
from memoriesql.application.authored_relations import RelationTypeDefinition
from memoriesql.application.evidence_packages import NativeFacts, SourceQualification
from memoriesql.application.investigation_contracts import (
    HYDRATION_SPAN_BYTES,
    AccessContext,
    HydrateRequest,
    InspectRequest,
    result_json_bytes,
)
from memoriesql.application.relation_inspection import InspectedPairDisposition


def _object(value: Any, keys: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != set(keys.split()):
        raise ValueError("helper payload shape mismatch")
    return value


def _array(value: Any) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError("helper payload array mismatch")
    return value


def _row(catalog: SqlCatalog, relation: str, value: Any) -> dict[str, Any]:
    columns = catalog.relations["memory_v1." + relation].columns
    row = _object(value, " ".join(c.name for c in columns))
    for column in columns:
        cell = row[column.name]
        if cell is None:
            if not column.type.nullable:
                raise ValueError("helper row null mismatch")
        else:
            _value(column.type.pg_type, cell)
    return row


def _ref(value: Any) -> None:
    _value("uuid", value)
    if value is None:
        raise ValueError("missing helper reference")


def _hash(value: Any) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(c not in "0123456789abcdef" for c in value)
    ):
        raise ValueError("invalid helper digest")


def _text(value: Any) -> None:
    if not isinstance(value, str):
        raise ValueError("missing helper text")
    _value("text", value)


def _positive_control(value: Any) -> None:
    if type(value) is not int or value < 1:
        raise ValueError("invalid helper control")


def _clock(value: Any, *, nullable: bool = False) -> None:
    if value is None and not nullable:
        raise ValueError("missing helper clock")
    _value("timestamptz", value)


_ACCESS_CONTEXT: TypeAdapter[AccessContext] = TypeAdapter(AccessContext)


def _result_metadata(request: InspectRequest, value: Any) -> None:
    metadata = _object(
        value,
        "result_id content_digest originating_run_ref created_at expires_at catalog_hash policy_hash result_schema frame parents query_ref lineage_ref coverage order_basis total_rows",
    )
    if request.result is None or (
        metadata["result_id"],
        metadata["content_digest"],
    ) != (
        request.result.pin.result_id,
        request.result.pin.content_digest,
    ):
        raise ValueError("helper result pin binding")
    for key in ("result_id", "originating_run_ref", "query_ref", "lineage_ref"):
        _ref(metadata[key])
    for key in ("content_digest", "catalog_hash", "policy_hash"):
        _hash(metadata[key])
    for key in ("created_at", "expires_at"):
        _clock(metadata[key])
    if metadata["total_rows"] is None or _value("int8", metadata["total_rows"]) < 0:
        raise ValueError("invalid helper result row count")
    frame = _object(
        metadata["frame"],
        "frame_ref known_at snapshot_at snapshot_digest view source_watermarks lifecycle_projection_version projection_manifest_sha256",
    )
    _ref(frame["frame_ref"])
    _hash(frame["snapshot_digest"])
    _clock(frame["snapshot_at"])
    _clock(frame["known_at"])
    if frame["view"] != request.view or _value(
        "timestamptz", frame["known_at"]
    ) != _value("timestamptz", request.known_at):
        raise ValueError("helper result frame binding")
    if (frame["lifecycle_projection_version"] is None) != (
        frame["projection_manifest_sha256"] is None
    ):
        raise ValueError("helper lifecycle frame binding")
    if frame["lifecycle_projection_version"] is not None:
        _positive_control(frame["lifecycle_projection_version"])
        _hash(frame["projection_manifest_sha256"])
    states = {"complete", "partial", "unknown", "unavailable"}
    for watermark in _array(frame["source_watermarks"]):
        watermark = _object(watermark, "source_ref known_through coverage")
        _ref(watermark["source_ref"])
        _clock(watermark["known_through"], nullable=True)
        if watermark["coverage"] not in states:
            raise ValueError("invalid helper watermark coverage")
    catalog = SqlCatalog.installed()
    columns = set()
    for column in _array(metadata["result_schema"]):
        column = _object(column, "name pg_type ref_type nullable")
        _text(column["name"])
        columns.add(column["name"])
        if (
            column["pg_type"]
            not in {"uuid", "text", "bool", "int8", "numeric", "float8", "timestamptz"}
            or type(column["nullable"]) is not bool
        ):
            raise ValueError("invalid helper result type")
        if (
            column["ref_type"] is not None
            and catalog.reference_types.get(column["ref_type"]) != column["pg_type"]
        ):
            raise ValueError("invalid helper semantic reference type")
    for parent in _array(metadata["parents"]):
        parent = _object(parent, "result_id content_digest edge")
        _ref(parent["result_id"])
        _hash(parent["content_digest"])
        if parent["edge"] not in {"refine", "join", "expand", "refresh"}:
            raise ValueError("invalid helper lineage edge")
    coverage = metadata["coverage"]
    if not isinstance(coverage, dict):
        raise ValueError("invalid helper coverage")
    _object(
        coverage,
        "query_result population_basis source_capture authored search_ready discovered gaps truncation"
        + (" known_through" if "known_through" in coverage else ""),
    )
    if (
        coverage["query_result"] != "complete"
        or coverage["population_basis"]
        not in {"authorized_logical_scope", "retained_subset", "limited_query"}
        or coverage["truncation"] not in {None, "explicit_limit", "explicit_depth"}
    ):
        raise ValueError("invalid helper coverage claim")
    for key in ("source_capture", "authored", "search_ready", "discovered"):
        if coverage[key] not in states:
            raise ValueError("invalid helper coverage state")
    if "known_through" in coverage:
        _clock(coverage["known_through"], nullable=True)
    for gap in _array(coverage["gaps"]):
        gap = _object(gap, "facet reason")
        _text(gap["facet"])
        _text(gap["reason"])
    order = _object(metadata["order_basis"], "columns tie_basis")
    if order["tie_basis"] != "witness_ordinal":
        raise ValueError("invalid helper order basis")
    for column in _array(order["columns"]):
        column = _object(column, "column direction nulls")
        if (
            column["column"] not in columns
            or column["direction"] not in {"asc", "desc"}
            or column["nulls"] not in {"first", "last"}
        ):
            raise ValueError("invalid helper ordering column")


def _pin(value: Any) -> dict[str, Any]:
    pin = _object(
        value,
        "ref bead_id bead_version_id statement_id unit package part native qualification normalization_policy_version",
    )
    for key in ("ref", "bead_id", "bead_version_id", "statement_id"):
        _ref(pin[key])
    unit = _object(pin["unit"], "event_id source_unit_id content_sha256")
    _ref(unit["event_id"])
    _ref(unit["source_unit_id"])
    _hash(unit["content_sha256"])
    if pin["package"] is not None:
        package = _object(
            pin["package"],
            "package_id sealed_receipt_id inventory_sha256 required_parts required_characters required_utf8_bytes source_revision_key occurrence_key occurrence_identity_basis package_revision",
        )
        _ref(package["package_id"])
        _ref(package["sealed_receipt_id"])
        _hash(package["inventory_sha256"])
        for key in (
            "required_parts",
            "required_characters",
            "required_utf8_bytes",
            "package_revision",
        ):
            if type(package[key]) is not int or package[key] < 1:
                raise ValueError("invalid helper package control")
        if package["occurrence_identity_basis"] not in {"native", "producer_assigned"}:
            raise ValueError("invalid helper occurrence basis")
        for key in ("source_revision_key", "occurrence_key"):
            _value("text", package[key])
            if not package[key]:
                raise ValueError("missing helper package identity")
    if pin["part"] is not None:
        if pin["package"] is None:
            raise ValueError("helper part requires its package")
        part = _object(
            pin["part"],
            "part_id ordinal component_key component_offset parent_component_key kind content_sha256 characters utf8_bytes derivation lineage",
        )
        _ref(part["part_id"])
        _hash(part["content_sha256"])
        for key in ("ordinal", "component_offset", "characters", "utf8_bytes"):
            if type(part[key]) is not int or part[key] < 0:
                raise ValueError("invalid helper part control")
        for key in ("component_key", "kind"):
            _value("text", part[key])
        if part["parent_component_key"] is not None:
            _value("text", part["parent_component_key"])
        if part["derivation"] not in {"identity_utf8", "producer_normalized"}:
            raise ValueError("invalid helper derivation")
        for span in _array(part["lineage"]):
            span = _object(
                span,
                "source_range_receipt_id byte_start byte_end_exclusive source_bytes_sha256 fold_receipt_id fold_outcome_ordinal",
            )
            _ref(span["source_range_receipt_id"])
            _hash(span["source_bytes_sha256"])
            if (
                type(span["byte_start"]) is not int
                or type(span["byte_end_exclusive"]) is not int
                or not 0 <= span["byte_start"] < span["byte_end_exclusive"]
            ):
                raise ValueError("invalid helper lineage interval")
            if (span["fold_receipt_id"] is None) != (
                span["fold_outcome_ordinal"] is None
            ):
                raise ValueError("incomplete helper fold binding")
            if span["fold_receipt_id"] is not None:
                _ref(span["fold_receipt_id"])
                if (
                    type(span["fold_outcome_ordinal"]) is not int
                    or span["fold_outcome_ordinal"] < 0
                ):
                    raise ValueError("invalid helper fold ordinal")
    native = _object(
        pin["native"],
        "native_id parent_native_id session_native_id branch_native_id participant_native_id role source_order occurred_at occurred_at_raw time_precision",
    )
    NativeFacts.model_validate_json(json.dumps(native))
    if native["occurred_at"] is not None:
        _value("timestamptz", native["occurred_at"])
    if pin["qualification"] is not None:
        _object(
            pin["qualification"],
            "qualification_ref boundary boundary_basis physical_records topology normalized_input source_completeness exclusions unresolved_coverage",
        )
        SourceQualification.model_validate_json(json.dumps(pin["qualification"]))
    if pin["normalization_policy_version"] is not None:
        _value("text", pin["normalization_policy_version"])
    return pin


def _source_ref(pin: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "source",
        "ref": pin["ref"],
        "unit_ref": pin["unit"]["source_unit_id"],
        "content_sha256": pin["unit"]["content_sha256"],
    }


def _observation_ref(bead: str, version: str) -> dict[str, Any]:
    _ref(bead)
    _ref(version)
    return {"kind": "observation", "ref": bead, "version_ref": version}


_PROVENANCE_OPERATIONS = {
    "scan",
    "filter",
    "project",
    "join",
    "group",
    "distinct",
    "exists",
    "set",
    "window",
    "recursion",
    "limit",
}


class UnsupportedHelperProvenance(ValueError):
    """The approved public type cannot faithfully represent this witness."""


def _provenance(value: Any, *, require_complete: bool) -> None:
    graph = _object(value, "population_ref nodes members next_cursor has_more")
    _ref(graph["population_ref"])
    if (
        type(graph["has_more"]) is not bool
        or (graph["next_cursor"] is None) == graph["has_more"]
    ):
        raise ValueError("invalid helper provenance cursor")
    if graph["next_cursor"] is not None:
        _value("text", graph["next_cursor"])
    nodes = {}
    members = {}
    catalog = SqlCatalog.installed()
    for member in _array(graph["members"]):
        member = _object(
            member,
            "member_ref relation key_values frame_ref evidence_refs parent_fact_ref",
        )
        _ref(member["member_ref"])
        _ref(member["frame_ref"])
        relation = catalog.relations.get(member["relation"])
        if relation is None or not relation.unique_keys:
            raise ValueError("unknown helper provenance relation")
        keys = relation.unique_keys[0]
        values = _array(member["key_values"])
        if len(keys) != len(values):
            raise ValueError("helper provenance key arity")
        for key, cell in zip(keys, values, strict=True):
            if cell is None:
                raise ValueError("null helper provenance key")
            _value(relation.column(key).type.pg_type, cell)
        for evidence in _array(member["evidence_refs"]):
            _ref(evidence)
        if member["parent_fact_ref"] is not None:
            parent = _object(member["parent_fact_ref"], "result_id row_ref")
            _ref(parent["result_id"])
            _value("int8", parent["row_ref"])
        if member["member_ref"] in members:
            raise ValueError("duplicate helper provenance member")
        members[member["member_ref"]] = member
    for node in _array(graph["nodes"]):
        node = _object(node, "node_ref operation input_refs member_refs multiplicities")
        _ref(node["node_ref"])
        if node["operation"] not in _PROVENANCE_OPERATIONS:
            raise ValueError("unknown helper provenance operation")
        for ref in [*_array(node["input_refs"]), *_array(node["member_refs"])]:
            _ref(ref)
        if len(_array(node["multiplicities"])) != len(node["member_refs"]):
            raise ValueError("helper provenance bag arity")
        for count in node["multiplicities"]:
            if _value("int8", count) < 1:
                raise ValueError("invalid helper provenance multiplicity")
        if node["node_ref"] in nodes:
            raise ValueError("duplicate helper provenance node")
        nodes[node["node_ref"]] = node
    if require_complete:
        for node in nodes.values():
            if any(ref not in nodes for ref in node["input_refs"]) or any(
                ref not in members for ref in node["member_refs"]
            ):
                raise ValueError("incomplete helper provenance")


def project_helper_provenance(
    native: Any, root: str, *, check_work: Callable[[], None] | None = None
) -> dict[str, Any]:
    """Project a representable native DAG; never invent an empty witness.

    Unsupported window/recursion/negative-input detail retains an explicit
    refusal until its lossless mapping has independent evidence.
    """
    _ref(root)
    if not isinstance(native, dict) or native.get("qualification") != "native-bag-v1":
        raise UnsupportedHelperProvenance("unqualified helper provenance")
    _ref(native["population_ref"])
    _ref(native["frame_ref"])
    nodes = {node["node_ref"]: node for node in _array(native["nodes"])}
    members = {member["member_ref"]: member for member in _array(native["members"])}
    if (
        len(nodes) != len(native["nodes"])
        or len(members) != len(native["members"])
        or root not in nodes.keys() | members.keys()
    ):
        raise ValueError("helper provenance root binding")
    output_nodes = []
    needed_members = set()
    visited = set()
    visiting = set()
    encoded_inputs = 0

    def visit(ref: str, depth: int) -> None:
        nonlocal encoded_inputs
        if check_work:
            check_work()
        if ref in visited:
            return
        if ref in visiting or depth > 128 or ref not in nodes:
            raise ValueError("invalid helper provenance graph")
        visiting.add(ref)
        node = nodes[ref]
        required = {
            "node_ref",
            "stage",
            "operation",
            "input_refs",
            "member_refs",
            "multiplicities",
            "absent_inputs",
            "population_ref",
        }
        if (
            not required <= set(node)
            or set(node) - required - {"input_multiplicities"}
            or node["absent_inputs"]
            or node["operation"] not in _PROVENANCE_OPERATIONS
        ):
            raise UnsupportedHelperProvenance("unrepresented helper witness detail")
        if node["population_ref"] != native["population_ref"]:
            raise ValueError("helper provenance population binding")
        inputs = _array(node["input_refs"])
        counts = node.get("input_multiplicities", [1] * len(inputs))
        if len(_array(counts)) != len(inputs) or any(
            type(n) is not int or n < 1 for n in counts
        ):
            raise ValueError("invalid helper input multiplicities")
        encoded_inputs += sum(counts) * 39
        if encoded_inputs > 67108864:
            raise UnsupportedHelperProvenance(
                "helper witness expansion exceeds artifact limit"
            )
        for child in inputs:
            visit(child, depth + 1)
        for member in _array(node["member_refs"]):
            if (
                member not in members
                or members[member]["frame_ref"] != native["frame_ref"]
            ):
                raise ValueError("helper provenance member binding")
            needed_members.add(member)
        counts_out = []
        for count in _array(node["multiplicities"]):
            if type(count) is not int or count < 1:
                raise ValueError("invalid helper native multiplicity")
            counts_out.append(str(count))
        output_nodes.append(
            {
                "node_ref": ref,
                "operation": node["operation"],
                "input_refs": [
                    child
                    for child, count in zip(inputs, counts, strict=True)
                    for _ in range(count)
                ],
                "member_refs": node["member_refs"],
                "multiplicities": counts_out,
            }
        )
        visiting.remove(ref)
        visited.add(ref)

    if root in nodes:
        visit(root, 0)
    else:
        if check_work:
            check_work()
        if members[root]["frame_ref"] != native["frame_ref"]:
            raise ValueError("helper provenance member binding")
        needed_members.add(root)
    graph = {
        "population_ref": native["population_ref"],
        "nodes": output_nodes,
        "members": [
            {**members[ref], "parent_fact_ref": None} for ref in sorted(needed_members)
        ],
        "next_cursor": None,
        "has_more": False,
    }
    _provenance(graph, require_complete=True)
    return graph


def _inspection(
    request: InspectRequest, payload: Any, *, require_complete: bool = True
) -> list[dict[str, Any]]:
    value = _object(
        payload,
        "observations corrections source_units result relations relation_types relation_pairs evidence_pins provenance",
    )
    for facet, fields in (
        ("content", ("observations", "relations", "relation_types")),
        ("lifecycle", ("corrections", "relation_pairs")),
        ("evidence_metadata", ("source_units", "evidence_pins")),
    ):
        if facet not in request.facets and any(
            _array(value[field]) for field in fields
        ):
            raise ValueError("unrequested helper facet")
    if "provenance" not in request.facets and value["provenance"] is not None:
        raise ValueError("unrequested helper provenance")
    catalog = SqlCatalog.installed()
    refs: list[dict[str, Any]] = []
    for inspection in _array(value["observations"]):
        inspection = _object(
            inspection,
            "observation statements render type authorship mentions entities topics",
        )
        observation = _row(catalog, "observations", inspection["observation"])
        refs.append(
            _observation_ref(observation["bead_id"], observation["bead_version_id"])
        )
        for statement in _array(inspection["statements"]):
            statement = _row(catalog, "statements", statement)
            if (
                statement["bead_id"] != observation["bead_id"]
                or statement["bead_version_id"] != observation["bead_version_id"]
            ):
                raise ValueError("helper statement version mismatch")
            refs.append(
                {
                    "kind": "statement",
                    "ref": statement["statement_id"],
                    "version_ref": statement["bead_version_id"],
                }
            )
        type_pin = _object(inspection["type"], "key revision_id definition")
        if (type_pin["key"], type_pin["revision_id"]) != (
            observation["bead_type_key"],
            observation["bead_type_revision_id"],
        ):
            raise ValueError("helper observation type binding")
        _text(type_pin["definition"])
        authorship = _object(
            inspection["authorship"],
            "receipt_id task_id attempt_id run_ref task_contract_key task_contract_version",
        )
        for key in ("receipt_id", "task_id", "attempt_id"):
            _ref(authorship[key])
        if not isinstance(authorship["run_ref"], str) or not authorship["run_ref"]:
            raise ValueError("helper authorship run mismatch")
        _text(authorship["task_contract_key"])
        _positive_control(authorship["task_contract_version"])
        if observation["render_state"] not in {"present", "unsupported"} or (
            inspection["render"] is None
        ) != (observation["render_state"] == "unsupported"):
            raise ValueError("helper render state binding")
        if inspection["render"] is not None:
            render = _object(
                inspection["render"], "revision title summary detail omissions"
            )
            _positive_control(render["revision"])
            statement_ids = {row["statement_id"] for row in inspection["statements"]}
            clauses = [
                render["title"],
                *_array(render["summary"]),
                *_array(render["detail"]),
            ]
            for clause in clauses:
                clause = _object(clause, "text statement_ids")
                _text(clause["text"])
                for ref in _array(clause["statement_ids"]):
                    _ref(ref)
                    if ref not in statement_ids:
                        raise ValueError("helper render statement binding")
            for omission in _array(render["omissions"]):
                omission = _object(omission, "statement_id reason")
                _ref(omission["statement_id"])
                _text(omission["reason"])
                if omission["statement_id"] not in statement_ids:
                    raise ValueError("helper render omission binding")
            if render["title"]["text"] != observation["title"]:
                raise ValueError("helper render title binding")
        for field, relation in (
            ("mentions", "entity_mentions"),
            ("entities", "entities"),
            ("topics", "topics"),
        ):
            for row in _array(inspection[field]):
                _row(catalog, relation, row)
    for row in _array(value["corrections"]):
        correction = _row(catalog, "corrections", row)
        for side in ("predecessor", "successor"):
            refs.append(
                _observation_ref(
                    correction[side + "_bead_id"], correction[side + "_version_id"]
                )
            )
    for row in _array(value["source_units"]):
        _row(catalog, "source_units", row)
    for pin in _array(value["evidence_pins"]):
        refs.append(_source_ref(_pin(pin)))
    for relation in _array(value["relations"]):
        # The normative lifecycle dependency defines the complete v3 shape.
        # Convert only for validation; preserve decimal/int8 wire strings.
        checked = dict(relation)
        checked["author_confidence"] = float(
            _value("numeric", relation["author_confidence"])
        )
        if relation["independent_root_count"] is not None:
            checked["independent_root_count"] = _value(
                "int8", relation["independent_root_count"]
            )
        checked["events"] = [
            dict(event, event_number=_value("int8", event["event_number"]))
            for event in relation["events"]
        ]
        ProjectedAssertion.model_validate_json(json.dumps(checked))
        for side in ("source", "target"):
            refs.append(
                _observation_ref(
                    relation[side + "_bead_id"], relation[side + "_bead_version_id"]
                )
            )
        for field in ("source_statements", "target_statements", "basis_statements"):
            for statement in relation[field]:
                refs.append(
                    _observation_ref(statement["bead_id"], statement["bead_version_id"])
                )
                refs.append(
                    {
                        "kind": "statement",
                        "ref": statement["statement_id"],
                        "version_ref": statement["bead_version_id"],
                    }
                )
    for row in _array(value["relation_types"]):
        RelationTypeDefinition.model_validate_json(json.dumps(row))
    for row in _array(value["relation_pairs"]):
        InspectedPairDisposition.model_validate_json(json.dumps(row))
        for side in ("first", "second"):
            refs.append(
                _observation_ref(row[side + "_bead_id"], row[side + "_bead_version_id"])
            )
    if value["result"] is not None:
        _result_metadata(request, value["result"])
    elif request.result is not None:
        raise ValueError("helper omitted result target")
    if value["provenance"] is not None:
        _provenance(value["provenance"], require_complete=require_complete)
    if (
        require_complete
        and request.provenance_ref is not None
        and "provenance" in request.facets
    ):
        if value["provenance"] is None or str(request.provenance_ref) not in (
            {node["node_ref"] for node in value["provenance"]["nodes"]}
            | {member["member_ref"] for member in value["provenance"]["members"]}
        ):
            raise ValueError("helper omitted requested provenance root")
    if require_complete and "content" in request.facets:
        requested = {
            (row.bead_id, row.bead_version_id) for row in request.observation_refs
        }
        returned = {
            (row["observation"]["bead_id"], row["observation"]["bead_version_id"])
            for row in value["observations"]
        }
        if not requested <= returned:
            raise ValueError("helper inspection omitted a target")
    return refs


def validate_helper_content(
    request: InspectRequest | HydrateRequest,
    content: Any,
    *,
    require_complete: bool = True,
    selection_offset: int = 0,
) -> list[dict[str, Any]]:
    """Return only refs actually disclosed by a closed, correctly bound payload."""
    body = _object(content, "kind payload refs access_bases")
    if body["kind"] != request.kind:
        raise ValueError("helper kind mismatch")
    if isinstance(request, InspectRequest):
        refs = _inspection(
            request,
            _object(body["payload"], "inspection")["inspection"],
            require_complete=require_complete,
        )
    else:
        hydration = _object(
            _object(body["payload"], "hydration")["hydration"],
            "slices selection_complete",
        )
        slices = _array(hydration["slices"])
        if type(hydration["selection_complete"]) is not bool or selection_offset < 0:
            raise ValueError("invalid helper selection control")
        if require_complete and (
            hydration["selection_complete"] is not True
            or len(slices) != len(request.selections)
        ):
            raise ValueError("helper hydration omitted a selection")
        if selection_offset + len(slices) > len(request.selections):
            raise ValueError("helper selection exceeds its request")
        refs = []
        amount = 0
        for selection, item in zip(
            request.selections[selection_offset : selection_offset + len(slices)],
            slices,
            strict=True,
        ):
            item = _object(
                item,
                "evidence_pin representation start end content bytes_base64 returned_sha256",
            )
            pin = _pin(item["evidence_pin"])
            if (
                pin["ref"],
                pin["part"]["part_id"],
                item["representation"],
                item["start"],
                item["end"],
            ) != (
                selection.evidence_ref,
                selection.part_id,
                selection.representation,
                selection.start,
                selection.end,
            ):
                raise ValueError("helper hydration selection mismatch")
            if selection.representation == "normalized_text":
                if (
                    not isinstance(item["content"], str)
                    or item["bytes_base64"] is not None
                    or len(item["content"]) != selection.end - selection.start
                ):
                    raise ValueError("helper normalized interval mismatch")
                raw = item["content"].encode("utf-8")
            else:
                if item["content"] is not None or not isinstance(
                    item["bytes_base64"], str
                ):
                    raise ValueError("helper raw representation mismatch")
                raw = base64.b64decode(item["bytes_base64"], validate=True)
                if len(raw) != selection.end - selection.start:
                    raise ValueError("helper raw interval mismatch")
            if len(raw) > HYDRATION_SPAN_BYTES:
                raise ValueError("helper hydration span allocation exceeded")
            if hashlib.sha256(raw).hexdigest() != item["returned_sha256"]:
                raise ValueError("helper span digest mismatch")
            amount += len(raw)
            refs.append(_source_ref(pin))
        if amount > request.max_bytes:
            raise ValueError("helper hydration byte allocation exceeded")
    bases = _array(body["access_bases"])
    for basis in bases:
        basis = _object(basis, "context checked_at valid_until end_condition hold_ref")
        context = _ACCESS_CONTEXT.validate_json(json.dumps(basis["context"]))
        _clock(basis["checked_at"])
        _clock(basis["valid_until"], nullable=True)
        if context.kind == "standalone":
            if (
                basis["end_condition"] != "standalone_expiry"
                or basis["valid_until"] is None
                or basis["hold_ref"] is not None
            ):
                raise ValueError("invalid helper standalone basis")
        else:
            if basis["end_condition"] not in {
                "checkpoint_expiry_or_prune",
                "save_release",
            } or (basis["valid_until"] is None) != (
                basis["end_condition"] == "save_release"
            ):
                raise ValueError("invalid helper retained basis")
            _ref(basis["hold_ref"])
    if (
        isinstance(request, InspectRequest)
        and request.result is not None
        and not any(
            basis["context"] == request.result.access.model_dump(mode="json")
            for basis in bases
        )
    ):
        raise ValueError("helper omitted selected access basis")
    unique = {result_json_bytes(ref): ref for ref in refs}
    return [unique[key] for key in sorted(unique)]


_INSPECTION_LISTS = (
    "observations",
    "corrections",
    "source_units",
    "relations",
    "relation_types",
    "relation_pairs",
    "evidence_pins",
)


def helper_item_count(
    request: InspectRequest | HydrateRequest, content: dict[str, Any]
) -> int:
    if isinstance(request, HydrateRequest):
        return len(content["payload"]["hydration"]["slices"])
    inspection = content["payload"]["inspection"]
    count = sum(len(inspection[key]) for key in _INSPECTION_LISTS)
    if inspection["provenance"] is not None:
        count += len(inspection["provenance"]["nodes"]) + len(
            inspection["provenance"]["members"]
        )
    return count


def helper_content_page(
    request: InspectRequest | HydrateRequest,
    content: dict[str, Any],
    *,
    first: int,
    count: int,
    cursor: str | None,
) -> dict[str, Any]:
    """Page only complete typed objects; preserve their identities and order."""
    total = helper_item_count(request, content)
    if (
        type(first) is not int
        or type(count) is not int
        or first < 1
        or count < 0
        or first - 1 + count > total
    ):
        raise ValueError("invalid helper page binding")
    if isinstance(request, HydrateRequest):
        original = content["payload"]["hydration"]
        payload = {
            "hydration": {
                "slices": original["slices"][first - 1 : first - 1 + count],
                "selection_complete": first - 1 + count == total,
            }
        }
    else:
        original = content["payload"]["inspection"]
        inspection = dict(original)
        atoms = [(key, item) for key in _INSPECTION_LISTS for item in original[key]]
        for key in _INSPECTION_LISTS:
            inspection[key] = []
        if original["provenance"] is not None:
            graph = original["provenance"]
            inspection["provenance"] = {
                **graph,
                "nodes": [],
                "members": [],
                "next_cursor": cursor,
                "has_more": cursor is not None,
            }
            atoms.extend(("nodes", item) for item in graph["nodes"])
            atoms.extend(("members", item) for item in graph["members"])
        for key, item in atoms[first - 1 : first - 1 + count]:
            if key in ("nodes", "members"):
                inspection["provenance"][key].append(item)
            else:
                inspection[key].append(item)
        payload = {"inspection": inspection}
    page = {**content, "payload": payload, "refs": []}
    page["refs"] = validate_helper_content(
        request,
        page,
        require_complete=False,
        selection_offset=first - 1 if isinstance(request, HydrateRequest) else 0,
    )
    return page
