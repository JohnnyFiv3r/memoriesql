"""The relation author's repair round, DB-free (guard audit P1-f D2; custody R-1, R-4, R-6).

The author of an assess-relations revision-2 task that misses a pair's
disposition, or uses an unregistered label, gets one evidence-free repair
frame. The frame is the packet it already received without its evidence; the
executor holds the repaired output to fixed edit rules. Revision 1 never
repairs, since its specialist needs the second request.
"""

from __future__ import annotations

import asyncio
import json
import unittest
import uuid
from types import SimpleNamespace
from typing import Any, cast

from memoriesql.application.complete_input_execution import CompleteInputError
from memoriesql.application.relation_assessment import (
    RELATION_ASSESSMENT_TASK,
    RELATION_REPAIR_INSTRUCTION,
    REPAIRABLE_RELATION_ASSESSMENT_TASK,
    PairDisposition,
    RelationAuthorOutput,
    RelationAuthorStep,
    RelationProposal,
)
from memoriesql.infrastructure.models.pydanticai_executor import (
    PydanticAISemanticExecutor,
    _checked_relation_repair,
    _relation_repair_frame,
    _relation_repair_issues,
)

A, B = sorted((uuid.uuid4(), uuid.uuid4()), key=str)
SECRET = "Fictional private orchard evidence that must never be sent again."
VOCABULARY = [SimpleNamespace(key="caused_by", revision=1), SimpleNamespace(key="supports", revision=1)]
PAYLOAD = SimpleNamespace(
    beads=[SimpleNamespace(bead_id=A), SimpleNamespace(bead_id=B)],
    relation_vocabulary=VOCABULARY,
)


def proposal(key: str = "caused_by", **updates: Any) -> RelationProposal:
    statement_a, statement_b = uuid.uuid4(), uuid.uuid4()
    data: dict[str, Any] = {
        "proposal_id": str(uuid.uuid4()),
        "relation_type": {"key": key, "revision": 1},
        "source": {"bead_id": str(A), "bead_version_id": str(uuid.uuid4()), "statement_ids": [str(statement_a)]},
        "target": {"bead_id": str(B), "bead_version_id": str(uuid.uuid4()), "statement_ids": [str(statement_b)]},
        "basis": "agent_inferred",
        "rationale": "The fictional author infers this.",
        "author_confidence": 0.5,
    }
    data.update(updates)
    # Evidence for the source endpoint actually used.
    data["evidence"] = [{"statement_id": data["source"]["statement_ids"][0],
                         "source_unit_id": str(uuid.uuid4()), "content_hash": "1" * 64}]
    return RelationProposal.model_validate(data)


def disposition(first: uuid.UUID, second: uuid.UUID, kind: str, **extra: Any) -> PairDisposition:
    return PairDisposition.model_validate(
        {"first_bead_id": str(first), "second_bead_id": str(second), "disposition": kind, **extra}
    )


NOT_ASSESSED = {"reason": "Not assessed in the repair round; no evidence was shown."}


def output(proposals: list[RelationProposal], dispositions: list[PairDisposition]) -> RelationAuthorOutput:
    return RelationAuthorOutput(proposals=tuple(proposals), dispositions=tuple(dispositions))


def step(authored: RelationAuthorOutput, action: str = "finish") -> RelationAuthorStep:
    if action != "finish":
        return RelationAuthorStep(action="incomplete")
    return RelationAuthorStep(action="finish", typed_output=authored)


class RelationRepairIssues(unittest.TestCase):
    def test_missing_pairs_and_unregistered_labels_are_listed(self) -> None:
        p = proposal(key="unregistered")
        authored = output([p], [disposition(A, A, "not_related"), disposition(A, B, "related")])
        self.assertEqual(
            _relation_repair_issues(cast(Any, PAYLOAD), authored), (((B, B),), (p.proposal_id,))
        )

    def test_a_disposition_outside_the_pins_is_not_repairable(self) -> None:
        stranger = uuid.UUID(int=0)
        authored = output([], [disposition(stranger, A, "not_related")])
        self.assertIsNone(_relation_repair_issues(cast(Any, PAYLOAD), authored))


