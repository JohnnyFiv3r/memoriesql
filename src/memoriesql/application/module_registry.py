from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from memoriesql.domain.module_manifest import (
    IDENTIFIER_PATTERN,
    KERNEL_VERSION,
    MANIFEST_SCHEMA_VERSION,
    SUPPORTED_MODULE_API,
    ContributionKind,
    ModuleManifest,
    RuntimePlacement,
)

ALLOWED_READ_PERMISSIONS = frozenset(
    {
        "beads",
        "claims",
        "relations",
        "sources",
        "proposals",
        "projections",
        "telemetry",
    }
)
ALLOWED_WRITE_PERMISSIONS = frozenset({"own_schema", "proposals", "kernel_commands"})
DATABASE_WRITE_DENIED_PLACEMENTS = frozenset(
    {RuntimePlacement.HOST_HELPER, RuntimePlacement.BROWSER_EXTENSION}
)
DEVELOPER_ARTIFACT_IDENTITY = "developer"
RESERVED_DESKTOP_IDENTITY = "desktop_full"


class ModuleState(StrEnum):
    ENABLED = "enabled"
    DISABLED = "disabled"
    DEGRADED = "degraded"
    INCOMPATIBLE = "incompatible"


@dataclass(frozen=True)
class ProfileDefinition:
    profile_id: str
    module_ids: tuple[str, ...]

    def canonical_payload(self) -> dict[str, object]:
        return {"id": self.profile_id, "modules": list(self.module_ids)}


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    subject: str
    detail: str

    def sort_key(self) -> tuple[str, str, str]:
        return self.code, self.subject, self.detail


