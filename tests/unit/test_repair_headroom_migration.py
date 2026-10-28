"""Database-free structure of migration 0058 (request headroom, guard audit P1-f D2).

Part 1: author-complete-unit revision 8 is revision 2 with one more request.
Part 2: assess-relations revision 2 is revision 1 with one more request, and
its author agent is memory.semantic.relation-author.v2. So 0058 restates every
installed function that treats complete-unit revision 2 or relation revision 1
specially, with the new revision handled exactly like it and nothing else
changed. Each restatement is held here to its installed text plus exactly the
listed edits.
"""

from __future__ import annotations

import hashlib
import json
import re
import unittest
from pathlib import Path

from memoriesql.application.complete_input_execution import (
    COMPLETE_EXECUTION_TASK,
    REPAIRABLE_EXECUTION_TASK,
    load_complete_input_task_registry,
    load_repairable_complete_input_task_registry,
)
from memoriesql.application.module_registry import (
    BuiltInModuleRegistry,
    ProfileDefinition,
)
from memoriesql.application.relation_assessment import (
    RELATION_ASSESSMENT_TASK,
    REPAIRABLE_RELATION_ASSESSMENT_TASK,
    load_relation_assessment_task_registry,
    load_repairable_relation_assessment_task_registry,
)

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "migrations"
NAME = "0058_repair_headroom.sql"
SQL = (MIGRATIONS / NAME).read_text() if (MIGRATIONS / NAME).exists() else ""
MODULES = BuiltInModuleRegistry._from_source_controlled(
    (), (ProfileDefinition("core", ()),), {}
)

TASK8 = REPAIRABLE_EXECUTION_TASK.contract_hash
REGISTRY8 = load_repairable_complete_input_task_registry(MODULES).registry_hash
TASK2 = COMPLETE_EXECUTION_TASK.contract_hash
REGISTRY2 = load_complete_input_task_registry(MODULES).registry_hash
OUTPUT = COMPLETE_EXECUTION_TASK.output_contract.schema_hash
RTASK1 = RELATION_ASSESSMENT_TASK.contract_hash
RREGISTRY1 = load_relation_assessment_task_registry(MODULES).registry_hash
RTASK2 = REPAIRABLE_RELATION_ASSESSMENT_TASK.contract_hash
RREGISTRY2 = load_repairable_relation_assessment_task_registry(MODULES).registry_hash


def author(revision: str) -> str:
    return (
        f"CASE WHEN {revision}=2 THEN 'memory.semantic.relation-author.v2'"
        " ELSE 'memory.semantic.relation-author' END"
    )
APPLY_V6 = "'complete_input.apply.v5','complete_input.apply.v6'"
APPLY_V7 = APPLY_V6 + ",'complete_input.apply.v7'"
LIST7 = "(2,3,4,5,6,7)"
LIST8 = "(2,3,4,5,6,7,8)"


def function(text: str, name: str) -> str:
    """The last CREATE [OR REPLACE] FUNCTION statement of `name`, through `$$;`."""
    starts = [
        m.start()
        for m in re.finditer(rf"CREATE (?:OR REPLACE )?FUNCTION memoriesql\.{name}\(", text)
    ]
    assert starts, name
    body = text.index("AS $$", starts[-1]) + len("AS $$")
    return text[starts[-1] : text.index("$$;", body) + len("$$;")]


def installed(name: str) -> tuple[str, str]:
    """(migration, definition) of the newest definition before 0058."""
    found: tuple[str, str] | None = None
    for path in sorted(MIGRATIONS.glob("[0-9][0-9][0-9][0-9]_*.sql")):
        if path.name >= NAME:
            break
        text = path.read_text()
        if re.search(rf"CREATE (?:OR REPLACE )?FUNCTION memoriesql\.{name}\(", text):
            found = (path.name, function(text, name))
    assert found, name
    return found


CLAUSE8 = (
    "            OR (requested_command->>'contract_version'='8' AND requested_command->>'expected_schema_version'='41'\n"
    "                AND requested_command->>'task_kind'='memory.semantic.author-complete-unit' AND requested_command->>'contract_revision'='7'\n"
    "                AND requested_command->>'output_contract_hash'='360953819cc83cc29ccaaa629e4d505aab2e289c0618dd6e42f0a7cc9213c638')\n"
)
CLAUSE9 = (
    "            OR (requested_command->>'contract_version'='9' AND requested_command->>'expected_schema_version'='58'\n"
    "                AND requested_command->>'task_kind'='memory.semantic.author-complete-unit' AND requested_command->>'contract_revision'='8'\n"
    f"                AND requested_command->>'output_contract_hash'='{OUTPUT}')\n"
)

