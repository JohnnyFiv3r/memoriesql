"""Fictional bytes originate here, with no filesystem acquisition implementation."""

from __future__ import annotations

import base64
import hashlib
import unittest
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from pydantic import ValidationError

from memoriesql.application.capture.contracts import (
    CaptureSurface,
    KernelCaptureBinding,
)
from memoriesql.application.capture.host_protocol import (
    FileIdentity,
    HostReadResponse,
    HostReadStatus,
)
from memoriesql.application.capture.source_range import (
    RawSourceCaptureLoop,
    SourceRangePolicyReferences,
    build_capture_source_range_command,
)
from memoriesql.application.capture.transcript_fold import (
    DurableTranscriptRead,
    DurableTranscriptReadStatus,
    TranscriptFoldSourceRange,
)

DATA = b'{"fictional_orchard":"four trees"}\n'
HASH = hashlib.sha256(DATA).hexdigest()
IDENTITY = FileIdentity(platform="fictional", device=1, inode=7, birth_time_ns=0)
BINDING = KernelCaptureBinding(
    tenant_id=UUID(int=1),
    workspace_id=UUID(int=2),
    access_scope_id=UUID(int=3),
    source_object_id=UUID(int=4),
    expected_source_object_schema_version=1,
)
POLICIES = SourceRangePolicyReferences(
    capture_policy_id="orchard.capture", retention_policy_ref="orchard.retention"
)


class MemoryPorts:
    def __init__(self) -> None:
        self.saved: list[Any] = []
        self.submissions: list[Any] = []

    def load(self, capability_id: str) -> None:
        return None

    def commit(self, *, expected_revision: int, state: Any) -> None:
        self.saved.append(state)

    def read(self, request: Any) -> HostReadResponse:
        return HostReadResponse(
            request_id=request.request_id,
            status=HostReadStatus.DATA,
            file_identity=IDENTITY,
            start_offset=0,
            end_offset=len(DATA),
            observed_size=len(DATA),
            data_base64=base64.b64encode(DATA).decode(),
            data_sha256=HASH,
            end_of_file=True,
        )

    def capture(self, command: Any, *, recorded_at: datetime) -> Any:
        self.submissions.append(command)
        raise RuntimeError("fictional durable store failed")


class RetainedBytes(unittest.TestCase):
    def test_exact_chunks_and_checkpoint_hash_are_frozen(self) -> None:
        command = build_capture_source_range_command(
            payload=DATA,
            binding=BINDING,
            capability_id="orchard.bytes",
            connector_id="orchard.synthetic",
            observed_connector_version="1",
            capture_surface=CaptureSurface.SYNTHETIC,
            source_revision_key="orchard.revision",
            file_identity=IDENTITY,
            observed_source_format_version="orchard.v1",
            byte_start=0,
            checkpoint_key="orchard.cursor",
            expected_checkpoint_sequence=0,
            policies=POLICIES,
            chunk_size=7,
        )
        self.assertEqual(b"".join(chunk.payload for chunk in command.chunks), DATA)
        self.assertEqual(command.payload_sha256, HASH)
        self.assertEqual(command.checkpoint.next_sequence, 1)
        mutated = command.model_dump(mode="json")
        mutated["chunks"][0]["payload_sha256"] = "0" * 64
        with self.assertRaises(ValidationError):
            type(command).model_validate(mutated)
        self.assertEqual(command.checkpoint.expected_sequence, 0)

    def test_failed_durability_never_advances_cursor(self) -> None:
        ports = MemoryPorts()
        loop = RawSourceCaptureLoop(
            acquisition=ports,
            submission=ports,
            cursor_store=ports,
            binding=BINDING,
            capability_id="orchard.bytes",
            capability_credential="fictional-orchard-capability-credential",
            checkpoint_key="orchard.cursor",
            connector_id="orchard.synthetic",
            observed_connector_version="1",
            capture_surface=CaptureSurface.SYNTHETIC,
            source_revision_key="orchard.revision",
            observed_source_format_version="orchard.v1",
            policies=POLICIES,
        )
        with self.assertRaisesRegex(RuntimeError, "fictional durable"):
            loop.run_once(recorded_at=datetime.now(UTC))
        self.assertEqual(len(ports.submissions), 1)
        self.assertEqual(ports.saved, [])

    def test_fold_read_requires_exact_bytes_and_contiguous_lineage(self) -> None:
        lineage = TranscriptFoldSourceRange(
            source_range_receipt_id=UUID(int=5),
            receipt_byte_start=0,
            receipt_byte_end_exclusive=len(DATA),
            receipt_payload_sha256=HASH,
            byte_start=0,
            byte_end_exclusive=len(DATA),
            source_bytes_sha256=HASH,
        )
        read = DurableTranscriptRead(
            status=DurableTranscriptReadStatus.DATA,
            source_object_id=BINDING.source_object_id,
            source_revision_key="orchard.revision",
            file_identity=IDENTITY,
            file_identity_key=IDENTITY.stable_key,
            byte_start=0,
            byte_end_exclusive=len(DATA),
            durable_end_offset=len(DATA),
            payload_hex=DATA.hex(),
            payload_sha256=HASH,
            ranges=(lineage,),
            first_captured_at=datetime.now(UTC),
        )
        self.assertEqual(read.payload, DATA)
        update: dict[str, Any]
        for update in (
            {"payload_sha256": "0" * 64},
            {"durable_end_offset": len(DATA) - 1},
            {"ranges": []},
        ):
            with self.subTest(update=update), self.assertRaises(ValidationError):
                DurableTranscriptRead.model_validate(read.model_dump() | update)
