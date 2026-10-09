#!/usr/bin/env python3
"""Golden, differential, option, and determinism coverage for lcovmerge."""

from __future__ import annotations

import argparse
import os
import random
import re
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ORACLE = ROOT / "tools" / "oracle.py"
MAX_U64 = (1 << 64) - 1


def expected_trace(path: str, rows: str = "", *, tn: tuple[str, ...] = (),
                   summary: tuple[int, int, int, int, int, int, int, int] = (0, 0, 0, 0, 0, 0, 0, 0)) -> str:
    if path == "":
        return ""
    functions, function_hits, branches, branch_hits, mcdc, mcdc_hits, lines, line_hits = summary
    testcase_rows = "".join(f"TN:{name}\n" for name in sorted(tn))
    return (
        testcase_rows + f"SF:{path}\n" + rows +
        f"FNF:{functions}\nFNH:{function_hits}\n"
        f"BRF:{branches}\nBRH:{branch_hits}\n"
        f"MCF:{mcdc}\nMCH:{mcdc_hits}\n"
        f"LF:{lines}\nLH:{line_hits}\nend_of_record\n"
    )


def mk_case(name: str, inputs: list[str | bytes], path: str | None = None, rows: str = "", *,
            tn: tuple[str, ...] = (), summary=(0, 0, 0, 0, 0, 0, 0, 0), options=(),
            expected: str | None = None, warning: str | None = None) -> dict:
    if path is None:
        path = "/case.c"
        for content in inputs:
            decoded = content.decode("utf-8", errors="replace") if isinstance(content, bytes) else content
            match = re.search(r"(?m)^SF:(.*?)(?:\r?\n|$)", decoded)
            if match:
                path = match.group(1)
                break
    return {
        "name": name,
        "inputs": inputs,
        "options": list(options),
        "expected": expected if expected is not None else expected_trace(path, rows, tn=tn, summary=summary),
        "warning": warning,
    }


