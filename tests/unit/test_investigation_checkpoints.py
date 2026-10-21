"""Checkpoint caller controls; SQL/authority acknowledgments remain inert."""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import re
import time
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import MethodType, SimpleNamespace
from typing import Any
from unittest.mock import Mock, patch
from uuid import UUID, uuid4

from psycopg import OperationalError
from sqlglot import exp, parse_one

from memoriesql.application.investigation_contracts import (
    CheckpointManifest,
    CheckpointRequest,
    ReadCheckpointRequest,
    parse_investigation_request,
    result_json_bytes,
)
from memoriesql.infrastructure.postgres.agent_sql_results import (
    PostgresAgentSqlResults,
    _Failure,
)

MODULE = "memoriesql.infrastructure.postgres.agent_sql_results"
ROOT = Path(__file__).resolve().parents[2]


class CheckpointReviewRegressions(unittest.TestCase):
    """Actual shipped SQL predicates/request builders, evaluated only in Python.

    This does not execute SQL or assert that PostgreSQL/authorization works.
    Runtime acceptance below remains necessary for the complete implementation.
    """

    def overdue_predicate(
        self, standalone_end: datetime, eligibility: datetime | None
    ) -> Any:
        text = (
            ROOT / "migrations/0047_automatic_investigation_checkpoints.sql"
        ).read_text()
        start = text.index(
            "CREATE OR REPLACE FUNCTION memoriesql.query_cleanup_overdue_v1("
        )
        body = text[text.index("AS $$", start) + 5 :]
        tree = parse_one(body[: body.index("$$;")], dialect="postgres")
        query = next(
            q
            for q in tree.find_all(exp.Select)
            if isinstance(q.args.get("from_"), exp.From)
            and isinstance(q.args["from_"].this, exp.Table)
            and q.args["from_"].this.name == "query_result_creations"
        )
        now = datetime(2026, 10, 21, tzinfo=UTC)
        values = {
            "c.tenant_id": "t",
            "o.workspace_id": "w",
            "c.result_id": "root",
            "c.expires_at": standalone_end,
            "t": "t",
            "w": "w",
        }

        def evaluate(node: exp.Expression) -> Any:
            if isinstance(node, exp.Paren):
                return evaluate(node.this)
            if isinstance(node, exp.Column):
                return values[node.sql(dialect="postgres")]
            if isinstance(node, exp.CurrentTimestamp):
                return now
            if isinstance(node, exp.Dot):
                return evaluate(node.expression)
            if isinstance(node, exp.Anonymous):
                if node.name.lower() == "clock_timestamp":
                    return now
                if node.name.lower() == "query_result_cleanup_eligible_at_v1":
                    return eligibility
                raise AssertionError(node.sql())
            if isinstance(node, exp.Interval):
                return timedelta(hours=int(node.this.this))
            if isinstance(node, exp.And):
                a, b = evaluate(node.this), evaluate(node.expression)
                return (
                    False
                    if a is False or b is False
                    else None
                    if a is None or b is None
                    else True
                )
            if isinstance(node, exp.EQ):
                return evaluate(node.this) == evaluate(node.expression)
            if isinstance(node, exp.Sub):
                return evaluate(node.this) - evaluate(node.expression)
            if isinstance(node, exp.LT):
                a, b = evaluate(node.this), evaluate(node.expression)
                return None if a is None or b is None else a < b
            raise AssertionError("unhandled predicate node: " + node.sql())

        return evaluate(query.args["where"].this)

    def test_actual_overdue_predicate_uses_last_required_hold_end(self) -> None:
        now = datetime(2026, 10, 21, tzinfo=UTC)
        for label, end, expected in (
            ("live-checkpoint", now + timedelta(days=29), False),
            ("recent-final-release", now - timedelta(hours=2), False),
            ("true-debt", now - timedelta(hours=25), True),
            ("private-child-still-held", None, None),
        ):
            with self.subTest(boundary=label):
                self.assertIs(
                    self.overdue_predicate(now - timedelta(days=2), end), expected
                )

    def frame_segment(self, migration: str, name: str, start: str, end: str) -> str:
        text = (ROOT / f"migrations/{migration}.sql").read_text()
        match = re.search(
            r"CREATE (?:OR REPLACE )?FUNCTION memoriesql\." + name + r"\(.*?\$\$;",
            text,
            re.DOTALL,
        )
        assert match is not None, (migration, name)
        body = " ".join(re.sub(r"--[^\n]*", "", match[0]).split())
        return body[body.index(start) : body.index(end, body.index(start))]

    def test_redefined_helpers_keep_frame_records_as_canonical_text(self) -> None:
        # 0047 redefines both helpers for checkpoint access. Retained rows carry
        # numeric SQL values (author_confidence) and private partitions refuse
        # JSON floats, so frames must stay canonical text exactly as 0046 does.
        for name, start, end in (
            (
                "prepare_query_inspection_v1",
                "'dependencies',jsonb_build_object('frames'",
                "'raw_sources',raw_sources",
            ),
            (
                "read_query_helper_artifact_v1",
                "FOR item IN SELECT value FROM jsonb_array_elements(deps->'frames')",
                "IF record_pin->>'kind'='unit_package'",
            ),
        ):
            with self.subTest(function=name):
                checkpoint = self.frame_segment(
                    "0047_automatic_investigation_checkpoints", name, start, end
                )
                self.assertEqual(
                    checkpoint,
                    self.frame_segment(
                        "0046_agent_sql_result_helpers", name, start, end
                    ),
                )
                self.assertIn("records_base64", checkpoint)
                self.assertNotIn("'records',(SELECT", checkpoint)
                self.assertNotIn("item->'records'", checkpoint)

    def runtime_request(self, *, hydration: bool) -> tuple[dict[str, Any], str]:
        source = ast.parse(
            (ROOT / "tests/runtime/test_investigation_checkpoints.py").read_text()
        )
        methods = next(n for n in source.body if isinstance(n, ast.ClassDef)).body
        selected = [
            copy.deepcopy(n)
            for n in methods
            if isinstance(n, ast.FunctionDef)
            and n.name
            in {
                "pin",
                "append",
                "read",
                "test_checkpoint_context_inspection_and_normalized_hydration_use_original_frame",
            }
        ]
        case_method = next(n for n in selected if n.name.startswith("test_"))
        kept: list[ast.stmt] = []
        for statement in case_method.body:
            if isinstance(statement, ast.Assign) and isinstance(
                statement.value, ast.Call
            ):
                call = statement.value
                if (
                    isinstance(call.func, ast.Attribute)
                    and isinstance(call.func.value, ast.Attribute)
                    and call.func.value.attr == "h"
                    and call.func.attr == "send"
                ):
                    arg = call.args[0]
                    if not hydration or (
                        isinstance(arg, ast.Name) and arg.id == "hydration"
                    ):
                        kept.append(ast.Return(arg))
                        break
            kept.append(statement)
        else:
            raise AssertionError("missing source request boundary")
        case_method.body = kept
        namespace: dict[str, Any] = {
            "Any": Any,
            "UUID": UUID,
            "uuid4": uuid4,
            "timedelta": timedelta,
        }
        klass = ast.ClassDef(
            name="BuilderOnly",
            bases=[],
            keywords=[],
            body=list[ast.stmt](selected),
            decorator_list=[],
            type_params=[],
        )
        exec(
            compile(
                ast.fix_missing_locations(ast.Module(body=[klass], type_ignores=[])),
                "<actual-runtime-builders-only>",
                "exec",
            ),
            namespace,
        )
        case = namespace["BuilderOnly"]()
        normalized = uuid4()
        run = {"run_ref": str(uuid4()), "default_known_at": "2026-10-21T00:00:00Z"}
        root = {
            "result_id": str(uuid4()),
            "content_digest": "a" * 64,
            "frame": {"known_at": run["default_known_at"], "view": "historical"},
            "result_schema": [
                {"name": "statement_id"},
                {"name": "bead_id"},
                {"name": "evidence_ref"},
            ],
        }
        statement_id, unit = str(uuid4()), str(uuid4())
        evidence = {
            "kind": "source",
            "ref": str(uuid4()),
            "unit_ref": unit,
            "content_sha256": "d" * 64,
        }
        result = {
            "outcome": "available",
            "result": root,
            "page": {
                "rows": [
                    {
                        "values": [statement_id, str(normalized), evidence["ref"]],
                        "evidence_refs": [evidence],
                    }
                ]
            },
        }
        pin = {"checkpoint_id": str(uuid4()), "manifest_digest": "b" * 64}
        context = {
            "kind": "checkpoint",
            **pin,
            "root": {k: root[k] for k in ("result_id", "content_digest")},
            "basis": {"kind": "automatic"},
        }
        first = {
            "checkpoint": {
                "checkpoint_id": pin["checkpoint_id"],
                "investigation_id": str(uuid4()),
                "branch_id": str(uuid4()),
            },
            "branch": {"head": pin},
        }
        part_pin = {
            "ref": str(uuid4()),
            "statement_id": statement_id,
            "unit": {
                "source_unit_id": unit,
                "content_sha256": evidence["content_sha256"],
            },
            "part": {"part_id": str(uuid4())},
        }
        case.r = Mock()
        case.r.start.return_value = run
        case.r.query.return_value = ({}, result)
        case.r.reuse.return_value = {"outcome": "unavailable"}
        case.r.send.side_effect = (
            lambda r: first
            if r["kind"] == "checkpoint"
            else {"root_contexts": [context]}
            if r["kind"] == "read_checkpoint"
            else result
        )
        case.query_root = lambda: (run, result)
        case.assert_checkpoint = Mock()
        for name in ("assertEqual", "assertTrue", "assertNotEqual", "assertIn"):
            setattr(case, name, getattr(self, name))
        h = SimpleNamespace(
            source=uuid4(), normalized=normalized, TEXT="fictional", available=Mock()
        )
        h.target = lambda bead: {"bead_id": str(bead), "bead_version_id": str(uuid4())}
        helper_tree = ast.parse(
            (ROOT / "tests/runtime/test_agent_sql_helpers_runtime.py").read_text()
        )
        helper = next(n for n in helper_tree.body if isinstance(n, ast.ClassDef))
        for name in ("inspection", "hydration"):
            fn = copy.deepcopy(
                next(
                    n
                    for n in helper.body
                    if isinstance(n, ast.FunctionDef) and n.name == name
                )
            )
            exec(
                compile(
                    ast.fix_missing_locations(ast.Module(body=[fn], type_ignores=[])),
                    "<actual-helper-request-builder>",
                    "exec",
                ),
                namespace,
            )
            setattr(h, name, MethodType(namespace[name], h))
        h.send = lambda request: (
            b"",
            {
                "inspection": {
                    "result": root,
                    "observations": [],
                    "evidence_pins": [part_pin],
                },
                "access_bases": [{"context": context}],
            },
        )
        case.h = h
        return getattr(case, case_method.name)(), evidence["ref"]

    def test_actual_positive_inspection_builder_does_not_mix_saved_and_live_targets(
        self,
    ) -> None:
        request, _ = self.runtime_request(hydration=False)
        self.assertIsNotNone(request["result"])
        self.assertEqual(request["observation_refs"], [])
        self.assertEqual(request["relation_refs"], [])

    def test_actual_positive_hydration_builder_uses_result_receipted_reference(
        self,
    ) -> None:
        request, result_ref = self.runtime_request(hydration=True)
        self.assertEqual(request["selections"][0]["evidence_ref"], result_ref)
        self.assertEqual(
            request["selections"][0]["origin"]["access"]["kind"], "checkpoint"
        )


