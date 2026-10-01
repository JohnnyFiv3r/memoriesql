"""Application transactions run nothing but SET until they switch role.

Migration 0033 revokes PUBLIC EXECUTE on every catalog routine and re-grants it
only to memoriesql_application and memoriesql_worker. A login that is a member of
those roles without inheriting their privileges can execute nothing, not even
pg_catalog.set_config, until its transaction runs SET LOCAL ROLE. So before a
switch only static SET statements may run, and a transaction that never switches
may run nothing else unless it belongs to a login that is never an application
member. A call to a prologue helper, a function that itself switches role
outside any transaction such as `_begin`, counts as the switch.
"""

from __future__ import annotations

import ast
import unittest
from collections.abc import Iterator, Sequence
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[2] / "src" / "memoriesql"
ROLE_SWITCH = ("SET LOCAL ROLE ", "SET ROLE ")
NESTED_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)
# Transactions that run as the migrating owner, never as an application login.
NEVER_SWITCHED = {"infrastructure/postgres/migration_runner.py:migrate"}
# Each once ran a function before, or without, switching role.
GUARDED = (
    "infrastructure/jobs/integrated_semantic_worker.py",
    "infrastructure/postgres/evidence_packages.py",
    "infrastructure/postgres/local_client_pairing.py",
    "infrastructure/postgres/logical_unit_materialization.py",
    "infrastructure/postgres/personal_local_initialization.py",
    "infrastructure/postgres/relation_assessment.py",
    "infrastructure/postgres/relation_lifecycle.py",
    "infrastructure/postgres/relation_projection.py",
    "infrastructure/postgres/source_enrollment.py",
    "infrastructure/postgres/stored_bead_inspection.py",
)

Assignments = dict[str, list[tuple[int, ast.expr]]]
Event = tuple[int, int, str | None, bool]  # line, column, SQL text, is a switch


def _own_nodes(root: ast.AST) -> Iterator[ast.AST]:
    """The nodes of root's own body, not of the functions or classes inside it."""
    stack = list(ast.iter_child_nodes(root))
    while stack:
        node = stack.pop()
        if isinstance(node, NESTED_SCOPES):
            continue
        yield node
        stack.extend(ast.iter_child_nodes(node))


def _functions(tree: ast.AST) -> Iterator[ast.FunctionDef | ast.AsyncFunctionDef]:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            yield node


def _text(node: ast.expr, assignments: Assignments, line: int) -> str | None:
    """A statement's SQL text with interpolations elided; None unless static."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(
            part.value
            if isinstance(part, ast.Constant) and isinstance(part.value, str)
            else "{}"
            for part in node.values
        )
    if isinstance(node, ast.Name):
        earlier = [value for at, value in assignments.get(node.id, []) if at < line]
        return _text(earlier[-1], {}, line) if earlier else None
    return None


def _callee(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return call.func.id if isinstance(call.func, ast.Name) else None


def _transactions(nodes: list[ast.AST]) -> list[tuple[int, int]]:
    """The line spans of `with ....transaction():` blocks, in source order."""
    return sorted(
        (node.lineno, node.end_lineno or node.lineno)
        for node in nodes
        if isinstance(node, ast.With)
        and any(
            isinstance(item.context_expr, ast.Call)
            and _callee(item.context_expr) == "transaction"
            for item in node.items
        )
    )


def _events(
    function: ast.FunctionDef | ast.AsyncFunctionDef, helpers: set[str]
) -> tuple[list[Event], list[tuple[int, int]]]:
    """Statements and helper calls in source order, and the function's transactions."""
    nodes = list(_own_nodes(function))
    assignments: Assignments = {}
    for node in nodes:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            assignments.setdefault(node.targets[0].id, []).append(
                (node.lineno, node.value)
            )
    events: list[Event] = []
    for node in nodes:
        if not isinstance(node, ast.Call):
            continue
        if _callee(node) == "execute" and node.args:
            text = _text(node.args[0], assignments, node.lineno)
            switch = text is not None and text.startswith(ROLE_SWITCH)
            events.append((node.lineno, node.col_offset, text, switch))
        elif _callee(node) in helpers:
            events.append((node.lineno, node.col_offset, None, True))
    return sorted(events, key=lambda event: event[:2]), _transactions(nodes)


