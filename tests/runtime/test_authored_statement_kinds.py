"""Fictional author-complete-unit revision 7 through the existing revisiting worker.

In revision 7 a correction supersedes an earlier statement of the same note.
The superseded statement keeps the prior meaning, and the render covers exactly
the statements no correction supersedes.
"""

from __future__ import annotations

import asyncio
import json
import re
import unittest
import uuid
from typing import TYPE_CHECKING, Any, cast

from pydantic_ai.messages import ToolCallPart

from memoriesql.application.local_entity_mentions import (
    AUTHORED_MENTION_TASK,
    CORRECTION_WITHIN_NOTE,
    EVERY_CURRENT_STATEMENT_RENDERED,
    MENTION_EXECUTION_TASK,
    ActivateAuthoredMentions,
    ActivateMentionAuthorship,
    load_authored_mentions_task_registry,
    load_local_mentions_task_registry,
)
from memoriesql.application.stored_bead_inspection import InspectStoredBead
from memoriesql.infrastructure.postgres.stored_bead_inspection import (
    PostgresStoredBeadInspection,
)

if TYPE_CHECKING:
    from tests.runtime import test_local_entity_mentions as fixtures
    from tests.runtime import test_source_revisiting as source_fixtures
    from tests.runtime.test_postgres_runtime import migrate
else:
    import test_local_entity_mentions as fixtures
    import test_source_revisiting as source_fixtures
    from test_postgres_runtime import migrate

KINDS = re.compile(r'"(observation|context|qualification|correction)"')