def golden_cases() -> list[dict]:
    cases = [
        mk_case("empty-file", [""] , expected=""),
        mk_case("test-name-without-source", ["TN:orphan\n"], expected=""),
        mk_case("test-name-empty-source", ["TN:empty\nSF:/empty.c\nend_of_record\n"], path="/empty.c", tn=("empty",)),
        mk_case("empty-section-without-end", ["SF:/empty.c\n"], path="/empty.c"),
        mk_case("missing-end-of-record", ["SF:/eof.c\nDA:7,2"], rows="DA:7,2\n", summary=(0, 0, 0, 0, 0, 0, 1, 1)),
        mk_case("crlf", ["SF:/crlf.c\r\nDA:1,1\r\nend_of_record\r\n"], rows="DA:1,1\n", summary=(0, 0, 0, 0, 0, 0, 1, 1)),
        mk_case("duplicate-source-sections", ["SF:/dup.c\nDA:4,2\nend_of_record\nSF:/dup.c\nDA:4,3\nend_of_record\n"], rows="DA:4,5\n", summary=(0, 0, 0, 0, 0, 0, 1, 1)),
        mk_case("da-without-checksum", ["SF:/da.c\nDA:3,9\nend_of_record\n"], rows="DA:3,9\n", summary=(0, 0, 0, 0, 0, 0, 1, 1)),
        mk_case("da-with-checksum", ["SF:/da.c\nDA:3,9,abc123\nend_of_record\n"], rows="DA:3,9,abc123\n", summary=(0, 0, 0, 0, 0, 0, 1, 1)),
        mk_case("checksum-stable-choice", ["SF:/da.c\nDA:3,2,zz\nend_of_record\n", "SF:/da.c\nDA:3,4,aa\nend_of_record\n"], rows="DA:3,6,aa\n", summary=(0, 0, 0, 0, 0, 0, 1, 1), warning="checksum mismatch"),
        mk_case("old-function", ["SF:/fn.c\nFN:8,run\nFNDA:1,run\nend_of_record\n"], rows="FN:8,run\nFNDA:1,run\n", summary=(1, 1, 0, 0, 0, 0, 0, 0)),
        mk_case("old-function-end-line", ["SF:/fn.c\nFN:8,14,run\nFNDA:0,run\nend_of_record\n"], rows="FN:8,14,run\nFNDA:0,run\n", summary=(1, 0, 0, 0, 0, 0, 0, 0)),
        mk_case("orphan-fnda", ["SF:/fn.c\nFNDA:5,missing\nend_of_record\n"], rows="FNDA:5,missing\n", summary=(0, 1, 0, 0, 0, 0, 0, 0)),
        mk_case("fnda-saturating-merge", ["SF:/fn.c\nFNDA:9,run\nend_of_record\n", "SF:/fn.c\nFNDA:12,run\nend_of_record\n"], rows="FNDA:21,run\n", summary=(0, 1, 0, 0, 0, 0, 0, 0)),
        mk_case("fnl-fna-aliases", ["SF:/fnl.c\nFNL:0,3,9\nFNA:0,2,first\nFNA:0,0,alias\nend_of_record\n"], rows="FNL:0,3,9\nFNA:0,0,alias\nFNA:0,2,first\n", summary=(1, 1, 0, 0, 0, 0, 0, 0)),
        mk_case("fnl-fna-count-merge", ["SF:/fnl.c\nFNL:0,3\nFNA:0,2,first\nend_of_record\n", "SF:/fnl.c\nFNL:0,3\nFNA:0,4,first\nend_of_record\n"], rows="FNL:0,3\nFNA:0,6,first\n", summary=(1, 1, 0, 0, 0, 0, 0, 0)),
        mk_case("da-saturating-merge", [f"SF:/sat.c\nDA:1,{MAX_U64}\nend_of_record\n", "SF:/sat.c\nDA:1,1\nend_of_record\n"], rows=f"DA:1,{MAX_U64}\n", summary=(0, 0, 0, 0, 0, 0, 1, 1)),
        mk_case("branch-numeric", ["SF:/br.c\nBRDA:5,0,2,4\nend_of_record\n"], rows="BRDA:5,0,2,4\n", summary=(0, 0, 1, 1, 0, 0, 0, 0)),
        mk_case("branch-dash", ["SF:/br.c\nBRDA:5,0,2,-\nend_of_record\n"], rows="BRDA:5,0,2,-\n", summary=(0, 0, 1, 0, 0, 0, 0, 0)),
        mk_case("branch-dash-and-count", ["SF:/br.c\nBRDA:5,0,2,-\nend_of_record\n", "SF:/br.c\nBRDA:5,0,2,3\nend_of_record\n"], rows="BRDA:5,0,2,3\n", summary=(0, 0, 1, 1, 0, 0, 0, 0)),
        mk_case("branch-expression-with-comma", ["SF:/br.c\nBRDA:5,0,check(a,b),2\nend_of_record\n"], rows="BRDA:5,0,check(a,b),2\n", summary=(0, 0, 1, 1, 0, 0, 0, 0)),
        mk_case("branch-exception-and-fallthrough", ["SF:/br.c\nBRDA:5,e0,0,1\nBRDA:5,f0,1,2\nend_of_record\n"], rows="BRDA:5,e0,0,1\nBRDA:5,f0,1,2\n", summary=(0, 0, 2, 2, 0, 0, 0, 0)),
        mk_case("unreachable-branch", ["SF:/br.c\nBRDA:5,U0,0,3\nend_of_record\n"], rows="BRDA:5,U0,0,3\n", summary=(0, 0, 0, 0, 0, 0, 0, 0)),
        mk_case("mcdc-count", ["SF:/mcdc.c\nMCDC:7,2,t,3,0,condition\nend_of_record\n"], rows="MCDC:7,2,t,3,0,condition\n", summary=(0, 0, 0, 0, 1, 1, 0, 0)),
        mk_case("mcdc-unreachable", ["SF:/mcdc.c\nMCDC:7,U2,f,3,0,condition\nend_of_record\n"], rows="MCDC:7,U2,f,3,0,condition\n", summary=(0, 0, 0, 0, 0, 0, 0, 0)),
        mk_case("mcdc-comma-expression-and-sum", ["SF:/mcdc.c\nMCDC:7,2,t,1,0,fn(a,b)\nend_of_record\n", "SF:/mcdc.c\nMCDC:7,2,t,2,0,fn(a,b)\nend_of_record\n"], rows="MCDC:7,2,t,3,0,fn(a,b)\n", summary=(0, 0, 0, 0, 1, 1, 0, 0)),
        mk_case("unknown-extension-preserved", ["SF:/x.c\nX:opaque\nend_of_record\n"], rows="X:opaque\n"),
        mk_case("unknown-extension-duplicates-preserved", ["SF:/x.c\nX:opaque\nX:opaque\nend_of_record\n"], rows="X:opaque\nX:opaque\n"),
        mk_case("unknown-extension-warning", ["SF:/x.c\nX:opaque\nend_of_record\n"], rows="X:opaque\n", options=("--warn-unknown",), warning="preserved unknown record X"),
        mk_case("mcdc-expression-mismatch", ["SF:/mcdc.c\nMCDC:7,2,t,1,0,zeta\nend_of_record\n", "SF:/mcdc.c\nMCDC:7,2,t,2,0,alpha\nend_of_record\n"], rows="MCDC:7,2,t,3,0,alpha\n", summary=(0, 0, 0, 0, 1, 1, 0, 0), warning="MC/DC expression mismatch"),
        mk_case("version-record-preserved", ["SF:/x.c\nVER:rev-1\nend_of_record\n"], rows="VER:rev-1\n"),
        mk_case("include-glob", ["SF:/src/keep.c\nDA:1,1\nend_of_record\nSF:/src/skip.c\nDA:1,1\nend_of_record\n"], path="/src/keep.c", rows="DA:1,1\n", summary=(0, 0, 0, 0, 0, 0, 1, 1), options=("--include", "*/keep.c")),
        mk_case("exclude-glob", ["SF:/src/skip.c\nDA:1,1\nend_of_record\nSF:/src/keep.c\nDA:1,1\nend_of_record\n"], path="/src/keep.c", rows="DA:1,1\n", summary=(0, 0, 0, 0, 0, 0, 1, 1), options=("--exclude", "*/skip.c")),
        mk_case("prefix-strip", ["SF:/work/project/src/a.c\nDA:2,1\nend_of_record\n"], path="src/a.c", rows="DA:2,1\n", summary=(0, 0, 0, 0, 0, 0, 1, 1), options=("--prefix-strip", "/work/project/")),
        mk_case("rebase", ["SF:/old/tree/src/a.c\nDA:2,1\nend_of_record\n"], path="/new/tree/src/a.c", rows="DA:2,1\n", summary=(0, 0, 0, 0, 0, 0, 1, 1), options=("--rebase", "/old/tree=/new/tree")),
        mk_case("branch-coverage-off", ["SF:/off.c\nBRDA:2,0,0,1\nDA:2,1\nend_of_record\n"], rows="DA:2,1\n", summary=(0, 0, 0, 0, 0, 0, 1, 1), options=("--branch-coverage", "off")),
        mk_case("function-data-off", ["SF:/off.c\nFN:2,run\nFNDA:1,run\nDA:2,1\nend_of_record\n"], rows="DA:2,1\n", summary=(0, 0, 0, 0, 0, 0, 1, 1), options=("--no-function-data",)),
        mk_case("size-suffix-and-small-budget", ["SF:/budget.c\nDA:1,1\nend_of_record\n"], rows="DA:1,1\n", summary=(0, 0, 0, 0, 0, 0, 1, 1), options=("--mem-limit", "8M")),
        mk_case("jobs-one", ["SF:/jobs.c\nDA:1,1\nend_of_record\n"], rows="DA:1,1\n", summary=(0, 0, 0, 0, 0, 0, 1, 1), options=("-j", "1")),
        mk_case("jobs-two", ["SF:/jobs.c\nDA:1,1\nend_of_record\n", "SF:/jobs.c\nDA:1,2\nend_of_record\n"], rows="DA:1,3\n", summary=(0, 0, 0, 0, 0, 0, 1, 1), options=("--jobs", "2")),
        mk_case("utf8-source-path", ["SF:/src/café.c\nDA:1,1\nend_of_record\n"], path="/src/café.c", rows="DA:1,1\n", summary=(0, 0, 0, 0, 0, 0, 1, 1)),
        mk_case("source-paths-sort", ["SF:/z.c\nDA:1,1\nend_of_record\nSF:/a.c\nDA:1,1\nend_of_record\n"], expected=(expected_trace("/a.c", "DA:1,1\n", summary=(0,0,0,0,0,0,1,1)) + expected_trace("/z.c", "DA:1,1\n", summary=(0,0,0,0,0,0,1,1)))),
        mk_case("repeated-paths-across-runs", [
            "SF:/a.c\nDA:1,1\nend_of_record\nSF:/c.c\nDA:1,3\nend_of_record\n",
            "SF:/a.c\nDA:1,2\nend_of_record\nSF:/d.c\nDA:1,4\nend_of_record\n",
        ], expected=(expected_trace("/a.c", "DA:1,3\n", summary=(0,0,0,0,0,0,1,1)) +
                     expected_trace("/c.c", "DA:1,3\n", summary=(0,0,0,0,0,0,1,1)) +
                     expected_trace("/d.c", "DA:1,4\n", summary=(0,0,0,0,0,0,1,1)))),
        mk_case("test-name-deduplicates", ["TN:alpha\nSF:/tn.c\nDA:1,1\nend_of_record\n", "TN:alpha\nSF:/tn.c\nDA:1,1\nend_of_record\n"], rows="DA:1,2\n", tn=("alpha",), summary=(0, 0, 0, 0, 0, 0, 1, 1)),
        mk_case("input-summaries-recomputed", ["SF:/sum.c\nFNF:91\nFNH:88\nLF:72\nLH:69\nBRF:100\nBRH:99\nDA:1,0\nend_of_record\n"], rows="DA:1,0\n", summary=(0, 0, 0, 0, 0, 0, 1, 0)),
        mk_case("multiple-branches-sort", ["SF:/order.c\nBRDA:9,0,10,1\nBRDA:2,0,2,1\nBRDA:2,0,1,1\nend_of_record\n"], rows="BRDA:2,0,1,1\nBRDA:2,0,2,1\nBRDA:9,0,10,1\n", summary=(0, 0, 3, 3, 0, 0, 0, 0)),
        mk_case("multiple-da-lines-sort", ["SF:/order.c\nDA:9,1\nDA:2,0\nDA:1,1\nend_of_record\n"], rows="DA:1,1\nDA:2,0\nDA:9,1\n", summary=(0, 0, 0, 0, 0, 0, 3, 2)),
        mk_case("checksum-mismatch-nonstrict", ["SF:/check.c\nDA:1,1,one\nend_of_record\n", "SF:/check.c\nDA:1,1,two\nend_of_record\n"], rows="DA:1,2,one\n", summary=(0,0,0,0,0,0,1,1), warning="checksum mismatch"),
        mk_case("eof-trailing-cr", ["SF:/cr.c\r\nDA:1,2\r"], rows="DA:1,2\n", summary=(0,0,0,0,0,0,1,1)),
        mk_case("comments-and-blanks", ["# comment\n\nSF:/comment.c\n# another\n\nDA:1,1\nend_of_record\n"], rows="DA:1,1\n", summary=(0,0,0,0,0,0,1,1)),
        mk_case("drive-and-backslash-source", ["SF:C:\\repo\\src\\a.c\nDA:1,1\nend_of_record\n"], path="C:\\repo\\src\\a.c", rows="DA:1,1\n", summary=(0,0,0,0,0,0,1,1)),
        mk_case("drive-path-rebase", ["SF:C:\\repo\\src\\a.c\nDA:1,1\nend_of_record\n"], path="D:\\new\\src\\a.c", rows="DA:1,1\n", summary=(0,0,0,0,0,0,1,1), options=("--rebase", "C:\\repo=D:\\new")),
        mk_case("unix-path-rebase-to-drive", ["SF:/old/tree/src/a.c\nDA:1,1\nend_of_record\n"], path="C:\\new\\src\\a.c", rows="DA:1,1\n", summary=(0,0,0,0,0,0,1,1), options=("--rebase", "/old/tree=C:\\new")),
        mk_case("branch-count-saturation", [f"SF:/brsat.c\nBRDA:1,0,0,{MAX_U64}\nend_of_record\n", "SF:/brsat.c\nBRDA:1,0,0,2\nend_of_record\n"], rows=f"BRDA:1,0,0,{MAX_U64}\n", summary=(0,0,1,1,0,0,0,0)),
        mk_case("fnl-summary-groups", ["SF:/groups.c\nFNL:0,1\nFNA:0,0,a\nFNA:0,1,b\nFNL:1,1\nFNA:1,0,c\nend_of_record\n"], rows="FNL:0,1\nFNA:0,0,a\nFNA:0,1,b\nFNL:1,1\nFNA:1,0,c\n", summary=(2,1,0,0,0,0,0,0)),
        mk_case("include-exclude-precedence", ["SF:/src/keep.c\nDA:1,1\nend_of_record\n"], expected="", options=("--include", "*", "--exclude", "*/keep.c")),
    ]
    return cases


