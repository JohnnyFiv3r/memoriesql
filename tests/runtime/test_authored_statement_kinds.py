"""Fictional author-complete-unit revision 7 through the existing revisiting worker.

Revision 7 offers authors only the statement kinds a new note can carry. A
correction heard in the source is authored as an observation of what is now
said to be true, with the earlier belief as context.
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
    CORRECTION_IN_SOURCE,
    EVERY_STATEMENT_RENDERED,
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

    def test_a_corrected_fact_is_an_observation_with_its_earlier_belief_as_context(
        self,
    ) -> None:
        self.setup_mentions()

        def author(messages: Any, info: Any) -> Any:
            response = self.response(messages, info)
            part = cast(ToolCallPart, response.parts[0])
            data = part.args_as_dict()
            if data.get("typed_output") is not None:
                annotation = data["typed_output"]["annotations"][0]
                now = annotation["statements"][0]
                now["statement_text"] = "Alex now says there are five fictional trees."
                earlier = dict(
                    now,
                    statement_id=str(uuid.uuid4()),
                    statement_kind="context",
                    statement_text="Alex had first counted four fictional trees.",
                )
                annotation["statements"].append(earlier)
                annotation["render"]["summary"].append(
                    {"text": "Earlier, four.", "statement_ids": [earlier["statement_id"]]}
                )
            part.args = data
            return response

        result = asyncio.run(self.worker(author).run_once())
        self.assertEqual(result.task_status, "succeeded", result)
        # The author was offered exactly the kinds a new note can carry, with
        # the correction and coverage rules stated in the schema.
        self.assertTrue(self.offered)
        for schema in self.offered:
            self.assertEqual(
                sorted(set(KINDS.findall(schema))),
                ["context", "observation", "qualification"],
            )
            self.assertIn(CORRECTION_IN_SOURCE, schema)
            self.assertIn(EVERY_STATEMENT_RENDERED, schema)
        self.assertEqual(self.receipt_revision(), (7,))
        kinds = sorted(
            str(row[0])
            for row in self.db.execute(
                "SELECT statement_kind FROM memoriesql.bead_semantic_statements"
            ).fetchall()
        )
        self.assertEqual(kinds, ["context", "observation"])
        self.assertEqual(
            self.row("SELECT count(*) FROM memoriesql.entity_mentions"), (1,)
        )
        # Inspection reads revision 7's mentions as it reads revision 4's.
        bead = self.row("SELECT bead_id FROM memoriesql.bead_versions")[0]
        read = PostgresStoredBeadInspection(
            self.db, credential_sha256=self.secret_hash, workspace_id=self.workspace
        ).inspect(InspectStoredBead(bead_id=bead))
        assert read.bead and read.bead.meaning
        self.assertEqual(len(read.bead.meaning.mentions or ()), 1)

    def test_an_output_with_the_correction_kind_is_refused_and_writes_nothing(
        self,
    ) -> None:
        # Revision 7 does not offer `correction`. An author that returns it
        # anyway fails the output schema; no meaning is written.
        self.setup_mentions()

        def author(messages: Any, info: Any) -> Any:
            response = self.response(messages, info)
            part = cast(ToolCallPart, response.parts[0])
            data = part.args_as_dict()
            if data.get("typed_output") is not None:
                statement = data["typed_output"]["annotations"][0]["statements"][0]
                statement["statement_kind"] = "correction"
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
