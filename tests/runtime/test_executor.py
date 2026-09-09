"""Public-owned fictional execution cases; no provider or product composition."""

from __future__ import annotations

import hashlib
import time
import unittest
from dataclasses import replace
from typing import Any, cast

from pydantic import BaseModel
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import RequestUsage

from memoriesql.application.builtin_semantic_tasks import (
    load_builtin_semantic_task_registry,
)
from memoriesql.application.module_registry import (
    BuiltInModuleRegistry,
    ProfileDefinition,
)
from memoriesql.application.semantic_task_contracts import (
    AgentContract,
    DispatchMode,
    EffortProfile,
    EvidenceManifest,
    EvidencePolicyContract,
    EvidenceReference,
    FrozenContractModel,
    ModelProfileReference,
    RunBudget,
    SemanticAuthorizationContext,
    SemanticRunDeps,
    SemanticTaskDefinition,
    SemanticTaskInput,
    ToolContract,
    TypedModelContract,
    UsageSummary,
)
from memoriesql.application.semantic_task_registry import SemanticTaskRegistry
from memoriesql.infrastructure.models.pydanticai_executor import (
    CHARACTERIZED_TEST_REQUEST_TARGET,
    ConductorAgentSpec,
    LeafAgentSpec,
    ModelProfileBinding,
    PydanticAIAgentRegistry,
    PydanticAIModelProfileRegistry,
    PydanticAISemanticExecutor,
)


class OrchardInput(FrozenContractModel):
    request: str


class OrchardOutput(FrozenContractModel):
    answer: str


class Ports:
    def __init__(self) -> None:
        self.cancelled = False
        self.cancel_on_response = False
        self.content = "The fictional orchard has four trees."
        self.fail_intent = False
        self.order: list[str] = []
        self.events: list[Any] = []
        self.intents: list[Any] = []
        self.usages: list[Any] = []
        self.usage = UsageSummary()

    def is_cancelled(self) -> bool:
        return self.cancelled

    async def read_authorized_evidence(self, reference: EvidenceReference) -> str:
        self.order.append("hydrate")
        return self.content

    def snapshot(self) -> UsageSummary:
        return self.usage

    def replace(self, usage: UsageSummary) -> None:
        self.usage = usage

    def retain_after_failure(self, usage: UsageSummary) -> None:
        self.usage = usage

    async def append(self, event: Any) -> None:
        self.events.append(event)

    async def record_intent(self, intent: Any) -> None:
        self.order.append("intent")
        if self.fail_intent:
            raise RuntimeError("fictional accounting store unavailable")
        self.intents.append(intent)

    async def append_usage(self, event: Any) -> None:
        self.order.append("usage")
        self.usages.append(event)


