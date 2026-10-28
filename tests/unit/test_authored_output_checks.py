"""Pre-apply checks of authored annotations name what canonical apply would refuse."""

from __future__ import annotations

import copy
import json
import re
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.function import FunctionModel

from memoriesql.application.authored_output_checks import (
    ISSUE_REFUSALS,
    PARTIAL_ACCEPTANCE,
    WITHHELD_REASONS,
    AuthoredOutputIssue,
    authored_annotation_issues,
    partial_annotations,
)
from memoriesql.application.bead_classification import ClassificationExecutionInput
from memoriesql.application.semantic_task_contracts import (
    WITHHELD_REASON_CODES,
    DispatchMode,
    SemanticResultStatus,
    SemanticTaskResult,
    WithheldOutputItem,
    canonical_sha256,
)
from memoriesql.infrastructure.models.pydanticai_executor import (
    REPAIR_INSTRUCTION,
    EvidenceGuardError,
    PydanticAISemanticExecutor,
    _DispatchGuardedModel,
    _normalize_failure,
    _repair_frame,
)

ROOT = Path(__file__).resolve().parents[2]
EVENT, OTHER_EVENT = "e" * 8 + "-0000-7000-8000-000000000001", "e" * 8 + "-0000-7000-8000-000000000002"
BEADS = ["b" * 8 + "-0000-7000-8000-00000000000" + str(i) for i in (1, 2)]
UNITS = ["a" * 8 + "-0000-7000-8000-00000000000" + str(i) for i in (1, 2)]
HASHES = ["1" * 64, "2" * 64]
SECRET = "Fictional private orchard detail that must never be echoed."
OBSERVATIONS = ("memory.semantic.author-observations", 2)
LEGACY = ("memory.semantic.author-observations", 1)
COMPLETE = ("memory.semantic.author-complete-unit", 3)


def task_input() -> dict[str, Any]:
    return {
        "payload": {"event_id": EVENT, "bead_ids": BEADS, "source_unit_ids": UNITS},
        "evidence_manifest": {
            "references": [
                {"reference_id": unit, "content_hash": digest}
                for unit, digest in zip(UNITS, HASHES, strict=True)
            ]
        },
    }


def annotation(index: int) -> dict[str, Any]:
    return {
        "bead_id": BEADS[index],
        "event_id": EVENT,
        "source_unit_id": UNITS[index],
        "statements": [
            {
                "statement_id": f"5{index}" + "0" * 6 + "-0000-7000-8000-000000000000",
                "statement_text": SECRET,
                "evidence": [
                    {"source_unit_id": UNITS[index], "content_hash": HASHES[index]}
                ],
                "context_source_ids": [],
            }
        ],
    }


def output() -> dict[str, Any]:
    return {"annotations": [annotation(0), annotation(1)]}


def issues(
    payload: dict[str, Any],
    used: list[str] = UNITS,
    kind: tuple[str, int] = OBSERVATIONS,
    task: dict[str, Any] | None = None,
) -> list[str]:
    found = authored_annotation_issues(
        task or task_input(), payload, used, task_kind=kind[0], contract_revision=kind[1]
    )
    for issue in found:
        assert SECRET not in issue.feedback()
    return [issue.code for issue in found]


