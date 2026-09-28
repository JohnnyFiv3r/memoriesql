"""CLI client for `memoriesql.agent-sql-results.v1` through a trusted executor.

The executor is the sole holder of the trusted control and restricted reader
connections. This client only assembles closed request bytes, persists the
caller's current run reference and prints the executor's closed reply; it never
executes SQL, repairs a refusal or retries a budgeted operation.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID, uuid4

from memoriesql.application.agent_sql_catalog import SqlCatalog
from memoriesql.application.agent_sql_results import (
    BASELINE_POLICY,
    CONTRACT_ID,
    CONTRACT_VERSION,
    PREPARED_RELATIONS,
)
from memoriesql.contracts import load_catalog

UNAVAILABLE_REPLY = json.dumps(
    {
        "contract_version": CONTRACT_VERSION,
        "outcome": "unavailable",
        "error": {"code": "unavailable"},
    }
).encode()

# Rotate before the database refuses an expired run; never retry a refusal.
RUN_ROTATION_MARGIN = timedelta(seconds=60)
MAX_SQL_BYTES = 32768
MAX_PARAMETER_FILE_BYTES = 65536 + 4096
EVALUATION_RELATION = "evaluation_v1.candidates"
ADMISSION_RULES = (
    "Every value is a `$n` typed parameter; SQL literals are refused except "
    "structural ones such as LIMIT/OFFSET and date_trunc units.",
    "`IN ($1)` takes one scalar; compare arrays with `= ANY($n::type[])`.",
    "Aggregate window functions need an explicit ROWS frame.",
    "One bounded recursive CTE requires the declared recursion object.",
    "Refused constructs reply invalid_request or unsupported_query with a code "
    "and position; project a column instead of `EXISTS (SELECT 1 ...)`.",
)


class ResultsTransport(Protocol):
    """Closed bytes in, closed bytes out; implemented by the trusted executor."""

    def start_run(self) -> bytes: ...

    def handle(self, request: bytes) -> bytes: ...


class TrustedHostTransport:
    """Direct executor for a credential-isolated trusted host process only.

    It holds the trusted control login and the restricted reader login, builds
    the reviewed reader profile at process start, refuses a pinned-profile
    mismatch and settles abandoned deliveries once. Never compose it inside an
    agent's shell: the agent must receive neither connection credential.
    """

    def __init__(
        self,
        *,
        control_url: str,
        reader_url: str,
        reader_role: str,
        pinned_profile_sha256: str | None,
        credential_sha256: str,
        workspace_id: UUID,
    ) -> None:
        self._control_url = control_url
        self._reader_url = reader_url
        self._reader_role = reader_role
        self._pin = pinned_profile_sha256
        self._credential = credential_sha256
        self._workspace = workspace_id
        self._executor: Any = None

    def _connect_control(self) -> Any:
        import psycopg

        return psycopg.connect(self._control_url, autocommit=True)

    def _connect_reader(self) -> Any:
        import psycopg

        return psycopg.connect(self._reader_url, autocommit=True)

    def _service(self) -> Any:
        if self._executor is not None:
            return self._executor
        from memoriesql.infrastructure.postgres.agent_sql_authority import (
            qualify_query_authority,
        )
        from memoriesql.infrastructure.postgres.agent_sql_results import (
            PostgresAgentSqlResults,
        )
        from memoriesql.infrastructure.postgres.query_reader_provisioning import (
            reviewed_query_reader_profile,
        )

        with self._connect_control() as control:
            profile = reviewed_query_reader_profile(control, self._reader_role)
            if (
                self._pin is not None
                and qualify_query_authority(control, profile).profile_sha256
                != self._pin
            ):
                raise PermissionError("reviewed reader profile drifted")
        executor = PostgresAgentSqlResults(
            control_factory=self._connect_control,
            reader_factory=self._connect_reader,
            authority_profile=profile,
            credential_sha256=self._credential,
            workspace_id=self._workspace,
        )
        executor.recover_abandoned()
        self._executor = executor
        return executor

    def start_run(self) -> bytes:
        try:
            service = self._service()
        except Exception:
            # Configuration and drift failures disclose no connection detail.
            return UNAVAILABLE_REPLY
        return bytes(service.start_run())

    def handle(self, request: bytes) -> bytes:
        try:
            service = self._service()
        except Exception:
            return UNAVAILABLE_REPLY
        return bytes(service.handle(request))


class RunStore:
    """One persisted run per principal and workspace, never a credential."""

    def __init__(self, root: Path, *, credential_sha256: str, workspace_id: UUID):
        key = hashlib.sha256(f"{workspace_id}:{credential_sha256}".encode()).hexdigest()
        self._path = root / "runs" / f"{key[:32]}.json"

    def current(
        self, transport: ResultsTransport, *, now: datetime
    ) -> dict[str, Any] | bytes:
        """Return the reusable run, or the executor's refusal bytes."""

        self._path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        lock = os.open(
            self._path.with_suffix(".lock"),
            os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
        try:
            fcntl.flock(lock, fcntl.LOCK_EX)
            run = self._load()
            if run is not None and _expires(run) - RUN_ROTATION_MARGIN > now:
                return run
            reply = transport.start_run()
            data = json.loads(reply)
            if data.get("outcome") != "available" or not isinstance(
                data.get("run"), dict
            ):
                return reply
            self._save(data["run"])
            return dict(data["run"])
        finally:
            os.close(lock)

    def _load(self) -> dict[str, Any] | None:
        try:
            data = json.loads(self._path.read_bytes())
        except (FileNotFoundError, ValueError):
            return None
        return data if isinstance(data, dict) and "run_ref" in data else None

    def _save(self, run: Mapping[str, Any]) -> None:
        temporary = self._path.with_suffix(".tmp")
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(json.dumps(dict(run), sort_keys=True).encode())
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self._path)


