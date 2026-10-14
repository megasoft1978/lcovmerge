#!/usr/bin/env python3
"""Bounded-memory compatibility stress cases with streaming verification."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time

BYTE_BUDGET = 8 * 1024 * 1024
MIN_INPUT_BYTES = 3 * BYTE_BUDGET
TIMEOUT = 240


def expected_records(spec: dict):
    """Yield canonical record lines independently from the input writer."""
    shape = spec["shape"]
    if shape == "checksums":
        yield f"DA:1,{spec['hits']},{spec['checksum']}\n"
        return
    if shape in ("functions", "legacy"):
        for i in range(spec["functions"]):
            yield f"FNL:{i},{i + 1},{i + 1}\n"
            yield f"FNA:{i},{spec['hits']},fn_{i:08d}\n"
    elif shape == "aliases":
        yield "FNL:0,1,1\n"
        for i in range(spec["aliases"]):
            yield f"FNA:0,{spec['hits']},alias_{i:08d}\n"
    elif shape == "branches":
        for i in range(spec["branch_points"]):
            kind = "e" if i % 7 == 0 else "f" if i % 11 == 0 else ""
            yield f"BRDA:1,{kind}{'U' if i % 5 == 0 else ''}0,branch_{i:08d},{spec['hits']}\n"
    elif shape == "mcdc":
        for i in range(spec["mcdc_conditions"]):
            group = f"{'U' if i % 5 == 0 else ''}{spec['mcdc_conditions']}"
            yield f"MCDC:1,{group},t,{spec['hits']},{i},condition_{i:08d}\n"
            yield f"MCDC:1,{group},f,0,{i},condition_{i:08d}\n"
    elif shape not in ("lines", "branches-no-da", "functions-no-da"):
        raise AssertionError(shape)
    yield from (f"DA:{i},{spec['hits']}\n" for i in range(1, spec["lines"] + 1))


def verify_output(path: Path, spec: dict) -> None:
    """Stream output and compare every record and summary against expectations."""
    source_seen = False
    records = iter(expected_records(spec))
    got_summary: dict[str, int] = {}
    seen_end = False
    version_seen = False
    with path.open("rt", encoding="utf-8", errors="strict") as stream:
        for raw in stream:
            line = raw.rstrip("\n")
            key, sep, value = line.partition(":")
            if not sep:
                if line == "end_of_record" and source_seen:
                    seen_end = True
                    if next(stream, ""):
                        raise AssertionError("trailing output after end_of_record")
                    break
                raise AssertionError(f"malformed output line {line[:100]!r}")
            if key == "SF":
                if source_seen or value != spec["source"]:
                    raise AssertionError(f"unexpected SF {value!r}")
                source_seen = True
            elif key == "TN":
                continue
            elif key == "VER":
                if version_seen or value != spec.get("version"):
                    raise AssertionError(f"unexpected source version {value!r}")
                version_seen = True
            elif key in ("LF", "LH", "FNF", "FNH", "BRF", "BRH", "MCF", "MCH"):
                if key in got_summary:
                    raise AssertionError(f"duplicate summary {key}")
                got_summary[key] = int(value)
            elif key in ("DA", "FNL", "FNA", "FNDA", "FN", "BRDA", "MCDC"):
                # LCOV 2.6 normalizes FN to FNL/FNA. Legacy input is checked
                # through the normalized function records declared in spec.
                want = next(records, None)
                actual = raw if key != "FN" else ""
                if want is None or actual != want:
                    raise AssertionError(f"record mismatch: got {raw[:120]!r}, expected {want!r}")
            else:
                raise AssertionError(f"unexpected output family {key!r}")
    if not source_seen or not seen_end:
        raise AssertionError("output missing SF or end_of_record")
    if bool(spec.get("version")) != version_seen:
        raise AssertionError("source version missing")
    extra = next(records, None)
    if extra is not None:
        raise AssertionError(f"truncated output; next expected record {extra[:100]!r}")
    wanted = {"LF": spec["lines"], "LH": spec["lines"] if spec["hits"] else 0,
              "FNF": spec["function_count"], "FNH": spec["function_count"] if spec["hits"] else 0}
    if spec.get("branch_points"):
        reachable = spec["branch_points"] - (spec["branch_points"] + 4) // 5
        wanted.update(BRF=reachable, BRH=reachable if spec["hits"] else 0)
    if spec.get("mcdc_conditions"):
        wanted.update(MCF=2 * spec["mcdc_conditions"], MCH=spec["mcdc_conditions"])
    # Some LCOV outputs omit function summaries when no function records exist.
    wanted = {k: v for k, v in wanted.items() if v or k in got_summary}
    if got_summary != wanted:
        raise AssertionError(f"summaries {got_summary!r} != {wanted!r}")


def validator_self_test() -> None:
    """Guard against regressions that accept altered values, IDs or truncation."""
    import tempfile
    spec = {"source": "/tmp/verify.c", "shape": "functions", "hits": 2,
            "lines": 1, "functions": 1, "aliases": 0, "function_count": 1}
    good = ("TN:t\nSF:/tmp/verify.c\nFNL:0,1,1\nFNA:0,2,fn_00000000\n"
            "FNF:1\nFNH:1\nDA:1,2\nLF:1\nLH:1\nend_of_record\n")
    mutants = (good.replace("FNA:0,2,", "FNA:0,3,"),
               good.replace("FNL:0,", "FNL:9,"),
               good.replace("FNH:1", "FNH:0"),
               good.replace("DA:1,2", "DA:1,3"),
               good.replace("LF:1", "LF:2"),
               good.replace("FNA:0,2,fn_00000000\n", ""))
    with tempfile.TemporaryDirectory() as directory:
        target = Path(directory) / "out.info"
        target.write_text(good)
        verify_output(target, spec)
        for mutant in mutants:
            target.write_text(mutant)
            try:
                verify_output(target, spec)
            except (AssertionError, ValueError):
                continue
            raise AssertionError("validator accepted malformed-output self-test")


def branch_validator_self_test() -> None:
    import tempfile
    spec = {"source": "/tmp/branch.c", "shape": "branches", "hits": 2,
            "lines": 1, "functions": 0, "aliases": 0, "function_count": 0,
            "branch_points": 6}
    good = ("TN:t\nSF:/tmp/branch.c\n" + "".join(expected_records(spec)) +
            "BRF:4\nBRH:4\nLF:1\nLH:1\nend_of_record\n")
    mutants = (good.replace("eU0", "e0", 1),
               good.replace("branch_00000000,2", "branch_00000000,3", 1),
               good.replace("BRF:4", "BRF:5"),
               good.replace("branch_00000005", "branch_00000004"))
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "out.info"
        path.write_text(good)
        verify_output(path, spec)
        for trace in mutants:
            path.write_text(trace)
            try:
                verify_output(path, spec)
            except (AssertionError, ValueError):
                continue
            raise AssertionError("branch validator accepted altered output")


def write_case(path: Path, source: Path, shape: str, size: int) -> dict:
    hits = 2
    functions = aliases = lines = conditions = branches = 0
    with path.open("wt", encoding="utf-8", newline="\n") as out:
        out.write(f"TN:bounded\nSF:{source}\n")
        if shape == "lines":
            # DA payload itself drives a >24 MiB input.
            lines = size
            for i in range(1, lines + 1):
                out.write(f"DA:{i},{hits}\n")
        elif shape == "checksums":
            out.write("VER:bounded-rust-v1\n")
            for _ in range(size):
                out.write("DA:1,1,XUFAKrxLKna5cZ2REBfFkg\n")
            lines, hits = 1, size
        elif shape == "functions":
            functions = size
            for i in range(functions):
                out.write(f"FNL:{i * 7 + 2},{i + 1},{i + 1}\n")
            for i in range(functions):
                out.write(f"FNA:{i * 7 + 2},{hits},fn_{i:08d}\n")
            lines = functions
        elif shape == "legacy":
            functions = size
            for i in range(functions):
                out.write(f"FN:{i + 1},{i + 1},fn_{i:08d}\n")
            for i in range(functions):
                out.write(f"FNDA:{hits},fn_{i:08d}\n")
            lines = functions
        elif shape == "aliases":
            aliases = size
            out.write("FNL:17,1,1\n")
            for i in range(aliases):
                out.write(f"FNA:17,{hits},alias_{i:08d}\n")
            lines = 1
        elif shape == "branches":
            branches = size
            for i in range(branches):
                kind = "e" if i % 7 == 0 else "f" if i % 11 == 0 else ""
                group = f"{kind}{'U' if i % 5 == 0 else ''}70000"
                out.write(f"BRDA:1,{group},branch_{i:08d},{hits}\n")
            lines = 1
        elif shape == "branches-no-da":
            out.write(f"DA:1,0\nend_of_record\nTN:removed\nSF:{source}\n")
            for i in range(size):
                out.write(f"BRDA:2,70000,branch_{i:08d},0\n")
            lines, hits = 1, 0
        elif shape == "functions-no-da":
            out.write(f"DA:1,1\nend_of_record\nTN:removed\nSF:{source}\nFNL:7,1,1\n")
            for i in range(size):
                out.write(f"FNA:7,1,alias_{i:08d}\n")
            lines, hits = 1, 1
        elif shape == "mcdc":
            conditions = size
            for i in range(conditions):
                group = f"{'U' if i % 5 == 0 else ''}{conditions}"
                out.write(f"MCDC:1,{group},t,{hits},{i},condition_{i:08d}\n")
                out.write(f"MCDC:1,{group},f,0,{i},condition_{i:08d}\n")
            lines = 1
        if shape not in ("lines", "checksums", "branches-no-da", "functions-no-da"):
            for i in range(1, lines + 1):
                out.write(f"DA:{i},{hits}\n")
        out.write("end_of_record\n")
    byte_size = path.stat().st_size
    if byte_size < MIN_INPUT_BYTES:
        raise AssertionError(f"{path.name} is {byte_size} bytes, below {MIN_INPUT_BYTES}")
    # Each function has line 1 covered; DA shape uses its full line sequence.
    return {"source": str(source), "shape": shape, "hits": hits, "lines": lines,
            "functions": functions, "aliases": aliases,
            "mcdc_conditions": conditions, "branch_points": branches,
            "function_count": functions or int(bool(aliases)), "bytes": byte_size,
            **({"checksum": "XUFAKrxLKna5cZ2REBfFkg", "version": "bounded-rust-v1"}
               if shape == "checksums" else {})}


class TempMeter:
    def __init__(self, path: Path):
        self.path = path
        self.peak = 0
        self.running = False

    def _size(self):
        total = 0
        for base, _, files in os.walk(self.path):
            for filename in files:
                try:
                    total += (Path(base) / filename).stat().st_size
                except FileNotFoundError:
                    pass
        self.peak = max(self.peak, total)

    def __enter__(self):
        self.running = True
        self.thread = threading.Thread(target=self._sample, daemon=True)
        self.thread.start()
        return self

    def _sample(self):
        while self.running:
            self._size()
            time.sleep(.05)

    def __exit__(self, *_):
        self.running = False
        self.thread.join()
        self._size()


def run_case(binary: Path, root: Path, config: Path, tempdir: Path,
             name: str, spec: dict, timeout: int) -> dict:
    output = root / f"{name}.out.info"
    stdout, stderr = root / f"{name}.stdout.log", root / f"{name}.stderr.log"
    output.unlink(missing_ok=True)
    for child in tempdir.iterdir():
        if child.is_dir(): shutil.rmtree(child)
        else: child.unlink()
    command = [str(binary), "--lcov-compat=2.6", "--quiet", "--config-file", str(config),
               "--mem-limit", "8M", "--tempdir", str(tempdir), "-a",
               "-" if spec.get("stdin") else str(root / f"{name}.info"), "-o", str(output)]
    if spec["shape"] in ("lines", "mcdc", "branches", "checksums", "branches-no-da"):
        command.append("--no-function-coverage")
    if spec["shape"] in ("branches", "branches-no-da"):
        command.append("--branch-coverage")
    if spec["shape"] == "branches-no-da":
        command += ["--ignore-errors", "inconsistent"]
    if spec.get("extra_input"):
        command += ["-a", str(root / spec["extra_input"])]
    if spec["shape"] == "mcdc":
        command.append("--mcdc-coverage")
    if spec["shape"] == "checksums":
        command.append("--checksum")
    started = time.monotonic()
    meter = TempMeter(tempdir)
    status, timed_out, rss, rss_note = None, False, None, "not measured"
    with meter, stdout.open("wb") as out, stderr.open("wb") as err:
        input_file = (root / spec["input_name"]).open("rb") if spec.get("stdin") else None
        try:
            proc = subprocess.Popen(command, cwd=root, stdout=out, stderr=err, stdin=input_file)
            deadline = time.monotonic() + timeout
            while True:
                if hasattr(os, "wait4"):
                    pid, wait_status, usage = os.wait4(proc.pid, os.WNOHANG)
                    if pid:
                        status = os.waitstatus_to_exitcode(wait_status)
                        proc.returncode = status
                        rss = int(usage.ru_maxrss) * (1 if sys.platform == "darwin" else 1024)
                        rss_note = "per-process wait4 ru_maxrss"
                        break
                    time.sleep(.05)
                else:
                    status = proc.poll()
                    rss_note = "unsupported: per-process wait4 unavailable"
                    if status is not None:
                        break
                    time.sleep(.05)
                if time.monotonic() >= deadline:
                    timed_out = True
                    proc.kill()
                    if hasattr(os, "wait4"):
                        _, wait_status, usage = os.wait4(proc.pid, 0)
                        status = os.waitstatus_to_exitcode(wait_status)
                        proc.returncode = status
                        rss = int(usage.ru_maxrss) * (1 if sys.platform == "darwin" else 1024)
                        rss_note = "per-process wait4 ru_maxrss"
                    else:
                        status = proc.wait()
                    break
        except OSError as exc:
            err.write(str(exc).encode())
        finally:
            if input_file is not None:
                input_file.close()
    row = {"name": name, "argv": command, "status": status, "timed_out": timed_out,
           "seconds": round(time.monotonic() - started, 3), "input_bytes": spec["bytes"],
           "peak_temp_bytes": meter.peak, "peak_rss_bytes": rss, "rss_measurement": rss_note,
           "output": str(output), "stdout_log": str(stdout), "stderr_log": str(stderr), **spec}
    try:
        if status != 0 or timed_out:
            raise AssertionError(f"command status={status} timed_out={timed_out}")
        verify_output(output, spec)
        leftovers = list(tempdir.iterdir())
        if leftovers:
            raise AssertionError(f"temporary files remain after success: {[p.name for p in leftovers[:5]]}")
        row["output_verified"] = True
        row["temp_cleanup_verified"] = True
    except (AssertionError, OSError, ValueError, StopIteration) as exc:
        row.update(output_verified=False, failure=str(exc))
    return row


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--binary", required=True, type=Path)
    ap.add_argument("--workdir", required=True, type=Path)
    ap.add_argument("--timeout", type=int, default=TIMEOUT)
    ap.add_argument("--case", action="append", help="run only the named case (repeatable)")
    ap.add_argument("--target-bytes", type=int, default=28 * 1024 * 1024,
                    help="approximate payload bytes per case (minimum is 24 MiB)")
    args = ap.parse_args()
    validator_self_test()
    branch_validator_self_test()
    if args.target_bytes < MIN_INPUT_BYTES:
        ap.error("--target-bytes must be at least 24 MiB")
    root = args.workdir.resolve(); root.mkdir(parents=True, exist_ok=True)
    binary = args.binary.resolve()
    config = root / "lcovrc"; config.write_text("# bounded compatibility fixture\n")
    tempdir = root / "tmp"; tempdir.mkdir(exist_ok=True)
    # DA rows, many distinct function IDs, and one enormous alias group each
    # independently occupy roughly 28 MiB. This is a byte budget, never rows.
    cases = (("lines", "lines", args.target_bytes // 10),
             ("function-ids", "functions", args.target_bytes // 55),
             ("legacy-functions", "legacy", args.target_bytes // 50),
             ("alias-group", "aliases", args.target_bytes // 21),
             ("mcdc-group", "mcdc", args.target_bytes // 70),
             ("branch-block", "branches", args.target_bytes // 28),
             ("checksum-group", "checksums", args.target_bytes // 28),
             ("branches-no-da", "branches-no-da", args.target_bytes // 28),
             ("functions-no-da", "functions-no-da", args.target_bytes // 21))
    valid_cases = {name for name, _, _ in cases} | {"lines-stdin", "lines-mixed-stdin"}
    if args.case and set(args.case) - valid_cases:
        ap.error("unknown case: " + ", ".join(sorted(set(args.case) - valid_cases)))
    results, failures = [], []
    stdin_spec = None
    for name, shape, size in cases:
        if args.case and name not in args.case and not (name == "lines" and set(args.case) & {"lines-stdin", "lines-mixed-stdin"}):
            continue
        source = root / f"{name}.c"; source.write_text("hello\n" if shape == "checksums" else "int x;\n")
        spec = write_case(root / f"{name}.info", source, shape, size)
        if name == "lines":
            stdin_spec = spec.copy()
            if args.case and name not in args.case:
                continue
        row = run_case(binary, root, config, tempdir, name, spec, args.timeout)
        results.append(row)
        if not row.get("output_verified"):
            failures.append({k: row.get(k) for k in ("name", "failure", "argv", "stdout_log", "stderr_log", "output")})
            print(f"FAIL {name}: {row.get('failure')}", file=sys.stderr)
    # Reuse the DA fixture through stdin without allocating its bytes in Python.
    if not args.case or "lines-stdin" in args.case:
        assert stdin_spec is not None
        stdin_spec.update(stdin=True, input_name="lines.info")
        row = run_case(binary, root, config, tempdir, "lines-stdin", stdin_spec, args.timeout)
        results.append(row)
        if not row.get("output_verified"):
            failures.append({k: row.get(k) for k in ("name", "failure", "argv", "stdout_log", "stderr_log", "output")})
            print(f"FAIL lines-stdin: {row.get('failure')}", file=sys.stderr)
    if not args.case or "lines-mixed-stdin" in args.case:
        assert stdin_spec is not None
        (root / "mixed-extra.info").write_text(f"TN:bounded\nSF:{stdin_spec['source']}\nDA:1,0\nend_of_record\n")
        mixed = dict(stdin_spec, stdin=True, input_name="lines.info", extra_input="mixed-extra.info")
        row = run_case(binary, root, config, tempdir, "lines-mixed-stdin", mixed, args.timeout)
        results.append(row)
        if not row.get("output_verified"):
            failures.append({k: row.get(k) for k in ("name", "failure", "argv", "stdout_log", "stderr_log", "output")})
            print(f"FAIL lines-mixed-stdin: {row.get('failure')}", file=sys.stderr)
    report = {"binary": str(binary), "byte_budget": BYTE_BUDGET, "minimum_input_bytes": MIN_INPUT_BYTES,
              "cases": results, "failures": failures}
    (root / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"workdir": str(root), "cases": len(results), "failures": len(failures),
                      "report": str(root / "report.json")}, indent=2))
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
