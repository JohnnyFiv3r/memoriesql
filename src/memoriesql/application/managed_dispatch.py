"""Explicit supervised qualification; observable turns are not single inferences.

These forward types preserve the published hard-bounded accounting contracts.
The host can stop further dispatch and refuse acceptance; it cannot attest to
hidden provider activity or guarantee remote termination or token/cash ceilings.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from memoriesql.application.model_accounting import (
    AllowanceState,
    BillingBasis,
    ModelUsageEvent,
    NormalizedUsage,
    ProviderRequestIntent,
    ProviderRequestTarget,
    QualityTier,
    RequiredModelAllowanceUnavailable,
    UsageProvenance,
)
from memoriesql.application.semantic_task_contracts import (
    SHA256_PATTERN,
    FrozenContractModel,
    ModelProfileReference,
)
from memoriesql.domain.module_manifest import IDENTIFIER_PATTERN


class ManagedDispatchTarget(FrozenContractModel):
    boundary_kind: Literal["managed_turn"] = "managed_turn"
    provider_key: str = Field(pattern=IDENTIFIER_PATTERN.pattern, max_length=128)
    credential_id: UUID
    model_id: str = Field(min_length=1, max_length=255)
    billing_basis: BillingBasis
    allowance_state: AllowanceState
    pricing_revision_id: UUID | None = None
    quality_policy_revision_id: UUID
    quality_tier: QualityTier = QualityTier.FRONTIER
    request_boundary_revision: str = Field(
        pattern=IDENTIFIER_PATTERN.pattern, max_length=128
    )
    # This is the observable host boundary, never a claim about hidden retries.
    host_retries_disabled: Literal[True] = True
    hidden_requests: Literal["not_independently_observed_or_bounded"] = (
        "not_independently_observed_or_bounded"
    )
    usage_provenance: UsageProvenance

    @model_validator(mode="after")
    def allowance(self) -> Self:
        if self.billing_basis == BillingBasis.SUBSCRIPTION:
            if self.allowance_state not in (
                AllowanceState.COVERED,
                AllowanceState.AVAILABLE,
            ):
                raise ValueError(
                    "managed subscription qualification requires known allowance"
                )
        elif self.allowance_state == AllowanceState.COVERED:
            raise ValueError("covered allowance is subscription-only")
        return self


class SupervisedRoute(FrozenContractModel):
    reference: ModelProfileReference
    target: ManagedDispatchTarget | ProviderRequestTarget
    qualification_revision: str = Field(
        pattern=IDENTIFIER_PATTERN.pattern, max_length=128
    )
    observable_dispatch_allowance: Literal[1] = 1


class SupervisedQualification(FrozenContractModel):
    """Exact task-scoped approval, provisioned separately from worker execution."""

    contract_version: Literal[1] = 1
    qualification_id: UUID
    semantic_task_id: UUID
    routes: tuple[SupervisedRoute, ...] = Field(min_length=1, max_length=2)
    deadline: datetime
    reported_input_token_stop: int = Field(ge=1)
    reported_generated_token_stop: int = Field(ge=1)
    reported_cash_stop_microunits: int | None = Field(default=None, ge=0)
    serial: Literal[True] = True
    usage_limits: Literal["reported_stop_triggers_not_remote_ceilings"] = (
        "reported_stop_triggers_not_remote_ceilings"
    )

    @model_validator(mode="after")
    def exact_routes(self) -> Self:
        if self.deadline.tzinfo is None or self.deadline.utcoffset() is None:
            raise ValueError("qualification requires an absolute aware deadline")
        if not isinstance(self.routes[0].target, ManagedDispatchTarget):
            raise ValueError("first route must be the managed turn")
        if len(self.routes) == 2:
            if not isinstance(self.routes[1].target, ProviderRequestTarget):
                raise ValueError(
                    "optional follow-up must retain hard single-inference bounds"
                )
            if self.routes[0].reference == self.routes[1].reference:
                raise ValueError("qualification routes require distinct exact profiles")
        return self


class SupervisedRequestIntent(ProviderRequestIntent):
    """Hard-bounded follow-up plus durable supervised dispatch allowance."""

    supervision: SupervisedQualification
    qualification_revision: str = Field(
        pattern=IDENTIFIER_PATTERN.pattern, max_length=128
    )
    dispatch_boundary: Literal["single_inference"] = "single_inference"


class ManagedDispatchIntent(ProviderRequestIntent):
    """One observable turn. Null bounds explicitly deny hard token/cash claims."""

    target: ManagedDispatchTarget  # type: ignore[assignment]
    max_input_tokens: None = None  # type: ignore[assignment]
    max_output_tokens: None = None  # type: ignore[assignment]
    safety_ceiling_microunits: None = None
    conservative_reservation_microunits: Literal[0] = 0
    supervision: SupervisedQualification
    qualification_revision: str = Field(
        pattern=IDENTIFIER_PATTERN.pattern, max_length=128
    )
    dispatch_boundary: Literal["managed_turn"] = "managed_turn"
    supply_attestation: Literal["host_visible_only"] = "host_visible_only"

    @model_validator(mode="after")
    def require_one_execution_authority(self) -> Self:
        # Override the v1 hard-bound validator, retaining exact semantic authority.
        if (
            self.semantic_task_id is None
            or self.semantic_attempt_id is None
            or self.retrieval_execution_id is not None
        ):
            raise ValueError(
                "managed dispatch requires one exact semantic task attempt"
            )
        if str(self.supervision.semantic_task_id) != self.semantic_task_id:
            raise ValueError("managed dispatch qualification names another task")
        return self


class InferenceUsageObservation(FrozenContractModel):
    request_id_hash: str = Field(pattern=SHA256_PATTERN)
    usage: NormalizedUsage | None
    provenance: UsageProvenance

    @model_validator(mode="after")
    def presence(self) -> Self:
        if (self.usage is None) != (self.provenance == UsageProvenance.UNAVAILABLE):
            raise ValueError(
                "request observation must state unavailable usage explicitly"
            )
        return self


class ManagedUsageObservation(FrozenContractModel):
    """Totals and parts are alternatives, never additive accounting dimensions."""

    turn_completion: Literal["completed", "ambiguous"] = "ambiguous"
    underlying_inference_count: int | None = Field(default=None, ge=0)
    inference_count_basis: Literal["provider_reported", "host_observed", "unknown"] = (
        "unknown"
    )
    reported_total: NormalizedUsage | None = None
    estimated_total: NormalizedUsage | None = None
    request_observations: tuple[InferenceUsageObservation, ...] = Field(
        default=(), max_length=64
    )
    request_observations_complete: bool = False

    @model_validator(mode="after")
    def truthful_counts(self) -> Self:
        if (self.underlying_inference_count is None) != (
            self.inference_count_basis == "unknown"
        ):
            raise ValueError("unknown inference count must remain null")
        ids = [r.request_id_hash for r in self.request_observations]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate inference observations")
        if (
            self.underlying_inference_count is not None
            and len(ids) > self.underlying_inference_count
        ):
            raise ValueError("observations exceed declared inference count")
        if self.request_observations_complete:
            if (
                self.inference_count_basis != "host_observed"
                or self.underlying_inference_count != len(ids)
            ):
                raise ValueError(
                    "complete request observations require an observed exact count"
                )
            total, provenance = self._request_total()
            if (
                self.reported_total is not None
                and provenance == UsageProvenance.PROVIDER_REPORTED
                and total != self.reported_total
            ):
                raise ValueError(
                    "complete reported requests disagree with reported total"
                )
        return self

    def _request_total(self) -> tuple[NormalizedUsage | None, UsageProvenance]:
        if not self.request_observations_complete or any(
            r.usage is None for r in self.request_observations
        ):
            return None, UsageProvenance.UNAVAILABLE
        totals = {
            key: sum(getattr(r.usage, key) for r in self.request_observations)
            for key in NormalizedUsage.model_fields
        }
        provenance = (
            UsageProvenance.PROVIDER_REPORTED
            if all(
                r.provenance == UsageProvenance.PROVIDER_REPORTED
                for r in self.request_observations
            )
            else UsageProvenance.FRAMEWORK_ESTIMATED
        )
        return NormalizedUsage(**totals), provenance

    def accounted_total(self) -> tuple[NormalizedUsage | None, UsageProvenance]:
        if self.reported_total is not None:
            return self.reported_total, UsageProvenance.PROVIDER_REPORTED
        total, provenance = self._request_total()
        if total is not None:
            return total, provenance
        if self.estimated_total is not None:
            return self.estimated_total, UsageProvenance.FRAMEWORK_ESTIMATED
        return None, UsageProvenance.UNAVAILABLE


class ManagedUsageEvent(ModelUsageEvent):
    observation: ManagedUsageObservation

    @model_validator(mode="after")
    def consistent_aggregate(self) -> Self:
        total, provenance = self.observation.accounted_total()
        if self.usage != total or self.usage_provenance != provenance:
            raise ValueError(
                "accounting must select one aggregate without double counting"
            )
        return self


class SupervisedDispatchUnavailable(RequiredModelAllowanceUnavailable):
    code = "accounting.supervised_dispatch_unavailable"
