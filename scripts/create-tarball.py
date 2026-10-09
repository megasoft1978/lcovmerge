#!/usr/bin/env python3
"""Create a normalized gzip-compressed tar archive with one binary at root."""

from __future__ import annotations

import gzip
import sys
import tarfile
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit("Usage: create-tarball.py BINARY OUTPUT")
    source = Path(sys.argv[1])
    destination = Path(sys.argv[2])
    if not source.is_file():
        raise SystemExit(f"Binary not found: {source}")

    info = tarfile.TarInfo("lcovmerge")
    info.size = source.stat().st_size
    info.mode = 0o755
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = 0

    with destination.open("wb") as raw_output:
        with gzip.GzipFile(fileobj=raw_output, mode="wb", filename="", mtime=0) as compressed_output:
            with tarfile.open(fileobj=compressed_output, mode="w", format=tarfile.USTAR_FORMAT) as archive:
                with source.open("rb") as binary_stream:
                    archive.addfile(info, binary_stream)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