class AuthoredOutputChecks(unittest.TestCase):
    def test_a_valid_output_has_no_issue(self) -> None:
        for kind in (OBSERVATIONS, LEGACY):
            self.assertEqual(issues(output(), kind=kind), [])
        self.assertEqual(issues({"decision": "not an annotation output"}), [])

    def test_coverage_names_missing_duplicated_and_outside_beads(self) -> None:
        payload = output()
        payload["annotations"] = [annotation(0), annotation(0)]
        self.assertEqual(
            issues(payload), ["annotation.bead_missing", "annotation.bead_duplicated"]
        )
        payload = output()
        payload["annotations"][1]["bead_id"] = "c" * 8 + "-0000-7000-8000-000000000009"
        self.assertEqual(
            issues(payload), ["annotation.bead_missing", "annotation.bead_outside_task"]
        )

    def test_evidence_must_be_declared_by_identity_and_hash_and_used(self) -> None:
        self.assertEqual(
            issues(output(), used=[*UNITS, "undeclared"]), ["evidence.outside_manifest"]
        )
        payload = output()
        payload["annotations"][0]["statements"][0]["evidence"][0]["content_hash"] = "9" * 64
        self.assertEqual(issues(payload), ["evidence.outside_manifest"])
        self.assertEqual(issues(output(), used=UNITS[:1]), ["evidence.not_declared_as_used"])

    def test_a_context_source_needs_statement_evidence(self) -> None:
        payload = output()
        payload["annotations"][0]["statements"][0]["context_source_ids"] = [UNITS[1]]
        self.assertEqual(issues(payload), ["statement.context_without_evidence"])

    def test_pins_are_checked_only_where_canonical_apply_checks_them(self) -> None:
        payload = output()
        payload["annotations"][0]["event_id"] = OTHER_EVENT
        payload["annotations"][1]["source_unit_id"] = UNITS[0]
        self.assertEqual(
            issues(payload),
            ["annotation.event_mismatch"],
        )
        # Revision-1 apply reads its pins from the stored bead, not here.
        self.assertEqual(issues(payload, kind=LEGACY), [])
        # A complete unit names exactly its one source unit.
        task = task_input()
        task["payload"] = {"event_id": EVENT, "bead_ids": BEADS[:1], "source_unit_ids": UNITS[:1]}
        unit = {"annotations": [copy.deepcopy(annotation(0))]}
        self.assertEqual(issues(unit, kind=COMPLETE, task=task, used=UNITS[:1]), [])
        unit["annotations"][0]["source_unit_id"] = UNITS[1]
        self.assertIn(
            "annotation.source_unit_mismatch",
            issues(unit, kind=COMPLETE, task=task, used=UNITS[:1]),
        )

    def test_feedback_names_only_identifiers(self) -> None:
        issue = AuthoredOutputIssue("annotation.event_mismatch", BEADS[0], EVENT)
        self.assertEqual(
            issue.feedback(), f"annotation.event_mismatch: {BEADS[0]} (expected {EVENT})"
        )
        with self.assertRaises(ValueError):
            AuthoredOutputIssue("annotation.unregistered", BEADS[0])

    def test_the_executor_names_issues_and_builds_an_evidence_free_frame(self) -> None:
        def dumped(value: dict[str, Any]) -> Any:
            return SimpleNamespace(model_dump=lambda **_: copy.deepcopy(value))

        task = SimpleNamespace(
            task_input=dumped(task_input()),
            definition=SimpleNamespace(task_kind=OBSERVATIONS[0], contract_revision=2),
        )
        payload = output()
        payload["annotations"][0]["event_id"] = OTHER_EVENT
        authored = SimpleNamespace(
            typed_output=dumped(payload),
            used_evidence_refs=UNITS,
            model_dump=lambda **_: {"typed_output": payload, "used_evidence_refs": UNITS},
        )
        issues = PydanticAISemanticExecutor._annotation_issues(
            cast(Any, task), cast(Any, authored)
        )
        self.assertEqual([issue.code for issue in issues], ["annotation.event_mismatch"])
        frame = json.loads(_repair_frame(cast(Any, task.task_input), cast(Any, authored), issues))
        self.assertEqual(
            set(frame),
            {"repair_frame", "instruction", "task_input", "previous_output", "refusal_codes"},
        )
        self.assertEqual(frame["instruction"], REPAIR_INSTRUCTION)
        self.assertEqual(
            frame["refusal_codes"],
            [f"annotation.event_mismatch: {BEADS[0]} (expected {EVENT})"],
        )
        # The task input carries references and hashes only, never evidence.
        self.assertEqual(frame["task_input"], task_input())
        self.assertEqual(frame["previous_output"]["typed_output"], payload)
        error = EvidenceGuardError(
            "output." + issues[0].code, "; ".join(i.feedback() for i in issues)
        )
        self.assertNotIn(SECRET, str(error))
        failure = _normalize_failure(error)
        self.assertEqual(
            (failure.status, failure.error_code),
            (SemanticResultStatus.INVALID_OUTPUT, "output.annotation.event_mismatch"),
        )
        valid = SimpleNamespace(typed_output=dumped(output()), used_evidence_refs=UNITS)
        self.assertEqual(
            PydanticAISemanticExecutor._annotation_issues(cast(Any, task), cast(Any, valid)), ()
        )

    def test_the_frame_carries_a_generic_output_in_full(self) -> None:
        # Source revisiting holds its output as AuthoredSemanticOutput[BaseModel];
        # the frame still carries every field the model wrote.
        from pydantic import BaseModel

        from memoriesql.infrastructure.models.pydanticai_executor import (
            AuthoredSemanticOutput,
        )

        class Written(BaseModel):
            annotations: list[dict[str, Any]]

        typed = Written(annotations=[annotation(0)])
        authored = AuthoredSemanticOutput[BaseModel](
            typed_output=typed, used_evidence_refs=tuple(UNITS)
        )
        frame = json.loads(_repair_frame(cast(Any, SimpleNamespace(model_dump=lambda **_: {})), authored, ()))
        self.assertEqual(
            frame["previous_output"],
            {"typed_output": typed.model_dump(mode="json"), "used_evidence_refs": UNITS},
        )

    def test_every_anticipated_refusal_is_raised_by_canonical_apply(self) -> None:
        text = (ROOT / "migrations/0041_authored_statement_kinds.sql").read_text()
        start = text.index("FUNCTION memoriesql.apply_semantic_annotations_schema29(")
        body = text[start : text.index("\n$$;", start)]
        for code, refusals in ISSUE_REFUSALS.items():
            for refusal in refusals:
                with self.subTest(code=code, refusal=refusal):
                    self.assertRegex(body, r"RAISE EXCEPTION\s+'" + re.escape(refusal) + "'")