class RelationRepairFrame(unittest.TestCase):
    """R-1: the frame's packet is the delivered packet without its evidence."""

    def test_the_frame_is_the_delivered_packet_less_its_evidence(self) -> None:
        delivered = {
            "contract_version": 1, "role": "author", "task_id": str(uuid.uuid4()),
            "attempt_id": str(uuid.uuid4()), "subject_bead_id": str(A),
            "beads": [{"bead_id": str(A), "statements": [{"statement_text": "Pinned text."}]}],
            "relation_vocabulary": [{"key": "caused_by"}], "reconsideration": None,
            "evidence": [{"source_unit_id": str(uuid.uuid4()), "content": SECRET, "parts": None}],
        }
        packet = SimpleNamespace(model_dump=lambda **_: json.loads(json.dumps(delivered)))
        p = proposal()
        previous = step(output([p], [disposition(A, B, "related")]))
        frame = json.loads(
            _relation_repair_frame(cast(Any, packet), previous, ((A, A), (B, B)), (p.proposal_id,))
        )
        self.assertEqual(
            set(frame),
            {"repair_frame", "instruction", "relation_packet", "previous_output", "refusal_codes"},
        )
        self.assertEqual(frame["instruction"], RELATION_REPAIR_INSTRUCTION)
        expected = {k: v for k, v in delivered.items() if k != "evidence"}
        self.assertEqual(set(frame["relation_packet"]), set(expected))
        for key, value in expected.items():
            with self.subTest(key=key):
                self.assertEqual(frame["relation_packet"][key], value)
        for key in ("evidence", "content", "parts"):
            self.assertNotIn(key, frame["relation_packet"])
        self.assertNotIn(SECRET, json.dumps(frame))
        self.assertEqual(frame["previous_output"], previous.model_dump(mode="json"))
        self.assertEqual(
            frame["refusal_codes"],
            [f"relation.pair_missing: {A}/{A}", f"relation.pair_missing: {B}/{B}",
             f"relation.label_unregistered: {p.proposal_id}"],
        )


class RelationRepairRules(unittest.TestCase):
    """R-4: the executor holds the repaired output to the edit rules."""

    def setUp(self) -> None:
        self.p = proposal()
        self.original = output([self.p], [disposition(A, A, "not_related"), disposition(A, B, "related")])
        self.completed = [*self.original.dispositions, disposition(B, B, "not_assessed", **NOT_ASSESSED)]

    def check(self, repaired: RelationAuthorStep, missing: Any = ((B, B),), unregistered: Any = ()) -> Any:
        return _checked_relation_repair(self.original, repaired, missing, unregistered)

    def refused(self, repaired: RelationAuthorStep, **kwargs: Any) -> None:
        with self.assertRaises(CompleteInputError) as raised:
            self.check(repaired, **kwargs)
        self.assertEqual(raised.exception.status, "invalid_output")

    def test_a_missing_pair_marked_not_assessed_is_accepted(self) -> None:
        repaired = output([self.p], self.completed)
        self.assertEqual(self.check(step(repaired)), repaired)

    def test_a_listed_proposal_may_be_dropped_where_its_pair_keeps_another(self) -> None:
        wrong = proposal(key="unregistered", source=self.p.source.model_dump(mode="json"),
                         target=self.p.target.model_dump(mode="json"))
        original = output([self.p, wrong], self.completed)
        repaired = output([self.p], self.completed)
        self.assertEqual(
            _checked_relation_repair(original, step(repaired), (), (wrong.proposal_id,)), repaired
        )

    def test_everything_else_is_refused(self) -> None:
        other = proposal()
        changed = self.p.model_copy(update={"rationale": "A different fictional rationale."})
        cases = {
            "incomplete": step(self.original, action="incomplete"),
            "still missing": step(self.original),
            "changed proposal": step(output([changed], self.completed)),
            "new proposal": step(output([self.p, other], [*self.original.dispositions[:1], self.original.dispositions[1], disposition(B, B, "not_assessed", **NOT_ASSESSED)])),
            "unlisted drop": step(output([], [disposition(A, A, "not_related"), disposition(A, B, "not_assessed", **NOT_ASSESSED), disposition(B, B, "not_assessed", **NOT_ASSESSED)])),
            "changed disposition": step(output([self.p], [disposition(A, A, "not_assessed", **NOT_ASSESSED), self.original.dispositions[1], disposition(B, B, "not_assessed", **NOT_ASSESSED)])),
            "new not_related": step(output([self.p], [*self.original.dispositions, disposition(B, B, "not_related")])),
            "new abstained": step(output([self.p], [*self.original.dispositions, disposition(B, B, "abstained", abstention="insufficient_evidence")])),
        }
        for name, repaired in cases.items():
            with self.subTest(case=name):
                self.refused(repaired)


