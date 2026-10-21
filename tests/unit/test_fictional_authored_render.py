"""Exercise real fictional output builders without database setup or a model.

Only fixture response/mutation methods execute. Task parsing and navigation are
inert here; this does not establish canonical acceptance or installed ownership.
"""

from __future__ import annotations

import ast
import copy
import json
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.usage import RequestUsage

RUNTIME = Path(__file__).resolve().parents[1] / "runtime"


class FictionalAuthoredRender(unittest.TestCase):
    def test_actual_builder_keeps_long_summary_separate_from_title(self) -> None:
        namespace: dict[str, Any] = {
            "Any": Any,
            "Packet": dict[str, Any],
            "ModelRequest": ModelRequest,
            "ModelResponse": ModelResponse,
            "ToolCallPart": ToolCallPart,
            "UserPromptPart": UserPromptPart,
            "cast": cast,
            "str": str,
            "uuid": uuid,
            "json": json,
            "copy": copy,
            "unittest": unittest,
            "RequestUsage": RequestUsage,
        }
        payload = SimpleNamespace(
            bead_ids=[uuid.uuid4()],
            event_id=uuid.uuid4(),
            source_unit_ids=[uuid.uuid4()],
            package=SimpleNamespace(inventory_sha256="a" * 64),
        )
        namespace["CompleteExecutionInput"] = SimpleNamespace(
            model_validate=lambda _: SimpleNamespace(payload=payload)
        )
        # Compile the real methods, preserving super() and the actual nested
        # RelationAssessment.bead statement/render mutation callback.
        for filename, methods, name, parent in (
            (
                "test_complete_input_execution.py",
                {"response"},
                "Output",
                "unittest.TestCase",
            ),
            ("test_authored_relations.py", {"response"}, "Related", "Output"),
            ("test_relation_assessment.py", {"bead"}, "Assessment", "Related"),
        ):
            tree = ast.parse((RUNTIME / filename).read_text())
            source_class = next(
                n
                for n in tree.body
                if isinstance(n, ast.ClassDef)
                and any(
                    isinstance(m, ast.FunctionDef) and m.name in methods for m in n.body
                )
            )
            body: list[ast.stmt] = [
                copy.deepcopy(m)
                for m in source_class.body
                if isinstance(m, ast.FunctionDef) and m.name in methods
            ]
            self.assertEqual(
                {m.name for m in body if isinstance(m, ast.FunctionDef)}, methods
            )
            cls = ast.ClassDef(
                name=name,
                bases=[ast.parse(parent, mode="eval").body],
                keywords=[],
                body=body,
                decorator_list=[],
                type_params=[],
            )
            exec(
                compile(
                    ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[])),
                    str(RUNTIME / filename),
                    "exec",
                ),
                namespace,
            )
        helper = ast.parse((RUNTIME / "test_agent_sql_helpers_runtime.py").read_text())
        helper_class = next(n for n in helper.body if isinstance(n, ast.ClassDef))
        text_node = next(
            n.value
            for n in helper_class.body
            if isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "TEXT" for t in n.targets)
        )
        self.assertIsInstance(text_node, ast.BinOp)
        assert isinstance(text_node, ast.BinOp)
        text = ast.literal_eval(text_node.left) * ast.literal_eval(text_node.right)
        self.assertGreater(len(text), 240)
        self.assertLessEqual(len(text), 2048)
        builder = namespace["Assessment"](methodName="runTest")
        builder.received = []
        frame = {
            "task_input": {
                "payload": {"relation_candidates": [], "relation_vocabulary": []}
            },
            "complete_input_window": {"final": True},
        }
        messages = [ModelRequest(parts=[UserPromptPart(json.dumps(frame))])]
        info = SimpleNamespace(output_tools=[SimpleNamespace(name="fictional")])

        def author(content: str, key: str, *, plan: Any, source: Any) -> Any:
            builder.statement_text, builder.plan = content, plan
            return builder.response(messages, info)

        builder.author = author
        response = builder.bead(text, key="long-normalized")
        output = response.parts[0].args_as_dict()["typed_output"]["annotations"][0]
        title, summary = output["render"]["title"], output["render"]["summary"][0]
        self.assertEqual(title["text"], text[:240])
        self.assertEqual(summary["text"], text)
        self.assertEqual(output["statements"][0]["statement_text"], text)
        self.assertIsNot(title, summary)
        self.assertEqual(title["statement_ids"], summary["statement_ids"])
