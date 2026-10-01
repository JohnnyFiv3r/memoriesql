"""No transaction executes a function before it switches role.

Migration 0033 revokes PUBLIC EXECUTE on every catalog routine and re-grants it
only to memoriesql_application and memoriesql_worker. A login that is a member of
those roles without inheriting their privileges can execute nothing, not even
pg_catalog.set_config, until its transaction runs SET LOCAL ROLE. Only SET
statements may come first, and they must be static text the scan can read.
"""

from __future__ import annotations

import ast
import unittest
from collections.abc import Iterator
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[2] / "src" / "memoriesql"
ROLE_SWITCH = ("SET LOCAL ROLE ", "SET ROLE ")
NESTED_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)
# Their prologues once called set_config before the switch.
GUARDED = (
    "infrastructure/jobs/integrated_semantic_worker.py",
    "infrastructure/postgres/evidence_packages.py",
    "infrastructure/postgres/local_client_pairing.py",
    "infrastructure/postgres/logical_unit_materialization.py",
    "infrastructure/postgres/personal_local_initialization.py",
    "infrastructure/postgres/relation_assessment.py",
    "infrastructure/postgres/relation_lifecycle.py",
    "infrastructure/postgres/source_enrollment.py",
    "infrastructure/postgres/stored_bead_inspection.py",
)

Assignments = dict[str, list[tuple[int, ast.expr]]]


def _own_nodes(root: ast.AST) -> Iterator[ast.AST]:
    """The nodes of root's own body, not of the functions or classes inside it."""
    stack = list(ast.iter_child_nodes(root))
    while stack:
        node = stack.pop()
        if isinstance(node, NESTED_SCOPES):
            continue
        yield node
        stack.extend(ast.iter_child_nodes(node))


def _text(node: ast.expr, assignments: Assignments, line: int) -> str | None:
    """A statement's SQL text with interpolations elided; None unless static."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(
            part.value if isinstance(part, ast.Constant) else "{}"
            for part in node.values
        )
    if isinstance(node, ast.Name):
        earlier = [value for at, value in assignments.get(node.id, []) if at < line]
        return _text(earlier[-1], {}, line) if earlier else None
    return None


def _opens_transaction(statement: ast.With) -> bool:
    return any(
        isinstance(item.context_expr, ast.Call)
        and isinstance(item.context_expr.func, ast.Attribute)
        and item.context_expr.func.attr == "transaction"
        for item in statement.items
    )


def _scan(
    tree: ast.AST, where: str
) -> tuple[list[str], list[tuple[str, int, str | None]]]:
    """Role-switching scopes, and each statement before a switch that is not SET.

    A scope is a `with ....transaction():` block, or a function's statements
    outside such blocks (a prologue helper such as `_begin`).
    """
    switching: list[str] = []
    refused: list[tuple[str, int, str | None]] = []
    for function in ast.walk(tree):
        if not isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
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
        executions = sorted(
            (
                node
                for node in nodes
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "execute"
                and node.args
            ),
            key=lambda call: (call.lineno, call.col_offset),
        )
        blocks = [
            (node.lineno, node.end_lineno or node.lineno)
            for node in nodes
            if isinstance(node, ast.With) and _opens_transaction(node)
        ]
        scopes = [
            [call for call in executions if start <= call.lineno <= end]
            for start, end in blocks
        ]
        scopes.append(
            [
                call
                for call in executions
                if not any(start <= call.lineno <= end for start, end in blocks)
            ]
        )
        for calls in scopes:
            texts = [
                (call.lineno, _text(call.args[0], assignments, call.lineno))
                for call in calls
            ]
            first = next(
                (
                    index
                    for index, (_, text) in enumerate(texts)
                    if text is not None and text.startswith(ROLE_SWITCH)
                ),
                None,
            )
            if first is None:
                continue
            name = f"{where}:{function.name}"
            switching.append(name)
            refused.extend(
                (name, line, text)
                for line, text in texts[:first]
                if text is None or not text.startswith("SET ")
            )
    return switching, refused


class RoleSwitchPrologues(unittest.TestCase):
    def test_only_set_statements_precede_a_role_switch(self) -> None:
        switching: list[str] = []
        refused: list[tuple[str, int, str | None]] = []
        for path in sorted(SOURCE.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            found, wrong = _scan(tree, path.relative_to(SOURCE).as_posix())
            switching += found
            refused += wrong
        self.assertEqual(refused, [])
        for adapter in GUARDED:
            with self.subTest(adapter=adapter):
                self.assertTrue(any(name.startswith(adapter + ":") for name in switching))

    def test_a_function_or_unreadable_statement_before_the_switch_is_refused(
        self,
    ) -> None:
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
            "def helper(connection, query):\n"
            "    connection.execute(query)\n"
            "    connection.execute('SET LOCAL ROLE memoriesql_worker')\n"
            "def unswitched(connection):\n"
            "    connection.execute('SELECT 3')\n"
        )
        switching, refused = _scan(ast.parse(source), "fictional.py")
        self.assertEqual(switching, ["fictional.py:begin", "fictional.py:helper"])
        self.assertEqual(
            refused,
            [
                (
                    "fictional.py:begin",
                    6,
                    "SELECT set_config('lock_timeout','500',true)",
                ),
                ("fictional.py:helper", 12, None),
            ],
        )


if __name__ == "__main__":
    unittest.main()
