"""Operator commands for the trusted query host; never reachable from its socket.

Every command loads (or, for first provisioning, creates) the service user's
private configuration, so only that user can run them: in practice
`sudo -H -u _memoriesql …`, from the owner's own terminal, never from an agent
session. Output never contains a password, credential or credential digest.
"""

from __future__ import annotations

import base64
import getpass
import hashlib
import hmac
import json
import os
import pwd
import secrets
import stat
import sys
import sysconfig
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
from psycopg import Connection, sql

from memoriesql.infrastructure.postgres.agent_sql_authority import (
    qualify_query_authority,
)
from memoriesql.infrastructure.postgres.query_reader_provisioning import (
    provision_query_reader,
    reviewed_query_reader_profile,
)
from memoriesql.infrastructure.results_broker.config import (
    BrokerConfig,
    ConfigurationRefused,
    DatabaseEndpoint,
    DatabaseLogin,
    commit_config,
    generate_password,
    load_config,
    private_directory,
    stage_config,
)
from memoriesql.infrastructure.results_broker.registry import (
    mark_closed,
    recorded_runs,
    run_owner,
)
from memoriesql.infrastructure.results_broker.server import (
    EXIT_FAILED,
    EXIT_OK,
    EXIT_REFUSED,
    HostRefused,
    check_control_login,
    prepare_host,
)
from memoriesql.infrastructure.results_broker.wire import UNAVAILABLE_REPLY

DEFAULT_CONTROL_ROLE = "memoriesql_query_host"
DEFAULT_READER_ROLE = "memoriesql_query_reader"
SCRAM_ITERATIONS = 4096


def scram_sha256_verifier(password: str, *, iterations: int = SCRAM_ITERATIONS) -> str:
    """PostgreSQL's stored SCRAM-SHA-256 verifier (RFC 5802/7677) for `password`.

    The server stores a pre-encoded verifier as given, so the plaintext never
    reaches the server, its statement logs or any administrator session. Only
    ASCII secrets are accepted, for which SASLprep is the identity.
    """
    if not password.isascii() or not password.isprintable():
        raise ValueError("verifier generation requires a printable ASCII secret")
    salt = secrets.token_bytes(16)
    salted = hashlib.pbkdf2_hmac("sha256", password.encode("ascii"), salt, iterations)
    client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
    stored_key = hashlib.sha256(client_key).digest()
    server_key = hmac.new(salted, b"Server Key", hashlib.sha256).digest()

    def encode(value: bytes) -> str:
        return base64.b64encode(value).decode("ascii")

    return (
        f"SCRAM-SHA-256${iterations}:{encode(salt)}"
        f"${encode(stored_key)}:{encode(server_key)}"
    )


def _emit(value: dict[str, Any], *, machine: bool = True) -> None:
    print(
        json.dumps(
            value, ensure_ascii=True, sort_keys=True, indent=None if machine else 2
        )
    )


def _refused(reason: str) -> int:
    _emit({"outcome": "refused", "reason": reason})
    return EXIT_REFUSED


def _prompt_admin_url() -> str:
    return getpass.getpass("Database administrator connection URL (not echoed): ")


