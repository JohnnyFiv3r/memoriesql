from __future__ import annotations

import argparse
import hashlib
import json
import stat
import tarfile
import zipfile
from collections.abc import Sequence
from pathlib import Path, PurePosixPath

VERSION = "0.0.2a1"
ROOT = Path(__file__).resolve().parents[1]
MIGRATION_ROWS = json.loads((ROOT / "contracts/migration-inventory.json").read_text())[
    "migrations"
]
MIGRATION_FILES = {row["filename"] for row in MIGRATION_ROWS}
RUNTIME_FILES = {
    "memoriesql/infrastructure/__init__.py",
    "memoriesql/infrastructure/postgres/__init__.py",
    "memoriesql/infrastructure/postgres/migration_runner.py",
    "memoriesql/infrastructure/postgres/schema_inspection.py",
}
RESOURCE_FILES = {
    "memoriesql/infrastructure/postgres/_migration_inventory.json",
    *(
        f"memoriesql/infrastructure/postgres/_migrations/{name}"
        for name in MIGRATION_FILES
    ),
}
CATALOG_FILES = {
    "cli.json",
    "connector.json",
    "http-openapi.json",
    "json-schema.json",
    "mcp.json",
    "module.json",
    "python.json",
    "sql-recall-operations.json",
}
EXPECTED_PACKAGE_FILES = {
    "memoriesql/__init__.py",
    "memoriesql/cli.py",
    "memoriesql/contracts/__init__.py",
    *(f"memoriesql/contracts/_catalogs/{name}" for name in CATALOG_FILES),
}
EXPECTED_PACKAGE_FILES |= RUNTIME_FILES | RESOURCE_FILES
EXPECTED_WHEEL_MEMBERS = EXPECTED_PACKAGE_FILES | {
    f"memoriesql-{VERSION}.dist-info/METADATA",
    f"memoriesql-{VERSION}.dist-info/RECORD",
    f"memoriesql-{VERSION}.dist-info/WHEEL",
    f"memoriesql-{VERSION}.dist-info/entry_points.txt",
    f"memoriesql-{VERSION}.dist-info/top_level.txt",
    f"memoriesql-{VERSION}.dist-info/licenses/LICENSE",
    f"memoriesql-{VERSION}.dist-info/licenses/NOTICE",
}
PACKAGE_APIS = (
    "memoriesql.__version__",
    "memoriesql.contracts.CATALOG_KINDS",
    "memoriesql.contracts.CatalogNotFoundError",
    "memoriesql.contracts.ContractNotFoundError",
    "memoriesql.contracts.available_catalogs",
    "memoriesql.contracts.load_catalog",
    "memoriesql.contracts.iter_contracts",
    "memoriesql.contracts.get_contract",
    "memoriesql.contracts.contract_inventory",
    "memoriesql --version",
    "memoriesql contracts",
    "memoriesql contract",
)
FORBIDDEN_PARTS = {
    ".env",
    "apps",
    "artifacts",
    "docs",
    "tests",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_path(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"unsafe archive path: {name}")
    lowered = {part.lower() for part in path.parts}
    forbidden = lowered & FORBIDDEN_PARTS
    if forbidden:
        raise ValueError(
            f"forbidden archive path component {sorted(forbidden)[0]}: {name}"
        )
    return path


def _wheel_inventory(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as archive:
        members = sorted(item.filename for item in archive.infolist())
        for item in archive.infolist():
            _safe_path(item.filename)
            mode = item.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise ValueError(f"wheel contains symlink: {item.filename}")
        if set(members) != EXPECTED_WHEEL_MEMBERS:
            raise ValueError(
                "wheel archive allowlist mismatch; "
                f"missing={sorted(EXPECTED_WHEEL_MEMBERS - set(members))}; "
                f"unexpected={sorted(set(members) - EXPECTED_WHEEL_MEMBERS)}"
            )
        if len(members) != len(set(members)):
            raise ValueError("duplicate wheel member")
        for row in MIGRATION_ROWS:
            data = archive.read(
                "memoriesql/infrastructure/postgres/_migrations/" + row["filename"]
            )
            if hashlib.sha256(data).hexdigest() != row["sha256"]:
                raise ValueError("wheel migration byte drift")
        package_members = {name for name in members if name.startswith("memoriesql/")}
        if package_members != EXPECTED_PACKAGE_FILES:
            missing = sorted(EXPECTED_PACKAGE_FILES - package_members)
            unexpected = sorted(package_members - EXPECTED_PACKAGE_FILES)
            raise ValueError(
                f"wheel package allowlist mismatch; missing={missing}; "
                f"unexpected={unexpected}"
            )
        metadata_name = next(
            name for name in members if name.endswith(".dist-info/METADATA")
        )
        metadata = archive.read(metadata_name).decode("utf-8")
        for expected in (
            "Name: memoriesql",
            f"Version: {VERSION}",
            "Requires-Python: <3.15,>=3.13",
        ):
            if expected not in metadata:
                raise ValueError(f"wheel metadata missing: {expected}")
        if [
            line for line in metadata.splitlines() if line.startswith("Requires-Dist:")
        ] != ["Requires-Dist: psycopg[binary]==3.3.3"]:
            raise ValueError("unexpected runtime dependency closure")
        for expected in (
            "License-Expression: Apache-2.0",
            "License-File: LICENSE",
            "License-File: NOTICE",
            "Author: John Inniger",
        ):
            if expected not in metadata:
                raise ValueError(f"wheel metadata missing: {expected}")
        notice_name = f"memoriesql-{VERSION}.dist-info/licenses/NOTICE"
        if archive.read(notice_name).decode("utf-8") != (
            "memoriesQL\nCopyright 2026 John Inniger\n"
        ):
            raise ValueError("wheel NOTICE attribution drift")
    return members


def _sdist_inventory(path: Path) -> list[str]:
    with tarfile.open(path, mode="r:gz") as archive:
        members = sorted(item.name for item in archive.getmembers())
        root = f"memoriesql-{VERSION}/"
        expected_members = {
            root.removesuffix("/"),
            f"{root}LICENSE",
            f"{root}MANIFEST.in",
            f"{root}NOTICE",
            f"{root}PKG-INFO",
            f"{root}PYPI_README.md",
            f"{root}README.md",
            f"{root}pyproject.toml",
            f"{root}setup.cfg",
            f"{root}src",
            f"{root}src/memoriesql",
            f"{root}src/memoriesql.egg-info",
            f"{root}src/memoriesql.egg-info/PKG-INFO",
            f"{root}src/memoriesql.egg-info/SOURCES.txt",
            f"{root}src/memoriesql.egg-info/dependency_links.txt",
            f"{root}src/memoriesql.egg-info/entry_points.txt",
            f"{root}src/memoriesql.egg-info/top_level.txt",
            f"{root}src/memoriesql/contracts",
            f"{root}src/memoriesql/contracts/_catalogs",
            *(f"{root}src/{name}" for name in EXPECTED_PACKAGE_FILES - RESOURCE_FILES),
        }
        expected_members |= {
            f"{root}setup.py",
            f"{root}contracts",
            f"{root}contracts/migration-inventory.json",
            f"{root}migrations",
            *(f"{root}migrations/{name}" for name in MIGRATION_FILES),
            f"{root}src/memoriesql/infrastructure",
            f"{root}src/memoriesql/infrastructure/postgres",
            f"{root}src/memoriesql.egg-info/requires.txt",
        }
        if len(members) != len(set(members)):
            raise ValueError("duplicate sdist member")
        for row in MIGRATION_ROWS:
            member = archive.extractfile(root + "migrations/" + row["filename"])
            if (
                member is None
                or hashlib.sha256(member.read()).hexdigest() != row["sha256"]
            ):
                raise ValueError("sdist migration byte drift")
        for item in archive.getmembers():
            _safe_path(item.name)
            if item.issym() or item.islnk() or item.isdev():
                raise ValueError(f"sdist contains unsafe member: {item.name}")
        if set(members) != expected_members:
            raise ValueError(
                "sdist archive allowlist mismatch; "
                f"missing={sorted(expected_members - set(members))}; "
                f"unexpected={sorted(set(members) - expected_members)}"
            )
        package_members = {
            item.name.removeprefix(f"{root}src/")
            for item in archive.getmembers()
            if item.isfile()
            and item.name.startswith(f"{root}src/memoriesql/")
            and not item.name.startswith(f"{root}src/memoriesql.egg-info/")
        }
        if package_members != EXPECTED_PACKAGE_FILES - RESOURCE_FILES:
            missing = sorted(
                (EXPECTED_PACKAGE_FILES - RESOURCE_FILES) - package_members
            )
            unexpected = sorted(package_members - EXPECTED_PACKAGE_FILES)
            raise ValueError(
                f"sdist package allowlist mismatch; missing={missing}; "
                f"unexpected={unexpected}"
            )
        metadata = archive.extractfile(f"{root}PKG-INFO")
        if metadata is None:
            raise ValueError("sdist is missing PKG-INFO")
        metadata_text = metadata.read().decode("utf-8")
        for expected in (
            "Name: memoriesql",
            f"Version: {VERSION}",
            "Requires-Python: <3.15,>=3.13",
        ):
            if expected not in metadata_text:
                raise ValueError(f"sdist metadata missing: {expected}")
        if [
            line
            for line in metadata_text.splitlines()
            if line.startswith("Requires-Dist:")
        ] != ["Requires-Dist: psycopg[binary]==3.3.3"]:
            raise ValueError("unexpected runtime dependency closure")
        for expected in (
            "License-Expression: Apache-2.0",
            "License-File: LICENSE",
            "License-File: NOTICE",
            "Author: John Inniger",
        ):
            if expected not in metadata_text:
                raise ValueError(f"sdist metadata missing: {expected}")
        notice = archive.extractfile(f"{root}NOTICE")
        if notice is None or notice.read().decode("utf-8") != (
            "memoriesQL\nCopyright 2026 John Inniger\n"
        ):
            raise ValueError("sdist NOTICE attribution drift")
    return members


def _record_inventory(path: Path) -> dict[str, object]:
    identifiers: list[str] = []
    counts: dict[str, int] = {}
    with zipfile.ZipFile(path) as archive:
        for filename in sorted(CATALOG_FILES):
            member = f"memoriesql/contracts/_catalogs/{filename}"
            catalog = json.loads(archive.read(member))
            entries = catalog["entries"]
            counts[catalog["kind"]] = len(entries)
            identifiers.extend(entry["id"] for entry in entries)
    return {
        "count": len(identifiers),
        "counts_by_catalog": counts,
        "identifiers": sorted(identifiers),
    }


def inspect_distribution(directory: Path) -> dict[str, object]:
    wheels = sorted(directory.glob("*.whl"))
    sdists = sorted(directory.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise ValueError("expected exactly one wheel and one source distribution")
    artifacts = []
    for path, members in (
        (wheels[0], _wheel_inventory(wheels[0])),
        (sdists[0], _sdist_inventory(sdists[0])),
    ):
        artifacts.append(
            {
                "filename": path.name,
                "size_bytes": path.stat().st_size,
                "sha256": _sha256(path),
                "members": members,
            }
        )
    return {
        "distribution": "memoriesql",
        "version": VERSION,
        "package_apis": list(PACKAGE_APIS),
        "records": _record_inventory(wheels[0]),
        "artifacts": artifacts,
    }


def parse_args(arguments: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inspect the allowlisted memoriesQL Python distributions."
    )
    parser.add_argument("directory", type=Path)
    return parser.parse_args(arguments)


def main(arguments: Sequence[str] | None = None) -> int:
    args = parse_args(arguments)
    print(json.dumps(inspect_distribution(args.directory), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
