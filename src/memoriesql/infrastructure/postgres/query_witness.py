"""Bind native bag membership to exact frozen logical keys, privately.

An empty query retains its searched population and predicate program. A missing
outer partner is recorded as absent, never an invented source/member. Nodes are
shared by identity; duplicate output occurrences retain separate row ordinals.
"""

from __future__ import annotations

import hashlib
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
        self, plan: WitnessPlan, population: PreparedRelationPopulation, frame_ref: UUID
    ) -> None:
        self.plan = plan
        self.frame_ref = frame_ref
        self.members: dict[tuple[str, bytes], UUID] = {}
        members: list[dict[str, Any]] = []
        used = sorted({s["relation"] for s in plan.stages if s["operation"] == "scan"})
        for name in used:
            schema = population.schemas[name]
            keys = schema.unique_keys[0]
            indices = [
                next(i for i, c in enumerate(schema.columns) if c.name == k)
                for k in keys
            ]
            for row in population.rows[name]:
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
        self.encoded_bytes = len(result_json_bytes(self.base))

    def add(self, trace: Any) -> UUID:
        def bind(value: Any, depth: int) -> UUID:
            if (
                depth > 128
                or not isinstance(value, list)
                or not value
                or type(value[0]) is not int
                or not 0 <= value[0] < len(self.plan.stages)
            ):
                raise ValueError("invalid native witness")
            stage = self.plan.stages[value[0]]
            member_refs: list[str] = []
            inputs: list[str] = []
            absent: list[int] = []
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
            identity = result_json_bytes([value[0], inputs, member_refs, absent])
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
            }
            if ref not in self.nodes:
                self.nodes[ref] = node
                self.encoded_bytes += len(result_json_bytes(node)) + 1
            return ref

        ref = bind(trace, 0)
        self.row_refs.append(ref)
        self.encoded_bytes += 39
        return ref

    def seal(self) -> NativeWitnesses:
        body = result_json_bytes(
            {
                **self.base,
                "nodes": list(self.nodes.values()),
                "row_provenance": [str(r) for r in self.row_refs],
            }
        )
        return NativeWitnesses(
            self.frame_ref, body, tuple(self.row_refs), self.program_sha256
        )
