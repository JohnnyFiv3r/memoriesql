from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from pydantic import Field, model_validator

from memoriesql.application.semantic_task_contracts import (
    SHA256_PATTERN,
    FrozenContractModel,
)
from memoriesql.domain.module_manifest import IDENTIFIER_PATTERN

MICROUNITS_PER_UNIT = 1_000_000


class BillingBasis(StrEnum):
    METERED = "metered"
    SUBSCRIPTION = "subscription"
    LOCAL = "local"
    DISCOUNTED = "discounted"
    CREDITED = "credited"
    INDETERMINATE = "indeterminate"


class AllowanceState(StrEnum):
    COVERED = "covered"
    AVAILABLE = "available"
    EXHAUSTED = "exhausted"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"


class UsageEventKind(StrEnum):
    USAGE_REPORTED = "usage_reported"
    USAGE_UNAVAILABLE = "usage_unavailable"
    REQUEST_FAILED = "request_failed"
    REQUEST_CANCELLED = "request_cancelled"
    LEASE_LOST_RETURN = "lease_lost_return"


class RequestOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    LEASE_LOST = "lease_lost"
    UNKNOWN = "unknown"


class CostBasis(StrEnum):
    PROVIDER_REPORTED = "provider_reported"
    CATALOG_ESTIMATE = "catalog_estimate"
    SUBSCRIPTION_COVERED = "subscription_covered"
    LOCAL = "local"
    UNKNOWN = "unknown"


class UsageState(StrEnum):
    REPORTED = "reported"
    UNAVAILABLE = "unavailable"


class QualityTier(StrEnum):
    FRONTIER = "frontier"
    QUALIFIED_LOWER = "qualified_lower"


class UsageProvenance(StrEnum):
    PROVIDER_REPORTED = "provider_reported"
    FRAMEWORK_ESTIMATED = "framework_estimated"
    UNAVAILABLE = "unavailable"


class ProviderRequestTarget(FrozenContractModel):
    """The exact provider, credential and billing snapshot used for one request."""

    provider_key: str = Field(pattern=IDENTIFIER_PATTERN.pattern, max_length=128)
    credential_id: UUID
    model_id: str = Field(min_length=1, max_length=255)
    max_input_tokens_per_request: int = Field(ge=1)
    max_output_tokens_per_request: int = Field(ge=1)
    billing_basis: BillingBasis
    allowance_state: AllowanceState
    pricing_revision_id: UUID | None = None
    quality_policy_revision_id: UUID
    quality_tier: QualityTier = QualityTier.FRONTIER
    request_boundary_revision: str = Field(
        pattern=IDENTIFIER_PATTERN.pattern,
        max_length=128,
    )
    transport_retries_disabled: bool
    usage_provenance: UsageProvenance

    @model_validator(mode="after")
    def require_visible_transport_boundary(self) -> ProviderRequestTarget:
        if not self.transport_retries_disabled:
            raise ValueError(
                "provider transport retries must be disabled or intercepted"
            )
        if (
            self.billing_basis == BillingBasis.SUBSCRIPTION
            and self.allowance_state == AllowanceState.NOT_APPLICABLE
        ):
            raise ValueError("subscription targets require an allowance state")
        if (
            self.billing_basis != BillingBasis.SUBSCRIPTION
            and self.allowance_state == AllowanceState.COVERED
        ):
            raise ValueError("covered allowance is subscription-only")
        return self


class NormalizedUsage(FrozenContractModel):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    cached_input_tokens: int = Field(default=0, ge=0)
    cache_write_tokens: int = Field(default=0, ge=0)
    reasoning_tokens: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def enforce_normalized_subtotals(self) -> NormalizedUsage:
        if self.total_tokens != self.input_tokens + self.output_tokens:
            raise ValueError("total_tokens must equal input_tokens plus output_tokens")
        if self.cached_input_tokens + self.cache_write_tokens > self.input_tokens:
            raise ValueError("cached reads and writes cannot exceed input tokens")
        if self.reasoning_tokens > self.output_tokens:
            raise ValueError("reasoning tokens cannot exceed output tokens")
        return self