# Function: (its latest installed source, the edits). Each edit is
# (installed text, 0058 text, occurrences).
RESTATED: dict[str, tuple[str, tuple[tuple[str, str, int], ...]]] = {
    "apply_semantic_annotations_schema29": (
        "0057_partial_observation_acceptance.sql",
        (
            (
                "is_complete := requested_command->>'contract_version' IN ('3','4','5','6','7','8');",
                "is_complete := requested_command->>'contract_version' IN ('3','4','5','6','7','8','9');",
                1,
            ),
            (
                "operation_key := CASE WHEN requested_command->>'contract_version'='8' THEN 'complete_input.apply.v6'",
                "operation_key := CASE WHEN requested_command->>'contract_version'='9' THEN 'complete_input.apply.v7'"
                " WHEN requested_command->>'contract_version'='8' THEN 'complete_input.apply.v6'",
                1,
            ),
            (CLAUSE8, CLAUSE8 + CLAUSE9, 1),
            (
                "jsonb_build_object('contract_version', CASE WHEN requested_command->>'contract_version'='8' THEN 8",
                "jsonb_build_object('contract_version', CASE WHEN requested_command->>'contract_version'='9' THEN 9"
                " WHEN requested_command->>'contract_version'='8' THEN 8",
                1,
            ),
        ),
    ),
    "assert_complete_execution_binding": (
        "0041_authored_statement_kinds.sql",
        (
            (
                f"NEW.contract_revision NOT IN {LIST7} THEN RETURN NULL;",
                f"NEW.contract_revision NOT IN {LIST8} THEN RETURN NULL;",
                1,
            ),
            (
                "    IF e.execution_task_id IS NULL OR b.task_id IS NULL OR t.task_id IS NULL",
                "    IF e.execution_contract_revision=8 THEN expected:=jsonb_set(expected,'{contract_revision}','8'); END IF;\n"
                "    IF e.execution_task_id IS NULL OR b.task_id IS NULL OR t.task_id IS NULL",
                1,
            ),
            (
                "t.task_contract_hash IS DISTINCT FROM (CASE WHEN e.execution_contract_revision=7 THEN",
                f"t.task_contract_hash IS DISTINCT FROM (CASE WHEN e.execution_contract_revision=8 THEN '{TASK8}'"
                " WHEN e.execution_contract_revision=7 THEN",
                1,
            ),
            (
                "t.semantic_registry_hash IS DISTINCT FROM (CASE WHEN e.execution_contract_revision=7 THEN",
                f"t.semantic_registry_hash IS DISTINCT FROM (CASE WHEN e.execution_contract_revision=8 THEN '{REGISTRY8}'"
                " WHEN e.execution_contract_revision=7 THEN",
                1,
            ),
        ),
    ),
    "consume_supervised_dispatch": (
        "0054_supervised_reapproval.sql",
        (
            (
                f"(i->>'task_contract_revision')::integer IN {LIST7})",
                f"(i->>'task_contract_revision')::integer IN {LIST8})",
                1,
            ),
            (
                "(i->>'task_kind'='memory.semantic.assess-relations' AND (i->>'task_contract_revision')::integer=1)",
                "(i->>'task_kind'='memory.semantic.assess-relations' AND (i->>'task_contract_revision')::integer IN (1,2))",
                1,
            ),
        ),
    ),
    "guard_complete_unit_execution": (
        "0041_authored_statement_kinds.sql",
        (
            (f"ELSIF NEW.contract_revision IN {LIST7} THEN", f"ELSIF NEW.contract_revision IN {LIST8} THEN", 1),
            (APPLY_V6 + ")", APPLY_V7 + ")", 2),
            (
                f"(t.contract_revision NOT IN {LIST7} OR NOT EXISTS",
                f"(t.contract_revision NOT IN {LIST8} OR NOT EXISTS",
                1,
            ),
            (
                f"OR t.contract_revision NOT IN {LIST7} OR t.status<>'running'",
                f"OR t.contract_revision NOT IN {LIST8} OR t.status<>'running'",
                1,
            ),
        ),
    ),
    "inspect_stored_bead_v1": (
        "0043_inspection_withholding.sql",
        ((APPLY_V6 + ")", APPLY_V7 + ")", 1),),
    ),
    "reauthorize_semantic_task": (
        "0041_authored_statement_kinds.sql",
        (
            (
                f"task_record.contract_revision IN {LIST7} THEN",
                f"task_record.contract_revision IN {LIST8} THEN",
                1,
            ),
            (
                "IF task_record.task_kind='memory.semantic.assess-relations' AND task_record.contract_revision=1 THEN",
                "IF task_record.task_kind='memory.semantic.assess-relations' AND task_record.contract_revision IN (1,2) THEN",
                1,
            ),
        ),
    ),
    "record_complete_input_exposure_v1": (
        "0020_author_controlled_source_revisiting.sql",
        (("IF e.execution_contract_revision<>2 OR", "IF e.execution_contract_revision NOT IN (2,8) OR", 1),),
    ),
    "semantic_task_input_reference_safe": (
        "0041_authored_statement_kinds.sql",
        (
            (
                "RETURN candidate->>'contract_revision'='1' AND octet_length(candidate::text)<=262144",
                "RETURN candidate->>'contract_revision' IN ('1','2') AND octet_length(candidate::text)<=262144",
                1,
            ),
            (
                "IF candidate->>'task_kind'='memory.semantic.author-complete-unit' AND candidate->>'contract_revision'='2' THEN",
                "IF candidate->>'task_kind'='memory.semantic.author-complete-unit' AND candidate->>'contract_revision' IN ('2','8') THEN",
                1,
            ),
        ),
    ),
    "apply_relation_assessment_v1": (
        "0030_assessed_relation_lifecycle.sql",
        (
            (
                "       OR requested_command->>'contract_version' IS DISTINCT FROM '1'\n"
                "       OR requested_command->>'expected_schema_version' IS DISTINCT FROM '29'\n"
                "       OR requested_command->>'task_kind' IS DISTINCT FROM 'memory.semantic.assess-relations'\n"
                "       OR requested_command->>'contract_revision' IS DISTINCT FROM '1'\n",
                "       OR NOT ((requested_command->>'contract_version' IS NOT DISTINCT FROM '1'\n"
                "                AND requested_command->>'expected_schema_version' IS NOT DISTINCT FROM '29'\n"
                "                AND requested_command->>'contract_revision' IS NOT DISTINCT FROM '1')\n"
                "            OR (requested_command->>'contract_version' IS NOT DISTINCT FROM '2'\n"
                "                AND requested_command->>'expected_schema_version' IS NOT DISTINCT FROM '58'\n"
                "                AND requested_command->>'contract_revision' IS NOT DISTINCT FROM '2'))\n"
                "       OR requested_command->>'task_kind' IS DISTINCT FROM 'memory.semantic.assess-relations'\n",
                1,
            ),
            (
                "      AND q.contract_revision = 1;",
                "      AND q.contract_revision = (requested_command->>'contract_revision')::integer;",
                1,
            ),
            (
                "AND r.parent_run_id IS NULL AND r.run_status = 'running' AND r.agent_key = 'memory.semantic.relation-author';",
                "AND r.parent_run_id IS NULL AND r.run_status = 'running'\n"
                "      AND r.agent_key = " + author("(requested_command->>'contract_revision')::integer") + ";",
                1,
            ),
        ),
    ),
    "record_relation_delivery_v1": (
        "0029_relation_assessment.sql",
        (
            (
                "OR q.task_kind IS DISTINCT FROM 'memory.semantic.assess-relations' OR q.contract_revision <> 1",
                "OR q.task_kind IS DISTINCT FROM 'memory.semantic.assess-relations' OR q.contract_revision NOT IN (1,2)",
                1,
            ),
            (
                "OR run.agent_key IS DISTINCT FROM 'memory.semantic.relation-author'))",
                "OR run.agent_key IS DISTINCT FROM " + author("q.contract_revision") + "))",
                1,
            ),
        ),
    ),
    "record_semantic_run_event": (
        "0029_relation_assessment.sql",
        (
            (
                "AND task_record.task_kind='memory.semantic.assess-relations' AND task_record.contract_revision=1",
                "AND task_record.task_kind='memory.semantic.assess-relations' AND task_record.contract_revision IN (1,2)",
                1,
            ),
        ),
    ),
}

