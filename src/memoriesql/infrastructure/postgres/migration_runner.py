from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any, Final

from psycopg import Connection
from psycopg.pq import TransactionStatus

from memoriesql.infrastructure.postgres.schema_inspection import (
    inspect_schema,
    schema_snapshot_sha256,
)

RUNNER_CONTRACT_VERSION: Final = 1
MIGRATION_LOCK_KEY: Final = 7_431_886_601_115_481_013
MIGRATION_FILE = re.compile(r"^(?P<version>[0-9]{4})_(?P<name>[a-z0-9_]+)\.sql$")


class MigrationError(RuntimeError):
    """Raised when the ordered migration contract cannot be satisfied safely."""


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    path: Traversable
    sql: str
    sha256: str


@dataclass(frozen=True, slots=True)
class AppliedMigration:
    version: int
    name: str
    sha256: str


@dataclass(frozen=True, slots=True)
class MigrationReceipt:
    runner_contract_version: int
    database_name: str
    server_version_num: int
    from_version: int
    to_version: int
    target_version: int
    applied_migrations: tuple[AppliedMigration, ...]
    schema_snapshot_sha256: str

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def discover_migrations(
    directory: Path | None = None,
) -> tuple[Migration, ...]:
    # Only the explicit test hook accepts a filesystem directory. Production
    # always reads the distribution-owned stream and its reviewed hash inventory.
    packaged = directory is None
    resource_root = files("memoriesql.infrastructure.postgres")
    stream: Traversable = (
        resource_root.joinpath("_migrations") if directory is None else directory
    )
    expected_resources = {}
    if packaged:
        try:
            inventory = json.loads(
                resource_root.joinpath("_migration_inventory.json").read_text(
                    encoding="utf-8"
                )
            )
            rows = inventory["migrations"]
            if (
                inventory["contract_version"] != 1
                or inventory["default_policy"] != "deny"
                or len(rows) != 16
            ):
                raise ValueError("invalid migration inventory")
            expected_resources = {row["filename"]: row["sha256"] for row in rows}
            if len(expected_resources) != 16 or any(
                not isinstance(name, str)
                or MIGRATION_FILE.fullmatch(name) is None
                or not isinstance(digest, str)
                or re.fullmatch(r"[0-9a-f]{64}", digest) is None
                for name, digest in expected_resources.items()
            ):
                raise ValueError("invalid migration inventory entries")
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise MigrationError(
                "packaged migration inventory is unavailable"
            ) from error
    migrations: list[Migration] = []
    if isinstance(stream, Path) and stream.is_symlink():
        raise MigrationError("symlink in migration stream")
    if not stream.is_dir():
        raise MigrationError("migration resources are unavailable")
    entries = sorted(stream.iterdir(), key=lambda entry: entry.name)
    if packaged and {entry.name for entry in entries} != set(expected_resources):
        raise MigrationError("packaged migration inventory mismatch")
    for path in entries:
        if isinstance(path, Path) and path.is_symlink():
            raise MigrationError("symlink in migration stream")
        if not path.is_file():
            raise MigrationError(f"unsupported entry in migration stream: {path.name}")
        match = MIGRATION_FILE.fullmatch(path.name)
        if match is None:
            raise MigrationError(f"unsupported file in migration stream: {path.name}")
        data = path.read_bytes()
        sql = data.decode("utf-8")
        if (
            packaged
            and hashlib.sha256(data).hexdigest() != expected_resources[path.name]
        ):
            raise MigrationError("packaged migration checksum mismatch")
        if not sql.strip():
            raise MigrationError(f"empty migration: {path.name}")
        migrations.append(
            Migration(
                version=int(match.group("version")),
                name=match.group("name"),
                path=path,
                sql=sql,
                sha256=hashlib.sha256(sql.encode("utf-8")).hexdigest(),
            )
        )
    if not migrations:
        raise MigrationError("migration stream is empty")
    expected = list(range(1, len(migrations) + 1))
    actual = [migration.version for migration in migrations]
    if actual != expected:
        raise MigrationError(
            f"migration versions must be contiguous from 1: expected {expected}, got {actual}"
        )
    return tuple(migrations)


