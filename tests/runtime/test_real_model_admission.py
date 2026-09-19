"""Fictional bounded transport tests; no provider network or credentials."""
import unittest
from dataclasses import replace
from typing import TYPE_CHECKING, Any, cast

from pydantic_ai.models.function import FunctionModel

from memoriesql.infrastructure.models.pydanticai_executor import (
    BoundedProviderModel,
    ModelProfileBinding,
    PydanticAIModelProfileRegistry,
    RealModelAdmission,
)

if TYPE_CHECKING:
    from tests.runtime.test_executor import fixture
else:
    try:
        from tests.runtime.test_executor import fixture
    except ModuleNotFoundError:  # copied installed-artifact acceptance lane
        from test_executor import fixture


class FictionalBoundedModel(BoundedProviderModel):
    def __init__(self, callback: Any) -> None:
        self.inner = FunctionModel(callback)
        super().__init__(profile=self.inner.profile)
        self.bounds: list[Any] = []

    @property
    def model_name(self) -> str:
        return "fictional-bounded"

    @property
    def system(self) -> str:
        return "fictional"

    async def request_bounded(self, messages: Any, settings: Any,
                              parameters: Any, bounds: Any) -> Any:
        self.bounds.append(bounds)
        return await self.inner.request(messages, settings, parameters)


def admitted(profile: Any, callback: Any, target: Any) -> ModelProfileBinding:
    model = FictionalBoundedModel(callback)
    target = target.model_copy(update={
        "max_input_tokens_per_request": 600,
        "max_output_tokens_per_request": 600,
    })
    return ModelProfileBinding(
        profile, model, request_target=target,
        real_model_admission=RealModelAdmission(model, profile, target, "fictional.v1"),
    )


class AdmissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_admitted_model_uses_existing_accounting_path(self) -> None:
        executor, task, deps, ports = fixture(binding_factory=admitted)
        result = await executor.execute(task, deps)
        self.assertEqual(result.status, "succeeded", result)
        self.assertEqual(ports.order, ["hydrate", "intent", "model", "usage"])
        self.assertEqual(ports.intents[0].max_input_tokens, 600)
        self.assertEqual(ports.intents[0].max_output_tokens, 600)

    async def test_next_maximum_is_reserved_before_dispatch(self) -> None:
        executor, task, deps, ports = fixture(conductor=True, binding_factory=admitted)
        result = await executor.execute(task, deps)
        self.assertEqual(result.status, "budget_exhausted", result)
        # Actual first usage is tiny. Reserving the next 600 would exceed 1000.
        self.assertEqual(ports.order.count("model"), 1)
        self.assertEqual(len(ports.intents), 1)
        self.assertEqual(len(ports.usages), 1)

    async def test_cancelled_return_still_accounts(self) -> None:
        executor, task, deps, ports = fixture(binding_factory=admitted)
        ports.cancel_on_response = True
        result = await executor.execute(task, deps)
        self.assertEqual(result.status, "cancelled", result)
        self.assertEqual(len(ports.usages), 1)
        self.assertIsNone(result.typed_output)

    async def test_failed_intent_never_dispatches(self) -> None:
        executor, task, deps, ports = fixture(binding_factory=admitted)
        ports.fail_intent = True
        result = await executor.execute(task, deps)
        self.assertNotEqual(result.status, "succeeded")
        self.assertNotIn("model", ports.order)

    async def test_unaffordable_first_request_has_no_intent_or_inference(self) -> None:
        def too_large(profile: Any, callback: Any, target: Any) -> Any:
            binding = admitted(profile, callback, target)
            target = binding.request_target.model_copy(update={
                "max_input_tokens_per_request": 1001,
            })
            return replace(binding, request_target=target,
                real_model_admission=replace(cast(RealModelAdmission, binding.real_model_admission),
                                             request_target=target))
        executor, task, deps, ports = fixture(binding_factory=too_large)
        result = await executor.execute(task, deps)
        self.assertEqual(result.status, "budget_exhausted", result)
        self.assertEqual(ports.intents, [])
        self.assertNotIn("model", ports.order)

    async def test_reported_overrun_is_accounted_but_rejected(self) -> None:
        from pydantic_ai.messages import ModelResponse, TextPart
        from pydantic_ai.usage import RequestUsage
        def overrun(*args: Any) -> Any:
            return ModelResponse(parts=[TextPart("untrusted")],
                                 usage=RequestUsage(input_tokens=601, output_tokens=1))
        executor, task, deps, ports = fixture(binding_factory=admitted,
                                             model_callback=overrun)
        result = await executor.execute(task, deps)
        self.assertEqual(result.status, "budget_exhausted", result)
        self.assertEqual(len(ports.usages), 1)
        self.assertIsNone(result.typed_output)

    def test_default_denial_and_exact_admission(self) -> None:
        captured = []
        def capture(profile: Any, callback: Any, target: Any) -> Any:
            binding = admitted(profile, callback, target)
            captured.append(binding)
            return binding
        fixture(binding_factory=capture)
        binding = captured[0]
        with self.assertRaisesRegex(ValueError, "explicit bounded admission"):
            replace(binding, real_model_admission=None)
        with self.assertRaisesRegex(ValueError, "exact binding"):
            replace(binding, model=FictionalBoundedModel(lambda *_: None))
        with self.assertRaisesRegex(ValueError, "exact binding"):
            replace(binding, request_target=binding.request_target.model_copy(
                update={"model_id": "other"}))
        with self.assertRaisesRegex(ValueError, "cannot share"):
            PydanticAIModelProfileRegistry((binding, ModelProfileBinding(
                binding.reference.model_copy(update={"revision": 2}),
                FunctionModel(cast(Any, lambda *_: None)))))
