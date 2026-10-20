"""AST-only checks for discovery and evidence boundaries; no runtime imports."""

from __future__ import annotations

import ast
import hashlib
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "runtime/test_legacy7_unavailable.py"
REVIEWED_CASE_NAMES = (
    "test_fresh_revision7_activation_has_no_effect",
    "test_prior_successful_activation_replay_refuses_and_preserves_history",
    "test_queued_revision7_pauses_without_provider_execution",
    "test_revision4_control_still_applies",
    "test_successful_canonical_apply_replay_refuses_but_history_remains",
    "test_denied_late_apply_preserves_reported_usage_and_redelivery",
    "test_denied_late_apply_preserves_unknown_usage_and_redelivery",
    "test_explicit_resume_never_resets_attempt_or_model_allowances",
    "test_revision6_relation_history_and_new_authoring_remain_available",
    "test_hidden_candidate_family_preserves_missing_equivalence",
)


class Legacy7RuntimeCaseBoundaries(unittest.TestCase):
    def setUp(self) -> None:
        self.source = SOURCE.read_text()
        self.tree = ast.parse(self.source)

    def test_module_body_does_not_start_a_runtime_fixture(self) -> None:
        for node in self.tree.body:
            if isinstance(node, ast.Expr):
                self.assertIsInstance(node.value, ast.Constant)
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                self.assertFalse(any(isinstance(x, ast.Call) for x in ast.walk(node)))

    def test_no_inherited_fixture_test_rediscovery(self) -> None:
        classes = [x for x in self.tree.body if isinstance(x, ast.ClassDef)]
        self.assertEqual(len(classes), 1)
        self.assertEqual(
            [ast.unparse(base) for base in classes[0].bases], ["unittest.TestCase"]
        )
        constructors = [
            x for x in ast.walk(self.tree)
            if isinstance(x, ast.Call) and isinstance(x.func, ast.Attribute)
            and x.func.attr in ("AuthoredStatementKinds", "AuthoredRelations")
        ]
        self.assertEqual(len(constructors), 2)
        for call in constructors:
            self.assertIn(
                ("methodName", "runTest"),
                [(x.arg, ast.literal_eval(x.value)) for x in call.keywords],
            )

    def test_old_code_races_are_specification_data_not_skipped_acceptance(self) -> None:
        declarations = [
            x for x in self.tree.body if isinstance(x, ast.Assign)
            and any(isinstance(t, ast.Name)
                    and t.id == "OLD_CODE_CUTOVER_RACE_SPECIFICATIONS" for t in x.targets)
        ]
        self.assertEqual(len(declarations), 1)
        specifications = ast.literal_eval(declarations[0].value)
        self.assertEqual(len(specifications), 4)
        self.assertEqual(len({key for key, _ in specifications}), 4)
        self.assertTrue(all(value.startswith("SPEC_ONLY:") for _, value in specifications))
        for node in ast.walk(self.tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for decorator in node.decorator_list:
                    self.assertNotIn("skip", ast.unparse(decorator))
                if node.name.startswith("test_"):
                    self.assertNotIn("cutover", node.name)
            if isinstance(node, ast.Attribute):
                self.assertNotIn(node.attr, ("skipTest", "skip", "skipIf", "skipUnless"))

    def test_no_schema_privilege_or_guard_bypass_in_drafted_cases(self) -> None:
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                value = node.value.upper()
                for forbidden in ("DISABLE TRIGGER", "CREATE FUNCTION", "GRANT "):
                    self.assertNotIn(forbidden, value)

    def test_reviewed_ten_cases_are_preserved_with_only_two_first_write_cases_added(self) -> None:
        case = next(x for x in self.tree.body if isinstance(x, ast.ClassDef))
        methods = [x for x in case.body if isinstance(x, ast.FunctionDef)
                   and x.name.startswith("test_")]
        retained = [x for x in methods if x.name in REVIEWED_CASE_NAMES]
        self.assertEqual(tuple(x.name for x in retained), REVIEWED_CASE_NAMES)
        digest = hashlib.sha256("\n".join(
            ast.dump(x, include_attributes=False) for x in retained
        ).encode()).hexdigest()
        self.assertEqual(
            digest, "c3279dc472429b441814ea7bdf958a527e91f351c4cb5f6a6111a00fe87af38d"
        )
        self.assertEqual(
            [x.name for x in methods if x.name not in REVIEWED_CASE_NAMES],
            ["test_original_intent_ack_loss_retains_uncertain_usage_after_gate",
             "test_first_usage_ack_loss_preserves_event_and_worker_error_after_gate"],
        )
