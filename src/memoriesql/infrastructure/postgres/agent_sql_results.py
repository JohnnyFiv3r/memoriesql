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
import math
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from psycopg import Connection, Error

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
    InvestigationRequestError,
    QueryRequest,
    ReuseRequest,
    StandaloneAccess,
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
)

ConnectionFactory = Callable[[], Connection[Any]]

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
}
_STATE_CODES = {
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


def _busy(error: BaseException) -> str | None:
    """The safe code of a failure the caller may simply try again, else None.

    A lock or statement timeout is `time`, a storage or work limit is `storage`
    and a conflict with work still in flight is `settlement`. The packet reports
    these as `budget_exhausted`, never as missing authority.
    """
    code = _STATE_CODES.get(getattr(error, "sqlstate", None) or "")
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


def _disclosure_failure(error: Error) -> _Failure:
    """What a database error means while resolving or disclosing a result."""
    code = _STATE_CODES.get(error.sqlstate or "", "database")
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
        self._control = control_factory
        self._credential = credential_sha256
        self._workspace = workspace_id
        self._executor = PostgresRestrictedQuery(
            reader_factory=reader_factory,
            control_factory=control_factory,
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
        if not isinstance(request, QueryRequest | ReuseRequest):
            return self._bare(
                request.run_ref,
                request.step_key,
                "unsupported_query",
                {"code": "feature", "feature": request.kind},
                None,
            )
        kind = "query" if isinstance(request, QueryRequest) else "reuse_result"
        fingerprint = request_fingerprint(request)
        admission: dict[str, Any] = {}
        for attempt in range(2):
            try:
                admission = self._admit(request, kind, fingerprint)
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
                # abandoned (full reservation charged); a live one stays pending.
                if self._owned(
                    "SELECT memoriesql.abandon_query_delivery_v1(%s)",
                    (UUID(admission["blocking_delivery"]),),
                ):
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
        request: QueryRequest | ReuseRequest,
        kind: str,
        fingerprint: str,
        admission: dict[str, Any],
        start: float,
    ) -> bytes:
        deadline = start + int(admission["reserved_db_ms"]) / 1000
        key = int(admission["owner_lock_key"])
        owner = self._control()
        try:
            # Session-scoped liveness: recovery may abandon this delivery only
            # after this owning session has ended.
            owner.execute("SELECT pg_advisory_lock(%s)", (key,))
            step = admission["step"]
            try:
                if admission["replay"] and step["state"] == "failed":
                    raise _Failure(step["outcome"], step["error_code"])
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

    def _query(
        self,
        request: QueryRequest,
        fingerprint: str,
        admission: dict[str, Any],
        start: float,
        deadline: float,
    ) -> bytes:
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
        if admission["replay"] and self._step_preparation(request) == "discarded":
            # This step's single-use preparation is gone (for example, its
            # commit was refused after its owner ended): a known execution
            # failure, never a rerun. The client uses a new step key.
            raise _Failure("execution_error", "database")
        with self._control() as store_connection:
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
            except Error as error:
                code = _STATE_CODES.get(error.sqlstate or "", "database")
                raise _Failure(
                    "idempotency_conflict"
                    if code == "idempotency"
                    else "unavailable"
                    if code == "unavailable"
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
                        try:
                            receipt = PostgresQueryResultCommit(
                                commit_connection,
                                credential_sha256=self._credential,
                                workspace_id=self._workspace,
                            ).commit(ownership, candidate)
                        except Error as error:
                            known = _STATE_CODES.get(error.sqlstate or "")
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
                                else "budget_exhausted",
                                known,
                            ) from None
                        committed = receipt.get("state") == "committed"
        except RelationPopulationError as error:
            raise _Failure(
                {
                    "unavailable": "unavailable",
                    "budget_exhausted": "budget_exhausted",
                    "invalid_request": "invalid_request",
                }.get(error.code, "execution_error"),
                {
                    "unavailable": "unavailable",
                    "budget_exhausted": "time",
                    "invalid_request": "time",
                }.get(error.code, "database"),
            ) from None
        if not committed:
            raise _Failure("settlement_pending", "settlement", pending=True)
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
            # Committed results are immutable: exact redelivery or reuse
            # discloses them later under current authority, never a rerun.
            failure.committed = True
            raise

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
            raise _Failure("unavailable", "unavailable") from None
        return str(row[0]) if row and row[0] is not None else None

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
        """A redelivered step whose earlier owner ended: never rerun the query."""
        if ownership.state == "sealed":
            with self._control() as connection:
                receipt = PostgresQueryResultCommit(
                    connection,
                    credential_sha256=self._credential,
                    workspace_id=self._workspace,
                ).recover(ownership)
            if receipt.get("state") == "committed":
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
            raise _Failure("settlement_pending", "settlement", pending=True)
        if ownership.state == "reserved":
            settlement = self._executor.recover(ownership, cancel=True)
            if settlement is not None and settlement.state != "settled":
                raise _Failure("settlement_pending", "settlement", pending=True)
            self._discard(store, ownership)
        raise _Failure("execution_error", "database")

    def _reuse(
        self,
        request: ReuseRequest,
        admission: dict[str, Any],
        start: float,
        deadline: float,
    ) -> bytes:
        if not isinstance(request.access, StandaloneAccess):
            # No checkpoint or named save exists in this contract cut.
            raise _Failure("unavailable", "unavailable")
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
                    ) as frame:
                        row = frame.execute(
                            "SELECT memoriesql.resolve_query_cursor_v1(%s)", (cursor,)
                        ).fetchone()
            except PermissionError:
                raise _Failure("unavailable", "unavailable") from None
            except Error as error:
                raise _disclosure_failure(error) from None
            binding = row[0] if row else None
            if (
                binding is None
                or binding["result_id"] != request.result.result_id
                or binding["content_digest"] != request.result.content_digest
                or binding["access_context"] != {"kind": "standalone"}
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
        with self._control() as connection:
            try:
                with relation_projection_frame(
                    connection,
                    credential_sha256=self._credential,
                    workspace_id=self._workspace,
                    statement_timeout_ms=self._remaining_ms(deadline),
                ) as frame:
                    row = frame.execute(
                        "SELECT memoriesql.read_query_result_page_v1(%s,%s,%s,%s,%s)",
                        (delivery, result_id, digest, first_row, page_size),
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
                        "SELECT memoriesql.record_query_disclosure_v1("
                        "%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s)",
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
                        ),
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
        keys = sorted({ref_key(ref) for row in rows for ref in row["evidence_refs"]})
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
        cursor = str(uuid4())
        count = len(rows) if exact_rows is None else exact_rows
        while True:
            if exact_rows is not None and len(rows) < exact_rows:
                raise _Failure("execution_error", "database")
            shown = rows[:count]
            refs = {ref_key(r): r for row in shown for r in row["evidence_refs"]}
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
        request: QueryRequest | ReuseRequest,
        admission: dict[str, Any],
        start: float,
        failure: _Failure,
    ) -> bytes:
        delivery = UUID(admission["delivery_ref"])
        before = self._owned(
            "SELECT memoriesql.query_delivery_remaining_v1(%s)", (delivery,)
        )
        if failure.pending:
            data = result_json_bytes(
                reply(
                    run_ref=request.run_ref,
                    step_key=request.step_key,
                    outcome="settlement_pending",
                    receipt_ref=admission["receipt_ref"],
                    access_receipt_ref=admission["delivery_ref"],
                    remaining=remaining_after(
                        before,
                        db_ms=int(admission["reserved_db_ms"]),
                        transport_bytes=int(admission["reserved_transport"]),
                    ),
                    error={"code": "settlement"},
                )
            )
            self._owned(
                "SELECT memoriesql.settle_query_delivery_v1(%s,%s,%s,%s,%s,%s,%s)",
                (delivery, None, None, None, None, None, True),
            )
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
        return data

    def _admit(
        self, request: QueryRequest | ReuseRequest, kind: str, fingerprint: str
    ) -> dict[str, Any]:
        with self._control() as connection:
            with relation_projection_frame(
                connection,
                credential_sha256=self._credential,
                workspace_id=self._workspace,
            ) as frame:
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
