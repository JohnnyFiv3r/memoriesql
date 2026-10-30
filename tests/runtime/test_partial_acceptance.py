"""Partial acceptance of authored observations (guard audit P1-f (b)); DB-free.

Fictional two-bead observation authoring. A bead whose annotation canonical
apply would refuse is withheld whole, with a fixed reason; the other bead is
returned unchanged as a succeeded result. With nothing acceptable left, or for
a task that is not multi-bead observation authoring, the output still settles
invalid_output. The repair round comes first. A used reference outside the
manifest is dropped before the scope guard while one inside it remains; it is
never echoed to the model or recorded, and every other run keeps the guard.
"""

from __future__ import annotations

import json
import unittest
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

from pydantic import BaseModel
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.usage import RequestUsage

from memoriesql.application.semantic_task_contracts import (
    SemanticDelegatedRunCorrelation,
    SemanticRunEventKind,
    WithheldOutputItem,
)

if TYPE_CHECKING:
    from tests.runtime.test_executor import Ports
    from tests.runtime.test_executor import fixture as conductor_fixture
    from tests.runtime.test_repair_frame import (
        BEAD,
        UNIT,
        OrchardAnnotation,
        annotation,
        fixture,
    )
else:
    from test_executor import Ports
    from test_executor import fixture as conductor_fixture
    from test_repair_frame import BEAD, UNIT, OrchardAnnotation, annotation, fixture

OBSERVATIONS = "memory.semantic.author-observations"
GATE_BEAD = "orchard-gate-bead"
GATE_UNIT = "gate-count"
BEADS = (BEAD, GATE_BEAD)
UNITS = (UNIT, GATE_UNIT)
# A used reference the task's manifest does not hold.
OUTSIDE = "orchard-outside-unit"


def answer(
    *, trees: bool = True, gate: str = "ok", used: tuple[str, ...] = UNITS
) -> Any:
    """Annotations of both beads; the gate's is "ok", "flawed" or "missing"."""

    def build(ports: Ports) -> dict[str, Any]:
        items = [annotation(ports, BEAD)]
        if not trees:
            items[0]["statements"][0]["context_source_ids"] = [GATE_UNIT]
        if gate != "missing":
            items.append(annotation(ports, GATE_BEAD, GATE_UNIT))
        if gate == "flawed":
            # Names a context source the statement does not cite.
            items[-1]["statements"][0]["context_source_ids"] = [UNIT]
        return {"typed_output": {"annotations": items}, "used_evidence_refs": list(used)}

    return build


def two_beads(outputs: list[Any], **options: Any) -> Any:
    return fixture(outputs, beads=BEADS, units=UNITS, **{"task_kind": OBSERVATIONS, **options})


def withheld(reason: str) -> tuple[WithheldOutputItem, ...]:
    return (WithheldOutputItem(item_kind="bead", item_id=GATE_BEAD, reason_code=reason),)


