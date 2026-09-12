from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType, UnionType
from typing import (
    TYPE_CHECKING,
    Any,
    Literal,
    Protocol,
    Union,
    cast,
    get_args,
    get_origin,
    runtime_checkable,
)
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from memoriesql.domain.module_manifest import IDENTIFIER_PATTERN

if TYPE_CHECKING:
    from memoriesql.application.complete_input_execution import (
        CompleteInputAccess,
        EvidenceExposureRecorder,
    )


CONTRACT_SNAPSHOT_VERSION = 1
SHA256_PATTERN = r"^[a-f0-9]{64}$"


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


class FrozenContractModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        revalidate_instances="always",
        use_enum_values=True,
        validate_default=True,
    )


def contract_model_issue(
    model_type: type[BaseModel],
    *,
    seen: frozenset[type[BaseModel]] = frozenset(),
) -> str | None:
    """Validate the public, source-controlled contract-model boundary."""

    if not issubclass(model_type, FrozenContractModel):
        return f"{model_type.__name__} must inherit FrozenContractModel"
    if model_type.model_config.get("frozen") is not True:
        return f"{model_type.__name__} must keep frozen=True"
    if model_type.model_config.get("extra") != "forbid":
        return f"{model_type.__name__} must keep extra='forbid'"
    if model_type.model_config.get("revalidate_instances") != "always":
        return f"{model_type.__name__} must revalidate model instances"
    if model_type.model_config.get("use_enum_values") is not True:
        return f"{model_type.__name__} must normalize enum members to values"
    if model_type in seen:
        return None
    nested_seen = seen | {model_type}
    for field_name, field in model_type.model_fields.items():
        issue = _contract_field_issue(field.annotation, seen=nested_seen)
        if issue is not None:
            return f"{model_type.__name__}.{field_name}: {issue}"
    return None


def _contract_field_issue(
    annotation: object,
    *,
    seen: frozenset[type[BaseModel]],
) -> str | None:
    origin = get_origin(annotation)
    arguments = get_args(annotation)
    if origin in (Union, UnionType):
        for argument in arguments:
            issue = _contract_field_issue(argument, seen=seen)
            if issue is not None:
                return issue
        return None
    if origin in (tuple, frozenset):
        for argument in arguments:
            if argument is Ellipsis:
                continue
            issue = _contract_field_issue(argument, seen=seen)
            if issue is not None:
                return issue
        return None
    if origin is Literal:
        return None
    if origin in (list, dict, set):
        return f"mutable container {origin.__name__} is not allowed"
    if annotation in (
        str,
        int,
        float,
        bool,
        bytes,
        type(None),
        date,
        datetime,
        time,
        timedelta,
        Decimal,
        UUID,
    ):
        return None
    if isinstance(annotation, type) and issubclass(annotation, StrEnum):
        return None
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return contract_model_issue(annotation, seen=seen)
    return f"unsupported contract field type {annotation!r}"


class DispatchMode(StrEnum):
    DIRECT_LEAF = "direct_leaf"
    CONDUCTOR = "conductor"


class SemanticResultStatus(StrEnum):
    SUCCEEDED = "succeeded"
    UNAVAILABLE = "unavailable"
    BUDGET_EXHAUSTED = "budget_exhausted"
    CANCELLED = "cancelled"
    INVALID_OUTPUT = "invalid_output"
    STALE_INPUT = "stale_input"
    POLICY_PAUSED = "policy_paused"
    FAILED = "failed"


class RetryClass(StrEnum):
    NEVER = "never"
    TRANSIENT = "transient"
    POLICY = "policy"
    STALE = "stale"


RESULT_STATUS_RETRY_POLICY: Mapping[
    SemanticResultStatus, RetryClass | None
] = MappingProxyType(
    {
        SemanticResultStatus.SUCCEEDED: None,
        SemanticResultStatus.UNAVAILABLE: RetryClass.TRANSIENT,
        SemanticResultStatus.BUDGET_EXHAUSTED: RetryClass.NEVER,
        SemanticResultStatus.CANCELLED: RetryClass.NEVER,
        SemanticResultStatus.INVALID_OUTPUT: RetryClass.NEVER,
        SemanticResultStatus.STALE_INPUT: RetryClass.STALE,
        SemanticResultStatus.POLICY_PAUSED: RetryClass.POLICY,
        SemanticResultStatus.FAILED: RetryClass.NEVER,
    }
)


