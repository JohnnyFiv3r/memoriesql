"""Database-free contract of author-complete-unit revision 7."""

from __future__ import annotations

import copy
import json
import re
import unittest
import uuid
from typing import Any

from pydantic import ValidationError

from memoriesql.application.canonical_transactions import StatementKind
from memoriesql.application.local_entity_mentions import (
    AUTHORED_MENTION_TASK,
    CORRECTION_WITHIN_NOTE,
    EVERY_CURRENT_STATEMENT_RENDERED,
    MENTION_EXECUTION_TASK,
    AuthoredMentionOutput,
    AuthoredMentionStep,
    MentionAuthorStep,
    MentionExecutionOutput,
)

HASH = "a" * 64
KINDS = re.compile(r'"(observation|context|qualification|correction)"')


def output(**statement: Any) -> dict[str, Any]:
    observation = str(uuid.uuid4())
    unit = str(uuid.uuid4())
    clause = {"text": "Fictional note.", "statement_ids": [observation]}
    return {
        "annotations": [
            {
                "bead_id": str(uuid.uuid4()),
                "event_id": str(uuid.uuid4()),
                "source_unit_id": unit,
                "bead_version_id": str(uuid.uuid4()),
                "expected_bead_version": 0,
                "bead_type_key": "observation",
                "bead_type_revision": 1,
                "statements": [
                    {
                        "statement_id": observation,
                        "statement_kind": "observation",
                        "statement_text": "Fictional note.",
                        "evidence": [{"source_unit_id": unit, "content_hash": HASH}],
                        "model_run_ref": "orchard.run",
                    }
                    | statement
                ],
                "render": {"title": clause, "summary": [clause]},
                "mentions": [],
            }
        ]
    }


def note(*statements: dict[str, Any], rendered: list[int]) -> dict[str, Any]:
    """An output whose statements cite the note's unit; `rendered` indexes them."""
    data = output()
    annotation = data["annotations"][0]
    unit = annotation["source_unit_id"]
    drafts = []
    for statement in statements:
        drafts.append(
            {
                "statement_id": str(uuid.uuid4()),
                "evidence": [{"source_unit_id": unit, "content_hash": HASH}],
                "model_run_ref": "orchard.run",
            }
            | statement
        )
    for draft in drafts:
        target = draft.get("supersedes_statement_id")
        if isinstance(target, int):
            draft["supersedes_statement_id"] = drafts[target]["statement_id"]
    clause = {
        "text": "Fictional note.",
        "statement_ids": [drafts[i]["statement_id"] for i in rendered],
    }
    annotation["statements"] = drafts
    annotation["render"] = {"title": clause, "summary": [clause]}
    return data


def earlier(text: str = "Alex first counted four fictional trees.") -> dict[str, Any]:
    return {"statement_kind": "observation", "statement_text": text}


def correction(
    target: int, reason: str = "Alex corrected the count."
) -> dict[str, Any]:
    return {
        "statement_kind": "correction",
        "statement_text": "Alex says there are five fictional trees.",
        "supersedes_statement_id": target,
        "correction_reason": reason,
    }


def offered(model: Any) -> list[str]:
    return sorted(set(KINDS.findall(json.dumps(model.model_json_schema()))))


class AuthoredStatementKindsContract(unittest.TestCase):
    def test_revision_7_offers_a_correction_within_its_note(self) -> None:
        self.assertEqual(
            offered(AuthoredMentionStep),
            ["context", "correction", "observation", "qualification"],
        )
        schema = json.dumps(AuthoredMentionStep.model_json_schema())
        self.assertIn(CORRECTION_WITHIN_NOTE, schema)
        self.assertIn(EVERY_CURRENT_STATEMENT_RENDERED, schema)
        self.assertEqual(AUTHORED_MENTION_TASK.contract_revision, 7)
        self.assertEqual(
            AUTHORED_MENTION_TASK.output_contract.reference.contract_id,
            "memory.semantic.local-mentions.output",
        )
        self.assertEqual(AUTHORED_MENTION_TASK.output_contract.reference.revision, 2)

    def test_a_correction_supersedes_an_earlier_statement_of_its_note(self) -> None:
        # The earlier statement keeps the prior meaning; the render covers the
        # correction, and a chain of corrections renders only the last.
        AuthoredMentionOutput.model_validate(
            note(earlier(), correction(0), rendered=[1])
        )
        AuthoredMentionOutput.model_validate(
            note(earlier(), correction(0), correction(1, "Again."), rendered=[2])
        )
        refused = {
            "a later target": note(
                earlier(), correction(2), earlier("x"), rendered=[0, 1]
            ),
            "a target outside the note": note(
                earlier(),
                correction(0) | {"supersedes_statement_id": str(uuid.uuid4())},
                rendered=[0, 1],
            ),
            "two corrections of one statement": note(
                earlier(), correction(0), correction(0, "Twice."), rendered=[1, 2]
            ),
            "the superseded statement rendered": note(
                earlier(), correction(0), rendered=[0, 1]
            ),
            "a superseded statement omitted": note(
                earlier(), correction(0), rendered=[1]
            ),
            "no reason": note(
                earlier(), correction(0) | {"correction_reason": None}, rendered=[1]
            ),
            "a blank reason": note(earlier(), correction(0, "   "), rendered=[1]),
            "supersession on an observation": note(
                earlier(),
                earlier("y") | {"supersedes_statement_id": 0},
                rendered=[1],
            ),
        }
        refused["a superseded statement omitted"]["annotations"][0]["render"][
            "omissions"
        ] = [
            {
                "statement_id": refused["a superseded statement omitted"][
                    "annotations"
                ][0]["statements"][0]["statement_id"],
                "reason": "Superseded.",
            }
        ]
        for label, data in refused.items():
            with self.subTest(label), self.assertRaises(ValidationError):
                AuthoredMentionOutput.model_validate(data)

    def test_revision_4_and_stored_history_are_unchanged(self) -> None:
        # Revision 4 keeps its pinned contract, including the kind it offers but
        # never accepts; stored statements still read every historical kind.
        self.assertEqual(
            offered(MentionAuthorStep),
            ["context", "correction", "observation", "qualification"],
        )
        self.assertEqual(
            MENTION_EXECUTION_TASK.contract_hash,
            "8488d4fddf19e65a2eb41203d6b26d7b400d73516c044700246ab3a7e75adce5",
        )
        self.assertEqual(
            MENTION_EXECUTION_TASK.output_contract.schema_hash,
            "0baecb82f0993a29dbc6c1afb6e41a5f3b83b8ebfe1f70315d87b5a7672d8e8c",
        )
        self.assertIn("correction", {kind.value for kind in StatementKind})
        # A valid revision-7 output keeps revision 4's canonical statement keys.
        data = output()
        seven = AuthoredMentionOutput.model_validate(copy.deepcopy(data))
        four = MentionExecutionOutput.model_validate(copy.deepcopy(data))
        self.assertEqual(seven.model_dump(mode="json"), four.model_dump(mode="json"))


if __name__ == "__main__":
    unittest.main()
