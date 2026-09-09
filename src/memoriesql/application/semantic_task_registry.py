from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import cast

from pydantic import BaseModel

from memoriesql.application.module_registry import (
    BuiltInModuleRegistry,
    CompositionHealthReport,
    ModuleState,
)
from memoriesql.application.semantic_task_contracts import (
    AgentContract,
    ContractReference,
    DispatchMode,
    EffortProfile,
    EvidencePolicyContract,
    ModelProfileReference,
    ResolvedSemanticTask,
    RunBudget,
    SemanticRunDeps,
    SemanticTaskDefinition,
    SemanticTaskInput,
    ToolContract,
    TypedModelContract,
    canonical_sha256,
    contract_model_issue,
)
from memoriesql.domain.module_manifest import IDENTIFIER_PATTERN, ContributionKind

KERNEL_SEMANTIC_TASK_OWNER = "memoriesql.kernel"


class RegistryIssueCode(StrEnum):
    DUPLICATE_TASK_REVISION = "duplicate_task_revision"
    CONTRACT_COLLISION = "contract_collision"
    DUPLICATE_AGENT = "duplicate_agent"
    DUPLICATE_TOOL = "duplicate_tool"
    DUPLICATE_EFFORT = "duplicate_effort"
    DUPLICATE_EFFORT_RANK = "duplicate_effort_rank"
    DUPLICATE_PROFILE = "duplicate_profile"
    DUPLICATE_EVIDENCE_POLICY = "duplicate_evidence_policy"
    UNKNOWN_MODULE = "unknown_module"
    MISSING_MODULE_CONTRIBUTION = "missing_module_contribution"
    ORPHAN_MODULE_CONTRIBUTION = "orphan_module_contribution"
    UNKNOWN_AGENT = "unknown_agent"
    UNKNOWN_CONTRACT = "unknown_contract"
    UNKNOWN_TOOL = "unknown_tool"
    UNKNOWN_EFFORT = "unknown_effort"
    UNKNOWN_PROFILE = "unknown_profile"
    UNKNOWN_EVIDENCE_POLICY = "unknown_evidence_policy"
    INCOMPATIBLE_CONTRACT = "incompatible_contract"


class ResolutionErrorCode(StrEnum):
    UNKNOWN_TASK = "unknown_task"
    STALE_REVISION = "stale_revision"
    MODULE_DISABLED = "module_disabled"
    MODULE_DEGRADED = "module_degraded"
    INPUT_CONTRACT_MISMATCH = "input_contract_mismatch"
    UNKNOWN_EFFORT = "unknown_effort"
    EFFORT_RAISED = "effort_raised"
    BUDGET_RAISED = "budget_raised"
    EVIDENCE_SCOPE_WIDENED = "evidence_scope_widened"
    EVIDENCE_BUDGET_EXHAUSTED = "evidence_budget_exhausted"
    EVIDENCE_MANIFEST_MISMATCH = "evidence_manifest_mismatch"
    EVIDENCE_REFERENCE_MISMATCH = "evidence_reference_mismatch"
    DEPENDENCY_TASK_MISMATCH = "dependency_task_mismatch"
    MODULE_REGISTRY_MISMATCH = "module_registry_mismatch"
    SEMANTIC_REGISTRY_MISMATCH = "semantic_registry_mismatch"


@dataclass(frozen=True)
class SemanticRegistryIssue:
    code: RegistryIssueCode
    subject: str
    detail: str

    def sort_key(self) -> tuple[str, str, str]:
        return self.code.value, self.subject, self.detail


@dataclass(frozen=True)
class SemanticRegistryInspection:
    registry_hash: str
    issues: tuple[SemanticRegistryIssue, ...]

    @property
    def compatible(self) -> bool:
        return not self.issues

    def require_compatible(self) -> None:
        if self.issues:
            raise SemanticRegistryValidationError(self)


class SemanticRegistryValidationError(ValueError):
    def __init__(self, inspection: SemanticRegistryInspection) -> None:
        self.inspection = inspection
        summary = "; ".join(
            f"{issue.code.value}({issue.subject}): {issue.detail}"
            for issue in inspection.issues
        )
        super().__init__(f"semantic task registry rejected: {summary}")