def retry_class_for_status(
    status: SemanticResultStatus | str,
) -> RetryClass | None:
    """Return the sole retry policy for a semantic result status."""

    return RESULT_STATUS_RETRY_POLICY[SemanticResultStatus(status)]


class EffortProfile(FrozenContractModel):
    key: str = Field(pattern=IDENTIFIER_PATTERN.pattern)
    rank: int = Field(ge=0)


class ModelProfileReference(FrozenContractModel):
    """Provider-neutral deployment-resolved model capability reference."""

    profile_key: str = Field(pattern=IDENTIFIER_PATTERN.pattern)
    revision: int = Field(ge=1)
    effort_key: str = Field(pattern=IDENTIFIER_PATTERN.pattern)


class RunBudget(FrozenContractModel):
    wall_clock_seconds: int = Field(ge=1)
    request_limit: int = Field(ge=1)
    input_token_limit: int = Field(ge=1)
    output_token_limit: int = Field(ge=1)
    total_token_limit: int = Field(ge=1)
    tool_call_limit: int = Field(ge=0)
    cost_safety_limit_microusd: int | None = Field(default=None, ge=0)
    max_delegate_calls: int = Field(ge=0)
    max_parallel_delegates: int = Field(ge=0)
    evidence_item_limit: int = Field(ge=1)
    hydrated_character_limit: int = Field(ge=1)
    output_retries: int = Field(ge=0)
    tool_retries: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_totals_and_delegate_limits(self) -> RunBudget:
        if self.total_token_limit < max(
            self.input_token_limit,
            self.output_token_limit,
        ):
            raise ValueError(
                "total_token_limit must cover each directional token limit"
            )
        if self.max_parallel_delegates > self.max_delegate_calls:
            raise ValueError("max_parallel_delegates cannot exceed max_delegate_calls")
        return self

    def is_not_wider_than(self, ceiling: RunBudget) -> bool:
        numeric_fields = (
            "wall_clock_seconds",
            "request_limit",
            "input_token_limit",
            "output_token_limit",
            "total_token_limit",
            "tool_call_limit",
            "max_delegate_calls",
            "max_parallel_delegates",
            "evidence_item_limit",
            "hydrated_character_limit",
            "output_retries",
            "tool_retries",
        )
        if any(
            getattr(self, field) > getattr(ceiling, field) for field in numeric_fields
        ):
            return False
        if ceiling.cost_safety_limit_microusd is None:
            return True
        return (
            self.cost_safety_limit_microusd is not None
            and self.cost_safety_limit_microusd <= ceiling.cost_safety_limit_microusd
        )


class EvidenceReference(FrozenContractModel):
    reference_id: str = Field(min_length=1)
    content_hash: str = Field(pattern=SHA256_PATTERN)
    declared_characters: int = Field(ge=0)


class EvidenceManifest(FrozenContractModel):
    manifest_id: str = Field(min_length=1)
    revision: int = Field(ge=1)
    references: tuple[EvidenceReference, ...]

    @model_validator(mode="after")
    def reject_duplicate_references(self) -> EvidenceManifest:
        reference_ids = [reference.reference_id for reference in self.references]
        if len(reference_ids) != len(set(reference_ids)):
            raise ValueError("evidence reference identifiers must be unique")
        return self


class SemanticTaskInput[InputT: BaseModel](FrozenContractModel):
    task_id: str = Field(min_length=1)
    task_kind: str = Field(pattern=IDENTIFIER_PATTERN.pattern)
    contract_revision: int = Field(ge=1)
    target_reference: str = Field(min_length=1)
    expected_target_revision: int = Field(ge=0)
    evidence_manifest: EvidenceManifest
    requested_effort_key: str | None = Field(
        default=None,
        pattern=IDENTIFIER_PATTERN.pattern,
    )
    requested_budget: RunBudget | None = None
    payload: InputT