def partial(
    payload: dict[str, Any],
    used: list[str] = UNITS,
    kind: tuple[str, int] = OBSERVATIONS,
) -> Any:
    found = authored_annotation_issues(
        task_input(), payload, used, task_kind=kind[0], contract_revision=kind[1]
    )
    return partial_annotations(
        task_input(), payload, used, found, task_kind=kind[0], contract_revision=kind[1]
    )


class PartialObservationAcceptance(unittest.TestCase):
    """Guard audit P1-f (b): each bead with an issue is withheld whole."""

    def test_a_bead_with_an_issue_is_withheld_and_the_rest_kept_unchanged(self) -> None:
        for kind in (OBSERVATIONS, LEGACY):
            with self.subTest(kind=kind):
                payload = output()
                payload["annotations"][1]["statements"][0]["context_source_ids"] = [UNITS[0]]
                plan = partial(payload, kind=kind)
                self.assertEqual(plan.payload, {"annotations": [annotation(0)]})
                self.assertEqual(plan.used_evidence_refs, tuple(UNITS))
                self.assertEqual(
                    plan.withheld, ((BEADS[1], "withheld.context_without_evidence"),)
                )

    def test_each_issue_withholds_its_bead_with_a_fixed_reason(self) -> None:
        missing = output()
        missing["annotations"] = [annotation(0)]
        duplicated = output()
        duplicated["annotations"] = [annotation(0), annotation(1), annotation(1)]
        pinned = output()
        pinned["annotations"][1]["event_id"] = OTHER_EVENT
        rehashed = output()
        rehashed["annotations"][1]["statements"][0]["evidence"][0]["content_hash"] = "9" * 64
        cases = (
            (missing, UNITS, "withheld.not_authored"),
            (duplicated, UNITS, "withheld.duplicated"),
            (pinned, UNITS, "withheld.pin_mismatch"),
            (rehashed, UNITS, "withheld.evidence_outside_manifest"),
            (output(), UNITS[:1], "withheld.evidence_not_declared_as_used"),
        )
        for payload, used, reason in cases:
            with self.subTest(reason=reason):
                plan = partial(payload, used=used)
                self.assertEqual(plan.payload["annotations"], [annotation(0)])
                self.assertEqual(plan.withheld, ((BEADS[1], reason),))

    def test_an_annotation_outside_the_task_is_dropped_not_withheld(self) -> None:
        payload = output()
        payload["annotations"].append(
            {**annotation(1), "bead_id": "c" * 8 + "-0000-7000-8000-000000000009"}
        )
        plan = partial(payload)
        self.assertEqual(plan.payload, output())
        self.assertEqual(plan.withheld, ())

    def test_nothing_is_kept_when_every_bead_has_an_issue(self) -> None:
        payload = output()
        for item in payload["annotations"]:
            item["statements"][0]["context_source_ids"] = [UNITS[0], UNITS[1]]
        self.assertIsNone(partial(payload))
        self.assertIsNone(partial({"annotations": []}))

    def test_only_multi_bead_observation_authoring_is_accepted_in_part(self) -> None:
        payload = output()
        payload["annotations"] = [annotation(0)]
        self.assertIsNone(partial(payload, kind=COMPLETE))
        self.assertIsNone(partial(payload, kind=("memory.semantic.correct-observation", 2)))
        self.assertEqual(
            PARTIAL_ACCEPTANCE,
            {
                ("memory.semantic.author-observations", 1),
                ("memory.semantic.author-observations", 2),
            },
        )

    def test_every_bead_issue_has_a_registered_reason(self) -> None:
        self.assertEqual(
            set(WITHHELD_REASONS), set(ISSUE_REFUSALS) - {"annotation.bead_outside_task"}
        )
        self.assertEqual(set(WITHHELD_REASONS.values()), set(WITHHELD_REASON_CODES))
        with self.assertRaises(ValueError):
            WithheldOutputItem(item_kind="bead", item_id=BEADS[0], reason_code="withheld.other")

    def test_only_a_succeeded_result_withholds_items_and_each_once(self) -> None:
        item = WithheldOutputItem(
            item_kind="bead", item_id=BEADS[1], reason_code="withheld.not_authored"
        )
        failed = {
            "status": SemanticResultStatus.INVALID_OUTPUT,
            "task_id": "orchard.task",
            "attempt_id": "orchard.attempt",
            "task_kind": OBSERVATIONS[0],
            "contract_revision": 2,
            "output_contract_hash": "0" * 64,
            "error_code": "output.annotation.bead_missing",
            "retry_class": "never",
        }
        self.assertEqual(SemanticTaskResult.model_validate(failed).withheld_items, ())
        with self.assertRaisesRegex(ValueError, "only succeeded results withhold items"):
            SemanticTaskResult.model_validate({**failed, "withheld_items": [item]})
        # Any typed output will do; this one is the item itself.
        succeeded = {
            **failed,
            "status": SemanticResultStatus.SUCCEEDED,
            "error_code": None,
            "retry_class": None,
            "typed_output": item,
            "output_hash": canonical_sha256(item.model_dump(mode="json")),
        }
        result = SemanticTaskResult[WithheldOutputItem].model_validate(
            {**succeeded, "withheld_items": [item]}
        )
        self.assertEqual(result.withheld_items, (item,))
        with self.assertRaisesRegex(ValueError, "withheld items must be unique"):
            SemanticTaskResult[WithheldOutputItem].model_validate(
                {**succeeded, "withheld_items": [item, item]}
            )


