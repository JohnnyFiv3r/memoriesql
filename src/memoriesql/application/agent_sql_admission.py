"""Closed PostgreSQL AST admission; no optimizer, query execution or provider path.

The trusted execution bridge must independently enforce database authority and
complete provenance/admission. This internal kernel alone publishes no results.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from importlib.metadata import PackageNotFoundError, distribution, version
from importlib.util import find_spec
from typing import Any, NoReturn

from sqlglot import ErrorLevel, exp, tokenizer_core
from sqlglot.dialects.postgres import Postgres
from sqlglot.errors import SqlglotError, UnsupportedError
from sqlglot.generators.postgres import PostgresGenerator
from sqlglot.parsers.postgres import PostgresParser

from memoriesql.application.agent_sql_catalog import (
    BoundParameter,
    SqlAdmissionError,
    SqlCatalog,
    SqlColumn,
    SqlParameter,
    SqlRelation,
    SqlType,
)

PARSER_VERSION = "30.19.0"
BOOL = SqlType("bool")
INT8 = SqlType("int8")
NULL = SqlType("null", nullable=True)
NUMERIC = {"int8", "numeric"}


class _ClosedPostgres(Postgres):
    class Generator(PostgresGenerator):
        # psycopg's parameter format applies even inside quoted SQL tokens.
        # Escape only emitted value/identifier tokens, never placeholders.
        def identifier_sql(self, expression: exp.Identifier) -> str:
            return super().identifier_sql(expression).replace("%", "%%")

        def literal_sql(self, expression: exp.Literal) -> str:
            return super().literal_sql(expression).replace("%", "%%")

    class Parser(PostgresParser):
        def _parse_as_command(self, start: Any) -> exp.Command:
            # SQLGlot's command fallback otherwise logs original query text.
            raise SqlAdmissionError("unsupported", "statement")

        def _parse_command(self) -> exp.Command:
            raise SqlAdmissionError("unsupported", "statement")


@dataclass(frozen=True)
class RecursionBound:
    cte: str
    depth_column: str
    node_column: str
    max_depth: int


@dataclass(frozen=True)
class AdmittedQuery:
    sql: str
    parameters: dict[str, Any]
    columns: tuple[SqlColumn, ...]
    catalog_hash: str
    relations: tuple[str, ...]
    derivation_program: dict[str, Any]
    recursion: RecursionBound | None
    execution_tree: list[dict[str, Any]]


@dataclass
class _Scope:
    sources: dict[str, SqlRelation]
    parent: _Scope | None = None
    origins: dict[str, str] | None = None

    def column(self, node: exp.Column) -> SqlType:
        if node.args.get("catalog"):
            _refuse(node, "column_qualification")
        if node.args.get("db"):
            qualified = node.db + "." + node.table
            if self.origins and self.origins.get(node.table) == qualified:
                return self.sources[node.table].column(node.name).type
            if self.parent:
                return self.parent.column(node)
            _refuse(node, "column_qualification")
        if node.table:
            if node.table in self.sources:
                return self.sources[node.table].column(node.name).type
            if self.parent:
                return self.parent.column(node)
            _refuse(node, "unknown_source", "invalid_request")
        found = [
            c.type
            for r in self.sources.values()
            for c in r.columns
            if c.name == node.name
        ]
        if len(found) == 1:
            return found[0]
        if not found and self.parent:
            return self.parent.column(node)
        _refuse(
            node, "ambiguous_column" if found else "unknown_column", "invalid_request"
        )

    def total_key(self, expressions: list[exp.Expr]) -> bool:
        present = {
            (n.table, n.name)
            for e in expressions
            for n in [e]
            if isinstance(n, exp.Column)
        }
        for alias, relation in self.sources.items():
            if not any(
                all((alias, name) in present or ("", name) in present for name in key)
                for key in relation.unique_keys
            ):
                return False
        return True


def _refuse(
    node: exp.Expr | None, construct: str, code: str = "unsupported"
) -> NoReturn:
    offset = None
    if node:
        value = node.meta.get("start")
        if type(value) is int:
            offset = value
    raise SqlAdmissionError(code, construct, offset)


def _same(left: SqlType, right: SqlType, node: exp.Expr) -> SqlType:
    if left.pg_type == "null":
        return right.optional()
    if right.pg_type == "null":
        return left.optional()
    if not left.same_value_type(right):
        _refuse(node, "incompatible_types", "invalid_request")
    return replace(left, nullable=left.nullable or right.nullable)


def _integer(node: exp.Expr | None, maximum: int) -> int:
    if (
        not isinstance(node, exp.Literal)
        or node.is_string
        or not re.fullmatch(r"[0-9]+", node.this)
    ):
        _refuse(node, "structural_integer", "invalid_request")
    number = int(node.this)
    if number > maximum:
        _refuse(node, "structural_integer", "invalid_request")
    return number


# Explicit classes AND argument keys. Unknown SQLGlot releases are not admitted.
_ARGS: dict[str, set[str]] = {
    "Select": {
        "expressions",
        "from_",
        "joins",
        "where",
        "group",
        "having",
        "distinct",
        "order",
        "limit",
        "offset",
        "with_",
    },
    "Union": {"this", "expression", "distinct", "order", "limit", "offset", "with_"},
    "Intersect": {
        "this",
        "expression",
        "distinct",
        "order",
        "limit",
        "offset",
        "with_",
    },
    "Except": {"this", "expression", "distinct", "order", "limit", "offset", "with_"},
    "Subquery": {"this", "alias"},
    "With": {"expressions", "recursive", "search"},
    "CTE": {"this", "alias", "materialized"},
    "RecursiveWithSearch": {"kind", "this", "expression", "using"},
    "From": {"this"},
    "Join": {"this", "side", "kind", "on"},
    "Table": {"this", "db", "alias"},
    "TableAlias": {"this", "columns"},
    "Identifier": {"this", "quoted"},
    "Column": {"this", "table", "db"},
    "Alias": {"this", "alias"},
    "Where": {"this"},
    "Having": {"this"},
    "Group": {"expressions"},
    "Distinct": {"expressions"},
    "Order": {"expressions"},
    "Ordered": {"this", "desc", "nulls_first"},
    "Limit": {"expression"},
    "Offset": {"expression"},
    "Window": {"this", "partition_by", "order", "spec", "over"},
    "WindowSpec": {"kind", "start", "start_side", "end", "end_side"},
    "Parameter": {"this"},
    "Literal": {"this", "is_string"},
    "Null": set(),
    "Star": set(),
    "Paren": {"this"},
    "And": {"this", "expression"},
    "Or": {"this", "expression"},
    "Not": {"this"},
    "EQ": {"this", "expression"},
    "NEQ": {"this", "expression"},
    "LT": {"this", "expression"},
    "LTE": {"this", "expression"},
    "GT": {"this", "expression"},
    "GTE": {"this", "expression"},
    "Is": {"this", "expression", "negate"},
    "In": {"this", "expressions", "query"},
    "Any": {"this"},
    "Exists": {"this"},
    "Like": {"this", "expression"},
    "ILike": {"this", "expression"},
    "Add": {"this", "expression"},
    "Sub": {"this", "expression"},
    "Mul": {"this", "expression"},
    "Div": {"this", "expression", "typed", "safe"},
    "Neg": {"this"},
    "Case": {"ifs", "default"},
    "If": {"this", "true"},
    "Cast": {"this", "to"},
    "DataType": {"this", "expressions", "nested"},
    "Lower": {"this"},
    "Upper": {"this"},
    "Coalesce": {"this", "expressions", "is_nvl", "is_null"},
    "Nullif": {"this", "expression"},
    "TimestampTrunc": {"this", "unit", "zone"},
    "Var": {"this"},
    "Count": {"this", "expressions", "big_int"},
    "Sum": {"this"},
    "Avg": {"this"},
    "Min": {"this"},
    "Max": {"this"},
    "Filter": {"this", "expression"},
    "RowNumber": set(),
    "Rank": {"expressions"},
    "DenseRank": {"expressions"},
    "Lag": {"this", "offset", "default"},
    "Lead": {"this", "offset", "default"},
}


def _closed_tree(tree: exp.Expr) -> None:
    nodes = list(tree.walk())
    if len(nodes) > 4096:
        _refuse(tree, "ast_nodes", "invalid_request")
    compositions = 0
    for node in nodes:
        name = type(node).__name__
        if (
            isinstance(node, exp.Distinct)
            and isinstance(node.parent, exp.Select)
            and (node.expressions or node.args.get("on"))
        ):
            _refuse(node, "distinct_on")
        if name not in _ARGS:
            _refuse(node, name)
        if any(
            value is not None and value != [] and key not in _ARGS[name]
            for key, value in node.args.items()
        ):
            _refuse(node, "clause")
        if isinstance(node, exp.Join | exp.Group | exp.SetOperation | exp.Window):
            compositions += 1
        if isinstance(node, exp.Div) and node.args.get("safe"):
            _refuse(node, "safe_division")
        depth = 0
        parent = node.parent
        while parent:
            depth += 1
            if depth > 32:
                _refuse(node, "ast_depth", "invalid_request")
            parent = parent.parent
    if (
        compositions > 16
        or len(list(tree.find_all(exp.CTE))) > 8
        or len(list(tree.find_all(exp.Table))) > 16
    ):
        _refuse(tree, "composition_budget", "invalid_request")


class _Binder:
    def __init__(
        self,
        catalog: SqlCatalog,
        parameters: dict[int, BoundParameter],
        inputs: dict[str, SqlRelation],
        evaluation_admitted: bool,
        recursion: RecursionBound | None,
    ) -> None:
        self.catalog = catalog
        self.parameters = parameters
        self.inputs = inputs
        self.evaluation_admitted = evaluation_admitted
        self.recursion = recursion
        self.used_parameters: set[int] = set()
        self.used_relations: set[str] = set()
        self.structural: set[int] = set()
        self.recursion_seen = False

    def query(
        self,
        node: exp.Expr,
        outer: _Scope | None,
        ctes: dict[str, SqlRelation],
    ) -> SqlRelation:
        if isinstance(node, exp.Subquery):
            return self.query(node.this, outer, ctes)
        with_ = node.args.get("with_")
        local = dict(ctes)
        if with_:
            for cte in with_.expressions:
                name = cte.alias
                if not name or name in local or name in self.inputs:
                    _refuse(cte, "cte_name", "invalid_request")
                self_references = [
                    t
                    for t in cte.this.find_all(exp.Table)
                    if not t.db and t.name == name
                ]
                if self_references:
                    if not with_.args.get("recursive") or self.recursion_seen:
                        _refuse(cte, "recursion")
                    local[name] = self.recursive(cte, with_, local)
                else:
                    local[name] = self.rename(
                        self.query(cte.this, None, local), cte.args["alias"]
                    )
            if with_.args.get("recursive") and not self.recursion_seen:
                _refuse(with_, "recursion", "invalid_request")
        if isinstance(node, exp.Select):
            result = self.projection(node, outer, local)
        elif isinstance(node, exp.Union | exp.Intersect | exp.Except):
            left = self.query(node.this, outer, local)
            right = self.query(node.expression, outer, local)
            if len(left.columns) != len(right.columns):
                _refuse(node, "set_columns", "invalid_request")
            columns = tuple(
                SqlColumn(a.name, _same(a.type, b.type, node))
                for a, b in zip(left.columns, right.columns, strict=True)
            )
            keys = (
                (tuple(c.name for c in columns),)
                if node.args.get("distinct") is not False
                else ()
            )
            result = SqlRelation(columns, keys)
            self.order(node.args.get("order"), _Scope({"": result}), {})
        else:
            _refuse(node, "select")
        for key in ("limit", "offset"):
            item = node.args.get(key)
            if item:
                value = item.expression
                if isinstance(value, exp.Parameter):
                    parameter = self.parameter(value)
                    if (
                        parameter.type.pg_type != "int8"
                        or parameter.type.array
                        or parameter.value is None
                    ):
                        _refuse(value, key, "invalid_request")
                    number = parameter.value
                else:
                    number = _integer(value, 2**63 - 1)
                    self.structural.add(id(value))
                if number < 0 or (key == "offset" and number > 10000):
                    _refuse(value, key, "invalid_request")
        return result

    def rename(
        self, relation: SqlRelation, alias: exp.TableAlias | None
    ) -> SqlRelation:
        names = [i.name for i in (alias.args.get("columns") or [])] if alias else []
        if len(names) > len(relation.columns):
            _refuse(alias, "column_alias", "invalid_request")
        renamed = [
            names[i] if i < len(names) else c.name
            for i, c in enumerate(relation.columns)
        ]
        if len(set(renamed)) != len(renamed):
            _refuse(alias, "column_alias", "invalid_request")
        mapping = {c.name: renamed[i] for i, c in enumerate(relation.columns)}
        return SqlRelation(
            tuple(
                SqlColumn(renamed[i], c.type) for i, c in enumerate(relation.columns)
            ),
            tuple(tuple(mapping[n] for n in key) for key in relation.unique_keys),
        )

    def source(
        self, node: exp.Expr, ctes: dict[str, SqlRelation]
    ) -> tuple[str, SqlRelation]:
        if isinstance(node, exp.Table):
            if not node.db and node.name in ctes:
                relation = ctes[node.name]
            elif node.db == "input" and node.name in self.inputs:
                relation = self.inputs[node.name]
                self.used_relations.add("input." + node.name)
            else:
                name = node.db + "." + node.name
                if name not in self.catalog.relations or (
                    node.db == "evaluation_v1" and not self.evaluation_admitted
                ):
                    _refuse(
                        node,
                        "relation",
                        "unavailable" if node.db == "evaluation_v1" else "unsupported",
                    )
                relation = self.catalog.relations[name]
                self.used_relations.add(name)
            return node.alias_or_name, self.rename(relation, node.args.get("alias"))
        if isinstance(node, exp.Subquery) and node.alias:
            return node.alias, self.rename(
                self.query(node.this, None, ctes), node.args.get("alias")
            )
        _refuse(node, "from_source")

    def projection(
        self, node: exp.Select, outer: _Scope | None, ctes: dict[str, SqlRelation]
    ) -> SqlRelation:
        scope = _Scope({}, outer)
        scope.origins = {}
        if node.args.get("from_"):
            source = node.args["from_"].this
            alias, relation = self.source(source, ctes)
            scope.sources[alias] = relation
            if isinstance(source, exp.Table) and source.db and not source.alias:
                scope.origins[alias] = source.db + "." + source.name
        for join in node.args.get("joins") or []:
            alias, relation = self.source(join.this, ctes)
            if alias in scope.sources or not scope.sources:
                _refuse(join, "join_source", "invalid_request")
            prior = set(scope.sources)
            scope.sources[alias] = relation
            if (
                isinstance(join.this, exp.Table)
                and join.this.db
                and not join.this.alias
            ):
                scope.origins[alias] = join.this.db + "." + join.this.name
            if join.args.get("kind") not in (None, "INNER", "OUTER") or join.args.get(
                "side"
            ) not in (None, "LEFT", "RIGHT", "FULL"):
                _refuse(join, "join_kind")
            on = join.args.get("on")
            predicates = self.conjuncts(on)
            connected = False
            for predicate in predicates:
                if (
                    not isinstance(predicate, exp.EQ)
                    or not isinstance(predicate.this, exp.Column)
                    or not isinstance(predicate.expression, exp.Column)
                ):
                    _refuse(predicate, "equality_join")
                a, b = predicate.this, predicate.expression
                at, bt = scope.column(a), scope.column(b)
                _same(at, bt, predicate)
                if (
                    {a.table, b.table} & {alias}
                    and {a.table, b.table} & prior
                    and at.reference_kind
                ):
                    connected = True
            if not connected:
                _refuse(join, "identity_join")
            if join.args.get("side") in ("RIGHT", "FULL"):
                for name in prior:
                    r = scope.sources[name]
                    scope.sources[name] = replace(
                        r,
                        columns=tuple(
                            SqlColumn(c.name, c.type.optional()) for c in r.columns
                        ),
                    )
            if join.args.get("side") in ("LEFT", "FULL"):
                scope.sources[alias] = replace(
                    relation,
                    columns=tuple(
                        SqlColumn(c.name, c.type.optional()) for c in relation.columns
                    ),
                )
        where = node.args.get("where")
        if where:
            _phase(where.this, aggregates=False, windows=False)
            self.boolean(where.this, scope, ctes)
        columns = []
        synthetic: set[str] = set()
        expressions: dict[str, exp.Expr] = {}
        for index, projected in enumerate(node.expressions):
            value = projected.this if isinstance(projected, exp.Alias) else projected
            name = projected.alias_or_name
            enclosing = node.parent
            if isinstance(enclosing, exp.Union) and node in (
                enclosing.this,
                enclosing.expression,
            ):
                enclosing = enclosing.parent
            declaration = (
                enclosing.args.get("alias") if isinstance(enclosing, exp.CTE) else None
            )
            declared = declaration.args.get("columns") if declaration else None
            if not name and declared and index < len(declared):
                name = "__cte_position_" + str(index)
                synthetic.add(name)
            if not name or name in expressions:
                _refuse(projected, "projection_alias", "invalid_request")
            _phase(value, aggregates=True, windows=True)
            type_ = self.expression(value, scope, ctes)
            if type_.array or type_.pg_type == "null":
                _refuse(projected, "projected_type", "invalid_request")
            expressions[name] = value
            columns.append(SqlColumn(name, type_))
        if not columns:
            _refuse(node, "projection", "invalid_request")
        group = node.args.get("group")
        group_expressions: list[exp.Expr] = []
        if group:
            for expression in group.expressions:
                if (
                    isinstance(expression, exp.Column)
                    and not expression.table
                    and expression.name in expressions
                ):
                    try:
                        scope.column(expression)
                    except SqlAdmissionError:
                        replacement = expressions[expression.name].copy()
                        expression.replace(replacement)
                        expression = replacement
                group_expressions.append(expression)
                _phase(expression, aggregates=False, windows=False)
                self.expression(expression, scope, ctes)
        having = node.args.get("having")
        if having:
            _phase(having.this, aggregates=True, windows=False)
            self.boolean(having.this, scope, ctes)
        if node.args.get("order"):
            for item in node.args["order"].expressions:
                _phase(item.this, aggregates=True, windows=True)
        self.order(
            node.args.get("order"),
            scope,
            {c.name: c.type for c in columns if c.name not in synthetic},
        )
        # Preserve visible uniqueness only when every member of a proven key is projected.
        keys: list[tuple[str, ...]] = []
        if node.args.get("distinct"):
            keys.append(tuple(c.name for c in columns))
        elif group:
            names = tuple(
                name
                for expression in group_expressions
                for name, value in expressions.items()
                if value == expression
            )
            if len(names) == len(group_expressions):
                keys.append(names)
        else:
            combined = []
            for alias, relation in scope.sources.items():
                key = relation.unique_keys[0] if relation.unique_keys else None
                if key is None:
                    break
                for field in key:
                    matching = [
                        n
                        for n, e in expressions.items()
                        if isinstance(e, exp.Column)
                        and e.name == field
                        and (
                            e.table == alias
                            or (not e.table and len(scope.sources) == 1)
                        )
                    ]
                    if not matching:
                        break
                    combined.append(matching[0])
                else:
                    continue
                break
            else:
                keys.append(tuple(combined))
        return SqlRelation(tuple(columns), tuple(keys))

    def conjuncts(self, node: exp.Expr | None) -> list[exp.Expr]:
        if isinstance(node, exp.Paren):
            return self.conjuncts(node.this)
        if isinstance(node, exp.And):
            return self.conjuncts(node.this) + self.conjuncts(node.expression)
        return [node] if node else []

    def parameter(self, node: exp.Parameter) -> BoundParameter:
        if not isinstance(node.this, exp.Literal):
            _refuse(node, "parameter", "invalid_request")
        position = _integer(node.this, 64)
        if position not in self.parameters:
            _refuse(node, "parameter", "invalid_request")
        self.used_parameters.add(position)
        return self.parameters[position]

    def boolean(
        self, node: exp.Expr, scope: _Scope, ctes: dict[str, SqlRelation]
    ) -> None:
        if self.expression(node, scope, ctes).pg_type != "bool":
            _refuse(node, "predicate_type", "invalid_request")

    def order(
        self, order: exp.Order | None, scope: _Scope, outputs: dict[str, SqlType]
    ) -> None:
        if order:
            for ordered in order.expressions:
                value = ordered.this
                if (
                    isinstance(value, exp.Column)
                    and not value.table
                    and value.name in outputs
                ):
                    continue
                self.expression(value, scope, {})

    def expression(
        self,
        node: exp.Expr,
        scope: _Scope,
        ctes: dict[str, SqlRelation],
        *,
        in_window: bool = False,
    ) -> SqlType:
        if isinstance(node, exp.Column):
            return scope.column(node)
        if isinstance(node, exp.Parameter):
            parameter = self.parameter(node)
            if parameter.type.array:
                _refuse(node, "array_surface")
            return parameter.type
        if isinstance(node, exp.Null):
            return NULL
        if isinstance(node, exp.Literal):
            if id(node) not in self.structural:
                _refuse(node, "unbound_literal", "invalid_request")
            return INT8
        if isinstance(node, exp.Paren):
            return self.expression(node.this, scope, ctes, in_window=in_window)
        if isinstance(node, exp.And | exp.Or):
            self.boolean(node.this, scope, ctes)
            self.boolean(node.expression, scope, ctes)
            return BOOL.optional()
        if isinstance(node, exp.Not):
            self.boolean(node.this, scope, ctes)
            return BOOL.optional()
        if isinstance(node, exp.Is):
            if not isinstance(node.expression, exp.Null):
                _refuse(node, "is_null")
            self.expression(node.this, scope, ctes)
            return BOOL
        if isinstance(node, exp.EQ | exp.NEQ | exp.LT | exp.LTE | exp.GT | exp.GTE):
            left = self.expression(node.this, scope, ctes)
            if isinstance(node.expression, exp.Any):
                if not isinstance(node, exp.EQ):
                    _refuse(node, "array_membership")
                value = node.expression.this
                if isinstance(value, exp.Paren):
                    value = value.this
                if not isinstance(value, exp.Cast) or not isinstance(
                    value.this, exp.Parameter
                ):
                    _refuse(node, "array_membership")
                parameter = self.parameter(value.this)
                target = value.args["to"]
                if (
                    target.this != exp.DataType.Type.ARRAY
                    or len(target.expressions) != 1
                ):
                    _refuse(value, "array_type", "invalid_request")
                physical = self.data_type(target.expressions[0])
                if not parameter.type.array or physical != parameter.type.pg_type:
                    _refuse(value, "array_type", "invalid_request")
                _same(left, replace(parameter.type, array=False), node)
            else:
                _same(left, self.expression(node.expression, scope, ctes), node)
            return BOOL.optional()
        if isinstance(node, exp.Like | exp.ILike):
            if (
                self.expression(node.this, scope, ctes).pg_type != "text"
                or self.expression(node.expression, scope, ctes).pg_type != "text"
            ):
                _refuse(node, "text_predicate", "invalid_request")
            return BOOL.optional()
        if isinstance(node, exp.In):
            type_ = self.expression(node.this, scope, ctes)
            if node.args.get("query"):
                query = self.query(node.args["query"], scope, ctes)
                if len(query.columns) != 1:
                    _refuse(node, "subquery_columns", "invalid_request")
                _same(type_, query.columns[0].type, node)
            else:
                if not node.expressions:
                    _refuse(node, "in_values", "invalid_request")
                for value in node.expressions:
                    if not isinstance(value, exp.Parameter):
                        _refuse(value, "in_values", "invalid_request")
                    _same(type_, self.expression(value, scope, ctes), node)
            return BOOL.optional()
        if isinstance(node, exp.Exists):
            self.query(node.this, scope, ctes)
            return BOOL
        if isinstance(node, exp.Subquery):
            subquery = self.query(node.this, scope, ctes)
            if len(subquery.columns) != 1:
                _refuse(node, "scalar_subquery", "invalid_request")
            return subquery.columns[0].type.optional()
        if isinstance(node, exp.Add | exp.Sub | exp.Mul | exp.Div):
            a, b = (
                self.expression(node.this, scope, ctes),
                self.expression(node.expression, scope, ctes),
            )
            if (
                a.pg_type not in NUMERIC
                or b.pg_type not in NUMERIC
                or a.reference_kind
                or b.reference_kind
            ):
                _refuse(node, "numeric_expression", "invalid_request")
            return SqlType(
                "numeric" if "numeric" in (a.pg_type, b.pg_type) else "int8",
                nullable=a.nullable or b.nullable,
            )
        if isinstance(node, exp.Neg):
            value = self.expression(node.this, scope, ctes)
            if value.pg_type not in NUMERIC or value.reference_kind:
                _refuse(node, "numeric_expression", "invalid_request")
            return value
        if isinstance(node, exp.Cast):
            value = self.expression(node.this, scope, ctes)
            physical = self.data_type(node.args["to"])
            if (
                value.reference_kind
                or value.pg_type not in NUMERIC
                or physical not in NUMERIC
            ):
                _refuse(node, "cast")
            return SqlType(physical, nullable=value.nullable)
        if isinstance(node, exp.Case):
            result = (
                self.expression(node.args["default"], scope, ctes)
                if node.args.get("default")
                else NULL
            )
            for branch in node.args.get("ifs", []):
                self.boolean(branch.this, scope, ctes)
                result = _same(
                    result, self.expression(branch.args["true"], scope, ctes), node
                )
            return result
        if isinstance(node, exp.Lower | exp.Upper):
            value = self.expression(node.this, scope, ctes)
            if value.pg_type != "text" or value.reference_kind:
                _refuse(node, "text_function", "invalid_request")
            return value
        if isinstance(node, exp.Coalesce):
            values = [node.this, *node.expressions]
            result = self.expression(values[0], scope, ctes)
            nullable = result.nullable
            for value in values[1:]:
                type_ = self.expression(value, scope, ctes)
                result = _same(result, type_, node)
                nullable = nullable and type_.nullable
            return replace(result, nullable=nullable)
        if isinstance(node, exp.Nullif):
            return _same(
                self.expression(node.this, scope, ctes),
                self.expression(node.expression, scope, ctes),
                node,
            ).optional()
        if isinstance(node, exp.TimestampTrunc):
            unit, zone = node.args.get("unit"), node.args.get("zone")
            if (
                not isinstance(unit, exp.Var)
                or unit.name.lower() not in {"day", "month", "year"}
                or not isinstance(zone, exp.Literal)
                or not zone.is_string
                or zone.this != "UTC"
            ):
                _refuse(node, "date_trunc")
            value = self.expression(node.this, scope, ctes)
            if value.pg_type != "timestamptz":
                _refuse(node, "date_trunc_type", "invalid_request")
            return value
        if isinstance(node, exp.Filter):
            if not isinstance(
                node.this, exp.Count | exp.Sum | exp.Avg | exp.Min | exp.Max
            ):
                _refuse(node, "aggregate_filter")
            self.boolean(node.expression.this, scope, ctes)
            return self.expression(node.this, scope, ctes, in_window=in_window)
        if isinstance(node, exp.Count | exp.Sum | exp.Avg | exp.Min | exp.Max):
            value = node.this
            if isinstance(node, exp.Count) and isinstance(value, exp.Star):
                return INT8
            if isinstance(value, exp.Distinct):
                if not isinstance(node, exp.Count) or len(value.expressions) != 1:
                    _refuse(node, "aggregate_distinct")
                value = value.expressions[0]
            type_ = self.expression(value, scope, ctes)
            if isinstance(node, exp.Count):
                if node.expressions:
                    _refuse(node, "count_arity")
                return INT8
            if isinstance(node, exp.Sum | exp.Avg):
                if type_.pg_type not in NUMERIC or type_.reference_kind:
                    _refuse(node, "aggregate_type", "invalid_request")
                return SqlType("numeric", nullable=True)
            node.meta["input_pg_type"] = type_.pg_type
            return type_.optional()
        if isinstance(node, exp.Window):
            function = (
                node.this.this if isinstance(node.this, exp.Filter) else node.this
            )
            if not isinstance(
                function,
                exp.Count
                | exp.Sum
                | exp.Avg
                | exp.Min
                | exp.Max
                | exp.RowNumber
                | exp.Rank
                | exp.DenseRank
                | exp.Lag
                | exp.Lead,
            ):
                _refuse(node, "window_function")
            partitions = node.args.get("partition_by") or []
            for value in partitions:
                self.expression(value, scope, ctes)
            self.order(node.args.get("order"), scope, {})
            ordered = (
                [e.this for e in node.args["order"].expressions]
                if node.args.get("order")
                else []
            )
            if isinstance(
                function, exp.RowNumber | exp.Lag | exp.Lead
            ) and not scope.total_key([*partitions, *ordered]):
                _refuse(node, "total_visible_order", "invalid_request")
            spec = node.args.get("spec")
            if isinstance(function, exp.Count | exp.Sum | exp.Avg | exp.Min | exp.Max):
                if not isinstance(spec, exp.WindowSpec):
                    _refuse(node, "explicit_rows_frame", "invalid_request")
                self.window_frame(spec)
            elif spec is not None:
                _refuse(spec, "ranking_frame")
            return self.expression(node.this, scope, ctes, in_window=True)
        if isinstance(node, exp.RowNumber | exp.Rank | exp.DenseRank):
            if not in_window or node.expressions:
                _refuse(node, "ranking_window")
            return INT8
        if isinstance(node, exp.Lag | exp.Lead):
            if not in_window:
                _refuse(node, "neighbor_window")
            _integer(node.args.get("offset"), 64)
            value = self.expression(node.this, scope, ctes)
            if node.args.get("default"):
                value = _same(
                    value, self.expression(node.args["default"], scope, ctes), node
                )
            return value.optional()
        _refuse(node, type(node).__name__)

    def data_type(self, node: exp.DataType) -> str:
        names = {
            exp.DataType.Type.BIGINT: "int8",
            exp.DataType.Type.DECIMAL: "numeric",
            exp.DataType.Type.UUID: "uuid",
            exp.DataType.Type.TEXT: "text",
            exp.DataType.Type.BOOLEAN: "bool",
            exp.DataType.Type.DOUBLE: "float8",
            exp.DataType.Type.TIMESTAMPTZ: "timestamptz",
        }
        if node.this not in names or node.expressions:
            _refuse(node, "sql_type")
        return names[node.this]

    def window_frame(self, spec: exp.WindowSpec) -> None:
        if spec.args.get("kind") != "ROWS":
            _refuse(spec, "window_frame")
        for side in ("start", "end"):
            value = spec.args.get(side)
            if isinstance(value, exp.Expr):
                _integer(value, 4096)
            elif value not in ("UNBOUNDED", "CURRENT ROW", None):
                _refuse(spec, "window_frame")
            if spec.args.get(side + "_side") not in ("PRECEDING", "FOLLOWING", None):
                _refuse(spec, "window_frame")

    def recursive(
        self, cte: exp.CTE, with_: exp.With, ctes: dict[str, SqlRelation]
    ) -> SqlRelation:
        request = self.recursion
        body = cte.this
        cycle = with_.args.get("search")
        if (
            not request
            or request.cte != cte.alias
            or type(request.max_depth) is not int
            or not 1 <= request.max_depth <= 8
        ):
            _refuse(cte, "recursion_bound", "invalid_request")
        if (
            not isinstance(body, exp.Union)
            or body.args.get("distinct") is not False
            or not isinstance(body.this, exp.Select)
            or not isinstance(body.expression, exp.Select)
        ):
            _refuse(cte, "recursive_union")
        if (
            not isinstance(cycle, exp.RecursiveWithSearch)
            or cycle.args.get("kind") != "CYCLE"
            or cycle.this.name != request.node_column
        ):
            _refuse(cte, "native_cycle", "invalid_request")
        names = [i.name for i in cte.args["alias"].args.get("columns", [])]
        if (
            request.node_column not in names
            or request.depth_column not in names
            or len(names) != len(body.this.expressions)
        ):
            _refuse(cte, "recursive_columns", "invalid_request")
        depth_index = names.index(request.depth_column)
        node_index = names.index(request.node_column)
        seed, arm = body.this, body.expression
        zero = seed.expressions[depth_index]
        if isinstance(zero, exp.Alias):
            zero = zero.this
        if _integer(zero, 0) != 0:
            _refuse(zero, "recursive_seed")
        self.structural.add(id(zero))
        relation = self.rename(self.query(seed, None, ctes), cte.args["alias"])
        if relation.columns[node_index].type.reference_kind not in {
            "bead_ref",
            "bead_version_ref",
        }:
            _refuse(seed, "recursive_anchor", "invalid_request")
        node_expression = seed.expressions[node_index]
        if isinstance(node_expression, exp.Alias):
            node_expression = node_expression.this
        anchors = []
        if isinstance(node_expression, exp.Parameter):
            self.parameter(node_expression)
            anchors.append(1)
        elif isinstance(node_expression, exp.Column):
            seed_where = seed.args.get("where")
            for predicate in self.conjuncts(seed_where.this if seed_where else None):
                if not isinstance(predicate, exp.EQ):
                    continue
                for column, value in [
                    (predicate.this, predicate.expression),
                    (predicate.expression, predicate.this),
                ]:
                    if column != node_expression:
                        continue
                    parameter = value
                    if isinstance(value, exp.Any):
                        parameter = value.this
                        if isinstance(parameter, exp.Paren):
                            parameter = parameter.this
                        if isinstance(parameter, exp.Cast):
                            parameter = parameter.this
                    if isinstance(parameter, exp.Parameter):
                        bound_anchor = self.parameter(parameter)
                        if (
                            bound_anchor.type.reference_kind
                            == relation.columns[node_index].type.reference_kind
                        ):
                            anchors.append(
                                len(bound_anchor.value)
                                if bound_anchor.type.array
                                else 1
                            )
        if not anchors or sum(anchors) > 16:
            _refuse(seed, "recursive_anchors", "invalid_request")
        references = [
            t for t in arm.find_all(exp.Table) if not t.db and t.name == request.cte
        ]
        if len(references) != 1:
            _refuse(arm, "recursive_self_reference")
        alias = references[0].alias_or_name
        step = arm.expressions[depth_index]
        if isinstance(step, exp.Alias):
            step = step.this
        if (
            not isinstance(step, exp.Add)
            or not isinstance(step.this, exp.Column)
            or step.this.table != alias
            or step.this.name != request.depth_column
            or _integer(step.expression, 1) != 1
        ):
            _refuse(step, "recursive_increment")
        self.structural.add(id(step.expression))
        guard = None
        arm_where = arm.args.get("where")
        for predicate in self.conjuncts(arm_where.this if arm_where else None):
            if (
                isinstance(predicate, exp.LT)
                and isinstance(predicate.this, exp.Column)
                and predicate.this.table == alias
                and predicate.this.name == request.depth_column
                and _integer(predicate.expression, 8) == request.max_depth
            ):
                guard = predicate
                self.structural.add(id(predicate.expression))
        if guard is None:
            _refuse(arm, "recursive_depth_guard", "invalid_request")
        edges = [t for t in arm.find_all(exp.Table) if t.db == "memory_v1"]
        if len(edges) != 1 or edges[0].name not in {
            "assessed_relations",
            "corrections",
        }:
            _refuse(arm, "recursive_edge")
        edge = edges[0]
        node_output = arm.expressions[node_index]
        if isinstance(node_output, exp.Alias):
            node_output = node_output.this
        pairs = (
            {
                "source_bead_id": "target_bead_id",
                "target_bead_id": "source_bead_id",
                "source_bead_version_id": "target_bead_version_id",
                "target_bead_version_id": "source_bead_version_id",
            }
            if edge.name == "assessed_relations"
            else {
                "predecessor_bead_id": "successor_bead_id",
                "successor_bead_id": "predecessor_bead_id",
                "predecessor_version_id": "successor_version_id",
                "successor_version_id": "predecessor_version_id",
            }
        )
        if (
            not isinstance(node_output, exp.Column)
            or node_output.table != edge.alias_or_name
            or node_output.name not in pairs
        ):
            _refuse(arm, "recursive_endpoint")
        linked = False
        for join in arm.args.get("joins") or []:
            for predicate in self.conjuncts(join.args.get("on")):
                if isinstance(predicate, exp.EQ):
                    for left, right in [
                        (predicate.this, predicate.expression),
                        (predicate.expression, predicate.this),
                    ]:
                        if (
                            isinstance(left, exp.Column)
                            and isinstance(right, exp.Column)
                            and left.table == alias
                            and left.name == request.node_column
                            and right.table == edge.alias_or_name
                            and right.name == pairs[node_output.name]
                        ):
                            linked = True
        if not linked:
            _refuse(arm, "recursive_endpoint_join")
        local = dict(ctes)
        local[request.cte] = replace(relation, unique_keys=())
        output = self.rename(self.query(arm, None, local), cte.args["alias"])
        for a, b in zip(relation.columns, output.columns, strict=True):
            _same(a.type, b.type, arm)
        # The independently restricted reader grants bigint comparison, not
        # PostgreSQL's implicit bigint/integer operator selected by a literal.
        # Apply casts only after validating the original admitted expression.
        guard.set(
            "expression",
            exp.Cast(
                this=guard.expression.copy(), to=exp.DataType.build("BIGINT")
            ),
        )
        step.set(
            "expression",
            exp.Cast(
                this=step.expression.copy(), to=exp.DataType.build("BIGINT")
            ),
        )
        # Independent guard is emitted even after structurally verifying the user guard.
        predicates: list[exp.Expr] = [
            exp.LT(
                this=exp.column(request.depth_column, table=alias),
                expression=exp.Cast(
                    this=exp.Literal.number(request.max_depth),
                    to=exp.DataType.build("BIGINT"),
                ),
            )
        ]
        for edge in edges:
            edge_alias = edge.alias_or_name
            if edge.name == "assessed_relations":
                predicates.extend(
                    [
                        exp.EQ(
                            this=exp.column("support_eligible", table=edge_alias),
                            expression=exp.Boolean(this=True),
                        ),
                        exp.EQ(
                            this=exp.column("roots_status", table=edge_alias),
                            expression=exp.Literal.string("qualified"),
                        ),
                    ]
                )
            else:
                predicates.append(
                    exp.Anonymous(
                        this="memoriesql_query_private.correction_path_qualified",
                        expressions=[
                            exp.column("predecessor_version_id", table=edge_alias),
                            exp.column("successor_version_id", table=edge_alias),
                        ],
                    )
                )
        for predicate in predicates:
            arm.where(predicate, append=True, copy=False)
        # Seed is bigint so native recursive output matches the declared wire type.
        zero.replace(exp.Cast(this=zero.copy(), to=exp.DataType.build("BIGINT")))
        self.recursion_seen = True
        cycle_flag = cycle.expression.name
        path = cycle.args["using"].name
        if cycle_flag in names or path in names or cycle_flag == path:
            _refuse(cycle, "cycle_columns", "invalid_request")
        return SqlRelation((*relation.columns, SqlColumn(cycle_flag, BOOL)), ())


def _phase(node: exp.Expr, *, aggregates: bool, windows: bool) -> None:
    """Check one query level; subqueries receive their own phase checks."""
    if isinstance(node, exp.Subquery | exp.Select | exp.SetOperation):
        return
    if isinstance(node, exp.Window):
        if not windows:
            _refuse(node, "window_phase", "invalid_request")
        function = node.this
        if isinstance(function, exp.Filter):
            _phase(function.expression, aggregates=False, windows=False)
            function = function.this
        if isinstance(function, exp.Count | exp.Sum | exp.Avg | exp.Min | exp.Max):
            # Ordinary group aggregates may feed a later window aggregate;
            # e.g. SUM(COUNT(*)) OVER is valid and useful PostgreSQL composition.
            for window_argument in function.iter_expressions():
                _phase(window_argument, aggregates=True, windows=False)
        else:
            _phase(function, aggregates=True, windows=False)
        for key in ("partition_by", "order", "spec"):
            value = node.args.get(key)
            for item in value if isinstance(value, list) else [value] if value else []:
                _phase(item, aggregates=True, windows=False)
        return
    if isinstance(node, exp.Filter):
        _phase(node.this, aggregates=aggregates, windows=windows)
        _phase(node.expression, aggregates=False, windows=False)
        return
    if isinstance(node, exp.Count | exp.Sum | exp.Avg | exp.Min | exp.Max):
        if not aggregates:
            _refuse(node, "aggregate_phase", "invalid_request")
        for aggregate_argument in node.iter_expressions():
            _phase(aggregate_argument, aggregates=False, windows=False)
        return
    for child in node.iter_expressions():
        _phase(child, aggregates=aggregates, windows=windows)


def _lower(tree: exp.Expr, parameters: dict[int, BoundParameter]) -> dict[str, Any]:
    values = {}
    for parameter in list(tree.find_all(exp.Parameter)):
        position = int(parameter.this.this)
        bound = parameters[position]
        name = "p" + str(position)
        values[name] = bound.value
        dtype = bound.type.pg_type + ("[]" if bound.type.array else "")
        lowered: exp.Expr = exp.Cast(
            this=exp.Placeholder(this=name),
            to=exp.DataType.build(dtype, dialect="postgres"),
        )
        if bound.type.pg_type == "text":
            lowered = exp.Collate(
                this=lowered,
                expression=exp.column("C", table="pg_catalog", quoted=True),
            )
        parameter.replace(lowered)
    for aggregate in list(tree.find_all(exp.Min, exp.Max)):
        physical = aggregate.meta.get("input_pg_type")
        if physical == "bool":
            aggregate.replace(
                (exp.LogicalAnd if isinstance(aggregate, exp.Min) else exp.LogicalOr)(
                    this=aggregate.this.copy()
                )
            )
        elif physical == "uuid":
            aggregate.set(
                "this",
                exp.Collate(
                    this=exp.Cast(
                        this=aggregate.this.copy(), to=exp.DataType.build("TEXT")
                    ),
                    expression=exp.column("C", table="pg_catalog", quoted=True),
                ),
            )
            enclosing: exp.Expr = aggregate
            if isinstance(enclosing.parent, exp.Filter):
                enclosing = enclosing.parent
            if isinstance(enclosing.parent, exp.Window):
                enclosing = enclosing.parent
            enclosing.replace(
                exp.Cast(this=enclosing.copy(), to=exp.DataType.build("UUID"))
            )
    return values


def admit_query(
    sql: str,
    parameters: tuple[SqlParameter, ...] = (),
    *,
    inputs: dict[str, SqlRelation] | None = None,
    admitted_anchors: frozenset[tuple[str, str]] = frozenset(),
    evaluation_admitted: bool = False,
    recursion: RecursionBound | None = None,
    catalog: SqlCatalog | None = None,
) -> AdmittedQuery:
    """The caller is the trusted host; agent wire requests never set admission context."""
    if version("sqlglot") != PARSER_VERSION:
        raise SqlAdmissionError("unavailable", "parser_version")
    try:
        distribution("sqlglotc")
    except PackageNotFoundError:
        pass
    else:
        raise SqlAdmissionError("unavailable", "parser_native_overlay")
    if find_spec("sqlglotrs") or not (tokenizer_core.__file__ or "").endswith(".py"):
        raise SqlAdmissionError("unavailable", "parser_native_overlay")
    try:
        length = len(sql.encode("utf-8"))
    except UnicodeError:
        raise SqlAdmissionError("invalid_request", "sql_encoding") from None
    if not length or length > 32768:
        raise SqlAdmissionError("invalid_request", "sql_bytes")
    try:
        statements = _ClosedPostgres().parse(
            sql, error_level=ErrorLevel.RAISE, max_nodes=4096
        )
    except (SqlglotError, RecursionError):
        raise SqlAdmissionError("invalid_request", "syntax") from None
    if len(statements) != 1 or statements[0] is None:
        raise SqlAdmissionError("unsupported", "statement_count")
    tree = statements[0]
    _closed_tree(tree)
    for identifier in tree.find_all(exp.Identifier):
        if not identifier.args.get("quoted"):
            identifier.set("this", identifier.this.lower())
        if (
            not identifier.this
            or len(identifier.this.encode()) > 63
            or "\x00" in identifier.this
        ):
            _refuse(identifier, "identifier", "invalid_request")
    selected = catalog or SqlCatalog.installed()
    bound = selected.parameters(parameters, admitted_anchors=admitted_anchors)
    saved = inputs or {}
    names = {name.rsplit(".", 1)[1] for name in selected.relations}
    ctes = {c.alias for c in tree.find_all(exp.CTE)}
    if len(saved) > 8 or any(
        not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", name) or name in names | ctes
        for name in saved
    ):
        raise SqlAdmissionError("invalid_request", "input_alias")
    binder = _Binder(selected, bound, saved, evaluation_admitted, recursion)
    output = binder.query(tree, None, {})
    if binder.used_parameters != set(bound):
        raise SqlAdmissionError("invalid_request", "unused_parameter")
    if bool(recursion) != binder.recursion_seen:
        raise SqlAdmissionError("invalid_request", "recursion_declaration")
    derivation = {
        "version": 1,
        "tree": tree.dump(),
        "relations": sorted(binder.used_relations),
        "parameters": {
            str(k): {
                "pg_type": v.type.pg_type,
                "reference_kind": v.type.reference_kind,
                "array": v.type.array,
            }
            for k, v in sorted(bound.items())
        },
        "coverage": {"explicit_depth": recursion.max_depth} if recursion else {},
    }
    values = _lower(tree, bound)
    try:
        emitted = tree.sql(
            dialect=_ClosedPostgres(),
            comments=False,
            unsupported_level=ErrorLevel.RAISE,
        )
    except UnsupportedError:
        raise SqlAdmissionError("unsupported", "emission") from None
    return AdmittedQuery(
        emitted,
        values,
        output.columns,
        selected.hash,
        tuple(sorted(binder.used_relations)),
        derivation,
        recursion,
        tree.dump(),
    )