class UsageSummary(FrozenContractModel):
    requests: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)


class ValidationResult(FrozenContractModel):
    validator_key: str = Field(pattern=IDENTIFIER_PATTERN.pattern)
    passed: bool
    code: str = Field(pattern=IDENTIFIER_PATTERN.pattern)


class SemanticTaskResult[OutputT: BaseModel](FrozenContractModel):
    status: SemanticResultStatus
    task_id: str = Field(min_length=1)
    attempt_id: str = Field(min_length=1)
    task_kind: str = Field(pattern=IDENTIFIER_PATTERN.pattern)
    contract_revision: int = Field(ge=1)
    output_contract_hash: str = Field(pattern=SHA256_PATTERN)
    typed_output: OutputT | None = None
    output_hash: str | None = Field(default=None, pattern=SHA256_PATTERN)
    used_evidence_refs: tuple[str, ...] = ()
    model_run_refs: tuple[str, ...] = ()
    validation_results: tuple[ValidationResult, ...] = ()
    usage: UsageSummary = UsageSummary()
    error_code: str | None = Field(
        default=None,
        pattern=IDENTIFIER_PATTERN.pattern,
    )
    retry_class: RetryClass | None = None

    @model_validator(mode="after")
    def enforce_discriminated_envelope(self) -> SemanticTaskResult[OutputT]:
        expected_retry_class = retry_class_for_status(self.status)
        if self.status == SemanticResultStatus.SUCCEEDED:
            if self.typed_output is None or self.output_hash is None:
                raise ValueError(
                    "succeeded results require typed output and output hash"
                )
            if self.error_code is not None or self.retry_class is not None:
                raise ValueError("succeeded results cannot carry failure metadata")
            calculated_hash = canonical_sha256(
                self.typed_output.model_dump(mode="json", warnings="error")
            )
            if calculated_hash != self.output_hash:
                raise ValueError("succeeded output_hash must match typed_output")
        else:
            if self.typed_output is not None or self.output_hash is not None:
                raise ValueError("non-success results cannot carry authored output")
            if self.error_code is None:
                raise ValueError("non-success results require error_code")
            if self.retry_class != expected_retry_class:
                raise ValueError(
                    f"{self.status} results require retry_class="
                    f"{expected_retry_class}"
                )
        if len(self.used_evidence_refs) != len(set(self.used_evidence_refs)):
            raise ValueError("used evidence references must be unique")
        return self


class ContractReference(FrozenContractModel):
    contract_id: str = Field(pattern=IDENTIFIER_PATTERN.pattern)
    revision: int = Field(ge=1)
    schema_hash: str = Field(pattern=SHA256_PATTERN)


@dataclass(frozen=True)
class TypedModelContract[ModelT: BaseModel]:
    contract_id: str
    revision: int
    model_type: type[ModelT]

    def __post_init__(self) -> None:
        if IDENTIFIER_PATTERN.fullmatch(self.contract_id) is None:
            raise ValueError(f"invalid contract identifier {self.contract_id!r}")
        if self.revision < 1:
            raise ValueError("contract revision must be positive")

    def schema_payload(self) -> dict[str, object]:
        return self.model_type.model_json_schema()

    @property
    def schema_hash(self) -> str:
        return canonical_sha256(self.schema_payload())

    @property
    def reference(self) -> ContractReference:
        return ContractReference(
            contract_id=self.contract_id,
            revision=self.revision,
            schema_hash=self.schema_hash,
        )

    def canonical_payload(self) -> dict[str, object]:
        return {
            **self.reference.model_dump(mode="json"),
            "json_schema": self.schema_payload(),
        }


@dataclass(frozen=True)
class AgentContract:
    agent_key: str
    input_contract: ContractReference
    output_contract: ContractReference
    maximum_effort_key: str
    may_delegate: bool = False


@dataclass(frozen=True)
class ToolContract:
    tool_key: str


