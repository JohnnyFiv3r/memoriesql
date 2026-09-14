"""Task-pinned typed reads composed into the existing worker's owned I/O."""

from collections.abc import AsyncIterator, Awaitable, Callable

from pydantic import ValidationError

from memoriesql.application.complete_input_execution import CompleteInputError
from memoriesql.application.semantic_task_contracts import SemanticResultStatus
from memoriesql.application.source_revisiting import (
    ReadSourceEvidence,
    RevisitingExecutionInput,
    RevisitingWindow,
    SourceDelivery,
    SourceEvidenceRead,
)
from memoriesql.infrastructure.jobs.complete_input_access import (
    CompleteInputEvidenceAccess,
)


class SourceRevisitingEvidenceAccess(CompleteInputEvidenceAccess):
    async def revisiting_windows(self) -> AsyncIterator[RevisitingWindow]:
        pending: RevisitingWindow | None = None
        async for window in super().windows():
            candidate = RevisitingWindow.model_validate(
                window.model_dump(mode="json") | {"contract_version": 2}
            )
            if pending is None:
                pending = candidate
                continue
            try:
                pending = RevisitingWindow(
                    task_id=self.task_id,
                    attempt_id=self.attempt_id,
                    package=self.package,
                    slices=(*pending.slices, *candidate.slices),
                    final=candidate.final,
                )
            except ValidationError:
                yield pending
                pending = candidate
        if pending is not None:
            yield pending

    def configure_revisiting(
        self,
        task: RevisitingExecutionInput,
        read: Callable[[ReadSourceEvidence], Awaitable[SourceEvidenceRead]],
        authorize: Callable[[SourceDelivery], Awaitable[None]],
    ) -> None:
        self._pins = {
            p.package_id: p
            for p in (
                task.payload.package,
                *(c.package for c in task.payload.authorized_context),
            )
        }
        self._revisit_read = read
        self._authorize_delivery = authorize

    async def revisit(self, request: ReadSourceEvidence) -> SourceEvidenceRead:
        pin = self._pins.get(request.package_id)
        if pin is None or pin.inventory_sha256 != request.inventory_sha256:
            raise CompleteInputError(
                SemanticResultStatus.POLICY_PAUSED, "source_revisiting.out_of_scope"
            )
        result = await self._revisit_read(request)
        if result.request != request:
            raise CompleteInputError(
                SemanticResultStatus.STALE_INPUT, "source_revisiting.read_mismatch"
            )
        return result

    async def authorize_delivery(self, delivery: SourceDelivery) -> None:
        await self._authorize_delivery(delivery)
