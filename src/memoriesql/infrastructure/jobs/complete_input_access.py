"""Lossless bounded transport into execution windows, independent of read pages."""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator, Awaitable, Callable
from uuid import UUID

from memoriesql.application.complete_input_execution import (
    WINDOW_CHARACTERS,
    WINDOW_JSON_BYTES,
    CompleteEvidenceBatch,
    CompleteInputError,
    EvidenceExecutionWindow,
    EvidenceExposureSlice,
    ReadCompleteEvidence,
)
from memoriesql.application.logical_unit_materialization import SealedPackagePin
from memoriesql.application.semantic_task_contracts import (
    SemanticResultStatus,
    canonical_json_bytes,
)


class CompleteInputEvidenceAccess:
    def __init__(
        self,
        *,
        task_id: UUID,
        attempt_id: UUID,
        package: SealedPackagePin,
        read: Callable[[ReadCompleteEvidence], Awaitable[CompleteEvidenceBatch]],
        authorize: Callable[[], Awaitable[None]],
    ) -> None:
        self.task_id = task_id
        self.attempt_id = attempt_id
        self.package = package
        self.read = read
        self.authorize = authorize

    async def authorize_dispatch(self) -> None:
        await self.authorize()

    async def windows(self) -> AsyncIterator[EvidenceExecutionWindow]:
        ordinal = 0
        inventory_hash = hashlib.sha256()
        characters = byte_count = 0
        slices: list[EvidenceExposureSlice] = []
        window_characters = 0
        while ordinal < self.package.required_parts:
            page = await self.read(
                ReadCompleteEvidence(
                    package_id=self.package.package_id,
                    inventory_sha256=self.package.inventory_sha256,
                    next_ordinal=ordinal,
                )
            )
            if (
                page.package_id != self.package.package_id
                or page.inventory_sha256 != self.package.inventory_sha256
                or not page.parts
            ):
                raise CompleteInputError(
                    SemanticResultStatus.STALE_INPUT,
                    "complete_input.reader_package_mismatch",
                )
            for part in page.parts:
                entry = part.inventory
                raw = part.content.encode("utf-8")
                if (
                    entry.ordinal != ordinal
                    or entry.characters != len(part.content)
                    or entry.utf8_bytes != len(raw)
                    or entry.content_sha256 != hashlib.sha256(raw).hexdigest()
                ):
                    raise CompleteInputError(
                        SemanticResultStatus.STALE_INPUT, "complete_input.part_mismatch"
                    )
                inventory_hash.update(
                    hashlib.sha256(canonical_json_bytes(entry.model_dump(mode="json")))
                    .hexdigest()
                    .encode("ascii")
                )
                characters += len(part.content)
                byte_count += len(raw)
                ordinal += 1
                offset = 0
                while offset < len(part.content):
                    count = min(
                        WINDOW_CHARACTERS - window_characters,
                        len(part.content) - offset,
                    )
                    item = (
                        EvidenceExposureSlice(
                            inventory=entry,
                            start_character=offset,
                            content=part.content[offset : offset + count],
                        )
                        if count
                        else None
                    )

                    def fits(candidate: EvidenceExposureSlice) -> bool:
                        data = dict(
                            contract_version=1,
                            task_id=str(self.task_id),
                            attempt_id=str(self.attempt_id),
                            package=self.package.model_dump(mode="json"),
                            slices=[
                                s.model_dump(mode="json") for s in [*slices, candidate]
                            ],
                            final=False,
                        )
                        return len(canonical_json_bytes(data)) <= WINDOW_JSON_BYTES

                    if item is not None and not fits(item):
                        # JSON escaping can use twelve bytes per astral code
                        # point. Find the largest exact prefix that fits the
                        # remaining byte capacity, including earlier slices,
                        # without changing source-unit identity.
                        low, high = 1, count - 1
                        item = None
                        while low <= high:
                            size = (low + high) // 2
                            candidate = EvidenceExposureSlice(
                                inventory=entry,
                                start_character=offset,
                                content=part.content[offset : offset + size],
                            )
                            if fits(candidate):
                                item = candidate
                                low = size + 1
                            else:
                                high = size - 1
                        count = len(item.content) if item is not None else 0
                    if item is None or not fits(item):
                        if slices:
                            yield self._window(slices, final=False)
                            slices, window_characters = [], 0
                            continue
                        raise ValueError(
                            "complete input window cannot represent fragment"
                        )
                    slices.append(item)
                    offset += count
                    window_characters += count
            expected_next = ordinal if ordinal < self.package.required_parts else None
            if page.next_ordinal != expected_next:
                raise CompleteInputError(
                    SemanticResultStatus.STALE_INPUT,
                    "complete_input.continuation_mismatch",
                )
        if (
            inventory_hash.hexdigest() != self.package.inventory_sha256
            or characters != self.package.required_characters
            or byte_count != self.package.required_utf8_bytes
        ):
            raise CompleteInputError(
                SemanticResultStatus.STALE_INPUT, "complete_input.seal_mismatch"
            )
        if slices:
            yield self._window(slices, final=True)

    def _window(
        self, slices: list[EvidenceExposureSlice], *, final: bool
    ) -> EvidenceExecutionWindow:
        return EvidenceExecutionWindow(
            task_id=self.task_id,
            attempt_id=self.attempt_id,
            package=self.package,
            slices=tuple(slices),
            final=final,
        )