@dataclass(frozen=True)
class EvidencePolicyContract:
    policy_key: str


@dataclass(frozen=True)
class SemanticTaskDefinition[InputT: BaseModel, OutputT: BaseModel]:
    task_kind: str
    contract_revision: int
    owning_module: str
    input_contract: TypedModelContract[SemanticTaskInput[InputT]]
    output_contract: TypedModelContract[OutputT]
    dispatch_mode: DispatchMode
    default_effort_key: str
    maximum_effort_key: str
    model_profile: ModelProfileReference
    evidence_policy_key: str
    run_budget: RunBudget
    leaf_agent_key: str | None = None
    allowed_delegate_keys: tuple[str, ...] = ()
    allowed_tool_keys: tuple[str, ...] = ()

    def canonical_payload(self) -> dict[str, object]:
        return {
            "task_kind": self.task_kind,
            "contract_revision": self.contract_revision,
            "owning_module": self.owning_module,
            "input_contract": self.input_contract.canonical_payload(),
            "output_contract": self.output_contract.canonical_payload(),
            "dispatch_mode": self.dispatch_mode.value,
            "default_effort_key": self.default_effort_key,
            "maximum_effort_key": self.maximum_effort_key,
            "model_profile": self.model_profile.model_dump(mode="json"),
            "evidence_policy_key": self.evidence_policy_key,
            "run_budget": self.run_budget.model_dump(mode="json"),
            "leaf_agent_key": self.leaf_agent_key,
            "allowed_delegate_keys": sorted(self.allowed_delegate_keys),
            "allowed_tool_keys": sorted(self.allowed_tool_keys),
        }

    @property
    def contract_hash(self) -> str:
        return canonical_sha256(self.canonical_payload())


@runtime_checkable
class EvidenceAccessor(Protocol):
    async def read_authorized_evidence(
        self,
        reference: EvidenceReference,
    ) -> str: ...


@runtime_checkable
class CancellationSignal(Protocol):
    def is_cancelled(self) -> bool: ...


class SemanticRunEventKind(StrEnum):
    RUN_STARTED = "run.started"
    RUN_SUCCEEDED = "run.succeeded"
    RUN_FAILED = "run.failed"
    DELEGATION_STARTED = "delegation.started"
    DELEGATION_FINISHED = "delegation.finished"


class SemanticAgentContractIdentity(FrozenContractModel):
    """The exact bounded agent-to-contract association used by one run."""

    agent_key: str = Field(pattern=IDENTIFIER_PATTERN.pattern, max_length=128)
    input_contract: ContractReference
    output_contract: ContractReference


class SemanticRootRunCorrelation(FrozenContractModel):
    correlation_kind: Literal["root_run"] = "root_run"
    run_id: str = Field(pattern=IDENTIFIER_PATTERN.pattern, max_length=128)
    run_role: Literal["direct_leaf", "conductor"]
    agent_contract: SemanticAgentContractIdentity
    model_profile: ModelProfileReference


class SemanticDelegatedRunCorrelation(FrozenContractModel):
    correlation_kind: Literal["delegated_run"] = "delegated_run"
    run_id: str = Field(pattern=IDENTIFIER_PATTERN.pattern, max_length=128)
    parent_run_id: str = Field(pattern=IDENTIFIER_PATTERN.pattern, max_length=128)
    run_role: Literal["delegate"] = "delegate"
    agent_contract: SemanticAgentContractIdentity
    model_profile: ModelProfileReference
    delegation_id: str = Field(pattern=IDENTIFIER_PATTERN.pattern, max_length=128)


SemanticRunCorrelation = SemanticRootRunCorrelation | SemanticDelegatedRunCorrelation


class SemanticRunEvent(FrozenContractModel):
    event_kind: SemanticRunEventKind
    task_id: str = Field(min_length=1)
    attempt_id: str = Field(min_length=1)
    correlation: SemanticRunCorrelation

    @model_validator(mode="after")
    def require_delegated_correlation_for_delegation_event(
        self,
    ) -> SemanticRunEvent:
        if self.event_kind.startswith("delegation.") and not isinstance(
            self.correlation,
            SemanticDelegatedRunCorrelation,
        ):
            raise ValueError("delegation events require delegated-run correlation")
        return self


