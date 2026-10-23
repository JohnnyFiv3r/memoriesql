"""One evidence-free repair round inside the run (guard audit P1-f (a)); DB-free.

Fictional task with annotation-shaped output. A first output canonical apply
would refuse gets one repair frame: the task input, the model's own output and
fixed refusal codes, never evidence or database text. The repaired output is
checked again; a second failure still settles invalid_output.
"""

from __future__ import annotations

import hashlib
import json
import time
import unittest
from typing import TYPE_CHECKING, Any, cast

from pydantic import BaseModel
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import RequestUsage

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
    TypedModelContract,
)
from memoriesql.application.semantic_task_registry import SemanticTaskRegistry
from memoriesql.infrastructure.models.pydanticai_executor import (
    LeafAgentSpec,
    ModelProfileBinding,
    PydanticAIAgentRegistry,
    PydanticAIModelProfileRegistry,
    PydanticAISemanticExecutor,
)

if TYPE_CHECKING:
    from tests.runtime.test_executor import Ports
else:
    from test_executor import Ports

UNIT = "tree-count"
BEAD = "orchard-bead"
STRAY = "orchard-stray-bead"


class OrchardUnits(FrozenContractModel):
    event_id: str
    bead_ids: tuple[str, ...]
    source_unit_ids: tuple[str, ...]


class OrchardEvidence(FrozenContractModel):
    source_unit_id: str
    content_hash: str


class OrchardStatement(FrozenContractModel):
    statement_id: str
    statement_text: str
    evidence: tuple[OrchardEvidence, ...]
    context_source_ids: tuple[str, ...] = ()


class OrchardAnnotation(FrozenContractModel):
    bead_id: str
    event_id: str
    source_unit_id: str
    statements: tuple[OrchardStatement, ...]


class OrchardAnnotations(FrozenContractModel):
    annotations: tuple[OrchardAnnotation, ...]


def annotated(ports: Ports, bead: str) -> dict[str, Any]:
    return {
        "annotations": [
            {
                "bead_id": bead,
                "event_id": "orchard-event",
                "source_unit_id": UNIT,
                "statements": [
                    {
                        "statement_id": "orchard-statement",
                        "statement_text": "Four fictional trees.",
                        "evidence": [
                            {
                                "source_unit_id": UNIT,
                                "content_hash": hashlib.sha256(
                                    ports.content.encode()
                                ).hexdigest(),
                            }
                        ],
                    }
                ],
            }
        ]
    }


def fixture(
    outputs: list[str], *, request_limit: int = 2
) -> tuple[Any, Any, SemanticRunDeps, Ports, list[str]]:
    """One task; the model answers with the next bead of `outputs` per call."""

    ports = Ports()
    prompts: list[str] = []
    modules = BuiltInModuleRegistry._from_source_controlled(
        (), (ProfileDefinition("core", ()),), {}
    )
    profile = ModelProfileReference(
        profile_key="orchard.frontier", revision=1, effort_key="frontier"
    )
    input_contract = TypedModelContract(
        contract_id="orchard.units", revision=1, model_type=SemanticTaskInput[OrchardUnits]
    )
    output_contract = TypedModelContract(
        contract_id="orchard.annotations", revision=1, model_type=OrchardAnnotations
    )
    agent = AgentContract(
        agent_key="orchard.annotator",
        input_contract=input_contract.reference,
        output_contract=output_contract.reference,
        maximum_effort_key="frontier",
    )
    task = SemanticTaskDefinition(
        task_kind="orchard.annotate",
        contract_revision=1,
        owning_module="memoriesql.kernel",
        input_contract=input_contract,
        output_contract=output_contract,
        dispatch_mode=DispatchMode.DIRECT_LEAF,
        default_effort_key="frontier",
        maximum_effort_key="frontier",
        model_profile=profile,
        evidence_policy_key="manifest-only",
        run_budget=RunBudget(
            wall_clock_seconds=10,
            request_limit=request_limit,
            input_token_limit=10000,
            output_token_limit=10000,
            total_token_limit=20000,
            tool_call_limit=0,
            max_delegate_calls=0,
            max_parallel_delegates=0,
            evidence_item_limit=1,
            hydrated_character_limit=1000,
            output_retries=0,
            tool_retries=0,
        ),
        leaf_agent_key=agent.agent_key,
    )
    registry = SemanticTaskRegistry._from_source_controlled(
        modules,
        (cast(SemanticTaskDefinition[BaseModel, BaseModel], task),),
        (agent,),
        (),
        (EffortProfile(key="frontier", rank=0),),
        (profile,),
        (EvidencePolicyContract(policy_key="manifest-only"),),
    )
    manifest = EvidenceManifest(
        manifest_id="orchard.evidence",
        revision=1,
        references=(
            EvidenceReference(
                reference_id=UNIT,
                content_hash=hashlib.sha256(ports.content.encode()).hexdigest(),
                declared_characters=len(ports.content),
            ),
        ),
    )
    task_input = SemanticTaskInput[OrchardUnits](
        task_id="orchard.task",
        task_kind=task.task_kind,
        contract_revision=1,
        target_reference="orchard.plot",
        expected_target_revision=1,
        evidence_manifest=manifest,
        payload=OrchardUnits(event_id="orchard-event", bead_ids=(BEAD,), source_unit_ids=(UNIT,)),
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
        prompts.extend(
            cast(str, part.content)
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, UserPromptPart)
        )
        bead = outputs[min(len(prompts), len(outputs)) - 1]
        return ModelResponse(
            parts=[
                ToolCallPart(
                    info.output_tools[0].name,
                    {"typed_output": annotated(ports, bead), "used_evidence_refs": [UNIT]},
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
                instructions="Annotate the fictional evidence.",
                model_profiles=(profile,),
            ),
        ),
        conductors=(),
        agent_contracts=(agent,),
    )
    executor = PydanticAISemanticExecutor(
        semantic_registry=registry,
        composition_provider=lambda: composition,
        agent_registry=agents,
        model_profiles=PydanticAIModelProfileRegistry(
            (ModelProfileBinding(profile, FunctionModel(respond)),)
        ),
    )
    return executor, resolved, deps, ports, prompts