class RepairAdmissionAndGuard(unittest.IsolatedAsyncioTestCase):
    """Custody S-1 and S-2 for #107: the guard and the admission rule."""

    async def test_a_repair_dispatch_carrying_anything_but_its_frame_is_refused(self) -> None:
        dispatched: list[Any] = []

        def respond(messages: Any, info: Any) -> ModelResponse:
            dispatched.append(messages)
            return ModelResponse(parts=[TextPart("unreachable")])

        async def dispatch_open() -> None:
            return None

        state = SimpleNamespace(require_dispatch_open=dispatch_open, repair_frame="FRAME")
        guarded = _DispatchGuardedModel(
            FunctionModel(respond), cast(Any, SimpleNamespace(state=state, parent_run_id=None))
        )
        for parts in (
            [UserPromptPart("FRAME"), UserPromptPart(SECRET)],
            [UserPromptPart("FRAME, altered")],
            [],
        ):
            with self.subTest(parts=parts):
                with self.assertRaises(EvidenceGuardError) as raised:
                    await guarded.request(
                        [ModelRequest(parts=parts)], None, ModelRequestParameters()
                    )
                self.assertEqual(raised.exception.code, "runtime.repair_frame_mismatch")
        # Refused before dispatch, and before any exposure or delivery record.
        self.assertEqual(dispatched, [])

    def test_only_a_tool_free_unsupervised_direct_leaf_with_headroom_is_repaired(self) -> None:
        def admitted(**changes: Any) -> bool:
            definition = SimpleNamespace(
                dispatch_mode=changes.get("dispatch_mode", DispatchMode.DIRECT_LEAF),
                allowed_delegate_keys=changes.get("delegates", ()),
                allowed_tool_keys=changes.get("tools", ()),
            )
            state = SimpleNamespace(
                repair_used=changes.get("repair_used", False),
                reserved_requests=changes.get("reserved", 1),
                task=SimpleNamespace(
                    definition=definition,
                    task_input=changes.get("task_input", SimpleNamespace()),
                    effective_budget=SimpleNamespace(request_limit=2),
                ),
            )
            run_deps = SimpleNamespace(
                binding=SimpleNamespace(evidence_tool_key=changes.get("evidence_tool")),
                model_profile=SimpleNamespace(
                    binding=SimpleNamespace(supervision=changes.get("supervision"))
                ),
            )
            return PydanticAISemanticExecutor._repair_admitted(
                cast(Any, state), cast(Any, run_deps)
            )

        self.assertTrue(admitted())
        for name, change in (
            ("supervised", {"supervision": object()}),
            ("classification", {"task_input": ClassificationExecutionInput.model_construct()}),
            ("delegated", {"dispatch_mode": DispatchMode.CONDUCTOR}),
            ("delegate keys", {"delegates": ("orchard.helper",)}),
            ("tools", {"tools": ("orchard.tool",)}),
            ("evidence tool", {"evidence_tool": "orchard.fetch"}),
            ("second repair", {"repair_used": True}),
            ("no headroom", {"reserved": 2}),
        ):
            with self.subTest(name=name):
                self.assertFalse(admitted(**change))


if __name__ == "__main__":
    unittest.main()
