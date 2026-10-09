#!/usr/bin/env python3
"""Compare simple DA count addition with the installed lcov implementation."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path


def read_line_counts(path: Path) -> dict[str, dict[int, int]]:
    result: dict[str, dict[int, int]] = {}
    source: str | None = None
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if raw_line.startswith("SF:"):
            source = raw_line[3:]
            result.setdefault(source, {})
        elif source is not None and raw_line.startswith("DA:"):
            fields = raw_line[3:].split(",")
            if len(fields) >= 2:
                line_number = int(fields[0])
                hit_count = int(fields[1])
                counts = result.setdefault(source, {})
                counts[line_number] = counts.get(line_number, 0) + hit_count
        elif raw_line == "end_of_record":
            source = None
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("binary", type=Path)
    args = parser.parse_args()
    if not shutil.which("lcov"):
        raise SystemExit("lcov executable was not found")

    with tempfile.TemporaryDirectory(prefix="lcovmerge-diff-") as temp_name:
        temp_dir = Path(temp_name)
        first = temp_dir / "first.info"
        second = temp_dir / "second.info"
        expected_path = "/lcovmerge-ci/src/example.c"
        first.write_text(
            f"SF:{expected_path}\nDA:1,2\nDA:2,4\nend_of_record\n",
            encoding="utf-8",
        )
        second.write_text(
            f"SF:{expected_path}\nDA:1,3\nDA:2,5\nend_of_record\n",
            encoding="utf-8",
        )
        lcov_output = temp_dir / "lcov.info"
        merge_output = temp_dir / "lcovmerge.info"

        subprocess.run(
            ["lcov", "--add-tracefile", str(first), "--add-tracefile", str(second), "--output-file", str(lcov_output)],
            check=True,
            text=True,
        )
        subprocess.run(
            [str(args.binary), str(first), str(second), "--output", str(merge_output)],
            check=True,
            text=True,
        )

        expected = {expected_path: {1: 5, 2: 9}}
        lcov_counts = read_line_counts(lcov_output)
        lcovmerge_counts = read_line_counts(merge_output)
        if lcov_counts != expected:
            raise SystemExit(f"lcov result differs from fixture expectation: {lcov_counts!r}")
        if lcovmerge_counts != expected:
            raise SystemExit(f"lcovmerge result differs from fixture expectation: {lcovmerge_counts!r}")
        if lcov_counts != lcovmerge_counts:
            raise SystemExit("lcov and lcovmerge line-count results differ")

    print("Differential check passed for the shared DA line-count fixture.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
