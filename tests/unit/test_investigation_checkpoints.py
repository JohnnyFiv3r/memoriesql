"""Checkpoint caller controls; SQL/authority acknowledgments remain inert."""

from __future__ import annotations

import copy
import hashlib
import json
import time
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from unittest.mock import Mock, patch
from uuid import uuid4

from psycopg import OperationalError

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
