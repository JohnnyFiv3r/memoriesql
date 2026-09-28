"""Core-owned, provider-neutral memoriesQL command line.

Commands use installed typed core contracts and their authorization-aware
Postgres adapters. They never infer identity from a terminal or import Desktop.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
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
from memoriesql.application.local_client_pairing import (
    LocalClientPairing,
    PairLocalClient,
    RevokeLocalClient,
)
from memoriesql.application.relation_inspection import InspectBeadRelationsV2
from memoriesql.application.source_enrollment import (
    EnrollExactSource,
    GrantExactSource,
    RevokeExactSource,
)
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
from memoriesql.infrastructure.postgres.local_client_pairing import (
    PairingRevisionConflict,
    PostgresLocalClientPairing,
)
from memoriesql.infrastructure.postgres.relation_assessment import (
    PostgresRelationAssessments,
)
from memoriesql.infrastructure.postgres.source_enrollment import (
    PostgresSourceEnrollment,
)
from memoriesql.infrastructure.postgres.stored_bead_inspection import (
    PostgresStoredBeadInspection,
)
from memoriesql.query_client import (
    MAX_PARAMETER_FILE_BYTES,
    MAX_SQL_BYTES,
    ResultsTransport,
    RunStore,
    TrustedHostTransport,
    default_state_root,
    query_request,
    read_bounded,
    reuse_request,
    schema_description,
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

    sources = commands.add_parser(
        "sources", help="Manage one explicitly selected source with current authority."
    )
    sources.add_argument("--json", action="store_true")
    source_commands = sources.add_subparsers(dest="source_command")
    for action in ("enroll", "grant", "revoke"):
        operation = source_commands.add_parser(
            action, help=f"Submit one typed exact-source {action} request."
        )
        operation.add_argument("--request-file", required=True, type=Path)
        operation.add_argument("--json", action="store_true")

    schema = commands.add_parser(
        "schema", help="Describe the installed logical query schema and its rules."
    )
    schema.add_argument("--json", action="store_true")
    query = commands.add_parser(
        "query", help="Run one caller-authored admitted SELECT in the current run."
    )
    query.add_argument("--file", required=True, type=Path)
    query.add_argument("--parameters-file", type=Path)
    query.add_argument("--intent", required=True, choices=("discover", "enumerate"))
    query.add_argument("--view", required=True, choices=("resolved", "historical"))
    query.add_argument("--known-at", type=_aware_time)
    query.add_argument("--page-size", type=int, default=20)
    query.add_argument("--json", action="store_true")
    result = commands.add_parser(
        "result", help="Page one retained immutable result by its exact pin."
    )
    result.add_argument("result_id", type=UUID)
    result.add_argument("--digest", required=True)
    result.add_argument("--cursor")
    result.add_argument("--page-size", type=int, default=20)
    result.add_argument("--json", action="store_true")
    clients = commands.add_parser(
        "clients", help="Pair or revoke one local agent client with current authority."
    )
    client_commands = clients.add_subparsers(dest="client_command", required=True)
    pair = client_commands.add_parser(
        "pair", help="Pair one agent client and write its new secret once to a file."
    )
    pair.add_argument("--request-file", required=True, type=Path)
    pair.add_argument(
        "--secret-file",
        required=True,
        type=Path,
        help="New owner-only file to create; never overwritten or printed.",
    )
    pair.add_argument("--json", action="store_true")
    revoke = client_commands.add_parser(
        "revoke", help="Terminally revoke one pairing grant at its current revision."
    )
    revoke.add_argument("--request-file", required=True, type=Path)
    revoke.add_argument("--json", action="store_true")
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
    return _request_file(path, StoredEvidenceSelection)


def _request_file[RequestModel: BaseModel](
    path: Path, model: type[RequestModel]
) -> RequestModel:
    with path.open("rb") as file:
        data = file.read(8193)
    if len(data) > 8192:
        raise ValueError("request file exceeds 8192 bytes")
    return model.model_validate_json(data)


def _source_authority(
    action: str, request: BaseModel, environment: Mapping[str, str]
) -> BaseModel | dict[str, str]:
    configured = _local_read_configuration(environment)
    if configured is None:
        return {"outcome": "unavailable", "reason": "local_source_authority_required"}
    database, credential_sha256, workspace_id = configured
    try:
        with psycopg.connect(database, autocommit=True) as connection:
            authority = PostgresSourceEnrollment(
                connection,
                credential_sha256=credential_sha256,
                workspace_id=workspace_id,
            )
            if action == "enroll":
                return authority.enroll(EnrollExactSource.model_validate(request))
            if action == "grant":
                return authority.grant(GrantExactSource.model_validate(request))
            return authority.revoke(RevokeExactSource.model_validate(request))
    except (
        PermissionError,
        InsufficientPrivilege,
        InvalidAuthorizationSpecification,
        NoDataFound,
    ):
        return {"outcome": "unavailable", "reason": "resource_unavailable"}
    except Exception:
        # Never echo source identity, credentials, connection or SQL diagnostics.
        return {"outcome": "failed", "reason": "source_authority_failed"}


def _results_transport(environment: Mapping[str, str]) -> ResultsTransport | None:
    """Compose the direct trusted-host executor only when every input is present."""

    configured = _local_read_configuration(environment)
    reader_url = environment.get("MEMORIESQL_QUERY_READER_URL")
    reader_role = environment.get("MEMORIESQL_QUERY_READER_ROLE")
    if configured is None or not reader_url or not reader_role:
        return None
    control_url, credential_sha256, workspace_id = configured
    return TrustedHostTransport(
        control_url=control_url,
        reader_url=reader_url,
        reader_role=reader_role,
        pinned_profile_sha256=environment.get("MEMORIESQL_QUERY_AUTHORITY_SHA256")
        or None,
        credential_sha256=credential_sha256,
        workspace_id=workspace_id,
    )


def _emit_reply(reply: bytes, *, machine: bool) -> int:
    """Print the executor's closed reply unchanged; exit by its outcome."""

    payload = json.loads(reply)
    if machine:
        sys.stdout.write(reply.decode("utf-8") + "\n")
    else:
        _write_json(payload)
    outcome = payload.get("outcome")
    if outcome == "available":
        return 0
    return 2 if outcome == "unavailable" else 3


