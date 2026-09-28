"""Host-private record of runs the trusted host started, for operator close.

PR-05's early close is owner-only: it acts as the run's own principal. The host
keeps, in its private state directory, which credential digest started each
run so an operator command (running as the service user) can close it. The
digest authenticates nothing without the host's trusted login, which this same
service user already holds. Entries are pruned a day after their run expires.
"""

from __future__ import annotations

import fcntl
import json
import os
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

from memoriesql.infrastructure.results_broker.config import private_directory

_RETAIN_AFTER_EXPIRY = timedelta(days=1)
_MAX_REGISTRY_BYTES = 4 * 1024 * 1024


def _time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@contextmanager
def _locked(state_dir: Path, *, exclusive: bool) -> Iterator[Path]:
    private_directory(state_dir)
    lock = os.open(
        state_dir / "runs.lock",
        os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
        0o600,
    )
    try:
        fcntl.flock(lock, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        yield state_dir / "runs.json"
    finally:
        os.close(lock)


def _load(path: Path) -> dict[str, dict[str, Any]]:
    try:
        descriptor = os.open(
            path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        )
    except FileNotFoundError:
        return {}
    try:
        data = os.read(descriptor, _MAX_REGISTRY_BYTES + 1)
    finally:
        os.close(descriptor)
    if len(data) > _MAX_REGISTRY_BYTES:
        raise ValueError("run registry exceeds its bound")
    value = json.loads(data)
    if not isinstance(value, dict):
        raise ValueError("invalid run registry")
    return value


def _store(path: Path, runs: dict[str, dict[str, Any]]) -> None:
    staged = path.with_name(f".{path.name}.{secrets.token_hex(8)}.new")
    descriptor = os.open(
        staged,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0),
        0o600,
    )
    try:
        data = json.dumps(runs, sort_keys=True).encode("utf-8")
        written = 0
        while written < len(data):
            written += os.write(descriptor, data[written:])
        os.fsync(descriptor)
    except BaseException:
        os.close(descriptor)
        staged.unlink(missing_ok=True)
        raise
    os.close(descriptor)
    os.replace(staged, path)


def record_run(
    state_dir: Path,
    run: dict[str, Any],
    *,
    credential_sha256: str,
    workspace_id: UUID,
    now: datetime | None = None,
) -> None:
    moment = now or datetime.now(UTC)
    with _locked(state_dir, exclusive=True) as path:
        runs = {
            ref: entry
            for ref, entry in _load(path).items()
            if _time(entry["expires_at"]) + _RETAIN_AFTER_EXPIRY > moment
        }
        runs[str(UUID(str(run["run_ref"])))] = {
            "credential_sha256": credential_sha256,
            "workspace_id": str(workspace_id),
            "started_at": str(run["started_at"]),
            "expires_at": str(run["expires_at"]),
        }
        _store(path, runs)


def run_owner(state_dir: Path, run_ref: UUID) -> tuple[str, UUID] | None:
    """The credential digest and workspace that started `run_ref`, if recorded."""
    with _locked(state_dir, exclusive=False) as path:
        entry = _load(path).get(str(run_ref))
    if entry is None:
        return None
    return str(entry["credential_sha256"]), UUID(str(entry["workspace_id"]))


def mark_closed(state_dir: Path, run_ref: UUID, closed_at: str) -> None:
    with _locked(state_dir, exclusive=True) as path:
        runs = _load(path)
        entry = runs.get(str(run_ref))
        if entry is not None:
            entry["closed_at"] = closed_at
            _store(path, runs)


def recorded_runs(
    state_dir: Path, *, now: datetime | None = None
) -> list[dict[str, Any]]:
    """Operator listing without credential digests."""
    moment = now or datetime.now(UTC)
    with _locked(state_dir, exclusive=False) as path:
        runs = _load(path)
    listing = []
    for ref, entry in sorted(runs.items(), key=lambda item: item[1]["started_at"]):
        closed = entry.get("closed_at")
        listing.append(
            {
                "run_ref": ref,
                "workspace_id": entry["workspace_id"],
                "started_at": entry["started_at"],
                "expires_at": entry["expires_at"],
                "closed_at": closed,
                "active": closed is None and _time(entry["expires_at"]) > moment,
            }
        )
    return listing
