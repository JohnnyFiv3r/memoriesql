"""Trusted executor for `memoriesql.agent-sql-results.v1`: query and reuse_result.

This is the sole holder of both connection classes. Agents must receive neither
the trusted login nor the restricted reader credentials; the host must qualify
that isolation against the agent's actual shell, filesystem and process access.

Every access is admitted and accounted by M0038 before work starts, results are
committed atomically by M0034, and every disclosure re-checks current authority
over the whole saved dependency closure (M0037) in the same fenced transaction
that records its disclosure receipt. Nothing is disclosed from a partial result.
"""

from __future__ import annotations

import hashlib
import importlib.util
import math
import time
from collections.abc import Callable
from contextvars import ContextVar
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from psycopg import Connection, Error
from psycopg.types.json import Jsonb

from memoriesql.application.agent_sql_admission import (
    RecursionBound,
    admit_query,
    require_reviewed,
)
from memoriesql.application.agent_sql_catalog import SqlAdmissionError, SqlCatalog
from memoriesql.application.agent_sql_results import (
    ALLOCATION_PROFILE_HASH,
    CONTRACT_VERSION,
    MAX_RESPONSE_BYTES,
    POLICY_HASH,
    PREPARED_RELATIONS,
    EvidenceIndex,
    ref_key,
    remaining_after,
    reply,
    stable_reply_bytes,
)
from memoriesql.application.investigation_contracts import (
    AutomaticBasis,
    CheckpointManifest,
    CheckpointRequest,
    CheckpointResultAccess,
    HydrateRequest,
    InspectRequest,
    InvestigationRequestError,
    QueryRequest,
    ReadCheckpointRequest,
    ReuseRequest,
    parse_investigation_request,
    request_fingerprint,
    result_json_bytes,
)
from memoriesql.infrastructure.postgres.agent_sql_authority import QueryAuthorityProfile
from memoriesql.infrastructure.postgres.authorization import PostgresAuthorizationPort
from memoriesql.infrastructure.postgres.query_reader_provisioning import (
    REVIEWED_BUILTINS,
)
from memoriesql.infrastructure.postgres.query_result_commit import (
    InternalResultCandidate,
    PostgresQueryResultCommit,
)
from memoriesql.infrastructure.postgres.relation_projection import (
    relation_projection_frame,
)
from memoriesql.infrastructure.postgres.relation_sql_population import (
    RelationPopulationError,
    prepare_query_population,
)
from memoriesql.infrastructure.postgres.restricted_query import PostgresRestrictedQuery
from memoriesql.infrastructure.postgres.result_preparation import (
    PostgresResultPreparation,
    PreparedContent,
)

ConnectionFactory = Callable[[], Connection[Any]]
AccessRequest = (
    QueryRequest
    | ReuseRequest
    | InspectRequest
    | HydrateRequest
    | CheckpointRequest
    | ReadCheckpointRequest
)
# The admitted delivery whose work runs in this context, and the connections
# opened for it (M0047). They name it for the session; the kernel's audit
# writer attributes a row only while it is a live, reserved delivery of the
# same principal, and any still open when it ends stop naming it.
_AUDIT_DELIVERY: ContextVar[tuple[str, list[Connection[Any]]] | None] = ContextVar(
    "memoriesql_query_audit_delivery", default=None
)
_AUDIT_CLAIM = "SELECT set_config('memoriesql.query_audit_delivery',%s,false)"
# Attributed audit rows one governed expiry call removes at most.
_AUDIT_EXPIRY_ROWS = 4096
# Earlier schemas have no audit retention; their cleanup and status are as before.
_AUDIT_RETENTION_SQL = (
    "SELECT to_regprocedure('memoriesql.query_audit_retention_v1()') IS NOT NULL"
)
CHECKPOINT_ALLOCATION_PROFILE_HASH = hashlib.sha256(
    result_json_bytes(
        {
            "kind": "automatic-checkpoint-v1",
            "manifest_encoding": "result-json-v1",
            "control_and_lineage_bytes": 40960,
            "bytes_per_unique_hold": 1024,
            "physical_measurement": "separate-unavailable-until-qualified",
        }
    )
).hexdigest()
_CHECKPOINT_BACKEND_CAPABILITY_SQL = "SELECT " + " AND ".join(
    "to_regprocedure('memoriesql." + signature + "') IS NOT NULL"
    for signature in (
        "prepare_automatic_checkpoint_v1(uuid,jsonb)",
        "publish_automatic_checkpoint_v1(uuid,jsonb,bytea,text)",
        "record_checkpoint_disclosure_v1(uuid,uuid,text,text,bigint,bigint)",
        "query_result_access_v1(uuid,text,jsonb)",
        "read_query_result_page_v2(uuid,uuid,text,bigint,integer,jsonb)",
    )
)

# Presence is a fail-closed capability check, never authorization or SQL proof.
# A registered, reviewed forward migration and installed tests remain required.
_HELPER_BACKEND_SIGNATURES = (
    "memoriesql.prepare_query_helper_v1(uuid,jsonb)",
    "memoriesql.record_query_helper_disclosure_v1(uuid,uuid,text,jsonb,text,bigint,bigint,timestamp with time zone,bigint,integer,uuid,bigint)",
    "memoriesql.query_helper_preparation_v1(uuid)",
    "memoriesql.read_query_helper_artifact_v1(uuid,uuid,uuid)",
    "memoriesql.query_helper_delivery_v1(uuid)",
    "memoriesql.query_helper_evidence_pin_v1(uuid,uuid,uuid,uuid,uuid)",
    "memoriesql.prepare_query_inspection_v1(uuid,jsonb)",
    "memoriesql.resolve_query_helper_evidence_v1(uuid,uuid,uuid,jsonb)",
    "memoriesql.prepare_query_hydration_v1(uuid,jsonb)",
    "memoriesql.purge_query_helper_step_v1()",
)
_HELPER_BACKEND_CAPABILITY_SQL = (
    "SELECT "
    + " AND ".join(
        "to_regprocedure('" + signature + "') IS NOT NULL"
        for signature in _HELPER_BACKEND_SIGNATURES
    )
    + " AND (SELECT count(*) = 4 FROM pg_catalog.pg_attribute "
    "WHERE attrelid=to_regclass('memoriesql.query_steps') "
    "AND NOT attisdropped AND attnum>0 "
    "AND ((attname='helper_artifact_ref' AND atttypid='uuid'::regtype) "
    "OR (attname='helper_page_first' AND atttypid='bigint'::regtype) "
    "OR (attname='helper_page_count' AND atttypid='integer'::regtype) "
    "OR (attname='helper_page_cursor' AND atttypid='uuid'::regtype)))"
    " AND EXISTS (SELECT 1 FROM pg_catalog.pg_attribute "
    "WHERE attrelid=to_regclass('memoriesql.query_disclosures') "
    "AND attname='result_id' AND NOT attisdropped AND NOT attnotnull)"
    " AND EXISTS (SELECT 1 FROM pg_catalog.pg_constraint "
    "WHERE conrelid=to_regclass('memoriesql.query_steps') "
    "AND conname='query_steps_helper_kind_check' AND contype='c' AND convalidated)"
    " AND EXISTS (SELECT 1 FROM pg_catalog.pg_constraint "
    "WHERE conrelid=to_regclass('memoriesql.query_steps') "
    "AND conname='query_steps_helper_complete_check' AND contype='c' AND convalidated)"
    " AND EXISTS (SELECT 1 FROM pg_catalog.pg_class "
    "WHERE oid=to_regclass('memoriesql.query_helper_cursors') "
    "AND relrowsecurity AND relforcerowsecurity)"
    " AND (SELECT count(*) = 2 FROM pg_catalog.pg_trigger "
    "WHERE tgrelid=to_regclass('memoriesql.query_steps') "
    "AND NOT tgisinternal AND tgenabled IN ('O','A') "
    "AND tgname IN ('query_helper_clear_pin','query_helper_purge'))"
)


def _attributed(factory: ConnectionFactory) -> ConnectionFactory:
    """Control connections that name the current delivery, if any, for audit."""

    def connect() -> Connection[Any]:
        connection = factory()
        claim = _AUDIT_DELIVERY.get()
        if claim is not None:
            delivery, opened = claim
            try:
                with connection.transaction():
                    connection.execute(_AUDIT_CLAIM, (delivery,))
            except BaseException:
                connection.close()
                raise
            opened.append(connection)
        return connection

    return connect


def _end_audit_claims(opened: list[Connection[Any]]) -> None:
    """A connection still open when its delivery ends stops naming it."""
    for connection in opened:
        if connection.closed:
            continue
        try:
            with connection.transaction():
                connection.execute(_AUDIT_CLAIM, ("",))
        except Error:
            connection.close()


def _helper_module_available() -> bool:
    # The source component is deliberately not yet in the runtime inventory.
    # Missing it must not break existing query/reuse imports or admit a helper.
    try:
        return (
            importlib.util.find_spec("memoriesql.application.agent_sql_helpers")
            is not None
        )
    except (ImportError, ValueError):
        return False


