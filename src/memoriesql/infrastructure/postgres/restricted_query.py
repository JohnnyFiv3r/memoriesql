"""Trusted restricted-login invocation, not an available query/result API.

The caller retains the canonical source frame and durable preparation owner.
Rows are private executor inputs until full witnesses/storage/disclosure qualify.
Provisioning and credential isolation require an independently reviewed host;
this module neither accepts agent credentials nor certifies that host boundary.
"""

from __future__ import annotations

import json
import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from psycopg import Connection, Error
from psycopg.pq import TransactionStatus
from psycopg.types.json import Jsonb

from memoriesql.application.agent_sql_admission import (
    RecursionBound,
    admit_query,
    require_reviewed,
)
from memoriesql.application.agent_sql_catalog import (
    SqlAdmissionError,
    SqlColumn,
    SqlParameter,
)
from memoriesql.application.agent_sql_witness import compile_bag_witness
from memoriesql.application.investigation_contracts import (
    encode_result_scalar,
    result_json_bytes,
)
from memoriesql.infrastructure.postgres.agent_sql_authority import (
    QueryAuthorityProfile,
    qualify_query_authority,
    verify_query_login,
)
from memoriesql.infrastructure.postgres.query_witness import (
    NativeWitnessBuilder,
    NativeWitnesses,
)
from memoriesql.infrastructure.postgres.relation_projection import (
    relation_projection_frame,
)
from memoriesql.infrastructure.postgres.relation_sql_population import (
    PreparedRelationPopulation,
)
from memoriesql.infrastructure.postgres.result_preparation import PreparationReceipt

ConnectionFactory = Callable[[], Connection[Any]]


@dataclass(frozen=True, slots=True)
class BackendTransaction:
    pid: int
    started_at: str
    virtual_transaction: str
    role_oid: int


@dataclass(frozen=True, slots=True)
class NativeQueryExecution:
    """Private native rows; no result identity, witness or evidence admission."""

    outcome: str
    columns: tuple[SqlColumn, ...] = ()
    rows: tuple[tuple[Any, ...], ...] = ()
    encoded_bytes: int = 0
    invocation_ref: UUID | None = None
    settlement_state: str | None = None
    observed_ms: int | None = None
    cancellation_started_ms: int | None = None
    cancellation_request_failed: bool = False
    error: str | None = None
    witnesses: NativeWitnesses | None = None
    # Safe source offset of an admission refusal (never query text or values).
    error_position: int | None = None
    query_metadata: bytes | None = None
    authority_profile_sha256: str | None = None
    deadline_monotonic: float | None = None


@dataclass(frozen=True, slots=True)
class InvocationSettlement:
    """Private journal state, never recovered rows or a new execution grant."""

    invocation_ref: UUID
    state: str
    outcome: str | None
    reserved_ms: int
    observed_ms: int | None
    charged_ms: int


class _StopQuery(Exception):
    def __init__(self, outcome: str, code: str | None = None) -> None:
        self.outcome = outcome
        # Safe classification: which declared bound stopped the work.
        self.code = code


def backend_transaction(inspector: Connection[Any], pid: int) -> BackendTransaction:
    """Observe a transaction without taking the reader's first data snapshot."""
    row = inspector.execute(
        """SELECT a.pid,a.backend_start,l.virtualxid,a.usesysid
           FROM pg_stat_activity a JOIN pg_locks l ON l.pid=a.pid
           WHERE a.pid=%s AND a.datid=(SELECT oid FROM pg_database
               WHERE datname=current_database())
             AND l.locktype='virtualxid' AND l.granted""",
        (pid,),
    ).fetchone()
    if row is None:
        raise SqlAdmissionError("unavailable", "query_transaction")
    return BackendTransaction(int(row[0]), row[1].isoformat(), row[2], int(row[3]))


def _configure_reader(reader: Connection[Any], remaining_ms: int) -> None:
    # These are trusted fixed settings, never part of the admitted SQL surface.
    reader.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
    for statement in (
        "SET LOCAL search_path=pg_catalog",
        "SET LOCAL work_mem='4MB'",
        "SET LOCAL hash_mem_multiplier=1",
        "SET LOCAL max_parallel_workers_per_gather=0",
        "SET LOCAL jit=off",
        "SET LOCAL lock_timeout='500ms'",
        f"SET LOCAL statement_timeout='{remaining_ms}ms'",
    ):
        reader.execute(statement)
    expected = {
        "search_path": "pg_catalog",
        "work_mem": "4MB",
        "hash_mem_multiplier": "1",
        "max_parallel_workers_per_gather": "0",
        "jit": "off",
        "lock_timeout": "500ms",
        "temp_file_limit": "64MB",
        "transaction_read_only": "on",
        "transaction_isolation": "repeatable read",
    }
    for setting, value in expected.items():
        if reader.execute("SHOW " + setting).fetchone() != (value,):
            raise SqlAdmissionError("unavailable", "query_settings")