# Relation revision 2's activation is revision 1's (0029) with its own command
# version, schema, revision, operation and pins.
RELATION_ACTIVATION: tuple[tuple[str, str, int], ...] = (
    (
        "CREATE FUNCTION memoriesql.activate_relation_assessment_v1(",
        "CREATE FUNCTION memoriesql.activate_relation_assessment_v2(",
        1,
    ),
    (
        "OR request->>'contract_version' IS DISTINCT FROM '1' OR request->>'expected_schema_version' IS DISTINCT FROM '29'",
        "OR request->>'contract_version' IS DISTINCT FROM '2' OR request->>'expected_schema_version' IS DISTINCT FROM '58'",
        1,
    ),
    ("'relation_assessment.activate.v1'", "'relation_assessment.activate.v2'", 2),
    (
        "input := jsonb_build_object('task_id', tid, 'task_kind', 'memory.semantic.assess-relations', 'contract_revision', 1,",
        "input := jsonb_build_object('task_id', tid, 'task_kind', 'memory.semantic.assess-relations', 'contract_revision', 2,",
        1,
    ),
    (
        f"'relation-assessment.v1:' || tid::text, 'memoriesql.kernel', 'memory.semantic.assess-relations', 1,\n"
        f"        '{RTASK1}', '{RREGISTRY1}'",
        f"'relation-assessment.v2:' || tid::text, 'memoriesql.kernel', 'memory.semantic.assess-relations', 2,\n"
        f"        '{RTASK2}', '{RREGISTRY2}'",
        1,
    ),
    (
        "result := jsonb_build_object('contract_version', 1, 'task_id', tid,",
        "result := jsonb_build_object('contract_version', 2, 'task_id', tid,",
        1,
    ),
)
RELATION_RESTATED = {
    "apply_relation_assessment_v1", "record_relation_delivery_v1", "record_semantic_run_event",
    "reauthorize_semantic_task", "consume_supervised_dispatch", "semantic_task_input_reference_safe",
}