def _investigate(args: argparse.Namespace, environment: Mapping[str, str]) -> int:
    configured = _local_read_configuration(environment)
    if configured is None:
        return _emit_result(
            {"outcome": "unavailable", "reason": "local_read_identity_required"},
            machine=args.json,
        )
    transport = _results_transport(environment)
    if transport is None:
        return _emit_result(
            {"outcome": "unavailable", "reason": "trusted_query_host_not_configured"},
            machine=args.json,
        )
    _database, credential_sha256, workspace_id = configured
    try:
        if args.command == "query":
            sql = read_bounded(args.file, MAX_SQL_BYTES).decode("utf-8")
            parameters = (
                json.loads(read_bounded(args.parameters_file, MAX_PARAMETER_FILE_BYTES))
                if args.parameters_file is not None
                else []
            )
            if not isinstance(parameters, list):
                raise ValueError("parameters must be a JSON list")
    except (OSError, ValueError):
        return _emit_result(
            {"outcome": "failed", "reason": "invalid_query_input"}, machine=args.json
        )
    run = RunStore(
        default_state_root(environment),
        credential_sha256=credential_sha256,
        workspace_id=workspace_id,
    ).current(transport, now=datetime.now(UTC))
    if isinstance(run, bytes):
        return _emit_reply(run, machine=args.json)
    if args.command == "query":
        request = query_request(
            run,
            sql=sql,
            parameters=parameters,
            intent=args.intent,
            view=args.view,
            known_at=args.known_at,
            page_size=args.page_size,
        )
    else:
        request = reuse_request(
            run,
            result_id=args.result_id,
            content_digest=args.digest,
            cursor=args.cursor,
            page_size=args.page_size,
        )
    return _emit_reply(transport.handle(request), machine=args.json)
