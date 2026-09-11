from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.types.json import Jsonb
from pydantic import BaseModel

from memoriesql.application.canonical_transactions import (
    ApplySemanticAnnotationsCommand,
)
from memoriesql.application.complete_input_execution import (
    ApplyCompleteInput,
    CompleteEvidenceBatch,
    ReadCompleteEvidence,
)
from memoriesql.application.observation_commands import (
    AuthorInitialObservationsCommand,
    CorrectObservationCommand,
)
from memoriesql.application.semantic_task_contracts import (
    EvidenceManifest,
    SemanticDelegatedRunCorrelation,
    SemanticResultStatus,
    SemanticRunEvent,
    SemanticTaskDefinition,
    SemanticTaskInput,
    SemanticTaskResult,
    canonical_json_bytes,
    retry_class_for_status,
)
from memoriesql.infrastructure.postgres.canonical_transactions import (
    PostgresCanonicalTransactions,
)


class SemanticQueueName(StrEnum):
    INTERACTIVE = "interactive"
    CAPTURE = "capture"
    CONTINUITY = "continuity"


class ReauthorizationResult(StrEnum):
    AUTHORIZED = "authorized"
    CANCEL_REQUESTED = "cancel_requested"
    POLICY_PAUSED = "policy_paused"
    EVIDENCE_UNAVAILABLE = "evidence_unavailable"
    STALE_EVIDENCE = "stale_evidence"
    STALE_FENCE = "stale_fence"
    INVALID_PHASE = "invalid_phase"


class EvidenceHydrationFailure(StrEnum):
    UNAVAILABLE = "unavailable"
    STALE_INPUT = "stale_input"
    BUDGET_EXHAUSTED = "budget_exhausted"


class EvidenceDisposition(StrEnum):
    DELETED = "deleted"
    STALE = "stale"


class ModuleQueueState(StrEnum):
    ENABLED = "enabled"
    DRAINING = "draining"
    DISABLED = "disabled"


@dataclass(frozen=True, slots=True)
class EnqueueSemanticTask:
    task_id: UUID
    idempotency_key: str
    definition: SemanticTaskDefinition[BaseModel, BaseModel]
    semantic_registry_hash: str
    task_input: SemanticTaskInput[BaseModel]
    access_scope_id: UUID
    available_at: datetime
    rerun_of_task_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class EnqueueReceipt:
    task_id: UUID
    idempotency_receipt_id: UUID
    input_hash: str
    replayed: bool


@dataclass(frozen=True, slots=True)
class SemanticTaskFence:
    tenant_id: UUID
    workspace_id: UUID
    access_scope_id: UUID
    task_id: UUID
    attempt_id: UUID
    attempt_number: int
    lease_generation: int
    worker_id: str
    worker_instance_id: str
    deadline_at: datetime
    lease_expires_at: datetime


@dataclass(frozen=True, slots=True)
class ClaimedSemanticTask:
    fence: SemanticTaskFence
    task_kind: str
    contract_revision: int
    task_contract_hash: str
    semantic_registry_hash: str
    queue_name: SemanticQueueName
    target_kind: str
    target_reference: str
    expected_target_revision: int
    input_hash: str
    evidence_manifest_id: str
    evidence_manifest_hash: str
    origin_principal_id: UUID
    origin_pairing_grant_id: UUID | None
    required_capability: str
    accepted_policy_revision_id: UUID


@dataclass(frozen=True, slots=True)
class SemanticAuthorizationSnapshot:
    principal_id: UUID
    delegation_id: UUID | None
    policy_revision: int
    access_scope_id: UUID


