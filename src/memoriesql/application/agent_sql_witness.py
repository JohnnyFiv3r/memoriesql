"""Trusted native bag-witness compiler; no agent capability or SQL fallback.

This qualification cut handles native bags, groups, collapsed values, sets and
qualified windows. Correlated subqueries and recursion remain private native
SELECTs but cannot be committed through this witness compiler.
No Python SQL evaluator is used: PostgreSQL evaluates values and membership in
one statement over the same frozen population, under the original work bound.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypeGuard

from sqlglot import ErrorLevel, exp

from memoriesql.application.agent_sql_admission import AdmittedQuery, _ClosedPostgres
from memoriesql.application.agent_sql_catalog import SqlAdmissionError, SqlRelation


@dataclass(frozen=True, slots=True)
class WitnessPlan:
    sql: str
    stages: tuple[dict[str, Any], ...]
    hidden_column: str
    ledger_rows: bool = False
    semantic_check: bool = False


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
        self.names = names | {self.hidden}
        self.ctes: list[exp.CTE] = []
        self.outputs: dict[str, list[str]] = {}
        self.ledgers: list[str] = []
        self.sequence = 0
        self.ledger_owners: dict[str, str] = {}
        self.ledger_conditions: dict[str, exp.Expr] = {}

    def fresh(self, stem: str) -> str:
        name = "_mq_" + stem
        while name in self.names:
            self.sequence += 1
            name = "_mq_" + stem + "_" + str(self.sequence)
        self.names.add(name)
        return name

    def columns(self, node: exp.Expr) -> list[str]:
        output = node.unnest()
        if not isinstance(output, exp.Select | exp.SetOperation):
            raise SqlAdmissionError("unsupported", "witness_shape")
        return [e.alias_or_name for e in output.selects]

    def store(self, node: exp.Expr, stem: str, *, ledger: bool = False) -> str:
        name = self.fresh(stem)
        self.ctes.append(
            exp.CTE(
                this=node,
                alias=exp.TableAlias(this=exp.to_identifier(name)),
                materialized=True,
            )
        )
        self.outputs[name] = self.columns(node)
        if ledger:
            self.ledgers.append(name)
        return name

    def name_outputs(self, node: exp.Expr) -> None:
        node = node.unnest()
        if isinstance(node, exp.SetOperation):
            self.name_outputs(node.this)
            self.name_outputs(node.expression)
        elif isinstance(node, exp.Select):
            for expression in list(node.expressions):
                if not expression.alias_or_name:
                    expression.replace(
                        exp.alias_(
                            expression.copy(), self.fresh("expression"), quoted=True
                        )
                    )

    def stage(self, operation: str, **attributes: Any) -> int:
        ordinal = len(self.stages)
        self.stages.append({"stage": ordinal, "operation": operation, **attributes})
        return ordinal

    def tag(self, stage: int, *parents: exp.Expr) -> exp.Expr:
        tag = _array(exp.Literal.number(stage), *parents)
        tag.meta["witness_stage"] = stage
        return tag

    def source(self, node: exp.Expr, ctes: dict[str, str]) -> tuple[exp.Expr, exp.Expr]:
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
                inner = node.copy()
                outer_alias = node.args.get("alias")
                if outer_alias:
                    outer_alias = outer_alias.copy()
                    inner.args["alias"].set("columns", None)
                else:
                    outer_alias = exp.TableAlias(
                        this=exp.to_identifier(alias, quoted=True)
                    )
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
                ).from_(inner)
                node = exp.Subquery(
                    this=scan,
                    alias=outer_alias,
                )
                self.alias_columns(node, [c.name for c in schema.columns])
            elif node.name in ctes:
                node.set("this", exp.to_identifier(ctes[node.name], quoted=True))
                if not node.args.get("alias"):
                    node.set(
                        "alias",
                        exp.TableAlias(this=exp.to_identifier(alias, quoted=True)),
                    )
            else:
                raise SqlAdmissionError("unsupported", "witness_source")
        else:
            raise SqlAdmissionError("unsupported", "witness_source")
        return node, exp.column(self.hidden, table=alias, quoted=True)

    def alias_columns(
        self, node: exp.Expr, visible_names: list[str] | None = None
    ) -> None:
        alias = node.args.get("alias")
        if alias and alias.args.get("columns"):
            if visible_names is None:
                output = node.this.unnest()
                if not isinstance(output, exp.Select | exp.SetOperation):
                    raise SqlAdmissionError("unsupported", "witness_shape")
                visible_names = [e.alias_or_name for e in output.selects[:-1]]
            supplied = alias.args["columns"]
            alias.set(
                "columns",
                [
                    *supplied,
                    *(
                        exp.to_identifier(n, quoted=True)
                        for n in visible_names[len(supplied) :]
                    ),
                    exp.to_identifier(self.hidden, quoted=True),
                ],
            )

    def subset(self, node: exp.Expr) -> int:
        return self.stage(
            "limit",
            limit=node.args["limit"].dump() if node.args.get("limit") else None,
            offset=node.args["offset"].dump() if node.args.get("offset") else None,
            order=node.meta.get(
                "original_order",
                node.args["order"].dump() if node.args.get("order") else None,
            ),
            tie_basis="native_trace_v1",
        )

    def query(self, node: exp.Expr, inherited: dict[str, str]) -> exp.Expr:
        if isinstance(node, exp.Subquery):
            node.set("this", self.query(node.this, inherited))
            return node
        ctes = dict(inherited)
        with_node = node.args.get("with_")
        if with_node:
            for cte in with_node.expressions:
                self.name_outputs(cte.this)
                cte.set("this", self.query(cte.this, ctes))
                self.alias_columns(cte)
                original = cte.alias
                name = self.fresh("cte")
                cte.args["alias"].set("this", exp.to_identifier(name))
                cte.set("materialized", True)
                self.ctes.append(cte)
                self.outputs[name] = [
                    e.name for e in cte.args["alias"].args.get("columns") or []
                ] or self.columns(cte.this)
                ctes[original] = name
            node.set("with_", None)
        if isinstance(node, exp.Intersect | exp.Except) or (
            isinstance(node, exp.Union) and node.args.get("distinct") is not False
        ):
            return self.set_query(node, ctes)
        if isinstance(node, exp.Union):
            # Tag each branch separately. UNION ALL preserves bag multiplicity;
            # branch identity must survive even when visible values are equal.
            for key in ("this", "expression"):
                arm = self.query(node.args[key], ctes)
                output = arm.unnest()
                if not isinstance(output, exp.Select | exp.SetOperation):
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
            if node.args.get("limit") or node.args.get("offset"):
                stage = self.subset(node)
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
        window_roots = [*node.expressions]
        if node.args.get("order"):
            window_roots.extend(o.this for o in node.args["order"].expressions)
        if any(isinstance(n, exp.Window) for e in window_roots for n in e.walk()):
            return self.window_query(node, trace)
        # The already admitted AST's ordinary aggregates are evaluated natively.
        # Inputs and all tested groups are materialized within this same SELECT.
        roots = [*node.expressions]
        if node.args.get("having"):
            roots.append(node.args["having"].this)
        if node.args.get("order"):
            roots.extend(o.this for o in node.args["order"].expressions)
        grouped = bool(node.args.get("group") or node.args.get("having")) or any(
            isinstance(n, exp.AggFunc) for r in roots for n in r.walk()
        )
        original_projections = [e.copy() for e in node.expressions]
        original_order = node.args.get("order")
        distinct = bool(node.args.get("distinct"))
        if grouped:
            node = self.group_query(node, trace)
            trace = exp.column(self.hidden, quoted=True)
        node.set("distinct", None)
        trace = self.tag(
            self.stage("project", expressions=[e.dump() for e in node.expressions]),
            trace,
        )
        node.append("expressions", exp.alias_(trace, self.hidden, quoted=True))
        if distinct:
            node = self.distinct_query(node, original_projections, original_order)
        if node.args.get("limit") or node.args.get("offset"):
            node.expressions[-1].set(
                "this", self.tag(self.subset(node), node.expressions[-1].this)
            )
        self.order(node)
        return node

    def window_query(self, node: exp.Select, trace: exp.Expr) -> exp.Select:
        """Bind native window values to shared ordered partitions and ROWS spans."""
        projections = [e.copy() for e in node.expressions]
        original_order = node.args.get("order")
        output_names = [e.alias_or_name for e in projections]
        projected = {
            e.alias_or_name: e.this if isinstance(e, exp.Alias) else e
            for e in projections
        }

        def ordinary_aggregate(value: exp.Expr) -> bool:
            if not isinstance(value, exp.AggFunc):
                return False
            parent = value.parent
            while parent is not None and not isinstance(parent, exp.Window):
                parent = parent.parent
            if parent is None:
                return True
            function = parent.this.this if isinstance(parent.this, exp.Filter) else parent.this
            return value is not function

        roots_for_phase = [*projections]
        if original_order:
            roots_for_phase.extend(o.this for o in original_order.expressions)
        if node.args.get("group") or node.args.get("having") or any(
            ordinary_aggregate(n)
            for root in roots_for_phase
            for n in root.walk()
        ):
            return self.group_window_query(node, trace)

        sort_expressions: list[tuple[str, exp.Expr]] = []
        ordered: list[exp.Ordered] = []
        if original_order:
            for item in original_order.expressions:
                value = item.this
                if isinstance(value, exp.Identifier):
                    value = exp.column(value.name, quoted=bool(value.args.get("quoted")))
                name: str | None = None
                if isinstance(value, exp.Literal) and not value.is_string:
                    position = int(value.this)
                    if 1 <= position <= len(output_names):
                        name = output_names[position - 1]
                if isinstance(value, exp.Column):
                    if not value.table and value.name in projected:
                        name = value.name
                    else:
                        name = next(
                            (
                                k
                                for k, expression in projected.items()
                                if isinstance(expression, exp.Column)
                                and expression.name == value.name
                                and (
                                    not value.table or value.table == expression.table
                                )
                            ),
                            None,
                        )
                if name is None:
                    name = next(
                        (
                            k for k, expression in projected.items()
                            if expression == value
                        ),
                        None,
                    )
                if name is None:
                    name = self.fresh("sort")
                    sort_expressions.append((name, value.copy()))
                ordered.append(
                    exp.Ordered(
                        this=exp.column(name, quoted=True),
                        desc=item.args.get("desc"),
                        nulls_first=item.args.get("nulls_first"),
                    )
                )

        sources = [node.args["from_"].this] if node.args.get("from_") else []
        sources.extend(j.this for j in node.args.get("joins") or [])
        source_columns: dict[str, list[str]] = {}
        for source in sources:
            names = (
                self.columns(source.this)
                if isinstance(source, exp.Subquery)
                else self.outputs[source.name]
            )
            alias = source.args.get("alias")
            supplied = (
                [i.name for i in alias.args.get("columns") or []] if alias else []
            )
            source_columns[source.alias_or_name] = supplied + names[len(supplied) :]

        def data_column(value: exp.Expr) -> TypeGuard[exp.Column]:
            return isinstance(value, exp.Column) and not (
                isinstance(value.parent, exp.Collate) and value.arg_key == "expression"
            )

        def canonical(expression: exp.Expr) -> exp.Expr:
            def qualify(value: exp.Expr) -> exp.Expr:
                if data_column(value) and not value.table:
                    aliases = [
                        a for a, names in source_columns.items() if value.name in names
                    ]
                    if len(aliases) == 1:
                        value.set("table", exp.to_identifier(aliases[0], quoted=True))
                return value

            return expression.transform(qualify)

        roots = [canonical(e) for e in projections]
        sort_roots = [(name, canonical(e)) for name, e in sort_expressions]
        columns: dict[str, tuple[str, exp.Expr]] = {}

        def key(value: exp.Expr) -> str:
            return value.sql(dialect=_ClosedPostgres(), comments=False)

        for root in [*roots, *(e for _, e in sort_roots)]:
            for value in root.walk():
                if data_column(value):
                    columns.setdefault(key(value), (self.fresh("value"), value.copy()))

        source_keys: list[list[set[str]]] = []
        for source in sources:
            if not isinstance(source, exp.Subquery):
                source_keys = []
                break
            scan = source.this.unnest()
            scan_from = scan.args.get("from_") if isinstance(scan, exp.Select) else None
            table = scan_from.this if scan_from else None
            if not isinstance(table, exp.Table) or not table.db:
                source_keys = []
                break
            schema = self.relations.get(table.db + "." + table.name)
            if schema is None:
                source_keys = []
                break
            catalog_positions = {
                column.name: index for index, column in enumerate(schema.columns)
            }
            exposed_names = source_columns[source.alias_or_name]
            keys_for_source: list[set[str]] = []
            for unique_key in schema.unique_keys:
                exposed_key = {
                    exposed_names[catalog_positions[column_name]]
                    for column_name in unique_key
                }
                flattened = {
                    flattened_name
                    for column_name in exposed_key
                    for flattened_name, original in columns.values()
                    if isinstance(original, exp.Column)
                    and original.table == source.alias_or_name
                    and original.name == column_name
                }
                if len(flattened) == len(unique_key):
                    keys_for_source.append(flattened)
            if not keys_for_source:
                source_keys = []
                break
            source_keys.append(keys_for_source)

        def total_window_order(window: exp.Window) -> bool:
            if not source_keys or len(source_keys) != len(sources):
                return False
            ordered = window.args.get("order")
            expressions = list(window.args.get("partition_by") or [])
            if ordered:
                expressions.extend(item.this for item in ordered.expressions)
            present = {
                value.name
                for value in expressions
                if isinstance(value, exp.Column) and not value.table
            }
            return all(any(key_columns <= present for key_columns in keys) for keys in source_keys)

        input_query = exp.select(
            *(exp.alias_(value, name, quoted=True) for name, value in columns.values()),
            exp.alias_(trace, self.hidden, quoted=True),
        )
        for clause in ("from_", "joins", "where"):
            value = node.args.get(clause)
            if value:
                input_query.set(
                    clause,
                    [x.copy() for x in value]
                    if isinstance(value, list)
                    else value.copy(),
                )
        input_name = self.store(input_query, "window_inputs")

        def rewrite(expression: exp.Expr) -> exp.Expr:
            return expression.transform(
                lambda value: exp.column(columns[key(value)][0], quoted=True)
                if data_column(value)
                else value
            )

        visible = [
            exp.alias_(
                rewrite(e.this if isinstance(e, exp.Alias) else e),
                original.alias_or_name,
                quoted=True,
            )
            for original, e in zip(projections, roots, strict=True)
        ]
        hidden_sorts = [
            exp.alias_(rewrite(expression), name, quoted=True)
            for name, expression in sort_roots
        ]
        windows: dict[str, exp.Window] = {}
        for root in [*visible, *hidden_sorts]:
            for value in root.walk():
                if isinstance(value, exp.Window):
                    windows.setdefault(key(value), value.copy())
        if not windows:
            raise SqlAdmissionError("unsupported", "witness_qualification_pending")

        partitions: dict[str, dict[str, Any]] = {}
        window_parts: dict[str, dict[str, Any]] = {}
        numbered = exp.select(
            *(exp.column(name, quoted=True) for name in self.outputs[input_name])
        ).from_(input_name)

        def partition_key(window: exp.Window) -> str:
            values = window.args.get("partition_by") or []
            ordering = window.args.get("order")
            return key(
                _array(
                    *[v.copy() for v in values],
                    ordering.copy() if ordering else exp.Null(),
                )
            )

        def companion(
            window: exp.Window,
            function: exp.Expr,
            *,
            frame: bool = False,
            total_order: bool = False,
        ) -> exp.Window:
            result = window.copy()
            result.set("this", function)
            if not frame:
                result.set("spec", None)
            if total_order:
                ordering = result.args.get("order")
                if ordering:
                    ordering.append(
                        "expressions",
                        exp.Ordered(
                            this=exp.column(self.hidden, quoted=True),
                            nulls_first=False,
                        ),
                    )
                else:
                    result.set(
                        "order",
                        exp.Order(
                            expressions=[
                                exp.Ordered(
                                    this=exp.column(self.hidden, quoted=True),
                                    nulls_first=False,
                                )
                            ]
                        ),
                    )
            return result

        for signature, window in windows.items():
            pkey = partition_key(window)
            if pkey not in partitions:
                part_values = [e.copy() for e in window.args.get("partition_by") or []]
                partition_id = self.fresh("partition_id")
                ordinal = self.fresh("window_ordinal")
                peer_rank = self.fresh("peer_rank")
                partition_class: exp.Expr = (
                    exp.Window(
                        this=exp.DenseRank(),
                        order=exp.Order(
                            expressions=[exp.Ordered(this=e.copy()) for e in part_values]
                        ),
                    )
                    if part_values
                    else exp.Cast(
                        this=exp.Literal.number(1),
                        to=exp.DataType.build("BIGINT"),
                    )
                )
                numbered.append(
                    "expressions", exp.alias_(partition_class, partition_id, quoted=True)
                )
                numbered.append(
                    "expressions",
                    exp.alias_(
                        companion(window, exp.RowNumber(), total_order=True),
                        ordinal,
                        quoted=True,
                    ),
                )
                numbered.append(
                    "expressions",
                    exp.alias_(companion(window, exp.DenseRank()), peer_rank, quoted=True),
                )
                partitions[pkey] = {
                    "window": window,
                    "partition": part_values,
                    "partition_id": partition_id,
                    "ordinal": ordinal,
                    "peer_rank": peer_rank,
                }
            window_parts[signature] = partitions[pkey]
        numbered_name = self.store(numbered, "window_numbered")

        for part in partitions.values():
            window = part["window"]
            partition_stage = self.stage(
                "window_partition",
                window=window.dump(),
                partition=[e.dump() for e in part["partition"]],
                ordering=window.args["order"].dump()
                if window.args.get("order")
                else None,
            )
            part["stage"] = partition_stage
            members = exp.Anonymous(
                this="pg_catalog.jsonb_agg",
                expressions=[
                    exp.Order(
                        this=_array(
                            exp.column(part["ordinal"], quoted=True),
                            exp.column(part["peer_rank"], quoted=True),
                            exp.column(self.hidden, quoted=True),
                        ),
                        expressions=[
                            exp.Ordered(this=exp.column(part["ordinal"], quoted=True))
                        ],
                    )
                ],
            )
            ledger = (
                exp.select(
                    exp.alias_(
                        self.tag(
                            partition_stage,
                            members,
                            exp.column(part["partition_id"], quoted=True),
                        ),
                        self.hidden,
                        quoted=True,
                    )
                )
                .from_(numbered_name)
                .group_by(exp.column(part["partition_id"], quoted=True))
            )
            ledger_name = self.store(ledger, "window_partitions", ledger=True)
            self.ledger_owners[ledger_name] = numbered_name

        witness_columns = [self.hidden]
        for part in partitions.values():
            witness_columns.extend(
                (part["partition_id"], part["ordinal"], part["peer_rank"])
            )
        evaluated = exp.select(
            *visible,
            *hidden_sorts,
            *(exp.column(name, quoted=True) for name in witness_columns),
        ).from_(numbered_name)
        descriptors: list[dict[str, Any]] = []
        for signature, window in windows.items():
            part = window_parts[signature]
            function = window.this.this if isinstance(window.this, exp.Filter) else window.this
            if not isinstance(
                function,
                exp.RowNumber | exp.Rank | exp.DenseRank | exp.Lag | exp.Lead
                | exp.Count | exp.Sum | exp.Avg | exp.Min | exp.Max,
            ):
                raise SqlAdmissionError("unsupported", "witness_qualification_pending")
            neighbor: str | None = None
            frame_mode: str | None = None
            frame_start: str | None = None
            frame_end: str | None = None
            frame_members: str | None = None
            if isinstance(function, exp.Lag | exp.Lead):
                neighbor = self.fresh("neighbor")
                evaluated.append(
                    "expressions",
                    exp.alias_(
                        companion(
                            window,
                            type(function)(
                                this=exp.column(self.hidden, quoted=True),
                                offset=function.args["offset"].copy(),
                            ),
                        ),
                        neighbor,
                        quoted=True,
                    ),
                )
            if isinstance(function, exp.Count | exp.Sum | exp.Avg | exp.Min | exp.Max):
                if not isinstance(window.args.get("spec"), exp.WindowSpec):
                    raise SqlAdmissionError("unsupported", "witness_qualification_pending")
                if total_window_order(window):
                    frame_mode = "total_span"
                    frame_start = self.fresh("frame_start")
                    frame_end = self.fresh("frame_end")
                    for name, kind in ((frame_start, exp.Min), (frame_end, exp.Max)):
                        evaluated.append(
                            "expressions",
                            exp.alias_(
                                companion(
                                    window,
                                    kind(
                                        this=exp.column(part["ordinal"], quoted=True)
                                    ),
                                    frame=True,
                                ),
                                name,
                                quoted=True,
                            ),
                        )
                else:
                    frame_mode = "native_members"
                    frame_members = self.fresh("frame_members")
                    evaluated.append(
                        "expressions",
                        exp.alias_(
                            companion(
                                window,
                                exp.Anonymous(
                                    this="pg_catalog.jsonb_agg",
                                    expressions=[exp.column(self.hidden, quoted=True)],
                                ),
                                frame=True,
                            ),
                            frame_members,
                            quoted=True,
                        ),
                    )
            stage = self.stage(
                "window",
                partition_stage=part["stage"],
                kind=type(function).__name__,
                offset=int(function.args["offset"].this)
                if isinstance(function, exp.Lag | exp.Lead)
                else None,
                window=window.dump(),
                frame=window.args["spec"].dump()
                if isinstance(window.args.get("spec"), exp.WindowSpec)
                else None,
                frame_mode=frame_mode,
            )
            descriptors.append(
                {
                    "stage": stage,
                    "part": part,
                    "neighbor": neighbor,
                    "frame_start": frame_start,
                    "frame_end": frame_end,
                    "frame_members": frame_members,
                }
            )
        evaluated_name = self.store(evaluated, "window_values")

        source_witness = exp.column(self.hidden, quoted=True)
        window_witnesses: list[exp.Expr] = []
        for descriptor in descriptors:
            part = descriptor["part"]
            window_witnesses.append(
                self.tag(
                    descriptor["stage"],
                    source_witness.copy(),
                    exp.column(part["partition_id"], quoted=True),
                    exp.column(part["ordinal"], quoted=True),
                    exp.column(part["peer_rank"], quoted=True),
                    exp.column(descriptor["neighbor"], quoted=True)
                    if descriptor["neighbor"]
                    else exp.Null(),
                    exp.column(descriptor["frame_members"], quoted=True)
                    if descriptor["frame_members"]
                    else exp.Null(),
                    exp.column(descriptor["frame_start"], quoted=True)
                    if descriptor["frame_start"]
                    else exp.Null(),
                    exp.column(descriptor["frame_end"], quoted=True)
                    if descriptor["frame_end"]
                    else exp.Null(),
                )
            )
        witness = self.tag(
            self.stage(
                "project",
                expressions=[e.dump() for e in projections],
                arity=len(window_witnesses),
            ),
            *window_witnesses,
        )
        result = exp.select(
            *(exp.column(name, quoted=True) for name in output_names),
            exp.alias_(witness, self.hidden, quoted=True),
        ).from_(evaluated_name)
        if ordered:
            result.set("order", exp.Order(expressions=ordered))
        if node.args.get("distinct"):
            result = self.distinct_query(result, projections, result.args.get("order"))
        for clause in ("limit", "offset"):
            if node.args.get(clause):
                result.set(clause, node.args[clause].copy())
        if node.args.get("limit") or node.args.get("offset"):
            result.meta["original_order"] = (
                original_order.dump() if original_order else None
            )
            result.expressions[-1].set(
                "this", self.tag(self.subset(result), result.expressions[-1].this)
            )
        self.order(result)
        return result

    def group_window_query(self, node: exp.Select, trace: exp.Expr) -> exp.Select:
        """Evaluate ordinary groups first, then native windows over accepted groups."""
        group = node.args.get("group")

        def key(value: exp.Expr) -> str:
            return value.sql(dialect=_ClosedPostgres(), comments=False)

        materialized: dict[str, tuple[str, exp.Expr]] = {}

        def save(value: exp.Expr) -> None:
            signature = key(value)
            if signature not in materialized:
                materialized[signature] = (self.fresh("group_value"), value.copy())

        if group:
            for value in group.expressions:
                save(value)

        roots = [*node.expressions]
        if node.args.get("order"):
            roots.extend(o.this for o in node.args["order"].expressions)
        for root in roots:
            for value in root.walk():
                if not isinstance(value, exp.AggFunc) or isinstance(
                    value.parent, exp.Filter
                ):
                    continue
                parent = value.parent
                while parent is not None and not isinstance(parent, exp.Window):
                    parent = parent.parent
                if parent is None or value is not (
                    parent.this.this
                    if isinstance(parent.this, exp.Filter)
                    else parent.this
                ):
                    save(value)
            for value in root.walk():
                if not isinstance(value, exp.Filter):
                    continue
                parent = value.parent
                while parent is not None and not isinstance(parent, exp.Window):
                    parent = parent.parent
                if parent is None or value is not parent.this:
                    save(value)

        if not materialized:
            raise SqlAdmissionError("unsupported", "witness_qualification_pending")
        grouped = node.copy()
        grouped.set(
            "expressions",
            [
                exp.alias_(expression.copy(), name, quoted=True)
                for name, expression in materialized.values()
            ],
        )
        for clause in ("distinct", "order", "limit", "offset"):
            grouped.set(clause, None)
        grouped = self.group_query(grouped, trace)
        grouped.append(
            "expressions",
            exp.alias_(
                self.tag(
                    self.stage("project", phase="accepted_group"),
                    exp.column(self.hidden, quoted=True),
                ),
                self.hidden,
                quoted=True,
            ),
        )
        grouped_name = self.store(grouped, "window_groups")

        def rewrite_group(value: exp.Expr) -> exp.Expr:
            entry = materialized.get(key(value))
            if entry:
                return exp.column(entry[0], quoted=True)
            if isinstance(value, exp.Column) and value.table:
                raise SqlAdmissionError("unsupported", "witness_qualification_pending")
            result = value.copy()
            for argument, child in list(result.args.items()):
                if isinstance(child, exp.Expr):
                    result.set(argument, rewrite_group(child))
                elif isinstance(child, list):
                    result.set(
                        argument,
                        [
                            rewrite_group(item) if isinstance(item, exp.Expr) else item
                            for item in child
                        ],
                    )
            return result

        outer = node.copy()
        outer.set("from_", exp.From(this=exp.to_table(grouped_name)))
        for clause in ("joins", "where", "group", "having"):
            outer.set(clause, None)
        outer.set(
            "expressions",
            [
                exp.alias_(
                    rewrite_group(
                        expression.this.copy()
                        if isinstance(expression, exp.Alias)
                        else expression.copy()
                    ),
                    original.alias_or_name,
                    quoted=True,
                )
                for original, expression in zip(
                    node.expressions, outer.expressions, strict=True
                )
            ],
        )
        if outer.args.get("order"):
            outer.set("order", rewrite_group(outer.args["order"]))
        return self.window_query(outer, exp.column(self.hidden, quoted=True))

    def aggregate(self, value: exp.Expr) -> exp.Expr:
        return exp.Coalesce(
            this=exp.Anonymous(
                this="pg_catalog.jsonb_agg",
                expressions=[
                    exp.Order(
                        this=value,
                        expressions=[
                            exp.Ordered(
                                this=exp.column(self.hidden, quoted=True),
                                nulls_first=False,
                            )
                        ],
                    )
                ],
            ),
            expressions=[_array()],
        )

    def group_query(self, node: exp.Select, trace: exp.Expr) -> exp.Select:
        projected: dict[str, exp.Expr] = {
            e.alias_or_name: e.this if isinstance(e, exp.Alias) else e
            for e in node.expressions
        }
        original_order = node.args.get("order")
        group = node.args.get("group")
        having = node.args.get("having")
        sources = [node.args["from_"].this] if node.args.get("from_") else []
        sources.extend(j.this for j in node.args.get("joins") or [])
        source_columns: dict[str, list[str]] = {}
        for source in sources:
            names = (
                self.columns(source.this)
                if isinstance(source, exp.Subquery)
                else self.outputs[source.name]
            )
            aliases = source.args.get("alias")
            supplied = (
                [e.name for e in aliases.args.get("columns") or []] if aliases else []
            )
            source_columns[source.alias_or_name] = supplied + names[len(supplied) :]

        def data_column(n: exp.Expr) -> TypeGuard[exp.Column]:
            return isinstance(n, exp.Column) and not (
                isinstance(n.parent, exp.Collate) and n.arg_key == "expression"
            )

        def canonical(expression: exp.Expr) -> exp.Expr:
            def qualify(n: exp.Expr) -> exp.Expr:
                if data_column(n) and not n.table:
                    candidates = [
                        a for a, names in source_columns.items() if n.name in names
                    ]
                    if len(candidates) == 1:
                        n.set("table", exp.to_identifier(candidates[0], quoted=True))
                return n

            return expression.transform(qualify)

        def order_expression(value: exp.Expr) -> exp.Expr:
            if (
                isinstance(value, exp.Column)
                and not value.table
                and value.name in projected
            ):
                return projected[value.name].copy()
            return value.copy()

        expression_roots = [canonical(e) for e in node.expressions]
        group_values = [canonical(e) for e in group.expressions] if group else []
        having_value = canonical(having.this) if having else exp.Boolean(this=True)
        order_values = (
            [canonical(order_expression(o.this)) for o in original_order.expressions]
            if original_order
            else []
        )
        roots = [*expression_roots, *group_values, having_value, *order_values]
        columns: dict[str, tuple[str, exp.Expr]] = {}
        aggregates: dict[str, tuple[exp.Expr, str | None, str | None, str | None]] = {}

        def key(n: exp.Expr) -> str:
            return n.sql(dialect=_ClosedPostgres(), comments=False)

        for root in roots:
            for n in root.walk():
                if data_column(n):
                    columns.setdefault(key(n), (self.fresh("value"), n.copy()))
                if isinstance(n, exp.Filter) or (
                    isinstance(n, exp.AggFunc) and not isinstance(n.parent, exp.Filter)
                ):
                    if key(n) in aggregates:
                        continue
                    function = n.this if isinstance(n, exp.Filter) else n
                    argument = function.this
                    is_star = isinstance(argument, exp.Star)
                    distinct = isinstance(argument, exp.Distinct)
                    aggregates[key(n)] = (
                        n.copy(),
                        None if is_star else self.fresh("argument"),
                        self.fresh("condition") if isinstance(n, exp.Filter) else None,
                        self.fresh("class") if distinct else None,
                    )

        input_query = exp.select(
            *(exp.alias_(v, a, quoted=True) for a, v in columns.values()),
            exp.alias_(trace, self.hidden, quoted=True),
        )
        for clause in ("from_", "joins", "where"):
            value = node.args.get(clause)
            if value:
                input_query.set(
                    clause,
                    [e.copy() for e in value]
                    if isinstance(value, list)
                    else value.copy(),
                )
        for original, argument_name, condition_name, _ in aggregates.values():
            function = original.this if isinstance(original, exp.Filter) else original
            argument = function.this
            if argument_name:
                argument = (
                    argument.expressions[0]
                    if isinstance(argument, exp.Distinct)
                    else argument
                )
                if condition_name:
                    # Native FILTER excludes the argument before evaluation.
                    argument = exp.Case(
                        ifs=[
                            exp.If(
                                this=original.expression.this.copy(),
                                true=argument.copy(),
                            )
                        ]
                    )
                input_query.append(
                    "expressions",
                    exp.alias_(argument.copy(), argument_name, quoted=True),
                )
            if condition_name:
                input_query.append(
                    "expressions",
                    exp.alias_(
                        original.expression.this.copy(), condition_name, quoted=True
                    ),
                )
        input_name = self.store(input_query, "group_inputs")

        def rewrite(expression: exp.Expr) -> exp.Expr:
            def replace(n: exp.Expr) -> exp.Expr:
                entry = aggregates.get(key(n))
                if entry and isinstance(n, exp.AggFunc | exp.Filter):
                    original, argument, condition, distinct = entry
                    function = (
                        original.this if isinstance(original, exp.Filter) else original
                    ).copy()
                    if argument:
                        value: exp.Expr = exp.column(argument, quoted=True)
                        if distinct:
                            value = exp.Distinct(expressions=[value])
                        function.set("this", value)
                    if condition:
                        return exp.Filter(
                            this=function,
                            expression=exp.Where(
                                this=exp.column(condition, quoted=True)
                            ),
                        )
                    return function
                if data_column(n):
                    return exp.column(columns[key(n)][0], quoted=True)
                return n

            return expression.transform(replace)

        grouped_values = [rewrite(e) for e in group_values]
        classes = exp.select(
            *(exp.column(n, quoted=True) for n in self.outputs[input_name])
        ).from_(input_name)
        extrema: dict[str, str] = {}
        for signature, (original, argument, condition, _) in aggregates.items():
            function = original.this if isinstance(original, exp.Filter) else original
            if isinstance(function, exp.Min | exp.Max | exp.LogicalAnd | exp.LogicalOr):
                assert argument is not None
                aggregate: exp.Expr = function.copy()
                aggregate.set("this", exp.column(argument, quoted=True))
                if condition:
                    aggregate = exp.Filter(
                        this=aggregate,
                        expression=exp.Where(this=exp.column(condition, quoted=True)),
                    )
                name = self.fresh("extremum")
                extrema[signature] = name
                classes.append(
                    "expressions",
                    exp.alias_(
                        exp.Window(
                            this=aggregate,
                            partition_by=[e.copy() for e in grouped_values],
                            spec=exp.WindowSpec(
                                kind="ROWS",
                                start="UNBOUNDED",
                                start_side="PRECEDING",
                                end="UNBOUNDED",
                                end_side="FOLLOWING",
                            ),
                        ),
                        name,
                        quoted=True,
                    ),
                )
        for _, argument, _, class_column in aggregates.values():
            if class_column:
                assert argument is not None
                rank = exp.Window(
                    this=exp.DenseRank(),
                    partition_by=[e.copy() for e in grouped_values],
                    order=exp.Order(
                        expressions=[
                            exp.Ordered(
                                this=exp.column(argument, quoted=True),
                                nulls_first=False,
                            )
                        ]
                    ),
                )
                classes.append(
                    "expressions", exp.alias_(rank, class_column, quoted=True)
                )
        class_name = self.store(classes, "aggregate_classes")
        accepted = rewrite(having_value)
        expressions = [
            exp.alias_(
                exp.Case(
                    ifs=[
                        exp.If(
                            this=accepted.copy(),
                            true=e.this.copy()
                            if isinstance(e, exp.Alias)
                            else e.copy(),
                        )
                    ]
                ),
                original.alias_or_name,
                quoted=True,
            )
            for original, e in zip(
                expression_roots, (rewrite(r) for r in expression_roots), strict=True
            )
        ]
        contributions: list[exp.Expr] = []
        descriptors: list[dict[str, Any]] = []
        for original, argument, condition, class_column in aggregates.values():
            extremum = extrema.get(key(original))
            winner: exp.Expr = exp.Null()
            if extremum:
                assert argument is not None
                winner = exp.and_(
                    exp.Not(
                        this=exp.Is(
                            this=exp.column(argument, quoted=True),
                            expression=exp.Null(),
                        )
                    ),
                    exp.Is(
                        this=exp.column(condition, quoted=True),
                        expression=exp.Boolean(this=True),
                    )
                    if condition
                    else exp.Boolean(this=True),
                    exp.NullSafeEQ(
                        this=exp.column(argument, quoted=True),
                        expression=exp.column(extremum, quoted=True),
                    ),
                )
            contributions.append(
                self.aggregate(
                    _array(
                        exp.column(self.hidden, quoted=True),
                        exp.column(condition, quoted=True)
                        if condition
                        else exp.Boolean(this=True),
                        exp.Not(
                            this=exp.Is(
                                this=exp.column(argument, quoted=True),
                                expression=exp.Null(),
                            )
                        )
                        if argument
                        else exp.Boolean(this=True),
                        exp.column(class_column, quoted=True)
                        if class_column
                        else exp.Null(),
                        winner,
                    )
                )
            )
            descriptors.append(
                {
                    "expression": original.dump(),
                    "distinct_classes": bool(class_column),
                    "star": argument is None,
                    "extremum": bool(extremum),
                }
            )
        stage = self.stage(
            "group",
            group=[e.dump() for e in group_values],
            having=having_value.dump(),
            aggregates=descriptors,
            expressions=[e.dump() for e in expression_roots],
            order=original_order.dump() if original_order else None,
        )
        native_values = _array(
            *(
                exp.Cast(
                    this=e.this.copy() if isinstance(e, exp.Alias) else e.copy(),
                    to=exp.DataType.build("TEXT"),
                )
                for e in expressions
            )
        )
        witness = self.tag(
            stage,
            self.aggregate(exp.column(self.hidden, quoted=True)),
            _array(*contributions),
            accepted.copy(),
            native_values,
            _array(
                *(
                    exp.Cast(this=e.copy(), to=exp.DataType.build("TEXT"))
                    for e in grouped_values
                )
            ),
            _array(
                *(
                    exp.Cast(this=rewrite(original), to=exp.DataType.build("TEXT"))
                    for original, _, _, _ in aggregates.values()
                )
            ),
        )
        accept_name = self.fresh("accepted")
        groups = exp.select(
            *expressions,
            exp.alias_(witness, self.hidden, quoted=True),
            exp.alias_(accepted, accept_name, quoted=True),
        ).from_(class_name)
        if group:
            groups.set("group", exp.Group(expressions=grouped_values))
        order_names: list[str] = []
        for value in order_values:
            name = self.fresh("ordering")
            order_names.append(name)
            groups.append(
                "expressions",
                exp.alias_(
                    exp.Case(ifs=[exp.If(this=accepted.copy(), true=rewrite(value))]),
                    name,
                    quoted=True,
                ),
            )
        name = self.store(groups, "groups", ledger=True)
        result = (
            exp.select(
                *(exp.column(e.alias_or_name, quoted=True) for e in node.expressions)
            )
            .from_(name)
            .where(exp.column(accept_name, quoted=True))
        )
        if original_order:
            result.set(
                "order",
                exp.Order(
                    expressions=[
                        exp.Ordered(
                            this=exp.column(n, quoted=True),
                            desc=o.args.get("desc"),
                            nulls_first=o.args.get("nulls_first"),
                        )
                        for n, o in zip(
                            order_names, original_order.expressions, strict=True
                        )
                    ]
                ),
            )
        for clause in ("limit", "offset"):
            if node.args.get(clause):
                result.set(clause, node.args[clause].copy())
        result.meta["original_order"] = (
            original_order.dump() if original_order else None
        )
        return result

    def set_query(self, node: exp.Expr, ctes: dict[str, str]) -> exp.Select:
        left = self.query(node.this, ctes)
        output_names = self.columns(left)[:-1]
        positions = [self.fresh("set_value") for _ in output_names]
        kind = type(node).__name__.lower()
        kind += "_all" if node.args.get("distinct") is False else "_distinct"
        branches: list[str] = []
        left_exists: exp.Expr | None = None
        for label in ("left", "right"):
            ledger_start = len(self.ledgers)
            arm = left if label == "left" else self.query(node.expression, ctes)
            if label == "right" and not isinstance(node, exp.Union):
                left_exists = exp.Exists(
                    this=exp.select(exp.Literal.number(1)).from_(branches[0])
                )
                for ledger_key in self.ledgers[ledger_start:]:
                    prior = self.ledger_conditions.get(ledger_key)
                    self.ledger_conditions[ledger_key] = (
                        exp.and_(prior.copy(), left_exists.copy())
                        if prior is not None
                        else left_exists.copy()
                    )
            alias = self.fresh("branch")
            stage = self.stage("set", kind=kind, branch=label)
            query = exp.select(
                *(exp.column(n, quoted=True) for n in positions),
                exp.alias_(
                    self.tag(stage, exp.column(self.hidden, quoted=True)),
                    self.hidden,
                    quoted=True,
                ),
            ).from_(
                exp.Subquery(
                    this=arm,
                    alias=exp.TableAlias(
                        this=exp.to_identifier(alias),
                        columns=[
                            exp.to_identifier(n, quoted=True)
                            for n in [*positions, self.hidden]
                        ],
                    ),
                )
            )
            if label == "right" and left_exists is not None:
                # PostgreSQL skips an unneeded right arm when the left is empty.
                # Do not force its projection/aggregate arguments through ledgers.
                query = query.where(left_exists.copy())
            branches.append(self.store(query, "set_branch"))
        branch_flag = self.fresh("left_branch")
        combined = exp.Union(
            this=exp.select(
                *(exp.column(n, quoted=True) for n in [*positions, self.hidden]),
                exp.alias_(exp.Boolean(this=True), branch_flag, quoted=True),
            ).from_(branches[0]),
            expression=exp.select(
                *(exp.column(n, quoted=True) for n in [*positions, self.hidden]),
                exp.alias_(exp.Boolean(this=False), branch_flag, quoted=True),
            ).from_(branches[1]),
            distinct=False,
        )
        combined_name = self.store(combined, "set_population")
        native = node.copy()
        for key, name in zip(("this", "expression"), branches, strict=True):
            native.set(
                key,
                exp.select(*(exp.column(n, quoted=True) for n in positions)).from_(
                    name
                ),
            )
        for key in ("order", "limit", "offset"):
            native.set(key, None)
        native_name = self.store(native, "native_set")
        output_count = self.fresh("output_count")
        count_query = (
            exp.select(
                *(exp.column(n, quoted=True) for n in positions),
                exp.alias_(exp.Count(this=exp.Star()), output_count, quoted=True),
            )
            .from_(native_name)
            .group_by(*(exp.column(n, quoted=True) for n in positions))
        )
        count_name = self.store(count_query, "set_output_counts")
        counts: list[str] = []
        traces: list[str] = []
        classes = (
            exp.select(*(exp.column(n, quoted=True) for n in positions))
            .from_(combined_name)
            .group_by(*(exp.column(n, quoted=True) for n in positions))
        )
        for is_left in (True, False):
            predicate: exp.Expr = exp.column(branch_flag, quoted=True)
            if not is_left:
                predicate = exp.Not(this=predicate)
            count = self.fresh("branch_count")
            trace = self.fresh("branch_members")
            counts.append(count)
            traces.append(trace)
            classes.append(
                "expressions",
                exp.alias_(
                    exp.Filter(
                        this=exp.Count(this=exp.Star()),
                        expression=exp.Where(this=predicate.copy()),
                    ),
                    count,
                    quoted=True,
                ),
            )
            aggregation = self.aggregate(exp.column(self.hidden, quoted=True))
            aggregation.set(
                "this",
                exp.Filter(this=aggregation.this, expression=exp.Where(this=predicate)),
            )
            classes.append("expressions", exp.alias_(aggregation, trace, quoted=True))
        class_name = self.store(classes, "set_classes")
        # Both empty branches have no equality class. Retain a population fact,
        # not an invented all-null tuple/class, with native zero cardinalities.
        empty_stage = self.stage(
            "set_population",
            kind=kind,
            empty_branches=True,
            right_skipped_if_left_empty=left_exists is not None,
        )
        empty = (
            exp.select(
                exp.alias_(
                    self.tag(
                        empty_stage,
                        self.aggregate(exp.column(self.hidden, quoted=True)),
                        self.aggregate(exp.column(self.hidden, quoted=True)),
                        _array(
                            exp.Count(this=exp.Star()),
                            exp.Null()
                            if left_exists is not None
                            else exp.Count(this=exp.Star()),
                            exp.Count(this=exp.Star()),
                        ),
                        _array(),
                    ),
                    self.hidden,
                    quoted=True,
                )
            )
            .from_(branches[0] if left_exists is not None else combined_name)
            .having(
                exp.EQ(
                    this=exp.Count(this=exp.Star()),
                    expression=exp.Cast(
                        this=exp.Literal.number(0), to=exp.DataType.build("BIGINT")
                    ),
                )
            )
        )
        empty_name = self.store(empty, "empty_set_population", ledger=True)
        self.ledger_owners[empty_name] = combined_name

        def equality(a: str, b: str) -> exp.Expr:
            return exp.and_(
                *(
                    exp.NullSafeEQ(
                        this=exp.column(n, table=a, quoted=True),
                        expression=exp.column(n, table=b, quoted=True),
                    )
                    for n in positions
                )
            )

        class_alias, count_alias = self.fresh("equivalence"), self.fresh("counts")
        stage = self.stage(
            "set_class",
            kind=kind,
            columns=output_names,
            equality="postgres_null_duplicate_collation",
            output_multiplicity="native_set_count",
        )
        witness = self.tag(
            stage,
            *(exp.column(n, table=class_alias, quoted=True) for n in traces),
            _array(
                *(exp.column(n, table=class_alias, quoted=True) for n in counts),
                exp.Coalesce(
                    this=exp.column(output_count, table=count_alias, quoted=True),
                    expressions=[exp.Literal.number(0)],
                ),
            ),
            _array(
                *(
                    exp.Cast(
                        this=exp.column(n, table=class_alias, quoted=True),
                        to=exp.DataType.build("TEXT"),
                    )
                    for n in positions
                )
            ),
        )
        ledger = (
            exp.select(
                *(exp.column(n, table=class_alias, quoted=True) for n in positions),
                exp.alias_(witness, self.hidden, quoted=True),
            )
            .from_(
                exp.Table(
                    this=exp.to_identifier(class_name),
                    alias=exp.TableAlias(this=exp.to_identifier(class_alias)),
                )
            )
            .join(
                exp.Table(
                    this=exp.to_identifier(count_name),
                    alias=exp.TableAlias(this=exp.to_identifier(count_alias)),
                ),
                on=equality(class_alias, count_alias),
                join_type="LEFT",
            )
        )
        ledger_name = self.store(ledger, "set_witnesses", ledger=True)
        native_alias, ledger_alias = (
            self.fresh("native_values"),
            self.fresh("witness_values"),
        )
        result = (
            exp.select(
                *(
                    exp.alias_(
                        exp.column(n, table=native_alias, quoted=True),
                        original,
                        quoted=True,
                    )
                    for n, original in zip(positions, output_names, strict=True)
                ),
                exp.alias_(
                    exp.column(self.hidden, table=ledger_alias, quoted=True),
                    self.hidden,
                    quoted=True,
                ),
            )
            .from_(
                exp.Table(
                    this=exp.to_identifier(native_name),
                    alias=exp.TableAlias(this=exp.to_identifier(native_alias)),
                )
            )
            .join(
                exp.Table(
                    this=exp.to_identifier(ledger_name),
                    alias=exp.TableAlias(this=exp.to_identifier(ledger_alias)),
                ),
                on=equality(native_alias, ledger_alias),
            )
        )
        for key in ("order", "limit", "offset"):
            if node.args.get(key):
                result.set(key, node.args[key].copy())
        if node.args.get("limit") or node.args.get("offset"):
            result.expressions[-1].set(
                "this", self.tag(self.subset(node), result.expressions[-1].this)
            )
        self.order(result)
        return result

    def finish(self, node: exp.Expr) -> exp.Expr:
        definitions = {c.alias: c for c in self.ctes}
        reachable: set[str] = set()
        pending = [t.name for t in node.find_all(exp.Table) if t.name in definitions]
        while pending:
            name = pending.pop()
            if name in reachable:
                continue
            reachable.add(name)
            pending.extend(
                t.name
                for t in definitions[name].this.find_all(exp.Table)
                if t.name in definitions
            )
        self.ledgers = [
            n
            for n in self.ledgers
            if n in reachable or self.ledger_owners.get(n) in reachable
        ]
        retained = reachable | set(self.ledgers)
        self.ctes = [c for c in self.ctes if c.alias in retained]
        active_stages = {
            n.meta["witness_stage"]
            for root in [node, *self.ctes]
            for n in root.walk()
            if "witness_stage" in n.meta
        }
        for stage in self.stages:
            stage["reachable"] = stage["stage"] in active_stages
        if not self.ledgers:
            if self.ctes:
                node.set("with_", exp.With(expressions=self.ctes))
            return node
        # Add a native ordinal before wrapping: outer UNION must not discard the
        # sealed ORDER BY. Resolve output aliases as PostgreSQL does for ordering.
        ordinal = self.fresh("ordinal")
        output_names = self.columns(node)[:-1]
        order = node.args["order"].copy()
        if isinstance(node, exp.Select):
            aliases: dict[str, exp.Expr] = {
                e.alias_or_name: e.this if isinstance(e, exp.Alias) else e
                for e in node.expressions
            }

            def resolve(n: exp.Expr) -> exp.Expr:
                if isinstance(n, exp.Column) and not n.table and n.name in aliases:
                    return aliases[n.name].copy()
                return n

            order = order.transform(resolve)
            node.append(
                "expressions",
                exp.alias_(
                    exp.Window(this=exp.RowNumber(), order=order), ordinal, quoted=True
                ),
            )
        else:
            inner_name = self.store(node, "ordered_set")
            node = exp.select(
                *(exp.column(n, quoted=True) for n in [*output_names, self.hidden]),
                exp.alias_(
                    exp.Window(this=exp.RowNumber(), order=order), ordinal, quoted=True
                ),
            ).from_(inner_name)
        result_name = self.store(node, "result")
        transport: exp.Expr = exp.select(
            *(exp.column(n, quoted=True) for n in [*output_names, self.hidden, ordinal])
        ).from_(result_name)
        for index, ledger in enumerate(self.ledgers):
            envelope = exp.select(
                *(exp.alias_(exp.Null(), n, quoted=True) for n in output_names),
                exp.alias_(
                    _array(exp.Null(), exp.column(self.hidden, quoted=True)),
                    self.hidden,
                    quoted=True,
                ),
                exp.alias_(
                    exp.Neg(this=exp.Literal.number(len(self.ledgers) - index)),
                    ordinal,
                    quoted=True,
                ),
            ).from_(ledger)
            if ledger in self.ledger_conditions:
                envelope = envelope.where(self.ledger_conditions[ledger].copy())
            transport = exp.Union(this=transport, expression=envelope, distinct=False)
        alias = self.fresh("transport")
        result = (
            exp.select(
                *(exp.column(n, quoted=True) for n in [*output_names, self.hidden])
            )
            .from_(
                exp.Subquery(
                    this=transport, alias=exp.TableAlias(this=exp.to_identifier(alias))
                )
            )
            .order_by(exp.column(ordinal, quoted=True))
        )
        result.set("with_", exp.With(expressions=self.ctes))
        return result

    def distinct_query(
        self,
        node: exp.Select,
        projections: list[exp.Expr],
        original_order: exp.Order | None,
    ) -> exp.Select:
        names = [e.alias_or_name for e in projections]
        order, limit, offset = (node.args.get(k) for k in ("order", "limit", "offset"))
        for k in ("order", "limit", "offset"):
            node.set(k, None)
        source = self.store(node, "distinct_inputs")
        values = [exp.column(n, quoted=True) for n in names]
        stage = self.stage(
            "collapse", kind="distinct", expressions=[e.dump() for e in projections]
        )
        grouped = (
            exp.select(
                *values,
                exp.alias_(
                    self.tag(
                        stage, self.aggregate(exp.column(self.hidden, quoted=True))
                    ),
                    self.hidden,
                    quoted=True,
                ),
            )
            .from_(source)
            .group_by(*(v.copy() for v in values))
        )
        name = self.store(grouped, "distinct_classes", ledger=True)
        result = exp.select(
            *(v.copy() for v in values), exp.column(self.hidden, quoted=True)
        ).from_(name)
        if original_order:

            def output(n: exp.Expr) -> exp.Expr:
                for p in projections:
                    expression = p.this if isinstance(p, exp.Alias) else p
                    if n == expression or (
                        isinstance(n, exp.Column)
                        and isinstance(expression, exp.Column)
                        and n.name == expression.name
                        and (not n.table or not expression.table)
                    ):
                        return exp.column(p.alias_or_name, quoted=True)
                return n

            result.set("order", original_order.transform(output))
        elif order:
            result.set("order", order)
        result.set("limit", limit)
        result.set("offset", offset)
        result.meta.update(node.meta)
        return result

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
        isinstance(n, exp.Exists)
        or (
            isinstance(n, exp.Subquery)
            and not isinstance(
                n.parent,
                exp.From | exp.Join | exp.CTE | exp.Subquery | exp.SetOperation,
            )
        )
        for n in tree.walk()
    ):
        raise SqlAdmissionError("unsupported", "witness_qualification_pending")
    compiler = _Compiler(tree, relations)
    body = compiler.query(tree, {})
    semantic_check = bool(compiler.ledgers)
    rewritten = compiler.finish(body)
    return WitnessPlan(
        rewritten.sql(
            dialect=_ClosedPostgres(),
            comments=False,
            unsupported_level=ErrorLevel.RAISE,
        ),
        tuple(compiler.stages),
        compiler.hidden,
        bool(compiler.ledgers),
        semantic_check,
    )