class RepairFrame(unittest.IsolatedAsyncioTestCase):
    async def test_one_repair_round_feeds_back_fixed_codes_without_evidence(self) -> None:
        executor, task, deps, ports, prompts = fixture([STRAY, BEAD])
        result = await executor.execute(task, deps)
        self.assertEqual(result.status, "succeeded", result)
        self.assertEqual(result.typed_output.annotations[0].bead_id, BEAD)
        self.assertEqual(ports.order.count("model"), 2)
        self.assertEqual(ports.order.count("hydrate"), 1)
        self.assertEqual((len(ports.intents), len(ports.usages)), (2, 2))
        self.assertEqual(result.usage.requests, 2)
        self.assertEqual(len(prompts), 2)
        self.assertIn(ports.content, prompts[0])
        frame = json.loads(prompts[1])
        self.assertEqual(
            set(frame),
            {"repair_frame", "instruction", "task_input", "previous_output", "refusal_codes"},
        )
        # No evidence is sent again, and nothing but fixed codes and identifiers.
        self.assertNotIn(ports.content, prompts[1])
        self.assertNotIn("hydrated_evidence", prompts[1])
        self.assertEqual(
            frame["refusal_codes"],
            [
                f"annotation.bead_missing: {BEAD}",
                f"annotation.bead_outside_task: {STRAY}",
            ],
        )
        self.assertEqual(
            frame["previous_output"]["typed_output"]["annotations"][0]["bead_id"], STRAY
        )
        self.assertEqual(frame["task_input"]["payload"]["bead_ids"], [BEAD])

    async def test_a_second_failure_still_settles_invalid_output(self) -> None:
        executor, task, deps, ports, prompts = fixture([STRAY, STRAY])
        result = await executor.execute(task, deps)
        self.assertEqual(
            (result.status, result.error_code, result.retry_class),
            ("invalid_output", "output.annotation.bead_missing", "never"),
        )
        self.assertIsNone(result.typed_output)
        # Exactly one repair: two dispatches, both accounted.
        self.assertEqual(ports.order.count("model"), 2)
        self.assertEqual((len(ports.intents), len(ports.usages)), (2, 2))

    async def test_no_repair_without_a_request_left_in_the_budget(self) -> None:
        executor, task, deps, ports, prompts = fixture([STRAY, BEAD], request_limit=1)
        result = await executor.execute(task, deps)
        self.assertEqual(
            (result.status, result.error_code),
            ("invalid_output", "output.annotation.bead_missing"),
        )
        self.assertEqual(ports.order.count("model"), 1)
        self.assertEqual(len(ports.intents), 1)

    async def test_a_valid_first_output_needs_no_repair(self) -> None:
        executor, task, deps, ports, prompts = fixture([BEAD])
        result = await executor.execute(task, deps)
        self.assertEqual(result.status, "succeeded", result)
        self.assertEqual(ports.order.count("model"), 1)
        self.assertEqual(len(prompts), 1)


if __name__ == "__main__":
    unittest.main()
