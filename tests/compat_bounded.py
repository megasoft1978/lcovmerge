#!/usr/bin/env python3
"""Large synthetic bounded-runtime gates for lcovmerge.

The records are generated in .cache and checked as a stream so the test does
not need a second multi-million-record expected tracefile in memory.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

RECORD_BUDGET = 8_000_000
MULTIPLIER = 3
TIMEOUT = 240


def parse_output(path: Path, expected: dict) -> None:
    """Assert one section's metadata and exact record totals without loading it."""
    counts = {key: 0 for key in ("FNL", "FNA", "FN", "FNDA", "DA")}
    lf = lh = None
    seen_sf = seen_end = False
    with path.open("rt", encoding="utf-8", errors="strict") as stream:
        first = stream.readline()
        if first != f"SF:{expected['source']}\n":
            raise AssertionError(f"unexpected first output record: {first[:160]!r}")
        seen_sf = True
        for line in stream:
            if line == "end_of_record\n":
                seen_end = True
                if stream.readline():
                    raise AssertionError("unexpected second section or trailing output")
                break
            key, sep, value = line.partition(":")
            if not sep:
                raise AssertionError(f"malformed output line: {line[:160]!r}")
            if key in counts:
                counts[key] += 1
            elif key == "LF":
                lf = int(value)
            elif key == "LH":
                lh = int(value)
            elif key in ("TN", "VER"):
                continue
            elif key == "DA":
                counts["DA"] += 1
                line_no, comma, hits = value.rstrip("\n").partition(",")
                if not comma or int(hits) != expected["hits"]:
                    raise AssertionError(f"unexpected DA row: {line[:100]!r}")
                if int(line_no) > expected["lines"]:
                    raise AssertionError(f"out-of-range DA line: {line_no}")
            else:
                raise AssertionError(f"unexpected output family {key!r}")
    if not seen_sf or not seen_end:
        raise AssertionError("output is missing SF or end_of_record")
    for key in ("FNL", "FNA", "FN", "FNDA", "DA"):
        if counts[key] != expected[key]:
            raise AssertionError(f"{key} count {counts[key]} != {expected[key]}")
    if lf != expected["LF"] or lh != expected["LH"]:
        raise AssertionError(f"LF/LH {lf}/{lh} != {expected['LF']}/{expected['LH']}")