@dataclass(frozen=True)
class ModuleHealth:
    module_id: str
    version: str
    state: ModuleState
    reason: str

    def canonical_payload(self) -> dict[str, str]:
        return {
            "id": self.module_id,
            "version": self.version,
            "state": self.state.value,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class KernelBoundaryHealth:
    canonical_store: str = "postgres"
    observation_ledger: str = "fixed_kernel_contract"
    semantic_authority: str = "fixed_kernel_contract"
    recall_contract: str = "fixed_kernel_contract"
    raw_sql_pluggable: bool = False


@dataclass(frozen=True)
class RegistryInspection:
    combined_module_version: str
    modules: tuple[ModuleHealth, ...]
    issues: tuple[ValidationIssue, ...]

    @property
    def compatible(self) -> bool:
        return not self.issues

    def require_compatible(self) -> None:
        if self.issues:
            raise RegistryValidationError(self)


class RegistryValidationError(ValueError):
    def __init__(self, inspection: RegistryInspection) -> None:
        self.inspection = inspection
        summary = "; ".join(
            f"{issue.code}({issue.subject}): {issue.detail}"
            for issue in inspection.issues
        )
        super().__init__(f"built-in module registry rejected: {summary}")


class ProfileCompositionError(ValueError):
    """Raised when a requested developer composition is not source-controlled."""


class ReleaseIdentityError(ValueError):
    """Raised when a developer composition claims a reserved release identity."""


@dataclass(frozen=True)
class CompositionHealthReport:
    profile_ids: tuple[str, ...]
    artifact_identity: str
    combined_module_version: str
    overall_state: ModuleState
    kernel: KernelBoundaryHealth
    modules: tuple[ModuleHealth, ...]

    def assert_artifact_identity(self, identity: str) -> None:
        if identity == self.artifact_identity:
            return
        if identity == RESERVED_DESKTOP_IDENTITY:
            raise ReleaseIdentityError(
                "desktop_full is reserved for the later SQL-12A signed release "
                "profile; PR-00A developer compositions cannot claim it"
            )
        raise ReleaseIdentityError(f"unknown artifact identity {identity!r}")


def _combined_version(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"modules-v1:{hashlib.sha256(encoded).hexdigest()}"


def _registry_payload(
    manifests: Sequence[ModuleManifest],
    profiles: Sequence[ProfileDefinition],
    placement_grants: Mapping[str, frozenset[RuntimePlacement]],
) -> dict[str, object]:
    return {
        "kernel_version": str(KERNEL_VERSION),
        "module_api": SUPPORTED_MODULE_API,
        "manifests": [
            manifest.canonical_payload()
            for manifest in sorted(
                manifests,
                key=lambda candidate: (candidate.module_id, candidate.version),
            )
        ],
        "profiles": [
            profile.canonical_payload()
            for profile in sorted(profiles, key=lambda candidate: candidate.profile_id)
        ],
        "placement_grants": {
            module_id: sorted(placement.value for placement in placements)
            for module_id, placements in sorted(placement_grants.items())
        },
    }


def _cycle_nodes(manifests_by_id: Mapping[str, ModuleManifest]) -> set[str]:
    graph = {
        module_id: tuple(
            sorted(
                requirement.module_id
                for requirement in manifest.requires_modules
                if requirement.module_id in manifests_by_id
            )
        )
        for module_id, manifest in manifests_by_id.items()
    }
    visited: set[str] = set()
    active: list[str] = []
    active_indexes: dict[str, int] = {}
    cyclic: set[str] = set()

    def visit(module_id: str) -> None:
        if module_id in visited:
            return
        if module_id in active_indexes:
            cyclic.update(active[active_indexes[module_id] :])
            return
        active_indexes[module_id] = len(active)
        active.append(module_id)
        for dependency_id in graph[module_id]:
            visit(dependency_id)
        active.pop()
        active_indexes.pop(module_id)
        visited.add(module_id)

    for module_id in sorted(graph):
        visit(module_id)
    return cyclic


def inspect_registry(
    manifests: Sequence[ModuleManifest],
    profiles: Sequence[ProfileDefinition],
    placement_grants: Mapping[str, frozenset[RuntimePlacement]],
) -> RegistryInspection:
    issues: list[ValidationIssue] = []
    manifests_by_id: dict[str, ModuleManifest] = {}
    module_id_counts: dict[str, int] = defaultdict(int)
    contribution_owners: dict[tuple[ContributionKind, str], str] = {}

    for manifest in manifests:
        module_id_counts[manifest.module_id] += 1
        manifests_by_id.setdefault(manifest.module_id, manifest)
    for module_id, count in sorted(module_id_counts.items()):
        if count > 1:
            issues.append(
                ValidationIssue(
                    "duplicate_module_id",
                    module_id,
                    f"registered {count} times",
                )
            )

    for module_id in sorted(placement_grants.keys() - manifests_by_id.keys()):
        issues.append(
            ValidationIssue(
                "unknown_placement_grant",
                module_id,
                "placement grant has no registered built-in module",
            )
        )

    for manifest in sorted(
        manifests, key=lambda candidate: (candidate.module_id, candidate.version)
    ):
        subject = manifest.module_id
        if IDENTIFIER_PATTERN.fullmatch(subject) is None:
            issues.append(
                ValidationIssue(
                    "invalid_module_id", subject, "identifier is not canonical"
                )
            )
        if manifest.manifest_schema != MANIFEST_SCHEMA_VERSION:
            issues.append(
                ValidationIssue(
                    "incompatible_manifest_schema",
                    subject,
                    f"requires {manifest.manifest_schema}, supports {MANIFEST_SCHEMA_VERSION}",
                )
            )
        if manifest.module_api != SUPPORTED_MODULE_API:
            issues.append(
                ValidationIssue(
                    "incompatible_module_api",
                    subject,
                    f"requires {manifest.module_api}, supports {SUPPORTED_MODULE_API}",
                )
            )
        if not manifest.requires_kernel.allows(KERNEL_VERSION):
            issues.append(
                ValidationIssue(
                    "incompatible_kernel_version",
                    subject,
                    f"requires {manifest.requires_kernel}, running {KERNEL_VERSION}",
                )
            )
        if not manifest.runtime_placements:
            issues.append(
                ValidationIssue(
                    "missing_runtime_placement",
                    subject,
                    "at least one placement is required",
                )
            )
        granted = placement_grants.get(subject)
        if granted is None:
            issues.append(
                ValidationIssue(
                    "missing_placement_grant",
                    subject,
                    "module has no source-controlled placement grant",
                )
            )
            granted = frozenset()
        seen_placements: set[RuntimePlacement] = set()
        for placement in manifest.runtime_placements:
            if not isinstance(placement, RuntimePlacement):
                issues.append(
                    ValidationIssue(
                        "invalid_runtime_placement",
                        subject,
                        f"unknown placement {placement!r}",
                    )
                )
                continue
            if placement in seen_placements:
                issues.append(
                    ValidationIssue(
                        "duplicate_runtime_placement", subject, placement.value
                    )
                )
            seen_placements.add(placement)
            if placement not in granted:
                issues.append(
                    ValidationIssue(
                        "unauthorized_runtime_placement", subject, placement.value
                    )
                )

        for permission in manifest.permissions_read:
            if permission not in ALLOWED_READ_PERMISSIONS:
                issues.append(
                    ValidationIssue("unauthorized_read_permission", subject, permission)
                )
        for permission in manifest.permissions_write:
            if permission not in ALLOWED_WRITE_PERMISSIONS:
                issues.append(
                    ValidationIssue(
                        "unauthorized_write_permission", subject, permission
                    )
                )
        if len(set(manifest.permissions_read)) != len(manifest.permissions_read):
            issues.append(
                ValidationIssue(
                    "duplicate_read_permission",
                    subject,
                    "permission identifiers repeat",
                )
            )
        if len(set(manifest.permissions_write)) != len(manifest.permissions_write):
            issues.append(
                ValidationIssue(
                    "duplicate_write_permission",
                    subject,
                    "permission identifiers repeat",
                )
            )
        for placement in sorted(
            seen_placements & DATABASE_WRITE_DENIED_PLACEMENTS,
            key=lambda candidate: candidate.value,
        ):
            for permission in sorted(set(manifest.permissions_write)):
                issues.append(
                    ValidationIssue(
                        "placement_write_denied",
                        subject,
                        f"{placement.value} cannot declare {permission}",
                    )
                )

        dependency_ids: set[str] = set()
        for requirement in manifest.requires_modules:
            if requirement.module_id in dependency_ids:
                issues.append(
                    ValidationIssue(
                        "duplicate_dependency", subject, requirement.module_id
                    )
                )
            dependency_ids.add(requirement.module_id)
            dependency = manifests_by_id.get(requirement.module_id)
            if dependency is None:
                issues.append(
                    ValidationIssue(
                        "missing_dependency", subject, requirement.module_id
                    )
                )
            elif not requirement.version.allows(dependency.version):
                issues.append(
                    ValidationIssue(
                        "incompatible_dependency_version",
                        subject,
                        f"{requirement.module_id} requires {requirement.version}, "
                        f"registered {dependency.version}",
                    )
                )

        local_contributions: set[tuple[ContributionKind, str]] = set()
        for contribution in manifest.contributions:
            key = contribution.kind, contribution.identifier
            if IDENTIFIER_PATTERN.fullmatch(contribution.identifier) is None:
                issues.append(
                    ValidationIssue(
                        "invalid_contribution_id",
                        subject,
                        f"{contribution.kind.value}:{contribution.identifier}",
                    )
                )
            if key in local_contributions:
                issues.append(
                    ValidationIssue(
                        "duplicate_contribution_id",
                        subject,
                        f"{contribution.kind.value}:{contribution.identifier}",
                    )
                )
            local_contributions.add(key)
            owner = contribution_owners.setdefault(key, subject)
            if owner != subject:
                issues.append(
                    ValidationIssue(
                        "contribution_collision",
                        subject,
                        f"{contribution.kind.value}:{contribution.identifier} "
                        f"already owned by {owner}",
                    )
                )

    cyclic = _cycle_nodes(manifests_by_id)
    for module_id in sorted(cyclic):
        issues.append(
            ValidationIssue(
                "dependency_cycle",
                module_id,
                "module participates in a dependency cycle",
            )
        )

    profile_ids: set[str] = set()
    for profile in sorted(profiles, key=lambda candidate: candidate.profile_id):
        if IDENTIFIER_PATTERN.fullmatch(profile.profile_id) is None:
            issues.append(
                ValidationIssue(
                    "invalid_profile_id",
                    profile.profile_id,
                    "identifier is not canonical",
                )
            )
        if profile.profile_id == RESERVED_DESKTOP_IDENTITY:
            issues.append(
                ValidationIssue(
                    "reserved_release_profile",
                    profile.profile_id,
                    "desktop_full belongs to the later SQL-12A signed profile",
                )
            )
        if profile.profile_id in profile_ids:
            issues.append(
                ValidationIssue(
                    "duplicate_profile_id",
                    profile.profile_id,
                    "profile identifiers repeat",
                )
            )
        profile_ids.add(profile.profile_id)
        selected = set(profile.module_ids)
        if len(selected) != len(profile.module_ids):
            issues.append(
                ValidationIssue(
                    "duplicate_profile_module",
                    profile.profile_id,
                    "module identifiers repeat",
                )
            )
        for module_id in sorted(selected):
            profile_manifest = manifests_by_id.get(module_id)
            if profile_manifest is None:
                issues.append(
                    ValidationIssue(
                        "unknown_profile_module", profile.profile_id, module_id
                    )
                )
                continue
            for requirement in profile_manifest.requires_modules:
                if requirement.module_id not in selected:
                    issues.append(
                        ValidationIssue(
                            "profile_missing_dependency",
                            profile.profile_id,
                            f"{module_id} requires {requirement.module_id}",
                        )
                    )

    sorted_issues = tuple(sorted(issues, key=ValidationIssue.sort_key))
    issues_by_subject: dict[str, list[ValidationIssue]] = defaultdict(list)
    for issue in sorted_issues:
        issues_by_subject[issue.subject].append(issue)
    module_health = tuple(
        ModuleHealth(
            module_id=manifest.module_id,
            version=str(manifest.version),
            state=(
                ModuleState.INCOMPATIBLE
                if manifest.module_id in issues_by_subject
                else ModuleState.DISABLED
            ),
            reason=(
                "; ".join(
                    f"{issue.code}: {issue.detail}"
                    for issue in issues_by_subject[manifest.module_id]
                )
                if manifest.module_id in issues_by_subject
                else "registry compatible; no profile composed"
            ),
        )
        for manifest in sorted(
            manifests, key=lambda candidate: (candidate.module_id, candidate.version)
        )
    )
    return RegistryInspection(
        combined_module_version=_combined_version(
            _registry_payload(manifests, profiles, placement_grants)
        ),
        modules=module_health,
        issues=sorted_issues,
    )


class BuiltInModuleRegistry:
    def __init__(
        self,
        manifests: tuple[ModuleManifest, ...],
        profiles: tuple[ProfileDefinition, ...],
        placement_grants: Mapping[str, frozenset[RuntimePlacement]],
        combined_module_version: str,
    ) -> None:
        self._manifests = manifests
        self._manifests_by_id = MappingProxyType(
            {manifest.module_id: manifest for manifest in manifests}
        )
        self._profiles = MappingProxyType(
            {profile.profile_id: profile for profile in profiles}
        )
        self._placement_grants = MappingProxyType(dict(placement_grants))
        self.combined_module_version = combined_module_version

    @classmethod
    def _from_source_controlled(
        cls,
        manifests: Sequence[ModuleManifest],
        profiles: Sequence[ProfileDefinition],
        placement_grants: Mapping[str, frozenset[RuntimePlacement]],
    ) -> BuiltInModuleRegistry:
        inspection = inspect_registry(manifests, profiles, placement_grants)
        inspection.require_compatible()
        return cls(
            tuple(sorted(manifests, key=lambda candidate: candidate.module_id)),
            tuple(sorted(profiles, key=lambda candidate: candidate.profile_id)),
            placement_grants,
            inspection.combined_module_version,
        )

    @property
    def manifests(self) -> tuple[ModuleManifest, ...]:
        return self._manifests

    @property
    def profile_ids(self) -> tuple[str, ...]:
        return tuple(self._profiles)

    def compose(
        self,
        profile_ids: Iterable[str],
        *,
        disabled_module_ids: Iterable[str] = (),
        degraded_modules: Mapping[str, str] | None = None,
    ) -> CompositionHealthReport:
        requested_profiles = set(profile_ids)
        if not requested_profiles:
            raise ProfileCompositionError(
                "at least one source-controlled profile is required"
            )
        if "core" in self._profiles:
            requested_profiles.add("core")
        unknown_profiles = requested_profiles - self._profiles.keys()
        if unknown_profiles:
            unknown = ", ".join(sorted(unknown_profiles))
            raise ProfileCompositionError(
                f"unknown source-controlled profile: {unknown}"
            )

        ordered_profiles = tuple(sorted(requested_profiles))
        selected = {
            module_id
            for profile_id in ordered_profiles
            for module_id in self._profiles[profile_id].module_ids
        }
        disabled = set(disabled_module_ids)
        unknown_disabled = disabled - selected
        if unknown_disabled:
            unknown = ", ".join(sorted(unknown_disabled))
            raise ProfileCompositionError(
                f"cannot disable modules outside the selected profiles: {unknown}"
            )
        degraded = dict(degraded_modules or {})
        unknown_degraded = degraded.keys() - selected
        if unknown_degraded:
            unknown = ", ".join(sorted(unknown_degraded))
            raise ProfileCompositionError(
                f"cannot degrade modules outside the selected profiles: {unknown}"
            )
        overlap = disabled & degraded.keys()
        if overlap:
            raise ProfileCompositionError(
                "a module cannot be both disabled and degraded: "
                + ", ".join(sorted(overlap))
            )
        if any(not reason.strip() for reason in degraded.values()):
            raise ProfileCompositionError("degraded module reasons cannot be empty")

        states = {
            module_id: (
                ModuleState.DISABLED
                if module_id in disabled
                else ModuleState.DEGRADED
                if module_id in degraded
                else ModuleState.ENABLED
            )
            for module_id in selected
        }
        reasons = {
            module_id: (
                "disabled by developer composition"
                if state is ModuleState.DISABLED
                else degraded[module_id]
                if state is ModuleState.DEGRADED
                else "selected and compatible"
            )
            for module_id, state in states.items()
        }
        changed = True
        while changed:
            changed = False
            for module_id in sorted(selected):
                if states[module_id] is not ModuleState.ENABLED:
                    continue
                unavailable_dependencies = sorted(
                    requirement.module_id
                    for requirement in self._manifests_by_id[module_id].requires_modules
                    if states.get(requirement.module_id) is not ModuleState.ENABLED
                )
                if unavailable_dependencies:
                    states[module_id] = ModuleState.DEGRADED
                    reasons[module_id] = "dependency unavailable: " + ", ".join(
                        unavailable_dependencies
                    )
                    changed = True

        health = tuple(
            ModuleHealth(
                module_id=manifest.module_id,
                version=str(manifest.version),
                state=states.get(manifest.module_id, ModuleState.DISABLED),
                reason=reasons.get(manifest.module_id, "not selected by profile"),
            )
            for manifest in self._manifests
        )
        overall_state = (
            ModuleState.DEGRADED
            if any(
                entry.module_id in selected and entry.state is not ModuleState.ENABLED
                for entry in health
            )
            else ModuleState.ENABLED
        )
        composition_version = _combined_version(
            {
                "registry": self.combined_module_version,
                "profiles": list(ordered_profiles),
                "modules": [
                    {
                        "id": entry.module_id,
                        "version": entry.version,
                        "state": entry.state.value,
                    }
                    for entry in health
                ],
            }
        )
        return CompositionHealthReport(
            profile_ids=ordered_profiles,
            artifact_identity=DEVELOPER_ARTIFACT_IDENTITY,
            combined_module_version=composition_version,
            overall_state=overall_state,
            kernel=KernelBoundaryHealth(),
            modules=health,
        )
