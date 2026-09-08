"""Validate the explicit reviewed export and public-authored file inventory."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = "docs/provenance/export-provenance.json"
SPIKE_COMMIT = "ea29499d7107802dfa2566332ccb2b6ed0119bd1"


def public_files(root: Path) -> set[str]:
    output = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=root,
    )
    return {name for name in output.decode().split("\0") if name}


def verify(root: Path = ROOT) -> dict[str, int]:
    manifest = json.loads((root / MANIFEST).read_text(encoding="utf-8"))
    if manifest.get("reviewed_spike_commit") != SPIKE_COMMIT:
        raise ValueError("unexpected reviewed spike commit")
    exports = manifest["exports"]
    public_only = manifest["public_only"]
    rows = exports + public_only
    paths = [row["public_path"] for row in rows]
    if len(set(paths)) != len(paths):
        raise ValueError("duplicate provenance path")
    expected = set(paths) | {MANIFEST}
    actual = public_files(root)
    if actual != expected:
        raise ValueError(
            f"public file allowlist mismatch: missing={sorted(expected - actual)}; "
            f"unlisted={sorted(actual - expected)}"
        )
    for row in rows:
        path = PurePosixPath(row["public_path"])
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("unsafe provenance path")
        target = root / path
        if target.is_symlink() or target.resolve() != root.resolve() / path:
            raise ValueError(f"symlink or redirected public path: {path}")
        if hashlib.sha256(target.read_bytes()).hexdigest() != row["public_sha256"]:
            raise ValueError(f"public hash drift: {path}")
        if not row.get("reason") or not row.get("classification"):
            raise ValueError(f"missing inclusion decision: {path}")
    for row in exports:
        if row.get("reviewed_spike_commit") != SPIKE_COMMIT:
            raise ValueError("export commit mismatch")
        if not re.fullmatch(r"[0-9a-f]{64}", row["reviewed_spike_source_sha256"]):
            raise ValueError("invalid reviewed source-file hash")
        if "reviewed_spike_record_sha256" in row:
            payload = json.loads((root / row["public_path"]).read_bytes())
            canonical = json.dumps(
                payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
            ).encode()
            if hashlib.sha256(canonical).hexdigest() != row["reviewed_spike_record_sha256"]:
                raise ValueError("approved record payload drift")
    return {"exported_files": len(exports), "public_authored_files": len(public_only)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh-public-hashes", action="store_true")
    args = parser.parse_args()
    if args.refresh_public_hashes:
        path = ROOT / MANIFEST
        manifest = json.loads(path.read_text())
        for row in manifest["exports"] + manifest["public_only"]:
            row["public_sha256"] = hashlib.sha256(
                (ROOT / row["public_path"]).read_bytes()
            ).hexdigest()
        path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(json.dumps(verify(), sort_keys=True))


if __name__ == "__main__":
    main()