def run(command: list[str], *, input_data: bytes | None = None, env=None) -> subprocess.CompletedProcess:
    return subprocess.run(command, input=input_data, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          env=env, check=False)


def run_goldens(binary: Path, temporary: Path) -> int:
    cases = golden_cases()
    for index, case in enumerate(cases):
        case_dir = temporary / f"gold-{index:02d}"
        case_dir.mkdir()
        paths = []
        for file_index, content in enumerate(case["inputs"]):
            input_path = case_dir / f"input-{file_index}.info"
            payload = content if isinstance(content, bytes) else content.encode("utf-8")
            input_path.write_bytes(payload)
            paths.append(str(input_path))
        output = case_dir / "out.info"
        command = [str(binary), *case["options"], *paths, "-o", str(output)]
        result = run(command)
        if result.returncode != 0:
            raise AssertionError(f"golden {case['name']} exited {result.returncode}: {result.stderr.decode(errors='replace')}")
        actual = output.read_text(encoding="utf-8") if output.exists() else ""
        if actual != case["expected"]:
            raise AssertionError(f"golden {case['name']} output mismatch\nexpected:\n{case['expected']}\nactual:\n{actual}")
        if case["warning"] and case["warning"].encode() not in result.stderr:
            raise AssertionError(f"golden {case['name']} did not report {case['warning']!r}")
    return len(cases)


