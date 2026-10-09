#!/usr/bin/env python3
"""Run bounded random LCOV parser inputs against the CLI for a fixed interval."""

from __future__ import annotations

import argparse
import os
import random
import subprocess
import tempfile
import time
from pathlib import Path


RECORDS = [
    b"TN:ci-fuzz",
    b"SF:src/fuzz.c",
    b"FN:1,fuzz_entry",
    b"FNDA:1,fuzz_entry",
    b"DA:1,1",
    b"BRDA:1,0,0,1",
    b"end_of_record",
    b"VER:revision",
    b"LF:1",
    b"LH:1",
    b"unknown:record",
    b"",
]


def make_input(rng: random.Random) -> bytes:
    if rng.randrange(4) == 0:
        return rng.randbytes(rng.randrange(0, 4097))

    lines: list[bytes] = []
    for _ in range(rng.randrange(1, 80)):
        if rng.randrange(5) == 0:
            lines.append(rng.randbytes(rng.randrange(0, 128)))
        else:
            record = rng.choice(RECORDS)
            if record.startswith((b"DA:", b"BRDA:", b"FNDA:")) and rng.randrange(2):
                record = record + b"," + str(rng.randrange(0, 2**32)).encode()
            lines.append(record)
    return b"\n".join(lines) + b"\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("binary", type=Path)
    parser.add_argument("--seconds", type=int, default=300)
    args = parser.parse_args()
    if args.seconds < 1:
        parser.error("--seconds must be positive")

    rng = random.Random(0x1C0A6)
    deadline = time.monotonic() + args.seconds
    cases = 0
    last_report = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="lcovmerge-fuzz-") as temp_name:
        temp_dir = Path(temp_name)
        while time.monotonic() < deadline:
            inputs: list[Path] = []
            for index in range(rng.randrange(1, 5)):
                input_path = temp_dir / f"case-{cases}-{index}.info"
                input_path.write_bytes(make_input(rng))
                inputs.append(input_path)

            output_path = temp_dir / f"out-{cases}.info"
            command = [os.fspath(args.binary), *(os.fspath(item) for item in inputs), "-o", os.fspath(output_path)]
            try:
                result = subprocess.run(command, capture_output=True, timeout=3, check=False)
            except subprocess.TimeoutExpired as exc:
                raise SystemExit(f"fuzz smoke timed out on case {cases}: {exc}") from exc
            if result.returncode not in (0, 2):
                raise SystemExit(
                    f"fuzz smoke failed on case {cases} with exit {result.returncode}\n"
                    f"stderr: {result.stderr.decode(errors='replace')}"
                )
            cases += 1
            now = time.monotonic()
            if now - last_report >= 30:
                print(f"Fuzz smoke: {cases} cases completed", flush=True)
                last_report = now

    print(f"Fuzz smoke passed: {cases} cases in {args.seconds} seconds")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
