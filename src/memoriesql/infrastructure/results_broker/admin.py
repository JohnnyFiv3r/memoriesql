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
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
from psycopg import Connection, sql

from memoriesql.application.agent_sql_catalog import SqlAdmissionError
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
    last_cleanup,
    latest_principal,
    mark_closed,
    recorded_runs,
    run_owner,
)
from memoriesql.infrastructure.results_broker.server import (
    EXIT_FAILED,
    EXIT_OK,
    EXIT_REFUSED,
    HostRefused,
    TrustedHost,
    check_control_login,
    prepare_host,
)
from memoriesql.infrastructure.results_broker.wire import UNAVAILABLE_REPLY

# `broker check` fails when the hourly cleanup has not completed for this long.
CLEANUP_RECENT = timedelta(hours=2)
DEFAULT_CONTROL_ROLE = "memoriesql_query_host"
DEFAULT_READER_ROLE = "memoriesql_query_reader"
SCRAM_ITERATIONS = 4096


# How long settling a lost commit waits for its transaction to end.
TRANSACTION_END_SECONDS = 30.0


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


def scram_sha256_matches(password: str, verifier: str | None) -> bool:
    """Whether a stored SCRAM-SHA-256 verifier was derived from `password`."""
    if not verifier or not password.isascii():
        return False
    try:
        method, parameters, keys = verifier.split("$")
        iterations, salt = parameters.split(":")
        stored_key = base64.b64decode(keys.split(":")[0], validate=True)
        salted = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("ascii"),
            base64.b64decode(salt, validate=True),
            int(iterations),
        )
    except ValueError:
        return False
    if method != "SCRAM-SHA-256":
        return False
    client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
    return hmac.compare_digest(hashlib.sha256(client_key).digest(), stored_key)


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
        if _kept_configurations(config_path):
            return _refused(
                "nothing to rotate yet: an interrupted first provisioning kept its "
                "configuration; run broker provision without --rotate to settle it"
            )
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
    # An interrupted earlier run may have kept the only copy of secrets the
    # database already requires: settle it before any role changes again.
    settled = _settle_kept(config_path, url)
    if settled is not None:
        return settled
    # Stage first: once the roles change, the only copy of the new secrets is
    # this file, so it must already be durable.
    staged = stage_config(config_path, config)
    try:
        admin = psycopg.connect(url, autocommit=True)
    except psycopg.Error as error:
        # Nothing reached the database: the staged secrets were never sent.
        staged.unlink(missing_ok=True)
        return _refused(f"database provisioning failed ({_connect_cause(error)})")
    recovered = False
    transaction: str | None = None
    sent = False  # whether COMMIT may have reached the server
    try:
        with admin:
            with admin.transaction():
                # Recorded beside the staged file before any role change: if the
                # reply to COMMIT is lost, a negative read is final only once
                # this transaction has ended.
                transaction = _current_transaction(admin)
                _record_transaction(staged, transaction)
                _apply_roles(admin, config, rotate=exists)
                sent = True  # leaving this block sends COMMIT
    except psycopg.Error as error:
        cause = f"SQLSTATE {error.sqlstate}" if error.sqlstate else "connection lost"
        if not sent:
            # COMMIT was never sent, so no role change can commit.
            _discard(staged)
            return _refused(
                f"database provisioning did not complete ({cause} before commit); "
                "nothing changed"
            )
        # COMMIT was sent but its reply was lost or refused: the database may
        # already require the staged secrets, so it settles the outcome.
        committed = _secrets_committed(url, config, transaction)
        if committed is None:
            return _outcome_unknown(staged, recorded=True)
        if not committed:
            _discard(staged)
            return _refused(
                f"database provisioning did not complete ({cause} during commit); "
                "nothing changed"
            )
        recovered = True
    except ValueError:
        _discard(staged)
        return _refused("reviewed reader provisioning refused")
    except BaseException:
        # Interrupted, for example by Ctrl-C. Before COMMIT nothing can commit;
        # after it, the recorded transaction lets a later run settle the file.
        if not sent:
            _discard(staged)
        raise
    # The roles now carry the new secrets and this staged file is their only
    # copy: make it live before anything else can fail.
    _transaction_record(staged).unlink(missing_ok=True)
    commit_config(staged, config_path)
    return _pin(config_path, config, rotated=exists, recovered=recovered)


def _kept_configurations(config_path: Path) -> list[Path]:
    """Staged files an interrupted run left beside the live configuration."""
    return sorted(
        config_path.parent.glob(f".{config_path.name}.*.new"),
        key=lambda path: path.stat().st_mtime,
    )