def validate_migration_history(
    migrations: Sequence[Migration], applied: Sequence[AppliedMigration]
) -> None:
    if len(applied) > len(migrations):
        raise MigrationError("database migration history is ahead of this package")
    for expected, recorded in zip(migrations, applied, strict=False):
        if recorded.version != expected.version:
            raise MigrationError(
                "database migration history is not a contiguous package prefix: "
                f"expected version {expected.version}, got {recorded.version}"
            )
        if recorded.name != expected.name:
            raise MigrationError(
                f"migration {recorded.version:04d} name drift: "
                f"database={recorded.name!r}, package={expected.name!r}"
            )
        if recorded.sha256 != expected.sha256:
            raise MigrationError(
                f"migration {recorded.version:04d} checksum drift: "
                f"database={recorded.sha256}, package={expected.sha256}"
            )


def _load_applied_migrations(
    connection: Connection[Any],
) -> tuple[AppliedMigration, ...]:
    exists = connection.execute(
        "SELECT pg_catalog.to_regclass('memoriesql.schema_migrations') IS NOT NULL"
    ).fetchone()
    if exists is None or not bool(exists[0]):
        return ()
    rows = connection.execute(
        """
        SELECT version, name, sha256
        FROM memoriesql.schema_migrations
        ORDER BY version
        """
    ).fetchall()
    return tuple(
        AppliedMigration(version=int(row[0]), name=str(row[1]), sha256=str(row[2]))
        for row in rows
    )


def migrate(
    connection: Connection[Any],
    *,
    expected_current_version: int,
    target_version: int,
) -> MigrationReceipt:
    if connection.info.transaction_status is not TransactionStatus.IDLE:
        raise MigrationError("migration runner requires an idle top-level connection")
    stream = discover_migrations()
    if not stream:
        raise MigrationError("migration stream is empty")
    latest_version = stream[-1].version
    if target_version < 0 or target_version > latest_version:
        raise MigrationError(
            f"target version must be between 0 and {latest_version}: {target_version}"
        )

    with connection.transaction():
        connection.execute("SET LOCAL lock_timeout = '10s'")
        connection.execute("SET LOCAL statement_timeout = '120s'")
        connection.execute("SET LOCAL search_path = pg_catalog")
        connection.execute(
            "SELECT pg_catalog.pg_advisory_xact_lock(%s)", (MIGRATION_LOCK_KEY,)
        )
        applied_before = _load_applied_migrations(connection)
        validate_migration_history(stream, applied_before)
        current_version = applied_before[-1].version if applied_before else 0
        if current_version != expected_current_version:
            raise MigrationError(
                "database version did not match the caller's bound: "
                f"expected {expected_current_version}, found {current_version}"
            )
        if target_version < current_version:
            raise MigrationError(
                "downgrades are unsupported; use the documented forward-only restore path"
            )

        planned = tuple(
            migration
            for migration in stream
            if current_version < migration.version <= target_version
        )
        for migration in planned:
            with connection.cursor() as cursor:
                cursor.execute(migration.sql, prepare=False)
            connection.execute(
                """
                INSERT INTO memoriesql.schema_migrations (
                    version,
                    name,
                    sha256,
                    runner_contract_version
                ) VALUES (%s, %s, %s, %s)
                """,
                (
                    migration.version,
                    migration.name,
                    migration.sha256,
                    RUNNER_CONTRACT_VERSION,
                ),
            )

        applied_after = _load_applied_migrations(connection)
        validate_migration_history(stream, applied_after)
        final_version = applied_after[-1].version if applied_after else 0
        if final_version != target_version:
            raise MigrationError(
                f"migration ended at version {final_version}, expected {target_version}"
            )
        snapshot = inspect_schema(connection)
        database_row = connection.execute(
            "SELECT current_database(), current_setting('server_version_num')::integer"
        ).fetchone()
        if database_row is None:
            raise MigrationError("Postgres did not return database identity metadata")

    return MigrationReceipt(
        runner_contract_version=RUNNER_CONTRACT_VERSION,
        database_name=str(database_row[0]),
        server_version_num=int(database_row[1]),
        from_version=current_version,
        to_version=target_version,
        target_version=target_version,
        applied_migrations=tuple(
            AppliedMigration(
                version=migration.version,
                name=migration.name,
                sha256=migration.sha256,
            )
            for migration in planned
        ),
        schema_snapshot_sha256=schema_snapshot_sha256(snapshot),
    )