def fixture(
    *, conductor: bool = False, model_callback: Any = None,
    request_target: Any = CHARACTERIZED_TEST_REQUEST_TARGET
) -> tuple[Any, Any, SemanticRunDeps, Ports]:
    ports = Ports()
    modules = BuiltInModuleRegistry._from_source_controlled(
        (), (ProfileDefinition("core", ()),), {}
    )
    profile = ModelProfileReference(
        profile_key="orchard.frontier", revision=1, effort_key="frontier"
    )
    input_contract = TypedModelContract(
        contract_id="orchard.input",
        revision=1,
        model_type=SemanticTaskInput[OrchardInput],
    )
    output_contract = TypedModelContract(
        contract_id="orchard.output", revision=1, model_type=OrchardOutput
    )
    agent = AgentContract(
        agent_key="orchard.author",
        input_contract=input_contract.reference,
        output_contract=output_contract.reference,
        maximum_effort_key="frontier",
    )
    budget = RunBudget(
        wall_clock_seconds=10,
        request_limit=2,
        input_token_limit=1000,
        output_token_limit=1000,
        total_token_limit=2000,
        tool_call_limit=0,
        cost_safety_limit_microusd=None,
        max_delegate_calls=0,
        max_parallel_delegates=0,
        evidence_item_limit=1,
        hydrated_character_limit=1000,
        output_retries=0,
        tool_retries=0,
    )
    task = SemanticTaskDefinition(
        task_kind="orchard.author",
        contract_revision=1,
        owning_module="memoriesql.kernel",
        input_contract=input_contract,
        output_contract=output_contract,
        dispatch_mode=DispatchMode.DIRECT_LEAF,
        default_effort_key="frontier",
        maximum_effort_key="frontier",
        model_profile=profile,
        evidence_policy_key="manifest-only",
        run_budget=budget,
        leaf_agent_key=agent.agent_key,
    )
    if conductor:
        task = replace(
            task,
            dispatch_mode=DispatchMode.CONDUCTOR,
            leaf_agent_key=None,
            allowed_delegate_keys=(agent.agent_key,),
            allowed_tool_keys=("orchard.read",),
            run_budget=budget.model_copy(
                update={
                    "max_delegate_calls": 1,
                    "max_parallel_delegates": 1,
                    "tool_call_limit": 1,
                }
            ),
        )
    registry = SemanticTaskRegistry._from_source_controlled(
        modules,
        (cast(SemanticTaskDefinition[BaseModel, BaseModel], task),),
        (agent,),
        (ToolContract(tool_key="orchard.read"),) if conductor else (),
        (EffortProfile(key="frontier", rank=0),),
        (profile,),
        (EvidencePolicyContract(policy_key="manifest-only"),),
    )
    manifest = EvidenceManifest(
        manifest_id="orchard.evidence",
        revision=1,
        references=(
            EvidenceReference(
                reference_id="tree-count",
                content_hash=hashlib.sha256(ports.content.encode()).hexdigest(),
                declared_characters=len(ports.content),
            ),
        ),
    )
    task_input = SemanticTaskInput[OrchardInput](
        task_id="orchard.task",
        task_kind=task.task_kind,
        contract_revision=1,
        target_reference="orchard.plot",
        expected_target_revision=1,
        evidence_manifest=manifest,
        payload=OrchardInput(request="Count the fictional trees."),
    )
    deps = SemanticRunDeps(
        task_id=task_input.task_id,
        task_kind=task.task_kind,
        contract_revision=1,
        task_contract_hash=task.contract_hash,
        semantic_registry_hash=registry.registry_hash,
        attempt_id="orchard.attempt",
        lease_generation=1,
        authorization=SemanticAuthorizationContext(
            principal_id="orchard.reader",
            delegation_id=None,
            tenant_id="orchard.tenant",
            workspace_id="orchard.workspace",
            policy_revision=1,
            allowed_access_scope_ids=frozenset({"orchard.scope"}),
        ),
        authorized_evidence_manifest=manifest,
        evidence_accessor=ports,
        cancellation=ports,
        usage=ports,
        event_sink=ports,
        model_accounting=ports,
        monotonic_deadline_ns=time.monotonic_ns() + 10_000_000_000,
    )
    composition = modules.compose(("core",))
    resolved = registry.resolve(
        cast(SemanticTaskInput[BaseModel], task_input), composition, deps
    )

    def respond(messages: Any, info: Any) -> ModelResponse:
        ports.order.append("model")
        if conductor and ports.order.count("model") == 1:
            return ModelResponse(
                parts=[ToolCallPart("orchard.read", {"reference_id": "tree-count"})],
                usage=RequestUsage(input_tokens=3, output_tokens=2),
            )
        if ports.cancel_on_response:
            ports.cancelled = True
        return ModelResponse(
            parts=[
                ToolCallPart(
                    info.output_tools[0].name,
                    {
                        "typed_output": {"answer": "Four fictional trees."},
                        "used_evidence_refs": ["tree-count"],
                    },
                )
            ],
            usage=RequestUsage(input_tokens=11, output_tokens=7),
        )

    agents = PydanticAIAgentRegistry(
        leaf_specs=(
            LeafAgentSpec(
                agent_key=agent.agent_key,
                input_contract=input_contract.reference,
                output_contract=output_contract.reference,
                input_model_type=input_contract.model_type,
                output_model_type=output_contract.model_type,
                instructions="Use the fictional evidence.",
                model_profiles=(profile,),
            ),
        ),
        conductors=(
            ConductorAgentSpec(
                task_kind=task.task_kind,
                contract_revision=1,
                agent_key="orchard.conductor",
                evidence_tool_key="orchard.read",
                input_contract=input_contract.reference,
                output_contract=output_contract.reference,
                input_model_type=input_contract.model_type,
                output_model_type=output_contract.model_type,
                instructions="Return the fictional structured result.",
            ),
        )
        if conductor
        else (),
        agent_contracts=(agent,),
    )
    executor = PydanticAISemanticExecutor(
        semantic_registry=registry,
        composition_provider=lambda: composition,
        agent_registry=agents,
        model_profiles=PydanticAIModelProfileRegistry(
            (ModelProfileBinding(profile, FunctionModel(model_callback or respond), request_target=request_target),)
        ),
    )
    return executor, resolved, deps, ports


