"""Verify the explicit runtime closure, exact bytes and public-only imports."""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def verify(root: Path = ROOT) -> dict[str, int]:
    inventory = json.loads((root / "contracts/runtime-inventory.json").read_text())
    if inventory["default_policy"] != "deny":
        raise ValueError("runtime inventory must deny by default")
    rows = inventory["runtime"]
    paths = [row["path"] for row in rows]
    if len(paths) != len(set(paths)):
        raise ValueError("duplicate runtime path")
    # Every first-party dependency must resolve to a reviewed public source file.
    provenance = json.loads(
        (root / "docs/provenance/export-provenance.json").read_text()
    )
    approved = {
        r["public_path"]
        for key in ("exports", "public_only", "substrate_exports")
        for r in provenance.get(key, [])
    }
    approved.update(paths)
    allowed_external = {"psycopg", "pydantic", "pydantic_ai"}
    import sys

    for row in rows:
        name = row["path"]
        path = root / name
        if (
            not name.startswith("src/memoriesql/")
            or ".." in Path(name).parts
            or path.is_symlink()
            or path.resolve() != root.resolve() / name
        ):
            raise ValueError("unsafe runtime path")
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != row["sha256"]:
            raise ValueError(f"runtime content drift: {name}")
        if row["disposition"] == "copy" and row["source_sha256"] != row["sha256"]:
            raise ValueError("unchanged export differs from approved source")
        for node in ast.walk(ast.parse(content)):
            modules = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    raise ValueError("relative runtime import needs explicit audit")
                modules = [node.module or ""]
            for module in modules:
                if module.startswith("memoriesql."):
                    target = "src/" + module.replace(".", "/")
                    if (
                        target + ".py" not in approved
                        and target + "/__init__.py" not in approved
                    ):
                        raise ValueError(f"unclosed runtime import: {module}")
                elif (
                    module.split(".")[0]
                    not in sys.stdlib_module_names | allowed_external
                ):
                    raise ValueError(f"unapproved runtime dependency: {module}")
    return {"runtime_files": len(rows)}


if __name__ == "__main__":
    print(json.dumps(verify(), sort_keys=True))