def provision(
    config_path: Path,
    *,
    workspace_id: UUID | None = None,
    client_uid: int | None = None,
    database_host: str | None = None,
    database_port: int | None = None,
    database_name: str | None = None,
    rotate: bool = False,
    control_role: str = DEFAULT_CONTROL_ROLE,
    reader_role: str = DEFAULT_READER_ROLE,
    admin_url: Callable[[], str] = _prompt_admin_url,
) -> int:
    """Create (or rotate) both host logins with generated secrets and pin them.

    First provisioning creates the trusted control login as a non-superuser
    INHERIT member of memoriesql_application and the restricted reader through
    PR-05's reviewed provisioning. `rotate` replaces both passwords. Secrets are
    generated here, stored only in the 0600 configuration and sent to the
    server only as SCRAM verifiers. The administrator URL is read without echo
    and never stored.
    """
    try:
        private_directory(config_path.parent)
    except ConfigurationRefused as refusal:
        return _refused(str(refusal))
    exists = os.path.lexists(config_path)
    if exists and not rotate:
        return _refused("already provisioned; use --rotate to replace the secrets")
    if not exists and rotate:
        return _refused("nothing to rotate; provision first")
    control_password, reader_password = generate_password(), generate_password()
    if exists:
        try:
            current = load_config(config_path)
        except ConfigurationRefused as refusal:
            return _refused(str(refusal))
        config = current.model_copy(
            update={
                "control": DatabaseLogin(
                    role=current.control.role, password=control_password
                ),
                "reader": DatabaseLogin(
                    role=current.reader.role, password=reader_password
                ),
            }
        )
    else:
        if (
            workspace_id is None
            or client_uid is None
            or database_host is None
            or database_port is None
            or database_name is None
        ):
            return _refused(
                "first provisioning needs --workspace-id, --client-uid, "
                "--database-host, --database-port and --database-name"
            )
        root = config_path.parent.parent
        try:
            config = BrokerConfig(
                workspace_id=workspace_id,
                client_uid=client_uid,
                socket_path=root / "run" / "broker.sock",
                state_dir=root / "state",
                database=DatabaseEndpoint(
                    host=database_host, port=database_port, name=database_name
                ),
                control=DatabaseLogin(role=control_role, password=control_password),
                reader=DatabaseLogin(role=reader_role, password=reader_password),
            )
        except ValueError:
            return _refused("invalid provisioning arguments")
    try:
        url = admin_url()
    except (EOFError, KeyboardInterrupt, OSError):
        return _refused("administrator connection URL not provided")
    # Stage first: once the roles change, the only copy of the new secrets is
    # this file, so it must already be durable.
    staged = stage_config(config_path, config)
    try:
        with psycopg.connect(url, autocommit=True) as admin:
            with admin.transaction():
                _apply_roles(admin, config, rotate=exists)
        with psycopg.connect(config.control_conninfo(), autocommit=True) as control:
            check_control_login(control)
            profile = reviewed_query_reader_profile(control, config.reader.role)
            pin = qualify_query_authority(control, profile).profile_sha256
    except HostRefused as refusal:
        staged.unlink(missing_ok=True)
        return _refused(str(refusal))
    except psycopg.Error as error:
        staged.unlink(missing_ok=True)
        return _refused(f"database provisioning failed (SQLSTATE {error.sqlstate})")
    except ValueError:
        staged.unlink(missing_ok=True)
        return _refused("reviewed reader provisioning refused")
    staged.unlink(missing_ok=True)
    final = stage_config(config_path, config.model_copy(update={"profile_sha256": pin}))
    commit_config(final, config_path)
    _emit(
        {
            "outcome": "available",
            "config": str(config_path),
            "control_role": config.control.role,
            "reader_role": config.reader.role,
            "profile_sha256": pin,
            "rotated": exists,
            "restart_required": exists,
        }
    )
    return EXIT_OK


