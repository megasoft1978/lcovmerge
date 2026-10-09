#!/usr/bin/env python3
"""Deterministic small fixture generator; benchmark runs use the bench C generator."""

from __future__ import annotations

import argparse
import random
from pathlib import Path

MASK64 = (1 << 64) - 1
CHECK_A = 0xBF58476D1CE4E5B9
CHECK_B = 0x94D049BB133111EB


class ReferenceRng:
    """Small xorshift64* generator matching the benchmark fixture definition."""

    def __init__(self, seed: int) -> None:
        self.state = seed or 0x9E3779B97F4A7C15

    def next(self) -> int:
        value = self.state
        value ^= value >> 12
        value ^= (value << 25) & MASK64
        value ^= value >> 27
        self.state = value & MASK64
        return (self.state * 2685821657736338717) & MASK64

    def hit_count(self) -> int:
        value = self.next() % 10000
        if value < 7600:
            return 0
        if value < 9700:
            return 1 + self.next() % 9
        if value < 9980:
            return 10 + self.next() % 990
        return 1000 + self.next() % 99001


def executable_line(line: int) -> bool:
    offset = (line - 1) % 20
    return offset < 3 or 5 <= offset < 10


def checksum(file_id: int, line: int, seed: int) -> str:
    first = seed ^ ((file_id * 0x9E3779B97F4A7C15) & MASK64) ^ ((line * CHECK_A) & MASK64)
    first ^= first >> 30
    first = (first * CHECK_A) & MASK64
    first ^= first >> 27
    second = first ^ CHECK_B
    second ^= second >> 31
    second = (second * CHECK_B) & MASK64
    second ^= second >> 29
    return f"{first:016x}{second:016x}"


def generate_benchmark(args: argparse.Namespace) -> int:
    """Generate sorted LCOV shards used by the published large-input benchmark."""
    args.out.mkdir(parents=True, exist_ok=True)
    rng = ReferenceRng(args.seed)
    checksum_seed = args.seed or 0x9E3779B97F4A7C15
    for shard in range(args.shards):
        path = args.out / f"shard-{shard:04}.info"
        with path.open("w", encoding="ascii", newline="\n", buffering=1 << 20) as stream:
            permutation = list(range(args.files))
            selected_percent = 30 + rng.next() % 31
            selected_count = max(1, args.files * selected_percent // 100)
            for index in range(selected_count):
                swap = index + rng.next() % (args.files - index)
                permutation[index], permutation[swap] = permutation[swap], permutation[index]
            for file_id in sorted(permutation[:selected_count]):
                rows = [
                    "TN:benchmark\n",
                    f"SF:/bench/src/pkg{file_id // 100:04}/source_{file_id:04}.c\n",
                ]
                function_count = 20
                function_lines: list[int] = []
                function_hits: list[int] = []
                for function in range(function_count):
                    line = 1 if function == 0 else function * args.lines // function_count
                    while not executable_line(line):
                        line += 1
                    function_lines.append(line)
                    function_hits.append(rng.hit_count())
                for function, line in enumerate(function_lines):
                    rows.append(f"FN:{line},function_{function:04}\n")
                for function, hits in enumerate(function_hits):
                    rows.append(f"FNDA:{hits},function_{function:04}\n")
                function_hits_total = sum(hits != 0 for hits in function_hits)
                rows.append(f"FNF:{function_count}\nFNH:{function_hits_total}\n")

                line_hits = [False] * (args.lines + 1)
                function_index = 0
                line_found = line_hit = 0
                for line in range(1, args.lines + 1):
                    if not executable_line(line):
                        continue
                    while function_index + 1 < function_count and line >= function_lines[function_index + 1]:
                        function_index += 1
                    hits = (function_hits[function_index] if line == function_lines[function_index]
                            else rng.hit_count()) if function_hits[function_index] else 0
                    line_hits[line] = hits != 0
                    line_found += 1
                    line_hit += hits != 0
                    row = f"DA:{line},{hits}"
                    if args.checksums:
                        row += f",{checksum(file_id, line, checksum_seed)}"
                    rows.append(row + "\n")
                rows.append(f"LF:{line_found}\nLH:{line_hit}\n")

                branch_found = branch_hit = 0
                for line in range(1, args.lines + 1):
                    if (line - 1) % 20 >= 3:
                        continue
                    for branch in range(2):
                        is_dash = not line_hits[line] or (branch != 0 and rng.next() % 100 < 55)
                        hits = 0 if is_dash else rng.hit_count()
                        if not is_dash and branch == 0 and hits == 0:
                            hits = 1
                        value = "-" if is_dash else str(hits)
                        rows.append(f"BRDA:{line},0,{branch},{value}\n")
                        branch_found += 1
                        branch_hit += hits != 0
                rows.append(f"BRF:{branch_found}\nBRH:{branch_hit}\nend_of_record\n")
                stream.write("".join(rows))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--shards", type=int, default=2)
    parser.add_argument("--files", type=int, default=20)
    parser.add_argument("--lines", type=int, default=80)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--checksums", action="store_true")
    parser.add_argument("--benchmark-compatible", action="store_true",
                        help="generate the deterministic benchmark LCOV format")
    args = parser.parse_args()
    if min(args.shards, args.files, args.lines) <= 0:
        parser.error("shards, files, and lines must be positive")
    if args.benchmark_compatible:
        return generate_benchmark(args)
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