class ProviderRequestIntent(FrozenContractModel):
    """Durable intent required before one actual first-party provider request."""

    request_id: UUID
    tenant_id: str = Field(min_length=1, max_length=128)
    workspace_id: str = Field(min_length=1, max_length=128)
    access_scope_id: str = Field(min_length=1, max_length=128)
    semantic_task_id: str | None = Field(default=None, min_length=1, max_length=128)
    semantic_attempt_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
    )
    retrieval_execution_id: UUID | None = None
    run_id: str = Field(pattern=IDENTIFIER_PATTERN.pattern, max_length=128)
    parent_run_id: str | None = Field(
        default=None,
        pattern=IDENTIFIER_PATTERN.pattern,
        max_length=128,
    )
    run_role: str = Field(pattern=IDENTIFIER_PATTERN.pattern, max_length=64)
    module_key: str = Field(pattern=IDENTIFIER_PATTERN.pattern, max_length=128)
    operation_key: str = Field(pattern=IDENTIFIER_PATTERN.pattern, max_length=128)
    task_kind: str = Field(pattern=IDENTIFIER_PATTERN.pattern, max_length=128)
    task_contract_revision: int = Field(ge=1)
    origin_principal_id: str = Field(min_length=1, max_length=128)
    origin_delegation_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
    )
    policy_revision: int = Field(ge=1)
    model_profile_key: str = Field(
        pattern=IDENTIFIER_PATTERN.pattern,
        max_length=128,
    )
    model_profile_revision: int = Field(ge=1)
    request_sequence: int = Field(ge=1)
    target: ProviderRequestTarget
    safety_ceiling_microunits: int | None = Field(default=None, ge=0)
    conservative_reservation_microunits: int = Field(ge=0)
    max_input_tokens: int = Field(ge=1)
    max_output_tokens: int = Field(ge=1)
    request_payload_hash: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def require_one_execution_authority(self) -> ProviderRequestIntent:
        semantic = (
            self.semantic_task_id is not None
            and self.semantic_attempt_id is not None
        )
        retrieval = self.retrieval_execution_id is not None
        if semantic == retrieval:
            raise ValueError(
                "exactly one semantic task attempt or retrieval execution is required"
            )
        if (self.semantic_task_id is None) != (self.semantic_attempt_id is None):
            raise ValueError("semantic task and attempt must be recorded together")
        if (
            self.target.billing_basis == BillingBasis.METERED
            and self.safety_ceiling_microunits is not None
            and self.target.pricing_revision_id is None
        ):
            raise ValueError(
                "a dollar safety ceiling requires a pinned pricing revision"
            )
        if (
            self.target.billing_basis == BillingBasis.METERED
            and self.safety_ceiling_microunits is not None
            and self.conservative_reservation_microunits <= 0
        ):
            raise ValueError(
                "metered requests with a safety ceiling require a reservation"
            )
        if (
            self.target.billing_basis == BillingBasis.METERED
            and self.target.pricing_revision_id is None
            and self.conservative_reservation_microunits != 0
        ):
            raise ValueError("unpriced requests cannot claim a cash reservation")
        if self.safety_ceiling_microunits is not None and not (
            self.target.billing_basis == BillingBasis.METERED
            and self.target.pricing_revision_id is not None
            or self.target.billing_basis == BillingBasis.SUBSCRIPTION
            and self.target.allowance_state == AllowanceState.COVERED
        ):
            raise ValueError(
                "a dollar safety ceiling requires a known incremental cash basis"
            )
        if self.max_input_tokens != self.target.max_input_tokens_per_request:
            raise ValueError("input reservation must use the certified target bound")
        if self.max_output_tokens > self.target.max_output_tokens_per_request:
            raise ValueError("output reservation exceeds the certified target bound")
        return self


class ModelUsageEvent(FrozenContractModel):
    """One immutable, idempotently deliverable request accounting fact."""

    event_id: UUID
    request_id: UUID
    dedupe_key: str = Field(pattern=SHA256_PATTERN)
    event_kind: UsageEventKind
    outcome: RequestOutcome
    usage_state: UsageState
    usage_provenance: UsageProvenance
    usage: NormalizedUsage | None = None
    provider_reported_cost_microunits: int | None = Field(default=None, ge=0)
    provider_reported_currency: str | None = Field(
        default=None,
        pattern=r"^USD$",
    )
    provider_request_id_hash: str | None = Field(
        default=None,
        pattern=SHA256_PATTERN,
    )
    diagnostic_code: str | None = Field(
        default=None,
        pattern=IDENTIFIER_PATTERN.pattern,
        max_length=128,
    )

    @model_validator(mode="after")
    def require_explicit_usage_and_money_state(self) -> ModelUsageEvent:
        if self.usage_state == UsageState.REPORTED and self.usage is None:
            raise ValueError("reported usage requires normalized units")
        if self.usage_state == UsageState.UNAVAILABLE and self.usage is not None:
            raise ValueError("unavailable usage cannot carry normalized units")
        if (
            self.usage_state == UsageState.UNAVAILABLE
            and self.usage_provenance != UsageProvenance.UNAVAILABLE
        ):
            raise ValueError("unavailable usage requires unavailable provenance")
        if (
            self.usage_state == UsageState.REPORTED
            and self.usage_provenance == UsageProvenance.UNAVAILABLE
        ):
            raise ValueError("reported usage requires a known provenance")
        if (self.provider_reported_cost_microunits is None) != (
            self.provider_reported_currency is None
        ):
            raise ValueError("provider-reported cost and currency are atomic")
        if (
            self.event_kind == UsageEventKind.USAGE_REPORTED
            and self.usage_state != UsageState.REPORTED
        ):
            raise ValueError("usage_reported requires reported usage")
        if (
            self.event_kind == UsageEventKind.USAGE_UNAVAILABLE
            and self.usage_state != UsageState.UNAVAILABLE
        ):
            raise ValueError("usage_unavailable requires unavailable usage")
        required_outcomes = {
            UsageEventKind.USAGE_REPORTED: RequestOutcome.SUCCEEDED,
            UsageEventKind.USAGE_UNAVAILABLE: RequestOutcome.SUCCEEDED,
            UsageEventKind.REQUEST_FAILED: RequestOutcome.FAILED,
            UsageEventKind.REQUEST_CANCELLED: RequestOutcome.CANCELLED,
            UsageEventKind.LEASE_LOST_RETURN: RequestOutcome.LEASE_LOST,
        }
        if self.outcome != required_outcomes[self.event_kind]:
            raise ValueError("usage event kind and request outcome disagree")
        return self


