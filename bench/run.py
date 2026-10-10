#!/usr/bin/env python3
"""Generate benchmark fixtures outside the repo and measure LCOV mergers."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import platform
import re
import signal
import shlex
import shutil
import subprocess
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKLOADS = {
    "S": (8, 56, 23000),
    "M": (32, 220, 16000),
    "L": (64, 480, 16000),
    "XL-single": (1, 1, 30340000),
    "XL-pair": (2, 1, 15170000),
}
WORKLOAD_NAMES = ("S", "M", "L", "XL-single", "XL-pair", "PATH-HEAVY", "REAL")


def time_wrapper_available() -> bool:
    if platform.system() != "Darwin":
        return pathlib.Path("/usr/bin/time").is_file()
    probe = subprocess.run(
        ["/usr/bin/time", "-l", "true"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    return probe.returncode == 0 and b"maximum resident set size" in probe.stdout


def timed(command: list[str], log: pathlib.Path, timeout: int, use_time_wrapper: bool) -> tuple[int, float, str]:
    start = time.perf_counter()
    if platform.system() == "Darwin" and use_time_wrapper:
        wrapped = ["/usr/bin/time", "-l", *command]
    elif platform.system() != "Darwin" and use_time_wrapper:
        wrapped = ["/usr/bin/time", "-v", *command]
    else:
        wrapped = command
    process = subprocess.Popen(
        wrapped,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=platform.system() != "Windows",
    )
    timed_out = False
    try:
        output, _ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            process.kill()
        output, _ = process.communicate()
    elapsed = time.perf_counter() - start
    payload = output.decode("utf-8", errors="replace")
    if timed_out:
        payload += "\nTIMEOUT\n"
    log.write_text(payload, encoding="utf-8")
    return (124 if timed_out else process.returncode), elapsed, payload


def hyperfine_timed(command: list[str], log: pathlib.Path, timeout: int,
                    runs: int) -> tuple[int, float, str]:
    hyperfine = shutil.which("hyperfine")
    if not hyperfine:
        raise RuntimeError("--hyperfine-runs requires hyperfine")
    json_path = log.with_suffix(".json")
    rss_path = log.with_suffix(".rss.txt")
    linux_time = platform.system() == "Linux"
    if linux_time and not pathlib.Path("/usr/bin/time").is_file():
        raise RuntimeError("repeatable Linux RSS measurements require /usr/bin/time")
    measured_command = command
    metrics = "time_wall_clock,memory_peak_resident"
    if linux_time:
        measured_command = ["/usr/bin/time", "-v", "-o", str(rss_path), "-a", *command]
        metrics = "time_wall_clock"
    wrapped = [
        hyperfine,
        "--shell=none",
        "--style=none",
        "--warmup", "1",
        "--runs", str(runs),
        "--metrics", metrics,
        "--ignore-failure=all-non-zero",
        "--command-name", log.stem,
        "--export-json", str(json_path),
        shlex.join(measured_command),
    ]
    start = time.perf_counter()
    process = subprocess.Popen(
        wrapped,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=platform.system() != "Windows",
    )
    timeout_seconds = timeout * (runs + 1)
    try:
        output, _ = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            process.kill()
        output, _ = process.communicate()
        elapsed = time.perf_counter() - start
        payload = output.decode("utf-8", errors="replace") + "\nTIMEOUT\n"
        log.write_text(payload, encoding="utf-8")
        return 124, elapsed, payload
    elapsed = time.perf_counter() - start
    payload = output.decode("utf-8", errors="replace")
    try:
        report = json.loads(json_path.read_text(encoding="utf-8"))
        result = report["results"][0]
        measurements = result["measurements"]
        summary = result["summary"]
        wall_seconds = float(summary["time_wall_clock"]["median"])
        if linux_time:
            rss_text = rss_path.read_text(encoding="utf-8", errors="replace")
            resident_kib = [int(value) for value in re.findall(
                r"Maximum resident set size \(kbytes\):\s*(\d+)", rss_text)]
            # GNU time records the warmup first; exclude it so RSS matches the
            # measured hyperfine samples used for the median wall time.
            measured_rss_kib = resident_kib[-len(measurements):]
            resident_values = [value * 1024 for value in measured_rss_kib]
        else:
            resident_values = [
                float(item["memory_peak_resident"]["value"])
                for item in measurements if "memory_peak_resident" in item
            ]
        if resident_values:
            payload += f"\nMaximum resident set size: {int(max(resident_values))} bytes\n"
        statuses = [int(item.get("exit_code", 0)) for item in measurements]
        status = next((code for code in statuses if code != 0), 0)
        payload += f"\nhyperfine_runs={len(measurements)} elapsed_s={wall_seconds:.6f}\n"
    except (OSError, KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
        status = process.returncode or 1
        wall_seconds = elapsed
        payload += "\nhyperfine result could not be parsed\n"
    log.write_text(payload, encoding="utf-8")
    return status, wall_seconds, payload


def generate(name: str, out: pathlib.Path, real_dir: pathlib.Path | None) -> list[pathlib.Path]:
    generated = out / "generated"
    generated.mkdir(exist_ok=True)
    if name == "REAL":
        if real_dir is None:
            raise ValueError("REAL requires --real-dir")
        paths = sorted(real_dir.glob("*.info"))
        if not paths:
            raise ValueError("--real-dir has no .info files")
        return paths
    if name == "PATH-HEAVY":
        output = generated / "path-heavy.info"
        subprocess.run(
            ["python3", str(ROOT / "tools/gen-path-heavy.py"), "--count", "2000000", "--output", str(output)],
            check=True,
        )
        return [output]
    shards, files, lines = WORKLOADS[name]
    directory = generated / name
    subprocess.run(
        [
            "python3",
            str(ROOT / "tools/gen-lcov.py"),
            "--out",
            str(directory),
            "--shards",
            str(shards),
            "--files",
            str(files),
            "--lines",
            str(lines),
            "--seed",
            "4242",
            "--checksums",
            "--benchmark-compatible",
        ],
        check=True,
    )
    return sorted(directory.glob("*.info"))


def size_of(inputs: list[pathlib.Path]) -> int:
    return sum(path.stat().st_size for path in inputs)


def rss_line(payload: str) -> str:
    marker = "maximum resident set size" if platform.system() == "Darwin" else "Maximum resident set size"
    return next((line.strip() for line in payload.splitlines() if marker.lower() in line.lower()),
                "RSS unavailable")


def measurement(name: str, inputs: list[pathlib.Path], command: list[str], output: pathlib.Path,
                work: pathlib.Path, timeout: int, tool_label: str, use_time_wrapper: bool,
                hyperfine_runs: int | None) -> tuple[str, dict[str, object]]:
    log = work / f"{name}-{tool_label}-time.txt"
    if hyperfine_runs:
        status, elapsed, payload = hyperfine_timed(command, log, timeout, hyperfine_runs)
    else:
        status, elapsed, payload = timed(command, log, timeout, use_time_wrapper)
    input_bytes = size_of(inputs)
    result_status = "OK" if status == 0 else ("TIMEOUT" if status == 124 else f"ERROR_{status}")
    throughput = f"{input_bytes / elapsed / 1_000_000:.1f} MB/s" if status == 0 and elapsed else "n/a"
    row = (
        f"{name} ({tool_label}): input_bytes={input_bytes} wall_seconds={elapsed:.3f} "
        f"throughput={throughput} status={result_status} {rss_line(payload)}"
    )
    print(row, flush=True)
    rss_match = re.search(r"Maximum resident set size:\s*(\d+) bytes", payload)
    return row, {
        "time_s": round(elapsed, 6),
        "rss_bytes": int(rss_match.group(1)) if rss_match else None,
        "throughput_mb_s": (round(input_bytes / elapsed / 1_000_000, 1)
                            if status == 0 and elapsed else None),
        "status": result_status,
        "exit_code": status,
        "run_count": hyperfine_runs or 1,
        "warmup_count": 1 if hyperfine_runs else 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workload", choices=("all", *WORKLOAD_NAMES), default="all")
    parser.add_argument("--real-dir", type=pathlib.Path)
    parser.add_argument("--node-bin", type=pathlib.Path)
    parser.add_argument("--binary", type=pathlib.Path, default=ROOT / "bin/lcovmerge")
    parser.add_argument("--skip-lcov", action="store_true",
                        help="omit LCOV when its comparison was measured in another run")
    parser.add_argument("--hyperfine-runs", type=int,
                        help="use one hyperfine warmup and this many measured runs per command")
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--report", type=pathlib.Path, default=ROOT / "docs/validation/benchmark.txt")
    parser.add_argument("--json-report", type=pathlib.Path,
                        help="write structured measurement results to this JSON file")
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    if args.hyperfine_runs is not None and args.hyperfine_runs <= 0:
        parser.error("--hyperfine-runs must be positive")
    if args.workload == "all" and args.real_dir is None:
        parser.error("--real-dir is required when --workload all or REAL")
    if args.workload == "REAL" and args.real_dir is None:
        parser.error("--real-dir is required for REAL")

    use_time_wrapper = time_wrapper_available() if not args.hyperfine_runs else False

    binary = args.binary.resolve()
    if not binary.is_file():
        parser.error("build lcovmerge first with make")
    node_candidate = args.node_bin or (pathlib.Path(shutil.which("lcov-result-merger"))
                                       if shutil.which("lcov-result-merger") else None)
    node = str(node_candidate) if node_candidate and node_candidate.is_file() else None
    lcov = shutil.which("lcov")
    lcov_label = "lcov"
    names = WORKLOAD_NAMES if args.workload == "all" else (args.workload,)
    report = [
        f"host={platform.platform()}",
        f"timeout_seconds={args.timeout}",
        "fixture_generator=tools/gen-lcov.py --benchmark-compatible",
        "temporary_data=under TMPDIR; generated inputs removed automatically",
        (f"measurement_source=hyperfine {args.hyperfine_runs} runs after one warmup; wall time is median; RSS is max measured sample" if args.hyperfine_runs else
         "measurement_source=single run"),
        ("rss_source=/usr/bin/time -v per measured hyperfine sample" if args.hyperfine_runs and platform.system() == "Linux" else
         "rss_source=hyperfine memory_peak_resident" if args.hyperfine_runs else
         "/usr/bin/time -l" if platform.system() == "Darwin" and use_time_wrapper else
         "rss_source=unavailable; host policy blocks /usr/bin/time -l sysctl query" if platform.system() == "Darwin" else
         "/usr/bin/time -v" if use_time_wrapper else "rss_source=unavailable"),
        "measurement_date=" + time.strftime("%Y-%m-%d", time.gmtime()),
    ]
    exit_code = 0
    dataset_results: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="lcovmerge-bench-") as directory:
        work = pathlib.Path(directory)
        for name in names:
            try:
                inputs = generate(name, work, args.real_dir)
            except (OSError, ValueError, subprocess.CalledProcessError) as error:
                parser.error(str(error))
            total_bytes = size_of(inputs)
            report.append(f"{name}: shards={len(inputs)} input_bytes={total_bytes}")
            dataset_result: dict[str, object] = {
                "name": name,
                "input_bytes": total_bytes,
                "shards": len(inputs),
                "results": {},
            }
            results = dataset_result["results"]
            output = work / f"{name}.lcovmerge.info"
            command = [str(binary), "-o", str(output), *map(str, inputs)]
            row, result = measurement(name, inputs, command, output, work, args.timeout,
                                      "lcovmerge-default", use_time_wrapper, args.hyperfine_runs)
            report.append(row)
            results["lcovmerge"] = result
            if "status=OK" not in row:
                exit_code = 1

            if name == "M":
                for jobs in (1, 2, 4, 8):
                    job_output = work / f"M-j{jobs}.info"
                    job_command = [str(binary), "--jobs", str(jobs), "-o", str(job_output), *map(str, inputs)]
                    row, result = measurement(name, inputs, job_command, job_output, work,
                                              args.timeout, f"lcovmerge-j{jobs}",
                                              use_time_wrapper, args.hyperfine_runs)
                    report.append(row)
                    dataset_result.setdefault("job_scaling", {})[str(jobs)] = result

            if args.skip_lcov:
                report.append(f"{name} ({lcov_label}): skipped by request")
            elif lcov:
                lcov_output = work / f"{name}.lcov.info"
                lcov_command = [lcov, "--branch-coverage"]
                for item in inputs:
                    lcov_command.extend(("-a", str(item)))
                lcov_command.extend(("-o", str(lcov_output)))
                row, result = measurement(name, inputs, lcov_command, lcov_output, work,
                                          args.timeout, lcov_label, use_time_wrapper,
                                          args.hyperfine_runs)
                report.append(row)
                results["lcov"] = result
                if "status=OK" not in row:
                    exit_code = 1
            else:
                report.append(f"{name} ({lcov_label}): unavailable")
                results["lcov"] = {"status": "UNAVAILABLE", "exit_code": None,
                                   "time_s": None, "rss_bytes": None,
                                   "throughput_mb_s": None, "run_count": 0,
                                   "warmup_count": 0}

            if node:
                node_output = work / f"{name}.node.info"
                pattern = str(inputs[0] if name == "PATH-HEAVY" else inputs[0].parent / "*.info")
                row, result = measurement(name, inputs, [node, pattern, str(node_output)],
                                          node_output, work, args.timeout, "lcov-result-merger",
                                          use_time_wrapper, args.hyperfine_runs)
                report.append(row)
                results["lcov-result-merger"] = result
            else:
                report.append(f"{name} (lcov-result-merger): unavailable")
            dataset_results.append(dataset_result)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text("\n".join(report) + "\n", encoding="utf-8")
    if args.json_report:
        args.json_report.parent.mkdir(parents=True, exist_ok=True)
        args.json_report.write_text(json.dumps({
            "schema_version": 1,
            "measurement_date": time.strftime("%Y-%m-%d", time.gmtime()),
            "method": {
                "hyperfine_runs": args.hyperfine_runs,
                "warmup_count": 1 if args.hyperfine_runs else 0,
                "timeout_seconds": args.timeout,
                "rss_source": ("/usr/bin/time -v per measured hyperfine sample"
                               if args.hyperfine_runs and platform.system() == "Linux" else
                               "hyperfine memory_peak_resident" if args.hyperfine_runs else
                               "/usr/bin/time -v" if platform.system() != "Darwin" else
                               "/usr/bin/time -l"),
                "text_report": str(args.report),
            },
            "datasets": dataset_results,
        }, indent=2) + "\n", encoding="utf-8")
    print("temporary benchmark data removed on exit")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