def _write_new_secret(path: Path) -> str:
    """Create one owner-only secret file; refuse existing paths and symlinks."""

    secret = secrets.token_urlsafe(32)
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    descriptor = os.open(path, flags, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        payload = secret.encode("ascii")
        written = 0
        while written < len(payload):
            written += os.write(descriptor, payload[written:])
        os.fsync(descriptor)
    except BaseException:
        os.close(descriptor)
        path.unlink(missing_ok=True)
        raise
    os.close(descriptor)
    return secret


def _pair_client(
    request: PairLocalClient, secret_file: Path, environment: Mapping[str, str]
) -> dict[str, object]:
    configured = _local_read_configuration(environment)
    if configured is None:
        return {"outcome": "unavailable", "reason": "local_client_authority_required"}
    database, credential_sha256, workspace_id = configured
    try:
        secret = _write_new_secret(secret_file)
    except OSError:
        return {"outcome": "failed", "reason": "secret_file_unavailable"}
    receipt: LocalClientPairing | None = None
    refusal: dict[str, object] = {"outcome": "failed", "reason": "client_pairing_failed"}
    try:
        with psycopg.connect(database, autocommit=True) as connection:
            receipt = PostgresLocalClientPairing(
                connection,
                credential_sha256=credential_sha256,
                workspace_id=workspace_id,
            ).pair(request, client_secret_sha256=LocalCredential(secret).sha256())
    except PermissionError:
        refusal = {"outcome": "unavailable", "reason": "resource_unavailable"}
    except Exception:
        # A failure after the committed pairing still reports that receipt.
        # Never echo the secret, credentials, connection or SQL diagnostics.
        pass
    if receipt is None:
        # An unpaired secret authorizes nothing; do not leave it behind.
        secret_file.unlink(missing_ok=True)
        return refusal
    return {
        "outcome": "available",
        "receipt": receipt.model_dump(mode="json"),
        "secret_file_written": True,
    }


def _revoke_client(
    request: RevokeLocalClient, environment: Mapping[str, str]
) -> dict[str, object]:
    configured = _local_read_configuration(environment)
    if configured is None:
        return {"outcome": "unavailable", "reason": "local_client_authority_required"}
    database, credential_sha256, workspace_id = configured
    try:
        with psycopg.connect(database, autocommit=True) as connection:
            receipt = PostgresLocalClientPairing(
                connection,
                credential_sha256=credential_sha256,
                workspace_id=workspace_id,
            ).revoke(request)
        return {"outcome": "available", "receipt": receipt.model_dump(mode="json")}
    except PairingRevisionConflict:
        return {"outcome": "failed", "reason": "pairing_revision_conflict"}
    except PermissionError:
        return {"outcome": "unavailable", "reason": "resource_unavailable"}
    except Exception:
        return {"outcome": "failed", "reason": "client_revocation_failed"}


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
        reference = get_contract("memoriesql.core-cli.v2")
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

    if args.command == "schema":
        return _emit_result(schema_description(), machine=args.json)

    if args.command in ("query", "result"):
        return _investigate(args, os.environ)

    if args.command == "sources":
        if args.source_command is None:
            return _emit_result(
                {"outcome": "unavailable", "reason": "source_inventory_not_released"},
                machine=args.json,
            )
        source_request: BaseModel
        try:
            if args.source_command == "enroll":
                source_request = _request_file(args.request_file, EnrollExactSource)
            elif args.source_command == "grant":
                source_request = _request_file(args.request_file, GrantExactSource)
            else:
                source_request = _request_file(args.request_file, RevokeExactSource)
        except (OSError, ValueError):
            return _emit_result(
                {"outcome": "failed", "reason": "invalid_source_request"},
                machine=args.json,
            )
        receipt = _source_authority(args.source_command, source_request, os.environ)
        if isinstance(receipt, BaseModel):
            return _emit_result(
                {"outcome": "available", "receipt": receipt.model_dump(mode="json")},
                machine=args.json,
            )
        return _emit_result(receipt, machine=args.json)

    if args.command == "clients":
        client_request: PairLocalClient | RevokeLocalClient
        try:
            if args.client_command == "pair":
                client_request = _request_file(args.request_file, PairLocalClient)
            else:
                client_request = _request_file(args.request_file, RevokeLocalClient)
        except (OSError, ValueError):
            return _emit_result(
                {"outcome": "failed", "reason": "invalid_client_request"},
                machine=args.json,
            )
        if isinstance(client_request, PairLocalClient):
            outcome = _pair_client(client_request, args.secret_file, os.environ)
        else:
            outcome = _revoke_client(client_request, os.environ)
        return _emit_result(outcome, machine=args.json)

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