class PricingRevision(FrozenContractModel):
    pricing_revision_id: UUID
    provider_key: str = Field(pattern=IDENTIFIER_PATTERN.pattern, max_length=128)
    model_id: str = Field(min_length=1, max_length=255)
    currency: str = Field(pattern=r"^USD$")
    effective_from_epoch_us: int = Field(ge=0)
    effective_until_epoch_us: int | None = Field(default=None, ge=1)
    input_microunits_per_million_tokens: int = Field(ge=0)
    cached_input_microunits_per_million_tokens: int = Field(ge=0)
    cache_write_microunits_per_million_tokens: int = Field(ge=0)
    output_microunits_per_million_tokens: int = Field(ge=0)
    source_hash: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def require_closed_effective_range(self) -> PricingRevision:
        if (
            self.effective_until_epoch_us is not None
            and self.effective_until_epoch_us <= self.effective_from_epoch_us
        ):
            raise ValueError("pricing revision range must be non-empty")
        return self


class QualityPolicyRevision(FrozenContractModel):
    quality_policy_revision_id: UUID
    policy_key: str = Field(pattern=IDENTIFIER_PATTERN.pattern, max_length=128)
    revision: int = Field(ge=1)
    default_quality_first: bool
    lower_tier_requires_frozen_qualification: bool = True
    source_hash: str = Field(pattern=SHA256_PATTERN)


class TaskProfileQualification(FrozenContractModel):
    qualification_id: UUID
    task_kind: str = Field(pattern=IDENTIFIER_PATTERN.pattern, max_length=128)
    contract_revision: int = Field(ge=1)
    model_profile_key: str = Field(
        pattern=IDENTIFIER_PATTERN.pattern,
        max_length=128,
    )
    model_profile_revision: int = Field(ge=1)
    quality_policy_revision_id: UUID
    evidence_hash: str = Field(pattern=SHA256_PATTERN)
    signer_key: str = Field(pattern=IDENTIFIER_PATTERN.pattern, max_length=128)
    signature_hash: str = Field(pattern=SHA256_PATTERN)


class OptimizationTargetRevision(FrozenContractModel):
    """Evaluation-only target; never a live routing instruction."""

    optimization_target_id: UUID
    target_key: str = Field(pattern=IDENTIFIER_PATTERN.pattern, max_length=128)
    revision: int = Field(ge=1)
    task_kind: str = Field(pattern=IDENTIFIER_PATTERN.pattern, max_length=128)
    contract_revision: int = Field(ge=1)
    baseline_model_profile_key: str = Field(
        pattern=IDENTIFIER_PATTERN.pattern,
        max_length=128,
    )
    baseline_model_profile_revision: int = Field(ge=1)
    candidate_model_profile_key: str = Field(
        pattern=IDENTIFIER_PATTERN.pattern,
        max_length=128,
    )
    candidate_model_profile_revision: int = Field(ge=1)
    baseline_evidence_hash: str = Field(pattern=SHA256_PATTERN)
    target_reduction_basis_points: int = Field(ge=0, le=10_000)
    quality_gate_hash: str = Field(pattern=SHA256_PATTERN)
    source_hash: str = Field(pattern=SHA256_PATTERN)