class PostgresSemanticTaskQueue:
    """Typed adapter over the one Postgres semantic-task protocol.

    Callers establish a trusted SQL-01B authorization context in each transaction.
    Claim returns metadata only; ``hydrate_input`` is a separately reauthorized
    operation. No method exposes arbitrary SQL or canonical semantic mutation.
    """

    def __init__(self, connection: Connection[Any]) -> None:
        self._connection = connection

    def allocate_task_id(self) -> UUID:
        row = self._connection.execute("SELECT pg_catalog.uuidv7()").fetchone()
        if row is None:
            raise RuntimeError("Postgres did not allocate a semantic task ID")
        return UUID(str(row[0]))

    def enqueue(
        self,
        command: EnqueueSemanticTask,
        *,
        recorded_at: datetime,
    ) -> EnqueueReceipt:
        input_payload = command.task_input.model_dump(mode="json", warnings="error")
        row = self._connection.execute(
            """
            SELECT task_id, idempotency_receipt_id, input_hash, replayed
            FROM memoriesql.enqueue_semantic_task(
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s
            )
            """,
            (
                command.task_id,
                command.idempotency_key,
                command.definition.owning_module,
                command.definition.task_kind,
                command.definition.contract_revision,
                command.definition.contract_hash,
                command.semantic_registry_hash,
                command.task_input.target_reference,
                command.task_input.expected_target_revision,
                Jsonb(input_payload),
                command.task_input.evidence_manifest.manifest_id,
                command.access_scope_id,
                command.available_at,
                command.rerun_of_task_id,
                recorded_at,
            ),
        ).fetchone()
        if row is None:
            raise RuntimeError("Postgres did not return an enqueue receipt")
        return EnqueueReceipt(
            task_id=UUID(str(row[0])),
            idempotency_receipt_id=UUID(str(row[1])),
            input_hash=str(row[2]),
            replayed=bool(row[3]),
        )

    def claim(
        self,
        *,
        worker_id: str,
        worker_instance_id: str,
        lease_seconds: int,
        deadline_seconds: int,
        executor_contract_version: int,
        claimed_at: datetime,
    ) -> ClaimedSemanticTask | None:
        row = self._connection.execute(
            """
            SELECT *
            FROM memoriesql.claim_semantic_task(
                %s, %s, %s, %s, %s, %s
            )
            """,
            (
                worker_id,
                worker_instance_id,
                lease_seconds,
                deadline_seconds,
                executor_contract_version,
                claimed_at,
            ),
        ).fetchone()
        if row is None:
            return None
        fence = SemanticTaskFence(
            tenant_id=UUID(str(row[0])),
            workspace_id=UUID(str(row[1])),
            access_scope_id=UUID(str(row[2])),
            task_id=UUID(str(row[3])),
            attempt_id=UUID(str(row[4])),
            attempt_number=int(row[5]),
            lease_generation=int(row[6]),
            worker_id=worker_id,
            worker_instance_id=worker_instance_id,
            deadline_at=row[22],
            lease_expires_at=row[23],
        )
        return ClaimedSemanticTask(
            fence=fence,
            task_kind=str(row[7]),
            contract_revision=int(row[8]),
            task_contract_hash=str(row[9]),
            semantic_registry_hash=str(row[10]),
            queue_name=SemanticQueueName(str(row[11])),
            target_kind=str(row[12]),
            target_reference=str(row[13]),
            expected_target_revision=int(row[14]),
            input_hash=str(row[15]),
            evidence_manifest_id=str(row[16]),
            evidence_manifest_hash=str(row[17]),
            origin_principal_id=UUID(str(row[18])),
            origin_pairing_grant_id=(UUID(str(row[19])) if row[19] else None),
            required_capability=str(row[20]),
            accepted_policy_revision_id=UUID(str(row[21])),
        )

    def start(self, fence: SemanticTaskFence, *, started_at: datetime) -> bool:
        row = self._connection.execute(
            """
            SELECT memoriesql.start_semantic_task_attempt(
                %s, %s, %s, %s, %s, %s, %s
            )
            """,
            self._fence_parameters(fence) + (started_at,),
        ).fetchone()
        return bool(row and row[0])

    def heartbeat(
        self,
        fence: SemanticTaskFence,
        *,
        lease_seconds: int,
        heartbeat_at: datetime,
    ) -> bool:
        row = self._connection.execute(
            """
            SELECT memoriesql.heartbeat_semantic_task(
                %s, %s, %s, %s, %s, %s, %s, %s
            )
            """,
            self._fence_parameters(fence) + (lease_seconds, heartbeat_at),
        ).fetchone()
        return bool(row and row[0])

    def retain_cleanup_lease(
        self,
        fence: SemanticTaskFence,
        *,
        lease_seconds: int,
        retained_at: datetime,
    ) -> bool:
        row = self._connection.execute(
            """
            SELECT memoriesql.retain_semantic_task_cleanup_lease(
                %s, %s, %s, %s, %s, %s, %s, %s
            )
            """,
            self._fence_parameters(fence) + (lease_seconds, retained_at),
        ).fetchone()
        return bool(row and row[0])

    def reauthorize(
        self,
        fence: SemanticTaskFence,
        *,
        phase: str,
        checked_at: datetime,
    ) -> ReauthorizationResult:
        row = self._connection.execute(
            """
            SELECT memoriesql.reauthorize_semantic_task(
                %s, %s, %s, %s, %s, %s, %s, %s
            )
            """,
            self._fence_parameters(fence) + (phase, checked_at),
        ).fetchone()
        if row is None:
            return ReauthorizationResult.STALE_FENCE
        return ReauthorizationResult(str(row[0]))

    def reauthorize_for_watch(
        self,
        fence: SemanticTaskFence,
        *,
        checked_at: datetime,
    ) -> ReauthorizationResult:
        row = self._connection.execute(
            """
            SELECT memoriesql.reauthorize_semantic_task_for_watch(
                %s, %s, %s, %s, %s, %s, %s
            )
            """,
            self._fence_parameters(fence) + (checked_at,),
        ).fetchone()
        if row is None:
            return ReauthorizationResult.STALE_FENCE
        return ReauthorizationResult(str(row[0]))

    def hydrate_input(
        self,
        fence: SemanticTaskFence,
        *,
        hydrated_at: datetime,
    ) -> dict[str, object] | None:
        row = self._connection.execute(
            """
            SELECT memoriesql.hydrate_semantic_task_input(
                %s, %s, %s, %s, %s, %s, %s
            )
            """,
            self._fence_parameters(fence) + (hydrated_at,),
        ).fetchone()
        if row is None or row[0] is None:
            return None
        payload = row[0]
        if not isinstance(payload, dict):
            raise RuntimeError("hydrated semantic task input is not an object")
        return payload

    def hydrate_evidence(
        self,
        fence: SemanticTaskFence,
        manifest: EvidenceManifest,
        *,
        character_limit: int,
        hydrated_at: datetime,
    ) -> dict[str, str] | EvidenceHydrationFailure:
        """Hydrate exactly one authorized source-unit manifest, or fail closed."""

        if character_limit <= 0:
            raise ValueError("evidence character limit must be positive")
        try:
            normalized_references = [
                (reference, UUID(reference.reference_id))
                for reference in manifest.references
            ]
        except ValueError:
            return EvidenceHydrationFailure.UNAVAILABLE
        reference_ids = [reference_id for _, reference_id in normalized_references]
        rows = self._connection.execute(
            """
            WITH current_gate AS MATERIALIZED (
                SELECT memoriesql.reauthorize_semantic_task(
                    %s, %s, %s, %s, %s, %s, 'hydrate', %s
                ) AS result
            ), requested AS MATERIALIZED (
                SELECT reference_id, declared_characters
                FROM unnest(%s::uuid[], %s::integer[])
                     AS requested(reference_id, declared_characters)
            )
            SELECT current_gate.result,
                   source.source_unit_id,
                   CASE
                     WHEN char_length(left(source.content_text, %s + 1)) =
                          requested.declared_characters
                     THEN source.content_text
                   END,
                   source.content_hash,
                   char_length(left(source.content_text, %s + 1))
            FROM current_gate
            LEFT JOIN requested
              ON current_gate.result = 'authorized'
            LEFT JOIN memoriesql.source_units AS source
              ON current_gate.result = 'authorized'
             AND source.tenant_id = %s
             AND source.workspace_id = %s
             AND source.access_scope_id = %s
             AND source.source_unit_id = requested.reference_id
            """,
            self._fence_parameters(fence)
            + (
                hydrated_at,
                reference_ids,
                [reference.declared_characters for reference in manifest.references],
                character_limit,
                character_limit,
                fence.tenant_id,
                fence.workspace_id,
                fence.access_scope_id,
            ),
        ).fetchall()
        if (
            not rows
            or ReauthorizationResult(str(rows[0][0]))
            is not ReauthorizationResult.AUTHORIZED
        ):
            return EvidenceHydrationFailure.UNAVAILABLE
        hydrated = {
            UUID(str(row[1])): (
                str(row[2]) if row[2] is not None else None,
                str(row[3]),
                int(row[4]),
            )
            for row in rows
            if row[1] is not None and row[3] is not None and row[4] is not None
        }
        if len(hydrated) != len(manifest.references):
            return EvidenceHydrationFailure.UNAVAILABLE
        if sum(candidate[2] for candidate in hydrated.values()) > character_limit:
            return EvidenceHydrationFailure.BUDGET_EXHAUSTED
        content: dict[str, str] = {}
        for reference, reference_id in normalized_references:
            candidate = hydrated.get(reference_id)
            if candidate is None:
                return EvidenceHydrationFailure.UNAVAILABLE
            text, content_hash, character_count = candidate
            if text is None:
                return EvidenceHydrationFailure.STALE_INPUT
            hydrated_content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
            if (
                content_hash != reference.content_hash
                or hydrated_content_hash != reference.content_hash
                or character_count != reference.declared_characters
            ):
                return EvidenceHydrationFailure.STALE_INPUT
            content[reference.reference_id] = text
        return content

    def complete_exposure_valid(self, fence: SemanticTaskFence) -> bool:
        row = self._connection.execute(
            "SELECT memoriesql.validate_complete_input_acceptance(%s,%s,%s,%s,%s,%s)",
            self._fence_parameters(fence),
        ).fetchone()
        return bool(row and row[0])

    def read_complete_evidence(
        self, fence: SemanticTaskFence, request: ReadCompleteEvidence
    ) -> CompleteEvidenceBatch:
        # Match the standalone reader's bounded server operations. This route
        # runs inside the worker's owned transaction, never a provider wait.
        self._connection.execute("SET LOCAL statement_timeout='2s'")
        self._connection.execute("SET LOCAL lock_timeout='500ms'")

        def authorize() -> None:
            if (
                self.reauthorize(fence, phase="hydrate", checked_at=datetime.now(UTC))
                is not ReauthorizationResult.AUTHORIZED
            ):
                raise PermissionError("complete input authorization unavailable")

        authorize()
        row = self._connection.execute(
            "SELECT memoriesql.read_complete_evidence_v2(%s)",
            (Jsonb(request.model_dump(mode="json")),),
        ).fetchone()
        authorize()
        if row is None:
            raise PermissionError("complete evidence unavailable")
        return CompleteEvidenceBatch.model_validate(row[0])

    def authorization_snapshot(
        self,
        fence: SemanticTaskFence,
        *,
        checked_at: datetime,
    ) -> SemanticAuthorizationSnapshot | None:
        row = self._connection.execute(
            """
            SELECT *
            FROM memoriesql.semantic_task_authorization_snapshot(
                %s, %s, %s, %s, %s, %s, %s
            )
            """,
            self._fence_parameters(fence) + (checked_at,),
        ).fetchone()
        if row is None:
            return None
        return SemanticAuthorizationSnapshot(
            principal_id=UUID(str(row[0])),
            delegation_id=UUID(str(row[1])) if row[1] else None,
            policy_revision=int(row[2]),
            access_scope_id=UUID(str(row[3])),
        )

    def record_run_event(
        self,
        fence: SemanticTaskFence,
        event: SemanticRunEvent,
        *,
        recorded_at: datetime,
    ) -> bool:
        if event.task_id != str(fence.task_id):
            raise ValueError("semantic run event belongs to a different task fence")
        if event.attempt_id != str(fence.attempt_id):
            raise ValueError("semantic run event belongs to a different attempt fence")
        correlation = event.correlation
        parent_run_id: str | None = None
        delegation_id: str | None = None
        if isinstance(correlation, SemanticDelegatedRunCorrelation):
            parent_run_id = correlation.parent_run_id
            delegation_id = correlation.delegation_id
        row = self._connection.execute(
            """
            SELECT memoriesql.record_semantic_run_event(
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            """,
            self._fence_parameters(fence)
            + (
                str(event.event_kind),
                correlation.run_id,
                parent_run_id,
                correlation.run_role,
                correlation.agent_contract.agent_key,
                correlation.agent_contract.input_contract.contract_id,
                correlation.agent_contract.input_contract.revision,
                correlation.agent_contract.input_contract.schema_hash,
                correlation.agent_contract.output_contract.contract_id,
                correlation.agent_contract.output_contract.revision,
                correlation.agent_contract.output_contract.schema_hash,
                correlation.model_profile.profile_key,
                correlation.model_profile.revision,
                correlation.model_profile.effort_key,
                delegation_id,
                recorded_at,
            ),
        ).fetchone()
        return bool(row and row[0])

    def record_synthetic_result(
        self,
        fence: SemanticTaskFence,
        result: SemanticTaskResult[BaseModel],
        *,
        recorded_at: datetime,
    ) -> str:
        if result.status != SemanticResultStatus.SUCCEEDED:
            raise ValueError("synthetic outcome sink accepts only successful results")
        if str(result.task_id) != str(fence.task_id):
            raise ValueError("semantic result belongs to a different task fence")
        if str(result.attempt_id) != str(fence.attempt_id):
            raise ValueError("semantic result belongs to a different attempt fence")
        if result.typed_output is None or result.output_hash is None:
            raise ValueError("successful semantic result is incomplete")
        row = self._connection.execute(
            """
            SELECT memoriesql.record_synthetic_semantic_task_outcome(
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s
            )
            """,
            self._fence_parameters(fence)
            + (
                result.output_contract_hash,
                Jsonb(result.typed_output.model_dump(mode="json", warnings="error")),
                result.output_hash,
                list(result.used_evidence_refs),
                list(result.model_run_refs),
                Jsonb(
                    [
                        validation.model_dump(mode="json", warnings="error")
                        for validation in result.validation_results
                    ]
                ),
                result.usage.requests,
                result.usage.input_tokens,
                result.usage.output_tokens,
                result.usage.tool_calls,
                recorded_at,
            ),
        ).fetchone()
        if row is None:
            raise RuntimeError("Postgres did not return a synthetic outcome status")
        return str(row[0])

    def record_canonical_result(
        self,
        fence: SemanticTaskFence,
        result: SemanticTaskResult[BaseModel],
        *,
        recorded_at: datetime,
    ) -> str:
        if result.status != SemanticResultStatus.SUCCEEDED:
            raise ValueError("canonical result persistence requires success")
        if str(result.task_id) != str(fence.task_id):
            raise ValueError("semantic result belongs to a different task fence")
        if str(result.attempt_id) != str(fence.attempt_id):
            raise ValueError("semantic result belongs to a different attempt fence")
        if result.typed_output is None or result.output_hash is None:
            raise ValueError("successful canonical result is incomplete")
        adapter = PostgresCanonicalTransactions(self._connection)
        payload = result.typed_output.model_dump(mode="json", warnings="error")
        command_data = {
            "idempotency_key": f"semantic-apply.{fence.attempt_id}",
            "tenant_id": fence.tenant_id,
            "workspace_id": fence.workspace_id,
            "access_scope_id": fence.access_scope_id,
            "task_id": fence.task_id,
            "attempt_id": fence.attempt_id,
            "lease_generation": fence.lease_generation,
            "task_kind": result.task_kind,
            "contract_revision": result.contract_revision,
            "output_contract_hash": result.output_contract_hash,
            "semantic_result_hash": result.output_hash,
            "semantic_payload_canonical_json": canonical_json_bytes(payload).decode(
                "utf-8"
            ),
            "used_evidence_refs": result.used_evidence_refs,
            "model_run_refs": result.model_run_refs,
            "payload": payload,
        }
        if (result.task_kind, result.contract_revision) == (
            "memory.semantic.author-observations",
            1,
        ):
            receipt = adapter.apply_semantic_annotations(
                ApplySemanticAnnotationsCommand.model_validate(command_data),
                worker_id=fence.worker_id,
                worker_instance_id=fence.worker_instance_id,
                recorded_at=recorded_at,
            )
        elif (result.task_kind, result.contract_revision) == (
            "memory.semantic.author-observations",
            2,
        ):
            receipt = adapter.author_initial_observations(
                AuthorInitialObservationsCommand.model_validate(command_data),
                worker_id=fence.worker_id,
                worker_instance_id=fence.worker_instance_id,
                recorded_at=recorded_at,
            )
        elif (result.task_kind, result.contract_revision) == (
            "memory.semantic.correct-observation",
            2,
        ):
            receipt = adapter.correct_observation(
                CorrectObservationCommand.model_validate(command_data),
                worker_id=fence.worker_id,
                worker_instance_id=fence.worker_instance_id,
                recorded_at=recorded_at,
            )
        elif (result.task_kind, result.contract_revision) == (
            "memory.semantic.author-complete-unit",
            2,
        ):
            command = ApplyCompleteInput.model_validate(command_data)
            row = self._connection.execute(
                "SELECT * FROM memoriesql.apply_semantic_annotations(%s,%s,%s,%s)",
                (
                    Jsonb(command.model_dump(mode="json")),
                    fence.worker_id,
                    fence.worker_instance_id,
                    recorded_at,
                ),
            ).fetchone()
            if row is None:
                raise RuntimeError("complete input application returned no receipt")
            return str(row[5])
        else:
            raise ValueError("unregistered canonical observation contract")
        return receipt.task_status

    def record_result(
        self,
        fence: SemanticTaskFence,
        result: SemanticTaskResult[BaseModel],
        *,
        result_ref: str | None,
        error_class: str | None,
        retry_after_seconds: int | None,
        jitter_basis_points: int,
        recorded_at: datetime,
    ) -> str:
        if str(result.task_id) != str(fence.task_id):
            raise ValueError("semantic result belongs to a different task fence")
        if str(result.attempt_id) != str(fence.attempt_id):
            raise ValueError("semantic result belongs to a different attempt fence")
        retry_class = retry_class_for_status(result.status)
        row = self._connection.execute(
            """
            SELECT memoriesql.record_semantic_task_outcome(
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s
            )
            """,
            self._fence_parameters(fence)
            + (
                str(result.status),
                result.output_hash,
                result_ref,
                result.error_code,
                error_class,
                str(retry_class) if retry_class else None,
                retry_after_seconds,
                jitter_basis_points,
                recorded_at,
            ),
        ).fetchone()
        if row is None:
            raise RuntimeError("Postgres did not return a fenced outcome")
        return str(row[0])

    def record_integrated_failure(
        self,
        fence: SemanticTaskFence,
        result: SemanticTaskResult[BaseModel],
        *,
        error_class: str,
        retry_after_seconds: int | None,
        jitter_basis_points: int,
        recorded_at: datetime,
    ) -> str:
        if result.status == SemanticResultStatus.SUCCEEDED:
            raise ValueError("integrated failure boundary cannot accept success")
        if str(result.task_id) != str(fence.task_id):
            raise ValueError("semantic result belongs to a different task fence")
        if str(result.attempt_id) != str(fence.attempt_id):
            raise ValueError("semantic result belongs to a different attempt fence")
        retry_class = retry_class_for_status(result.status)
        row = self._connection.execute(
            """
            SELECT memoriesql.record_integrated_semantic_task_failure(
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s
            )
            """,
            self._fence_parameters(fence)
            + (
                str(result.status),
                result.error_code,
                error_class,
                str(retry_class) if retry_class else None,
                retry_after_seconds,
                jitter_basis_points,
                recorded_at,
            ),
        ).fetchone()
        if row is None:
            raise RuntimeError("Postgres did not return an integrated failure status")
        return str(row[0])

    def reap_expired(
        self,
        *,
        worker_id: str,
        limit: int,
        reaped_at: datetime,
    ) -> int:
        row = self._connection.execute(
            "SELECT memoriesql.reap_expired_semantic_tasks(%s, %s, %s)",
            (worker_id, limit, reaped_at),
        ).fetchone()
        return int(row[0]) if row else 0

    def cancel(
        self,
        *,
        tenant_id: UUID,
        task_id: UUID,
        reason: str,
        cancelled_at: datetime,
    ) -> str:
        row = self._connection.execute(
            "SELECT memoriesql.cancel_semantic_task(%s, %s, %s, %s)",
            (tenant_id, task_id, reason, cancelled_at),
        ).fetchone()
        return str(row[0]) if row else "unavailable"

    def set_module_state(
        self,
        *,
        owning_module: str,
        state: ModuleQueueState,
        reason_code: str,
        cancel_running: bool,
        changed_at: datetime,
    ) -> int:
        row = self._connection.execute(
            """
            SELECT memoriesql.set_semantic_module_queue_state(%s, %s, %s, %s, %s)
            """,
            (owning_module, state.value, reason_code, cancel_running, changed_at),
        ).fetchone()
        return int(row[0]) if row else 0

    def resume(
        self,
        *,
        tenant_id: UUID,
        task_id: UUID,
        resumed_at: datetime,
    ) -> bool:
        row = self._connection.execute(
            "SELECT memoriesql.resume_semantic_task(%s, %s, %s)",
            (tenant_id, task_id, resumed_at),
        ).fetchone()
        return bool(row and row[0])

    def retire_evidence(
        self,
        *,
        tenant_id: UUID,
        task_id: UUID,
        disposition: EvidenceDisposition,
        retired_at: datetime,
    ) -> str:
        row = self._connection.execute(
            """
            SELECT memoriesql.retire_semantic_task_evidence(%s, %s, %s, %s)
            """,
            (tenant_id, task_id, disposition.value, retired_at),
        ).fetchone()
        return str(row[0]) if row else "unavailable"

    @staticmethod
    def _fence_parameters(fence: SemanticTaskFence) -> tuple[object, ...]:
        return (
            fence.tenant_id,
            fence.task_id,
            fence.attempt_id,
            fence.lease_generation,
            fence.worker_id,
            fence.worker_instance_id,
        )
