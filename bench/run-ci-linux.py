#!/usr/bin/env python3
"""Collect a repeatable Ubuntu benchmark artifact for manual CI runs."""

from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import platform
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / ".luna-tmp" / "linux-benchmark-artifact"


def command_output(argv: list[str]) -> str:
    try:
        result = subprocess.run(argv, text=True, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, check=False)
    except OSError:
        return "unavailable"
    return result.stdout.strip()


def first_line(value: str) -> str:
    return value.splitlines()[0] if value and value != "unavailable" else value


def cpu_model() -> str:
    try:
        contents = pathlib.Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "unavailable"
    for line in contents.splitlines():
        if line.lower().startswith(("model name", "hardware", "processor name")):
            value = line.partition(":")[2].strip()
            if value:
                return value
    return "unavailable"


def memory_bytes() -> int | None:
    try:
        contents = pathlib.Path("/proc/meminfo").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    match = re.search(r"^MemTotal:\s+(\d+)\s+kB$", contents, re.MULTILINE)
    return int(match.group(1)) * 1024 if match else None


def host_metadata() -> dict[str, object]:
    lcov_version = command_output(["lcov", "--version"])
    compiler_version = command_output(["cc", "--version"])
    hyperfine_version = command_output(["hyperfine", "--version"])
    return {
        "runner_label": os.environ.get("BENCH_RUNNER_LABEL", "ubuntu-24.04"),
        "os": platform.platform(),
        "kernel": platform.release(),
        "architecture": platform.machine(),
        "cpu_model": cpu_model(),
        "logical_cores": os.cpu_count(),
        "memory_bytes": memory_bytes(),
        "compiler": first_line(compiler_version),
        "lcov": lcov_version.splitlines()[-1] if lcov_version != "unavailable" else lcov_version,
        "hyperfine": first_line(hyperfine_version),
        "rss_tool": "/usr/bin/time -v",
    }


def source_metadata() -> dict[str, object]:
    revision = command_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"])
    return {
        "repository": os.environ.get("GITHUB_REPOSITORY", "local"),
        "revision": revision,
        "workflow": os.environ.get("GITHUB_WORKFLOW", "manual local run"),
        "run_id": os.environ.get("GITHUB_RUN_ID"),
        "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=pathlib.Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--include-path-heavy", action="store_true",
                        help="also measure the 2,000,000-path workload")
    args = parser.parse_args()
    if platform.system() != "Linux":
        parser.error("Linux CI measurements require a Linux host")
    if not pathlib.Path("/usr/bin/time").is_file():
        parser.error("/usr/bin/time is required for peak RSS measurements")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")

    scratch = ROOT / ".luna-tmp"
    scratch.mkdir(parents=True, exist_ok=True)
    os.environ["TMPDIR"] = str(scratch)
    os.environ.setdefault("LC_ALL", "C")
    output_dir = args.output_dir.resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        parser.error(f"refusing to overwrite non-empty artifact directory: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)

    runs = {"S": 5, "M": 3, "L": 2}
    if args.include_path_heavy:
        runs["PATH-HEAVY"] = 2
    datasets = []
    text = [
        "Linux benchmark artifact",
        f"measurement_date={datetime.datetime.now(datetime.timezone.utc).date().isoformat()}",
        f"host={json.dumps(host_metadata(), sort_keys=True)}",
        f"source={json.dumps(source_metadata(), sort_keys=True)}",
        "method=one hyperfine warmup; listed measured repeats; median wall time; max measured RSS from /usr/bin/time -v",
        "generator=tools/gen-lcov.py --benchmark-compatible; PATH-HEAVY uses tools/gen-path-heavy.py",
        "temporary_data=under .luna-tmp and removed by bench/run.py",
        "",
    ]
    status = 0
    for workload, repeat_count in runs.items():
        report_path = output_dir / f"{workload.lower().replace('-', '_')}.txt"
        json_path = output_dir / f"{workload.lower().replace('-', '_')}.json"
        command = [
            sys.executable, "-I", str(ROOT / "bench/run.py"),
            "--workload", workload,
            "--hyperfine-runs", str(repeat_count),
            "--timeout", str(args.timeout),
            "--report", str(report_path),
            "--json-report", str(json_path),
        ]
        result = subprocess.run(command, cwd=ROOT, env=os.environ.copy(),
                                text=True, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, check=False)
        text.extend([f"## {workload}", result.stdout.rstrip(), ""])
        if not json_path.is_file():
            reason = f"bench/run.py exited before producing JSON (status {result.returncode})"
            datasets.append({"name": workload, "status": "SKIPPED", "reason": reason})
            text.append(f"{workload}: status=SKIPPED reason={reason}")
            if workload != "PATH-HEAVY":
                status = 1
            continue
        measured = json.loads(json_path.read_text(encoding="utf-8"))
        if measured.get("schema_version") != 1 or len(measured.get("datasets", [])) != 1:
            raise SystemExit(f"invalid bench/run.py result for {workload}")
        dataset = measured["datasets"][0]
        dataset["measurement"] = measured["method"]
        datasets.append(dataset)
        lcovmerge = dataset.get("results", {}).get("lcovmerge", {})
        if lcovmerge.get("status") != "OK":
            text.append(f"{workload}: lcovmerge status={lcovmerge.get('status', 'missing')}")
            if workload != "PATH-HEAVY":
                status = 1
        if result.returncode != 0:
            text.append(f"{workload}: bench/run.py exit status {result.returncode}; individual comparator statuses are retained")

    artifact = {
        "schema_version": 1,
        "measurement_date": datetime.datetime.now(datetime.timezone.utc).date().isoformat(),
        "host": host_metadata(),
        "source": source_metadata(),
        "method": {
            "runner": "bench/run.py",
            "repeat_counts": runs,
            "warmup_count": 1,
            "wall_time_summary": "median of measured hyperfine runs",
            "rss_summary": "maximum across measured GNU /usr/bin/time -v samples; warmup excluded",
            "rss_tool": "/usr/bin/time -v",
            "timeout_seconds_per_run": args.timeout,
            "cache_state": "no explicit cache flush; one warmup per command; runner load uncontrolled",
        },
        "tool_labels": {
            "lcovmerge": "lcovmerge 1.0.2 built from checkout",
            "lcov": host_metadata()["lcov"],
        },
        "datasets": datasets,
        "text_report": "benchmark.txt",
    }
    (output_dir / "benchmark.json").write_text(json.dumps(artifact, indent=2) + "\n",
                                                encoding="utf-8")
    (output_dir / "benchmark.txt").write_text("\n".join(text).rstrip() + "\n",
                                               encoding="utf-8")
    print(f"Wrote {output_dir / 'benchmark.json'} and {output_dir / 'benchmark.txt'}")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