class BulkUsageForecast(FrozenContractModel):
    forecast_id: UUID
    forecast_series_key: str = Field(pattern=SHA256_PATTERN)
    forecast_hash: str = Field(pattern=SHA256_PATTERN)
    tenant_id: str = Field(min_length=1, max_length=128)
    workspace_id: str = Field(min_length=1, max_length=128)
    access_scope_id: str = Field(min_length=1, max_length=128)
    source_scope_hash: str = Field(pattern=SHA256_PATTERN)
    quality_policy_revision_id: UUID
    model_profile_key: str = Field(
        pattern=IDENTIFIER_PATTERN.pattern,
        max_length=128,
    )
    model_profile_revision: int = Field(ge=1)
    billing_basis: BillingBasis
    pricing_revision_id: UUID | None = None
    pricing_observed_at_epoch_us: int = Field(ge=0)
    item_count: int = Field(ge=1)
    low_shadow_cost_microunits: int | None = Field(default=None, ge=0)
    expected_shadow_cost_microunits: int | None = Field(default=None, ge=0)
    high_shadow_cost_microunits: int | None = Field(default=None, ge=0)
    incremental_cash_exposure_microunits: int | None = Field(default=None, ge=0)
    safety_ceiling_microunits: int | None = Field(default=None, ge=0)
    expected_completion_low_ms: int = Field(ge=0)
    expected_completion_high_ms: int = Field(ge=0)
    allowance_state: AllowanceState
    cost_basis: CostBasis
    queue_effect_hash: str = Field(pattern=SHA256_PATTERN)
    assumptions_hash: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def require_ordered_forecast_range(self) -> BulkUsageForecast:
        values = (
            self.low_shadow_cost_microunits,
            self.expected_shadow_cost_microunits,
            self.high_shadow_cost_microunits,
        )
        if all(value is not None for value in values):
            low, expected, high = values
            assert low is not None and expected is not None and high is not None
            if not low <= expected <= high:
                raise ValueError("forecast range must be ordered")
        elif any(value is not None for value in values):
            raise ValueError("forecast range is atomic or wholly unavailable")
        if self.expected_completion_low_ms > self.expected_completion_high_ms:
            raise ValueError("forecast completion range must be ordered")
        if (
            self.billing_basis == BillingBasis.METERED
            and self.cost_basis == CostBasis.CATALOG_ESTIMATE
            and self.pricing_revision_id is None
        ):
            raise ValueError("catalog-priced forecasts require a pricing revision")
        if self.cost_basis == CostBasis.CATALOG_ESTIMATE and (
            self.billing_basis != BillingBasis.METERED
            or self.pricing_revision_id is None
            or self.incremental_cash_exposure_microunits is None
        ):
            raise ValueError("catalog-estimated cash requires priced metered billing")
        if self.cost_basis == CostBasis.SUBSCRIPTION_COVERED and not (
            self.billing_basis == BillingBasis.SUBSCRIPTION
            and self.allowance_state == AllowanceState.COVERED
            and self.incremental_cash_exposure_microunits == 0
        ):
            raise ValueError("subscription-covered forecasts require covered zero cash")
        if self.cost_basis == CostBasis.LOCAL and not (
            self.billing_basis == BillingBasis.LOCAL
            and self.incremental_cash_exposure_microunits is None
        ):
            raise ValueError("local forecasts require unknown incremental cash")
        if (
            self.cost_basis == CostBasis.PROVIDER_REPORTED
            and self.incremental_cash_exposure_microunits is None
        ):
            raise ValueError("provider-reported forecasts require cash exposure")
        if (
            self.cost_basis == CostBasis.UNKNOWN
            and self.incremental_cash_exposure_microunits is not None
        ):
            raise ValueError("unknown forecast cash cannot carry a value")
        return self


class BulkForecastApproval(FrozenContractModel):
    approval_id: UUID
    forecast_id: UUID
    forecast_hash: str = Field(pattern=SHA256_PATTERN)
    source_scope_hash: str = Field(pattern=SHA256_PATTERN)
    assumptions_hash: str = Field(pattern=SHA256_PATTERN)
    quality_policy_revision_id: UUID
    model_profile_key: str = Field(
        pattern=IDENTIFIER_PATTERN.pattern,
        max_length=128,
    )
    model_profile_revision: int = Field(ge=1)
    safety_ceiling_microunits: int | None = Field(default=None, ge=0)
    approved_by_principal_id: str = Field(min_length=1, max_length=128)
    idempotency_key: str = Field(pattern=SHA256_PATTERN)


class ModelAccountingError(RuntimeError):
    code = "accounting.failed"


class AccountingPersistenceError(ModelAccountingError):
    code = "accounting.persistence_failed"


class CostSafetyCeilingExceeded(ModelAccountingError):
    code = "accounting.cost_safety_ceiling"


class RequiredModelAllowanceUnavailable(ModelAccountingError):
    code = "accounting.required_model_allowance_unavailable"


class RejectingModelRequestAccounting:
    """Safe composition default for execution paths that must never call a model."""

    async def record_intent(self, intent: ProviderRequestIntent) -> None:
        del intent
        raise AccountingPersistenceError("model accounting is not configured")

    async def append_usage(self, event: ModelUsageEvent) -> None:
        del event
        raise AccountingPersistenceError("model accounting is not configured")
