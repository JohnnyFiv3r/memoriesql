"""The relation author's repair round at head schema (guard audit P1-f D2).

Fictional data in a disposable database, composing the relation-assessment
fixture. assess-relations revision 1 spends its two requests on the author and
the batched specialist, so an author that misses a pair's disposition settles
invalid_output. Revision 2 has one more request: the author gets one
evidence-free repair frame, the packet it already received without its
evidence, and may only complete the missing pair. The repair records no
delivery; the specialist then judges the unchanged proposals exactly as today.
"""

from __future__ import annotations

import asyncio
import json
import os
import unittest
from contextlib import ExitStack
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import patch

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    UserPromptPart,
)

from memoriesql.application.relation_assessment import (
    REPAIRABLE_RELATION_ASSESSMENT_TASK,
    ActivateRepairableRelationAssessment,
    load_repairable_relation_assessment_task_registry,
)
from memoriesql.infrastructure.postgres.migration_runner import discover_migrations

if TYPE_CHECKING:
    from tests.runtime import test_relation_assessment as relation
    from tests.runtime.test_postgres_runtime import migrate
else:
    import test_relation_assessment as relation
    from test_postgres_runtime import migrate

NOT_ASSESSED = "Not assessed in the repair round; no evidence was shown."


@unittest.skipUnless(
    os.environ.get("N1_TEST_DATABASE_URL"), "disposable PostgreSQL required"
)
class RelationRepairRound(unittest.TestCase):
    def setUp(self) -> None:
        self.h = relation.RelationAssessment(methodName="runTest")
        self.addCleanup(self.h.doCleanups)
        self.h.setUp()
        h = self.h
        self.subject = h.bead(
            "Fictional change: the irrigation timer moved to dawn.",
            "Fictional regression: the east rows dried out.",
            key="subject",
        )
        self.candidate = h.bead("The fictional orchard has one gate.", key="candidate")
        migrate(h.db, expected_current_version=29, target_version=discover_migrations()[-1].version)
        h.db.execute(
            "INSERT INTO memoriesql.semantic_worker_claim_policies SELECT c.tenant_id,c.workspace_id,"
            "c.principal_id,c.pairing_grant_id,p.semantic_registry_hash,p.task_kind,p.contract_revision,"
            "p.queue_name FROM memoriesql.semantic_worker_claim_policies c JOIN "
            "memoriesql.semantic_task_admission_policies p ON p.task_kind=c.task_kind AND "
            "p.contract_revision=2 WHERE c.principal_id=%s AND c.task_kind='memory.semantic.assess-relations' "
            "AND c.contract_revision=1",
            (h.worker_principal,),
        )
        h.propose(lambda packet: [h.proposal(
            packet, relation.VOCABULARY[0].key, h.endpoint(packet, self.subject, 1),
            h.endpoint(packet, self.subject, 0), basis="agent_inferred",
        )])
        self.prompts: list[dict[str, Any]] = []

    def activate(self, revision: int) -> Any:
        h = self.h
        if revision == 1:
            self.task = h.activate_relations(self.subject, (self.candidate,)).task_id
        else:
            self.task = h.assessments.activate_repairable(
                ActivateRepairableRelationAssessment(
                    idempotency_key="orchard.assess.v2",
                    subject_bead_id=self.subject,
                    candidate_bead_ids=(self.candidate,),
                    relation_vocabulary=relation.VOCABULARY,
                    dispatch_policy_id=h.relation_policy,
                )
            ).task_id
        return self.task

    def execute(self, revision: int, repair: Any) -> Any:
        """The author misses the candidate's own pair; `repair` edits its repaired output."""
        h = self.h
        missing = {"first_bead_id": str(self.candidate), "second_bead_id": str(self.candidate)}

        def respond(messages: Any, info: Any) -> Any:
            prompts = [
                p.content
                for m in messages
                if isinstance(m, ModelRequest)
                for p in m.parts
                if isinstance(p, UserPromptPart)
            ]
            frame = json.loads(cast(str, prompts[0]))
            self.prompts.append(frame)
            if "repair_frame" in frame:
                previous = frame["previous_output"]
                repair(previous["typed_output"])
                return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, previous)])
            response = relation.RelationAssessment.relation_response(h, messages, info)
            part = response.parts[0]
            assert isinstance(part, ToolCallPart)
            data: Any = part.args
            if frame["relation_packet"]["role"] == "author":
                data["typed_output"]["dispositions"] = [
                    d for d in data["typed_output"]["dispositions"]
                    if {k: d[k] for k in missing} != missing
                ]
            return response

        with ExitStack() as stack:
            stack.enter_context(patch.object(h, "relation_response", respond))
            if revision == 2:
                stack.enter_context(patch.object(relation, "RELATION_ASSESSMENT_TASK", REPAIRABLE_RELATION_ASSESSMENT_TASK))
                stack.enter_context(patch.object(
                    relation, "load_relation_assessment_task_registry",
                    load_repairable_relation_assessment_task_registry,
                ))
            return asyncio.run(h.relation_worker().run_once())

    def complete(self, output: dict[str, Any]) -> None:
        output["dispositions"].append({
            "first_bead_id": str(self.candidate), "second_bead_id": str(self.candidate),
            "disposition": "not_assessed", "abstention": None, "reason": NOT_ASSESSED,
        })

    def counted(self, sql: str) -> int:
        return int(self.h.row(sql, (self.task,))[0])

    def deliveries(self) -> dict[str, int]:
        return dict(self.h.db.execute(
            "SELECT role,count(*) FROM memoriesql.relation_assessment_deliveries WHERE task_id=%s GROUP BY role",
            (self.task,),
        ).fetchall())

    def outcome(self) -> tuple[Any, ...]:
        return self.h.row(
            "SELECT t.status,a.result_status,a.error_code,a.retry_class FROM memoriesql.semantic_tasks t "
            "JOIN memoriesql.semantic_task_attempts a USING (tenant_id,task_id) WHERE t.task_id=%s",
            (self.task,),
        )

    def test_a_missing_disposition_is_repaired_without_a_delivery(self) -> None:
        self.activate(2)
        result = self.execute(2, self.complete)
        self.assertEqual(result.task_status, "succeeded", result)
        author, repair, specialist = self.prompts
        # R-1: the frame's packet is the delivered packet without its evidence.
        delivered = dict(author["relation_packet"])
        self.assertTrue(delivered.pop("evidence"))
        self.assertEqual(repair["relation_packet"], delivered)
        self.assertEqual(
            repair["refusal_codes"], [f"relation.pair_missing: {self.candidate}/{self.candidate}"]
        )
        # R-5: the specialist's prompt is its packet alone, judging the
        # unchanged proposals.
        self.assertEqual(set(specialist), {"relation_packet"})
        self.assertEqual(
            specialist["relation_packet"]["proposals"],
            repair["previous_output"]["typed_output"]["proposals"],
        )
        # R-3: one author and one specialist delivery; three requests in one attempt.
        self.assertEqual(self.deliveries(), {"author": 1, "specialist": 1})
        self.assertEqual(self.counted(
            "SELECT count(*) FROM memoriesql.model_provider_request_intents WHERE task_id=%s"), 3)
        self.assertEqual(self.counted(
            "SELECT count(*) FROM memoriesql.model_usage_events u JOIN "
            "memoriesql.model_provider_request_intents i USING (tenant_id,request_id) WHERE i.task_id=%s"), 3)
        self.assertEqual(self.counted(
            "SELECT count(*) FROM memoriesql.semantic_task_attempts WHERE task_id=%s"), 1)
        self.assertEqual(self.counted(
            "SELECT count(*) FROM memoriesql.assessed_relations WHERE task_id=%s"), 1)
        self.assertEqual(
            self.h.row(
                "SELECT disposition,reason_text FROM memoriesql.relation_pair_dispositions WHERE task_id=%s "
                "AND first_bead_id=%s AND second_bead_id=%s",
                (self.task, self.candidate, self.candidate),
            ),
            ("not_assessed", NOT_ASSESSED),
        )

    def test_a_repair_that_changes_more_settles_invalid_output(self) -> None:
        self.activate(2)

        def overreach(output: dict[str, Any]) -> None:
            # Completes the missing pair, but also rewrites a given disposition.
            self.complete(output)
            for d in output["dispositions"]:
                if d["first_bead_id"] != d["second_bead_id"]:
                    d.update(disposition="not_assessed", reason=NOT_ASSESSED)

        result = self.execute(2, overreach)
        self.assertEqual(result.result_status, "invalid_output", result)
        self.assertEqual(
            self.outcome(),
            ("failed_terminal", "invalid_output", "relation_assessment.repair_out_of_bounds", "never"),
        )
        self.assertEqual([("repair_frame" in p) for p in self.prompts], [False, True])
        self.assertEqual(self.deliveries(), {"author": 1})

    def test_revision_one_never_repairs(self) -> None:
        self.activate(1)
        result = self.execute(1, self.complete)
        self.assertEqual(result.result_status, "invalid_output", result)
        self.assertEqual(self.outcome()[2], "relation_assessment.coverage_incomplete")
        self.assertEqual(len(self.prompts), 1)
        self.assertEqual(self.counted(
            "SELECT count(*) FROM memoriesql.model_provider_request_intents WHERE task_id=%s"), 1)


if __name__ == "__main__":
    unittest.main()