def _settle_kept(config_path: Path, url: str) -> int | None:
    """Settle kept configurations before anything else changes the roles.

    A kept file may hold the only copy of secrets the database already
    requires. One the database accepts becomes the live configuration and is
    pinned; one it can no longer accept is removed. None: nothing was kept, or
    every kept file was safely removed, so provisioning proceeds.
    """
    for kept in _kept_configurations(config_path):
        try:
            candidate = load_config(kept)
        except ConfigurationRefused as refusal:
            return _refused(str(refusal))
        transaction = _recorded_transaction(kept)
        committed = _secrets_committed(url, candidate, transaction)
        if committed is None:
            return _outcome_unknown(kept, recorded=transaction is not None)
        if not committed:
            _discard(kept)
            continue
        _transaction_record(kept).unlink(missing_ok=True)
        rotated = os.path.lexists(config_path)
        commit_config(kept, config_path)
        return _pin(config_path, candidate, rotated=rotated, recovered=True)
    return None


def _secrets_committed(
    url: str, config: BrokerConfig, transaction: str | None
) -> bool | None:
    """Whether both roles now require `config`'s secrets; None if not yet known.

    The stored SCRAM verifiers are compared with the staged passwords, so
    neither a pg_hba rule nor a refused login can be mistaken for an
    uncommitted change. A match is final: a commit never reverts. A mismatch is
    final only once `transaction`, which sent the role changes, has ended;
    until then its commit may still become visible.
    """
    try:
        with psycopg.connect(url, autocommit=True, connect_timeout=10) as admin:
            # The verifiers are read after the end is observed, never before.
            ended = transaction is not None and _transaction_ended(admin, transaction)
            stored: dict[str, str | None] = dict(
                admin.execute(
                    "SELECT rolname, rolpassword FROM pg_catalog.pg_authid "
                    "WHERE rolname = ANY(%s)",
                    ([config.control.role, config.reader.role],),
                ).fetchall()
            )
    except psycopg.Error:
        return None
    if all(
        scram_sha256_matches(login.password.get_secret_value(), stored.get(login.role))
        for login in (config.control, config.reader)
    ):
        return True
    return False if ended else None


def _current_transaction(admin: Connection[Any]) -> str:
    """This transaction's 64-bit ID, which PostgreSQL never reuses."""
    (transaction,) = admin.execute(
        "SELECT pg_catalog.pg_current_xact_id()::text"
    ).fetchall()[0]
    return str(transaction)


def _transaction_ended(admin: Connection[Any], transaction: str) -> bool:
    """Wait, within a bound, until `transaction` has committed or aborted.

    pg_xact_status reports a committing transaction as in progress until its
    commit is visible to new snapshots, so once it reports anything else, a
    later read sees the transaction's final effect. A status too old to be kept
    (NULL) belongs to a transaction that ended long ago.
    """
    deadline = time.monotonic() + TRANSACTION_END_SECONDS
    while True:
        status = admin.execute(
            "SELECT pg_catalog.pg_xact_status(%s::xid8)", (transaction,)
        ).fetchall()[0][0]
        if status != "in progress":
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.2)


def _transaction_record(kept: Path) -> Path:
    return kept.with_name(kept.name + ".transaction")


