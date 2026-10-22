"""Staged helper executor protocol tests. All SQL acknowledgments are mocks, not authorization."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import time
import unittest
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from typing import Any, cast
from unittest.mock import MagicMock, Mock, call, patch
from uuid import uuid4

from psycopg import OperationalError
from psycopg.errors import InsufficientPrivilege

from memoriesql.application.agent_sql_catalog import SqlCatalog
from memoriesql.application.agent_sql_helpers import validate_helper_content
from memoriesql.application.investigation_contracts import (
    HydrateRequest,
    InspectRequest,
    parse_investigation_request,
)
from memoriesql.infrastructure.postgres.agent_sql_authority import (
    QueryAuthorityProfile,
)
from memoriesql.infrastructure.postgres.agent_sql_results import (
    _AUDIT_DELIVERY,
    PostgresAgentSqlResults,
    _attributed,
    _Failure,
)
from memoriesql.infrastructure.results_broker.server import TrustedHost
from memoriesql.query_client import TrustedHostTransport
from tests.unit.test_agent_sql_helpers import hydration_fixture
from tests.unit.test_results_broker import fictional_config, short_directory


class HelperDispatch(unittest.TestCase):
    def request(self, kind: str) -> dict[str, Any]:
        common: dict[str, Any] = {
            "contract_version": 1,
            "run_ref": str(uuid4()),
            "step_key": str(uuid4()),
            "kind": kind,
        }
        if kind == "inspect":
            common.update(
                observation_refs=[
                    {"bead_id": str(uuid4()), "bead_version_id": str(uuid4())}
                ],
                relation_refs=[],
                facets=["content"],
                view="historical",
                known_at="2026-10-19T00:00:00Z",
            )
        else:
            common.update(
                selections=[
                    {
                        "evidence_ref": str(uuid4()),
                        "part_id": str(uuid4()),
                        "representation": "normalized_text",
                        "start": 0,
                        "end": 3,
                    }
                ],
                max_bytes=64,
            )
        return common

    def check_dispatch(self, kind: str) -> None:
        service: Any = object.__new__(PostgresAgentSqlResults)
        service._admit = Mock(return_value={})
        service._deliver = Mock(return_value=b"canonical-helper")
        self.assertEqual(
            service.handle(json.dumps(self.request(kind)).encode()), b"canonical-helper"
        )
        self.assertEqual(service._admit.call_args.args[1], kind)
        service._deliver.assert_called_once()

    def test_inspection_uses_canonical_admission(self) -> None:
        self.check_dispatch("inspect")

    def test_hydration_uses_canonical_admission(self) -> None:
        self.check_dispatch("hydrate_source")


MODULE = "memoriesql.infrastructure.postgres.agent_sql_results"


class HelperExecution(HelperDispatch):
    def setUp(self) -> None:
        payload = self.request("inspect")
        self.request_value = cast(
            InspectRequest, parse_investigation_request(json.dumps(payload).encode())
        )
        bead = payload["observation_refs"][0]
        self.observation = {
            "bead_id": bead["bead_id"],
            "bead_version_id": bead["bead_version_id"],
            "event_id": str(uuid4()),
            "source_unit_id": str(uuid4()),
            "bead_type_key": "fictional",
            "bead_type_revision_id": str(uuid4()),
            "title": None,
            "summary": None,
            "detail": None,
            "render_state": "unsupported",
            "recorded_at": "2026-10-19T00:00:00Z",
            "effective_at": None,
            "effective_basis": "unknown",
            "correction_state": "unsuperseded",
        }
        self.inspection: dict[str, Any] = {
            "observations": [
                {
                    "observation": self.observation,
                    "statements": [],
                    "render": None,
                    "type": {
                        "key": "fictional",
                        "revision_id": self.observation["bead_type_revision_id"],
                        "definition": "Fictional fixture.",
                    },
                    "authorship": {
                        "receipt_id": str(uuid4()),
                        "task_id": str(uuid4()),
                        "attempt_id": str(uuid4()),
                        "run_ref": "fixture.author",
                        "task_contract_key": "fixture",
                        "task_contract_version": 1,
                    },
                    "mentions": [],
                    "entities": [],
                    "topics": [],
                }
            ],
            "corrections": [],
            "source_units": [],
            "result": None,
            "relations": [],
            "relation_types": [],
            "relation_pairs": [],
            "evidence_pins": [],
            "provenance": None,
        }
        self.packet: dict[str, Any] = {
            "content": {
                "kind": "inspect",
                "payload": {"inspection": self.inspection},
                "refs": [],
                "access_bases": [],
            },
            "witnesses": {"evidence_pins": []},
            "dependencies": {
                "frames": [],
                "raw_sources": [],
                "parts": [],
                "result_pins": [],
            },
            "checked_at": "2026-10-19T00:00:00.000000Z",
        }
        self.admission = {
            "delivery_ref": str(uuid4()),
            "receipt_ref": str(uuid4()),
            "reserved_db_ms": 30000,
            "reserved_transport": 262144,
        }
        self.remaining = {
            "accesses": 128,
            "db_ms": 300000,
            "transport_bytes": 16777216,
            "allocation_bytes": 134217728,
            "run_expires_at": "2026-10-19T00:30:00Z",
        }
        self.service: Any = object.__new__(PostgresAgentSqlResults)
        self.service._credential = "f" * 64
        self.service._workspace = uuid4()
        self.service._control = Mock(
            side_effect=AssertionError("no separate helper backend")
        )
        self.owner = MagicMock()
        self.existing: dict[str, Any] | None = None
        self.artifact = str(uuid4())
        self.sql: list[tuple[str, tuple[Any, ...]]] = []
        self.owner.execute.side_effect = self.execute

    def execute(self, statement: str, values: tuple[Any, ...] = ()) -> Mock:
        self.sql.append((statement, values))
        result: Any = None
        if "query_helper_preparation_v1" in statement:
            result = self.existing
        elif (
            "prepare_query_helper_v1" in statement
            or "read_query_helper_artifact_v1" in statement
        ):
            result = copy.deepcopy(self.packet)
            if self.existing:
                result.update(artifact_ref=self.artifact, retained_bytes=8192)
        elif "reserve_result_preparation_v1" in statement:
            result = {
                "operation_ref": str(uuid4()),
                "ownership_ref": str(uuid4()),
                "replayed": False,
            }
        elif "seal_result_preparation_v1" in statement:
            result = {"allocation_bytes": 8192 + sum(len(x) for x in values[3:6])}
        elif "query_delivery_remaining_v1" in statement:
            result = self.remaining
        elif "query_visible_refs_v1" in statement:
            result = []
        elif "record_query_helper_disclosure_v1" in statement:
            result = {"state": "settled"}
        elif not statement.startswith("SET LOCAL statement_timeout="):
            raise AssertionError(statement)
        return Mock(fetchone=Mock(return_value=(result,)))

    def hydration(self, *texts: str) -> HydrateRequest:
        """Make the prepared packet one normalized slice per text."""
        request, content = hydration_fixture()
        template = content["payload"]["hydration"]["slices"][0]
        slices, selections = [], []
        for text in texts:
            item = copy.deepcopy(template)
            item["evidence_pin"]["ref"] = str(uuid4())
            item["evidence_pin"]["part"]["part_id"] = str(uuid4())
            item.update(
                end=len(text),
                content=text,
                returned_sha256=hashlib.sha256(text.encode()).hexdigest(),
            )
            slices.append(item)
            selections.append(
                request.selections[0].model_copy(
                    update={
                        "evidence_ref": item["evidence_pin"]["ref"],
                        "part_id": item["evidence_pin"]["part"]["part_id"],
                        "end": len(text),
                    }
                )
            )
        content["payload"]["hydration"]["slices"] = slices
        self.packet["content"] = content
        return request.model_copy(
            update={"selections": tuple(selections), "max_bytes": 65536}
        )

    def run_helper(
        self, request: InspectRequest | HydrateRequest | None = None
    ) -> dict[str, Any]:
        start = time.monotonic()
        with patch(
            MODULE + ".relation_projection_frame", return_value=nullcontext(self.owner)
        ) as fence:
            data = self.service._helper(
                request or self.request_value,
                "0" * 64,
                self.admission,
                start,
                start + 30,
                self.owner,
            )
        self.assertIs(fence.call_args.args[0], self.owner)
        self.service._control.assert_not_called()
        return cast(dict[str, Any], json.loads(data))

    def test_actual_disclosure_association_is_derived_from_returned_observation(
        self,
    ) -> None:
        reply = self.run_helper()
        self.assertEqual(reply["outcome"], "available")
        self.assertEqual(reply["inspection"], self.inspection)
        self.assertEqual(
            reply["visibility"]["new_refs"],
            [
                {
                    "kind": "observation",
                    "ref": self.observation["bead_id"],
                    "version_ref": self.observation["bead_version_id"],
                }
            ],
        )
        self.assertEqual(reply["access_receipt_ref"], self.admission["delivery_ref"])
        encoded = json.dumps(
            reply, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode()
        self.assertEqual(reply["work"]["transport_bytes"], len(encoded))
        recorded = next(
            (
                values
                for statement, values in self.sql
                if "record_query_helper_disclosure_v1" in statement
            )
        )
        self.assertEqual(recorded[5], len(encoded))
        self.assertTrue(
            any(
                ("seal_result_preparation_v1" in statement for statement, _ in self.sql)
            )
        )

    def test_bad_payload_cannot_make_private_fields_visible(self) -> None:
        self.inspection["private_dependency_count"] = 9
        with self.assertRaises(_Failure) as caught:
            self.run_helper()
        self.assertEqual(
            (caught.exception.outcome, caught.exception.code),
            ("execution_error", "provenance"),
        )
        self.assertFalse(
            any(
                (
                    "record_query_helper_disclosure_v1" in statement
                    for statement, _ in self.sql
                )
            )
        )

    def test_denied_dependency_never_seals_or_discloses_a_partial_reply(self) -> None:
        original = self.execute

        def execute(statement: str, values: tuple[Any, ...] = ()) -> Mock:
            if "prepare_query_helper_v1" in statement:
                raise InsufficientPrivilege("protected target count and source data")
            return original(statement, values)

        self.owner.execute.side_effect = execute
        with self.assertRaises(_Failure) as caught:
            self.run_helper()
        self.assertEqual(
            (caught.exception.outcome, caught.exception.code),
            ("unavailable", "unavailable"),
        )
        self.assertFalse(
            any(
                ("seal_result_preparation_v1" in statement for statement, _ in self.sql)
            )
        )

    def test_sealed_replay_reauthorizes_without_repreparing_or_resealing(self) -> None:
        self.packet["content"]["refs"] = validate_helper_content(
            self.request_value, self.packet["content"]
        )
        self.existing = {
            "state": "sealed",
            "operation_ref": str(uuid4()),
            "ownership_ref": str(uuid4()),
        }
        self.assertEqual(self.run_helper()["outcome"], "available")
        self.assertTrue(
            any(
                (
                    "read_query_helper_artifact_v1" in statement
                    for statement, _ in self.sql
                )
            )
        )
        self.assertFalse(
            any(
                (
                    "prepare_query_helper_v1" in statement
                    or "seal_result_preparation_v1" in statement
                    for statement, _ in self.sql
                )
            )
        )

    def test_changed_visibility_never_rewrites_a_sealed_replay(self) -> None:
        self.existing = {
            "state": "sealed",
            "operation_ref": str(uuid4()),
            "ownership_ref": str(uuid4()),
        }
        with self.assertRaises(_Failure) as caught:
            self.run_helper()
        self.assertEqual(caught.exception.outcome, "unavailable")
        self.assertFalse(
            any(
                (
                    "record_query_helper_disclosure_v1" in statement
                    for statement, _ in self.sql
                )
            )
        )

    def test_lost_commit_acknowledgment_stays_pending(self) -> None:
        @contextmanager
        def lost_ack(*args: Any, **kwargs: Any) -> Iterator[MagicMock]:
            yield self.owner
            raise OperationalError("private connection failure")

        start = time.monotonic()
        with patch(MODULE + ".relation_projection_frame", side_effect=lost_ack):
            with self.assertRaises(_Failure) as caught:
                self.service._helper(
                    self.request_value,
                    "0" * 64,
                    self.admission,
                    start,
                    start + 30,
                    self.owner,
                )
        self.assertTrue(caught.exception.pending)
        self.assertEqual(caught.exception.outcome, "settlement_pending")

    def test_transport_refusal_cannot_publish_a_helper(self) -> None:
        self.admission["reserved_transport"] = 1024
        with self.assertRaises(_Failure) as caught:
            self.run_helper()
        self.assertEqual(
            (caught.exception.outcome, caught.exception.code),
            ("budget_exhausted", "transport"),
        )
        self.assertFalse(
            any(
                (
                    "record_query_helper_disclosure_v1" in statement
                    for statement, _ in self.sql
                )
            )
        )

    def test_bounded_transport_pages_complete_objects_and_only_their_refs(self) -> None:
        original = self.inspection["observations"][0]
        original["type"]["definition"] = "fictional " * 600
        second = copy.deepcopy(original)
        second["observation"]["bead_id"] = str(uuid4())
        second["observation"]["bead_version_id"] = str(uuid4())
        targets = [
            dict(
                bead_id=item["observation"]["bead_id"],
                bead_version_id=item["observation"]["bead_version_id"],
            )
            for item in (original, second)
        ]
        self.request_value = cast(
            InspectRequest,
            parse_investigation_request(
                json.dumps(
                    {
                        **self.request_value.model_dump(mode="json"),
                        "observation_refs": targets,
                    }
                ).encode()
            ),
        )
        self.inspection["observations"].append(second)
        self.admission["reserved_transport"] = 10000
        result = self.run_helper()
        self.assertTrue(result["has_more"])
        self.assertIsNotNone(result["next_cursor"])
        self.assertEqual(result["inspection"]["observations"], [original])
        self.assertEqual(len(result["visibility"]["new_refs"]), 1)
        self.assertEqual(
            result["visibility"]["new_refs"][0]["ref"],
            original["observation"]["bead_id"],
        )

    def disclosed_transport(self) -> list[int]:
        return [
            values[5]
            for statement, values in self.sql
            if "record_query_helper_disclosure_v1" in statement
        ]

    def test_hydration_delivers_64_kib_of_content_in_one_reply_under_the_cap(
        self,
    ) -> None:
        # Hydration's 64 KiB per response is selected content (W4: sixteen
        # calls carry 1 MiB plus metadata). Escaped, these four astral spans are
        # about 196 KiB: still one reply under the 256 KiB whole-response cap.
        texts = ("🙂" * 4096, "🍐" * 4096, "🌳" * 4096, "🚀" * 4096)
        request = self.hydration(*texts)
        self.assertEqual(sum(len(t.encode()) for t in texts), request.max_bytes)
        result = self.run_helper(request)
        self.assertFalse(result["has_more"])
        self.assertEqual(
            [item["content"] for item in result["hydration"]["slices"]], list(texts)
        )
        (transport,) = self.disclosed_transport()
        self.assertEqual(transport, result["work"]["transport_bytes"])
        self.assertGreater(transport, 65536)
        self.assertLessEqual(transport, 262144)

    def test_hydration_encoded_past_the_cap_pages_whole_spans(self) -> None:
        # 64 KiB of control characters escapes six-fold, to about 384 KiB.
        texts = tuple(chr(code) * 16384 for code in (1, 2, 3, 4))
        result = self.run_helper(self.hydration(*texts))
        self.assertTrue(result["has_more"])
        self.assertEqual(
            [item["content"] for item in result["hydration"]["slices"]],
            list(texts[:2]),
        )
        (transport,) = self.disclosed_transport()
        self.assertLessEqual(transport, 262144)

    def test_a_span_that_cannot_fit_its_reserved_transport_is_never_truncated(
        self,
    ) -> None:
        self.admission["reserved_transport"] = 65536
        with self.assertRaises(_Failure) as caught:
            self.run_helper(self.hydration("\x01" * 16384))
        self.assertEqual(
            (caught.exception.outcome, caught.exception.code),
            ("budget_exhausted", "transport"),
        )
        self.assertEqual(self.disclosed_transport(), [])

    def test_replay_preserves_exact_page_even_when_a_smaller_page_would_fit(
        self,
    ) -> None:
        self.packet["content"]["refs"] = validate_helper_content(
            self.request_value, self.packet["content"]
        )
        self.packet.update(page_first=1, page_count=1)
        self.existing = {
            "state": "sealed",
            "operation_ref": str(uuid4()),
            "ownership_ref": str(uuid4()),
            "page_first": 1,
            "page_count": 1,
        }
        self.inspection["observations"][0]["type"]["definition"] = "fictional " * 600
        self.admission["reserved_transport"] = 1024
        with self.assertRaises(_Failure) as caught:
            self.run_helper()
        self.assertEqual(
            (caught.exception.outcome, caught.exception.code),
            ("budget_exhausted", "transport"),
        )
        self.assertFalse(
            any(
                (
                    "record_query_helper_disclosure_v1" in statement
                    for statement, _ in self.sql
                )
            )
        )

    def test_cursor_page_reuses_original_artifact_and_does_not_allocate_again(
        self,
    ) -> None:
        original = self.inspection["observations"][0]
        second = copy.deepcopy(original)
        second["observation"]["bead_id"] = str(uuid4())
        second["observation"]["bead_version_id"] = str(uuid4())
        self.inspection["observations"].append(second)
        targets = [
            {key: item["observation"][key] for key in ("bead_id", "bead_version_id")}
            for item in (original, second)
        ]
        self.request_value = cast(
            InspectRequest,
            parse_investigation_request(
                json.dumps(
                    {
                        **self.request_value.model_dump(mode="json"),
                        "observation_refs": targets,
                        "cursor": str(uuid4()),
                    }
                ).encode()
            ),
        )
        self.packet["content"]["refs"] = validate_helper_content(
            self.request_value, self.packet["content"]
        )
        self.packet.update(
            artifact_ref=self.artifact, retained_bytes=8192, page_first=2
        )
        result = self.run_helper()
        self.assertEqual(result["inspection"]["observations"], [second])
        self.assertFalse(result["has_more"])
        self.assertEqual(
            result["visibility"]["new_refs"][0]["ref"], second["observation"]["bead_id"]
        )
        self.assertFalse(
            any(
                (
                    "reserve_result_preparation_v1" in statement
                    or "seal_result_preparation_v1" in statement
                    for statement, _ in self.sql
                )
            )
        )
        recorded = next(
            (
                values
                for statement, values in self.sql
                if "record_query_helper_disclosure_v1" in statement
            )
        )
        self.assertEqual(recorded[8:10], (2, 1))

    def test_expired_deadline_cannot_prepare_seal_or_disclose(self) -> None:
        start = time.monotonic()
        with patch(
            MODULE + ".relation_projection_frame", return_value=nullcontext(self.owner)
        ):
            with self.assertRaises(_Failure) as caught:
                self.service._helper(
                    self.request_value,
                    "0" * 64,
                    self.admission,
                    start,
                    start - 1,
                    self.owner,
                )
        self.assertEqual(
            (caught.exception.outcome, caught.exception.code),
            ("budget_exhausted", "time"),
        )
        self.assertFalse(self.sql)


# Expected source interface snapshot of f5's unregistered SQL declarations.
# These are CPU mocks, not installed functions, permissions or real receipts.
EXPECTED_BACKEND_SIGNATURES = {
    "memoriesql.prepare_query_helper_v1(uuid,jsonb)",
    "memoriesql.record_query_helper_disclosure_v1(uuid,uuid,text,jsonb,text,bigint,bigint,timestamp with time zone,bigint,integer,uuid,bigint)",
    "memoriesql.query_helper_preparation_v1(uuid)",
    "memoriesql.read_query_helper_artifact_v1(uuid,uuid,uuid)",
    "memoriesql.query_helper_delivery_v1(uuid)",
    "memoriesql.query_helper_evidence_pin_v1(uuid,uuid,uuid,uuid,uuid)",
    "memoriesql.prepare_query_inspection_v1(uuid,jsonb)",
    "memoriesql.resolve_query_helper_evidence_v1(uuid,uuid,uuid,jsonb)",
    "memoriesql.prepare_query_hydration_v1(uuid,jsonb)",
    "memoriesql.purge_query_helper_step_v1()",
}


class QueryAuditAttribution(unittest.TestCase):
    """M0047: connections opened for a delivery's work name it; no others do."""

    CLAIM = "SELECT set_config('memoriesql.query_audit_delivery',%s,false)"

    def test_only_connections_opened_for_a_delivery_name_it(self) -> None:
        opened: list[MagicMock] = []

        def factory() -> Any:
            opened.append(MagicMock())
            return opened[-1]

        service: Any = object.__new__(PostgresAgentSqlResults)
        service._control = _attributed(factory)

        def work(*_args: Any) -> bytes:
            with service._control():
                pass
            return b"delivered"

        service._owned_delivery = Mock(side_effect=work)
        delivery = str(uuid4())
        request = parse_investigation_request(
            json.dumps(HelperDispatch().request("inspect")).encode()
        )
        admission = {"delivery_ref": delivery}
        delivered = service._deliver(request, "inspect", "f" * 64, admission, 0.0)
        self.assertEqual(delivered, b"delivered")
        with service._control():
            pass
        self.assertEqual(
            opened[0].execute.call_args_list, [call(self.CLAIM, (delivery,))]
        )
        opened[0].transaction.assert_called_once_with()
        opened[1].execute.assert_not_called()
        self.assertIsNone(_AUDIT_DELIVERY.get())

    def deliver_opening(self, *connections: MagicMock) -> str:
        """Run one delivery whose work opens these connections and leaves them."""
        service: Any = object.__new__(PostgresAgentSqlResults)
        service._control = _attributed(Mock(side_effect=list(connections)))

        def work(*_args: Any) -> bytes:
            for _ in connections:
                service._control()
            return b"delivered"

        service._owned_delivery = Mock(side_effect=work)
        delivery = str(uuid4())
        request = parse_investigation_request(
            json.dumps(HelperDispatch().request("inspect")).encode()
        )
        admission = {"delivery_ref": delivery}
        delivered = service._deliver(request, "inspect", "f" * 64, admission, 0.0)
        self.assertEqual(delivered, b"delivered")
        return delivery

    def test_a_connection_still_open_when_its_delivery_ends_stops_naming_it(
        self,
    ) -> None:
        kept, closed = MagicMock(closed=False), MagicMock(closed=True)
        delivery = self.deliver_opening(kept, closed)
        self.assertEqual(
            kept.execute.call_args_list,
            [call(self.CLAIM, (delivery,)), call(self.CLAIM, ("",))],
        )
        self.assertEqual(closed.execute.call_args_list, [call(self.CLAIM, (delivery,))])
        kept.close.assert_not_called()

    def test_a_connection_that_cannot_stop_naming_its_delivery_is_closed(
        self,
    ) -> None:
        broken = MagicMock(closed=False)
        broken.execute.side_effect = [None, OperationalError("fictional lost")]
        self.deliver_opening(broken)
        broken.close.assert_called_once_with()

    def test_a_connection_that_cannot_name_its_delivery_is_closed(self) -> None:
        connection = MagicMock()
        connection.execute.side_effect = OperationalError("fictional lost connection")
        token = _AUDIT_DELIVERY.set((str(uuid4()), []))
        try:
            with self.assertRaises(OperationalError):
                _attributed(lambda: connection)()
        finally:
            _AUDIT_DELIVERY.reset(token)
        connection.close.assert_called_once_with()

    def test_restricted_query_work_opens_the_same_attributed_connections(self) -> None:
        control = Mock(return_value=MagicMock())
        service = PostgresAgentSqlResults(
            control_factory=control,
            reader_factory=Mock(),
            authority_profile=cast(QueryAuthorityProfile, None),
            credential_sha256="0" * 64,
            workspace_id=uuid4(),
        )
        self.assertIs(service._executor._control_factory, service._control)
        delivery = str(uuid4())
        token = _AUDIT_DELIVERY.set((delivery, []))
        try:
            service._executor._control_factory()
        finally:
            _AUDIT_DELIVERY.reset(token)
        control.return_value.execute.assert_called_once_with(self.CLAIM, (delivery,))

    def test_host_compositions_open_a_fresh_control_connection_per_call(
        self,
    ) -> None:
        # The claim is session-level: the broker's and the local trusted
        # host's control factories must never hand out a pooled connection.
        host = TrustedHost(
            fictional_config(short_directory(self)), cast(QueryAuthorityProfile, None)
        )
        transport = TrustedHostTransport(
            control_url="postgresql://fictional-control",
            reader_url="postgresql://fictional-reader",
            reader_role="fictional_reader",
            pinned_profile_sha256=None,
            credential_sha256="0" * 64,
            workspace_id=uuid4(),
        )
        opened: list[MagicMock] = []

        def connect(*_args: Any, **_kwargs: Any) -> MagicMock:
            opened.append(MagicMock())
            return opened[-1]

        with patch("psycopg.connect", side_effect=connect):
            for factory in (
                host.executor("0" * 64)._control,
                transport._connect_control,
            ):
                self.assertIsNot(factory(), factory())
        self.assertEqual(len(opened), 4)