def malformed_tests(binary: Path, temporary: Path) -> int:
    examples = [
        ("bad-number", b"SF:/bad.c\nDA:nope,1\n", 2, b"DA record"),
        ("no-colon", b"not-lcov\n", 2, b"outside an SF"),
        ("bare-garbage", b"garbage", 2, b"outside an SF"),
        ("binary-nul", b"SF:/bad.c\nX:\x00\n", 2, b"binary or invalid UTF-8"),
        ("invalid-utf8", b"SF:/bad.c\nX:\xff\n", 2, b"binary or invalid UTF-8"),
        ("oversize-line", b"SF:/bad.c\nX:" + b"a" * ((1 << 20) + 1) + b"\n", 2, b"line exceeds 1 MiB"),
        ("end-without-section", b"end_of_record\n", 2, b"end_of_record without SF"),
    ]
    for name, content, expected_code, text in examples:
        base = temporary / f"bad-{name}"
        base.mkdir()
        source = base / "bad.info"
        source.write_bytes(content)
        result = run([str(binary), str(source), "-o", str(base / "out.info")])
        if result.returncode != expected_code or text not in result.stderr:
            raise AssertionError(f"malformed {name}: status={result.returncode}, stderr={result.stderr!r}")
    strict_dir = temporary / "strict-checksum"
    strict_dir.mkdir()
    inputs = []
    for index, checksum in enumerate(("one", "two")):
        path = strict_dir / f"{index}.info"
        path.write_text(f"SF:/strict.c\nDA:1,1,{checksum}\nend_of_record\n", encoding="utf-8")
        inputs.append(str(path))
    strict_output = strict_dir / "out.info"
    strict_output.write_text("keep existing output\n", encoding="utf-8")
    result = run([str(binary), *inputs, "--strict-checksum", "-o", str(strict_output)])
    if result.returncode != 2 or b"checksum mismatch" not in result.stderr or strict_output.read_text(encoding="utf-8") != "keep existing output\n":
        raise AssertionError("strict checksum did not fail atomically with input error status 2")

    unreadable = run([str(binary), str(temporary / "missing.info"), "-o", str(temporary / "missing-out.info")])
    if unreadable.returncode != 3 or b"cannot open input" not in unreadable.stderr:
        raise AssertionError("unreadable input did not return I/O status 3")
    usage = run([str(binary), "--mem-limit", "2M", "x.info", "-o", "x.out"])
    if usage.returncode != 1:
        raise AssertionError("invalid options did not return status 1")
    no_args = run([str(binary)])
    if no_args.returncode != 1:
        raise AssertionError("missing arguments did not return status 1")
    empty_path_dir = temporary / "empty-rewritten-path"
    empty_path_dir.mkdir()
    empty_source = empty_path_dir / "source.info"
    empty_source.write_text("SF:/root\nDA:1,1\nend_of_record\n", encoding="utf-8")
    empty_rewrite = run([str(binary), str(empty_source), "--prefix-strip", "/root", "-o", str(empty_path_dir / "out.info")])
    if empty_rewrite.returncode != 2 or b"becomes empty" not in empty_rewrite.stderr:
        raise AssertionError("path rewriting accepted an empty SF path")
    version = run([str(binary), "--version"])
    help_result = run([str(binary), "--help"])
    if version.returncode != 0 or not re.search(rb"^lcovmerge 1\.0\.0 \(git [^)]+\)\r?\n?$", version.stdout):
        raise AssertionError("version string did not include v1.0.0 and build commit")
    if help_result.returncode != 0 or b"--warn-unknown" not in help_result.stdout:
        raise AssertionError("help output is missing an option")
    return len(examples) + 7


def random_trace(rng: random.Random, case_index: int, input_index: int) -> str:
    path = f"/generated/{case_index % 9:02}/source-{case_index % 13:02}.c"
    records = []
    if rng.random() < 0.5:
        records.append(f"TN:test-{input_index}\n")
    records.append(f"SF:{path}\n")
    if rng.random() < 0.45:
        index = rng.randrange(3)
        start = rng.randrange(1, 80)
        records.extend((f"FNL:{index},{start},{start + 4}\n",
                        f"FNA:{index},{rng.randrange(8)},fn_{index}\n"))
    if rng.random() < 0.55:
        start = rng.randrange(1, 80)
        records.extend((f"FN:{start},legacy\n", f"FNDA:{rng.randrange(9)},legacy\n"))
    for _ in range(rng.randrange(1, 5)):
        line_no = rng.randrange(1, 100)
        records.append(f"DA:{line_no},{rng.randrange(12)},{rng.randrange(1 << 16):04x}\n")
    if rng.random() < 0.7:
        line_no = rng.randrange(1, 100)
        records.append(f"BRDA:{line_no},{rng.randrange(2)},{rng.randrange(3)},{rng.randrange(4)}\n")
    if rng.random() < 0.5:
        line_no = rng.randrange(1, 100)
        group = rng.randrange(1, 4)
        index = rng.randrange(group)
        records.append(f"MCDC:{line_no},{group},t,{rng.randrange(4)},{index},term({input_index},{index})\n")
    if rng.random() < 0.4:
        records.append(f"X-CUSTOM:field-{rng.randrange(4)}\n")
    records.append("FNF:0\nLF:0\nBRF:0\nBRH:0\n")
    if rng.random() < 0.9:
        records.append("end_of_record\n")
    return "".join(records)