def _apply_roles(admin: Connection[Any], config: BrokerConfig, *, rotate: bool) -> None:
    control = sql.Identifier(config.control.role)
    control_verifier = scram_sha256_verifier(config.control.password.get_secret_value())
    reader_verifier = scram_sha256_verifier(config.reader.password.get_secret_value())
    if rotate:
        admin.execute(
            sql.SQL("ALTER ROLE {} PASSWORD {}").format(
                control, sql.Literal(control_verifier)
            )
        )
        admin.execute(
            sql.SQL("ALTER ROLE {} PASSWORD {}").format(
                sql.Identifier(config.reader.role), sql.Literal(reader_verifier)
            )
        )
        return
    admin.execute(
        sql.SQL(
            "CREATE ROLE {} LOGIN INHERIT NOSUPERUSER NOBYPASSRLS NOCREATEDB "
            "NOCREATEROLE NOREPLICATION PASSWORD {}"
        ).format(control, sql.Literal(control_verifier))
    )
    admin.execute(
        sql.SQL(
            "GRANT memoriesql_application TO {} WITH INHERIT TRUE, SET TRUE"
        ).format(control)
    )
    # Qualification refuses a profile whose creators would grant PUBLIC EXECUTE
    # on future functions, PostgreSQL's implicit default.
    admin.execute(
        sql.SQL(
            "ALTER DEFAULT PRIVILEGES FOR ROLE {} REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC"
        ).format(control)
    )
    # PR-05's reviewed closure; it is handed the verifier, never the plaintext.
    provision_query_reader(admin, config.reader.role, reader_verifier)
    # The host must observe, cancel and confirm the end of its own reader
    # backends; PostgreSQL shows another role's session identity only to a role
    # that inherits it. This is narrower than cluster-wide pg_read_all_stats and
    # gives the reader nothing: it gains no membership of its own. Inheriting
    # the reader also gives the control login the query schemas' USAGE, which
    # re-deriving the reviewed profile by name at every start requires.
    admin.execute(
        sql.SQL("GRANT {} TO {} WITH INHERIT TRUE, SET FALSE").format(
            sql.Identifier(config.reader.role), control
        )
    )


def _client_groups(uid: int) -> set[int]:
    try:
        entry = pwd.getpwuid(uid)
    except KeyError:
        return set()
    # Group ids are unsigned 32-bit; getgrouplist takes and returns C ints.
    primary = entry.pw_gid - 2**32 if entry.pw_gid >= 2**31 else entry.pw_gid
    try:
        groups = os.getgrouplist(entry.pw_name, primary)
    except OSError:
        groups = [primary]
    return {group & 0xFFFFFFFF for group in groups} | {entry.pw_gid}


def _writable_by(path: Path, uid: int, groups: set[int]) -> bool:
    status = os.lstat(path)
    mode = status.st_mode
    if stat.S_ISLNK(mode):
        # A symlink's own mode is irrelevant; its owner can repoint it.
        return status.st_uid == uid
    if status.st_uid == uid:
        return True
    if status.st_gid in groups and mode & 0o020:
        return True
    return bool(mode & 0o002)


def _chain(path: Path) -> list[Path]:
    """`path` and every ancestor, plus each symlink target along the way."""
    seen: list[Path] = []
    pending = [Path(os.path.abspath(path))]
    while pending:
        current = pending.pop()
        for candidate in [current, *current.parents]:
            if candidate in seen:
                continue
            seen.append(candidate)
            if candidate.is_symlink():
                pending.append(candidate.resolve())
    return seen


def integrity_findings(paths: list[Path], client_uid: int) -> list[str]:
    """Paths (or ancestors) the client uid could modify, rename or replace.

    Mode bits only: an ACL can widen access, so the qualification also probes
    writes as the client. A finding means the host's code, configuration or
    socket could be replaced by the agent's own uid.
    """
    groups = _client_groups(client_uid)
    findings = []
    for path in paths:
        for component in _chain(path):
            try:
                writable = _writable_by(component, client_uid, groups)
            except OSError:
                findings.append(f"{component}: unavailable")
                continue
            if writable:
                findings.append(str(component))
    return sorted(set(findings))


def _passwordless_login_refused(config: BrokerConfig, role: str) -> bool:
    try:
        with psycopg.connect(
            host=config.database.host,
            port=str(config.database.port),
            dbname=config.database.name,
            user=role,
            password="",
            passfile=str(config.no_password_file()),
            sslmode=config.database.sslmode,
            connect_timeout=5,
        ):
            return False
    except psycopg.Error:
        return True