class HelperBackendBoundary(unittest.TestCase):
    def service(self) -> Any:
        service: Any = object.__new__(PostgresAgentSqlResults)
        service._credential = "a" * 64
        service._workspace = uuid4()
        service._control = Mock(return_value=MagicMock())
        service._deliver = Mock(return_value=b"not-an-authenticated-response")
        return service

    def test_every_missing_primitive_refuses_before_admission(self) -> None:
        for kind in ("inspect", "hydrate_source"):
            for missing in EXPECTED_BACKEND_SIGNATURES:
                with self.subTest(kind=kind, missing=missing):
                    service = self.service()
                    frame = Mock()

                    def execute(sql: str, values: tuple[Any, ...] = ()) -> Mock:
                        probes = set(re.findall(r"to_regprocedure\('([^']+)'\)", sql))
                        self.assertEqual(probes, EXPECTED_BACKEND_SIGNATURES)
                        return Mock(
                            fetchone=Mock(return_value=(missing not in probes,))
                        )

                    frame.execute.side_effect = execute
                    with patch(
                        MODULE + ".relation_projection_frame",
                        return_value=nullcontext(frame),
                    ):
                        value = json.loads(
                            service.handle(
                                json.dumps(HelperDispatch().request(kind)).encode()
                            )
                        )
                    self.assertEqual(value["outcome"], "unsupported_query")
                    self.assertEqual(
                        value["error"], {"code": "feature", "feature": kind}
                    )
                    self.assertIsNone(value["access_receipt_ref"])
                    self.assertIsNone(value["receipt_ref"])
                    self.assertEqual(frame.execute.call_count, 1)
                    service._deliver.assert_not_called()

    def test_absent_source_module_never_opens_control_or_admits(self) -> None:
        for kind in ("inspect", "hydrate_source"):
            service = self.service()
            with patch(MODULE + "._helper_module_available", return_value=False):
                value = json.loads(
                    service.handle(json.dumps(HelperDispatch().request(kind)).encode())
                )
            self.assertEqual(value["outcome"], "unsupported_query")
            self.assertIsNone(value["access_receipt_ref"])
            service._control.assert_not_called()
            service._deliver.assert_not_called()

    def test_incomplete_schema_refuses_even_with_declared_functions(self) -> None:
        service = self.service()
        frame = Mock()
        frame.execute.return_value.fetchone.return_value = (False,)
        with patch(
            MODULE + ".relation_projection_frame", return_value=nullcontext(frame)
        ):
            value = json.loads(
                service.handle(json.dumps(HelperDispatch().request("inspect")).encode())
            )
        sql = frame.execute.call_args.args[0]
        for required in (
            "helper_artifact_ref",
            "helper_page_first",
            "helper_page_count",
            "helper_page_cursor",
            "query_helper_cursors",
            "relforcerowsecurity",
            "query_helper_clear_pin",
            "query_helper_purge",
            "NOT attnotnull",
            "query_steps_helper_kind_check",
            "query_steps_helper_complete_check",
            "convalidated",
        ):
            self.assertIn(required, sql)
        self.assertEqual(value["outcome"], "unsupported_query")
        self.assertIsNone(value["access_receipt_ref"])
        service._deliver.assert_not_called()

    def test_declared_backend_uses_actual_canonical_admission_statement(self) -> None:
        for kind in ("inspect", "hydrate_source"):
            service = self.service()
            frame = Mock()
            admission = {"delivery_ref": str(uuid4())}
            frame.execute.side_effect = [
                Mock(fetchone=Mock(return_value=(True,))),
                Mock(fetchone=Mock(return_value=(admission,))),
            ]
            request = parse_investigation_request(
                json.dumps(HelperDispatch().request(kind)).encode()
            )
            with patch(
                MODULE + ".relation_projection_frame", return_value=nullcontext(frame)
            ):
                result = service._admit(request, kind, "b" * 64)
            self.assertIs(result, admission)
            call = frame.execute.call_args
            self.assertIn("admit_query_delivery_v1", call.args[0])
            self.assertEqual(call.args[1][2:4], (kind, "b" * 64))
            # Hydration's 64 KiB is content; its whole reply has the general cap.
            self.assertEqual(call.args[1][5], 262144)
            self.assertEqual(frame.execute.call_count, 2)

    def test_authorization_failure_cannot_reach_helper_delivery_or_leak_details(
        self,
    ) -> None:
        service = self.service()
        with patch(
            MODULE + ".relation_projection_frame",
            side_effect=InsufficientPrivilege("fictional internal diagnostic"),
        ):
            value = json.loads(
                service.handle(json.dumps(HelperDispatch().request("inspect")).encode())
            )
        self.assertEqual(value["outcome"], "unavailable")
        self.assertIsNone(value["access_receipt_ref"])
        self.assertNotIn("fictional internal diagnostic", json.dumps(value))
        service._deliver.assert_not_called()

    def test_disclosure_probe_requires_exact_twelve_argument_signature(self) -> None:
        service = self.service()
        frame = Mock()
        frame.execute.return_value.fetchone.return_value = (False,)
        request = parse_investigation_request(
            json.dumps(HelperDispatch().request("inspect")).encode()
        )
        with patch(
            MODULE + ".relation_projection_frame", return_value=nullcontext(frame)
        ):
            with self.assertRaises(_Failure):
                service._admit(request, "inspect", "b" * 64)
        probes = re.findall(
            r"to_regprocedure\('([^']+)'\)", frame.execute.call_args.args[0]
        )
        signature = next(
            p for p in probes if ".record_query_helper_disclosure_v1(" in p
        )
        self.assertEqual(len(signature.split("(", 1)[1][:-1].split(",")), 12)

    def test_query_and_reuse_do_not_require_helper_module_or_backend(self) -> None:
        common = {
            "contract_version": 1,
            "run_ref": str(uuid4()),
            "step_key": str(uuid4()),
        }
        requests = [
            {
                **common,
                "kind": "query",
                "catalog_hash": SqlCatalog.installed().hash,
                "sql": "SELECT o.bead_id FROM memory_v1.observations o",
                "parameters": [],
                "inputs": [],
                "parents": [],
                "scope": {
                    "source_refs": [],
                    "known_at": "2026-10-19T00:00:00Z",
                    "view": "historical",
                },
                "intent": "enumerate",
                "max_result_bytes": 8192,
                "page_size": 1,
            },
            {
                **common,
                "kind": "reuse_result",
                "result": {"result_id": str(uuid4()), "content_digest": "a" * 64},
                "access": {"kind": "standalone"},
                "page_size": 1,
            },
        ]
        for payload in requests:
            with self.subTest(kind=payload["kind"]):
                service = self.service()
                frame = Mock()
                frame.execute.return_value.fetchone.return_value = (
                    {"delivery_ref": str(uuid4())},
                )
                with (
                    patch(
                        MODULE + "._helper_module_available", return_value=False
                    ) as available,
                    patch(
                        MODULE + ".relation_projection_frame",
                        return_value=nullcontext(frame),
                    ),
                ):
                    self.assertEqual(
                        service.handle(json.dumps(payload).encode()),
                        b"not-an-authenticated-response",
                    )
                available.assert_not_called()
                self.assertEqual(frame.execute.call_count, 1)
                self.assertIn(
                    "admit_query_delivery_v1", frame.execute.call_args.args[0]
                )
                self.assertNotIn("to_regprocedure", frame.execute.call_args.args[0])

    def test_non_boolean_backend_probe_never_admits(self) -> None:
        values: tuple[Any, ...] = (None, False, 1, "true", {})
        for value in values:
            with self.subTest(value=value):
                service = self.service()
                frame = Mock()
                frame.execute.return_value.fetchone.return_value = (value,)
                with patch(
                    MODULE + ".relation_projection_frame",
                    return_value=nullcontext(frame),
                ):
                    response = json.loads(
                        service.handle(
                            json.dumps(HelperDispatch().request("inspect")).encode()
                        )
                    )
                self.assertEqual(response["outcome"], "unsupported_query")
                self.assertIsNone(response["access_receipt_ref"])
                self.assertEqual(frame.execute.call_count, 1)
                service._deliver.assert_not_called()
