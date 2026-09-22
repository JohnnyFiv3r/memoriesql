"""Verify the explicit runtime closure and its public-only imports."""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHIPPED_NON_RUNTIME = {
    "src/memoriesql/__init__.py",
    "src/memoriesql/cli.py",
    "src/memoriesql/contracts/__init__.py",
}
ALLOWED_EXTERNAL = {"psycopg", "pydantic", "pydantic_ai"}


def verify(root: Path = ROOT) -> dict[str, int]:
    inventory = json.loads((root / "contracts/runtime-inventory.json").read_text())
    if inventory["default_policy"] != "deny":
        raise ValueError("runtime inventory must deny by default")
    rows = inventory["runtime"]
    paths = [row["path"] for row in rows]
    if len(paths) != len(set(paths)):
        raise ValueError("duplicate runtime path")
    # Every first-party import must resolve to a shipped package module.
    approved = set(paths) | SHIPPED_NON_RUNTIME
    for name in paths:
        path = root / name
        if (
            not name.startswith("src/memoriesql/")
            or ".." in Path(name).parts
            or path.is_symlink()
            or not path.is_file()
            or path.resolve() != root.resolve() / name
        ):
            raise ValueError(f"unsafe runtime path: {name}")
        for node in ast.walk(ast.parse(path.read_bytes())):
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
                    not in sys.stdlib_module_names | ALLOWED_EXTERNAL
                ):
                    raise ValueError(f"unapproved runtime dependency: {module}")
    return {"runtime_files": len(rows)}


if __name__ == "__main__":
    print(json.dumps(verify(), sort_keys=True))