class AuthoredStatementKinds(fixtures.LocalMentions):
    def setUp(self) -> None:
        super().setUp()
        migrate(self.db, expected_current_version=22, target_version=41)
        self.revision_four_policy = self.dispatch_policy
        self.dispatch_policy = uuid.uuid4()
        self.db.execute(
            "INSERT INTO memoriesql.complete_input_dispatch_policies SELECT tenant_id,%s,workspace_id,access_scope_id,source_object_id,attestor_principal_id,approved_by_principal_id,qualification_evidence_sha256,created_at,expires_at,status,7 FROM memoriesql.complete_input_dispatch_policies WHERE dispatch_policy_id=%s",
            (self.dispatch_policy, self.revision_four_policy),
        )
        self.db.execute(
            "INSERT INTO memoriesql.semantic_worker_claim_policies SELECT c.tenant_id,c.workspace_id,c.principal_id,c.pairing_grant_id,p.semantic_registry_hash,p.task_kind,p.contract_revision,p.queue_name FROM memoriesql.semantic_worker_claim_policies c JOIN memoriesql.semantic_task_admission_policies p ON p.task_kind=c.task_kind AND p.contract_revision=7 WHERE c.principal_id=%s AND c.contract_revision=4",
            (self.worker_principal,),
        )
        self.revision = 7
        self.offered: list[str] = []

    def setup_mentions(self) -> None:
        self.setup_revisiting(
            "Alex counted four fictional trees, then said it was five.",
            activate=False,
        )
        if self.revision == 4:
            command = ActivateMentionAuthorship(
                idempotency_key="orchard.mentions.activate",
                binding_task_id=self.bound.task_id,
                dispatch_policy_id=self.revision_four_policy,
            )
            self.activation = self.complete.activate_mentions(command)
            return
        authored = ActivateAuthoredMentions(
            idempotency_key="orchard.mentions.activate.v7",
            binding_task_id=self.bound.task_id,
            dispatch_policy_id=self.dispatch_policy,
        )
        self.activation = self.complete.activate_authored_mentions(authored)
        self.assertEqual(
            self.complete.activate_authored_mentions(authored).execution_task_id,
            self.activation.execution_task_id,
        )

    def worker(
        self, callback: Any = None, *, recorder: Any = True, **kwargs: Any
    ) -> Any:
        revision_four = self.revision == 4
        return source_fixtures.SourceRevisiting.worker(
            self,
            callback,
            recorder=recorder,
            task_definition=(
                MENTION_EXECUTION_TASK if revision_four else AUTHORED_MENTION_TASK
            ),
            registry_factory=(
                load_local_mentions_task_registry
                if revision_four
                else load_authored_mentions_task_registry
            ),
        )

    def response(self, messages: Any, info: Any) -> Any:
        self.offered.append(json.dumps(info.output_tools[0].parameters_json_schema))
        return super().response(messages, info)

    def receipt_revision(self) -> tuple[Any, ...]:
        return self.row(
            "SELECT r.task_contract_version FROM memoriesql.bead_versions v JOIN memoriesql.semantic_task_receipts r USING(tenant_id,semantic_task_receipt_id)"
        )

    def test_a_correction_supersedes_an_earlier_statement_of_its_note(self) -> None:
        # Owner decision 3 (iv): Alex first counts four trees, then corrects the
        # count. The earlier statement keeps the prior meaning, the correction
        # names it with the reason, and only the correction is rendered.
        self.setup_mentions()
        ids: dict[str, str] = {}

        def author(messages: Any, info: Any) -> Any:
            response = self.response(messages, info)
            part = cast(ToolCallPart, response.parts[0])
            data = part.args_as_dict()
            if data.get("typed_output") is not None:
                annotation = data["typed_output"]["annotations"][0]
                earlier = annotation["statements"][0]
                earlier["statement_text"] = "Alex first counted four fictional trees."
                correction = dict(
                    earlier,
                    statement_id=str(uuid.uuid4()),
                    statement_kind="correction",
                    statement_text="Alex says there are five fictional trees.",
                    supersedes_statement_id=earlier["statement_id"],
                    correction_reason="Alex corrected the count.",
                )
                annotation["statements"].append(correction)
                render = annotation["render"]
                for clause in [render["title"], *render["summary"], *render.get("detail", [])]:
                    clause["statement_ids"] = [correction["statement_id"]]
                render["omissions"] = []
                ids.update(earlier=earlier["statement_id"], correction=correction["statement_id"])
            part.args = data
            return response

        result = asyncio.run(self.worker(author).run_once())
        self.assertEqual(result.task_status, "succeeded", result)
        # The author was offered the correction of an earlier statement, with
        # both rules stated in the schema.
        self.assertTrue(self.offered)
        for schema in self.offered:
            self.assertEqual(
                sorted(set(KINDS.findall(schema))),
                ["context", "correction", "observation", "qualification"],
            )
            self.assertIn(CORRECTION_WITHIN_NOTE, schema)
            self.assertIn(EVERY_CURRENT_STATEMENT_RENDERED, schema)
        self.assertEqual(self.receipt_revision(), (7,))
        stored = [
            tuple(None if value is None else str(value) for value in row)
            for row in self.db.execute(
                "SELECT statement_id, statement_kind, supersedes_statement_id, correction_reason"
                " FROM memoriesql.bead_semantic_statements ORDER BY statement_sequence"
            ).fetchall()
        ]
        self.assertEqual(
            stored,
            [
                (ids["earlier"], "observation", None, None),
                (ids["correction"], "correction", ids["earlier"], "Alex corrected the count."),
            ],
        )
        # Inspection keeps the prior meaning and the correction's lineage.
        bead = self.row("SELECT bead_id FROM memoriesql.bead_versions")[0]
        read = PostgresStoredBeadInspection(
            self.db, credential_sha256=self.secret_hash, workspace_id=self.workspace
        ).inspect(InspectStoredBead(bead_id=bead))
        assert read.bead and read.bead.meaning
        statements = {str(s.statement_id): s for s in read.bead.meaning.statements}
        self.assertEqual(
            statements[ids["earlier"]].text, "Alex first counted four fictional trees."
        )
        self.assertEqual(
            str(statements[ids["correction"]].supersedes_statement_id), ids["earlier"]
        )
        self.assertEqual(
            statements[ids["correction"]].correction_reason, "Alex corrected the count."
        )
        self.assertEqual(len(read.bead.meaning.mentions or ()), 1)

    def test_a_correction_outside_its_note_is_refused_and_writes_nothing(
        self,
    ) -> None:
        # A correction that names no earlier statement of its note fails the
        # output contract; no meaning is written and no note is flattened.
        self.setup_mentions()

        def author(messages: Any, info: Any) -> Any:
            response = self.response(messages, info)
            part = cast(ToolCallPart, response.parts[0])
            data = part.args_as_dict()
            if data.get("typed_output") is not None:
                annotation = data["typed_output"]["annotations"][0]
                statement = annotation["statements"][0]
                annotation["statements"].append(
                    dict(
                        statement,
                        statement_id=str(uuid.uuid4()),
                        statement_kind="correction",
                        statement_text="Alex says there are five fictional trees.",
                        supersedes_statement_id=str(uuid.uuid4()),
                        correction_reason="Alex corrected an earlier count.",
                    )
                )
            part.args = data
            return response

        result = asyncio.run(self.worker(author).run_once())
        self.assertNotEqual(result.task_status, "succeeded", result)
        self.assertEqual(
            self.row(
                "SELECT status, error_code FROM memoriesql.semantic_task_attempts"
            ),
            ("terminal_failure", "runtime.invalid_output"),
        )
        self.assert_no_meaning()

    def test_a_note_with_a_context_source_is_inspectable(self) -> None:
        # A context source is a unit of the statement's own evidence. Inspection
        # authorizes it through its event's source object, as it does evidence.
        # Before, it read the unit ID as a source object, and every such note
        # inspected as unavailable (revisions 4 and 7 alike).
        self.setup_mentions()
        named: list[str] = []

        def author(messages: Any, info: Any) -> Any:
            response = self.response(messages, info)
            part = cast(ToolCallPart, response.parts[0])
            data = part.args_as_dict()
            if data.get("typed_output") is not None:
                annotation = data["typed_output"]["annotations"][0]
                now = annotation["statements"][0]
                unit = now["evidence"][0]["source_unit_id"]
                named.append(unit)
                context = dict(
                    now,
                    statement_id=str(uuid.uuid4()),
                    statement_kind="context",
                    statement_text="Alex had first counted four fictional trees.",
                    context_source_ids=[unit],
                )
                annotation["statements"].append(context)
                annotation["render"]["summary"].append(
                    {"text": "Earlier, four.", "statement_ids": [context["statement_id"]]}
                )
            part.args = data
            return response

        result = asyncio.run(self.worker(author).run_once())
        self.assertEqual(result.task_status, "succeeded", result)
        bead = self.row("SELECT bead_id FROM memoriesql.bead_versions")[0]
        reader = PostgresStoredBeadInspection(
            self.db, credential_sha256=self.secret_hash, workspace_id=self.workspace
        )
        read = reader.inspect(InspectStoredBead(bead_id=bead))
        assert read.bead and read.bead.meaning
        self.assertEqual(
            sorted(s.kind for s in read.bead.meaning.statements), ["context", "observation"]
        )
        # Unreadable, the note reads exactly as one that does not exist.
        source = self.row(
            "SELECT e.source_object_id FROM memoriesql.source_units u"
            " JOIN memoriesql.source_events e ON e.tenant_id=u.tenant_id AND e.event_id=u.event_id"
            " WHERE u.source_unit_id=%s",
            (named[0],),
        )[0]
        self.db.execute(
            "UPDATE memoriesql.protected_resources SET status='revoked',"
            "revoked_at=clock_timestamp() WHERE resource_id=%s",
            (source,),
        )
        hidden = reader.inspect(InspectStoredBead(bead_id=bead))
        missing = reader.inspect(InspectStoredBead(bead_id=uuid.uuid4()))
        self.assertEqual(hidden, missing)
        self.assertIsNone(hidden.bead)

    def test_revision_4_still_authors_at_schema_41(self) -> None:
        # Migration 0041 restates revision 4's functions with revision 7 added
        # beside it; revision 4 itself is unchanged.
        self.revision = 4
        self.setup_mentions()
        result = asyncio.run(self.worker().run_once())
        self.assertEqual(result.task_status, "succeeded", result)
        self.assertEqual(self.receipt_revision(), (4,))
        self.assertEqual(
            self.row("SELECT count(*) FROM memoriesql.entity_mentions"), (1,)
        )


def load_tests(loader: Any, standard_tests: Any, pattern: Any) -> Any:
    # Reuse the fixtures without rerunning every inherited test.
    return unittest.TestSuite(
        AuthoredStatementKinds(name)
        for name in AuthoredStatementKinds.__dict__
        if name.startswith("test_")
    )