# Admission refusals write nothing and disclose no other principal's work.
_REFUSALS = {
    "unavailable": ("unavailable", "unavailable"),
    "run_expired": ("budget_exhausted", "time"),
    "idempotency": ("idempotency_conflict", "idempotency"),
    "settlement": ("budget_exhausted", "settlement"),
    "busy": ("budget_exhausted", "database"),
    "accesses": ("budget_exhausted", "database"),
    "time": ("budget_exhausted", "time"),
    "transport": ("budget_exhausted", "transport"),
    # The workspace's query audit allowance is spent until expiry (M0047).
    "audit": ("budget_exhausted", "storage"),
}
_STATE_CODES = {
    "22003": "arithmetic",
    "22012": "arithmetic",
    "53400": "storage",
    "54000": "storage",
    "57014": "time",
    "55P03": "time",
    "23505": "idempotency",
    "42501": "unavailable",
    "28000": "unavailable",
    "55000": "settlement",
    "40001": "settlement",
}
_POPULATION_BYTES = 64 * 1024 * 1024
_REVIEWED = frozenset(REVIEWED_BUILTINS)
# A committed result's disclosure that failed for one of these reasons fixes
# its step as failed. Any other failure leaves the step open for redelivery.
_DEFINITIVE = frozenset({"unavailable", "idempotency", "transport"})


def _state_code(error: BaseException) -> str | None:
    """The safe code of a database error, or None when its SQLSTATE has none.

    SQLSTATE 54000 is both a deadline and a byte limit. The database names a
    passed deadline `..._work_exhausted`; every other 54000 is `storage`.
    """
    sqlstate = getattr(error, "sqlstate", None) or ""
    if sqlstate == "54000":
        message = getattr(getattr(error, "diag", None), "message_primary", None)
        if (message or "").endswith("_work_exhausted"):
            return "time"
    return _STATE_CODES.get(sqlstate)


def _busy(error: BaseException) -> str | None:
    """The safe code of a failure the caller may simply try again, else None.

    A lock or statement timeout is `time`, a storage or work limit is `storage`
    and a conflict with work still in flight is `settlement`. The packet reports
    these as `budget_exhausted`, never as missing authority.
    """
    code = _state_code(error)
    return code if code in ("time", "storage", "settlement") else None


def _refusal(error: BaseException) -> tuple[str, str]:
    """Outcome and safe code for a control call that failed and wrote nothing.

    Every failure that is not a busy database keeps the one shape that missing
    and denied authority share.
    """
    busy = _busy(error)
    return ("budget_exhausted", busy) if busy else ("unavailable", "unavailable")


class _Failure(Exception):
    """Terminal non-available outcome for one admitted delivery."""

    def __init__(
        self,
        outcome: str,
        code: str | None,
        *,
        feature: str | None = None,
        position: int | None = None,
        pending: bool = False,
    ) -> None:
        super().__init__(outcome)
        self.outcome = outcome
        self.code = code
        self.feature = feature
        self.position = position
        self.pending = pending
        # Set once the result is committed: it is immutable, and a failed
        # first disclosure never discards it.
        self.committed = False

    def error(self) -> dict[str, Any] | None:
        if self.code is None:
            return None
        value: dict[str, Any] = {"code": self.code}
        if self.position is not None:
            value["position"] = self.position
        if self.feature is not None:
            value["feature"] = self.feature
        return value


def _pending() -> _Failure:
    """Commit or settlement is unknown: the exact redelivery resolves it."""
    return _Failure("settlement_pending", "settlement", pending=True)


def _committed(failure: _Failure) -> _Failure:
    """A committed result's failed disclosure: the result is immutable and is
    never discarded. Only a definitive refusal fixes its step as failed; a
    busy database or a lost connection leaves it for the exact redelivery."""
    failure.committed = True
    if failure.pending or failure.code in _DEFINITIVE:
        return failure
    return _pending()


def _disclosure_failure(error: Error) -> _Failure:
    """What a database error means while resolving or disclosing a result."""
    code = _state_code(error) or "database"
    return _Failure(
        "unavailable"
        if code == "unavailable"
        else "idempotency_conflict"
        if code == "idempotency"
        else "budget_exhausted"
        if code in {"time", "storage"}
        else "execution_error",
        code,
    )


def _admission_error(outcome: str, construct: str | None) -> tuple[str, str | None]:
    """Map a content-free admission construct to an approved safe error code."""
    if outcome == "unsupported_query":
        return (
            "provenance" if construct == "witness_qualification_pending" else "feature"
        ), construct
    if outcome == "invalid_request":
        if construct == "syntax":
            return "syntax", None
        if construct == "reference_type":
            # The one type error the SQL text cannot show: an identifier column
            # compares only with a value bound under its own reference type.
            return "type", construct
        if construct and "type" in construct:
            return "type", None
        return "binding", None
    if outcome == "unavailable":
        # Missing, denied and dependency-lost identifiers share one shape; the
        # internal construct that refused never reaches a reply.
        return "unavailable", None
    return construct or "database", None


