from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

MANIFEST_SCHEMA_VERSION = 1
SUPPORTED_MODULE_API = 1
KERNEL_VERSION_TEXT = "1.0.0"

IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
VERSION_PATTERN = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
VERSION_BOUND_PATTERN = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:\.(0|[1-9][0-9]*))?$"
)
CONSTRAINT_PATTERN = re.compile(r"^(>=|<=|==|>|<)(.+)$")


class ManifestContractError(ValueError):
    """Raised when a typed manifest value cannot be represented safely."""


@dataclass(frozen=True, order=True)
class SemanticVersion:
    major: int
    minor: int
    patch: int

    def __post_init__(self) -> None:
        if min(self.major, self.minor, self.patch) < 0:
            raise ManifestContractError("semantic version parts cannot be negative")

    @classmethod
    def parse(cls, value: str) -> SemanticVersion:
        match = VERSION_PATTERN.fullmatch(value)
        if match is None:
            raise ManifestContractError(
                f"invalid semantic version {value!r}; expected MAJOR.MINOR.PATCH"
            )
        return cls(*(int(part) for part in match.groups()))

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"

    @classmethod
    def parse_range_bound(cls, value: str) -> SemanticVersion:
        match = VERSION_BOUND_PATTERN.fullmatch(value)
        if match is None:
            raise ManifestContractError(
                f"invalid semantic version bound {value!r}; expected "
                "MAJOR.MINOR or MAJOR.MINOR.PATCH"
            )
        major, minor, patch = match.groups()
        return cls(int(major), int(minor), int(patch or 0))


KERNEL_VERSION = SemanticVersion.parse(KERNEL_VERSION_TEXT)


@dataclass(frozen=True)
class VersionConstraint:
    operator: str
    version: SemanticVersion

    def allows(self, candidate: SemanticVersion) -> bool:
        if self.operator == ">=":
            return candidate >= self.version
        if self.operator == "<=":
            return candidate <= self.version
        if self.operator == ">":
            return candidate > self.version
        if self.operator == "<":
            return candidate < self.version
        if self.operator == "==":
            return candidate == self.version
        raise ManifestContractError(f"unsupported version operator {self.operator!r}")


@dataclass(frozen=True)
class VersionRange:
    constraints: tuple[VersionConstraint, ...]

    def __post_init__(self) -> None:
        if not self.constraints:
            raise ManifestContractError("a version range requires at least one bound")

    @classmethod
    def parse(cls, value: str) -> VersionRange:
        constraints: list[VersionConstraint] = []
        for raw_constraint in value.split(","):
            constraint = raw_constraint.strip()
            match = CONSTRAINT_PATTERN.fullmatch(constraint)
            if match is None:
                raise ManifestContractError(
                    f"invalid version constraint {constraint!r} in {value!r}"
                )
            constraints.append(
                VersionConstraint(
                    match.group(1),
                    SemanticVersion.parse_range_bound(match.group(2)),
                )
            )
        return cls(tuple(constraints))

    def allows(self, candidate: SemanticVersion) -> bool:
        return all(constraint.allows(candidate) for constraint in self.constraints)

    def __str__(self) -> str:
        return ",".join(
            f"{constraint.operator}{constraint.version}"
            for constraint in self.constraints
        )


class ModuleKind(StrEnum):
    CAPABILITY = "capability"
    CAPTURE_ADAPTER = "capture_adapter"
    HARNESS_BRIDGE = "harness_bridge"
    PROVIDER = "provider"
    SURFACE = "surface"


class ModuleAudience(StrEnum):
    END_USER = "end_user"
    ADMINISTRATOR = "administrator"
    DEVELOPER = "developer"
    INTERNAL = "internal"


class SetupClass(StrEnum):
    AUTOMATIC = "automatic"
    GUIDED_PERMISSION = "guided_permission"
    CONFIGURATION = "configuration"
    PROJECT_PATCH = "project_patch"


class RuntimePlacement(StrEnum):
    API_CONTAINER = "api_container"
    WORKER_CONTAINER = "worker_container"
    HOST_HELPER = "host_helper"
    DESKTOP_UI = "desktop_ui"
    BROWSER_EXTENSION = "browser_extension"
    EXTERNAL_SERVICE = "external_service"


class ContributionKind(StrEnum):
    EVENT_EMITTER = "event_emitter"
    HEALTH_CHECK = "health_check"
    JOB = "job"
    MATERIALIZER = "materializer"
    MIGRATION = "migration"
    PROPOSAL_KIND = "proposal_kind"
    QUERY_OPERATION = "query_operation"
    SEMANTIC_TASK = "semantic_task"
    UI_PANEL = "ui_panel"
    VIEW = "view"


@dataclass(frozen=True)
class Contribution:
    kind: ContributionKind
    identifier: str

    def canonical_payload(self) -> dict[str, str]:
        return {"kind": self.kind.value, "id": self.identifier}


@dataclass(frozen=True)
class ModuleRequirement:
    module_id: str
    version: VersionRange

    def canonical_payload(self) -> dict[str, str]:
        return {"id": self.module_id, "version": str(self.version)}


@dataclass(frozen=True)
class ModuleManifest:
    module_id: str
    version: SemanticVersion
    module_api: int
    kind: ModuleKind
    audience: ModuleAudience
    setup_class: SetupClass
    runtime_placements: tuple[RuntimePlacement | str, ...]
    requires_kernel: VersionRange
    requires_modules: tuple[ModuleRequirement, ...] = ()
    permissions_read: tuple[str, ...] = ()
    permissions_write: tuple[str, ...] = ()
    contributions: tuple[Contribution, ...] = ()
    manifest_schema: int = MANIFEST_SCHEMA_VERSION

    def canonical_payload(self) -> dict[str, object]:
        return {
            "manifest_schema": self.manifest_schema,
            "id": self.module_id,
            "version": str(self.version),
            "module_api": self.module_api,
            "kind": self.kind.value,
            "audience": self.audience.value,
            "setup_class": self.setup_class.value,
            "runtime_placements": [
                placement.value
                if isinstance(placement, RuntimePlacement)
                else placement
                for placement in self.runtime_placements
            ],
            "requires": {
                "kernel": str(self.requires_kernel),
                "modules": [
                    requirement.canonical_payload()
                    for requirement in self.requires_modules
                ],
            },
            "permissions": {
                "reads": list(self.permissions_read),
                "writes": list(self.permissions_write),
            },
            "contributions": [
                contribution.canonical_payload() for contribution in self.contributions
            ],
        }
