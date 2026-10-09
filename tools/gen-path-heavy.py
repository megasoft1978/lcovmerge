#!/usr/bin/env python3
"""Generate a deterministic LCOV tracefile with many distinct source paths."""

from __future__ import annotations

import argparse
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=2_000_000)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.count <= 0:
        parser.error("count must be positive")
    with args.output.open("w", encoding="ascii", buffering=1 << 20, newline="\n") as stream:
        for index in range(args.count):
            file_id = (index * 65537) % args.count
            stream.write(
                f"SF:/path-heavy/src/file-{file_id:07d}.c\n"
                "DA:1,1\nLF:1\nLH:1\nend_of_record\n"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
