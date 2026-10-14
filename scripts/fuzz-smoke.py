#!/usr/bin/env python3
"""Bounded deterministic CLI mutation smoke for native and LCOV 2.6 modes."""

from __future__ import annotations

import argparse
import os
import random
import subprocess
import tempfile
import time
from pathlib import Path

RECORDS = [b"TN:fuzz", b"SF:src/fuzz.c", b"FN:1,fuzz_entry", b"FNL:1,1,2",
           b"FNA:1,1,fuzz_entry", b"FNDA:1,fuzz_entry", b"DA:1,1",
           b"BRDA:1,0,0,1", b"MCDC:1,2,t,1,0,A", b"end_of_record",
           b"VER:revision", b"LF:1", b"LH:1", b"unknown:record", b""]


def make_input(rng: random.Random) -> bytes:
    if rng.randrange(4) == 0:
        return rng.randbytes(rng.randrange(0, 2049))
    lines = []
    for _ in range(rng.randrange(1, 35)):
        if rng.randrange(6) == 0:
            lines.append(rng.randbytes(rng.randrange(0, 64)))
            continue
        record = rng.choice(RECORDS)
        if record.startswith((b"DA:", b"BRDA:", b"FNDA:", b"FNA:", b"MCDC:")) and rng.randrange(2):
            record += b"," + str(rng.randrange(0, 1 << 20)).encode()
        lines.append(record)
    return b"\n".join(lines) + b"\n"


def mutate_seed(rng: random.Random, seeds: list[bytes]) -> bytes:
    data = bytearray(rng.choice(seeds))
    for _ in range(rng.randrange(1, 7)):
        op = rng.randrange(4)
        if op == 0 and data:
            pos = rng.randrange(len(data))
            del data[pos:pos + 1]
        elif op == 1:
            pos = rng.randrange(len(data) + 1)
            data[pos:pos] = rng.randbytes(rng.randrange(1, 8))
        elif op == 2 and data:
            data[rng.randrange(len(data))] = rng.randrange(256)
        else:
            data.extend(rng.choice(RECORDS) + b"\n")
    return bytes(data[:1 << 16])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("binary", type=Path)
    ap.add_argument("--seconds", type=int, default=300)
    ap.add_argument("--profile", choices=("native", "compat"), default="native")
    ap.add_argument("--corpus", type=Path, default=Path(__file__).resolve().parents[1] / "tests" / "fuzz-corpus")
    args = ap.parse_args()
    if args.seconds < 1:
        ap.error("--seconds must be positive")
    seeds = [p.read_bytes() for p in sorted(args.corpus.glob("*.info"))]
    if not seeds:
        ap.error(f"no .info seed files found in {args.corpus}")
    rng = random.Random(0x1C0A6)
    deadline, cases = time.monotonic() + args.seconds, 0
    with tempfile.TemporaryDirectory(prefix="lcovmerge-fuzz-") as td:
        root = Path(td)
        def retain(data: bytes, command: list[str], detail: str) -> None:
            evidence = Path(__file__).resolve().parents[1] / ".cache" / "fuzz-smoke" / args.profile
            evidence.mkdir(parents=True, exist_ok=True)
            seed_path = evidence / "failure-seed.bin"
            seed_path.write_bytes(data)
            (evidence / "failure-command.txt").write_text(" ".join(command) + "\n" + detail + "\n", encoding="utf-8")
            raise SystemExit(f"fuzz {args.profile} case={cases} failed; seed={seed_path}; command={command!r}; {detail}")
        while time.monotonic() < deadline:
            data = mutate_seed(rng, seeds) if rng.randrange(2) else make_input(rng)
            source = root / f"case-{cases}.info"
            source.write_bytes(data)
            output = root / f"out-{cases}.info"
            command = [os.fspath(args.binary)]
            if args.profile == "compat":
                command += ["--lcov-compat=2.6", "--quiet", "--config-file", os.fspath(root / "lcovrc"), "-a"]
                (root / "lcovrc").write_bytes(b"")
            command += [os.fspath(source), "-o", os.fspath(output)]
            try:
                result = subprocess.run(command, capture_output=True, timeout=2, check=False)
            except subprocess.TimeoutExpired as exc:
                retain(data, command, f"timeout: {exc}")
            allowed = (0, 2, 3) if args.profile == "compat" else (0, 2)
            if result.returncode not in allowed:
                retain(data, command, f"exit={result.returncode}; stderr={result.stderr.decode(errors='replace')}")
            cases += 1
    print(f"Fuzz smoke {args.profile} passed: {cases} cases in {args.seconds} seconds")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