def _record_transaction(staged: Path, transaction: str) -> None:
    """Durably name, beside `staged`, the transaction that may commit it."""
    descriptor = os.open(
        _transaction_record(staged),
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0),
        0o600,
    )
    try:
        os.write(descriptor, transaction.encode("ascii"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    directory = os.open(staged.parent, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _recorded_transaction(kept: Path) -> str | None:
    try:
        recorded = _transaction_record(kept).read_text(encoding="ascii")
    except (OSError, ValueError):
        return None
    return recorded if recorded.isdigit() else None


def _discard(staged: Path) -> None:
    """Remove a staged file the database never accepted, then its record."""
    staged.unlink(missing_ok=True)
    _transaction_record(staged).unlink(missing_ok=True)


def _connect_cause(error: psycopg.Error) -> str:
    if error.sqlstate is None:
        return "administrator login could not connect"
    return f"SQLSTATE {error.sqlstate}"


def _outcome_unknown(kept: Path, *, recorded: bool) -> int:
    _emit(
        {
            "outcome": "failed",
            "reason": "provisioning_outcome_unknown",
            "kept_configuration": str(kept),
            "next": (
                "once the database is reachable and the transaction that wrote "
                "this file has ended, run broker provision again (with --rotate "
                "if this was a rotation) to settle it"
                if recorded
                else "no record names the transaction that wrote this file, so a "
                "rerun cannot settle it: once no provisioning run is in progress, "
                "remove the file and provision again"
            ),
        }
    )
    return EXIT_FAILED


def _pin(
    config_path: Path, config: BrokerConfig, *, rotated: bool, recovered: bool
) -> int:
    """Pin the reviewed reader profile into the live configuration."""
    try:
        with psycopg.connect(config.control_conninfo(), autocommit=True) as control:
            check_control_login(control)
            profile = reviewed_query_reader_profile(control, config.reader.role)
            pin = qualify_query_authority(control, profile).profile_sha256
    except (HostRefused, psycopg.Error, ValueError, RuntimeError) as error:
        # A host with an unconfirmed pin refuses to serve; nothing is weakened.
        return _refused(
            "logins provisioned but the reviewed reader profile was not pinned "
            f"({_pinning_cause(error)}); fix it and run broker provision --rotate"
        )
    final = stage_config(config_path, config.model_copy(update={"profile_sha256": pin}))
    commit_config(final, config_path)
    result: dict[str, Any] = {
        "outcome": "available",
        "config": str(config_path),
        "control_role": config.control.role,
        "reader_role": config.reader.role,
        "profile_sha256": pin,
        "rotated": rotated,
        "restart_required": rotated,
    }
    if recovered:
        result["recovered"] = True
    _emit(result)
    return EXIT_OK


def _pinning_cause(error: BaseException) -> str:
    """The pinning failure's cause without secrets, as the host preflight words it."""
    if isinstance(error, HostRefused):
        return str(error)
    if isinstance(error, SqlAdmissionError):
        return f"reviewed reader authority refused: {error.construct}"
    if isinstance(error, psycopg.Error):
        if error.sqlstate is None:
            return (
                "control login could not connect "
                "(unreachable or authentication refused)"
            )
        return f"SQLSTATE {error.sqlstate}"
    return "reviewed reader specification unavailable"


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
    host: TrustedHost | None = None
    try:
        host = prepare_host(config)
        record("database_authority", True)
    except HostRefused as refusal:
        record("database_authority", False, str(refusal))
    record(*_cleanup_recent(config))
    if host is not None:
        record(*_cleanup_status(host))
    for role in (config.control.role, config.reader.role):
        record(
            f"passwordless_login_refused:{role}",
            _passwordless_login_refused(config, role),
        )
    ok = all(item["ok"] for item in checks)
    _emit({"outcome": "available" if ok else "refused", "checks": checks})
    return EXIT_OK if ok else EXIT_REFUSED


def _cleanup_recent(config: BrokerConfig) -> tuple[str, bool, str | None]:
    """The serving host's own record of its last scheduled expiry cleanup."""
    try:
        last = last_cleanup(config.state_dir)
    except (OSError, ValueError, ConfigurationRefused):
        return "cleanup_recent", False, "cleanup record unavailable"
    if last is None:
        return "cleanup_recent", False, "no expiry cleanup has run"
    age = datetime.now(UTC) - datetime.fromisoformat(str(last["at"]))
    cleanup = last.get("cleanup") or {}
    detail = (
        f"last at {last['at']}: outcome {last.get('outcome')}, "
        f"pending {cleanup.get('pending')}, failed {cleanup.get('failed')}"
    )
    ok = (
        age <= CLEANUP_RECENT
        and last.get("outcome") == "available"
        and not cleanup.get("failed")
    )
    return "cleanup_recent", ok, detail


def _cleanup_status(host: TrustedHost) -> tuple[str, bool, str | None]:
    """PR-05's noncontent status for the workspace: nothing overdue or failed.

    The status is read under the most recent host-started run's principal, the
    only authenticated context the host keeps; before any run it is not needed,
    because nothing can be due.
    """
    config = host.config
    try:
        principal = latest_principal(config.state_dir, config.workspace_id)
    except (OSError, ValueError, ConfigurationRefused):
        return "cleanup_status", False, "run registry unavailable"
    if principal is None:
        return "cleanup_status", True, "no host-started run yet"
    try:
        reply = json.loads(host.executor(principal).cleanup_status())
    except ValueError:
        return "cleanup_status", False, "status unavailable"
    status = reply.get("cleanup")
    if reply.get("outcome") != "available" or not isinstance(status, dict):
        # A revoked or expired principal cannot read status; say so plainly.
        return "cleanup_status", False, "status unavailable under the last principal"
    overdue = status.get("overdue") or {}
    failures = (status.get("failures") or {}).get("count") or 0
    late = sum(int(value or 0) for value in overdue.values())
    return (
        "cleanup_status",
        late == 0 and int(failures) == 0,
        f"overdue {late}, failures {failures}",
    )


def cleanup(config_path: Path) -> int:
    """Run the host's expiry cleanup once, now; prints PR-05's closed reply."""
    try:
        host = prepare_host(load_config(config_path))
    except (ConfigurationRefused, HostRefused) as refusal:
        return _refused(str(refusal))
    reply = host.cleanup()
    print(json.dumps(reply, ensure_ascii=True, sort_keys=True))
    return EXIT_OK if reply.get("outcome") == "available" else EXIT_FAILED


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
    "cleanup",
    "close_run",
    "integrity_findings",
    "list_runs",
    "provision",
    "scram_sha256_verifier",
]