class SemanticTaskResolutionError(ValueError):
    def __init__(
        self,
        code: ResolutionErrorCode,
        subject: str,
        detail: str,
    ) -> None:
        self.code = code
        self.subject = subject
        self.detail = detail
        super().__init__(f"{code.value}({subject}): {detail}")


def _duplicate_keys(values: Sequence[str]) -> set[str]:
    counts: dict[str, int] = defaultdict(int)
    for value in values:
        counts[value] += 1
    return {key for key, count in counts.items() if count > 1}


def _issue(
    issues: list[SemanticRegistryIssue],
    code: RegistryIssueCode,
    subject: str,
    detail: str,
) -> None:
    issues.append(SemanticRegistryIssue(code, subject, detail))


def _registry_payload(
    module_registry: BuiltInModuleRegistry,
    definitions: Sequence[SemanticTaskDefinition[BaseModel, BaseModel]],
    agents: Sequence[AgentContract],
    tools: Sequence[ToolContract],
    effort_profiles: Sequence[EffortProfile],
    model_profiles: Sequence[ModelProfileReference],
    evidence_policies: Sequence[EvidencePolicyContract],
) -> dict[str, object]:
    return {
        "registry_version": 1,
        "module_registry_hash": module_registry.combined_module_version,
        "definitions": [
            definition.canonical_payload()
            for definition in sorted(
                definitions,
                key=lambda candidate: (
                    candidate.task_kind,
                    candidate.contract_revision,
                ),
            )
        ],
        "agents": [
            {
                "key": agent.agent_key,
                "input_contract": agent.input_contract.model_dump(mode="json"),
                "output_contract": agent.output_contract.model_dump(mode="json"),
                "maximum_effort_key": agent.maximum_effort_key,
                "may_delegate": agent.may_delegate,
            }
            for agent in sorted(agents, key=lambda candidate: candidate.agent_key)
        ],
        "tools": sorted(tool.tool_key for tool in tools),
        "effort_profiles": [
            profile.model_dump(mode="json")
            for profile in sorted(effort_profiles, key=lambda candidate: candidate.key)
        ],
        "model_profiles": [
            profile.model_dump(mode="json")
            for profile in sorted(
                model_profiles,
                key=lambda candidate: (candidate.profile_key, candidate.revision),
            )
        ],
        "evidence_policies": sorted(policy.policy_key for policy in evidence_policies),
    }