class ExecutionParity(unittest.IsolatedAsyncioTestCase):
    async def test_direct_execution_retains_request_accounting(self) -> None:
        executor, task, deps, ports = fixture()
        result = await executor.execute(task, deps)
        self.assertEqual(result.status, "succeeded", result)
        self.assertEqual(result.typed_output.answer, "Four fictional trees.")
        self.assertEqual(ports.order, ["hydrate", "intent", "model", "usage"])
        self.assertEqual(
            (
                result.usage.requests,
                result.usage.input_tokens,
                result.usage.output_tokens,
            ),
            (1, 11, 7),
        )
        self.assertEqual(len(ports.intents), 1)
        self.assertEqual(len(ports.usages), 1)

    async def test_late_rejected_output_retains_usage(self) -> None:
        executor, task, deps, ports = fixture()
        ports.cancel_on_response = True
        result = await executor.execute(task, deps)
        self.assertEqual(result.status, "cancelled")
        self.assertIsNone(result.typed_output)
        self.assertEqual(result.usage.requests, 1)
        self.assertEqual(len(ports.intents), 1)
        self.assertEqual(len(ports.usages), 1)

    async def test_bounded_conductor_execution_remains_supported(self) -> None:
        executor, task, deps, ports = fixture(conductor=True)
        result = await executor.execute(task, deps)
        self.assertEqual(result.status, "succeeded", result)
        self.assertEqual(result.typed_output.answer, "Four fictional trees.")
        self.assertEqual(len(ports.intents), 2)
        self.assertEqual(len(ports.usages), 2)

    async def test_cancel_during_intent_commit_prevents_model_dispatch(self) -> None:
        executor, task, deps, ports = fixture()
        original = ports.record_intent

        async def commit_then_cancel(intent: Any) -> None:
            await original(intent)
            ports.cancelled = True

        from unittest.mock import patch
        with patch.object(ports, "record_intent", commit_then_cancel):
            result = await executor.execute(task, deps)
        self.assertEqual(result.status, "cancelled")
        self.assertEqual(len(ports.intents), 1)
        self.assertEqual(ports.usages, [])
        self.assertNotIn("model", ports.order)

    async def test_cancel_before_dispatch(self) -> None:
        executor, task, deps, ports = fixture()
        ports.cancelled = True
        result = await executor.execute(task, deps)
        self.assertEqual(result.status, "cancelled")
        self.assertNotIn("model", ports.order)

    async def test_stale_evidence_never_dispatches(self) -> None:
        executor, task, deps, ports = fixture()
        ports.content = "Altered fictional evidence."
        result = await executor.execute(task, deps)
        self.assertNotEqual(result.status, "succeeded")
        self.assertNotIn("model", ports.order)

    async def test_accounting_failure_prevents_dispatch(self) -> None:
        executor, task, deps, ports = fixture()
        ports.fail_intent = True
        result = await executor.execute(task, deps)
        self.assertNotEqual(result.status, "succeeded")
        self.assertNotIn("model", ports.order)

    async def test_registry_mismatch_rejects_inflight_work(self) -> None:
        executor, task, deps, ports = fixture()
        result = await executor.execute(
            task, replace(deps, semantic_registry_hash="0" * 64)
        )
        self.assertNotEqual(result.status, "succeeded")
        self.assertNotIn("model", ports.order)

    def test_canonical_registry_requires_static_composition(self) -> None:
        modules = BuiltInModuleRegistry._from_source_controlled(
            (), (ProfileDefinition("core", ()),), {}
        )
        registry = load_builtin_semantic_task_registry(modules)
        self.assertEqual(len(registry.definitions), 1)
        self.assertEqual(
            registry.definitions[0].task_kind, "memory.semantic.author-observations"
        )
        self.assertEqual(registry.definitions[0].contract_revision, 1)


if __name__ == "__main__":
    unittest.main()
