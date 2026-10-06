"""Database-free structure of migration 0041 (author-complete-unit revision 7).

Revision 7 is revision 4 with a narrower author output. So 0041 restates every
installed function that treats revision 4 specially, with revision 7 (apply
command contract version 8) added beside it and nothing else changed. Each
restatement is held here to its installed text plus exactly the listed edits.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from memoriesql.application.local_entity_mentions import (
    AUTHORED_MENTION_TASK,
    load_authored_mentions_task_registry,
)
from memoriesql.application.module_registry import (
    BuiltInModuleRegistry,
    ProfileDefinition,
)

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "migrations"
NAME = "0041_authored_statement_kinds.sql"

TASK = AUTHORED_MENTION_TASK.contract_hash
OUTPUT = AUTHORED_MENTION_TASK.output_contract.schema_hash
REGISTRY = load_authored_mentions_task_registry(
    BuiltInModuleRegistry._from_source_controlled((), (ProfileDefinition("core", ()),), {})
).registry_hash

R4_OUTPUT = "0baecb82f0993a29dbc6c1afb6e41a5f3b83b8ebfe1f70315d87b5a7672d8e8c"
R6_OUTPUT = "7873968a84f6e7279040e45045a4948068c0cde6fcfdb68b417698ad330e0cfd"
APPLY_V5 = "'complete_input.apply.v4','complete_input.apply.v5'"
APPLY_V6 = APPLY_V5 + ",'complete_input.apply.v6'"
STATEMENTS = "jsonb_array_elements(requested_command#>'{payload,annotations,0,statements}')"
CONTEXT_AUTHORIZED = (
    "   FOREACH source IN ARRAY s.context_source_ids LOOP\n"
    "    PERFORM memoriesql.revisiting_source_authorize(source); sources:=array_append(sources,source);\n"
    "   END LOOP;\n"
)
CONTEXT_AUTHORIZED_BY_UNIT = (
    "   FOREACH source IN ARRAY s.context_source_ids LOOP\n"
    "    -- A context source is a unit of the statement's own evidence. Authorize\n"
    "    -- its event's source object, as evidence is; missing or unreadable reads\n"
    "    -- exactly as an unknown note.\n"
    "    SELECT e.source_object_id INTO source FROM memoriesql.source_units cu\n"
    "     JOIN memoriesql.source_events e ON e.tenant_id=cu.tenant_id AND e.event_id=cu.event_id\n"
    "     WHERE cu.tenant_id=s.tenant_id AND cu.source_unit_id=source;\n"
    "    IF NOT FOUND THEN RETURN unavailable; END IF;\n"
    "    PERFORM memoriesql.revisiting_source_authorize(source); sources:=array_append(sources,source);\n"
    "   END LOOP;\n"
)
CORRECTIONS_REFUSED = (
    "        OR EXISTS(SELECT 1 FROM " + STATEMENTS + " q WHERE q->>'statement_kind'='correction')\n"
    "    ) THEN RAISE EXCEPTION 'complete_input_exposure_required' USING ERRCODE='42501'; END IF;\n"
)
CORRECTIONS_REFUSED_BEFORE_8 = (
    "        OR (requested_command->>'contract_version'<>'8' AND EXISTS(SELECT 1 FROM "
    + STATEMENTS + " q WHERE q->>'statement_kind'='correction'))\n"
    "    ) THEN RAISE EXCEPTION 'complete_input_exposure_required' USING ERRCODE='42501'; END IF;\n"
)
CORRECTION_TARGETS = (
    "    -- Revision 7 (apply version 8): a correction supersedes an earlier\n"
    "    -- statement of the same note, and a statement has at most one correction.\n"
    "    IF requested_command->>'contract_version'='8' AND EXISTS(\n"
    "        SELECT 1 FROM " + STATEMENTS + " WITH ORDINALITY q(s,i)\n"
    "        WHERE s->>'statement_kind'='correction' AND (\n"
    "            NOT EXISTS(SELECT 1 FROM " + STATEMENTS + " WITH ORDINALITY p(t,j)\n"
    "                WHERE j<i AND t->>'statement_id'=s->>'supersedes_statement_id')\n"
    "            OR (SELECT count(*) FROM " + STATEMENTS + " u(v)\n"
    "                WHERE v->>'supersedes_statement_id'=s->>'supersedes_statement_id')>1)\n"
    "    ) THEN RAISE EXCEPTION 'invalid_correction_target' USING ERRCODE='22023'; END IF;\n"
)

# Function: (its latest installed source, its name in 0041, the edits).
# Each edit is (installed text, 0041 text, occurrences).
RESTATED: dict[str, tuple[str, str, tuple[tuple[str, str, int], ...]]] = {
    "semantic_task_input_reference_safe": (
        "0029_relation_assessment.sql",
        "semantic_task_input_reference_safe",
        (
            (
                "candidate->>'contract_revision' IN ('3','4','5') THEN",
                "candidate->>'contract_revision' IN ('3','4','5','7') THEN",
                1,
            ),
        ),
    ),
    "reauthorize_semantic_task": (
        "0029_relation_assessment.sql",
        "reauthorize_semantic_task",
        (
            (
                "task_record.contract_revision IN (2,3,4,5,6) THEN",
                "task_record.contract_revision IN (2,3,4,5,6,7) THEN",
                1,
            ),
            (
                "IF task_record.contract_revision IN (3,4,5,6) THEN",
                "IF task_record.contract_revision IN (3,4,5,6,7) THEN",
                1,
            ),
        ),
    ),
    "consume_supervised_dispatch": (
        "0029_relation_assessment.sql",
        "consume_supervised_dispatch",
        (
            (
                "(i->>'task_contract_revision')::integer IN (2,3,4,5,6))",
                "(i->>'task_contract_revision')::integer IN (2,3,4,5,6,7))",
                1,
            ),
        ),
    ),
    "source_revisiting_authorize": (
        "0027_authored_claims_and_relations.sql",
        "source_revisiting_authorize",
        (
            (
                "e.execution_contract_revision NOT IN (3,4,5,6))",
                "e.execution_contract_revision NOT IN (3,4,5,6,7))",
                1,
            ),
        ),
    ),
    "assert_complete_execution_binding": (
        "0027_authored_claims_and_relations.sql",
        "assert_complete_execution_binding",
        (
            (
                "NEW.contract_revision NOT IN (2,3,4,5,6) THEN RETURN NULL;",
                "NEW.contract_revision NOT IN (2,3,4,5,6,7) THEN RETURN NULL;",
                1,
            ),
            (
                "IF e.execution_contract_revision IN (3,4,5,6) THEN",
                "IF e.execution_contract_revision IN (3,4,5,6,7) THEN",
                1,
            ),
            (
                "t.task_contract_hash IS DISTINCT FROM (CASE WHEN e.execution_contract_revision=6 THEN",
                "t.task_contract_hash IS DISTINCT FROM (CASE WHEN e.execution_contract_revision=7 THEN '"
                + TASK
                + "' WHEN e.execution_contract_revision=6 THEN",
                1,
            ),
            (
                "t.semantic_registry_hash IS DISTINCT FROM (CASE WHEN e.execution_contract_revision=6 THEN",
                "t.semantic_registry_hash IS DISTINCT FROM (CASE WHEN e.execution_contract_revision=7 THEN '"
                + REGISTRY
                + "' WHEN e.execution_contract_revision=6 THEN",
                1,
            ),
        ),
    ),
    "guard_complete_unit_execution": (
        "0027_authored_claims_and_relations.sql",
        "guard_complete_unit_execution",
        (
            (
                "ELSIF NEW.contract_revision IN (2,3,4,5,6) THEN",
                "ELSIF NEW.contract_revision IN (2,3,4,5,6,7) THEN",
                1,
            ),
            (APPLY_V5 + ")", APPLY_V6 + ")", 2),
            (
                "(t.contract_revision NOT IN (2,3,4,5,6) OR NOT EXISTS",
                "(t.contract_revision NOT IN (2,3,4,5,6,7) OR NOT EXISTS",
                1,
            ),
            (
                "OR t.contract_revision NOT IN (2,3,4,5,6) OR t.status<>'running'",
                "OR t.contract_revision NOT IN (2,3,4,5,6,7) OR t.status<>'running'",
                1,
            ),
        ),
    ),
    "apply_semantic_annotations": (
        "0027_authored_claims_and_relations.sql",
        # Migration 0030 renamed it and installed a lifecycle wrapper under the
        # old name; the body is unchanged since 0027.
        "apply_semantic_annotations_schema29",
        (
            (
                "FUNCTION memoriesql.apply_semantic_annotations(",
                "FUNCTION memoriesql.apply_semantic_annotations_schema29(",
                1,
            ),
            (
                "is_complete := requested_command->>'contract_version' IN ('3','4','5','6','7');",
                "is_complete := requested_command->>'contract_version' IN ('3','4','5','6','7','8');",
                1,
            ),
            (
                "operation_key := CASE WHEN requested_command->>'contract_version'='7' THEN 'complete_input.apply.v5'",
                "operation_key := CASE WHEN requested_command->>'contract_version'='8' THEN 'complete_input.apply.v6'"
                " WHEN requested_command->>'contract_version'='7' THEN 'complete_input.apply.v5'",
                1,
            ),
            (
                "AND requested_command->>'output_contract_hash'='" + R6_OUTPUT + "')\n",
                "AND requested_command->>'output_contract_hash'='"
                + R6_OUTPUT
                + "')\n"
                "            OR (requested_command->>'contract_version'='8' AND requested_command->>'expected_schema_version'='41'\n"
                "                AND requested_command->>'task_kind'='memory.semantic.author-complete-unit' AND requested_command->>'contract_revision'='7'\n"
                "                AND requested_command->>'output_contract_hash'='" + OUTPUT + "')\n",
                1,
            ),
            (
                "IF requested_command->>'contract_version' IN ('4','5','6','7') THEN",
                "IF requested_command->>'contract_version' IN ('4','5','6','7','8') THEN",
                1,
            ),
            (
                "IF requested_command->>'contract_version' IN ('5','6','7') THEN",
                "IF requested_command->>'contract_version' IN ('5','6','7','8') THEN",
                1,
            ),
            (
                "jsonb_build_object('contract_version', CASE WHEN requested_command->>'contract_version'='7' THEN 7",
                "jsonb_build_object('contract_version', CASE WHEN requested_command->>'contract_version'='8' THEN 8"
                " WHEN requested_command->>'contract_version'='7' THEN 7",
                1,
            ),
            # Revision 7 only: a correction supersedes an earlier statement of
            # the same note, at most once each (the existing statement write).
            (CORRECTIONS_REFUSED, CORRECTIONS_REFUSED_BEFORE_8 + CORRECTION_TARGETS, 1),
        ),
    ),
    "record_source_delivery_v1": (
        "0027_authored_claims_and_relations.sql",
        "record_source_delivery_v1",
        (
            (
                "OR task.contract_revision NOT IN (3,4,5,6) OR task.status<>'running'",
                "OR task.contract_revision NOT IN (3,4,5,6,7) OR task.status<>'running'",
                1,
            ),
        ),
    ),
    "complete_input_exposure_valid": (
        "0027_authored_claims_and_relations.sql",
        "complete_input_exposure_valid",
        (
            (
                "AND (e.execution_contract_revision NOT IN (3,4,5,6) OR",
                "AND (e.execution_contract_revision NOT IN (3,4,5,6,7) OR",
                1,
            ),
            (
                "CASE WHEN e.execution_contract_revision IN (3,4,5,6) THEN NOT COALESCE(",
                "CASE WHEN e.execution_contract_revision IN (3,4,5,6,7) THEN NOT COALESCE(",
                1,
            ),
        ),
    ),
    "inspect_stored_bead_v1": (
        "0027_authored_claims_and_relations.sql",
        "inspect_stored_bead_v1",
        (
            (APPLY_V5 + ")\n", APPLY_V6 + ")\n", 1),
            (
                "     (r.task_contract_version=6 AND ir.operation_kind='complete_input.apply.v5')) THEN",
                "     (r.task_contract_version=6 AND ir.operation_kind='complete_input.apply.v5') OR\n"
                "     (r.task_contract_version=7 AND ir.operation_kind='complete_input.apply.v6')) THEN",
                1,
            ),
            # A context source is a unit of the statement's own evidence:
            # authorize its event's source object, as evidence is. Before, the
            # unit ID was read as a source object, so the note was unavailable.
            (CONTEXT_AUTHORIZED, CONTEXT_AUTHORIZED_BY_UNIT, 1),
        ),
    ),
}

# The revision-7 activation is revision 4's (activate_complete_input_v3, 0022)
# with its own command version, schema, revision, operation and pins.
ACTIVATION: tuple[tuple[str, str, int], ...] = (
    (
        "CREATE OR REPLACE FUNCTION memoriesql.activate_complete_input_v3(",
        "CREATE FUNCTION memoriesql.activate_complete_input_v6(",
        1,
    ),
    (
        "IF request->>'contract_version' IS DISTINCT FROM '3' OR request->>'expected_schema_version' IS DISTINCT FROM '22' OR",
        "IF request->>'contract_version' IS DISTINCT FROM '6' OR request->>'expected_schema_version' IS DISTINCT FROM '41' OR",
        1,
    ),
    ("d.execution_contract_revision=4)", "d.execution_contract_revision=7)", 3),
    ("'complete_input.activate.v3'", "'complete_input.activate.v6'", 2),
    (
        "IF e.execution_contract_revision<>4 OR",
        "IF e.execution_contract_revision<>7 OR",
        1,
    ),
    ("rid,4,request->'authorized_context');", "rid,7,request->'authorized_context');", 1),
    (
        "'task_kind','memory.semantic.author-complete-unit','contract_revision',4,",
        "'task_kind','memory.semantic.author-complete-unit','contract_revision',7,",
        1,
    ),
    (
        "'complete-input.v3:'||b.task_id::text,'memoriesql.kernel','memory.semantic.author-complete-unit',4,"
        "'8488d4fddf19e65a2eb41203d6b26d7b400d73516c044700246ab3a7e75adce5',"
        "'semantic-tasks-v1:0876addc48bcf62db405dff9d1a8b75eb497cbec739945d741d4db72aea4b6dd'",
        "'complete-input.v6:'||b.task_id::text,'memoriesql.kernel','memory.semantic.author-complete-unit',7,'"
        + TASK
        + "','"
        + REGISTRY
        + "'",
        1,
    ),
    ("result:=jsonb_build_object('contract_version',3,", "result:=jsonb_build_object('contract_version',6,", 1),
)

CHECKS = (
    (
        "complete_input_executions",
        "complete_input_executions_execution_contract_revision_check",
    ),
    (
        "complete_input_dispatch_policies",
        "complete_input_dispatch_polic_execution_contract_revision_check",
    ),
)

ADMISSION = (
    "INSERT INTO memoriesql.semantic_task_admission_policies (semantic_registry_hash,task_kind,"
    "contract_revision,owning_module,task_contract_hash,target_kind,required_capability,"
    "queue_name,base_priority,max_attempts,concurrency_key,concurrency_limit) VALUES ('"
    + REGISTRY
    + "','memory.semantic.author-complete-unit',7,'memoriesql.kernel','"
    + TASK
    + "','canonical_semantics','memory.capture','capture',50,3,NULL,NULL);\n"
)


def function_text(source: str, name: str, occurrence: int = -1) -> str:
    """One function's full statement, from CREATE to its closing ``$$;``."""
    starts = [
        m.start()
        for m in re.finditer(
            r"CREATE (?:OR REPLACE )?FUNCTION memoriesql\." + re.escape(name) + r"\(",
            source,
        )
    ]
    start = starts[occurrence]
    tag = re.compile(r"AS (\$[A-Za-z_]*\$)").search(source, start)
    assert tag is not None
    end = source.index(tag.group(1), tag.end()) + len(tag.group(1))
    assert source[end] == ";"
    return source[start : end + 1]