def inspect_semantic_task_registry(
    module_registry: BuiltInModuleRegistry,
    definitions: Sequence[SemanticTaskDefinition[BaseModel, BaseModel]],
    agents: Sequence[AgentContract],
    tools: Sequence[ToolContract],
    effort_profiles: Sequence[EffortProfile],
    model_profiles: Sequence[ModelProfileReference],
    evidence_policies: Sequence[EvidencePolicyContract],
) -> SemanticRegistryInspection:
    issues: list[SemanticRegistryIssue] = []
    manifests = {manifest.module_id: manifest for manifest in module_registry.manifests}
    effort_by_key = {profile.key: profile for profile in effort_profiles}
    profile_refs = {
        (profile.profile_key, profile.revision): profile for profile in model_profiles
    }
    agent_by_key = {agent.agent_key: agent for agent in agents}
    tool_keys = {tool.tool_key for tool in tools}
    evidence_policy_keys = {policy.policy_key for policy in evidence_policies}

    duplicate_catalogs = (
        (
            RegistryIssueCode.DUPLICATE_AGENT,
            [agent.agent_key for agent in agents],
        ),
        (RegistryIssueCode.DUPLICATE_TOOL, [tool.tool_key for tool in tools]),
        (
            RegistryIssueCode.DUPLICATE_EFFORT,
            [profile.key for profile in effort_profiles],
        ),
        (
            RegistryIssueCode.DUPLICATE_PROFILE,
            [f"{profile.profile_key}@{profile.revision}" for profile in model_profiles],
        ),
        (
            RegistryIssueCode.DUPLICATE_EVIDENCE_POLICY,
            [policy.policy_key for policy in evidence_policies],
        ),
    )
    for code, catalog_keys in duplicate_catalogs:
        for catalog_key in sorted(_duplicate_keys(catalog_keys)):
            _issue(
                issues,
                code,
                catalog_key,
                "catalog key is registered more than once",
            )

    rank_keys = [str(profile.rank) for profile in effort_profiles]
    for rank in sorted(_duplicate_keys(rank_keys)):
        _issue(
            issues,
            RegistryIssueCode.DUPLICATE_EFFORT_RANK,
            rank,
            "effort ranks must define one total order",
        )

    definitions_by_key: dict[
        tuple[str, int], SemanticTaskDefinition[BaseModel, BaseModel]
    ] = {}
    typed_contracts: dict[
        tuple[str, int], TypedModelContract[BaseModel]
    ] = {}
    for definition in definitions:
        definition_key = definition.task_kind, definition.contract_revision
        if definition_key in definitions_by_key:
            _issue(
                issues,
                RegistryIssueCode.DUPLICATE_TASK_REVISION,
                f"{definition.task_kind}@{definition.contract_revision}",
                "task revision is registered more than once",
            )
        else:
            definitions_by_key[definition_key] = definition
        for contract in (definition.input_contract, definition.output_contract):
            contract_key = contract.contract_id, contract.revision
            erased_contract = cast(TypedModelContract[BaseModel], contract)
            prior_contract = typed_contracts.setdefault(
                contract_key,
                erased_contract,
            )
            if (
                prior_contract.schema_hash != contract.schema_hash
                or prior_contract.model_type is not contract.model_type
            ):
                _issue(
                    issues,
                    RegistryIssueCode.CONTRACT_COLLISION,
                    f"{contract.contract_id}@{contract.revision}",
                    "one contract revision has multiple schemas or model classes",
                )

        input_contract_issue = contract_model_issue(
            definition.input_contract.model_type
        )
        if input_contract_issue is not None:
            _issue(
                issues,
                RegistryIssueCode.INCOMPATIBLE_CONTRACT,
                f"{definition.task_kind}@{definition.contract_revision}",
                f"input contract is unsupported: {input_contract_issue}",
            )

        output_contract_issue = contract_model_issue(
            definition.output_contract.model_type
        )
        if output_contract_issue is not None:
            _issue(
                issues,
                RegistryIssueCode.INCOMPATIBLE_CONTRACT,
                f"{definition.task_kind}@{definition.contract_revision}",
                f"output contract is unsupported: {output_contract_issue}",
            )

    registered_task_kinds = {definition.task_kind for definition in definitions}
    contributed_task_kinds: dict[str, set[str]] = defaultdict(set)
    for manifest in module_registry.manifests:
        for contribution in manifest.contributions:
            if contribution.kind is ContributionKind.SEMANTIC_TASK:
                contributed_task_kinds[manifest.module_id].add(contribution.identifier)
                if contribution.identifier not in registered_task_kinds:
                    _issue(
                        issues,
                        RegistryIssueCode.ORPHAN_MODULE_CONTRIBUTION,
                        contribution.identifier,
                        f"declared by {manifest.module_id} without a task definition",
                    )

    for definition in definitions:
        subject = f"{definition.task_kind}@{definition.contract_revision}"
        if IDENTIFIER_PATTERN.fullmatch(definition.task_kind) is None:
            _issue(
                issues,
                RegistryIssueCode.INCOMPATIBLE_CONTRACT,
                subject,
                "task kind is not a canonical identifier",
            )
        if definition.contract_revision < 1:
            _issue(
                issues,
                RegistryIssueCode.INCOMPATIBLE_CONTRACT,
                subject,
                "contract revision must be positive",
            )
        resolved_manifest = manifests.get(definition.owning_module)
        if definition.owning_module == KERNEL_SEMANTIC_TASK_OWNER:
            pass
        elif resolved_manifest is None:
            _issue(
                issues,
                RegistryIssueCode.UNKNOWN_MODULE,
                subject,
                definition.owning_module,
            )
        elif (
            definition.task_kind
            not in contributed_task_kinds[resolved_manifest.module_id]
        ):
            _issue(
                issues,
                RegistryIssueCode.MISSING_MODULE_CONTRIBUTION,
                subject,
                f"{resolved_manifest.module_id} does not declare this semantic task",
            )

        for field_name, values in (
            ("allowed_delegate_keys", definition.allowed_delegate_keys),
            ("allowed_tool_keys", definition.allowed_tool_keys),
        ):
            if len(values) != len(set(values)):
                _issue(
                    issues,
                    RegistryIssueCode.INCOMPATIBLE_CONTRACT,
                    subject,
                    f"{field_name} contains duplicates",
                )

        for effort_key in (
            definition.default_effort_key,
            definition.maximum_effort_key,
        ):
            if effort_key not in effort_by_key:
                _issue(
                    issues,
                    RegistryIssueCode.UNKNOWN_EFFORT,
                    subject,
                    effort_key,
                )
        default_effort = effort_by_key.get(definition.default_effort_key)
        maximum_effort = effort_by_key.get(definition.maximum_effort_key)
        if (
            default_effort is not None
            and maximum_effort is not None
            and default_effort.rank > maximum_effort.rank
        ):
            _issue(
                issues,
                RegistryIssueCode.INCOMPATIBLE_CONTRACT,
                subject,
                "default effort exceeds maximum effort",
            )
        profile = profile_refs.get(
            (
                definition.model_profile.profile_key,
                definition.model_profile.revision,
            )
        )
        if profile is None or profile != definition.model_profile:
            _issue(
                issues,
                RegistryIssueCode.UNKNOWN_PROFILE,
                subject,
                (
                    f"{definition.model_profile.profile_key}@"
                    f"{definition.model_profile.revision}"
                ),
            )
        if definition.model_profile.effort_key != definition.maximum_effort_key:
            _issue(
                issues,
                RegistryIssueCode.INCOMPATIBLE_CONTRACT,
                subject,
                "model profile must match the task maximum effort",
            )
        if definition.evidence_policy_key not in evidence_policy_keys:
            _issue(
                issues,
                RegistryIssueCode.UNKNOWN_EVIDENCE_POLICY,
                subject,
                definition.evidence_policy_key,
            )
        for tool_key in definition.allowed_tool_keys:
            if tool_key not in tool_keys:
                _issue(
                    issues,
                    RegistryIssueCode.UNKNOWN_TOOL,
                    subject,
                    tool_key,
                )

        if definition.dispatch_mode is DispatchMode.DIRECT_LEAF:
            if (
                definition.leaf_agent_key is None
                or definition.allowed_delegate_keys
                or definition.allowed_tool_keys
                or definition.run_budget.max_delegate_calls != 0
                or definition.run_budget.max_parallel_delegates != 0
            ):
                _issue(
                    issues,
                    RegistryIssueCode.INCOMPATIBLE_CONTRACT,
                    subject,
                    "direct_leaf requires one leaf and no tools or delegation",
                )
            leaf = (
                agent_by_key.get(definition.leaf_agent_key)
                if definition.leaf_agent_key is not None
                else None
            )
            if leaf is None and definition.leaf_agent_key is not None:
                _issue(
                    issues,
                    RegistryIssueCode.UNKNOWN_AGENT,
                    subject,
                    definition.leaf_agent_key,
                )
            elif leaf is not None:
                if leaf.may_delegate:
                    _issue(
                        issues,
                        RegistryIssueCode.INCOMPATIBLE_CONTRACT,
                        subject,
                        "a direct leaf cannot delegate",
                    )
                if leaf.input_contract != definition.input_contract.reference:
                    _issue(
                        issues,
                        RegistryIssueCode.INCOMPATIBLE_CONTRACT,
                        subject,
                        "leaf input contract differs from the task input contract",
                    )
                if leaf.output_contract != definition.output_contract.reference:
                    _issue(
                        issues,
                        RegistryIssueCode.INCOMPATIBLE_CONTRACT,
                        subject,
                        "leaf output contract differs from the task output contract",
                    )
                leaf_effort = effort_by_key.get(leaf.maximum_effort_key)
                if leaf_effort is None:
                    _issue(
                        issues,
                        RegistryIssueCode.UNKNOWN_EFFORT,
                        subject,
                        leaf.maximum_effort_key,
                    )
                elif (
                    maximum_effort is not None
                    and maximum_effort.rank > leaf_effort.rank
                ):
                    _issue(
                        issues,
                        RegistryIssueCode.INCOMPATIBLE_CONTRACT,
                        subject,
                        "task effort exceeds the leaf agent ceiling",
                    )
        else:
            if definition.leaf_agent_key is not None:
                _issue(
                    issues,
                    RegistryIssueCode.INCOMPATIBLE_CONTRACT,
                    subject,
                    "conductor tasks cannot also name a direct leaf",
                )
            if (
                definition.run_budget.max_delegate_calls < 1
                or definition.run_budget.max_parallel_delegates < 1
            ):
                _issue(
                    issues,
                    RegistryIssueCode.INCOMPATIBLE_CONTRACT,
                    subject,
                    "conductor tasks require bounded non-zero delegation limits",
                )
            for delegate_key in definition.allowed_delegate_keys:
                delegate = agent_by_key.get(delegate_key)
                if delegate is None:
                    _issue(
                        issues,
                        RegistryIssueCode.UNKNOWN_AGENT,
                        subject,
                        delegate_key,
                    )
                    continue
                if delegate.may_delegate:
                    _issue(
                        issues,
                        RegistryIssueCode.INCOMPATIBLE_CONTRACT,
                        subject,
                        f"delegate {delegate_key} cannot recursively delegate",
                    )
                for boundary, reference in (
                    ("input", delegate.input_contract),
                    ("output", delegate.output_contract),
                ):
                    registered_contract = typed_contracts.get(
                        (reference.contract_id, reference.revision)
                    )
                    if (
                        registered_contract is None
                        or registered_contract.schema_hash != reference.schema_hash
                    ):
                        _issue(
                            issues,
                            RegistryIssueCode.UNKNOWN_CONTRACT,
                            subject,
                            (
                                f"delegate {delegate_key} {boundary} contract "
                                f"{reference.contract_id}@{reference.revision} "
                                "is not in the typed contract catalog"
                            ),
                        )
                delegate_effort = effort_by_key.get(delegate.maximum_effort_key)
                if delegate_effort is None:
                    _issue(
                        issues,
                        RegistryIssueCode.UNKNOWN_EFFORT,
                        subject,
                        f"{delegate_key}: {delegate.maximum_effort_key}",
                    )
                elif (
                    maximum_effort is not None
                    and maximum_effort.rank > delegate_effort.rank
                ):
                    _issue(
                        issues,
                        RegistryIssueCode.INCOMPATIBLE_CONTRACT,
                        subject,
                        f"task effort exceeds delegate {delegate_key} ceiling",
                    )

    payload = _registry_payload(
        module_registry,
        definitions,
        agents,
        tools,
        effort_profiles,
        model_profiles,
        evidence_policies,
    )
    return SemanticRegistryInspection(
        registry_hash=f"semantic-tasks-v1:{canonical_sha256(payload)}",
        issues=tuple(sorted(issues, key=SemanticRegistryIssue.sort_key)),
    )


