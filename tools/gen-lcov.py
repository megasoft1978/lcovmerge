#!/usr/bin/env python3
"""Deterministic small fixture generator; benchmark runs use the bench C generator."""

from __future__ import annotations

import argparse
import random
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--shards", type=int, default=2)
    parser.add_argument("--files", type=int, default=20)
    parser.add_argument("--lines", type=int, default=80)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--checksums", action="store_true")
    args = parser.parse_args()
    if min(args.shards, args.files, args.lines) <= 0:
        parser.error("shards, files, and lines must be positive")
    rng = random.Random(args.seed)
    args.out.mkdir(parents=True, exist_ok=True)
    for shard in range(args.shards):
        path = args.out / f"shard-{shard:04}.info"
        with path.open("w", encoding="utf-8", newline="\n") as stream:
            for source in range(args.files):
                if rng.randrange(100) >= 65:
                    continue
                stream.write(f"TN:test-{shard}\nSF:/generated/pkg{source // 100:04}/source-{source:06}.c\n")
                for function in range(4):
                    line = 1 + function * max(1, args.lines // 4)
                    stream.write(f"FN:{line},function_{function:03}\n")
                    stream.write(f"FNDA:{rng.randrange(8)},{'function_%03d' % function}\n")
                for line in range(1, args.lines + 1):
                    if line % 4 == 0:
                        count = rng.randrange(20)
                        checksum = f",{(source * 65537 + line):032x}" if args.checksums else ""
                        stream.write(f"DA:{line},{count}{checksum}\n")
                    if line % 10 == 0:
                        stream.write(f"BRDA:{line},0,0,{rng.randrange(4)}\n")
                        stream.write(f"BRDA:{line},0,1,-\n")
                stream.write("end_of_record\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
