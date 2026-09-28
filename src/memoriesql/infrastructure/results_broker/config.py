"""The trusted query host's private configuration and its file discipline.

The configuration holds both database logins, so it is readable only by the
service user: a regular 0600 file, owned by the effective uid that loads it,
inside a directory only that uid can enter. Anything else is refused before a
byte is parsed. Refusal reasons never contain a secret or a path's contents.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import stat
from pathlib import Path
from typing import Literal
from uuid import UUID

from psycopg.conninfo import make_conninfo
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

MAX_CONFIG_BYTES = 64 * 1024
_ROLE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ConfigurationRefused(Exception):
    """An operator-actionable refusal; its message names a check, never a secret."""


class DatabaseEndpoint(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    host: str = Field(min_length=1, max_length=255)
    port: int = Field(ge=1, le=65535)
    name: str = Field(min_length=1, max_length=63)
    sslmode: Literal["disable", "prefer", "require", "verify-ca", "verify-full"] = (
        "prefer"
    )


class DatabaseLogin(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    role: str
    password: SecretStr

    @field_validator("role")
    @classmethod
    def plain_role(cls, value: str) -> str:
        if not _ROLE.fullmatch(value):
            raise ValueError("role must be a plain lowercase identifier")
        return value

    @field_validator("password")
    @classmethod
    def generated_password(cls, value: SecretStr) -> SecretStr:
        # Provisioning generates 43-character secrets; short ones are refused so
        # that exposure digests of these values cannot be guessed offline.
        if len(value.get_secret_value()) < 32:
            raise ValueError("password too short")
        return value


class BrokerConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Literal[1] = 1
    workspace_id: UUID
    client_uid: int = Field(ge=0, lt=2**32)
    socket_path: Path
    state_dir: Path
    database: DatabaseEndpoint
    control: DatabaseLogin
    reader: DatabaseLogin
    profile_sha256: str | None = None

    @field_validator("socket_path", "state_dir")
    @classmethod
    def absolute(cls, value: Path) -> Path:
        if not value.is_absolute() or ".." in value.parts:
            raise ValueError("paths must be absolute and normalized")
        return value

    @field_validator("profile_sha256")
    @classmethod
    def pinned_digest(cls, value: str | None) -> str | None:
        if value is not None and not _SHA256.fullmatch(value):
            raise ValueError("profile pin must be 64 lowercase hex")
        return value

    def control_conninfo(self) -> str:
        return self._conninfo(self.control)

    def reader_conninfo(self) -> str:
        return self._conninfo(self.reader)

    def _conninfo(self, login: DatabaseLogin) -> str:
        return make_conninfo(
            host=self.database.host,
            port=str(self.database.port),
            dbname=self.database.name,
            user=login.role,
            password=login.password.get_secret_value(),
            sslmode=self.database.sslmode,
            application_name="memoriesql-query-host",
            # Never consult a password file or service file of the host user.
            passfile=os.devnull,
        )

    def serialized(self) -> bytes:
        document = {
            "version": self.version,
            "workspace_id": str(self.workspace_id),
            "client_uid": self.client_uid,
            "socket_path": str(self.socket_path),
            "state_dir": str(self.state_dir),
            "database": self.database.model_dump(mode="json"),
            "control": {
                "role": self.control.role,
                "password": self.control.password.get_secret_value(),
            },
            "reader": {
                "role": self.reader.role,
                "password": self.reader.password.get_secret_value(),
            },
            "profile_sha256": self.profile_sha256,
        }
        return (json.dumps(document, indent=2, sort_keys=True) + "\n").encode("utf-8")


def generate_password() -> SecretStr:
    """A 256-bit URL-safe secret that needs no quoting in a connection string."""
    return SecretStr(secrets.token_urlsafe(32))


def private_directory(path: Path) -> None:
    """Refuse unless `path` is a real directory only the effective uid can use."""
    try:
        status = os.lstat(path)
    except OSError:
        raise ConfigurationRefused(
            f"{path.name or path}: directory unavailable"
        ) from None
    if not stat.S_ISDIR(status.st_mode):
        raise ConfigurationRefused(f"{path}: not a directory")
    if status.st_uid != os.geteuid():
        raise ConfigurationRefused(f"{path}: not owned by the service user")
    if status.st_mode & 0o077:
        raise ConfigurationRefused(f"{path}: must be mode 0700")


def load_config(path: Path) -> BrokerConfig:
    """Read the private configuration, refusing unsafe ownership or modes first."""
    private_directory(path.parent)
    try:
        descriptor = os.open(
            path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        )
    except OSError:
        raise ConfigurationRefused(f"{path}: configuration unavailable") from None
    try:
        status = os.fstat(descriptor)
        if not stat.S_ISREG(status.st_mode):
            raise ConfigurationRefused(f"{path}: not a regular file")
        if status.st_uid != os.geteuid():
            raise ConfigurationRefused(f"{path}: not owned by the service user")
        if status.st_mode & 0o077:
            raise ConfigurationRefused(f"{path}: must be mode 0600")
        if status.st_size > MAX_CONFIG_BYTES:
            raise ConfigurationRefused(f"{path}: configuration too large")
        data = os.read(descriptor, MAX_CONFIG_BYTES + 1)
    finally:
        os.close(descriptor)
    try:
        return BrokerConfig.model_validate_json(data)
    except ValueError:
        # Validation messages can quote input values; report only the file.
        raise ConfigurationRefused(f"{path}: invalid configuration") from None


def stage_config(path: Path, config: BrokerConfig) -> Path:
    """Write the new configuration beside `path` (0600, fsynced), not yet live."""
    private_directory(path.parent)
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
        os.fchmod(descriptor, 0o600)
        data = config.serialized()
        written = 0
        while written < len(data):
            written += os.write(descriptor, data[written:])
        os.fsync(descriptor)
    except BaseException:
        os.close(descriptor)
        staged.unlink(missing_ok=True)
        raise
    os.close(descriptor)
    return staged


def commit_config(staged: Path, path: Path) -> None:
    """Atomically replace the live configuration with a staged one."""
    os.replace(staged, path)
    directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