@runtime_checkable
class SemanticRunEventSink(Protocol):
    async def append(self, event: SemanticRunEvent) -> None: ...


@runtime_checkable
class ProcessLocalUsageRecorder(Protocol):
    """Writable in-process usage bookkeeping; no durable accounting semantics."""

    def snapshot(self) -> UsageSummary: ...

    def replace(self, usage: UsageSummary) -> None: ...

    def retain_after_failure(self, usage: UsageSummary) -> None: ...


@runtime_checkable
class ModelRequestAccounting(Protocol):
    """Durable request accounting port; concrete fact types live in PR-01G."""

    async def record_intent(self, intent: Any) -> None: ...

    async def append_usage(self, event: Any) -> None: ...


@dataclass(frozen=True)
class SemanticAuthorizationContext:
    principal_id: str
    delegation_id: str | None
    tenant_id: str
    workspace_id: str
    policy_revision: int
    allowed_access_scope_ids: frozenset[str]


@dataclass(frozen=True)
class SemanticRunDeps:
    task_id: str
    task_kind: str
    contract_revision: int
    task_contract_hash: str
    semantic_registry_hash: str
    attempt_id: str
    lease_generation: int
    authorization: SemanticAuthorizationContext
    authorized_evidence_manifest: EvidenceManifest
    evidence_accessor: EvidenceAccessor
    cancellation: CancellationSignal
    usage: ProcessLocalUsageRecorder
    event_sink: SemanticRunEventSink
    model_accounting: ModelRequestAccounting
    monotonic_deadline_ns: int
    complete_input: CompleteInputAccess | None = None
    exposure_recorder: EvidenceExposureRecorder | None = None


@dataclass(frozen=True)
class ResolvedSemanticTask[InputT: BaseModel, OutputT: BaseModel]:
    definition: SemanticTaskDefinition[InputT, OutputT]
    task_input: SemanticTaskInput[InputT]
    effective_effort: EffortProfile
    effective_budget: RunBudget
    semantic_registry_hash: str


@runtime_checkable
class SemanticExecutor[InputT: BaseModel, OutputT: BaseModel](Protocol):
    def cancellation_grace_seconds(
        self,
        *,
        max_delegate_calls: int,
    ) -> float:
        """Return finite grace before repeated cancellation and attached drain."""

        ...

    async def execute(
        self,
        task: ResolvedSemanticTask[InputT, OutputT],
        deps: SemanticRunDeps,
    ) -> SemanticTaskResult[OutputT]: ...


class ResultAcceptanceErrorCode(StrEnum):
    INVALID_ENVELOPE = "invalid_envelope"
    DEPENDENCY_TASK_MISMATCH = "dependency_task_mismatch"
    DEPENDENCY_CONTRACT_MISMATCH = "dependency_contract_mismatch"
    DEPENDENCY_REGISTRY_MISMATCH = "dependency_registry_mismatch"
    DEPENDENCY_EVIDENCE_MISMATCH = "dependency_evidence_mismatch"
    TASK_ID_MISMATCH = "task_id_mismatch"
    ATTEMPT_ID_MISMATCH = "attempt_id_mismatch"
    TASK_KIND_MISMATCH = "task_kind_mismatch"
    CONTRACT_REVISION_MISMATCH = "contract_revision_mismatch"
    OUTPUT_CONTRACT_MISMATCH = "output_contract_mismatch"
    OUTPUT_TYPE_MISMATCH = "output_type_mismatch"
    EVIDENCE_SCOPE_WIDENED = "evidence_scope_widened"
    USAGE_MISMATCH = "usage_mismatch"
    BUDGET_EXCEEDED = "budget_exceeded"


class SemanticTaskResultAcceptanceError(ValueError):
    def __init__(
        self,
        code: ResultAcceptanceErrorCode,
        detail: str,
    ) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code.value}: {detail}")


