"""Core-owned, provider-neutral memoriesQL command line.

Read commands use installed typed core contracts and their authorization-aware
Postgres adapters. They never infer identity from a terminal or import Desktop.
"""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
from psycopg.errors import (
    InsufficientPrivilege,
    InvalidAuthorizationSpecification,
    NoDataFound,
)
from pydantic import BaseModel

from memoriesql import __version__
from memoriesql.application.authorization import LocalCredential
from memoriesql.application.relation_inspection import InspectBeadRelationsV2
from memoriesql.application.stored_bead_inspection import (
    InspectStoredBead,
    ReadStoredBeadEvidence,
    StoredEvidenceSelection,
)
from memoriesql.contracts import (
    CATALOG_KINDS,
    ContractNotFoundError,
    contract_inventory,
    get_contract,
)
from memoriesql.infrastructure.postgres.relation_assessment import (
    PostgresRelationAssessments,
)
from memoriesql.infrastructure.postgres.stored_bead_inspection import (
    PostgresStoredBeadInspection,
)


def _write_json(value: object, *, pretty: bool = True) -> None:
    print(
        json.dumps(
            value, ensure_ascii=True, indent=2 if pretty else None, sort_keys=True
        )
    )


def _aware_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise argparse.ArgumentTypeError("expected an ISO 8601 timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("timestamp must include a UTC offset")
    return parsed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="memoriesql",
        description="Inspect memoriesQL public contracts and authorized core records.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)

    contracts = commands.add_parser(
        "contracts", help="List bundled public contract catalogs and entries."
    )
    contracts.add_argument(
        "--kind",
        choices=CATALOG_KINDS,
        help="Limit output to one catalog kind.",
    )
    contracts.add_argument(
        "--status", help="Limit entries to one exact generated status."
    )
    contracts.add_argument(
        "--json", action="store_true", help="Emit stable machine-readable JSON."
    )

    contract = commands.add_parser(
        "contract", help="Show one exact contract entry by identifier."
    )
    contract.add_argument("identifier")
    contract.add_argument(
        "--json", action="store_true", help="Emit stable machine-readable JSON."
    )

    doctor = commands.add_parser(
        "doctor", help="Show core and local read configuration without connecting."
    )
    doctor.add_argument("--json", action="store_true")

    capabilities = commands.add_parser(
        "capabilities", help="Show this artifact's static command availability."
    )
    capabilities.add_argument("--json", action="store_true")

    inspect = commands.add_parser("inspect", help="Read one authorized stored bead.")
    inspect.add_argument("bead_id", type=UUID)
    inspect.add_argument("--json", action="store_true")

    source = commands.add_parser(
        "source", help="Read one exact authorized source selection for a bead."
    )
    source.add_argument("bead_id", type=UUID)
    source.add_argument("--selection-file", required=True, type=Path)
    source.add_argument("--json", action="store_true")

    relations = commands.add_parser(
        "relations", help="Read authorized relations for one bead."
    )
    relations.add_argument("bead_id", type=UUID)
    relations.add_argument("--known-at", type=_aware_time)
    relations.add_argument("--json", action="store_true")
    return parser


def _local_read_configuration(
    environment: Mapping[str, str],
) -> tuple[str, str, UUID] | None:
    database = environment.get("MEMORIESQL_DATABASE_URL")
    secret = environment.get("MEMORIESQL_LOCAL_CREDENTIAL")
    workspace = environment.get("MEMORIESQL_WORKSPACE_ID")
    if not database or not secret or not workspace:
        return None
    try:
        return database, LocalCredential(secret).sha256(), UUID(workspace)
    except ValueError:
        return None


def _read_core(
    command: str, request: BaseModel, environment: Mapping[str, str]
) -> BaseModel | dict[str, str]:
    configured = _local_read_configuration(environment)
    if configured is None:
        return {"outcome": "unavailable", "reason": "local_read_identity_required"}
    database, credential_sha256, workspace_id = configured
    try:
        with psycopg.connect(database, autocommit=True) as connection:
            if command == "relations":
                return PostgresRelationAssessments(
                    connection,
                    credential_sha256=credential_sha256,
                    workspace_id=workspace_id,
                ).inspect_relations(InspectBeadRelationsV2.model_validate(request))
            reader = PostgresStoredBeadInspection(
                connection,
                credential_sha256=credential_sha256,
                workspace_id=workspace_id,
            )
            if command == "inspect":
                return reader.inspect(InspectStoredBead.model_validate(request))
            return reader.read(ReadStoredBeadEvidence.model_validate(request))
    except (
        PermissionError,
        InsufficientPrivilege,
        InvalidAuthorizationSpecification,
        NoDataFound,
    ):
        return {"outcome": "unavailable", "reason": "resource_unavailable"}
    except Exception:
        # A failure is never a successful empty result. Hide connection strings,
        # secrets, protected IDs, SQL diagnostics and partial content.
        return {"outcome": "failed", "reason": "core_read_failed"}


def _selection(path: Path) -> StoredEvidenceSelection:
    with path.open("rb") as file:
        data = file.read(8193)
    if len(data) > 8192:
        raise ValueError("source selection exceeds 8192 bytes")
    return StoredEvidenceSelection.model_validate_json(data)


def _emit_result(result: BaseModel | dict[str, Any], *, machine: bool) -> int:
    payload = (
        result.model_dump(mode="json") if isinstance(result, BaseModel) else result
    )
    _write_json(payload, pretty=not machine)
    outcome = payload.get("outcome")
    if outcome == "available":
        return 0
    return 2 if outcome == "unavailable" else 3


def main(arguments: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(arguments)
    if args.command == "contracts":
        inventory = contract_inventory(kind=args.kind, status=args.status)
        if args.json:
            _write_json(inventory)
        else:
            print("memoriesQL experimental contract-catalog preview")
            catalogs = inventory.get("catalogs")
            if not isinstance(catalogs, list):
                raise RuntimeError("generated catalog inventory is malformed")
            for catalog in catalogs:
                if not isinstance(catalog, dict):
                    continue
                entries = catalog.get("entries")
                entry_count = len(entries) if isinstance(entries, list) else 0
                print(
                    f"{catalog.get('kind')}: {catalog.get('status')} "
                    f"({entry_count} entries)"
                )
        return 0

    if args.command == "contract":
        try:
            entry = get_contract(args.identifier)
        except ContractNotFoundError as error:
            _parser().error(str(error))
        if args.json:
            _write_json(entry)
        else:
            print(f"{entry['id']} {entry['version']} [{entry['status']}]")
            print(entry["summary"])
        return 0

    if args.command == "doctor":
        return _emit_result(
            {
                "outcome": "available",
                "core_version": __version__,
                "configuration_only": True,
                "local_read_identity_configured": _local_read_configuration(os.environ)
                is not None,
                "database_contacted": False,
                "model_contacted": False,
            },
            machine=args.json,
        )

    if args.command == "capabilities":
        reference = get_contract("memoriesql.core-cli.v1")
        command_contract = reference["contract"]
        if not isinstance(command_contract, dict):
            raise RuntimeError("bundled CLI reference is malformed")
        commands = command_contract.get("core_commands")
        unavailable = command_contract.get("unavailable")
        if not isinstance(commands, list) or not isinstance(unavailable, dict):
            raise RuntimeError("bundled CLI capabilities are malformed")
        return _emit_result(
            {
                "outcome": "available",
                "reference": reference["id"],
                "reference_version": reference["version"],
                "core_commands": commands,
                "unavailable": unavailable,
            },
            machine=args.json,
        )

    request: BaseModel
    if args.command == "source":
        try:
            request = ReadStoredBeadEvidence(
                bead_id=args.bead_id, selection=_selection(args.selection_file)
            )
        except (OSError, ValueError):
            return _emit_result(
                {"outcome": "failed", "reason": "invalid_source_selection"},
                machine=args.json,
            )
    elif args.command == "inspect":
        request = InspectStoredBead(bead_id=args.bead_id)
    else:
        request = InspectBeadRelationsV2(bead_id=args.bead_id, known_at=args.known_at)
    return _emit_result(
        _read_core(args.command, request, os.environ), machine=args.json
    )


if __name__ == "__main__":
    raise SystemExit(main())