def differential_tests(binary: Path, temporary: Path, count: int = 220) -> int:
    rng = random.Random(0x1C0A2026)
    for case_index in range(count):
        case_dir = temporary / f"diff-{case_index:03d}"
        case_dir.mkdir()
        input_count = 1 + rng.randrange(3)
        inputs = []
        for input_index in range(input_count):
            path = case_dir / f"input-{input_index}.info"
            path.write_text(random_trace(rng, case_index, input_index), encoding="utf-8")
            inputs.append(str(path))
        c_output = case_dir / "c.info"
        py_output = case_dir / "py.info"
        c_result = run([str(binary), *inputs, "-o", str(c_output)])
        py_result = run([sys.executable, str(ORACLE), "-o", str(py_output), *inputs])
        if c_result.returncode != py_result.returncode:
            raise AssertionError(f"diff {case_index} status mismatch: C={c_result.returncode} Python={py_result.returncode}")
        if c_result.returncode == 0:
            if c_output.read_bytes() != py_output.read_bytes():
                raise AssertionError(f"diff {case_index} output mismatch\n{c_output.read_text()}\n---\n{py_output.read_text()}")

    malformed = [
        "SF:/bad.c\nDA:x,1\nend_of_record\n",
        "SF:/bad.c\nBRDA:1,0,0,nope\nend_of_record\n",
        "SF:/bad.c\nFNDA:1\nend_of_record\n",
        "SF:/bad.c\nMCDC:1,2,z,1,0,x\nend_of_record\n",
        "not a trace\n",
        "SF:/bad.c\nend_of_record\nend_of_record\n",
    ]
    for _ in range(20):
        line_no = rng.randrange(1, 10000)
        token = rng.randrange(1 << 20)
        defect = rng.randrange(5)
        if defect == 0:
            row = f"DA:{line_no},invalid-{token},abcd\n"
        elif defect == 1:
            row = f"BRDA:{line_no},0,0,-{token}\n"
        elif defect == 2:
            row = f"FNDA:invalid-{token},generated\n"
        elif defect == 3:
            row = f"MCDC:{line_no},1,z,{token},0,term-{token}\n"
        else:
            row = f"FNDA:{token}\n"
        malformed.append(f"SF:/generated/malformed-{token}.c\n{row}end_of_record\n")
    for case_index, text in enumerate(malformed):
        case_dir = temporary / f"diff-malformed-{case_index}"
        case_dir.mkdir()
        source = case_dir / "bad.info"
        source.write_text(text, encoding="utf-8")
        c_result = run([str(binary), str(source), "-o", str(case_dir / "c.info")])
        py_result = run([sys.executable, str(ORACLE), "-o", str(case_dir / "py.info"), str(source)])
        if c_result.returncode != 2 or py_result.returncode != 2:
            raise AssertionError(f"malformed differential {case_index} class mismatch: C={c_result.returncode} Python={py_result.returncode}")
    return count + len(malformed)


def determinism_test(binary: Path, temporary: Path) -> int:
    base = temporary / "determinism"
    base.mkdir()
    inputs = []
    for index in range(8):
        path = base / f"{index}.info"
        path.write_text(
            f"TN:run-{index % 3}\nSF:/deterministic/{index % 4}.c\n"
            f"FN:{index + 1},fn_{index % 4}\nFNDA:{index + 2},fn_{index % 4}\n"
            f"DA:{index % 7 + 1},{index},sum-check\nBRDA:{index % 5 + 1},0,0,{index % 3}\n"
            "end_of_record\n", encoding="utf-8")
        inputs.append(str(path))
    reference = None
    runs = 0
    for jobs, order in ((1, inputs), (2, inputs), (8, inputs), (2, list(reversed(inputs)))):
        output = base / f"out-{runs}.info"
        result = run([str(binary), "--jobs", str(jobs), *order, "-o", str(output)])
        if result.returncode != 0:
            raise AssertionError(f"determinism run failed: {result.stderr.decode(errors='replace')}")
        payload = output.read_bytes()
        if reference is None:
            reference = payload
        elif payload != reference:
            raise AssertionError(f"output differed for jobs={jobs} and shuffled={order != inputs}")
        runs += 1
    return runs


def list_and_stdio_tests(binary: Path, temporary: Path) -> int:
    root = temporary / "list-stdin"
    root.mkdir()
    first = root / "first.info"
    second = root / "second.info"
    first.write_text("SF:/list.c\nDA:1,2\nend_of_record\n", encoding="utf-8")
    second.write_text("SF:/list.c\nDA:1,3\nend_of_record\n", encoding="utf-8")
    listfile = root / "inputs.txt"
    listfile.write_text(f"{first}\n{second}\n", encoding="utf-8")
    output = root / "list-out.info"
    result = run([str(binary), f"@{listfile}", "-o", str(output)])
    expected = expected_trace("/list.c", "DA:1,5\n", summary=(0,0,0,0,0,0,1,1))
    if result.returncode != 0 or output.read_text(encoding="utf-8") != expected:
        raise AssertionError("@listfile did not merge its entries")
    stdin_text = b"SF:/stdin.c\nDA:2,7\nend_of_record\n"
    stdio = run([str(binary), "-", "-o", "-"], input_data=stdin_text)
    expected_stdout = expected_trace("/stdin.c", "DA:2,7\n", summary=(0,0,0,0,0,0,1,1)).encode()
    if stdio.returncode != 0 or stdio.stdout != expected_stdout:
        raise AssertionError("stdin/stdout mode failed")
    run_dir = root / "runs"
    run_dir.mkdir()
    temp_output = root / "tmpdir-out.info"
    with_tmp = run([str(binary), "--tmpdir", str(run_dir), "-v", str(first), "-o", str(temp_output)])
    if with_tmp.returncode != 0 or list(run_dir.iterdir()) or b"parsed " not in with_tmp.stderr or b"stats LF=" not in with_tmp.stderr:
        raise AssertionError("--tmpdir cleanup or verbose summary failed")
    quiet = run([str(binary), "-q", str(first), "-o", str(root / "quiet.info")])
    if quiet.returncode != 0 or quiet.stderr:
        raise AssertionError("-q emitted unexpected progress")
    in_place = root / "in-place.info"
    in_place.write_text("SF:/same.c\nDA:1,3\nend_of_record\n", encoding="utf-8")
    same_path = run([str(binary), str(in_place), "-o", str(in_place)])
    expected_in_place = expected_trace("/same.c", "DA:1,3\n", summary=(0,0,0,0,0,0,1,1))
    if same_path.returncode != 0 or in_place.read_text(encoding="utf-8") != expected_in_place:
        raise AssertionError("atomic output did not support overwriting an input")
    return 5