class RelationRepairAdmission(unittest.TestCase):
    """R-6: revision 2 only, with headroom for the pending specialist request."""

    def admit(self, *, revision: int = 2, reserved: int = 1, supervised: bool = False,
              used: bool = False, authored: RelationAuthorOutput | None = None) -> tuple[Any, list[Any]]:
        p = proposal()
        authored = authored or output([p], [disposition(A, A, "not_related"), disposition(A, B, "related")])
        task = REPAIRABLE_RELATION_ASSESSMENT_TASK if revision == 2 else RELATION_ASSESSMENT_TASK
        state = SimpleNamespace(
            task=SimpleNamespace(
                task_input=SimpleNamespace(payload=PAYLOAD),
                definition=SimpleNamespace(contract_revision=revision),
                effective_budget=task.run_budget,
            ),
            repair_used=used, reserved_requests=reserved, repair_frame=None,
            usage=SimpleNamespace(requests=reserved),
        )
        parent = SimpleNamespace(
            state=state,
            model_profile=SimpleNamespace(binding=SimpleNamespace(supervision=object() if supervised else None)),
        )
        packet = SimpleNamespace(model_dump=lambda **_: {"role": "author", "evidence": [SECRET]})
        calls: list[Any] = []
        given = authored

        async def invoke(prompt: str, output_type: Any) -> Any:
            calls.append((prompt, output_type, state.repair_frame))
            repaired = output(
                list(given.proposals),
                [*given.dispositions, disposition(B, B, "not_assessed", **NOT_ASSESSED)],
            )
            return SimpleNamespace(output=step(repaired).model_dump(mode="json"))

        result = asyncio.run(
            PydanticAISemanticExecutor._repaired_relation_output(
                cast(Any, None), cast(Any, parent), invoke, cast(Any, packet), step(authored)
            )
        )
        return (result, state), calls

    def test_revision_two_repairs_once_through_the_author_binding(self) -> None:
        (result, state), calls = self.admit()
        self.assertEqual(len(calls), 1)
        prompt, output_type, active = calls[0]
        self.assertIs(output_type, RelationAuthorStep)
        self.assertEqual(prompt, active)
        self.assertNotIn(SECRET, prompt)
        self.assertEqual(len(result.dispositions), 3)
        self.assertTrue(state.repair_used)
        self.assertIsNone(state.repair_frame)

    def test_no_repair_where_it_is_not_admitted(self) -> None:
        cases: dict[str, dict[str, Any]] = {
            "revision 1": {"revision": 1},
            "no headroom for the specialist": {"reserved": 2},
            "supervised": {"supervised": True},
            "second repair": {"used": True},
            "no issue": {"authored": output([], [disposition(A, A, "not_related"), disposition(A, B, "not_related"), disposition(B, B, "not_related")])},
        }
        for name, options in cases.items():
            with self.subTest(case=name):
                (result, state), calls = self.admit(**options)
                self.assertEqual(calls, [])
        self.assertEqual(
            (RELATION_ASSESSMENT_TASK.run_budget.request_limit,
             REPAIRABLE_RELATION_ASSESSMENT_TASK.run_budget.request_limit),
            (2, 3),
        )


if __name__ == "__main__":
    unittest.main()
