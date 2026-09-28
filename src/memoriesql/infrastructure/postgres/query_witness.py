"""Bind native bag membership to exact frozen logical keys, privately.

An empty query retains its searched population and predicate program. A missing
outer partner is recorded as absent, never an invented source/member. Nodes are
shared by identity; duplicate output occurrences retain separate row ordinals.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid5

from memoriesql.application.agent_sql_witness import WitnessPlan
from memoriesql.application.investigation_contracts import (
    encode_result_scalar,
    result_json_bytes,
)
from memoriesql.infrastructure.postgres.relation_sql_population import (
    PreparedRelationPopulation,
)


@dataclass(frozen=True, slots=True)
class NativeWitnesses:
    frame_ref: UUID
    bytes: bytes
    row_provenance: tuple[UUID, ...]
    program_sha256: str


class NativeWitnessBuilder:
    def __init__(
        self,
        plan: WitnessPlan,
        population: PreparedRelationPopulation,
        frame_ref: UUID,
        *,
        check_work: Callable[[], None] | None = None,
    ) -> None:
        self.plan = plan
        self.frame_ref = frame_ref
        self.check_work = check_work
        self.members: dict[tuple[str, bytes], UUID] = {}
        members: list[dict[str, Any]] = []
        used = sorted(
            {
                s["relation"]
                for s in plan.stages
                if s["operation"] == "scan" and s.get("reachable", True)
            }
        )
        for name in used:
            schema = population.schemas[name]
            keys = schema.unique_keys[0]
            indices = [
                next(i for i, c in enumerate(schema.columns) if c.name == k)
                for k in keys
            ]
            for row in population.rows[name]:
                if check_work:
                    check_work()
                values = [encode_result_scalar(row[i]) for i in indices]
                encoded = result_json_bytes(values)
                ref = uuid5(frame_ref, name + ":" + encoded.decode("ascii"))
                if (name, encoded) in self.members:
                    raise ValueError("duplicate frozen logical key")
                self.members[name, encoded] = ref
                evidence = [
                    str(v)
                    for c, v in zip(schema.columns, row, strict=True)
                    if c.name == "evidence_ref" and v is not None
                ]
                members.append(
                    {
                        "member_ref": str(ref),
                        "relation": name,
                        "key_values": values,
                        "frame_ref": str(frame_ref),
                        "evidence_refs": evidence,
                    }
                )
        self.population_ref = uuid5(
            frame_ref, "searched-population:" + population.dependency_manifest_sha256
        )
        self.program_sha256 = hashlib.sha256(result_json_bytes(plan.stages)).hexdigest()
        self.base: dict[str, Any] = {
            "version": 1,
            "qualification": "native-bag-v1",
            "frame_ref": str(frame_ref),
            "population_ref": str(self.population_ref),
            "projection_manifest_sha256": population.dependency_manifest_sha256,
            "searched_relations": used,
            "stages": plan.stages,
            "members": members,
        }
        self.nodes: dict[UUID, dict[str, Any]] = {}
        self.row_refs: list[UUID] = []
        self.tested_refs: list[UUID] = []
        self.cache: dict[bytes, UUID] = {}
        self.partitions: dict[
            tuple[int, int],
            tuple[UUID, list[str], list[int], dict[int, tuple[int, int]]],
        ] = {}
        self.encoded_bytes = len(result_json_bytes(self.base))

    def add(self, trace: Any, *, published: bool = True) -> UUID:
        def bind(value: Any, depth: int) -> UUID:
            if self.check_work:
                self.check_work()
            if (
                depth > 128
                or not isinstance(value, list)
                or not value
                or type(value[0]) is not int
                or not 0 <= value[0] < len(self.plan.stages)
            ):
                raise ValueError("invalid native witness")
            stage = self.plan.stages[value[0]]
            cache_key = result_json_bytes(value)
            if cache_key in self.cache:
                return self.cache[cache_key]
            member_refs: list[str] = []
            inputs: list[str] = []
            absent: list[int] = []
            details: dict[str, Any] = {}
            if stage["operation"] == "scan":
                if (
                    len(value) != 2
                    or not isinstance(value[1], list)
                    or len(value[1]) != len(stage["key_columns"])
                ):
                    raise ValueError("invalid native scan witness")
                key = result_json_bytes([encode_result_scalar(v) for v in value[1]])
                member = self.members.get((stage["relation"], key))
                if member is None:
                    raise ValueError("unknown frozen logical key")
                member_refs.append(str(member))
            elif stage["operation"] == "window_partition":
                if (
                    len(value) != 3
                    or not isinstance(value[1], list)
                    or type(value[2]) is not int
                    or value[2] < 1
                ):
                    raise ValueError("invalid native window partition")
                ordered_refs: list[str] = []
                peer_ranks: list[int] = []
                peer_spans: dict[int, tuple[int, int]] = {}
                for position, entry in enumerate(value[1], start=1):
                    if (
                        not isinstance(entry, list)
                        or len(entry) != 3
                        or type(entry[0]) is not int
                        or entry[0] != position
                        or type(entry[1]) is not int
                        or entry[1] < 1
                        or entry[1] > (peer_ranks[-1] + 1 if peer_ranks else 1)
                        or (peer_ranks and entry[1] < peer_ranks[-1])
                    ):
                        raise ValueError("invalid native window order")
                    child = str(bind(entry[2], depth + 1))
                    ordered_refs.append(child)
                    peer_ranks.append(entry[1])
                    old = peer_spans.get(entry[1])
                    peer_spans[entry[1]] = (old[0] if old else position, position)
                if not ordered_refs:
                    raise ValueError("empty native window partition")
                multiplicities = Counter(ordered_refs)
                inputs = list(multiplicities)
                details.update(
                    partition_id=value[2],
                    ordered_input_refs=ordered_refs,
                    input_multiplicities=list(multiplicities.values()),
                    peer_spans=[
                        {
                            "rank": rank,
                            "start_ordinal": bounds[0],
                            "end_ordinal": bounds[1],
                        }
                        for rank, bounds in peer_spans.items()
                    ],
                )
            elif stage["operation"] == "window":
                if len(value) != 6 or any(
                    type(v) is not int or v < 1 for v in value[2:5]
                ):
                    raise ValueError("invalid native window row")
                partition = self.partitions.get((stage["partition_stage"], value[2]))
                if partition is None:
                    raise ValueError("missing native window partition")
                partition_ref, ordered_refs, peers, spans = partition
                ordinal = value[3]
                if ordinal > len(ordered_refs) or peers[ordinal - 1] != value[4]:
                    raise ValueError("native window peer mismatch")
                source_ref = str(bind(value[1], depth + 1))
                if source_ref != ordered_refs[ordinal - 1]:
                    raise ValueError("native window ordinal mismatch")
                inputs.extend((source_ref, str(partition_ref)))
                offset = stage.get("offset")
                if offset is None:
                    if value[5] is not None:
                        raise ValueError("unexpected native window neighbor")
                else:
                    target = ordinal + (offset if stage["kind"] == "Lead" else -offset)
                    if 1 <= target <= len(ordered_refs):
                        if value[5] is None:
                            raise ValueError("missing native window neighbor")
                        neighbor_ref = str(bind(value[5], depth + 1))
                        if neighbor_ref != ordered_refs[target - 1]:
                            raise ValueError("native window neighbor mismatch")
                        inputs.append(neighbor_ref)
                        details["neighbor_ref"] = neighbor_ref
                    elif value[5] is not None:
                        raise ValueError("invented native window neighbor")
                    details["neighbor_exists"] = 1 <= target <= len(ordered_refs)
                details.update(
                    partition_ref=str(partition_ref),
                    partition_id=value[2],
                    ordinal=ordinal,
                    peer_dense_rank=value[4],
                    peer_span=list(spans[value[4]]),
                )
            elif stage["operation"] in {
                "group",
                "collapse",
                "set_class",
                "set_population",
            }:
                expected = (
                    2
                    if stage["operation"] == "collapse"
                    else 7
                    if stage["operation"] == "group"
                    else 5
                )
                if len(value) != expected or not isinstance(value[1], list):
                    raise ValueError("invalid native equivalence witness")
                left = [str(bind(v, depth + 1)) for v in value[1]]
                left_counts = Counter(left)
                inputs.extend(left)
                if stage["operation"] == "group":
                    if (
                        not isinstance(value[2], list)
                        or len(value[2]) != len(stage["aggregates"])
                        or type(value[3]) not in {bool, type(None)}
                    ):
                        raise ValueError("invalid native group witness")
                    details["having_truth"] = value[3]
                    details["projection_evaluated"] = value[3] is True
                    for field, values in (
                        ("native_group_key_values", value[5]),
                        ("native_aggregate_values", value[6]),
                    ):
                        if not isinstance(values, list) or any(
                            v is not None and not isinstance(v, str) for v in values
                        ):
                            raise ValueError("invalid native group values")
                        details[field] = values
                    aggregate_inputs = []
                    for descriptor, entries in zip(
                        stage["aggregates"], value[2], strict=True
                    ):
                        if not isinstance(entries, list) or len(entries) != len(left):
                            raise ValueError("invalid native aggregate witness")
                        bound_entries = []
                        for entry in entries:
                            if (
                                not isinstance(entry, list)
                                or len(entry) != 5
                                or type(entry[1]) not in {bool, type(None)}
                                or type(entry[2]) is not bool
                                or (
                                    entry[3] is not None
                                    and (type(entry[3]) is not int or entry[3] < 1)
                                )
                                or type(entry[4]) not in {bool, type(None)}
                            ):
                                raise ValueError("invalid native contribution witness")
                            contribution_ref = str(bind(entry[0], depth + 1))
                            if contribution_ref not in left_counts or bool(
                                descriptor["distinct_classes"]
                            ) != (entry[3] is not None):
                                raise ValueError("invalid native contribution class")
                            if bool(descriptor["extremum"]) != (entry[4] is not None):
                                raise ValueError(
                                    "invalid native extremum qualification"
                                )
                            bound_entries.append(
                                {
                                    "input_ref": contribution_ref,
                                    "filter_truth": entry[1],
                                    "argument_nonnull": entry[2],
                                    "argument_evaluated": entry[1] is True,
                                    "equality_class": str(entry[3])
                                    if entry[3] is not None
                                    else None,
                                    "extremum_winner": entry[4],
                                }
                            )
                        if (
                            Counter(e["input_ref"] for e in bound_entries)
                            != left_counts
                        ):
                            raise ValueError("incomplete native aggregate population")
                        aggregate_inputs.append(bound_entries)
                    details["aggregate_inputs"] = aggregate_inputs
                elif stage["operation"] in {"set_class", "set_population"}:
                    skipped = bool(stage.get("right_skipped_if_left_empty"))
                    if (
                        not isinstance(value[2], list)
                        or not isinstance(value[3], list)
                        or len(value[3]) != 3
                        or any(
                            not (type(n) is int and n >= 0)
                            and not (skipped and i == 1 and n is None)
                            for i, n in enumerate(value[3])
                        )
                    ):
                        raise ValueError("invalid native set counts")
                    right = [str(bind(v, depth + 1)) for v in value[2]]
                    if value[3][:2] != [len(left), None if skipped else len(right)] or (
                        skipped and (left or right or value[3][2] != 0)
                    ):
                        raise ValueError("incomplete native set population")
                    inputs.extend(right)
                    details.update(
                        left_input_refs=left,
                        right_input_refs=right,
                        left_multiplicity=value[3][0],
                        right_multiplicity=value[3][1],
                        output_multiplicity=value[3][2],
                        **({"right_evaluated": False} if skipped else {}),
                    )
                if stage["operation"] != "collapse":
                    if not isinstance(value[4], list) or any(
                        v is not None and not isinstance(v, str) for v in value[4]
                    ):
                        raise ValueError("invalid native textual values")
                    details["native_text_values"] = value[4]
                multiplicities = Counter(inputs)
                inputs = list(multiplicities)
                details["input_multiplicities"] = list(multiplicities.values())
            else:
                expected = 2 if stage["operation"] == "join" else 1
                # A source project has exactly zero or one source. The generated
                # stage pins that arity independently of each returned trace.
                expected = stage.get("arity", expected)
                if len(value) != expected + 1:
                    raise ValueError("invalid native witness arity")
                for index, child in enumerate(value[1:]):
                    if child is None:
                        absent.append(index)
                    else:
                        inputs.append(str(bind(child, depth + 1)))
            identity = result_json_bytes(
                [value[0], inputs, member_refs, absent, details]
            )
            ref = uuid5(self.frame_ref, identity.decode("ascii"))
            node = {
                "node_ref": str(ref),
                "stage": value[0],
                "operation": stage["operation"],
                "input_refs": inputs,
                "member_refs": member_refs,
                "multiplicities": [1] * len(member_refs),
                "absent_inputs": absent,
                "population_ref": str(self.population_ref),
                **details,
            }
            if ref not in self.nodes:
                self.nodes[ref] = node
                self.encoded_bytes += len(result_json_bytes(node)) + 1
            if stage["operation"] == "window_partition":
                pin = (value[0], value[2])
                recorded = self.partitions.get(pin)
                if recorded is not None and recorded[0] != ref:
                    raise ValueError("conflicting native window partition")
                self.partitions[pin] = (ref, ordered_refs, peer_ranks, peer_spans)
            self.cache[cache_key] = ref
            return ref

        ref = bind(trace, 0)
        if published:
            self.row_refs.append(ref)
        else:
            self.tested_refs.append(ref)
        self.encoded_bytes += 39
        return ref

    def seal(self) -> NativeWitnesses:
        body = result_json_bytes(
            {
                **self.base,
                "nodes": list(self.nodes.values()),
                "row_provenance": [str(r) for r in self.row_refs],
                **(
                    {
                        "composition_revision": 2,
                        "tested_nodes": [str(r) for r in self.tested_refs],
                    }
                    if self.plan.ledger_rows
                    else {}
                ),
            }
        )
        return NativeWitnesses(
            self.frame_ref, body, tuple(self.row_refs), self.program_sha256
        )
