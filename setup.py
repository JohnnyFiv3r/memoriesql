"""Stage the sole authored SQL stream into the installed package at build time."""

import hashlib
import json
import shutil
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py


class BuildMigrations(build_py):
    def run(self):
        super().run()
        root = Path(__file__).parent
        inventory = root / "contracts/migration-inventory.json"
        rows = json.loads(inventory.read_text())["migrations"]
        source = root / "migrations"
        names = [row["filename"] for row in rows]
        if len(names) != 18 or len(set(names)) != 18:
            raise ValueError("invalid migration inventory")
        if {p.name for p in source.iterdir()} != set(names):
            raise ValueError("unregistered migration source")
        target = Path(self.build_lib) / "memoriesql/infrastructure/postgres"
        destination = target / "_migrations"
        if destination.exists():
            shutil.rmtree(destination)
        destination.mkdir(parents=True)
        for row in rows:
            name = row["filename"]
            if Path(name).name != name:
                raise ValueError("unsafe migration name")
            path = source / name
            if (
                path.is_symlink()
                or hashlib.sha256(path.read_bytes()).hexdigest() != row["sha256"]
            ):
                raise ValueError("migration source drift")
            shutil.copyfile(path, destination / name)
        shutil.copyfile(inventory, target / "_migration_inventory.json")


setup(cmdclass={"build_py": BuildMigrations})