# Revision 8's activation is revision 2's (activate_complete_input_v1, 0020)
# with its own command version, schema, revision, operation, pins and a
# dispatch policy provisioned for revision 8.
ACTIVATION: tuple[tuple[str, str, int], ...] = (
    (
        "CREATE OR REPLACE FUNCTION memoriesql.activate_complete_input_v1(",
        "CREATE FUNCTION memoriesql.activate_complete_input_v7(",
        1,
    ),
    (
        "IF request->>'contract_version' IS DISTINCT FROM '1' OR request->>'expected_schema_version' IS DISTINCT FROM '18' OR",
        "IF request->>'contract_version' IS DISTINCT FROM '7' OR request->>'expected_schema_version' IS DISTINCT FROM '58' OR",
        1,
    ),
    (
        "    b:=memoriesql.complete_input_authorize(c.tenant_id,b.task_id,(request->>'dispatch_policy_id')::uuid);\n"
        "    request_hash:=",
        "    b:=memoriesql.complete_input_authorize(c.tenant_id,b.task_id,(request->>'dispatch_policy_id')::uuid);\n"
        "    -- Revision 8 runs only under a dispatch policy provisioned for it.\n"
        "    IF NOT EXISTS(SELECT 1 FROM memoriesql.complete_input_dispatch_policies d WHERE d.tenant_id=c.tenant_id"
        " AND d.dispatch_policy_id=(request->>'dispatch_policy_id')::uuid AND d.execution_contract_revision=8) THEN\n"
        "        RAISE EXCEPTION 'complete_input_activation_denied' USING ERRCODE='42501'; END IF;\n"
        "    request_hash:=",
        1,
    ),
    ("'complete_input.activate.v1'", "'complete_input.activate.v7'", 2),
    ("IF e.execution_contract_revision<>2 OR", "IF e.execution_contract_revision<>8 OR", 1),
    (
        "INSERT INTO memoriesql.complete_input_executions VALUES(",
        "INSERT INTO memoriesql.complete_input_executions(tenant_id,binding_task_id,execution_task_id,"
        "dispatch_policy_id,source_schema_version,activation_receipt_id,execution_contract_revision) VALUES(",
        1,
    ),
    ("p.source_object_id),rid);", "p.source_object_id),rid,8);", 1),
    (
        "'task_kind','memory.semantic.author-complete-unit','contract_revision',2,",
        "'task_kind','memory.semantic.author-complete-unit','contract_revision',8,",
        1,
    ),
    (
        f"'complete-input.v1:'||b.task_id::text,'memoriesql.kernel','memory.semantic.author-complete-unit',2,'{TASK2}','{REGISTRY2}'",
        f"'complete-input.v7:'||b.task_id::text,'memoriesql.kernel','memory.semantic.author-complete-unit',8,'{TASK8}','{REGISTRY8}'",
        1,
    ),
    ("result:=jsonb_build_object('contract_version',1,", "result:=jsonb_build_object('contract_version',7,", 1),
)


