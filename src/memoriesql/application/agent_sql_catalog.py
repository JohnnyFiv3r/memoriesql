"""Typed admission data, loaded only from the installed registry-generated catalog."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from memoriesql.contracts import load_catalog


class SqlAdmissionError(ValueError):
    """A content-free refusal; never include query text or bound values."""

    def __init__(self, code: str, construct: str, position: int | None = None) -> None:
        self.code = code
        self.construct = construct
        self.position = position
        super().__init__(f"{code}: {construct}")


@dataclass(frozen=True)
class SqlType:
    pg_type: str
    reference_kind: str | None = None
    nullable: bool = False
    array: bool = False

    def optional(self) -> SqlType:
        return replace(self, nullable=True)

    def same_value_type(self, other: SqlType) -> bool:
        return (self.pg_type, self.reference_kind, self.array) == (
            other.pg_type,
            other.reference_kind,
            other.array,
        )


@dataclass(frozen=True)
class SqlColumn:
    name: str
    type: SqlType


@dataclass(frozen=True)
class SqlRelation:
    columns: tuple[SqlColumn, ...]
    unique_keys: tuple[tuple[str, ...], ...] = ()

    def column(self, name: str) -> SqlColumn:
        for column in self.columns:
            if column.name == name:
                return column
        raise SqlAdmissionError("invalid_request", "unknown_column")


@dataclass(frozen=True)
class SqlParameter:
    position: int
    type: str
    value: Any


@dataclass(frozen=True)
class BoundParameter:
    position: int
    type: SqlType
    value: Any


def _relation(raw: dict[str, Any]) -> SqlRelation:
    columns = tuple(
        SqlColumn(
            c["name"],
            SqlType(c["pg_type"], c["reference_kind"], c["nullable"]),
        )
        for c in raw["columns"]
    )
    return SqlRelation(columns, (tuple(raw["keys"]),))


@dataclass(frozen=True)
class SqlCatalog:
    hash: str
    relations: dict[str, SqlRelation]
    reference_types: dict[str, str]
    collation: str

    @classmethod
    def installed(cls) -> SqlCatalog:
        raw = load_catalog("sql-recall-operations").get("logical_catalog")
        if not isinstance(raw, dict) or raw.get("revision") != 1:
            raise SqlAdmissionError("unavailable", "catalog")
        encoded = json.dumps(
            raw, sort_keys=True, ensure_ascii=True, separators=(",", ":")
        ).encode()
        relations = {
            "memory_v1." + name: _relation(value)
            for name, value in raw["relations"].items()
        }
        evaluation = raw["evaluation_relation"]
        relations["evaluation_v1.candidates"] = _relation(evaluation)
        references: dict[str, str] = {}
        for relation in relations.values():
            for column in relation.columns:
                if column.type.reference_kind:
                    kind = column.type.reference_kind
                    prior = references.setdefault(kind, column.type.pg_type)
                    if prior != column.type.pg_type:
                        raise SqlAdmissionError("unavailable", "catalog_reference")
        return cls(
            hashlib.sha256(encoded).hexdigest(),
            relations,
            references,
            raw["collation"],
        )

    def parameters(
        self,
        parameters: tuple[SqlParameter, ...],
        *,
        admitted_anchors: frozenset[tuple[str, str]] = frozenset(),
    ) -> dict[int, BoundParameter]:
        """Anchors come from trusted current-authorization bookkeeping, never wire input."""
        if len(parameters) > 64:
            raise SqlAdmissionError("invalid_request", "parameter_count")
        result: dict[int, BoundParameter] = {}
        size = 0
        scalars = {"uuid", "text", "bool", "int8", "numeric", "float8", "timestamptz"}
        for parameter in parameters:
            position = parameter.position
            if (
                type(position) is not int
                or not 1 <= position <= 64
                or position in result
            ):
                raise SqlAdmissionError("invalid_request", "parameter_position")
            array = parameter.type.endswith("[]")
            name = parameter.type[:-2] if array else parameter.type
            reference = name if name in self.reference_types else None
            physical = self.reference_types[name] if reference else name
            if physical not in scalars:
                raise SqlAdmissionError("invalid_request", "parameter_type")
            try:
                size += len(json.dumps(parameter.value, ensure_ascii=True).encode())
            except (TypeError, ValueError, UnicodeError):
                raise SqlAdmissionError("invalid_request", "parameter_value") from None
            if size > 65536:
                raise SqlAdmissionError("invalid_request", "parameter_bytes")
            if array:
                if (
                    not isinstance(parameter.value, tuple | list)
                    or len(parameter.value) > 64
                ):
                    raise SqlAdmissionError("invalid_request", "parameter_array")
                if any(v is None for v in parameter.value):
                    raise SqlAdmissionError("invalid_request", "parameter_array_null")
                values = parameter.value
            else:
                values = (parameter.value,)
            converted = []
            for value in values:
                converted.append(_value(physical, value))
                if reference and value is not None:
                    anchor_value = str(converted[-1])
                    if (reference, anchor_value) not in admitted_anchors:
                        raise SqlAdmissionError("unavailable", "parameter_anchor")
            result[position] = BoundParameter(
                position,
                SqlType(physical, reference, parameter.value is None, array),
                converted if array else converted[0],
            )
        return result


def _value(type_name: str, value: Any) -> Any:
    if value is None:
        return None
    try:
        if type_name == "bool" and type(value) is bool:
            return value
        if type_name == "text" and isinstance(value, str):
            value.encode("utf-8")
            if "\x00" in value:
                raise ValueError
            return value
        if type_name == "uuid" and isinstance(value, str):
            result = UUID(value)
            if str(result) != value:
                raise ValueError
            return result
        if type_name == "int8" and isinstance(value, str):
            if not re.fullmatch(r"-?(0|[1-9][0-9]*)", value):
                raise ValueError
            integer = int(value)
            if -(2**63) <= integer < 2**63:
                return integer
        if type_name == "numeric" and isinstance(value, str):
            number = Decimal(value)
            if (
                number.is_finite()
                and -65536 <= int(number.as_tuple().exponent) <= 65536
            ):
                return number
        if type_name == "float8" and isinstance(value, str):
            floating = float.fromhex(value)
            if math.isfinite(floating) and floating.hex() == value:
                return floating
        if (
            type_name == "timestamptz"
            and isinstance(value, str)
            and re.fullmatch(
                r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?Z",
                value,
            )
        ):
            timestamp = datetime.fromisoformat(value[:-1] + "+00:00")
            if timestamp.utcoffset() is not None:
                return timestamp
    except (ValueError, TypeError, UnicodeError, InvalidOperation, OverflowError):
        pass
    raise SqlAdmissionError("invalid_request", "parameter_value")