def _control_call(
    connection: Connection[Any], query: str, values: tuple[Any, ...]
) -> Any:
    # Blind owned cancellation/settlement remains possible after auth expires.
    # Nothing here authorizes a new invocation or discloses protected contents.
    with connection.transaction():
        connection.execute("SET LOCAL ROLE memoriesql_application")
        connection.execute("SET LOCAL lock_timeout='500ms'")
        connection.execute("SET LOCAL statement_timeout='500ms'")
        row = connection.execute(query, values).fetchone()
        if row is None:
            raise RuntimeError("query settlement unavailable")
        return row[0]


class PostgresRestrictedQuery:
    """Use fresh connections; never send original SQL or reuse an uncertain owner.

    Factories/profile are trusted deployment dependencies. The reviewed profile
    is checked independently on every invocation; it is not inferred/approved
    from whatever permissions happen to be present. Agent-facing provisioning
    stays unavailable until the actual host credential boundary is qualified.
    """

    def __init__(
        self,
        *,
        reader_factory: ConnectionFactory,
        control_factory: ConnectionFactory,
        authority_profile: QueryAuthorityProfile,
        credential_sha256: str,
        workspace_id: UUID,
        policy_hash: str,
        reviewed_builtins: frozenset[str] | None = None,
    ) -> None:
        if len(policy_hash) != 64 or any(
            c not in "0123456789abcdef" for c in policy_hash
        ):
            raise ValueError("invalid query policy pin")
        self._reader_factory = reader_factory
        self._control_factory = control_factory
        self._profile = authority_profile
        self._credential = credential_sha256
        self._workspace = workspace_id
        self._policy_hash = policy_hash
        # The reader's reviewed builtin closure, when the host states it: an
        # admitted operator outside it is refused as unsupported, never
        # dispatched. Without one, the reader's own privileges decide alone.
        self._reviewed = reviewed_builtins

    def recover(
        self, ownership: PreparationReceipt, *, cancel: bool = False
    ) -> InvocationSettlement | None:
        """Recover only an existing owned journal; optionally stop that owner.

        Absence is not permission to retry. Unknown timing retains the complete
        duration reservation; recovery never estimates it as zero or reruns SQL.
        """
        with self._control_factory() as control:
            row = _control_call(
                control,
                "SELECT memoriesql.recover_relation_query_v1(%s,%s)",
                (ownership.operation_ref, ownership.ownership_ref),
            )
            if row is None:
                return None
            ref = UUID(row["invocation_ref"])
            if cancel and row["state"] != "settled":
                _control_call(
                    control,
                    "SELECT memoriesql.cancel_relation_query_v1(%s,%s)",
                    (ref, ownership.ownership_ref),
                )
                row = _control_call(
                    control,
                    "SELECT memoriesql.settle_relation_query_v1(%s,%s,%s,%s)",
                    (ref, ownership.ownership_ref, "cancelled", None),
                )
        return InvocationSettlement(
            ref,
            row["state"],
            row["outcome"],
            int(row["reserved_ms"]),
            row["observed_ms"],
            int(row["charged_ms"]),
        )

    def execute(
        self,
        source: Connection[Any],
        population: PreparedRelationPopulation,
        ownership: PreparationReceipt,
        sql: str,
        parameters: tuple[SqlParameter, ...] = (),
        *,
        recursion: RecursionBound | None = None,
        budget_ms: int = 30000,
        max_output_bytes: int = 64 * 1024 * 1024,
        cancellation: threading.Event | None = None,
        collect_bag_witnesses: bool = False,
    ) -> NativeQueryExecution:
        if (
            type(budget_ms) is not int
            or not 1 <= budget_ms <= 30000
            or type(max_output_bytes) is not int
            or not 8192 <= max_output_bytes <= 64 * 1024 * 1024
        ):
            return NativeQueryExecution("invalid_request", error="time_or_storage")
        # A replayed reservation is never permission to dispatch another SELECT.
        if ownership.replayed or ownership.state != "reserved":
            return NativeQueryExecution("settlement_pending", error="ownership")
        if source.info.transaction_status != TransactionStatus.INTRANS:
            return NativeQueryExecution("unavailable", error="source_frame")
        start = time.monotonic()
        deadline = start + budget_ms / 1000
        ref: UUID | None = None
        rows: list[tuple[Any, ...]] = []
        encoded = 8192
        outcome, safe_error = "complete", None
        error_position: int | None = None
        stop = threading.Event()
        cancel_started: list[int] = []
        cancel_failed = threading.Event()
        worker: threading.Thread | None = None
        reader: Connection[Any] | None = None
        control: Connection[Any] | None = None
        query = None
        stage_started = False
        stage_confirmed = False
        witness_builder: NativeWitnessBuilder | None = None
        witnesses: NativeWitnesses | None = None
        frame_ref = uuid4()
        query_metadata: bytes | None = None
        try:
            anchors = frozenset(
                (column.type.reference_kind, str(value))
                for name, schema in population.schemas.items()
                for row in population.rows[name]
                for column, value in zip(schema.columns, row, strict=True)
                if column.type.reference_kind and value is not None
            )
            query = admit_query(
                sql, parameters, admitted_anchors=anchors, recursion=recursion
            )
            if query.catalog_hash != population.catalog_hash:
                raise SqlAdmissionError("unavailable", "catalog")
            if not set(query.relations) <= set(population.rows):
                raise SqlAdmissionError("unsupported", "unprepared_relation")
            if self._reviewed is not None:
                require_reviewed(query, self._reviewed)
            plan = (
                compile_bag_witness(query, dict(population.schemas))
                if collect_bag_witnesses
                else None
            )
            if plan:

                def check_witness_work() -> None:
                    if time.monotonic() >= deadline:
                        raise _StopQuery("budget_exhausted", "time")
                    if cancellation is not None and cancellation.is_set():
                        raise _StopQuery("cancelled")

                witness_builder = NativeWitnessBuilder(
                    plan, population, frame_ref, check_work=check_witness_work
                )
                encoded += witness_builder.encoded_bytes
            query_metadata = result_json_bytes(
                {
                    "sql": sql,
                    "parameters": [
                        {"position": p.position, "type": p.type, "value": p.value}
                        for p in parameters
                    ],
                    "recursion": (
                        {
                            "cte": recursion.cte,
                            "depth_column": recursion.depth_column,
                            "node_column": recursion.node_column,
                            "max_depth": recursion.max_depth,
                        }
                        if recursion else None
                    ),
                    "derivation_program": query.derivation_program,
                }
            )
            encoded += len(result_json_bytes(query.derivation_program))
            if encoded > max_output_bytes:
                raise _StopQuery("budget_exhausted", "storage")
            source.execute(
                "SELECT memoriesql.check_relation_sql_population_authority_v1()"
            )
            context = source.execute(
                "SELECT to_jsonb(c)::text FROM memoriesql.current_authorization_context() c"
            ).fetchone()
            if context is None or population.source_context != context[0].encode(
                "utf-8"
            ):
                raise SqlAdmissionError("unavailable", "source_frame")
            control = self._control_factory()
            reader = self._reader_factory()
            if not control.autocommit or not reader.autocommit:
                raise SqlAdmissionError("unavailable", "connection_ownership")
            endpoint = (source.info.host, source.info.port, source.info.dbname)
            if any(
                (c.info.host, c.info.port, c.info.dbname) != endpoint
                or c.info.transaction_status != TransactionStatus.IDLE
                for c in (control, reader)
            ):
                raise SqlAdmissionError("unavailable", "connection_database")
            authority = qualify_query_authority(control, self._profile)
            verify_query_login(reader, authority)
            view_names = {
                r[0]
                for r in control.execute(
                    "SELECT n.nspname||'.'||c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.oid=ANY(%s)",
                    ([v.oid for v in self._profile.views],),
                ).fetchall()
            }
            if view_names != set(population.schemas):
                raise SqlAdmissionError("unavailable", "projection_manifest")
            remaining = math.floor((deadline - time.monotonic()) * 1000)
            if remaining <= 0 or (cancellation is not None and cancellation.is_set()):
                raise _StopQuery(
                    "cancelled"
                    if cancellation is not None and cancellation.is_set()
                    else "budget_exhausted",
                    "time",
                )
            _configure_reader(reader, remaining)
            target = backend_transaction(control, reader.info.backend_pid)
            issuer = backend_transaction(control, source.info.backend_pid)
            payload = {
                name.split(".")[1]: [
                    {
                        c.name: encode_result_scalar(value)
                        for c, value in zip(schema.columns, row, strict=True)
                    }
                    for row in population.rows[name]
                ]
                for name, schema in population.schemas.items()
            }
            request = {
                "operation_ref": str(ownership.operation_ref),
                "ownership_ref": str(ownership.ownership_ref),
                "reader_oid": target.role_oid,
                "reader_pid": target.pid,
                "reader_start": target.started_at,
                "reader_transaction": target.virtual_transaction,
                "issuer_pid": issuer.pid,
                "issuer_start": issuer.started_at,
                "issuer_transaction": issuer.virtual_transaction,
                "frame_ref": str(frame_ref),
                "catalog_hash": population.catalog_hash,
                "policy_hash": self._policy_hash,
                "scope_hash": population.dependency_manifest_sha256,
                "projection_manifest_sha256": population.dependency_manifest_sha256,
                "known_at": population.known_at.isoformat(),
                "snapshot_at": population.snapshot_at.isoformat(),
                "reserved_ms": budget_ms,
                "remaining_ms": max(
                    1, math.floor((deadline - time.monotonic()) * 1000)
                ),
                "rows": payload,
                "issuer_context": json.loads(population.source_context),
            }
            if time.monotonic() >= deadline:
                raise _StopQuery("budget_exhausted", "time")
            stage_started = True
            with relation_projection_frame(
                control,
                credential_sha256=self._credential,
                workspace_id=self._workspace,
                # Revision 2 stages larger populations inside the admitted
                # operation deadline; revision 1 keeps the canonical 2.5 s.
                statement_timeout_ms=(
                    max(1, min(30000, math.floor((deadline - time.monotonic()) * 1000)))
                    if population.revision == 2
                    else 2500
                ),
            ) as stage:
                # Revision 2 stages all fourteen prepared relations; revision 1
                # keeps the original nine-relation staging function unchanged.
                response = stage.execute(
                    "SELECT memoriesql.stage_query_population_v2(%s)"
                    if population.revision == 2
                    else "SELECT memoriesql.stage_relation_query_v1(%s)",
                    (Jsonb(request),),
                ).fetchone()
                if response is None:
                    raise SqlAdmissionError("unavailable", "invocation")
                ref = UUID(response[0]["invocation_ref"])
            stage_confirmed = True
            if time.monotonic() >= deadline:
                raise _StopQuery("budget_exhausted", "time")

            def supervise() -> None:
                # Independent of statement_timeout, query thread and client GUCs.
                while not stop.wait(min(0.01, max(0, deadline - time.monotonic()))):
                    if time.monotonic() >= deadline or (
                        cancellation is not None and cancellation.is_set()
                    ):
                        cancel_started.append(
                            math.ceil((time.monotonic() - start) * 1000)
                        )
                        try:
                            with self._control_factory() as monitor:
                                _control_call(
                                    monitor,
                                    "SELECT memoriesql.cancel_relation_query_v1(%s,%s)",
                                    (ref, ownership.ownership_ref),
                                )
                        except Exception:
                            cancel_failed.set()
                        return

            worker = threading.Thread(
                target=supervise, name="memoriesql-query-supervisor", daemon=False
            )
            worker.start()
            if plan and plan.semantic_check:
                # Planning only: preserve original PostgreSQL group/order errors.
                # Estimates are neither read nor used as an authority/work fence.
                # This uses the same restricted login, snapshot and deadline.
                reader.execute("EXPLAIN (COSTS FALSE) " + query.sql, query.parameters)
            with reader.cursor(name="memoriesql_" + uuid4().hex) as cursor:
                cursor.execute(plan.sql if plan else query.sql, query.parameters)
                while batch := cursor.fetchmany(128):
                    for row in batch:
                        if (
                            plan
                            and plan.ledger_rows
                            and isinstance(row[-1], list)
                            and row[-1]
                            and row[-1][0] is None
                        ):
                            if (
                                len(row[-1]) != 2
                                or witness_builder is None
                                or any(v is not None for v in row[:-1])
                            ):
                                raise ValueError(
                                    "invalid native tested-population envelope"
                                )
                            before = witness_builder.encoded_bytes
                            witness_builder.add(row[-1][1], published=False)
                            encoded += witness_builder.encoded_bytes - before
                            if encoded > max_output_bytes:
                                raise _StopQuery("budget_exhausted", "storage")
                            continue
                        native = row[:-1] if plan else row
                        encoded += (
                            len(
                                result_json_bytes(
                                    [encode_result_scalar(v) for v in native]
                                )
                            )
                            + 64
                        )
                        if witness_builder is not None:
                            before = witness_builder.encoded_bytes
                            witness_builder.add(row[-1])
                            encoded += witness_builder.encoded_bytes - before
                        if encoded > max_output_bytes:
                            raise _StopQuery("budget_exhausted", "storage")
                        rows.append(tuple(native))
                    if time.monotonic() >= deadline:
                        raise _StopQuery("budget_exhausted", "time")
                    if cancellation is not None and cancellation.is_set():
                        raise _StopQuery("cancelled")
            if witness_builder is not None:
                witnesses = witness_builder.seal()
                encoded += max(0, len(witnesses.bytes) - witness_builder.encoded_bytes)
                if encoded > max_output_bytes:
                    raise _StopQuery("budget_exhausted", "storage")
            source.execute(
                "SELECT memoriesql.check_relation_sql_population_authority_v1()"
            )
            context_after = source.execute(
                "SELECT to_jsonb(c)::text FROM memoriesql.current_authorization_context() c"
            ).fetchone()
            if (
                context_after is None
                or context_after[0].encode("utf-8") != population.source_context
                or backend_transaction(control, reader.info.backend_pid) != target
                or backend_transaction(control, source.info.backend_pid) != issuer
                or not _control_call(
                    control,
                    "SELECT memoriesql.check_relation_query_v1(%s,%s)",
                    (ref, ownership.ownership_ref),
                )
            ):
                raise SqlAdmissionError("unavailable", "invocation_epoch")
            if cancel_started or time.monotonic() >= deadline:
                raise _StopQuery(
                    "cancelled"
                    if cancellation is not None and cancellation.is_set()
                    else "budget_exhausted",
                    "time",
                )
        except _StopQuery as error:
            outcome, safe_error = error.outcome, error.code
        except SqlAdmissionError as error:
            outcome = "unsupported_query" if error.code == "unsupported" else error.code
            safe_error = error.construct
            error_position = error.position
        except Error as error:
            code = error.sqlstate
            if code in {"57014", "55P03", "54000", "53400"}:
                outcome = (
                    "cancelled"
                    if cancellation is not None and cancellation.is_set()
                    else "budget_exhausted"
                )
            elif code in {"42501", "28000"}:
                outcome = "unavailable"
            elif code in {"55000", "40001"}:
                outcome = "settlement_pending"
            else:
                outcome = "execution_error"
            safe_error = {
                "53400": "storage",
                "54000": "storage",
                "57014": "time",
                "55P03": "time",
                "42501": "unavailable",
                "28000": "unavailable",
                "55000": "settlement",
                "40001": "settlement",
            }.get(code or "", "database")
        except (ValueError, TypeError, OverflowError):
            outcome, safe_error = "execution_error", "type"
        finally:
            stop.set()
            if reader is not None:
                try:
                    reader.rollback()
                except Error:
                    outcome = "settlement_pending"
                finally:
                    reader.close()
            # At most the monitor's bounded owned statement is in flight; if its
            # client is still uncertain, do not abandon it or report settlement.
            if worker is not None:
                worker.join(timeout=1)
                if worker.is_alive():
                    outcome = "settlement_pending"
            if control is not None:
                control.close()
        observed = math.ceil((time.monotonic() - start) * 1000)
        settlement: str | None = None
        if ref is not None:
            terminal = (
                outcome
                if outcome
                in {
                    "complete",
                    "unavailable",
                    "budget_exhausted",
                    "cancelled",
                    "execution_error",
                }
                else "execution_error"
            )
            try:
                with self._control_factory() as settle:
                    receipt = _control_call(
                        settle,
                        "SELECT memoriesql.settle_relation_query_v1(%s,%s,%s,%s)",
                        (ref, ownership.ownership_ref, terminal, observed),
                    )
                settlement = receipt["state"]
                if settlement != "settled" or (
                    worker is not None and worker.is_alive()
                ):
                    outcome = "settlement_pending"
            except Exception:
                outcome, settlement = "settlement_pending", "settlement_pending"
        if stage_started and not stage_confirmed:
            outcome = "settlement_pending"
        if outcome != "complete":
            return NativeQueryExecution(
                outcome,
                invocation_ref=ref,
                settlement_state=settlement,
                observed_ms=observed,
                cancellation_started_ms=cancel_started[0] if cancel_started else None,
                cancellation_request_failed=cancel_failed.is_set(),
                error=safe_error,
                error_position=error_position,
            )
        assert query is not None
        return NativeQueryExecution(
            outcome,
            columns=query.columns,
            rows=tuple(rows),
            encoded_bytes=encoded,
            invocation_ref=ref,
            settlement_state=settlement,
            observed_ms=observed,
            cancellation_started_ms=cancel_started[0] if cancel_started else None,
            cancellation_request_failed=cancel_failed.is_set(),
            witnesses=witnesses,
            query_metadata=query_metadata,
            authority_profile_sha256=authority.profile_sha256,
            deadline_monotonic=deadline,
        )
