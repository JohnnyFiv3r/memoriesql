from __future__ import annotations

import argparse
import gzip
import io
import os
import stat
import tarfile
import tempfile
from collections.abc import Sequence
from pathlib import Path

NORMALIZED_MTIME = 315532800


def normalize_sdist(path: Path) -> None:
    entries: list[tuple[tarfile.TarInfo, bytes | None]] = []
    with tarfile.open(path, mode="r:gz") as source:
        for original in source.getmembers():
            if not (original.isfile() or original.isdir()):
                raise ValueError(f"unsupported sdist member type: {original.name}")
            content = None
            if original.isfile():
                extracted = source.extractfile(original)
                if extracted is None:
                    raise ValueError(f"unreadable sdist member: {original.name}")
                content = extracted.read()
            entry = tarfile.TarInfo(original.name)
            entry.type = original.type
            entry.size = len(content) if content is not None else 0
            entry.mode = 0o644 if original.isfile() else 0o755
            entry.mtime = NORMALIZED_MTIME
            entry.uid = 0
            entry.gid = 0
            entry.uname = ""
            entry.gname = ""
            entries.append((entry, content))

    tar_buffer = io.BytesIO()
    with tarfile.open(fileobj=tar_buffer, mode="w", format=tarfile.PAX_FORMAT) as output:
        for entry, content in sorted(entries, key=lambda item: item[0].name):
            output.addfile(entry, io.BytesIO(content) if content is not None else None)

    normalized = gzip.compress(
        tar_buffer.getvalue(), compresslevel=9, mtime=NORMALIZED_MTIME
    )
    descriptor, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=".sdist-")
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(normalized)
        os.chmod(temporary_name, stat.S_IMODE(path.stat().st_mode))
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def parse_args(arguments: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Normalize one Python sdist for byte-identical rebuilds."
    )
    parser.add_argument("sdist", type=Path)
    return parser.parse_args(arguments)


def main(arguments: Sequence[str] | None = None) -> int:
    args = parse_args(arguments)
    normalize_sdist(args.sdist)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