def write_single_source(path: Path, source: Path, lines: int, functions: int,
                        modern: bool, legacy: bool, aliases: int = 3) -> dict:
    hits = 2
    with path.open("wt", encoding="utf-8", newline="\n") as out:
        out.write(f"TN:bounded\nSF:{source}\n")
        if legacy:
            for i in range(functions):
                out.write(f"FN:{i + 1},legacy_{i}\n")
            for i in range(functions):
                out.write(f"FNDA:1,legacy_{i}\n")
        if modern:
            # One definition list, then three aliases per distinct sparse ID.
            for i in range(functions):
                out.write(f"FNL:{i * 7 + 2},{i + 1},{i + 1}\n")
            for i in range(functions):
                fid = i * 7 + 2
                for alias in range(aliases):
                    out.write(f"FNA:{fid},1,modern_{i}_{alias}\n")
        for i in range(lines):
            out.write(f"DA:{i + 1},{hits}\n")
        out.write("end_of_record\n")
    row_count = lines + (functions * (1 + aliases) if modern else 0) + (functions * 2 if legacy else 0)
    return {"source": str(source), "lines": lines, "hits": hits,
            "FNL": functions if modern else 0, "FNA": functions * aliases if modern else 0,
            "FN": functions if legacy else 0, "FNDA": functions if legacy else 0,
            "DA": lines, "LF": lines, "LH": lines,
            "input_records": row_count, "record_budget_multiplier": row_count / RECORD_BUDGET}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--binary", required=True, type=Path)
    ap.add_argument("--workdir", required=True, type=Path)
    ap.add_argument("--timeout", type=int, default=TIMEOUT)
    ap.add_argument("--records", type=int, default=RECORD_BUDGET * MULTIPLIER + 17)
    args = ap.parse_args()
    if args.records < RECORD_BUDGET * MULTIPLIER:
        ap.error(f"--records must be at least {RECORD_BUDGET * MULTIPLIER}")
    root = args.workdir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    binary = args.binary.resolve()
    config = root / "lcovrc"
    config.write_text("# bounded compatibility fixture\n")
    tempdir = root / "tmp"
    tempdir.mkdir(exist_ok=True)
    results = []
    failures = []
    # Single-source current format with 3 aliases per sparse ID.
    source = root / "modern.c"
    source.write_text("\n" * args.records)
    functions = 4096
    lines = args.records - 4 * functions
    spec = write_single_source(root / "modern.info", source, lines, functions, True, False)
    results.append(run_case(binary, root, config, tempdir, "modern", spec, failures, args.timeout))
    # Legacy distinct function names without modern IDs.
    source2 = root / "legacy.c"
    source2.write_text("\n" * args.records)
    functions2 = 8192
    lines2 = args.records - 2 * functions2
    spec2 = write_single_source(root / "legacy.info", source2, lines2, functions2, False, True)
    results.append(run_case(binary, root, config, tempdir, "legacy", spec2, failures, args.timeout))
    # Sparse modern IDs are a separate alias-free shape to exercise ID indexing.
    source3 = root / "sparse.c"
    source3.write_text("\n" * args.records)
    functions3 = 32768
    lines3 = args.records - 2 * functions3
    spec3 = write_single_source(root / "sparse.info", source3, lines3, functions3,
                                True, False, aliases=1)
    results.append(run_case(binary, root, config, tempdir, "sparse", spec3, failures, args.timeout))
    report = {"binary": str(binary), "record_budget": RECORD_BUDGET,
              "minimum_multiplier": MULTIPLIER, "cases": results, "failures": failures}
    (root / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"workdir": str(root), "cases": len(results), "failures": len(failures),
                      "report": str(root / "report.json")}, indent=2))
    return 1 if failures else 0


def run_case(binary: Path, root: Path, config: Path, tempdir: Path, name: str, expected: dict,
             failures: list, timeout: int) -> dict:
    output = root / f"{name}.out.info"
    stdout = root / f"{name}.stdout.log"
    stderr = root / f"{name}.stderr.log"
    output.unlink(missing_ok=True)
    command = [str(binary), "--lcov-compat=2.6", "--quiet", "--config-file", str(config),
               "--mem-limit", "8M", "--tempdir", str(tempdir),
               "-a", str(root / f"{name}.info"), "-o", str(output)]
    started = time.monotonic()
    try:
        with stdout.open("wb") as out, stderr.open("wb") as err:
            proc = subprocess.run(command, cwd=root, stdout=out, stderr=err, timeout=timeout)
        row = {"name": name, "argv": command, "status": proc.returncode, "timed_out": False}
    except subprocess.TimeoutExpired:
        row = {"name": name, "argv": command, "status": None, "timed_out": True}
    row.update({"seconds": round(time.monotonic() - started, 3), "output": str(output),
                "stdout_log": str(stdout), "stderr_log": str(stderr), **expected})
    try:
        if row["status"] != 0 or row["timed_out"]:
            raise AssertionError(f"command status={row['status']} timed_out={row['timed_out']}")
        parse_output(output, expected)
        row["output_verified"] = True
    except (AssertionError, OSError, ValueError) as exc:
        row["output_verified"] = False
        row["failure"] = str(exc)
        failures.append({"case": name, "failure": str(exc), "command": command,
                         "stdout_log": str(stdout), "stderr_log": str(stderr),
                         "output": str(output)})
        print(f"FAIL {name}: {exc}", file=sys.stderr)
    return row


if __name__ == "__main__":
    raise SystemExit(main())