def check(config_path: Path) -> int:
    """Self-check of the installation and database authority; JSON report."""
    checks: list[dict[str, Any]] = []

    def record(name: str, ok: bool, detail: str | None = None) -> None:
        checks.append({"check": name, "ok": ok, "detail": detail})

    try:
        config = load_config(config_path)
    except ConfigurationRefused as refusal:
        record("configuration", False, str(refusal))
        _emit({"outcome": "refused", "checks": checks})
        return EXIT_REFUSED
    record("configuration", True)
    code_paths = [
        Path(sys.executable),
        Path(sys.prefix),
        Path(sys.base_prefix),
        Path(sysconfig.get_path("stdlib")),
        Path(sysconfig.get_path("purelib")),
        Path(__file__).parent,
    ]
    findings = integrity_findings(
        [
            *code_paths,
            config_path,
            config.state_dir,
            config.socket_path.parent,
        ],
        config.client_uid,
    )
    record("client_cannot_modify_host", not findings, ", ".join(findings) or None)
    try:
        socket_dir = os.lstat(config.socket_path.parent)
        record(
            "socket_directory",
            socket_dir.st_uid == os.geteuid() and not socket_dir.st_mode & 0o027,
            f"mode {stat.S_IMODE(socket_dir.st_mode):04o}",
        )
    except OSError:
        record("socket_directory", False, "unavailable")
    try:
        private_directory(config.state_dir)
        record("state_directory", True)
    except ConfigurationRefused as refusal:
        record("state_directory", False, str(refusal))
    try:
        prepare_host(config)
        record("database_authority", True)
    except HostRefused as refusal:
        record("database_authority", False, str(refusal))
    for role in (config.control.role, config.reader.role):
        record(
            f"passwordless_login_refused:{role}",
            _passwordless_login_refused(config, role),
        )
    ok = all(item["ok"] for item in checks)
    _emit({"outcome": "available" if ok else "refused", "checks": checks})
    return EXIT_OK if ok else EXIT_REFUSED


def list_runs(config_path: Path) -> int:
    try:
        config = load_config(config_path)
        runs = recorded_runs(config.state_dir)
    except ConfigurationRefused as refusal:
        return _refused(str(refusal))
    except (OSError, ValueError):
        _emit({"outcome": "failed", "reason": "run_registry_unavailable"})
        return EXIT_FAILED
    _emit({"outcome": "available", "runs": runs})
    return EXIT_OK


def close_run(config_path: Path, run_ref: UUID) -> int:
    """Owner-only early close of a host-started run (PR-05 semantics).

    Refused while any of the run's work is unsettled; refunds nothing, keeps
    the run's charges in the rolling window and never reopens it. Prints the
    executor's closed reply bytes unchanged.
    """
    try:
        config = load_config(config_path)
        owner = run_owner(config.state_dir, run_ref)
    except ConfigurationRefused as refusal:
        return _refused(str(refusal))
    except (OSError, ValueError):
        _emit({"outcome": "failed", "reason": "run_registry_unavailable"})
        return EXIT_FAILED
    if owner is None or owner[1] != config.workspace_id:
        sys.stdout.write(UNAVAILABLE_REPLY.decode("ascii") + "\n")
        return EXIT_REFUSED
    try:
        host = prepare_host(config)
    except HostRefused as refusal:
        return _refused(str(refusal))
    reply = host.executor(owner[0]).close_run(str(run_ref))
    sys.stdout.write(reply.decode("utf-8") + "\n")
    try:
        data = json.loads(reply)
    except ValueError:
        return EXIT_FAILED
    outcome = data.get("outcome")
    if outcome == "available":
        closed = data.get("closed", {})
        mark_closed(
            config.state_dir,
            run_ref,
            str(closed.get("closed_at") or datetime.now(UTC).isoformat()),
        )
        return EXIT_OK
    return EXIT_REFUSED if outcome == "unavailable" else EXIT_FAILED


__all__ = [
    "EXIT_FAILED",
    "EXIT_OK",
    "EXIT_REFUSED",
    "check",
    "close_run",
    "integrity_findings",
    "list_runs",
    "provision",
    "scram_sha256_verifier",
]