def _validate_semantic_task_dependencies[
    InputT: BaseModel, OutputT: BaseModel
](
    task: ResolvedSemanticTask[InputT, OutputT],
    deps: SemanticRunDeps,
) -> None:
    if (
        deps.task_id != task.task_input.task_id
        or deps.task_kind != task.definition.task_kind
        or deps.contract_revision != task.definition.contract_revision
    ):
        raise SemanticTaskResultAcceptanceError(
            ResultAcceptanceErrorCode.DEPENDENCY_TASK_MISMATCH,
            "dependency task identity differs from the resolved task",
        )
    if deps.task_contract_hash != task.definition.contract_hash:
        raise SemanticTaskResultAcceptanceError(
            ResultAcceptanceErrorCode.DEPENDENCY_CONTRACT_MISMATCH,
            "dependency task contract hash differs from the resolved definition",
        )
    if deps.semantic_registry_hash != task.semantic_registry_hash:
        raise SemanticTaskResultAcceptanceError(
            ResultAcceptanceErrorCode.DEPENDENCY_REGISTRY_MISMATCH,
            "dependency semantic registry hash differs from resolution",
        )

    requested_manifest = task.task_input.evidence_manifest
    authorized_manifest = deps.authorized_evidence_manifest
    if (
        requested_manifest.manifest_id != authorized_manifest.manifest_id
        or requested_manifest.revision != authorized_manifest.revision
    ):
        raise SemanticTaskResultAcceptanceError(
            ResultAcceptanceErrorCode.DEPENDENCY_EVIDENCE_MISMATCH,
            "dependency evidence manifest identity or revision differs",
        )
    authorized_by_id = {
        reference.reference_id: reference
        for reference in authorized_manifest.references
    }
    mismatched = sorted(
        reference.reference_id
        for reference in requested_manifest.references
        if authorized_by_id.get(reference.reference_id) != reference
    )
    if mismatched:
        raise SemanticTaskResultAcceptanceError(
            ResultAcceptanceErrorCode.DEPENDENCY_EVIDENCE_MISMATCH,
            "dependency evidence differs for: " + ", ".join(mismatched),
        )


