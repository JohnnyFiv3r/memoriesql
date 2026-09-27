"""Trusted native bag-witness compiler; no agent capability or SQL fallback.

This first qualification cut handles select/project/filter, key/outer joins,
nonrecursive CTEs/derived tables and UNION ALL. Other admitted shapes remain
private native SELECTs but cannot be committed through this witness compiler.
No Python SQL evaluator is used: PostgreSQL evaluates values and membership in
one statement over the same frozen population, under the original work bound.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlglot import ErrorLevel, exp

from memoriesql.application.agent_sql_admission import AdmittedQuery, _ClosedPostgres
from memoriesql.application.agent_sql_catalog import SqlAdmissionError, SqlRelation


@dataclass(frozen=True, slots=True)
class WitnessPlan:
    sql: str
    stages: tuple[dict[str, Any], ...]
    hidden_column: str


def _array(*items: exp.Expr) -> exp.Expr:
    return exp.Anonymous(this="pg_catalog.jsonb_build_array", expressions=list(items))


class _Compiler:
    def __init__(self, tree: exp.Expr, relations: dict[str, SqlRelation]) -> None:
        names = {i.name for i in tree.find_all(exp.Identifier)}
        names.update(c.name for r in relations.values() for c in r.columns)
        ordinal = 0
        self.hidden = "_mq_native_witness"
        while self.hidden in names:
            ordinal += 1
            self.hidden = "_mq_native_witness_" + str(ordinal)
        self.relations = relations
        self.stages: list[dict[str, Any]] = []

    def stage(self, operation: str, **attributes: Any) -> int:
        ordinal = len(self.stages)
        self.stages.append({"stage": ordinal, "operation": operation, **attributes})
        return ordinal

    def tag(self, stage: int, *parents: exp.Expr) -> exp.Expr:
        return _array(exp.Literal.number(stage), *parents)

    def source(self, node: exp.Expr, ctes: set[str]) -> tuple[exp.Expr, exp.Expr]:
        alias = node.alias_or_name
        if isinstance(node, exp.Subquery):
            node.set("this", self.query(node.this, ctes))
            self.alias_columns(node)
        elif isinstance(node, exp.Table):
            if node.db:
                name = node.db + "." + node.name
                schema = self.relations[name]
                keys = schema.unique_keys[0]
                stage = self.stage("scan", relation=name, key_columns=list(keys))
                scan = exp.select(
                    *(
                        exp.column(c.name, table=alias, quoted=True)
                        for c in schema.columns
                    ),
                    exp.alias_(
                        self.tag(
                            stage,
                            _array(
                                *(exp.column(k, table=alias, quoted=True) for k in keys)
                            ),
                        ),
                        self.hidden,
                        quoted=True,
                    ),
                ).from_(node.copy())
                node = exp.Subquery(
                    this=scan,
                    alias=exp.TableAlias(this=exp.to_identifier(alias, quoted=True)),
                )
            elif node.name not in ctes:
                raise SqlAdmissionError("unsupported", "witness_source")
        else:
            raise SqlAdmissionError("unsupported", "witness_source")
        return node, exp.column(self.hidden, table=alias, quoted=True)

    def alias_columns(self, node: exp.Expr) -> None:
        alias = node.args.get("alias")
        if alias and alias.args.get("columns"):
            alias.append("columns", exp.to_identifier(self.hidden, quoted=True))

    def query(self, node: exp.Expr, inherited: set[str]) -> exp.Expr:
        if isinstance(node, exp.Subquery):
            node.set("this", self.query(node.this, inherited))
            return node
        ctes = set(inherited)
        with_node = node.args.get("with_")
        if with_node:
            for cte in with_node.expressions:
                cte.set("this", self.query(cte.this, ctes))
                self.alias_columns(cte)
                ctes.add(cte.alias)
        if isinstance(node, exp.Union):
            # Tag each branch separately. UNION ALL preserves bag multiplicity;
            # branch identity must survive even when visible values are equal.
            for key in ("this", "expression"):
                arm = self.query(node.args[key], ctes)
                output = arm.unnest()
                if not isinstance(output, exp.Select | exp.Union):
                    raise SqlAdmissionError("unsupported", "witness_shape")
                names = [e.alias_or_name for e in output.selects[:-1]]
                alias = "_mq_branch"
                stage = self.stage("set", kind="union_all", branch=key)
                wrapper = exp.select(
                    *(exp.column(n, table=alias, quoted=True) for n in names),
                    exp.alias_(
                        self.tag(
                            stage, exp.column(self.hidden, table=alias, quoted=True)
                        ),
                        self.hidden,
                        quoted=True,
                    ),
                ).from_(
                    exp.Subquery(
                        this=arm, alias=exp.TableAlias(this=exp.to_identifier(alias))
                    )
                )
                node.set(key, wrapper)
            self.order(node)
            if node.args.get("limit"):
                stage = self.stage("limit", limit=node.args["limit"].dump())
                alias = "_mq_set"
                names = [e.alias_or_name for e in node.selects[:-1]]
                wrapper = exp.select(
                    *(exp.column(n, table=alias, quoted=True) for n in names),
                    exp.alias_(
                        self.tag(
                            stage, exp.column(self.hidden, table=alias, quoted=True)
                        ),
                        self.hidden,
                        quoted=True,
                    ),
                ).from_(
                    exp.Subquery(
                        this=node, alias=exp.TableAlias(this=exp.to_identifier(alias))
                    )
                )
                wrapper.set("order", node.args["order"].copy())
                return wrapper
            return node
        if not isinstance(node, exp.Select):
            raise SqlAdmissionError("unsupported", "witness_shape")
        parents: list[exp.Expr] = []
        source = node.args.get("from_")
        if source:
            rewritten, trace = self.source(source.this, ctes)
            source.set("this", rewritten)
            parents.append(trace)
        # Schema-qualified references now target derived aliases rather than
        # the original physical-looking logical name; never rewrite predicates.
        for column in node.find_all(exp.Column):
            if column.db == "memory_v1":
                column.set("db", None)
        trace = self.tag(
            self.stage("project", phase="source", arity=len(parents)), *parents
        )
        for join in node.args.get("joins") or ():
            rewritten, right = self.source(join.this, ctes)
            join.set("this", rewritten)
            trace = self.tag(
                self.stage(
                    "join", side=join.side or "inner", predicate=join.args["on"].dump()
                ),
                trace,
                right,
            )
        where = node.args.get("where")
        if where:
            trace = self.tag(self.stage("filter", predicate=where.this.dump()), trace)
        trace = self.tag(
            self.stage("project", expressions=[e.dump() for e in node.expressions]),
            trace,
        )
        if node.args.get("limit"):
            trace = self.tag(
                self.stage("limit", limit=node.args["limit"].dump()), trace
            )
        node.append("expressions", exp.alias_(trace, self.hidden, quoted=True))
        self.order(node)
        return node

    def order(self, node: exp.Expr) -> None:
        tie = exp.Ordered(this=exp.column(self.hidden, quoted=True), nulls_first=False)
        order = node.args.get("order")
        if order:
            order.append("expressions", tie)
        else:
            node.set("order", exp.Order(expressions=[tie]))


def compile_bag_witness(
    query: AdmittedQuery, relations: dict[str, SqlRelation]
) -> WitnessPlan:
    """Compile only an already admitted, lowered tree; refuse pending shapes."""
    tree = exp.Expr.load(query.execution_tree)
    if query.recursion or any(
        isinstance(
            n,
            exp.AggFunc
            | exp.Window
            | exp.Distinct
            | exp.Group
            | exp.Having
            | exp.Exists
            | exp.Intersect
            | exp.Except,
        )
        or (isinstance(n, exp.Union) and n.args.get("distinct") is not False)
        or (
            isinstance(n, exp.Subquery)
            and not isinstance(
                n.parent, exp.From | exp.Join | exp.CTE | exp.Subquery | exp.Union
            )
        )
        for n in tree.walk()
    ):
        raise SqlAdmissionError("unsupported", "witness_qualification_pending")
    compiler = _Compiler(tree, relations)
    rewritten = compiler.query(tree, set())
    return WitnessPlan(
        rewritten.sql(
            dialect=_ClosedPostgres(),
            comments=False,
            unsupported_level=ErrorLevel.RAISE,
        ),
        tuple(compiler.stages),
        compiler.hidden,
    )