def edited(text: str, edits: tuple[tuple[str, str, int], ...]) -> str:
    for old, new, count in edits:
        assert text.count(old) == count, (old, text.count(old))
        text = text.replace(old, new)
    return text


def installed(name: str) -> str:
    source, _, edits = RESTATED[name]
    original = function_text((MIGRATIONS / source).read_text(), name)
    original = original.replace("CREATE FUNCTION ", "CREATE OR REPLACE FUNCTION ", 1)
    return edited(original, edits)


def activation() -> str:
    original = function_text(
        (MIGRATIONS / "0022_authored_local_entity_mentions.sql").read_text(),
        "activate_complete_input_v3",
    )
    return edited(original, ACTIVATION)


class AuthoredStatementKindsMigration(unittest.TestCase):
    def setUp(self) -> None:
        self.sql = (MIGRATIONS / NAME).read_text()

    def test_each_restatement_is_its_installed_text_with_revision_7_added(
        self,
    ) -> None:
        for name, (_, target, _) in RESTATED.items():
            with self.subTest(function=name):
                self.assertEqual(function_text(self.sql, target), installed(name))

    def test_revision_7_has_its_own_activation_admission_and_bounds(self) -> None:
        self.assertEqual(
            function_text(self.sql, "activate_complete_input_v6"), activation()
        )
        self.assertIn(
            "REVOKE ALL ON FUNCTION memoriesql.activate_complete_input_v6(jsonb) FROM PUBLIC;\n"
            "GRANT EXECUTE ON FUNCTION memoriesql.activate_complete_input_v6(jsonb) TO memoriesql_application;\n",
            self.sql,
        )
        self.assertEqual(self.sql.count(ADMISSION), 1)
        for table, constraint in CHECKS:
            with self.subTest(table=table):
                self.assertIn(
                    f"ALTER TABLE memoriesql.{table}\n"
                    f"    DROP CONSTRAINT {constraint},\n"
                    f"    ADD CONSTRAINT {constraint}\n"
                    "        CHECK (execution_contract_revision IN (2, 3, 4, 5, 6, 7));\n",
                    self.sql,
                )

    def test_nothing_else_is_in_the_migration(self) -> None:
        # Without the statements checked above, only comments remain.
        rest = self.sql
        for _, target, _ in RESTATED.values():
            rest = rest.replace(function_text(self.sql, target), "", 1)
        rest = rest.replace(function_text(self.sql, "activate_complete_input_v6"), "", 1)
        rest = rest.replace(ADMISSION, "", 1)
        rest = rest.replace(
            "REVOKE ALL ON FUNCTION memoriesql.activate_complete_input_v6(jsonb) FROM PUBLIC;\n"
            "GRANT EXECUTE ON FUNCTION memoriesql.activate_complete_input_v6(jsonb) TO memoriesql_application;\n",
            "",
            1,
        )
        for table, constraint in CHECKS:
            rest = rest.replace(
                f"ALTER TABLE memoriesql.{table}\n"
                f"    DROP CONSTRAINT {constraint},\n"
                f"    ADD CONSTRAINT {constraint}\n"
                "        CHECK (execution_contract_revision IN (2, 3, 4, 5, 6, 7));\n",
                "",
                1,
            )
        leftover = [
            line for line in rest.splitlines() if line.strip() and not line.startswith("--")
        ]
        self.assertEqual(leftover, [])

    def test_the_pinned_hashes_are_the_python_contracts(self) -> None:
        self.assertIn(TASK, self.sql)
        self.assertIn(REGISTRY, self.sql)
        self.assertIn(OUTPUT, self.sql)
        # Revision 4's pins are untouched by the new revision.
        self.assertNotEqual(OUTPUT, R4_OUTPUT)


if __name__ == "__main__":
    unittest.main()
