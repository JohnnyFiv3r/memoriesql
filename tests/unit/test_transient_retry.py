"""Database-free behaviour of the bounded owned-transaction retry."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path
from unittest.mock import patch

from psycopg import errors

from memoriesql.infrastructure.postgres import transient_retry
from memoriesql.infrastructure.postgres.transient_retry import (
    ATTEMPTS,
    run_owned,
    transient,
)

ROOT = Path(__file__).resolve().parents[2]
INFRASTRUCTURE = ROOT / "src/memoriesql/infrastructure"
# Owned transactions that set a lock timeout but are not re-run here, and why.
EXEMPT = {
    # Agent-SQL operations carry an admitted deadline and their own outcome
    # mapping; their retry belongs to the agent-SQL admission change.
    ("postgres/agent_sql_results.py", "_owned"),
    ("postgres/agent_sql_results.py", "_cleanup_workspace"),
    ("postgres/agent_sql_results.py", "cleanup_expired"),
    ("postgres/restricted_query.py", "_control_call"),
    ("results_broker/server.py", "paired_agent"),
    # The operator's migration runner waits up to 10 s for its own lock.
    ("postgres/migration_runner.py", "migrate"),
    # The snapshot transaction runs the caller's body; only its lock-taking
    # transaction (`acquire`) can be re-run.
    ("postgres/relation_projection.py", "relation_projection_frame"),
}


def owned_lock_transactions() -> set[tuple[str, str]]:
    """(module, enclosing function) of every owned transaction with a lock timeout."""
    found = set()
    for path in sorted(INFRASTRUCTURE.rglob("*.py")):
        tree = ast.parse(path.read_text())
        parents = {
            child: node
            for node in ast.walk(tree)
            for child in ast.iter_child_nodes(node)
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.With):
                continue
            context = node.items[0].context_expr
            if not (
                isinstance(context, ast.Call)
                and isinstance(context.func, ast.Attribute)
                and context.func.attr == "transaction"
            ):
                continue
            if not any(
                isinstance(item, ast.Constant)
                and isinstance(item.value, str)
                and "lock_timeout" in item.value
                or isinstance(item, ast.Attribute)
                and item.attr == "_begin"
                for item in ast.walk(node)
            ):
                continue
            enclosing = parents[node]
            while not isinstance(enclosing, ast.FunctionDef | ast.AsyncFunctionDef):
                enclosing = parents[enclosing]
            found.add((path.relative_to(INFRASTRUCTURE).as_posix(), enclosing.name))
    return found


def no_jitter() -> float:
    return 0.0


def full_jitter() -> float:
    return 1.0


def lock_timeout() -> errors.LockNotAvailable:
    return errors.LockNotAvailable("canceling statement due to lock timeout")


class Classification(unittest.TestCase):
    def test_lock_contention_is_transient(self) -> None:
        self.assertTrue(transient(lock_timeout()))
        self.assertTrue(transient(errors.DeadlockDetected("deadlock detected")))
        self.assertTrue(
            transient(
                errors.SerializationFailure(
                    "could not serialize access due to concurrent update"
                )
            )
        )

    def test_a_conflict_a_kernel_function_raises_is_not(self) -> None:
        # Kernel functions raise 40001 themselves for stale revisions.
        self.assertFalse(
            transient(errors.SerializationFailure("stale pairing-grant revision"))
        )
        self.assertFalse(
            transient(errors.SerializationFailure("stale access grant revision"))
        )

    def test_timeouts_refusals_and_other_errors_are_not(self) -> None:
        for error in (
            errors.QueryCanceled("canceling statement due to statement timeout"),
            errors.InsufficientPrivilege("permission denied"),
            errors.UniqueViolation("duplicate key"),
            RuntimeError("not a database error"),
        ):
            with self.subTest(error=type(error).__name__):
                self.assertFalse(transient(error))


class BoundedRerun(unittest.TestCase):
    def setUp(self) -> None:
        self.waits: list[float] = []
        pausing = patch.object(transient_retry, "pause", self.waits.append)
        pausing.start()
        self.addCleanup(pausing.stop)
        steady = patch.object(transient_retry, "jitter", no_jitter)
        steady.start()
        self.addCleanup(steady.stop)

    def failing(self, *outcomes: BaseException | str) -> tuple[list[int], object]:
        calls: list[int] = []

        def attempt() -> str:
            calls.append(len(calls) + 1)
            outcome = outcomes[len(calls) - 1]
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

        return calls, attempt

    def test_a_transient_failure_is_run_again_until_it_succeeds(self) -> None:
        calls, attempt = self.failing(lock_timeout(), lock_timeout(), "done")
        self.assertEqual(run_owned(attempt), "done")  # type: ignore[arg-type]
        self.assertEqual(calls, [1, 2, 3])
        # Half of each doubling backoff, with no jitter.
        self.assertEqual(self.waits, [0.025, 0.05])

    def test_attempts_are_bounded_and_the_last_error_is_unchanged(self) -> None:
        last = lock_timeout()
        calls, attempt = self.failing(*[lock_timeout()] * (ATTEMPTS - 1), last)
        with self.assertRaises(errors.LockNotAvailable) as raised:
            run_owned(attempt)  # type: ignore[arg-type]
        self.assertIs(raised.exception, last)
        self.assertEqual(len(calls), ATTEMPTS)
        self.assertEqual(self.waits, [0.025, 0.05, 0.1])

    def test_backoff_is_capped_and_jitter_fills_only_the_upper_half(self) -> None:
        with patch.object(transient_retry, "jitter", full_jitter):
            calls, attempt = self.failing(*[lock_timeout()] * ATTEMPTS)
            with self.assertRaises(errors.LockNotAvailable):
                run_owned(attempt)  # type: ignore[arg-type]
        self.assertEqual(self.waits, [0.05, 0.1, 0.2])
        self.assertLessEqual(max(self.waits), transient_retry.MAX_BACKOFF_SECONDS)

    def test_anything_else_is_raised_at_once(self) -> None:
        stale = errors.SerializationFailure("stale pairing-grant revision")
        calls, attempt = self.failing(stale)
        with self.assertRaises(errors.SerializationFailure) as raised:
            run_owned(attempt)  # type: ignore[arg-type]
        self.assertIs(raised.exception, stale)
        self.assertEqual((calls, self.waits), ([1], []))

    def test_one_attempt_never_waits(self) -> None:
        calls, attempt = self.failing(lock_timeout())
        with self.assertRaises(errors.LockNotAvailable):
            run_owned(attempt, attempts=1)  # type: ignore[arg-type]
        self.assertEqual((calls, self.waits), ([1], []))

    def test_the_attempt_count_is_validated(self) -> None:
        for attempts in (0, ATTEMPTS + 1, True):
            with self.subTest(attempts=attempts), self.assertRaises(ValueError):
                run_owned(lambda: None, attempts=attempts)


class Wiring(unittest.TestCase):
    def test_every_owned_lock_timeout_transaction_is_rerun_or_exempt(self) -> None:
        found = owned_lock_transactions()
        rerun = {site for site in found if site[1] in {"attempt", "acquire"}}
        self.assertEqual(found - rerun, EXEMPT)
        # Each re-runnable closure is handed to run_owned in its module.
        for module in {module for module, _ in rerun}:
            with self.subTest(module=module):
                self.assertIn("run_owned(", (INFRASTRUCTURE / module).read_text())


if __name__ == "__main__":
    unittest.main()