class AutomaticCheckpointDispatch(unittest.TestCase):
    def test_append_and_read_use_canonical_admission(self) -> None:
        for kind in ("checkpoint", "read_checkpoint"):
            request: dict[str, Any] = {
                "contract_version": 1,
                "run_ref": str(uuid4()),
                "step_key": str(uuid4()),
                "kind": kind,
            }
            if kind == "checkpoint":
                request.update(
                    question="fictional",
                    progress={"status": "paused", "note": ""},
                    findings=[],
                    roots=[],
                )
            else:
                request["access"] = {
                    "checkpoint": {
                        "checkpoint_id": str(uuid4()),
                        "manifest_digest": "a" * 64,
                    },
                    "basis": {"kind": "automatic"},
                }
            service: Any = object.__new__(PostgresAgentSqlResults)
            service._admit = Mock(return_value={})
            service._deliver = Mock(return_value=b"canonical-checkpoint")
            with self.subTest(kind=kind):
                self.assertEqual(
                    service.handle(json.dumps(request).encode()),
                    b"canonical-checkpoint",
                )
                self.assertEqual(service._admit.call_args.args[1], kind)


class AutomaticCheckpointExecution(unittest.TestCase):
    def setUp(self) -> None:
        self.request = CheckpointRequest.model_validate(
            {
                "contract_version": 1,
                "run_ref": str(uuid4()),
                "step_key": str(uuid4()),
                "kind": "checkpoint",
                "question": "fictional α question",
                "progress": {"status": "paused", "note": "note"},
                "findings": [],
                "roots": [
                    {
                        "result": {
                            "result_id": str(uuid4()),
                            "content_digest": "a" * 64,
                        },
                        "access": {"kind": "standalone"},
                    }
                ],
            }
        )
        self.manifest = CheckpointManifest.model_validate(
            {
                "checkpoint_id": str(uuid4()),
                "investigation_id": str(uuid4()),
                "branch_id": str(uuid4()),
                "sequence": "1",
                "investigation_sequence": "1",
                "predecessor": None,
                "fork_origin": None,
                "working_state_source": None,
                "created_at": "2026-10-21T00:00:00.000000Z",
                "automatic_expires_at": "2026-11-20T00:00:00.000000Z",
                "question": self.request.question,
                "progress": self.request.progress.model_dump(mode="json"),
                "findings": [],
                "roots": [
                    {
                        "result": self.request.roots[0].result.model_dump(mode="json"),
                        "frame_ref": str(uuid4()),
                    }
                ],
            }
        )
        self.pin = {
            "checkpoint_id": self.manifest.checkpoint_id,
            "manifest_digest": self.manifest.content_digest(),
        }
        self.context = {
            "kind": "checkpoint",
            **self.pin,
            "root": self.request.roots[0].result.model_dump(mode="json"),
            "basis": {"kind": "automatic"},
        }
        self.packet: dict[str, Any] = {
            "checkpoint": self.manifest.model_dump(mode="json"),
            "root_contexts": [self.context],
            "retention": {
                "automatic_until": self.manifest.automatic_expires_at,
                "automatic_retained": True,
                "named_holds": [],
                "retained": True,
                "hold_ref": self.manifest.checkpoint_id,
                "unique_bytes": "60000",
                "context_end": "automatic_expiry_or_prune",
            },
        }
        self.admission = {
            "delivery_ref": str(uuid4()),
            "receipt_ref": str(uuid4()),
            "reserved_db_ms": 30000,
            "reserved_transport": 262144,
        }

    def execute(
        self,
        request: Any = None,
        *,
        pending: bool = True,
        commit_lost: bool = False,
        packet: Any = None,
        pending_manifest: dict[str, Any] | None = None,
    ) -> tuple[bytes, list[tuple[str, Any]]]:
        calls: list[tuple[str, Any]] = []

        def sql(statement: str, values: tuple[Any, ...] = ()) -> Any:
            calls.append((statement, values))
            if "prepare_automatic" in statement:
                value = (
                    {
                        "pending": pending_manifest
                        if pending_manifest is not None
                        else self.manifest.model_dump(mode="json")
                    }
                    if pending
                    else (packet or self.packet)
                )
            elif "publish_automatic" in statement:
                value = packet or self.packet
            elif "query_delivery_remaining" in statement:
                value = {
                    "accesses": 126,
                    "db_ms": 299000,
                    "transport_bytes": 16000000,
                    "allocation_bytes": 130000000,
                    "run_expires_at": "2026-10-21T00:30:00.000000Z",
                }
            else:
                value = None
            return Mock(fetchone=Mock(return_value=(value,)))

        frame = Mock(execute=Mock(side_effect=sql))

        @contextmanager
        def projection(*args: Any, **kwargs: Any) -> Iterator[Any]:
            yield frame
            if commit_lost:
                raise OperationalError("fictional lost commit acknowledgement")

        service: Any = object.__new__(PostgresAgentSqlResults)
        service._credential, service._workspace = "c" * 64, uuid4()
        with patch(MODULE + ".relation_projection_frame", projection):
            result = service._checkpoint(
                request or self.request,
                self.admission,
                time.monotonic(),
                time.monotonic() + 10,
                Mock(),
            )
        return result, calls

    def test_append_binds_canonical_bytes_and_records_no_visible_facts(self) -> None:
        raw, calls = self.execute()
        response = json.loads(raw)
        self.assertEqual(response["checkpoint"], self.packet["checkpoint"])
        self.assertEqual(response["branch"]["head"], self.pin)
        self.assertNotIn("root_contexts", response)
        for field in ("page", "result", "visibility"):
            self.assertNotIn(field, response)
        published = next(
            values for statement, values in calls if "publish_automatic" in statement
        )
        self.assertEqual(published[3], hashlib.sha256(published[2]).hexdigest())
        self.assertEqual(published[3], self.manifest.content_digest())
        recorded = next(
            values for statement, values in calls if "record_checkpoint" in statement
        )
        self.assertEqual(recorded[3], hashlib.sha256(raw).hexdigest())
        self.assertFalse(
            any("query_visible_refs" in statement for statement, _ in calls)
        )

    def test_read_returns_only_exact_supplied_root_basis(self) -> None:
        request = ReadCheckpointRequest.model_validate(
            {
                "contract_version": 1,
                "run_ref": self.request.run_ref,
                "step_key": str(uuid4()),
                "kind": "read_checkpoint",
                "access": {"checkpoint": self.pin, "basis": {"kind": "automatic"}},
            }
        )
        raw, calls = self.execute(request, pending=False)
        self.assertEqual(json.loads(raw)["root_contexts"], [self.context])
        self.assertFalse(
            any("publish_automatic" in statement for statement, _ in calls)
        )

    def test_replay_reads_original_manifest_without_another_publication(self) -> None:
        raw, calls = self.execute(pending=False)
        self.assertEqual(json.loads(raw)["branch"]["head"], self.pin)
        self.assertFalse(
            any("publish_automatic" in statement for statement, _ in calls)
        )

    def test_lost_commit_ack_is_pending_and_never_returns_computed_bytes(self) -> None:
        with self.assertRaises(_Failure) as failure:
            self.execute(commit_lost=True)
        self.assertTrue(failure.exception.pending)

    def test_oversized_reply_is_not_acknowledged(self) -> None:
        self.admission["reserved_transport"] = 1024
        with self.assertRaises(_Failure) as failure:
            self.execute()
        self.assertEqual(failure.exception.outcome, "budget_exhausted")

    def test_oversized_actual_manifest_is_a_storage_refusal_before_publication(
        self,
    ) -> None:
        payload = self.request.model_dump(mode="json")
        payload.update(
            question="α" * 2000,
            progress={"status": "paused", "note": "α" * 1000},
            findings=[{"text": "x" * 8192, "facts": [], "evidence_refs": []}],
        )
        manifest = self.manifest.model_dump(mode="json")
        manifest.update({k: payload[k] for k in ("question", "progress", "findings")})
        padding = (32760 - len(result_json_bytes(manifest))) // 4
        self.assertTrue(0 <= padding <= 4096)
        payload["findings"][0]["text"] = "α" * padding + "x" * (8192 - 2 * padding)
        request = CheckpointRequest.model_validate(payload)
        manifest.update({k: payload[k] for k in ("question", "progress", "findings")})
        self.assertLessEqual(len(result_json_bytes(manifest)), 32768)
        # Only trusted assigned int8 sequences increase; all authored text/root
        # bounds remain valid. The request lower bound cannot reserve this space.
        manifest.update(
            sequence="1000000000000000000", investigation_sequence="1000000000000000000"
        )
        with self.assertRaises(_Failure) as failure:
            self.execute(request, pending_manifest=manifest)
        self.assertEqual(
            (failure.exception.outcome, failure.exception.code),
            ("budget_exhausted", "storage"),
        )

    def test_packet_cannot_add_fields_change_roots_or_change_retention(self) -> None:
        variants: list[dict[str, Any]] = []
        value: Any
        for key, value in (
            ("checkpoint_id", str(uuid4())),
            ("manifest_digest", "b" * 64),
            ("basis", {"kind": "named_save", "save_id": str(uuid4()), "revision": "1"}),
        ):
            p = copy.deepcopy(self.packet)
            p["root_contexts"][0][key] = value
            variants.append(p)
        p = copy.deepcopy(self.packet)
        p["root_contexts"] = []
        variants.append(p)
        p = copy.deepcopy(self.packet)
        p["private"] = "hidden"
        variants.append(p)
        for key, value in (
            ("automatic_retained", False),
            ("hold_ref", str(uuid4())),
            ("unique_bytes", 60000),
            ("automatic_until", "2026-11-21T00:00:00.000000Z"),
        ):
            p = copy.deepcopy(self.packet)
            p["retention"][key] = value
            variants.append(p)
        for p in variants:
            with self.subTest(packet=p), self.assertRaises(ValueError):
                PostgresAgentSqlResults._checkpoint_payload(self.request, p)

    def test_request_cannot_be_replaced_by_another_working_state(self) -> None:
        for key, value in (
            ("question", "other question"),
            ("working_state_source", self.pin),
            ("predecessor", self.pin),
        ):
            data = self.manifest.model_dump(mode="json")
            data[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                PostgresAgentSqlResults._check_checkpoint_request(
                    self.request, CheckpointManifest.model_validate(data)
                )

    def test_saved_context_and_mutations_still_have_existing_closed_wire(self) -> None:
        for kind in ("save_checkpoint", "restore_checkpoint"):
            data: dict[str, Any] = {
                "contract_version": 1,
                "run_ref": self.request.run_ref,
                "step_key": str(uuid4()),
                "kind": kind,
                "access": {"checkpoint": self.pin, "basis": {"kind": "automatic"}},
            }
            if kind == "save_checkpoint":
                data["name"] = "fictional"
            else:
                data["selected_roots"] = []
            parse_investigation_request(json.dumps(data).encode())
            service: Any = object.__new__(PostgresAgentSqlResults)
            service._admit = Mock()
            response = json.loads(service.handle(json.dumps(data).encode()))
            self.assertEqual(response["outcome"], "unsupported_query")
            service._admit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