def accept_semantic_task_result[InputT: BaseModel, OutputT: BaseModel](
    task: ResolvedSemanticTask[InputT, OutputT],
    deps: SemanticRunDeps,
    result: SemanticTaskResult[OutputT],
) -> SemanticTaskResult[OutputT]:
    """Fail closed before an executor result may cross an apply boundary."""

    _validate_semantic_task_dependencies(task, deps)
    try:
        original_output = result.typed_output
    except AttributeError as error:
        raise SemanticTaskResultAcceptanceError(
            ResultAcceptanceErrorCode.INVALID_ENVELOPE,
            "executor result is missing typed_output",
        ) from error
    if (
        original_output is not None
        and type(original_output) is not task.definition.output_contract.model_type
    ):
        raise SemanticTaskResultAcceptanceError(
            ResultAcceptanceErrorCode.OUTPUT_TYPE_MISMATCH,
            task.definition.output_contract.contract_id,
        )

    result_type = cast(
        type[SemanticTaskResult[OutputT]],
        SemanticTaskResult.__class_getitem__(
            task.definition.output_contract.model_type
        ),
    )
    try:
        result = result_type.model_validate(result)
    except Exception as error:
        raise SemanticTaskResultAcceptanceError(
            ResultAcceptanceErrorCode.INVALID_ENVELOPE,
            "executor result failed complete envelope revalidation",
        ) from error

    expected_identity = (
        (
            ResultAcceptanceErrorCode.TASK_ID_MISMATCH,
            "task_id",
            task.task_input.task_id,
        ),
        (
            ResultAcceptanceErrorCode.ATTEMPT_ID_MISMATCH,
            "attempt_id",
            deps.attempt_id,
        ),
        (
            ResultAcceptanceErrorCode.TASK_KIND_MISMATCH,
            "task_kind",
            task.definition.task_kind,
        ),
        (
            ResultAcceptanceErrorCode.CONTRACT_REVISION_MISMATCH,
            "contract_revision",
            task.definition.contract_revision,
        ),
        (
            ResultAcceptanceErrorCode.OUTPUT_CONTRACT_MISMATCH,
            "output_contract_hash",
            task.definition.output_contract.schema_hash,
        ),
    )
    for code, field_name, expected in expected_identity:
        actual = getattr(result, field_name)
        if actual != expected:
            raise SemanticTaskResultAcceptanceError(
                code,
                f"expected {expected!r}; received {actual!r}",
            )

    if (
        result.typed_output is not None
        and type(result.typed_output) is not task.definition.output_contract.model_type
    ):
        raise SemanticTaskResultAcceptanceError(
            ResultAcceptanceErrorCode.OUTPUT_TYPE_MISMATCH,
            task.definition.output_contract.contract_id,
        )

    authorized_refs = {
        reference.reference_id
        for reference in task.task_input.evidence_manifest.references
    }
    widened_refs = set(result.used_evidence_refs) - authorized_refs
    if widened_refs:
        raise SemanticTaskResultAcceptanceError(
            ResultAcceptanceErrorCode.EVIDENCE_SCOPE_WIDENED,
            ", ".join(sorted(widened_refs)),
        )

    trusted_usage = deps.usage.snapshot()
    if result.usage != trusted_usage:
        raise SemanticTaskResultAcceptanceError(
            ResultAcceptanceErrorCode.USAGE_MISMATCH,
            "executor usage summary differs from the trusted counter",
        )
    usage = trusted_usage
    budget = task.effective_budget
    budget_exceeded = (
        usage.requests > budget.request_limit
        or usage.input_tokens > budget.input_token_limit
        or usage.output_tokens > budget.output_token_limit
        or usage.input_tokens + usage.output_tokens > budget.total_token_limit
        or usage.tool_calls > budget.tool_call_limit
    )
    if budget_exceeded and result.status != SemanticResultStatus.BUDGET_EXHAUSTED:
        raise SemanticTaskResultAcceptanceError(
            ResultAcceptanceErrorCode.BUDGET_EXCEEDED,
            "executor usage exceeds the resolved task budget",
        )
    return result


async def execute_semantic_task[InputT: BaseModel, OutputT: BaseModel](
    executor: SemanticExecutor[InputT, OutputT],
    task: ResolvedSemanticTask[InputT, OutputT],
    deps: SemanticRunDeps,
) -> SemanticTaskResult[OutputT]:
    """The sole executor call path, including result identity acceptance."""

    _validate_semantic_task_dependencies(task, deps)
    result = await executor.execute(task, deps)
    return accept_semantic_task_result(task, deps, result)


def contract_schema_catalog(
    definitions: tuple[SemanticTaskDefinition[BaseModel, BaseModel], ...],
) -> dict[str, object]:
    ordered_definitions = tuple(
        sorted(
            definitions,
            key=lambda item: (item.task_kind, item.contract_revision),
        )
    )
    return {
        "snapshot_version": CONTRACT_SNAPSHOT_VERSION,
        "supporting_contracts": {
            "effort_profile": EffortProfile.model_json_schema(),
            "evidence_manifest": EvidenceManifest.model_json_schema(),
            "model_profile_reference": ModelProfileReference.model_json_schema(),
            "run_budget": RunBudget.model_json_schema(),
        },
        "task_contracts": [
            definition.canonical_payload() for definition in ordered_definitions
        ],
        "task_result_contracts": [
            {
                "task_kind": definition.task_kind,
                "contract_revision": definition.contract_revision,
                "output_contract_hash": definition.output_contract.schema_hash,
                "json_schema": _typed_result_schema(
                    definition.output_contract.model_type
                ),
            }
            for definition in ordered_definitions
        ],
    }


def _typed_result_schema(output_type: type[BaseModel]) -> dict[str, object]:
    result_type = cast(
        type[BaseModel],
        SemanticTaskResult.__class_getitem__(output_type),
    )
    return result_type.model_json_schema()