class PartialAcceptance(unittest.IsolatedAsyncioTestCase):
    async def test_a_flawed_bead_is_withheld_and_the_other_succeeds(self) -> None:
        executor, task, deps, ports, prompts = two_beads(
            [answer(gate="flawed")], request_limit=1
        )
        result = await executor.execute(task, deps)
        self.assertEqual(result.status, "succeeded", result)
        self.assertEqual([a.bead_id for a in result.typed_output.annotations], [BEAD])
        # The kept annotation is exactly what the model wrote.
        self.assertEqual(
            result.typed_output.annotations[0],
            OrchardAnnotation.model_validate(annotation(ports, BEAD)),
        )
        self.assertEqual(result.withheld_items, withheld("withheld.context_without_evidence"))
        self.assertEqual(result.used_evidence_refs, UNITS)
        # No request was left for a repair: one dispatch, accounted.
        self.assertEqual(ports.order.count("model"), 1)
        self.assertEqual((len(ports.intents), len(ports.usages)), (1, 1))

    async def test_the_repair_round_comes_first_and_a_second_failure_withholds(self) -> None:
        executor, task, deps, ports, prompts = two_beads(
            [answer(gate="flawed"), answer(gate="missing")]
        )
        result = await executor.execute(task, deps)
        self.assertEqual(result.status, "succeeded", result)
        self.assertEqual([a.bead_id for a in result.typed_output.annotations], [BEAD])
        self.assertEqual(result.withheld_items, withheld("withheld.not_authored"))
        self.assertEqual(ports.order.count("model"), 2)
        self.assertEqual(
            json.loads(prompts[1])["refusal_codes"],
            [f"statement.context_without_evidence: orchard-statement names {UNIT}"],
        )

    async def test_a_repaired_output_withholds_nothing(self) -> None:
        executor, task, deps, ports, prompts = two_beads(
            [answer(gate="flawed"), answer()]
        )
        result = await executor.execute(task, deps)
        self.assertEqual(result.status, "succeeded", result)
        self.assertEqual(
            [a.bead_id for a in result.typed_output.annotations], [BEAD, GATE_BEAD]
        )
        self.assertEqual(result.withheld_items, ())

    async def test_nothing_acceptable_left_still_settles_invalid_output(self) -> None:
        executor, task, deps, ports, prompts = two_beads(
            [answer(trees=False, gate="flawed")], request_limit=1
        )
        result = await executor.execute(task, deps)
        self.assertEqual(
            (result.status, result.error_code, result.retry_class),
            ("invalid_output", "output.statement.context_without_evidence", "never"),
        )
        self.assertIsNone(result.typed_output)
        self.assertEqual(result.withheld_items, ())

    async def test_other_tasks_stay_all_or_nothing(self) -> None:
        executor, task, deps, ports, prompts = two_beads(
            [answer(gate="flawed")], request_limit=1, task_kind="orchard.annotate"
        )
        result = await executor.execute(task, deps)
        self.assertEqual(
            (result.status, result.error_code),
            ("invalid_output", "output.statement.context_without_evidence"),
        )
        self.assertEqual(result.withheld_items, ())


