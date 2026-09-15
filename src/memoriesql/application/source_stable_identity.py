"""Explicit qualified source-stable identity; no revision or content inference."""

from typing import Literal

from pydantic import model_validator

from memoriesql.application.logical_unit_materialization import (
    LogicalUnitMaterializationReceipt,
    MaterializeLogicalUnit,
)


class MaterializeSourceStableUnit(MaterializeLogicalUnit):
    contract_version: Literal[2] = 2  # type: ignore[assignment]
    expected_schema_version: Literal[21] = 21  # type: ignore[assignment]

    @model_validator(mode="after")
    def qualified_identity(self) -> "MaterializeSourceStableUnit":
        if self.event.identity_basis != "native":
            raise ValueError("source-stable events require qualified native identity")
        return self


class SourceStableMaterializationReceipt(LogicalUnitMaterializationReceipt):
    contract_version: Literal[2] = 2  # type: ignore[assignment]