def signal_cleanup_tests(binary: Path) -> int:
    if os.name == "nt":
        return 0

    signals = [(name, getattr(signal, name)) for name in ("SIGHUP", "SIGTERM", "SIGINT")
               if hasattr(signal, name)]
    cache_root = Path(tempfile.gettempdir())
    cache_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="signal-cleanup-", dir=cache_root) as work_dir:
        work = Path(work_dir)
        source = work / "out-of-order.info"
        with source.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write("SF:/signal-cleanup.c\n")
            for line_number in range(1_000_000, 0, -1):
                stream.write(f"DA:{line_number},1\n")
            stream.write("end_of_record\n")

        for name, signal_number in signals:
            run_dir = work / name.lower()
            run_dir.mkdir()
            output = work / f"{name.lower()}-out.info"
            process = subprocess.Popen(
                [str(binary), "--mem-limit", "8M", "--jobs", "1", "--tmpdir", str(run_dir),
                 str(source), "-o", str(output)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            try:
                deadline = time.monotonic() + 20
                run_files = []
                while time.monotonic() < deadline:
                    run_files = list(run_dir.iterdir())
                    if run_files:
                        break
                    if process.poll() is not None:
                        stdout, stderr = process.communicate()
                        raise AssertionError(
                            f"{name} cleanup test exited before creating an external-sort run: "
                            f"status={process.returncode}, stdout={stdout!r}, stderr={stderr!r}"
                        )
                    time.sleep(0.005)
                if not run_files or process.poll() is not None:
                    stdout, stderr = process.communicate(timeout=5)
                    raise AssertionError(
                        f"{name} cleanup test did not catch a live external-sort run: "
                        f"status={process.returncode}, stdout={stdout!r}, stderr={stderr!r}"
                    )
                process.send_signal(signal_number)
                stdout, stderr = process.communicate(timeout=30)
            except BaseException:
                if process.poll() is None:
                    process.kill()
                    process.communicate()
                raise

            remaining = list(run_dir.iterdir())
            if process.returncode != 3 or b"interrupted" not in stderr or remaining:
                raise AssertionError(
                    f"{name} did not clean external-sort runs after interruption: "
                    f"status={process.returncode}, runs_before_signal={run_files!r}, "
                    f"remaining={remaining!r}, stdout={stdout!r}, stderr={stderr!r}"
                )
    return len(signals)


def closed_stdout_pipe_cleanup_test(binary: Path, temporary: Path) -> int:
    if os.name == "nt":
        return 0

    work = temporary / "closed-stdout-pipe"
    work.mkdir()
    source = work / "out-of-order.info"
    with source.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write("SF:/closed-pipe.c\n")
        for line_number in range(300_000, 0, -1):
            stream.write(f"DA:{line_number},1\n")
        stream.write("end_of_record\n")

    run_dir = work / "runs"
    run_dir.mkdir()
    process = subprocess.Popen(
        [str(binary), "--mem-limit", "8M", "--jobs", "1", "--tmpdir", str(run_dir),
         str(source), "-o", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    stdout = process.stdout
    stderr = process.stderr
    try:
        deadline = time.monotonic() + 30
        run_files = []
        while time.monotonic() < deadline:
            run_files = list(run_dir.iterdir())
            if run_files:
                break
            if process.poll() is not None:
                output, error = process.communicate()
                raise AssertionError(
                    "closed-pipe test exited before creating an external-sort run: "
                    f"status={process.returncode}, stdout={output!r}, stderr={error!r}"
                )
            time.sleep(0.01)
        if not run_files:
            raise AssertionError("closed-pipe test did not create an external-sort run")

        stdout.close()
        returncode = process.wait(timeout=30)
        error = stderr.read()
    except BaseException:
        if process.poll() is None:
            process.kill()
            process.wait()
        if stdout is not None and not stdout.closed:
            stdout.close()
        if stderr is not None:
            stderr.close()
        raise

    remaining = list(run_dir.iterdir())
    if returncode != 3 or remaining:
        raise AssertionError(
            "closed stdout pipe did not fail through normal cleanup: "
            f"status={returncode}, runs_before_close={run_files!r}, remaining={remaining!r}, "
            f"stderr={error!r}"
        )
    if stderr is not None:
        stderr.close()
    return 1


def interrupted_staged_output_tests(binary: Path) -> int:
    if os.name == "nt" or not hasattr(signal, "SIGSTOP") or not hasattr(signal, "SIGCONT"):
        return 0

    cache_root = Path(tempfile.gettempdir())
    cache_root.mkdir(parents=True, exist_ok=True)
    line_count = 500_000
    summary = (
        "FNF:0\nFNH:0\nBRF:0\nBRH:0\nMCF:0\nMCH:0\n"
        f"LF:{line_count}\nLH:{line_count}\n"
    ).encode()
    with tempfile.TemporaryDirectory(prefix="cancel-final-", dir=cache_root) as work_dir:
        work = Path(work_dir)
        for name, numbers in (("direct", range(1, line_count + 1)),
                              ("fallback", range(line_count, 0, -1))):
            case_dir = work / name
            case_dir.mkdir()
            run_dir = case_dir / "runs"
            run_dir.mkdir()
            source = case_dir / "input.info"
            with source.open("w", encoding="utf-8", newline="\n") as stream:
                stream.write("SF:/cancel.c\n")
                for line_number in numbers:
                    stream.write(f"DA:{line_number},1\n")
                stream.write("end_of_record\n")
            output = case_dir / "output.info"
            original = "preexisting output\n"
            output.write_text(original, encoding="utf-8")
            expected_size = source.stat().st_size + len(summary)
            target_size = expected_size - (512 * 1024)
            process = subprocess.Popen(
                [str(binary), "--mem-limit", "8M", "--jobs", "1", "--tmpdir", str(run_dir),
                 str(source), "-o", str(output)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            try:
                deadline = time.monotonic() + 60
                staged = None
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        stdout, stderr = process.communicate()
                        raise AssertionError(
                            f"{name} interruption test exited before staged output reached its final phase: "
                            f"status={process.returncode}, stdout={stdout!r}, stderr={stderr!r}"
                        )
                    for candidate in case_dir.glob("lcovmerge-*"):
                        try:
                            if candidate.stat().st_size >= target_size:
                                staged = candidate
                                break
                        except FileNotFoundError:
                            continue
                    if staged is not None:
                        break
                    time.sleep(0.001)
                if staged is None:
                    stdout, stderr = process.communicate(timeout=5)
                    raise AssertionError(
                        f"{name} interruption test did not observe nearly complete staged output: "
                        f"status={process.returncode}, stdout={stdout!r}, stderr={stderr!r}"
                    )

                process.send_signal(signal.SIGSTOP)
                time.sleep(0.02)
                if process.poll() is not None or not staged.exists() or output.read_text(encoding="utf-8") != original:
                    stdout, stderr = process.communicate()
                    raise AssertionError(
                        f"{name} output completed before the staged file could be interrupted: "
                        f"status={process.returncode}, stdout={stdout!r}, stderr={stderr!r}"
                    )
                process.send_signal(signal.SIGTERM)
                process.send_signal(signal.SIGCONT)
                stdout, stderr = process.communicate(timeout=30)
            except BaseException:
                if process.poll() is None:
                    process.kill()
                    process.communicate()
                raise

            remaining_output_temps = list(case_dir.glob("lcovmerge-*"))
            remaining_runs = list(run_dir.iterdir())
            if (process.returncode != 3 or output.read_text(encoding="utf-8") != original or
                    remaining_output_temps or remaining_runs):
                raise AssertionError(
                    f"{name} interruption published output or left temporary files: "
                    f"status={process.returncode}, remaining_output_temps={remaining_output_temps!r}, "
                    f"remaining_runs={remaining_runs!r}, stdout={stdout!r}, stderr={stderr!r}"
                )
    return 2


def interruption_documentation_tests() -> int:
    claims = {
        "docs/LIMITATIONS.md": [
            ("POSIX handled interruption", ("posix", "sigint", "sigterm", "sighup", "caught")),
            ("SIGPIPE cleanup", ("sigpipe", "epipe", "cleanup")),
            ("stdout rollback limit", ("stdout", "cannot be rolled back", "partial output")),
            ("Windows cancellation limit", ("windows", "readfile", "writefile", "blocking", "posix only")),
        ],
        "docs/USAGE.md": [
            ("POSIX handled interruption", ("posix", "sigint", "sigterm", "sighup", "caught")),
            ("SIGPIPE cleanup", ("sigpipe", "epipe", "normal cleanup")),
            ("stdout rollback limit", ("stdout", "cannot be rolled back", "partial")),
            ("Windows cancellation limit", ("windows", "readfile", "writefile", "worker-thread joins", "posix-only")),
        ],
        "man/lcovmerge.1": [
            ("POSIX handled interruption", ("posix", "sigint", "sigterm", "sighup", "caught")),
            ("SIGPIPE cleanup", ("sigpipe", "epipe", "normal cleanup")),
            ("stdout rollback limit", ("stdout", "cannot be rolled back", "partial")),
            ("Windows cancellation limit", ("windows", "readfile", "writefile", "blocking worker", "posix-only")),
        ],
        "docs/ARCHITECTURE.md": [
            ("POSIX handled interruption", ("posix", "sigint", "sigterm", "sighup", "caught")),
            ("SIGPIPE cleanup", ("sigpipe", "epipe", "i/o-failure cleanup")),
            ("stdout rollback limit", ("stdout", "cannot be rolled back")),
            ("Windows cancellation limit", ("windows", "readfile", "writefile", "blocking worker joins", "posix-only")),
        ],
        "CHANGELOG.md": [
            ("POSIX handled interruption", ("posix", "sigint", "sigterm", "sighup", "caught interruption")),
            ("SIGPIPE cleanup", ("sigpipe", "epipe", "failure cleanup")),
            ("platform and stdout limits", ("windows", "stdout", "cannot be rolled back")),
        ],
    }
    for relative_path, document_claims in claims.items():
        text = re.sub(r"\s+", " ", (ROOT / relative_path).read_text(encoding="utf-8")).casefold()
        for claim_name, terms in document_claims:
            missing = [term for term in terms if term not in text]
            if missing:
                raise AssertionError(
                    f"{relative_path} is missing {claim_name}: {', '.join(missing)}"
                )
    return sum(len(document_claims) for document_claims in claims.values())


def many_paths_test(binary: Path, temporary: Path) -> int:
    base = temporary / "many-paths"
    base.mkdir()
    source = base / "many-paths.info"
    with source.open("w", encoding="utf-8", newline="\n") as stream:
        for index in range(4105):
            stream.write(f"SF:/table/{index:05}.c\nDA:1,1\nend_of_record\n")
        stream.write("SF:/table/00000.c\nDA:1,2\nend_of_record\n")
    output = base / "out.info"
    result = run([str(binary), str(source), "-o", str(output)])
    if result.returncode != 0:
        raise AssertionError(f"path table overflow run failed: {result.stderr!r}")
    trace = output.read_text(encoding="utf-8")
    paths = re.findall(r"(?m)^SF:(.*)$", trace)
    if len(paths) != 4105 or paths != sorted(paths) or len(set(paths)) != len(paths):
        raise AssertionError("many-path input produced missing, duplicate, or unsorted SF paths")
    sections = trace.split("end_of_record\n")
    first = next((section for section in sections if section.startswith("SF:/table/00000.c\n")), "")
    if "DA:1,3\n" not in first:
        raise AssertionError("many-path input failed to merge a repeated SF path")
    return 1


def lcov_differential(binary: Path, temporary: Path) -> int:
    import shutil

    lcov = shutil.which("lcov")
    if not lcov:
        return 0
    base = temporary / "lcov-diff"
    base.mkdir()
    rng = random.Random(0x1C0A2C06)
    inputs = []
    for shard in range(2):
        path = base / f"shard-{shard}.info"
        with path.open("w", encoding="utf-8", newline="\n") as stream:
            for case_index in range(200):
                hits = rng.randrange(4)
                stream.write(
                    f"SF:/lcov/generated/case-{case_index:03}.c\n"
                    "FN:1,generated\n"
                    f"FNDA:{hits},generated\nDA:1,{hits}\nBRDA:1,0,0,{hits}\n"
                    f"MCDC:1,1,f,{hits},0,generated\nMCDC:1,1,t,{hits},0,generated\nend_of_record\n")
        inputs.append(str(path))
    ours = base / "ours.info"
    oracle_lcov = base / "lcov.info"
    ours_result = run([str(binary), *inputs, "-o", str(ours)])
    command = [lcov, "--branch-coverage", "--mcdc-coverage"]
    for path in inputs:
        command.extend(("-a", path))
    command.extend(("-o", str(oracle_lcov)))
    lcov_result = run(command, env={**os.environ, "LC_ALL": "C"})
    if ours_result.returncode != 0 or lcov_result.returncode != 0:
        raise AssertionError(f"lcov differential command failed: ours={ours_result.stderr!r} lcov={lcov_result.stderr!r}")
    ours_stats_result = run([str(binary), "--stats", *inputs, "-o", str(base / "stats.info")])
    ours_match = re.search(rb"stats LF=(\d+) LH=(\d+) FNF=(\d+) FNH=(\d+) BRF=(\d+) BRH=(\d+)", ours_stats_result.stderr)
    lcov_summary = run([lcov, "--branch-coverage", "--mcdc-coverage", "--summary", str(oracle_lcov)], env={**os.environ, "LC_ALL": "C"})
    line = re.search(rb"lines\.*: [^(]*\((\d+) of (\d+) line", lcov_summary.stdout + lcov_summary.stderr)
    function = re.search(rb"functions\.*: [^(]*\((\d+) of (\d+) function", lcov_summary.stdout + lcov_summary.stderr)
    branch = re.search(rb"branches\.*: [^(]*\((\d+) of (\d+) branch", lcov_summary.stdout + lcov_summary.stderr)
    if not (ours_match and line and function and branch):
        raise AssertionError(f"could not read lcov summary counts: {lcov_summary.stdout!r} {lcov_summary.stderr!r}")
    ours_counts = tuple(map(int, ours_match.groups()))
    lcov_counts = (int(line[2]), int(line[1]), int(function[2]), int(function[1]), int(branch[2]), int(branch[1]))
    if ours_counts != lcov_counts:
        raise AssertionError(f"lcov summary mismatch: lcovmerge={ours_counts} lcov={lcov_counts}")

    def mcdc_counts(trace: Path) -> dict[tuple[str, str, str, str, str, str], int]:
        result: dict[tuple[str, str, str, str, str, str], int] = {}
        current = ""
        for raw in trace.read_text(encoding="utf-8").splitlines():
            if raw.startswith("SF:"):
                current = raw[3:]
            elif raw.startswith("MCDC:"):
                fields = raw[5:].split(",", 5)
                key = (current, fields[0], fields[1], fields[2], fields[4], fields[5])
                result[key] = int(fields[3])
        return result

    if mcdc_counts(ours) != mcdc_counts(oracle_lcov):
        raise AssertionError("lcov MC/DC merged rows differed from lcovmerge")
    return 200


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--no-lcov", action="store_true")
    args = parser.parse_args()
    binary = args.binary.resolve()
    if not binary.exists():
        parser.error(f"binary does not exist: {binary}")
    with tempfile.TemporaryDirectory(prefix="lcovmerge-tests-") as temp:
        temporary = Path(temp)
        goldens = run_goldens(binary, temporary)
        malformed = malformed_tests(binary, temporary)
        differential = differential_tests(binary, temporary)
        deterministic = determinism_test(binary, temporary)
        io_cases = list_and_stdio_tests(binary, temporary)
        signal_cases = signal_cleanup_tests(binary)
        closed_pipe_cases = closed_stdout_pipe_cleanup_test(binary, temporary)
        staged_signal_cases = interrupted_staged_output_tests(binary)
        interruption_doc_cases = interruption_documentation_tests()
        many_paths_cases = many_paths_test(binary, temporary)
        lcov_cases = 0 if args.no_lcov else lcov_differential(binary, temporary)
    print(f"golden_cases={goldens} malformed_cases={malformed} oracle_cases={differential} "
          f"determinism_runs={deterministic} io_cases={io_cases} signal_cleanup_cases={signal_cases} "
          f"closed_pipe_cases={closed_pipe_cases} "
          f"interrupted_staged_output_cases={staged_signal_cases} many_paths_cases={many_paths_cases} "
          f"interruption_doc_cases={interruption_doc_cases} "
          f"lcov_cases={lcov_cases}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