def _expires(run: Mapping[str, Any]) -> datetime:
    return datetime.fromisoformat(str(run["expires_at"]).replace("Z", "+00:00"))


def default_state_root(environment: Mapping[str, str]) -> Path:
    configured = environment.get("MEMORIESQL_STATE_DIR")
    if configured:
        return Path(configured)
    base = environment.get("XDG_STATE_HOME") or str(Path.home() / ".local/state")
    return Path(base) / "memoriesql"


def schema_description() -> dict[str, Any]:
    """Describe the installed logical catalog; no database or run is used."""

    catalog = SqlCatalog.installed()
    logical = load_catalog("sql-recall-operations").get("logical_catalog")
    assert isinstance(logical, dict)
    relations = {}
    for name in sorted(catalog.relations):
        if name in PREPARED_RELATIONS:
            status = "prepared"
        elif name == EVALUATION_RELATION:
            status = "reserved_evaluation_only"
        else:
            status = "not_prepared"
        relations[name] = status
    return {
        "outcome": "available",
        "contract": CONTRACT_ID,
        "contract_version": CONTRACT_VERSION,
        "catalog_hash": catalog.hash,
        "relation_status": relations,
        "not_prepared_reply": {
            "outcome": "unsupported_query",
            "error": {"code": "feature", "feature": "unprepared_relation"},
        },
        "admission_rules": list(ADMISSION_RULES),
        "delivery": dict(BASELINE_POLICY["delivery"]),
        "run": dict(BASELINE_POLICY["run"]),
        "logical_catalog": logical,
    }


def query_request(
    run: Mapping[str, Any],
    *,
    sql: str,
    parameters: list[Any],
    intent: str,
    view: str,
    known_at: datetime | None,
    page_size: int,
    step_key: Callable[[], UUID] = uuid4,
) -> bytes:
    request = {
        "contract_version": CONTRACT_VERSION,
        "run_ref": run["run_ref"],
        "step_key": str(step_key()),
        "kind": "query",
        "catalog_hash": SqlCatalog.installed().hash,
        "sql": sql,
        "parameters": parameters,
        "inputs": [],
        "parents": [],
        "scope": {
            "source_refs": [],
            "known_at": (
                known_at.astimezone(UTC).isoformat()
                if known_at is not None
                else run["default_known_at"]
            ),
            "view": view,
        },
        "intent": intent,
        "max_result_bytes": BASELINE_POLICY["result"]["allocation_bytes"],
        "page_size": page_size,
    }
    return json.dumps(request).encode()


def reuse_request(
    run: Mapping[str, Any],
    *,
    result_id: UUID,
    content_digest: str,
    cursor: str | None,
    page_size: int,
    step_key: Callable[[], UUID] = uuid4,
) -> bytes:
    request: dict[str, Any] = {
        "contract_version": CONTRACT_VERSION,
        "run_ref": run["run_ref"],
        "step_key": str(step_key()),
        "kind": "reuse_result",
        "result": {"result_id": str(result_id), "content_digest": content_digest},
        "access": {"kind": "standalone"},
        "page_size": page_size,
    }
    if cursor is not None:
        request["cursor"] = cursor
    return json.dumps(request).encode()


def read_bounded(path: Path, limit: int) -> bytes:
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("input file exceeds its bound")
    return data