def edited(text: str, edits: tuple[tuple[str, str, int], ...]) -> str:
    for old, new, count in edits:
        assert text.count(old) == count, (old, text.count(old), count)
        text = text.replace(old, new)
    return text


class RepairHeadroomMigration(unittest.TestCase):
    def test_each_restatement_is_installed_text_plus_listed_edits(self) -> None:
        for name, (source, edits) in RESTATED.items():
            with self.subTest(name=name):
                migration, before = installed(name)
                self.assertEqual(migration, source)
                expected = edited(before, edits).replace(
                    "CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1
                )
                self.assertEqual(function(SQL, name), expected)

    def test_no_other_function_lists_revision_two_beside_seven(self) -> None:
        # Every installed function that names revision 2 with revision 7, the
        # apply operation list or the apply command versions is restated; none
        # is left behind. Revision 2's own activation stays as it is.
        latest: dict[str, str] = {}
        for path in sorted(MIGRATIONS.glob("[0-9][0-9][0-9][0-9]_*.sql")):
            if path.name >= NAME:
                break
            text = path.read_text()
            for match in re.finditer(r"CREATE (?:OR REPLACE )?FUNCTION memoriesql\.([a-z0-9_]+)\(", text):
                latest[match.group(1)] = function(text, match.group(1))
        complete = {
            name
            for name, body in latest.items()
            if LIST7 in body
            or APPLY_V6 + ")" in body
            or "execution_contract_revision<>2" in body
            or "is_complete :=" in body
            or "author-complete-unit' AND candidate->>'contract_revision'='2'" in body
        }
        relation = {
            name
            for name, body in latest.items()
            if "memory.semantic.assess-relations" in body or "memory.semantic.relation-author" in body
        }
        self.assertEqual(
            complete | relation,
            set(RESTATED) | {"activate_complete_input_v1", "activate_relation_assessment_v1"},
        )
        self.assertEqual(relation, RELATION_RESTATED | {"activate_relation_assessment_v1"})

    def test_revision_eight_activation_is_revision_two_plus_listed_edits(self) -> None:
        _, before = installed("activate_complete_input_v1")
        self.assertEqual(function(SQL, "activate_complete_input_v7"), edited(before, ACTIVATION))
        text = " ".join(SQL.split())
        self.assertIn(
            "REVOKE ALL ON FUNCTION memoriesql.activate_complete_input_v7(jsonb) FROM PUBLIC;", text
        )
        self.assertEqual(
            re.findall(r"GRANT EXECUTE ON FUNCTION memoriesql\.([a-z0-9_]+)\([^)]*\) TO (\w+);", SQL),
            [
                ("activate_complete_input_v7", "memoriesql_application"),
                ("activate_relation_assessment_v2", "memoriesql_application"),
            ],
        )

    def test_relation_revision_two_activation_is_revision_one_plus_listed_edits(self) -> None:
        _, before = installed("activate_relation_assessment_v1")
        self.assertEqual(
            function(SQL, "activate_relation_assessment_v2"), edited(before, RELATION_ACTIVATION)
        )
        self.assertIn(
            "REVOKE ALL ON FUNCTION memoriesql.activate_relation_assessment_v2(jsonb) FROM PUBLIC;",
            " ".join(SQL.split()),
        )

    def test_revision_eight_is_admitted_beside_revision_two(self) -> None:
        text = " ".join(SQL.split())
        for table, constraint in (
            ("complete_input_executions", "complete_input_executions_execution_contract_revision_check"),
            ("complete_input_dispatch_policies", "complete_input_dispatch_polic_execution_contract_revision_check"),
        ):
            with self.subTest(table=table):
                self.assertIn(
                    f"ALTER TABLE memoriesql.{table} DROP CONSTRAINT {constraint}, ADD CONSTRAINT {constraint}"
                    " CHECK (execution_contract_revision IN (2, 3, 4, 5, 6, 7, 8));",
                    text,
                )
        self.assertIn(
            f"VALUES ('{REGISTRY8}','memory.semantic.author-complete-unit',8,'memoriesql.kernel','{TASK8}',"
            "'canonical_semantics','memory.capture','capture',50,3,NULL,NULL);",
            text,
        )
        self.assertIn(
            f"VALUES ('{RREGISTRY2}','memory.semantic.assess-relations',2,'memoriesql.kernel','{RTASK2}',"
            "'canonical_semantics','memory.maintain','capture',50,1,NULL,NULL);",
            text,
        )
        # The statements of this file: CHECKs, the admission row, the
        # restatements, the activation and its ACL; nothing else.
        self.assertEqual(
            sorted(re.findall(r"^(CREATE (?:OR REPLACE )?FUNCTION memoriesql\.[a-z0-9_]+)", SQL, re.M)),
            sorted(
                [f"CREATE OR REPLACE FUNCTION memoriesql.{name}" for name in RESTATED]
                + ["CREATE FUNCTION memoriesql.activate_complete_input_v7",
                   "CREATE FUNCTION memoriesql.activate_relation_assessment_v2"]
            ),
        )

    def test_revision_eight_is_revision_two_with_one_more_request(self) -> None:
        r2, r8 = COMPLETE_EXECUTION_TASK, REPAIRABLE_EXECUTION_TASK
        self.assertEqual((r2.run_budget.request_limit, r8.run_budget.request_limit), (8, 9))
        self.assertEqual(
            r8.run_budget.model_copy(update={"request_limit": 8}), r2.run_budget
        )
        self.assertEqual(r8.output_contract, r2.output_contract)
        self.assertEqual(
            (r8.contract_revision, r8.input_contract.revision, r8.leaf_agent_key),
            (8, 2, "memory.semantic.complete-unit-author.v2"),
        )
        for field in (
            "task_kind", "owning_module", "dispatch_mode", "default_effort_key",
            "maximum_effort_key", "model_profile", "evidence_policy_key",
        ):
            with self.subTest(field=field):
                self.assertEqual(getattr(r8, field), getattr(r2, field))

    def test_relation_revision_two_is_revision_one_with_one_more_request(self) -> None:
        r1, r2 = RELATION_ASSESSMENT_TASK, REPAIRABLE_RELATION_ASSESSMENT_TASK
        self.assertEqual((r1.run_budget.request_limit, r2.run_budget.request_limit), (2, 3))
        self.assertEqual(r2.run_budget.model_copy(update={"request_limit": 2}), r1.run_budget)
        self.assertEqual(r2.output_contract, r1.output_contract)
        self.assertEqual(
            (r2.contract_revision, r2.input_contract.revision, r2.leaf_agent_key),
            (2, 2, "memory.semantic.relation-author.v2"),
        )
        for field in (
            "task_kind", "owning_module", "dispatch_mode", "default_effort_key",
            "maximum_effort_key", "model_profile", "evidence_policy_key", "allowed_delegate_keys",
        ):
            with self.subTest(field=field):
                self.assertEqual(getattr(r2, field), getattr(r1, field))

    def test_inventory_pins_the_migration(self) -> None:
        rows = json.loads((ROOT / "contracts/migration-inventory.json").read_text())["migrations"]
        self.assertEqual([int(r["filename"][:4]) for r in rows], list(range(1, len(rows) + 1)))
        self.assertEqual(rows[57]["filename"], NAME)
        self.assertEqual(rows[57]["sha256"], hashlib.sha256((MIGRATIONS / NAME).read_bytes()).hexdigest())


if __name__ == "__main__":
    unittest.main()