class SemanticTaskRegistry:
    def __init__(
        self,
        definitions: tuple[SemanticTaskDefinition[BaseModel, BaseModel], ...],
        effort_profiles: Mapping[str, EffortProfile],
        module_registry_version: str,
        registry_hash: str,
    ) -> None:
        self._definitions = definitions
        contract_models: dict[tuple[str, int, str], type[BaseModel]] = {}
        for definition in definitions:
            for contract in (
                definition.input_contract,
                definition.output_contract,
            ):
                contract_models[
                    (
                        contract.contract_id,
                        contract.revision,
                        contract.schema_hash,
                    )
                ] = contract.model_type
        self._contract_models = MappingProxyType(contract_models)
        self._definitions_by_key = MappingProxyType(
            {
                (definition.task_kind, definition.contract_revision): definition
                for definition in definitions
            }
        )
        revisions: dict[str, list[int]] = defaultdict(list)
        for definition in definitions:
            revisions[definition.task_kind].append(definition.contract_revision)
        self._revisions = MappingProxyType(
            {
                task_kind: tuple(sorted(values))
                for task_kind, values in revisions.items()
            }
        )
        self._effort_profiles = MappingProxyType(dict(effort_profiles))
        self._module_registry_version = module_registry_version
        self.registry_hash = registry_hash

    @classmethod
    def _from_source_controlled(
        cls,
        module_registry: BuiltInModuleRegistry,
        definitions: Sequence[SemanticTaskDefinition[BaseModel, BaseModel]],
        agents: Sequence[AgentContract],
        tools: Sequence[ToolContract],
        effort_profiles: Sequence[EffortProfile],
        model_profiles: Sequence[ModelProfileReference],
        evidence_policies: Sequence[EvidencePolicyContract],
    ) -> SemanticTaskRegistry:
        inspection = inspect_semantic_task_registry(
            module_registry,
            definitions,
            agents,
            tools,
            effort_profiles,
            model_profiles,
            evidence_policies,
        )
        inspection.require_compatible()
        return cls(
            tuple(
                sorted(
                    definitions,
                    key=lambda candidate: (
                        candidate.task_kind,
                        candidate.contract_revision,
                    ),
                )
            ),
            {profile.key: profile for profile in effort_profiles},
            module_registry.combined_module_version,
            inspection.registry_hash,
        )

    @property
    def definitions(self) -> tuple[SemanticTaskDefinition[BaseModel, BaseModel], ...]:
        return self._definitions

    def contract_model_type(self, reference: ContractReference) -> type[BaseModel]:
        """Resolve a validated contract reference to its registered model class."""

        key = reference.contract_id, reference.revision, reference.schema_hash
        try:
            return self._contract_models[key]
        except KeyError as error:
            raise KeyError(
                f"typed contract is not registered: {reference.contract_id}@"
                f"{reference.revision} ({reference.schema_hash})"
            ) from error

    def resolve(
        self,
        task_input: SemanticTaskInput[BaseModel],
        composition: CompositionHealthReport,
        deps: SemanticRunDeps,
    ) -> ResolvedSemanticTask[BaseModel, BaseModel]:
        if deps.task_id != task_input.task_id:
            raise SemanticTaskResolutionError(
                ResolutionErrorCode.DEPENDENCY_TASK_MISMATCH,
                task_input.task_id,
                "trusted dependencies belong to a different task",
            )
        if deps.semantic_registry_hash != self.registry_hash:
            raise SemanticTaskResolutionError(
                ResolutionErrorCode.SEMANTIC_REGISTRY_MISMATCH,
                task_input.task_id,
                "trusted dependencies pin a different semantic registry revision",
            )
        if (
            deps.task_kind != task_input.task_kind
            or deps.contract_revision != task_input.contract_revision
        ):
            raise SemanticTaskResolutionError(
                ResolutionErrorCode.DEPENDENCY_TASK_MISMATCH,
                task_input.task_id,
                "trusted dependencies pin a different task kind or revision",
            )

        definition = self._definitions_by_key.get(
            (task_input.task_kind, task_input.contract_revision)
        )
        if definition is None:
            revisions = self._revisions.get(task_input.task_kind)
            if revisions is None:
                raise SemanticTaskResolutionError(
                    ResolutionErrorCode.UNKNOWN_TASK,
                    task_input.task_kind,
                    "task kind is not code-registered",
                )
            raise SemanticTaskResolutionError(
                ResolutionErrorCode.STALE_REVISION,
                task_input.task_kind,
                f"requested {task_input.contract_revision}; registered {revisions}",
            )
        if deps.task_contract_hash != definition.contract_hash:
            raise SemanticTaskResolutionError(
                ResolutionErrorCode.DEPENDENCY_TASK_MISMATCH,
                task_input.task_id,
                "trusted dependencies pin a different task contract hash",
            )

        input_model_type = definition.input_contract.model_type
        if type(task_input) is not input_model_type:
            raise SemanticTaskResolutionError(
                ResolutionErrorCode.INPUT_CONTRACT_MISMATCH,
                task_input.task_kind,
                definition.input_contract.contract_id,
            )
        try:
            task_input = input_model_type.model_validate(task_input)
        except Exception as error:
            raise SemanticTaskResolutionError(
                ResolutionErrorCode.INPUT_CONTRACT_MISMATCH,
                task_input.task_kind,
                "task input failed complete contract revalidation",
            ) from error

        expected_composition_version = f"modules-v1:{
            canonical_sha256(
                {
                    'registry': self._module_registry_version,
                    'profiles': list(composition.profile_ids),
                    'modules': [
                        {
                            'id': module.module_id,
                            'version': module.version,
                            'state': module.state.value,
                        }
                        for module in composition.modules
                    ],
                }
            )
        }"
        if composition.combined_module_version != expected_composition_version:
            raise SemanticTaskResolutionError(
                ResolutionErrorCode.MODULE_REGISTRY_MISMATCH,
                task_input.task_kind,
                "composition was produced by a different module registry revision",
            )

        if definition.owning_module != KERNEL_SEMANTIC_TASK_OWNER:
            module_states = {
                module.module_id: module.state for module in composition.modules
            }
            state = module_states.get(definition.owning_module, ModuleState.DISABLED)
            if state is ModuleState.DISABLED:
                raise SemanticTaskResolutionError(
                    ResolutionErrorCode.MODULE_DISABLED,
                    definition.owning_module,
                    "owning module is not enabled in this composition",
                )
            if state is not ModuleState.ENABLED:
                raise SemanticTaskResolutionError(
                    ResolutionErrorCode.MODULE_DEGRADED,
                    definition.owning_module,
                    f"owning module state is {state.value}",
                )
        effort_key = task_input.requested_effort_key or definition.default_effort_key
        effort = self._effort_profiles.get(effort_key)
        if effort is None:
            raise SemanticTaskResolutionError(
                ResolutionErrorCode.UNKNOWN_EFFORT,
                task_input.task_kind,
                effort_key,
            )
        maximum_effort = self._effort_profiles[definition.maximum_effort_key]
        if effort.rank > maximum_effort.rank:
            raise SemanticTaskResolutionError(
                ResolutionErrorCode.EFFORT_RAISED,
                task_input.task_kind,
                f"requested {effort.key}; maximum {maximum_effort.key}",
            )

        effective_budget: RunBudget
        if task_input.requested_budget is None:
            effective_budget = definition.run_budget
        elif task_input.requested_budget.is_not_wider_than(definition.run_budget):
            effective_budget = task_input.requested_budget
        else:
            raise SemanticTaskResolutionError(
                ResolutionErrorCode.BUDGET_RAISED,
                task_input.task_kind,
                "requested budget exceeds the registered ceiling",
            )

        authorized_manifest = deps.authorized_evidence_manifest
        requested_manifest = task_input.evidence_manifest
        if (
            requested_manifest.manifest_id != authorized_manifest.manifest_id
            or requested_manifest.revision != authorized_manifest.revision
        ):
            raise SemanticTaskResolutionError(
                ResolutionErrorCode.EVIDENCE_MANIFEST_MISMATCH,
                task_input.task_kind,
                "evidence manifest identity or revision differs from authorization",
            )
        authorized_by_id = {
            reference.reference_id: reference
            for reference in authorized_manifest.references
        }
        requested_by_id = {
            reference.reference_id: reference
            for reference in requested_manifest.references
        }
        evidence_refs = set(requested_by_id)
        widened = evidence_refs - authorized_by_id.keys()
        if widened:
            raise SemanticTaskResolutionError(
                ResolutionErrorCode.EVIDENCE_SCOPE_WIDENED,
                task_input.task_kind,
                ", ".join(sorted(widened)),
            )
        mismatched = sorted(
            reference_id
            for reference_id, requested in requested_by_id.items()
            if requested != authorized_by_id[reference_id]
        )
        if mismatched:
            raise SemanticTaskResolutionError(
                ResolutionErrorCode.EVIDENCE_REFERENCE_MISMATCH,
                task_input.task_kind,
                "authorized evidence metadata differs for: " + ", ".join(mismatched),
            )
        declared_characters = sum(
            authorized_by_id[reference_id].declared_characters
            for reference_id in evidence_refs
        )
        if (
            len(evidence_refs) > effective_budget.evidence_item_limit
            or declared_characters > effective_budget.hydrated_character_limit
        ):
            raise SemanticTaskResolutionError(
                ResolutionErrorCode.EVIDENCE_BUDGET_EXHAUSTED,
                task_input.task_kind,
                "evidence manifest exceeds the effective registered budget",
            )

        return ResolvedSemanticTask(
            definition=definition,
            task_input=task_input,
            effective_effort=effort,
            effective_budget=effective_budget,
            semantic_registry_hash=self.registry_hash,
        )


def task_definitions_by_hash(
    registry: SemanticTaskRegistry,
) -> tuple[tuple[str, str], ...]:
    return tuple(
        (
            f"{definition.task_kind}@{definition.contract_revision}",
            definition.contract_hash,
        )
        for definition in registry.definitions
    )