class UsedReferencesOutsideTheManifest(unittest.IsolatedAsyncioTestCase):
    async def test_a_stray_used_reference_is_dropped_and_the_rest_succeeds(self) -> None:
        executor, task, deps, ports, prompts = two_beads(
            [answer(used=(*UNITS, OUTSIDE))], request_limit=1
        )
        with patch.object(
            executor, "_partial_root_output", wraps=executor._partial_root_output
        ) as partial:
            result = await executor.execute(task, deps)
        self.assertEqual(result.status, "succeeded", result)
        self.assertEqual(
            [a.bead_id for a in result.typed_output.annotations], [BEAD, GATE_BEAD]
        )
        # The guard's filtered output is what the run returns: no issue is left
        # for the partial path to drop it from.
        self.assertEqual(result.used_evidence_refs, UNITS)
        partial.assert_not_called()
        self.assertEqual(result.withheld_items, ())
        self.assertEqual(ports.order.count("model"), 1)

    async def test_a_dropped_reference_never_reaches_the_repair_frame(self) -> None:
        executor, task, deps, ports, prompts = two_beads(
            [answer(gate="flawed", used=(*UNITS, OUTSIDE)), answer(used=(*UNITS, OUTSIDE))]
        )
        with patch.object(
            executor, "_partial_root_output", wraps=executor._partial_root_output
        ) as partial:
            result = await executor.execute(task, deps)
        partial.assert_not_called()
        self.assertEqual(result.status, "succeeded", result)
        self.assertEqual(
            [a.bead_id for a in result.typed_output.annotations], [BEAD, GATE_BEAD]
        )
        self.assertEqual(result.used_evidence_refs, UNITS)
        self.assertEqual(result.withheld_items, ())
        self.assertEqual(ports.order.count("model"), 2)
        frame = json.loads(prompts[1])
        self.assertEqual(
            frame["refusal_codes"],
            [f"statement.context_without_evidence: orchard-statement names {UNIT}"],
        )
        self.assertEqual(frame["previous_output"]["used_evidence_refs"], list(UNITS))
        self.assertNotIn(OUTSIDE, prompts[1])

    async def test_a_stray_reference_beside_a_flawed_bead_withholds_only_the_bead(
        self,
    ) -> None:
        executor, task, deps, ports, prompts = two_beads(
            [answer(gate="flawed", used=(*UNITS, OUTSIDE))], request_limit=1
        )
        with patch.object(
            executor, "_partial_root_output", wraps=executor._partial_root_output
        ) as partial:
            result = await executor.execute(task, deps)
        self.assertEqual(partial.call_count, 1)
        self.assertEqual(partial.call_args.args[2].used_evidence_refs, UNITS)
        self.assertEqual(result.status, "succeeded", result)
        self.assertEqual([a.bead_id for a in result.typed_output.annotations], [BEAD])
        self.assertEqual(result.withheld_items, withheld("withheld.context_without_evidence"))
        self.assertEqual(result.used_evidence_refs, UNITS)

    async def test_other_tasks_still_refuse_a_stray_reference(self) -> None:
        executor, task, deps, ports, prompts = two_beads(
            [answer(used=(*UNITS, OUTSIDE))], request_limit=1, task_kind="orchard.annotate"
        )
        result = await executor.execute(task, deps)
        self.assertEqual(
            (result.status, result.error_code),
            ("invalid_output", "runtime.evidence_scope_widened"),
        )
        self.assertIsNone(result.typed_output)

    async def test_citing_only_references_outside_the_manifest_still_refuses(self) -> None:
        executor, task, deps, ports, prompts = two_beads(
            [answer(used=(OUTSIDE,))], request_limit=1
        )
        result = await executor.execute(task, deps)
        self.assertEqual(
            (result.status, result.error_code),
            ("invalid_output", "runtime.evidence_scope_widened"),
        )
        self.assertIsNone(result.typed_output)
        self.assertEqual(result.withheld_items, ())

    async def test_a_delegate_citing_outside_its_evidence_still_refuses(self) -> None:
        # The drop is the root output's alone: a delegated run of observation
        # authoring keeps the hard scope guard.
        outcomes: list[Any] = []
        calls = 0

        def respond(messages: Any, info: Any) -> ModelResponse:
            nonlocal calls
            calls += 1
            if calls == 1:
                part = ToolCallPart(
                    "delegate_semantic_task",
                    {
                        "request": {
                            "agent_key": "orchard.author",
                            "effort_key": "frontier",
                            "expected_output_contract": task.definition.output_contract.reference.model_dump(
                                mode="json"
                            ),
                            "brief": "Count the fictional trees.",
                            "child_input_json": task.task_input.model_dump_json(),
                            "evidence_reference_ids": ["tree-count"],
                        }
                    },
                )
            else:
                outcomes.extend(
                    item.content
                    for message in messages
                    if isinstance(message, ModelRequest)
                    for item in message.parts
                    if isinstance(item, ToolReturnPart)
                    and item.tool_name == "delegate_semantic_task"
                )
                part = ToolCallPart(
                    info.output_tools[0].name,
                    {
                        "typed_output": {"answer": "Four fictional trees."},
                        "used_evidence_refs": ["tree-count", OUTSIDE],
                    },
                )
            return ModelResponse(
                parts=[part], usage=RequestUsage(input_tokens=11, output_tokens=7)
            )

        executor, task, deps, ports = conductor_fixture(
            conductor=True, model_callback=respond, task_kind=OBSERVATIONS, request_limit=3
        )
        result = await executor.execute(task, deps)
        self.assertNotEqual(result.status, "succeeded", result)
        outcome = [
            item.model_dump(mode="json") if isinstance(item, BaseModel) else item
            for item in outcomes
        ]
        self.assertEqual(len(outcome), 1, outcomes)
        self.assertEqual(
            (outcome[0]["status"], outcome[0]["error_code"], outcome[0]["typed_output"]),
            ("invalid_output", "runtime.evidence_scope_widened", None),
        )
        child = [
            event.event_kind
            for event in ports.events
            if isinstance(event.correlation, SemanticDelegatedRunCorrelation)
        ]
        self.assertIn(SemanticRunEventKind.RUN_FAILED, child)
        self.assertNotIn(SemanticRunEventKind.RUN_SUCCEEDED, child)


if __name__ == "__main__":
    unittest.main()
