"""The executor binds authored statements to its own run reference."""

from __future__ import annotations

import unittest

from memoriesql.application.semantic_task_contracts import (
    FrozenContractModel,
    stamp_statement_run_reference,
)


class Statement(FrozenContractModel):
    statement_text: str
    model_run_ref: str


class Annotation(FrozenContractModel):
    statements: tuple[Statement, ...]


class Output(FrozenContractModel):
    annotations: tuple[Annotation, ...]


class Bare(FrozenContractModel):
    value: int


class StampStatementRunReferenceTests(unittest.TestCase):
    def test_differing_references_are_rebound_and_reported(self) -> None:
        output = Output(
            annotations=(
                Annotation(
                    statements=(
                        Statement(statement_text="a", model_run_ref="task.kind"),
                        Statement(statement_text="b", model_run_ref="host.run"),
                    )
                ),
            )
        )
        stamped, changed = stamp_statement_run_reference(output, "host.run")
        self.assertTrue(changed)
        self.assertEqual(
            [s.model_run_ref for s in stamped.annotations[0].statements],
            ["host.run", "host.run"],
        )
        self.assertEqual(
            [s.statement_text for s in stamped.annotations[0].statements], ["a", "b"]
        )
        # The authored output itself is untouched.
        self.assertEqual(output.annotations[0].statements[0].model_run_ref, "task.kind")

    def test_matching_references_and_other_shapes_pass_through(self) -> None:
        output = Output(
            annotations=(
                Annotation(
                    statements=(
                        Statement(statement_text="a", model_run_ref="host.run"),
                    )
                ),
            )
        )
        same, changed = stamp_statement_run_reference(output, "host.run")
        self.assertFalse(changed)
        self.assertIs(same, output)
        bare = Bare(value=1)
        untouched, changed = stamp_statement_run_reference(bare, "host.run")
        self.assertFalse(changed)
        self.assertIs(untouched, bare)