class PostgresAgentSqlResults:
    """Trusted executor; construct it only in the credential-isolated host."""

    def __init__(
        self,
        *,
        control_factory: ConnectionFactory,
        reader_factory: ConnectionFactory,
        authority_profile: QueryAuthorityProfile,
        credential_sha256: str,
        workspace_id: UUID,
    ) -> None:
        self._control = _attributed(control_factory)
        self._credential = credential_sha256
        self._workspace = workspace_id
        self._executor = PostgresRestrictedQuery(
            reader_factory=reader_factory,
            control_factory=self._control,
            authority_profile=authority_profile,
            credential_sha256=credential_sha256,
            workspace_id=workspace_id,
            policy_hash=POLICY_HASH,
            reviewed_builtins=_REVIEWED,
        )

    # -- public operations -------------------------------------------------

    def start_run(self) -> bytes:
        """Start a run for the authenticated principal; callers set nothing.

        A bounded expiry-cleanup pass for this workspace runs first, in its own
        transaction. Content cleanup more than 24 hours past due (missed or
        failed) refuses the run as `budget_exhausted` / `settlement`. A lock or
        statement timeout, for example while a cleanup pass holds this
        workspace's admission lock, is `budget_exhausted` / `time` and starts
        nothing.
        """
        self._cleanup_workspace()
        try:
            with self._control() as connection:
                with relation_projection_frame(
                    connection,
                    credential_sha256=self._credential,
                    workspace_id=self._workspace,
                    query_access=True,
                ) as frame:
                    row = frame.execute(
                        "SELECT memoriesql.start_query_run_v1(%s,%s)",
                        (SqlCatalog.installed().hash, POLICY_HASH),
                    ).fetchone()
        except (PermissionError, Error) as error:
            outcome, code = _refusal(error)
            return result_json_bytes(
                {
                    "contract_version": CONTRACT_VERSION,
                    "outcome": outcome,
                    "error": {"code": code},
                }
            )
        data: dict[str, Any] = row[0] if row else {"refused": "unavailable"}
        if "run" not in data:
            code = "settlement" if data.get("refused") == "cleanup" else "database"
            return result_json_bytes(
                {
                    "contract_version": CONTRACT_VERSION,
                    "outcome": "budget_exhausted",
                    "error": {"code": code},
                }
            )
        return result_json_bytes(
            {
                "contract_version": CONTRACT_VERSION,
                "outcome": "available",
                "run": data["run"],
            }
        )

    def close_run(self, run_ref: str) -> bytes:
        """Owner-only early close; refused while any of its work is unsettled.

        Closing frees the run's active-run slot but refunds nothing: charges
        stay in the rolling workspace window and the run cannot be reopened.
        """
        try:
            with self._control() as connection:
                with relation_projection_frame(
                    connection,
                    credential_sha256=self._credential,
                    workspace_id=self._workspace,
                    query_access=True,
                ) as frame:
                    row = frame.execute(
                        "SELECT memoriesql.close_query_run_v1(%s)", (UUID(run_ref),)
                    ).fetchone()
        except (PermissionError, Error, ValueError) as error:
            outcome, code = _refusal(error)
            return result_json_bytes(
                {
                    "contract_version": CONTRACT_VERSION,
                    "outcome": outcome,
                    "error": {"code": code},
                }
            )
        data: dict[str, Any] = row[0] if row and row[0] else {"refused": "unavailable"}
        refused = data.get("refused")
        if refused:
            outcome, code = _REFUSALS[refused]
            return result_json_bytes(
                {
                    "contract_version": CONTRACT_VERSION,
                    "outcome": outcome,
                    "error": {"code": code},
                }
            )
        return result_json_bytes(
            {
                "contract_version": CONTRACT_VERSION,
                "outcome": "available",
                "closed": data,
            }
        )

    def recover_abandoned(self) -> int:
        """Host recovery after an executor crash; returns deliveries settled.

        Only deliveries whose owning sessions have ended are abandoned, each
        charged its full reservation. Their steps resolve on exact redelivery
        from committed state; nothing is rerun, refunded or disclosed.
        """
        with self._control() as connection:
            with relation_projection_frame(
                connection,
                credential_sha256=self._credential,
                workspace_id=self._workspace,
                query_access=True,
            ) as frame:
                row = frame.execute(
                    "SELECT memoriesql.abandon_query_deliveries_v1()"
                ).fetchone()
        return int(row[0]) if row else 0

    def cleanup_expired(self, *, batch_size: int = 16, max_batches: int = 64) -> bytes:
        """Trusted-host/operator expiry cleanup for every workspace.

        Never an agent wire action. Each batch commits on its own, so an
        interrupted pass leaves nothing partial and the next pass continues.
        The 24-hour deadline holds only while the host schedules this; missed
        or failed cleanup becomes visible and refuses new runs (M0038).
        """
        if not 1 <= batch_size <= 256 or not 1 <= max_batches <= 1024:
            raise ValueError("invalid cleanup bounds")
        totals = {"runs": 0, "results": 0, "tombstones": 0}
        audits = {"audit_rows": 0, "released_bytes": 0}
        pending = failed = batches = 0
        try:
            with self._control() as connection:
                while batches < max_batches:
                    with connection.transaction():
                        connection.execute("SET LOCAL lock_timeout='500ms'")
                        connection.execute("SET LOCAL statement_timeout='10000ms'")
                        connection.execute("SET LOCAL ROLE memoriesql_application")
                        row = connection.execute(
                            "SELECT memoriesql.purge_expired_query_state_v1(%s)",
                            (batch_size,),
                        ).fetchone()
                    batches += 1
                    data: dict[str, Any] = row[0] if row else {}
                    purged = data.get("purged", {})
                    for key in totals:
                        totals[key] += int(purged.get(key, 0))
                    pending = int(data.get("pending", 0))
                    failed = int(data.get("failed", 0))
                    if not any(int(purged.get(key, 0)) for key in totals):
                        break
                # Governed expiry of query-attributed audit rows (M0047).
                with connection.transaction():
                    retained = connection.execute(_AUDIT_RETENTION_SQL).fetchone()
                for _ in range(max_batches if retained and retained[0] else 0):
                    with connection.transaction():
                        connection.execute("SET LOCAL lock_timeout='500ms'")
                        connection.execute("SET LOCAL statement_timeout='10000ms'")
                        connection.execute("SET LOCAL ROLE memoriesql_application")
                        row = connection.execute(
                            "SELECT memoriesql.expire_query_audits_v1(%s)",
                            (_AUDIT_EXPIRY_ROWS,),
                        ).fetchone()
                    expired: dict[str, Any] = row[0] if row else {}
                    for key in audits:
                        audits[key] += int(expired.get(key, 0))
                    if not int(expired.get("audit_rows", 0)):
                        break
        except Error as error:
            busy = _busy(error)
            return result_json_bytes(
                {
                    "contract_version": CONTRACT_VERSION,
                    "outcome": "budget_exhausted" if busy else "execution_error",
                    "error": {"code": busy or "database"},
                    "cleanup": {
                        "purged": totals,
                        "pending": pending,
                        "failed": failed,
                        "batches": batches,
                        "audits": audits,
                    },
                }
            )
        return result_json_bytes(
            {
                "contract_version": CONTRACT_VERSION,
                "outcome": "available",
                "cleanup": {
                    "purged": totals,
                    "pending": pending,
                    "failed": failed,
                    "batches": batches,
                    "audits": audits,
                },
            }
        )

    def cleanup_status(self) -> bytes:
        """Noncontent cleanup status of this workspace, for the host or operator."""
        try:
            with self._control() as connection:
                with relation_projection_frame(
                    connection,
                    credential_sha256=self._credential,
                    workspace_id=self._workspace,
                ) as frame:
                    row = frame.execute(
                        "SELECT memoriesql.query_cleanup_status_v1()"
                    ).fetchone()
                    audit = (
                        frame.execute(
                            "SELECT memoriesql.query_audit_retention_v1()"
                        ).fetchone()
                        if frame.execute(_AUDIT_RETENTION_SQL).fetchone() == (True,)
                        else None
                    )
        except (PermissionError, Error) as error:
            outcome, code = _refusal(error)
            return result_json_bytes(
                {
                    "contract_version": CONTRACT_VERSION,
                    "outcome": outcome,
                    "error": {"code": code},
                }
            )
        return result_json_bytes(
            {
                "contract_version": CONTRACT_VERSION,
                "outcome": "available",
                "cleanup": row[0] if row else None,
                "audit_retention": audit[0] if audit else None,
            }
        )

    def handle(self, data: bytes) -> bytes:
        """One closed request in, one closed reply out; never raises for input."""
        # Charged duration includes parsing and admission bookkeeping; the local
        # deadline starts no later than the database's own delivery deadline.
        started = time.monotonic()
        try:
            request = parse_investigation_request(data)
        except InvestigationRequestError as error:
            return self._bare(None, None, error.outcome, {"code": error.code}, None)
        if not isinstance(
            request,
            QueryRequest
            | ReuseRequest
            | InspectRequest
            | HydrateRequest
            | CheckpointRequest
            | ReadCheckpointRequest,
        ):
            return self._bare(
                request.run_ref,
                request.step_key,
                "unsupported_query",
                {"code": "feature", "feature": request.kind},
                None,
            )
        kind = request.kind
        fingerprint = request_fingerprint(request)
        admission: dict[str, Any] = {}
        for attempt in range(2):
            try:
                admission = self._admit(request, kind, fingerprint)
            except _Failure as failure:
                return self._bare(
                    request.run_ref,
                    request.step_key,
                    failure.outcome,
                    failure.error(),
                    None,
                )
            except (PermissionError, Error) as error:
                # Nothing was admitted or charged: a timed-out admission is a
                # budget refusal, not missing authority.
                outcome, code = _refusal(error)
                return self._bare(
                    request.run_ref,
                    request.step_key,
                    outcome,
                    {"code": code},
                    None,
                )
            refused = admission.get("refused")
            if refused == "pending_self" and attempt == 0:
                # The same step is open. Only a confirmed-dead owner may be
                # abandoned (full reservation charged); a live one stays pending,
                # and so does one whose abandonment could not be confirmed.
                try:
                    abandoned = self._owned(
                        "SELECT memoriesql.abandon_query_delivery_v1(%s)",
                        (UUID(admission["blocking_delivery"]),),
                    )
                except Error:
                    abandoned = False
                if abandoned:
                    continue
            if refused == "pending_self":
                return self._bare(
                    request.run_ref,
                    request.step_key,
                    "settlement_pending",
                    {"code": "settlement"},
                    admission.get("remaining"),
                )
            if refused:
                outcome, code = _REFUSALS[refused]
                return self._bare(
                    request.run_ref,
                    request.step_key,
                    outcome,
                    {"code": code},
                    admission.get("remaining"),
                )
            break
        return self._deliver(request, kind, fingerprint, admission, started)

    # -- delivery --------------------------------------------------------------

    def _deliver(
        self,
        request: AccessRequest,
        kind: str,
        fingerprint: str,
        admission: dict[str, Any],
        start: float,
    ) -> bytes:
        # Every connection opened for this delivery's work names it (M0047).
        opened: list[Connection[Any]] = []
        token = _AUDIT_DELIVERY.set((str(admission["delivery_ref"]), opened))
        try:
            return self._owned_delivery(request, kind, fingerprint, admission, start)
        finally:
            _AUDIT_DELIVERY.reset(token)
            _end_audit_claims(opened)

    def _owned_delivery(
        self,
        request: AccessRequest,
        kind: str,
        fingerprint: str,
        admission: dict[str, Any],
        start: float,
    ) -> bytes:
        deadline = start + int(admission["reserved_db_ms"]) / 1000
        key = int(admission["owner_lock_key"])
        try:
            owner = self._control()
        except Error:
            # Admitted but never owned: recovery or the exact redelivery
            # abandons it at its full reservation.
            return self._pending_reply(request, admission, None)
        try:
            # Session-scoped liveness: recovery may abandon this delivery only
            # after this owning session has ended.
            try:
                owner.execute("SELECT pg_advisory_lock(%s)", (key,))
            except Error:
                return self._pending_reply(request, admission, None)
            step = admission["step"]
            try:
                if admission["replay"] and step["state"] == "failed":
                    raise _Failure(step["outcome"], step["error_code"])
                if isinstance(request, CheckpointRequest | ReadCheckpointRequest):
                    return self._checkpoint(request, admission, start, deadline, owner)
                if isinstance(request, InspectRequest | HydrateRequest):
                    return self._helper(
                        request, fingerprint, admission, start, deadline, owner
                    )
                if admission["replay"] and step["state"] == "complete":
                    return self._disclose(
                        request,
                        admission,
                        start,
                        deadline,
                        UUID(step["result_id"]),
                        step["content_digest"],
                        int(step["first_row"]),
                        int(step["page_size"]),
                        exact_rows=int(step["row_count"]),
                    )
                if isinstance(request, QueryRequest):
                    return self._query(request, fingerprint, admission, start, deadline)
                return self._reuse(request, admission, start, deadline)
            except _Failure as failure:
                return self._settle(request, admission, start, failure)
            except Exception:
                # Our own code failed outside owned remote work: the attempt is
                # an execution error, charged for its observed duration.
                return self._settle(
                    request, admission, start, _Failure("execution_error", "database")
                )
        finally:
            try:
                owner.execute("SELECT pg_advisory_unlock(%s)", (key,))
            except Error:
                pass
            owner.close()

    def _checkpoint(
        self,
        request: CheckpointRequest | ReadCheckpointRequest,
        admission: dict[str, Any],
        start: float,
        deadline: float,
        owner: Connection[Any],
    ) -> bytes:
        """Publish/read automatic manifests through the admitted owning backend.

        SQL retains current authority, allocation and investigation locks through
        final disclosure. No root row/evidence becomes visible by saving metadata.
        A lost commit acknowledgement remains pending, never a second append.
        """
        delivery = UUID(admission["delivery_ref"])
        try:
            with relation_projection_frame(
                owner,
                credential_sha256=self._credential,
                workspace_id=self._workspace,
                statement_timeout_ms=self._remaining_ms(deadline),
            ) as frame:

                def execute(statement: str, values: tuple[Any, ...] = ()) -> Any:
                    frame.execute(
                        f"SET LOCAL statement_timeout='{self._remaining_ms(deadline)}ms'"
                    )
                    return frame.execute(statement, values)

                body = Jsonb(request.model_dump(mode="json"))
                row = execute(
                    "SELECT memoriesql.prepare_automatic_checkpoint_v1(%s,%s)",
                    (delivery, body),
                ).fetchone()
                if not row or not isinstance(row[0], dict):
                    raise _Failure("unavailable", "unavailable")
                packet = row[0]
                if "pending" in packet:
                    if not isinstance(request, CheckpointRequest):
                        raise ValueError("pending checkpoint read")
                    # The request validator bounds a minimum manifest. Trusted
                    # assigned sequences can make the actual encoding larger;
                    # allocation failure is not an execution/database failure.
                    if len(result_json_bytes(packet["pending"])) > 32768:
                        raise _Failure("budget_exhausted", "storage")
                    manifest = CheckpointManifest.model_validate(packet["pending"])
                    self._check_checkpoint_request(request, manifest)
                    encoded = result_json_bytes(manifest.model_dump(mode="json"))
                    row = execute(
                        "SELECT memoriesql.publish_automatic_checkpoint_v1(%s,%s,%s,%s)",
                        (delivery, body, encoded, manifest.content_digest()),
                    ).fetchone()
                    if not row or not isinstance(row[0], dict):
                        raise _Failure("unavailable", "unavailable")
                    packet = row[0]
                manifest, extra = self._checkpoint_payload(request, packet)
                before = execute(
                    "SELECT memoriesql.query_delivery_remaining_v1(%s)", (delivery,)
                ).fetchone()
                if not before:
                    raise _Failure("unavailable", "unavailable")
                observed = math.ceil((time.monotonic() - start) * 1000)

                def build(transport: int, remaining: dict[str, Any]) -> dict[str, Any]:
                    return reply(
                        run_ref=request.run_ref,
                        step_key=request.step_key,
                        outcome="available",
                        receipt_ref=admission["receipt_ref"],
                        access_receipt_ref=admission["delivery_ref"],
                        remaining=remaining,
                        extra={
                            **extra,
                            "work": {
                                "reserved_ms": int(admission["reserved_db_ms"]),
                                "observed_db_ms": observed,
                                "charged_ms": observed,
                                "transport_bytes": transport,
                                "retained_bytes": int(
                                    packet["retention"]["unique_bytes"]
                                ),
                                "allocation_profile_hash": CHECKPOINT_ALLOCATION_PROFILE_HASH,
                                "measured_physical_bytes": None,
                                "measurement_state": "unavailable",
                                "cancellation_state": "none",
                            },
                        },
                    )

                data, transport = stable_reply_bytes(
                    build, charge={"db_ms": observed}, remaining_before=before[0]
                )
                if transport > min(
                    MAX_RESPONSE_BYTES, int(admission["reserved_transport"])
                ):
                    raise _Failure("budget_exhausted", "transport")
                execute(
                    "SELECT memoriesql.record_checkpoint_disclosure_v1(%s,%s,%s,%s,%s,%s)",
                    (
                        delivery,
                        UUID(manifest.checkpoint_id),
                        manifest.content_digest(),
                        hashlib.sha256(data).hexdigest(),
                        transport,
                        observed,
                    ),
                )
                self._remaining_ms(deadline)
            return data
        except Error as error:
            if (
                not error.sqlstate
                or error.sqlstate.startswith("08")
                or error.sqlstate == "55000"
            ):
                raise _pending() from None
            if error.diag.message_primary == "checkpoint_branch_conflict":
                raise _Failure("invalid_request", "branch_conflict") from None
            if error.sqlstate == "22023":
                raise _Failure("invalid_request", "binding") from None
            raise _disclosure_failure(error) from None

    @staticmethod
    def _check_checkpoint_request(
        request: CheckpointRequest, manifest: CheckpointManifest
    ) -> None:
        if (
            manifest.question != request.question
            or manifest.progress != request.progress
            or manifest.findings != request.findings
            or tuple(root.result for root in manifest.roots)
            != tuple(root.result for root in request.roots)
            or manifest.predecessor != request.expected_head
            or manifest.fork_origin is not None
            or manifest.working_state_source is not None
            or (
                request.investigation_id is not None
                and (
                    manifest.investigation_id != request.investigation_id
                    or manifest.branch_id != request.branch_id
                )
            )
        ):
            raise ValueError("checkpoint request/manifest substitution")

    @staticmethod
    def _checkpoint_payload(
        request: CheckpointRequest | ReadCheckpointRequest,
        packet: dict[str, Any],
    ) -> tuple[CheckpointManifest, dict[str, Any]]:
        if set(packet) != {"checkpoint", "retention", "root_contexts"}:
            raise ValueError("unexpected checkpoint payload fields")
        manifest = CheckpointManifest.model_validate(packet["checkpoint"])
        digest = manifest.content_digest()
        pin = {"checkpoint_id": manifest.checkpoint_id, "manifest_digest": digest}
        if isinstance(request, CheckpointRequest):
            PostgresAgentSqlResults._check_checkpoint_request(request, manifest)
        elif (
            not isinstance(request.access.basis, AutomaticBasis)
            or request.access.checkpoint.model_dump(mode="json") != pin
        ):
            raise ValueError("checkpoint read pin/basis substitution")
        expected = [
            CheckpointResultAccess(
                kind="checkpoint",
                checkpoint_id=manifest.checkpoint_id,
                manifest_digest=digest,
                root=root.result,
                basis=AutomaticBasis(kind="automatic"),
            ).model_dump(mode="json")
            for root in manifest.roots
        ]
        contexts = [
            CheckpointResultAccess.model_validate(c).model_dump(mode="json")
            for c in packet["root_contexts"]
        ]
        if len(contexts) != len(expected) or {
            result_json_bytes(c) for c in contexts
        } != {result_json_bytes(c) for c in expected}:
            raise ValueError("checkpoint root context substitution")
        retention = packet["retention"]
        if (
            not isinstance(retention, dict)
            or set(retention)
            != {
                "automatic_until",
                "automatic_retained",
                "named_holds",
                "retained",
                "hold_ref",
                "unique_bytes",
                "context_end",
            }
            or retention["automatic_until"] != manifest.automatic_expires_at
            or retention["automatic_retained"] is not True
            or retention["retained"] is not True
            or retention["named_holds"] != []
            or retention["hold_ref"] != manifest.checkpoint_id
            or retention["context_end"] != "automatic_expiry_or_prune"
            or type(retention["unique_bytes"]) is not str
            or not retention["unique_bytes"].isascii()
            or not retention["unique_bytes"].isdigit()
            or int(retention["unique_bytes"]) > 536870912
        ):
            raise ValueError("invalid checkpoint retention state")
        extra: dict[str, Any] = {
            "checkpoint": manifest.model_dump(mode="json"),
            "retention": retention,
        }
        if isinstance(request, CheckpointRequest):
            extra["branch"] = {"branch_id": manifest.branch_id, "head": pin}
        else:
            extra["root_contexts"] = contexts
        return manifest, extra

    def _helper(
        self,
        request: InspectRequest | HydrateRequest,
        fingerprint: str,
        admission: dict[str, Any],
        start: float,
        deadline: float,
        owner: Connection[Any],
    ) -> bytes:
        """Use the admitted delivery's owning backend for all helper work.

        Work is already durably reserved by admission. Helper bytes, their
        exact preparation charge, step and disclosure publish in one fenced
        transaction. No untracked reader or owner-only inspector is used.
        """
        from memoriesql.application.agent_sql_helpers import (
            UnsupportedHelperProvenance,
            project_helper_provenance,
            validate_helper_content,
        )

        delivery = UUID(admission["delivery_ref"])
        try:
            with relation_projection_frame(
                owner,
                credential_sha256=self._credential,
                workspace_id=self._workspace,
                statement_timeout_ms=self._remaining_ms(deadline),
            ) as frame:

                def check_work() -> None:
                    self._remaining_ms(deadline)

                def execute(statement: str, values: tuple[Any, ...] = ()) -> Any:
                    frame.execute(
                        f"SET LOCAL statement_timeout='{self._remaining_ms(deadline)}ms'"
                    )
                    return frame.execute(statement, values)

                found = execute(
                    "SELECT memoriesql.query_helper_preparation_v1(%s)",
                    (delivery,),
                ).fetchone()
                existing = found[0] if found else None
                if existing is not None:
                    if existing["state"] != "sealed":
                        raise _Failure("execution_error", "database")
                    row = execute(
                        "SELECT memoriesql.read_query_helper_artifact_v1(%s,%s,%s)",
                        (
                            delivery,
                            UUID(existing["operation_ref"]),
                            UUID(existing["ownership_ref"]),
                        ),
                    ).fetchone()
                else:
                    row = execute(
                        "SELECT memoriesql.prepare_query_helper_v1(%s,%s)",
                        (delivery, Jsonb(request.model_dump(mode="json"))),
                    ).fetchone()
                if not row or not isinstance(row[0], dict):
                    raise _Failure("unavailable", "unavailable")
                packet = row[0]
                sealed = existing is not None or packet.get("artifact_ref") is not None
                try:
                    if (
                        not sealed
                        and isinstance(request, InspectRequest)
                        and packet["witnesses"].get("native_provenance") is not None
                    ):
                        graph = project_helper_provenance(
                            packet["witnesses"].pop("native_provenance"),
                            str(request.provenance_ref),
                            check_work=check_work,
                        )
                        packet["content"]["payload"]["inspection"]["provenance"] = graph
                    visible = validate_helper_content(request, packet["content"])
                except UnsupportedHelperProvenance:
                    raise _Failure("unsupported_query", "provenance") from None
                except (ValueError, KeyError, TypeError):
                    raise _Failure("execution_error", "provenance") from None
                if existing is not None:
                    packet.update(
                        {
                            key: existing[key]
                            for key in ("page_first", "page_count", "page_cursor")
                            if key in existing
                        }
                    )
                if not sealed:
                    packet["content"]["refs"] = visible
                elif packet["content"]["refs"] != visible:
                    # Never rewrite sealed bytes when a newer implementation
                    # cannot validate their original disclosure associations.
                    raise _Failure("unavailable", "unavailable")
                if not sealed:
                    content = PreparedContent.encode(
                        content=packet["content"],
                        witnesses=packet["witnesses"],
                        dependencies=packet["dependencies"],
                    )
                    capacity = max(8192, content.encoded_bytes + 8192)
                    if capacity > _POPULATION_BYTES:
                        raise _Failure("budget_exhausted", "storage")
                    try:
                        content.validate()
                    except ValueError:
                        raise _Failure("execution_error", "provenance") from None
                    reserved = execute(
                        "SELECT memoriesql.reserve_result_preparation_v1(%s,%s,%s,%s)",
                        (
                            UUID(request.run_ref),
                            UUID(request.step_key),
                            fingerprint,
                            capacity,
                        ),
                    ).fetchone()
                    if not reserved or reserved[0]["replayed"]:
                        raise _pending()
                    artifact = uuid4()
                    sealed = execute(
                        "SELECT memoriesql.seal_result_preparation_v1("
                        "%s,%s,%s,%s,%s,%s,%s,%s)",
                        (
                            UUID(reserved[0]["operation_ref"]),
                            UUID(reserved[0]["ownership_ref"]),
                            artifact,
                            content.content,
                            content.witnesses,
                            content.dependencies,
                            content.artifact_sha256,
                            Jsonb([]),
                        ),
                    ).fetchone()
                    if not sealed or not isinstance(sealed[0], dict):
                        raise _Failure("execution_error", "provenance")
                    packet["retained_bytes"] = int(sealed[0]["allocation_bytes"])
                else:
                    artifact = UUID(packet["artifact_ref"])
                before = execute(
                    "SELECT memoriesql.query_delivery_remaining_v1(%s)",
                    (delivery,),
                ).fetchone()
                known = execute(
                    "SELECT memoriesql.query_visible_refs_v1(%s,%s)",
                    (
                        delivery,
                        sorted(ref_key(ref) for ref in packet["content"]["refs"]),
                    ),
                ).fetchone()
                if not before:
                    raise _Failure("unavailable", "unavailable")
                packet["remaining_before"] = before[0]
                packet["previous_refs"] = known[0] if known else []
                data, transport, observed, page = self._helper_reply(
                    request, admission, start, packet, deadline
                )
                execute(
                    "SELECT memoriesql.record_query_helper_disclosure_v1("
                    "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        delivery,
                        artifact,
                        hashlib.sha256(
                            result_json_bytes(packet["content"])
                        ).hexdigest(),
                        Jsonb(sorted(ref_key(ref) for ref in page["refs"])),
                        hashlib.sha256(data).hexdigest(),
                        transport,
                        observed,
                        datetime.fromisoformat(
                            packet["checked_at"].replace("Z", "+00:00")
                        ),
                        page["first"],
                        page["count"],
                        UUID(page["cursor"]) if page["cursor"] else None,
                        page["next"],
                    ),
                )
                self._remaining_ms(deadline)
            return data
        except Error as error:
            if (
                not error.sqlstate
                or error.sqlstate.startswith("08")
                or error.sqlstate == "55000"
            ):
                # Recovery keeps the full work reservation until the SAME
                # backend has ended; a timeout/response never proves cessation.
                raise _pending() from None
            if error.sqlstate == "0A000":
                raise _Failure("unsupported_query", "provenance") from None
            if error.sqlstate == "22023":
                code = (
                    "cursor"
                    if error.diag.message_primary == "query_helper_cursor_invalid"
                    else "binding"
                )
                raise _Failure("invalid_request", code) from None
            raise _disclosure_failure(error) from None

    def _helper_reply(
        self,
        request: InspectRequest | HydrateRequest,
        admission: dict[str, Any],
        start: float,
        packet: dict[str, Any],
        deadline: float,
    ) -> tuple[bytes, int, int, dict[str, Any]]:
        from memoriesql.application.agent_sql_helpers import (
            helper_content_page,
            helper_item_count,
        )

        content = packet["content"]
        total = helper_item_count(request, content)
        first = packet.get("page_first", 1)
        exact_count = packet.get("page_count")
        maximum = total - first + 1
        if type(first) is not int or first < 1 or maximum < 0:
            raise _Failure("unavailable", "unavailable")
        if exact_count is not None and (
            type(exact_count) is not int or not 0 <= exact_count <= maximum
        ):
            raise _Failure("unavailable", "unavailable")
        cursor = packet.get("page_cursor") or str(uuid4())
        previous = set(packet["previous_refs"])
        limit = min(MAX_RESPONSE_BYTES, int(admission["reserved_transport"]))

        def encode(count: int) -> tuple[bytes, int, int, dict[str, Any]]:
            self._remaining_ms(deadline)
            more = count < maximum
            page = helper_content_page(
                request,
                content,
                first=first,
                count=count,
                cursor=cursor if more else None,
            )
            refs = {ref_key(ref): ref for ref in page["refs"]}
            observed = math.ceil((time.monotonic() - start) * 1000)
            data, transport = stable_reply_bytes(
                lambda transport, remaining: reply(
                    run_ref=request.run_ref,
                    step_key=request.step_key,
                    outcome="available",
                    receipt_ref=admission["receipt_ref"],
                    access_receipt_ref=admission["delivery_ref"],
                    remaining=remaining,
                    extra={
                        **page["payload"],
                        "visibility": {
                            "new_refs": [
                                refs[k] for k in sorted(refs.keys() - previous)
                            ],
                            "previous_refs": [
                                refs[k] for k in sorted(refs.keys() & previous)
                            ],
                            "hydration_required": [
                                refs[k]
                                for k in sorted(refs)
                                if refs[k]["kind"] == "source"
                            ]
                            if isinstance(request, InspectRequest)
                            else [],
                        },
                        "work": {
                            "reserved_ms": int(admission["reserved_db_ms"]),
                            "observed_db_ms": observed,
                            "charged_ms": observed,
                            "transport_bytes": transport,
                            "retained_bytes": packet["retained_bytes"],
                            "allocation_profile_hash": ALLOCATION_PROFILE_HASH,
                            "measured_physical_bytes": None,
                            "measurement_state": "unavailable",
                            "cancellation_state": "none",
                        },
                        "access_bases": [
                            {**basis, "checked_at": packet["checked_at"]}
                            for basis in content["access_bases"]
                        ],
                        "next_cursor": cursor if more else None,
                        "has_more": more,
                    },
                ),
                charge={"db_ms": observed},
                remaining_before=packet["remaining_before"],
            )
            return (
                data,
                transport,
                observed,
                {
                    "first": first,
                    "count": count,
                    "cursor": cursor if more else None,
                    "next": first + count if more else None,
                    "refs": page["refs"],
                },
            )

        # Exact redelivery cannot silently change the previously recorded page.
        candidate = encode(maximum if exact_count is None else exact_count)
        if candidate[1] <= limit and (maximum == 0 or candidate[3]["count"] > 0):
            return candidate
        if exact_count is not None or maximum < 2:
            raise _Failure("budget_exhausted", "transport")
        # The complete page was checked first. All remaining candidates carry
        # the same cursor; encoded size grows monotonically with complete atoms.
        lower, upper = 1, maximum - 1
        best = None
        while lower <= upper:
            count = (lower + upper) // 2
            candidate = encode(count)
            if candidate[1] <= limit:
                best = candidate
                lower = count + 1
            else:
                upper = count - 1
        if best is None:
            raise _Failure("budget_exhausted", "transport")
        return best

    def _query(
        self,
        request: QueryRequest,
        fingerprint: str,
        admission: dict[str, Any],
        start: float,
        deadline: float,
    ) -> bytes:
        # A redelivered step that already holds a preparation is only ever
        # recovered, never screened or run again.
        prepared = self._step_preparation(request) if admission["replay"] else None
        if prepared == "discarded":
            # This step's single-use preparation is gone (for example, its
            # commit was refused after its owner ended): a known execution
            # failure, never a rerun. The client uses a new step key.
            raise _Failure("execution_error", "database")
        if prepared is None:
            self._refuse_unsupported(request)
        try:
            store_connection = self._control()
        except Error:
            if prepared is not None:
                raise _pending() from None
            raise
        with store_connection:
            store = PostgresResultPreparation(
                store_connection,
                credential_sha256=self._credential,
                workspace_id=self._workspace,
            )
            try:
                ownership = store.reserve(
                    run_ref=UUID(request.run_ref),
                    step_key=UUID(request.step_key),
                    request_fingerprint=fingerprint,
                    reservation_bytes=max(8192, request.max_result_bytes),
                )
            except Exception as error:
                if prepared is not None:
                    # The step's earlier work was not found out.
                    raise _pending() from None
                if not isinstance(error, Error):
                    raise
                code = _state_code(error) or "database"
                raise _Failure(
                    "idempotency_conflict"
                    if code == "idempotency"
                    else "unavailable"
                    if code == "unavailable"
                    else "execution_error"
                    if code == "arithmetic"
                    else "budget_exhausted",
                    code,
                ) from None
            if ownership.replayed:
                return self._recover_query(
                    request, store, ownership, admission, start, deadline
                )
            try:
                return self._execute(
                    request, store, ownership, admission, start, deadline
                )
            except _Failure as failure:
                if not failure.pending and not failure.committed:
                    self._discard(store, ownership)
                raise

    def _execute(
        self,
        request: QueryRequest,
        store: PostgresResultPreparation,
        ownership: Any,
        admission: dict[str, Any],
        start: float,
        deadline: float,
    ) -> bytes:
        known_at = datetime.fromisoformat(request.scope.known_at.replace("Z", "+00:00"))
        parameters = self._parameters(request)
        recursion = self._recursion(request)
        # Once the commit is sent its outcome may be unknown: from then on a
        # failure neither discards the preparation nor fails the step.
        committing = False
        committed = False
        try:
            with self._control() as source:
                with prepare_query_population(
                    source,
                    credential_sha256=self._credential,
                    workspace_id=self._workspace,
                    known_at=known_at,
                    view=request.scope.view,
                    byte_budget=_POPULATION_BYTES,
                    statement_timeout_ms=self._remaining_ms(deadline),
                ) as population:
                    execution = self._executor.execute(
                        source,
                        population,
                        ownership,
                        request.sql,
                        parameters,
                        recursion=recursion,
                        budget_ms=self._remaining_ms(deadline),
                        max_output_bytes=max(8192, request.max_result_bytes),
                        collect_bag_witnesses=True,
                    )
                    if (
                        execution.outcome == "settlement_pending"
                        and execution.invocation_ref is None
                        and execution.error == "settlement"
                    ):
                        # Staging refused because other work is unsettled; this
                        # step dispatched nothing and its own reservation ends.
                        raise _Failure("budget_exhausted", "settlement")
                    if execution.outcome != "complete":
                        code, feature = _admission_error(
                            execution.outcome, execution.error
                        )
                        raise _Failure(
                            execution.outcome,
                            "settlement"
                            if execution.outcome == "settlement_pending"
                            else code,
                            feature=feature,
                            position=execution.error_position,
                            pending=execution.outcome == "settlement_pending",
                        )
                    try:
                        candidate = InternalResultCandidate.construct(
                            request, population, execution, policy_hash=POLICY_HASH
                        )
                    except ValueError as error:
                        text = str(error)
                        if "exhausted" not in text:
                            raise _Failure("execution_error", "provenance") from None
                        raise _Failure(
                            "budget_exhausted",
                            "storage" if "storage" in text else "time",
                        ) from None
                    with self._control() as commit_connection:
                        committing = True
                        try:
                            receipt = PostgresQueryResultCommit(
                                commit_connection,
                                credential_sha256=self._credential,
                                workspace_id=self._workspace,
                            ).commit(ownership, candidate)
                        except Error as error:
                            known = _state_code(error)
                            if known is None:
                                # Commit acknowledgement is uncertain; never
                                # refund or retry, recover the owned receipt.
                                raise _Failure(
                                    "settlement_pending", "settlement", pending=True
                                ) from None
                            if (
                                known == "unavailable"
                                and error.diag.message_primary
                                == "query_result_unavailable"
                            ):
                                # The commit's own ownership, issuer or
                                # invocation checks refused this single-use
                                # preparation; lost authority is reported by
                                # the authority fence instead.
                                raise _Failure("execution_error", "database") from None
                            raise _Failure(
                                "unavailable"
                                if known == "unavailable"
                                else "execution_error"
                                if known == "arithmetic"
                                else "budget_exhausted",
                                known,
                            ) from None
                        committed = receipt.get("state") == "committed"
        except _Failure:
            raise
        except RelationPopulationError as error:
            if committing:
                raise _pending() from None
            raise _Failure(
                {
                    "unavailable": "unavailable",
                    "budget_exhausted": "budget_exhausted",
                    "invalid_request": "invalid_request",
                }.get(error.code, "execution_error"),
                {
                    "unavailable": "unavailable",
                    "budget_exhausted": error.limit or "time",
                    "invalid_request": "time",
                }.get(error.code, "database"),
            ) from None
        except Exception as error:
            if committing:
                raise _pending() from None
            # Before any commit was sent nothing of this step can commit, so
            # its reservation is discarded, never held. A busy database is a
            # budget refusal; any other failure is the host's own.
            busy = _busy(error)
            if busy:
                raise _Failure("budget_exhausted", busy) from None
            raise _Failure("execution_error", "database") from None
        if not committed:
            raise _pending()
        try:
            return self._disclose(
                request,
                admission,
                start,
                deadline,
                candidate.result_id,
                candidate.content_digest,
                1,
                request.page_size,
            )
        except _Failure as failure:
            # Committed results are immutable: the exact redelivery discloses
            # them later under current authority, never a rerun.
            raise _committed(failure) from None
        except Exception:
            raise _pending() from None

    @staticmethod
    def _parameters(request: QueryRequest) -> tuple[Any, ...]:
        catalog = SqlCatalog.installed()
        try:
            return tuple(p.sql_parameter(catalog) for p in request.parameters)
        except InvestigationRequestError as error:
            raise _Failure(error.outcome, error.code) from None

    @staticmethod
    def _recursion(request: QueryRequest) -> RecursionBound | None:
        if not request.recursion:
            return None
        return RecursionBound(
            request.recursion.cte,
            request.recursion.depth_column,
            request.recursion.node_column,
            request.recursion.max_depth,
        )

    def _step_preparation(self, request: QueryRequest) -> str | None:
        """The caller's own preparation state for a redelivered step."""
        try:
            with self._control() as connection:
                with relation_projection_frame(
                    connection,
                    credential_sha256=self._credential,
                    workspace_id=self._workspace,
                ) as frame:
                    row = frame.execute(
                        "SELECT memoriesql.query_step_preparation_v1(%s,%s)",
                        (UUID(request.run_ref), UUID(request.step_key)),
                    ).fetchone()
        except (PermissionError, Error):
            # Whether the step's earlier work committed is unknown: never a
            # failure without proof.
            raise _pending() from None
        return str(row[0]) if row and row[0] is not None else None

    def _refuse_unsupported(self, request: QueryRequest) -> None:
        """Refuse what this cut does not run, before any reservation."""
        feature = (
            "saved_input_refinement"
            if request.inputs
            else "live_expansion"
            if request.parents
            else "candidate_profile"
            if request.candidate_profile_ref
            else "source_scope"
            if request.scope.source_refs
            else None
        )
        if feature is not None or request.intent not in {"discover", "enumerate"}:
            raise _Failure(
                "unsupported_query", "feature", feature=feature or request.intent
            )
        self._screen(request)

    def _screen(self, request: QueryRequest) -> None:
        """Refuse what the SQL and its bound values alone decide, before any
        reservation or preparation.

        Whether a bound identifier is visible depends on the prepared population.
        The request's own identifiers therefore stand in as anchors here, which
        admits nothing: the full admission after preparation still requires each
        one to be visible, and only that admission is executed.
        """
        parameters = self._parameters(request)
        references = SqlCatalog.installed().reference_types
        own = frozenset(
            (kind, value)
            for parameter in parameters
            if (kind := parameter.type.removesuffix("[]")) in references
            for value in (
                parameter.value
                if isinstance(parameter.value, tuple)
                else (parameter.value,)
            )
            if isinstance(value, str)
        )
        try:
            screened = admit_query(
                request.sql,
                parameters,
                admitted_anchors=own,
                recursion=self._recursion(request),
            )
            if not set(screened.relations) <= PREPARED_RELATIONS:
                raise SqlAdmissionError("unsupported", "unprepared_relation")
            require_reviewed(screened, _REVIEWED)
        except SqlAdmissionError as error:
            if error.construct == "parameter_anchor":
                return
            outcome = "unsupported_query" if error.code == "unsupported" else error.code
            code, feature = _admission_error(outcome, error.construct)
            raise _Failure(
                outcome,
                code,
                feature=feature,
                # Every unavailable reply has one shape.
                position=None if outcome == "unavailable" else error.position,
            ) from None

    def _recover_query(
        self,
        request: QueryRequest,
        store: PostgresResultPreparation,
        ownership: Any,
        admission: dict[str, Any],
        start: float,
        deadline: float,
    ) -> bytes:
        """A redelivered step whose earlier owner ended: never rerun the query.

        Only proof settles the step: a committed result is disclosed, and a
        preparation that can no longer commit is an execution error. A failure
        while finding out which leaves it `settlement_pending`.
        """
        if ownership.state not in ("sealed", "reserved"):
            raise _Failure("execution_error", "database")
        try:
            if ownership.state == "reserved":
                settlement = self._executor.recover(ownership, cancel=True)
                if settlement is not None and settlement.state != "settled":
                    raise _pending()
                self._discard(store, ownership)
                raise _Failure("execution_error", "database")
            with self._control() as connection:
                receipt = PostgresQueryResultCommit(
                    connection,
                    credential_sha256=self._credential,
                    workspace_id=self._workspace,
                ).recover(ownership)
        except _Failure:
            raise
        except Exception:
            raise _pending() from None
        if receipt.get("state") != "committed":
            raise _pending()
        try:
            return self._disclose(
                request,
                admission,
                start,
                deadline,
                UUID(receipt["result_id"]),
                receipt["content_digest"],
                1,
                request.page_size,
            )
        except _Failure as failure:
            raise _committed(failure) from None
        except Exception:
            raise _pending() from None

    def _reuse(
        self,
        request: ReuseRequest,
        admission: dict[str, Any],
        start: float,
        deadline: float,
    ) -> bytes:
        context = request.access.model_dump(mode="json")
        first_row = 1
        if request.cursor is not None:
            try:
                cursor = UUID(request.cursor)
            except ValueError:
                raise _Failure("invalid_request", "cursor") from None
            try:
                with self._control() as connection:
                    with relation_projection_frame(
                        connection,
                        credential_sha256=self._credential,
                        workspace_id=self._workspace,
                        statement_timeout_ms=self._remaining_ms(deadline),
                    ) as frame:
                        row = frame.execute(
                            "SELECT memoriesql.resolve_query_cursor_v1(%s)", (cursor,)
                        ).fetchone()
                        binding = row[0] if row else None
                        if binding is None:
                            # Cleanup can remove a once-valid cursor. Classify
                            # its requested result under the existing whole
                            # authority fence, without disclosing a page or
                            # recording a disclosure for an invalid cursor.
                            contextual = isinstance(
                                request.access, CheckpointResultAccess
                            )
                            usable = frame.execute(
                                "SELECT memoriesql.query_result_access_v1(%s,%s,%s)"
                                if contextual
                                else "SELECT memoriesql.check_query_result_closure_v2(%s,%s)",
                                (
                                    UUID(request.result.result_id),
                                    request.result.content_digest,
                                )
                                + ((Jsonb(context),) if contextual else ()),
                            ).fetchone()
                            if usable is None or (
                                not contextual and usable[0] is not True
                            ):
                                raise _Failure("unavailable", "unavailable")
            except PermissionError:
                raise _Failure("unavailable", "unavailable") from None
            except Error as error:
                raise _disclosure_failure(error) from None
            if (
                binding is None
                or binding["result_id"] != request.result.result_id
                or binding["content_digest"] != request.result.content_digest
                or binding["access_context"] != context
            ):
                raise _Failure("invalid_request", "cursor")
            first_row = int(binding["next_row"])
        return self._disclose(
            request,
            admission,
            start,
            deadline,
            UUID(request.result.result_id),
            request.result.content_digest,
            first_row,
            request.page_size,
        )

    def _disclose(
        self,
        request: QueryRequest | ReuseRequest,
        admission: dict[str, Any],
        start: float,
        deadline: float,
        result_id: UUID,
        digest: str,
        first_row: int,
        page_size: int,
        *,
        exact_rows: int | None = None,
    ) -> bytes:
        delivery = UUID(admission["delivery_ref"])
        limit = min(MAX_RESPONSE_BYTES, int(admission["reserved_transport"]))
        contextual = isinstance(request, ReuseRequest) and isinstance(
            request.access, CheckpointResultAccess
        )
        context = (
            request.access.model_dump(mode="json")
            if isinstance(request, ReuseRequest)
            else {"kind": "standalone"}
        )
        with self._control() as connection:
            try:
                with relation_projection_frame(
                    connection,
                    credential_sha256=self._credential,
                    workspace_id=self._workspace,
                    statement_timeout_ms=self._remaining_ms(deadline),
                ) as frame:
                    row = frame.execute(
                        "SELECT memoriesql.read_query_result_page_v2(%s,%s,%s,%s,%s,%s)"
                        if contextual
                        else "SELECT memoriesql.read_query_result_page_v1(%s,%s,%s,%s,%s)",
                        (delivery, result_id, digest, first_row, page_size)
                        + ((Jsonb(context),) if contextual else ()),
                    ).fetchone()
                    if row is None:
                        raise _Failure("unavailable", "unavailable")
                    page = row[0]
                    data, transport, response = self._page_reply(
                        request,
                        admission,
                        start,
                        page,
                        result_id,
                        digest,
                        first_row,
                        page_size,
                        limit,
                        exact_rows,
                        frame,
                    )
                    frame.execute(
                        (
                            "SELECT memoriesql.record_query_disclosure_v2("
                            "%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s,%s)"
                        )
                        if contextual
                        else (
                            "SELECT memoriesql.record_query_disclosure_v1("
                            "%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s)"
                        ),
                        (
                            delivery,
                            result_id,
                            digest,
                            first_row,
                            response["rows"],
                            page_size,
                            response["delivered_json"],
                            hashlib.sha256(data).hexdigest(),
                            transport,
                            response["observed"],
                            datetime.fromisoformat(
                                page["checked_at"].replace("Z", "+00:00")
                            ),
                            UUID(response["cursor"]) if response["cursor"] else None,
                            response["next_row"],
                        )
                        + ((Jsonb(context),) if contextual else ()),
                    )
            except Error as error:
                if error.diag.message_primary == "query_delivery_settled":
                    # Recovery settled this access while it ran: its outcome
                    # belongs to the exact redelivery, never a false failure.
                    raise _Failure(
                        "settlement_pending", "settlement", pending=True
                    ) from None
                raise _disclosure_failure(error) from None
            except PermissionError:
                raise _Failure("unavailable", "unavailable") from None
        return data

    def _page_reply(
        self,
        request: QueryRequest | ReuseRequest,
        admission: dict[str, Any],
        start: float,
        page: dict[str, Any],
        result_id: UUID,
        digest: str,
        first_row: int,
        page_size: int,
        limit: int,
        exact_rows: int | None,
        frame: Connection[Any],
    ) -> tuple[bytes, int, dict[str, Any]]:
        meta = page["metadata"]
        schema = meta["result_schema"]
        total = int(page["total_rows"])
        index = EvidenceIndex(
            page["evidence"]["logical_rows"], page["evidence"]["bindings"]
        )
        rows = [
            {
                "row_ref": item["row_ref"],
                "values": item["values"],
                "fact_ref": {"result_id": str(result_id), "row_ref": item["row_ref"]},
                "evidence_refs": index.refs(schema, item["values"]),
                "provenance_ref": item["provenance_ref"],
            }
            for item in page["rows"]
        ]
        # Delivering a row makes its evidence and its own fact visible. An
        # aggregate's fact makes none of its contributors visible.
        visible = [
            [*row["evidence_refs"], {"kind": "result_fact", "fact": row["fact_ref"]}]
            for row in rows
        ]
        keys = sorted({ref_key(ref) for row_refs in visible for ref in row_refs})
        known = frame.execute(
            "SELECT memoriesql.query_visible_refs_v1(%s,%s)",
            (UUID(admission["delivery_ref"]), keys),
        ).fetchone()
        previous_keys = set(known[0] if known else ())
        result = {
            "result_id": str(result_id),
            "content_digest": digest,
            "originating_run_ref": str(page["originating_run_ref"]),
            "created_at": page["created_at"],
            "expires_at": page["expires_at"],
            "catalog_hash": meta["catalog_hash"],
            "policy_hash": meta["policy_hash"],
            "result_schema": schema,
            "frame": meta["wire_frame"],
            "parents": [],
            "query_ref": meta["query_ref"],
            "lineage_ref": meta["lineage_ref"],
            "coverage": meta["coverage"],
            "order_basis": meta["order_basis"],
            "total_rows": meta["total_rows"],
        }
        access_bases = [
            {
                "context": {"kind": "standalone"},
                "checked_at": page["checked_at"],
                "valid_until": page["expires_at"],
                "end_condition": "standalone_expiry",
                "hold_ref": None,
            }
        ]
        if isinstance(request, ReuseRequest) and isinstance(
            request.access, CheckpointResultAccess
        ):
            access_bases = page["access_bases"]
            if (
                not isinstance(access_bases, list)
                or len(access_bases) != 1
                or access_bases[0]["context"] != request.access.model_dump(mode="json")
                or access_bases[0]["end_condition"] != "checkpoint_expiry_or_prune"
                or access_bases[0]["valid_until"] is None
                or access_bases[0]["hold_ref"] != request.access.checkpoint_id
            ):
                raise _Failure("execution_error", "database")
        cursor = str(uuid4())
        count = len(rows) if exact_rows is None else exact_rows
        while True:
            if exact_rows is not None and len(rows) < exact_rows:
                raise _Failure("execution_error", "database")
            shown = rows[:count]
            refs = {ref_key(r): r for row_refs in visible[:count] for r in row_refs}
            new = [refs[k] for k in sorted(refs) if k not in previous_keys]
            old = [refs[k] for k in sorted(refs) if k in previous_keys]
            next_row = first_row + count if first_row + count <= total else None
            observed = math.ceil((time.monotonic() - start) * 1000)

            def build(transport: int, remaining: dict[str, Any]) -> dict[str, Any]:
                return reply(
                    run_ref=request.run_ref,
                    step_key=request.step_key,
                    outcome="available",
                    receipt_ref=admission["receipt_ref"],
                    access_receipt_ref=admission["delivery_ref"],
                    remaining=remaining,
                    extra={
                        "result": result,
                        "page": {
                            "rows": shown,
                            "next_cursor": cursor if next_row is not None else None,
                            "has_more": next_row is not None,
                        },
                        "visibility": {
                            "new_refs": new,
                            "previous_refs": old,
                            "hydration_required": [
                                r for r in new + old if r["kind"] == "source"
                            ],
                        },
                        "work": {
                            "reserved_ms": int(admission["reserved_db_ms"]),
                            "observed_db_ms": observed,
                            "charged_ms": observed,
                            "transport_bytes": transport,
                            "retained_bytes": int(page["retained_bytes"]),
                            "allocation_profile_hash": ALLOCATION_PROFILE_HASH,
                            "measured_physical_bytes": None,
                            "cancellation_state": "none",
                            "measurement_state": "unavailable",
                        },
                        "access_bases": access_bases,
                    },
                )

            data, transport = stable_reply_bytes(
                build,
                charge={"db_ms": observed},
                remaining_before=page["remaining_before"],
            )
            # An empty page is only a real answer when no rows remain; otherwise
            # its cursor would point back at the same row forever.
            if transport <= limit and (count > 0 or not rows):
                return (
                    data,
                    transport,
                    {
                        "rows": count,
                        "delivered_json": result_json_bytes(sorted(refs)).decode(
                            "ascii"
                        ),
                        "observed": observed,
                        "cursor": cursor if next_row is not None else None,
                        "next_row": next_row,
                    },
                )
            if exact_rows is not None or count == 0:
                # A single oversized row needs a bounded read, never half a row.
                raise _Failure("budget_exhausted", "transport")
            count -= 1

    # -- settlement and helpers ---------------------------------------------

    def _settle(
        self,
        request: AccessRequest,
        admission: dict[str, Any],
        start: float,
        failure: _Failure,
    ) -> bytes:
        delivery = UUID(admission["delivery_ref"])
        try:
            before = self._owned(
                "SELECT memoriesql.query_delivery_remaining_v1(%s)", (delivery,)
            )
        except Error:
            # Nothing is recorded: recovery or the exact redelivery settles it.
            return self._pending_reply(request, admission, None)
        if failure.pending:
            data = self._pending_reply(request, admission, before)
            try:
                self._owned(
                    "SELECT memoriesql.settle_query_delivery_v1(%s,%s,%s,%s,%s,%s,%s)",
                    (delivery, None, None, None, None, None, True),
                )
            except Error:
                pass  # still unsettled, so it blocks until recovery settles it
            return data
        observed = math.ceil((time.monotonic() - start) * 1000)
        data, transport = stable_reply_bytes(
            lambda transport, remaining: reply(
                run_ref=request.run_ref,
                step_key=request.step_key,
                outcome=failure.outcome,
                receipt_ref=admission["receipt_ref"],
                access_receipt_ref=admission["delivery_ref"],
                remaining=remaining,
                error=failure.error(),
            ),
            charge={"db_ms": observed},
            remaining_before=before,
        )
        try:
            self._owned(
                "SELECT memoriesql.settle_query_delivery_v1(%s,%s,%s,%s,%s,%s,%s)",
                (
                    delivery,
                    failure.outcome,
                    failure.code,
                    observed,
                    transport,
                    hashlib.sha256(data).hexdigest(),
                    False,
                ),
            )
        except Error:
            # The outcome may be unrecorded: the exact redelivery replays it if
            # it was recorded and recovers the step if it was not.
            return self._pending_reply(request, admission, before)
        return data

    @staticmethod
    def _pending_reply(
        request: AccessRequest,
        admission: dict[str, Any],
        before: dict[str, Any] | None,
    ) -> bytes:
        """`settlement_pending`, charged the full reservation, as recovery will.

        Without the delivery's own remaining allowance, the admission's stands.
        """
        return result_json_bytes(
            reply(
                run_ref=request.run_ref,
                step_key=request.step_key,
                outcome="settlement_pending",
                receipt_ref=admission["receipt_ref"],
                access_receipt_ref=admission["delivery_ref"],
                remaining=remaining_after(
                    admission["remaining"] if before is None else before,
                    db_ms=int(admission["reserved_db_ms"]),
                    transport_bytes=int(admission["reserved_transport"]),
                ),
                error={"code": "settlement"},
            )
        )

    def _admit(
        self, request: AccessRequest, kind: str, fingerprint: str
    ) -> dict[str, Any]:
        if (
            isinstance(request, InspectRequest | HydrateRequest)
            and not _helper_module_available()
        ):
            raise _Failure("unsupported_query", "feature", feature=request.kind)
        with self._control() as connection:
            with relation_projection_frame(
                connection,
                credential_sha256=self._credential,
                workspace_id=self._workspace,
                query_access=True,
            ) as frame:
                checkpoint_access = (
                    isinstance(request, CheckpointRequest | ReadCheckpointRequest)
                    or (
                        isinstance(request, ReuseRequest)
                        and isinstance(request.access, CheckpointResultAccess)
                    )
                    or (
                        isinstance(request, InspectRequest)
                        and request.result is not None
                        and isinstance(request.result.access, CheckpointResultAccess)
                    )
                    or (
                        isinstance(request, HydrateRequest)
                        and any(
                            s.origin is not None
                            and isinstance(s.origin.access, CheckpointResultAccess)
                            for s in request.selections
                        )
                    )
                )
                if checkpoint_access:
                    supported = frame.execute(
                        _CHECKPOINT_BACKEND_CAPABILITY_SQL
                    ).fetchone()
                    if not supported or supported[0] is not True:
                        raise _Failure(
                            "unsupported_query", "feature", feature=request.kind
                        )
                if isinstance(request, InspectRequest | HydrateRequest):
                    supported = frame.execute(_HELPER_BACKEND_CAPABILITY_SQL).fetchone()
                    if not supported or supported[0] is not True:
                        raise _Failure(
                            "unsupported_query", "feature", feature=request.kind
                        )
                row = frame.execute(
                    "SELECT memoriesql.admit_query_delivery_v1(%s,%s,%s,%s,%s,%s)",
                    (
                        UUID(request.run_ref),
                        UUID(request.step_key),
                        kind,
                        fingerprint,
                        30000,
                        MAX_RESPONSE_BYTES,
                    ),
                ).fetchone()
        if row is None or row[0] is None:
            raise PermissionError("query admission unavailable")
        admission: dict[str, Any] = row[0]
        return admission

    def _owned(self, statement: str, values: tuple[Any, ...]) -> Any:
        """Blind owned bookkeeping; discloses nothing and grants no new work."""
        with self._control() as connection:
            with connection.transaction():
                connection.execute("SET LOCAL ROLE memoriesql_application")
                connection.execute("SET LOCAL lock_timeout='500ms'")
                connection.execute("SET LOCAL statement_timeout='2500ms'")
                row = connection.execute(statement, values).fetchone()
        return row[0] if row else None

    def _cleanup_workspace(self) -> None:
        """One bounded owned expiry-cleanup pass for this workspace.

        Item failures are recorded by the database and retried on every pass;
        a missed or failed pass is enforced by the run-start overdue refusal.
        """
        try:
            with self._control() as connection:
                with connection.transaction():
                    connection.execute("SET LOCAL lock_timeout='500ms'")
                    connection.execute("SET LOCAL statement_timeout='10000ms'")
                    connection.execute("SET LOCAL ROLE memoriesql_application")
                    PostgresAuthorizationPort(connection).begin_context(
                        credential_sha256=self._credential,
                        requested_workspace_id=self._workspace,
                    )
                    connection.execute(
                        "SELECT memoriesql.purge_workspace_query_state_v1(8)"
                    )
                with connection.transaction():
                    connection.execute("SET LOCAL lock_timeout='500ms'")
                    connection.execute("SET LOCAL statement_timeout='10000ms'")
                    connection.execute("SET LOCAL ROLE memoriesql_application")
                    PostgresAuthorizationPort(connection).begin_context(
                        credential_sha256=self._credential,
                        requested_workspace_id=self._workspace,
                    )
                    if connection.execute(_AUDIT_RETENTION_SQL).fetchone() == (True,):
                        connection.execute(
                            "SELECT memoriesql.expire_workspace_query_audits_v1(%s)",
                            (_AUDIT_EXPIRY_ROWS,),
                        )
        except (PermissionError, Error):
            pass

    def _discard(self, store: PostgresResultPreparation, ownership: Any) -> None:
        try:
            store.discard(
                operation_ref=ownership.operation_ref,
                ownership_ref=ownership.ownership_ref,
            )
        except (Error, PermissionError, ValueError):
            # An undiscarded reservation stays charged; it is never refunded
            # while any remote work or commit could still be pending.
            pass

    @staticmethod
    def _remaining_ms(deadline: float) -> int:
        remaining = math.floor((deadline - time.monotonic()) * 1000)
        if remaining < 1:
            raise _Failure("budget_exhausted", "time")
        return min(30000, remaining)

    @staticmethod
    def _bare(
        run_ref: str | None,
        step_key: str | None,
        outcome: str,
        error: dict[str, Any] | None,
        remaining: dict[str, Any] | None,
    ) -> bytes:
        return result_json_bytes(
            reply(
                run_ref=run_ref,
                step_key=step_key,
                outcome=outcome,
                receipt_ref=None,
                access_receipt_ref=None,
                remaining=remaining,
                error=error,
            )
        )