def _helpers(trees: Sequence[ast.AST]) -> set[str]:
    """Functions that switch role outside any transaction: prologue helpers."""
    found: set[str] = set()
    for tree in trees:
        for function in _functions(tree):
            events, blocks = _events(function, set())
            if any(
                switch and not any(start <= line <= end for start, end in blocks)
                for line, _, _, switch in events
            ):
                found.add(function.name)
    return found


def _scan(
    tree: ast.AST, where: str, helpers: set[str]
) -> tuple[list[str], list[tuple[str, int, str | None]]]:
    """Role-switching scopes, and each statement refused for running as the login.

    A scope is a `with ....transaction():` block, or a function's statements
    outside such blocks. Before its first switch a scope may run only static SET
    statements; a transaction that never switches may run nothing else.
    """
    switching: list[str] = []
    refused: list[tuple[str, int, str | None]] = []
    for function in _functions(tree):
        name = f"{where}:{function.name}"
        events, blocks = _events(function, helpers)
        scopes = [
            ([e for e in events if start <= e[0] <= end], True)
            for start, end in blocks
            # A savepoint inside a transaction that already switched keeps its role.
            if not any(
                outer < start <= end <= last
                and any(
                    switch and outer <= line < start for line, _, _, switch in events
                )
                for outer, last in blocks
            )
        ]
        scopes.append(
            (
                [
                    e
                    for e in events
                    if not any(start <= e[0] <= end for start, end in blocks)
                ],
                False,
            )
        )
        for scope, transaction in scopes:
            first = next(
                (index for index, (*_, switch) in enumerate(scope) if switch), None
            )
            if first is None and not (transaction and name not in NEVER_SWITCHED):
                # Outside a transaction it runs inside a caller's, already switched.
                continue
            if first is not None:
                switching.append(name)
            refused.extend(
                (name, line, text)
                for line, _, text, _ in scope[:first]
                if text is None or not text.startswith("SET ")
            )
    return switching, refused


class RoleSwitchPrologues(unittest.TestCase):
    def test_application_transactions_switch_role_before_any_function(self) -> None:
        paths = sorted(SOURCE.rglob("*.py"))
        trees = [
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for path in paths
        ]
        helpers = _helpers(trees)
        switching: list[str] = []
        refused: list[tuple[str, int, str | None]] = []
        for path, tree in zip(paths, trees, strict=True):
            found, wrong = _scan(tree, path.relative_to(SOURCE).as_posix(), helpers)
            switching += found
            refused += wrong
        self.assertEqual(refused, [])
        for adapter in GUARDED:
            with self.subTest(adapter=adapter):
                self.assertTrue(any(name.startswith(adapter + ":") for name in switching))

    def test_a_function_before_or_without_the_switch_is_refused(self) -> None:
        source = (
            "def begin(connection, timeout):\n"
            "    statement = f\"SET LOCAL statement_timeout='{timeout}ms'\"\n"
            "    with connection.transaction():\n"
            "        connection.execute('SET TRANSACTION ISOLATION LEVEL READ COMMITTED')\n"
            "        connection.execute(statement)\n"
            "        connection.execute(\"SELECT set_config('lock_timeout','500',true)\")\n"
            "        connection.execute('SET LOCAL ROLE memoriesql_application')\n"
            "        connection.execute('SELECT 1')\n"
            "    with connection.transaction():\n"
            "        connection.execute('SELECT 2')\n"
            "def _begin(connection, query):\n"
            "    connection.execute(query)\n"
            "    connection.execute('SET LOCAL ROLE memoriesql_worker')\n"
            "def through_helper(connection):\n"
            "    with connection.transaction():\n"
            "        _begin(connection, \"SET LOCAL lock_timeout='500ms'\")\n"
            "        connection.execute('SELECT 3')\n"
            "def inside_a_callers_transaction(connection):\n"
            "    connection.execute('SELECT 4')\n"
        )
        tree = ast.parse(source)
        helpers = _helpers([tree])
        self.assertEqual(helpers, {"_begin"})
        switching, refused = _scan(tree, "fictional.py", helpers)
        self.assertEqual(
            switching,
            ["fictional.py:begin", "fictional.py:_begin", "fictional.py:through_helper"],
        )
        self.assertEqual(
            refused,
            [
                (
                    "fictional.py:begin",
                    6,
                    "SELECT set_config('lock_timeout','500',true)",
                ),
                ("fictional.py:begin", 10, "SELECT 2"),
                ("fictional.py:_begin", 12, None),
            ],
        )


if __name__ == "__main__":
    unittest.main()
