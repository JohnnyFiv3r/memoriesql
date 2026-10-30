"""Deterministic accounting misconfigurations pause the task for owner action."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from memoriesql.application.managed_dispatch import SupervisedDispatchUnavailable
from memoriesql.application.model_accounting import (
    ACCOUNTING_CONFIGURATION_REFUSALS,
    AccountingPersistenceError,
    CostSafetyCeilingExceeded,
    ModelAccountingConfigurationError,
    RequiredModelAllowanceUnavailable,
    accounting_configuration_refusal,
)
from memoriesql.application.semantic_task_contracts import (
    RetryClass,
    SemanticResultStatus,
    retry_class_for_status,
)
from memoriesql.infrastructure.models.pydanticai_executor import _normalize_failure
from memoriesql.infrastructure.postgres.model_accounting import (
    _raise_accounting_error,
)

ROOT = Path(__file__).resolve().parents[2]
ERROR_CODE = re.compile(r"^[a-z][a-z0-9_-]*([.][a-z0-9][a-z0-9_-]*)*$")
EXECUTOR_CODES = (
    "accounting.access_scope_ambiguous",
    "accounting.supervision_task_mismatch",
)


class _Diagnostic:
    def __init__(self, primary: str) -> None:
        self.message_primary = primary


class _DatabaseError(Exception):
    """A driver error whose diagnostics name the database's primary message."""

    def __init__(self, primary: str, context: str = "") -> None:
        super().__init__(primary + context)
        self.diag = _Diagnostic(primary)


def classified(error: Exception) -> BaseException:
    try:
        _raise_accounting_error(error)
    except Exception as raised:
        return raised
    raise AssertionError("the accounting adapter must always raise")


def intent_function_body() -> str:
    """The installed (latest) definition of the provider request intent function."""

    body = None
    for migration in sorted((ROOT / "migrations").glob("[0-9][0-9][0-9][0-9]_*.sql")):
        text = migration.read_text()
        for match in re.finditer(
            r"FUNCTION memoriesql\.record_model_provider_request_intent\(.*?\n\$\$;",
            text,
            re.DOTALL,
        ):
            body = match.group(0)
    assert body is not None
    return body


class AccountingOwnerAction(unittest.TestCase):
    def test_each_configuration_refusal_names_its_setting(self) -> None:
        for message, (code, setting) in ACCOUNTING_CONFIGURATION_REFUSALS.items():
            with self.subTest(message=message):
                error = _DatabaseError(message, "\nCONTEXT: PL/pgSQL function")
                raised = classified(error)
                self.assertIsInstance(raised, ModelAccountingConfigurationError)
                assert isinstance(raised, ModelAccountingConfigurationError)
                self.assertEqual((raised.code, raised.setting), (code, setting))
                self.assertIn(code, str(raised))
                self.assertIs(raised.__cause__, error)
                self.assertRegex(code, ERROR_CODE)
                self.assertLessEqual(len(code), 128)
                self.assertTrue(code.startswith("accounting."))
                self.assertTrue(setting.strip())

    def test_only_the_exact_refusal_message_counts(self) -> None:
        # A type error can quote an authored value; that is not this refusal.
        quoted = (
            'invalid input syntax for type uuid: "pinned pricing revision is unavailable"'
        )
        self.assertIsInstance(
            classified(_DatabaseError(quoted)), AccountingPersistenceError
        )
        self.assertIsInstance(
            classified(_DatabaseError("provider request worker lease is not live")),
            AccountingPersistenceError,
        )
        self.assertIsNone(
            accounting_configuration_refusal("pinned pricing revision is unavailable.")
        )

    def test_an_error_without_diagnostics_is_judged_by_its_text(self) -> None:
        self.assertIsInstance(
            classified(RuntimeError("pinned pricing revision is unavailable")),
            ModelAccountingConfigurationError,
        )
        self.assertIsInstance(
            classified(RuntimeError("connection refused")), AccountingPersistenceError
        )

    def test_existing_accounting_markers_are_unchanged(self) -> None:
        self.assertIsInstance(
            classified(_DatabaseError("accounting.cost_safety_ceiling")),
            CostSafetyCeilingExceeded,
        )
        self.assertIsInstance(
            classified(_DatabaseError("accounting.required_model_allowance_unavailable")),
            RequiredModelAllowanceUnavailable,
        )
        self.assertIsInstance(
            classified(_DatabaseError("supervised_dispatch_allowance_consumed")),
            SupervisedDispatchUnavailable,
        )

    def test_owner_action_pauses_and_is_never_a_transient_retry(self) -> None:
        codes = [code for code, _ in ACCOUNTING_CONFIGURATION_REFUSALS.values()]
        for code in (*codes, *EXECUTOR_CODES):
            with self.subTest(code=code):
                failure = _normalize_failure(
                    ModelAccountingConfigurationError(code, "a fictional setting")
                )
                self.assertEqual(
                    (failure.status, failure.error_code),
                    (SemanticResultStatus.POLICY_PAUSED, code),
                )
                self.assertEqual(retry_class_for_status(failure.status), RetryClass.POLICY)
        # A real persistence failure still retries.
        persistence = _normalize_failure(
            AccountingPersistenceError("durable model accounting failed")
        )
        self.assertEqual(persistence.status, SemanticResultStatus.UNAVAILABLE)
        self.assertEqual(retry_class_for_status(persistence.status), RetryClass.TRANSIENT)

    def test_every_mapped_message_is_a_refusal_of_the_installed_intent_function(
        self,
    ) -> None:
        body = intent_function_body()
        self.assertIn("consume_supervised_dispatch", body)
        for message in ACCOUNTING_CONFIGURATION_REFUSALS:
            with self.subTest(message=message):
                self.assertRegex(body, r"RAISE EXCEPTION\s+'" + re.escape(message) + "'")


if __name__ == "__main__":
    unittest.main()
