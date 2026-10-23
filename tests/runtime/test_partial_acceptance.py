"""Partial acceptance of authored observations (guard audit P1-f (b)); DB-free.

Fictional two-bead observation authoring. A bead whose annotation canonical
apply would refuse is withheld whole, with a fixed reason; the other bead is
returned unchanged as a succeeded result. With nothing acceptable left, or for
a task that is not multi-bead observation authoring, the output still settles
invalid_output. The repair round comes first.
"""

from __future__ import annotations

import json
import unittest
from typing import TYPE_CHECKING, Any

from memoriesql.application.semantic_task_contracts import WithheldOutputItem

if TYPE_CHECKING:
    from tests.runtime.test_executor import Ports
    from tests.runtime.test_repair_frame import (
        BEAD,
        UNIT,
        OrchardAnnotation,
        annotation,
        fixture,
    )
else:
    from test_executor import Ports
    from test_repair_frame import BEAD, UNIT, OrchardAnnotation, annotation, fixture

OBSERVATIONS = "memory.semantic.author-observations"
GATE_BEAD = "orchard-gate-bead"
GATE_UNIT = "gate-count"
BEADS = (BEAD, GATE_BEAD)
UNITS = (UNIT, GATE_UNIT)


def answer(*, trees: bool = True, gate: str = "ok") -> Any:
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
        return {"typed_output": {"annotations": items}, "used_evidence_refs": list(UNITS)}

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


if __name__ == "__main__":
    unittest.main()
