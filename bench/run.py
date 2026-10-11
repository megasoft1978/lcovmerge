#!/usr/bin/env python3
"""Generate benchmark fixtures outside the repo and measure LCOV mergers."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import pathlib
import platform
import re
import signal
import shlex
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKLOADS = {
    "S": (8, 56, 23000),
    "M": (32, 220, 16000),
    "L": (64, 480, 16000),
    "XXL": (128, 480, 32000),
    "XL-single": (1, 1, 30340000),
    "XL-pair": (2, 1, 15170000),
}
WORKLOAD_NAMES = ("S", "M", "L", "XXL", "XL-single", "XL-pair", "PATH-HEAVY", "REAL")
L_INPUT_BYTES = 4853340843
XXL_DISK_FACTOR = 2.5
XXL_PRECHECK_MARGIN = 1.10
XXL_COMPARISON_METHOD = "per-SF streaming comparison of normalized SF/DA/FN/FNDA/BRDA keys and counts"


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
        payload += "\nbenchmark_samples=" + json.dumps({
            "times_s": [float(item["time_wall_clock"]["value"]) for item in measurements],
            "exit_codes": statuses,
            "rss_bytes": [int(value) for value in resident_values],
        }, separators=(",", ":")) + "\n"
        payload += f"\nhyperfine_runs={len(measurements)} elapsed_s={wall_seconds:.6f}\n"
    except (OSError, KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
        status = process.returncode or 1
        wall_seconds = elapsed
        payload += "\nhyperfine result could not be parsed\n"
    log.write_text(payload, encoding="utf-8")
    return status, wall_seconds, payload


def xxl_parameters(scale: float) -> tuple[int, int, int]:
    shards, files, lines = WORKLOADS["XXL"]
    return shards, max(1, round(files * scale)), max(1, round(lines * scale))


def estimate_xxl_bytes(scale: float) -> int:
    _shards, files, lines = xxl_parameters(scale)
    # L is the measured 64-shard, 480-file, 16,000-line reference. Keep a
    # 10% margin because generated shard file-selection percentages vary.
    estimated = L_INPUT_BYTES * 2 * (files / 480) * (lines / 16000)
    return math.ceil(estimated * XXL_PRECHECK_MARGIN)


def generate(name: str, out: pathlib.Path, real_dir: pathlib.Path | None,
             scale: float = 1.0) -> list[pathlib.Path]:
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
    shards, files, lines = (xxl_parameters(scale) if name == "XXL"
                            else WORKLOADS[name])
    directory = generated / name
    command = [
        sys.executable,
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
    ]
    if name == "XXL":
        command.append("--lcov-valid")
    subprocess.run(command, check=True)
    return sorted(directory.glob("*.info"))


def size_of(inputs: list[pathlib.Path]) -> int:
    return sum(path.stat().st_size for path in inputs)


def rss_line(payload: str) -> str:
    marker = "maximum resident set size" if platform.system() == "Darwin" else "Maximum resident set size"
    return next((line.strip() for line in payload.splitlines() if marker.lower() in line.lower()),
                "RSS unavailable")


def rss_bytes(payload: str) -> int | None:
    match = re.search(r"Maximum resident set size:\s*(\d+) bytes", payload)
    if match:
        return int(match.group(1))
    match = re.search(r"Maximum resident set size \(kbytes\):\s*(\d+)", payload)
    if match:
        return int(match.group(1)) * 1024
    match = re.search(r"maximum resident set size\s*:?\s*(\d+)", payload, re.IGNORECASE)
    if match:
        return int(match.group(1))
    match = re.search(r"^\s*(\d+)\s+maximum resident set size\s*$", payload,
                      re.IGNORECASE | re.MULTILINE)
    if match:
        return int(match.group(1))
    return None


def repeat_counts(results: dict[str, dict[str, object]]) -> dict[str, int]:
    return {name: int(result.get("run_count", 0)) for name, result in results.items()}


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def input_manifest(inputs: list[pathlib.Path]) -> list[dict[str, object]]:
    return [{"index": index, "size_bytes": path.stat().st_size, "sha256": sha256_file(path)}
            for index, path in enumerate(inputs)]


def measurement(name: str, inputs: list[pathlib.Path], command: list[str], output: pathlib.Path,
                work: pathlib.Path, timeout: int, tool_label: str, use_time_wrapper: bool,
                hyperfine_runs: int | None) -> tuple[str, dict[str, object]]:
    log = work / f"{name}-{tool_label}-time.txt"
    if hyperfine_runs:
        status, elapsed, payload = hyperfine_timed(command, log, timeout, hyperfine_runs)
    else:
        status, elapsed, payload = timed(command, log, timeout, use_time_wrapper)
    input_bytes = size_of(inputs)
    samples_match = re.search(r"^benchmark_samples=(\{.*\})$", payload, re.MULTILINE)
    samples = json.loads(samples_match.group(1)) if samples_match else (
        {"times_s": [], "exit_codes": [], "rss_bytes": []} if hyperfine_runs else
        {"times_s": [elapsed], "exit_codes": [status], "rss_bytes": [rss_bytes(payload)]}
    )
    sample_statuses = samples.get("exit_codes", [])
    result_status = "OK" if status == 0 else ("TIMEOUT" if status == 124 else f"ERROR_{status}")
    throughput = f"{input_bytes / elapsed / 1_000_000:.1f} MB/s" if status == 0 and elapsed else "n/a"
    row = (
        f"{name} ({tool_label}): input_bytes={input_bytes} wall_seconds={elapsed:.3f} "
        f"throughput={throughput} status={result_status} {rss_line(payload)}"
    )
    print(row, flush=True)
    return row, {
        "time_s": round(elapsed, 6),
        "rss_bytes": rss_bytes(payload),
        "throughput_mb_s": (round(input_bytes / elapsed / 1_000_000, 1)
                            if status == 0 and elapsed else None),
        "status": result_status,
        "exit_code": status,
        "run_count": len(sample_statuses),
        "successful_run_count": sum(code == 0 for code in sample_statuses),
        "run_samples_s": samples.get("times_s", []),
        "sample_exit_codes": sample_statuses,
        "rss_samples_bytes": samples.get("rss_bytes", []),
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
    parser.add_argument("--skip-node", action="store_true",
                        help="omit the optional lcov-result-merger comparison")
    parser.add_argument("--hyperfine-runs", type=int,
                        help="use one hyperfine warmup and this many measured runs per command; XXL uses one lcov/node run")
    parser.add_argument("--scale", type=float, default=1.0,
                        help="scale XXL files and lines for a small validation run (0 < scale <= 1)")
    parser.add_argument("--timeout", type=int,
                        help="per-command timeout in seconds (default: 7,200 for XXL, 1,800 otherwise)")
    parser.add_argument("--report", type=pathlib.Path, default=ROOT / "docs/validation/benchmark.txt")
    parser.add_argument("--json-report", type=pathlib.Path,
                        help="write structured measurement results to this JSON file")
    args = parser.parse_args()
    names = WORKLOAD_NAMES if args.workload == "all" else (args.workload,)
    args.timeout = args.timeout if args.timeout is not None else (7200 if "XXL" in names else 1800)
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    if args.hyperfine_runs is not None and args.hyperfine_runs <= 0:
        parser.error("--hyperfine-runs must be positive")
    if not 0 < args.scale <= 1:
        parser.error("--scale must be greater than 0 and at most 1")
    if args.scale != 1 and "XXL" not in names:
        parser.error("--scale is supported only for the XXL workload")
    if "XXL" in names and args.hyperfine_runs is not None and args.hyperfine_runs < 3:
        parser.error("XXL requires at least 3 hyperfine runs for lcovmerge")
    if "XXL" in names and args.skip_lcov:
        parser.error("XXL requires lcov for the semantic comparison")
    if args.workload == "all" and args.real_dir is None:
        parser.error("--real-dir is required when --workload all or REAL")
    if args.workload == "REAL" and args.real_dir is None:
        parser.error("--real-dir is required for REAL")

    use_time_wrapper = (time_wrapper_available()
                        if not args.hyperfine_runs and "XXL" not in names else False)

    binary = args.binary.resolve()
    if not binary.is_file():
        parser.error("build lcovmerge first with make")
    node_candidate = (args.node_bin or (pathlib.Path(shutil.which("lcov-result-merger"))
                                       if shutil.which("lcov-result-merger") else None))
    if args.skip_node:
        node_candidate = None
    node = str(node_candidate) if node_candidate and node_candidate.is_file() else None
    lcov = shutil.which("lcov")
    if "XXL" in names:
        if not shutil.which("hyperfine"):
            parser.error("XXL requires hyperfine")
        if not lcov:
            parser.error("XXL requires lcov to compare merged output semantically")
    lcov_label = "lcov"
    xxl_lcovmerge_runs = args.hyperfine_runs or 3
    use_hyperfine = bool(args.hyperfine_runs) or "XXL" in names
    report = [
        f"host={platform.platform()}",
        f"timeout_seconds={args.timeout}",
        "fixture_generator=tools/gen-lcov.py --benchmark-compatible"
        + (" --lcov-valid for XXL" if "XXL" in names else ""),
        "temporary_data=under TMPDIR; generated inputs removed automatically",
        (f"measurement_source=hyperfine; XXL uses {xxl_lcovmerge_runs} lcovmerge runs, one lcov run, and one optional Node run after one warmup" if "XXL" in names else
         f"measurement_source=hyperfine {args.hyperfine_runs} runs after one warmup; wall time is median; RSS is max measured sample" if args.hyperfine_runs else
         "measurement_source=single run"),
        ("rss_source=/usr/bin/time -v per measured hyperfine sample" if use_hyperfine and platform.system() == "Linux" else
         "rss_source=hyperfine memory_peak_resident" if use_hyperfine else
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
            disk_free_before: int | None = None
            disk_required: int | None = None
            parameters: tuple[int, int, int] | None = None
            if name == "XXL":
                parameters = xxl_parameters(args.scale)
                estimated_bytes = estimate_xxl_bytes(args.scale)
                disk_required = math.ceil(estimated_bytes * XXL_DISK_FACTOR)
                disk_free_before = shutil.disk_usage(work).free
                if disk_free_before < disk_required:
                    parser.error(
                        "XXL disk guard refused run: "
                        f"free={disk_free_before} bytes, estimated_input={estimated_bytes} bytes, "
                        f"required_free={disk_required} bytes (2.5x estimated input)"
                    )
                report.append(
                    f"XXL disk_precheck=PASS free_bytes={disk_free_before} "
                    f"estimated_input_bytes={estimated_bytes} required_free_bytes={disk_required}"
                )
            try:
                inputs = generate(name, work, args.real_dir, args.scale)
            except (OSError, ValueError, subprocess.CalledProcessError) as error:
                parser.error(str(error))
            total_bytes = size_of(inputs)
            if name == "XXL":
                assert disk_free_before is not None
                free_after_generation = shutil.disk_usage(work).free
                available_for_run = min(disk_free_before, free_after_generation + total_bytes)
                exact_required = math.ceil(total_bytes * XXL_DISK_FACTOR)
                if available_for_run < exact_required:
                    parser.error(
                        "XXL disk guard refused run after generation: "
                        f"available_free={available_for_run} bytes, input={total_bytes} bytes, "
                        f"required_free={exact_required} bytes (2.5x input)"
                    )
                report.append(
                    f"XXL disk_guard=PASS available_free_bytes={available_for_run} "
                    f"input_bytes={total_bytes} required_free_bytes={exact_required}"
                )
            report.append(f"{name}: shards={len(inputs)} input_bytes={total_bytes}")
            dataset_result: dict[str, object] = {
                "name": name,
                "input_bytes": total_bytes,
                "shards": len(inputs),
                "results": {},
            }
            if args.json_report:
                dataset_result["input_manifest"] = input_manifest(inputs)
            if parameters:
                dataset_result["files"] = parameters[1]
                dataset_result["lines"] = parameters[2]
                dataset_result["scale"] = args.scale
                dataset_result["disk_guard"] = {
                    "free_before_bytes": disk_free_before,
                    "available_free_bytes": available_for_run,
                    "required_free_bytes": math.ceil(total_bytes * XXL_DISK_FACTOR),
                }
            results = dataset_result["results"]
            output = work / f"{name}.lcovmerge.info"
            command = [str(binary), "-o", str(output), *map(str, inputs)]
            lcovmerge_runs = xxl_lcovmerge_runs if name == "XXL" else args.hyperfine_runs
            row, result = measurement(name, inputs, command, output, work, args.timeout,
                                      "lcovmerge-default", use_time_wrapper, lcovmerge_runs)
            report.append(row)
            results["lcovmerge"] = result
            if "status=OK" not in row:
                exit_code = 1
            if name == "XXL" and result["rss_bytes"] is None:
                result["rss_status"] = "UNAVAILABLE"
                if result["status"] == "OK":
                    result["status"] = "ERROR_RSS_UNAVAILABLE"
                    result["exit_code"] = 1
                    report.append("XXL (lcovmerge-default): ERROR_RSS_UNAVAILABLE")
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
                                          1 if name == "XXL" else args.hyperfine_runs)
                report.append(row)
                results["lcov"] = result
                if "status=OK" not in row:
                    exit_code = 1
                if name == "XXL" and result["rss_bytes"] is None:
                    result["rss_status"] = "UNAVAILABLE"
                if name == "XXL" and result["status"] == "OK" and result["rss_bytes"] is None:
                    result["status"] = "ERROR_RSS_UNAVAILABLE"
                    result["exit_code"] = 1
                    report.append("XXL (lcov): ERROR_RSS_UNAVAILABLE")
                    exit_code = 1
            else:
                report.append(f"{name} ({lcov_label}): unavailable")
                results["lcov"] = {"status": "UNAVAILABLE", "exit_code": None,
                                   "time_s": None, "rss_bytes": None,
                                   "throughput_mb_s": None, "run_count": 0,
                                   "warmup_count": 0}

            if args.skip_node:
                report.append(f"{name} (lcov-result-merger): skipped by request")
                results["lcov-result-merger"] = {
                    "status": "SKIPPED", "exit_code": None, "time_s": None,
                    "rss_bytes": None, "throughput_mb_s": None,
                    "run_count": 0, "warmup_count": 0,
                }
            elif node:
                node_output = work / f"{name}.node.info"
                pattern = str(inputs[0] if name == "PATH-HEAVY" else inputs[0].parent / "*.info")
                row, result = measurement(name, inputs, [node, pattern, str(node_output)],
                                          node_output, work, args.timeout, "lcov-result-merger",
                                          use_time_wrapper,
                                          1 if name == "XXL" else args.hyperfine_runs)
                report.append(row)
                results["lcov-result-merger"] = result
                if name == "XXL" and result["rss_bytes"] is None:
                    result["rss_status"] = "UNAVAILABLE"
                if name == "XXL" and result["status"] == "OK" and result["rss_bytes"] is None:
                    result["status"] = "ERROR_RSS_UNAVAILABLE"
                    result["exit_code"] = 1
                    report.append("XXL (lcov-result-merger): ERROR_RSS_UNAVAILABLE")
                    exit_code = 1
            else:
                report.append(f"{name} (lcov-result-merger): unavailable")
                results["lcov-result-merger"] = {
                    "status": "UNAVAILABLE", "exit_code": None, "time_s": None,
                    "rss_bytes": None, "throughput_mb_s": None,
                    "run_count": 0, "warmup_count": 0,
                }

            if name == "XXL":
                lcov_result = results.get("lcov", {})
                lcovmerge_result = results.get("lcovmerge", {})
                comparison: dict[str, object] = {
                    "method": XXL_COMPARISON_METHOD,
                    "status": "NOT_RUN",
                }
                if lcov_result.get("status") == "OK" and lcovmerge_result.get("status") == "OK":
                    try:
                        comparator = subprocess.run(
                            [sys.executable, "-I", str(ROOT / "bench/compare_lcov.py"),
                             str(output), str(work / f"{name}.lcov.info")],
                            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            check=False, timeout=args.timeout,
                        )
                        detail = comparator.stdout.strip()
                        comparison.update({
                            "status": "PASS" if comparator.returncode == 0 else "FAIL",
                            "exit_code": comparator.returncode,
                            "details": detail,
                        })
                    except subprocess.TimeoutExpired:
                        detail = f"TIMEOUT after {args.timeout} seconds"
                        comparison.update({"status": "TIMEOUT", "exit_code": 124,
                                           "details": detail})
                    report.append(f"{name} (lcovmerge vs lcov semantic comparison): {detail}")
                    if comparison["status"] != "PASS":
                        exit_code = 1
                else:
                    report.append(f"{name} (lcovmerge vs lcov semantic comparison): NOT_RUN; tool status not OK")
                    comparison["details"] = "comparison requires successful lcovmerge and lcov outputs"
                    exit_code = 1
                dataset_result["comparison"] = {"lcovmerge_vs_lcov": comparison}

            repeat_counts_by_tool = {
                tool: int(result.get("successful_run_count", 0))
                for tool, result in results.items()
            }
            attempt_counts_by_tool = repeat_counts(results)
            dataset_result["measurement"] = {
                "hyperfine_runs": lcovmerge_runs,
                "repeat_counts": repeat_counts_by_tool,
                "attempt_counts": attempt_counts_by_tool,
                "warmup_count": 1 if use_hyperfine else 0,
                "timeout_seconds": args.timeout,
                "rss_source": ("/usr/bin/time -v per measured hyperfine sample"
                               if use_hyperfine and platform.system() == "Linux" else
                               "hyperfine memory_peak_resident" if use_hyperfine else
                               "/usr/bin/time -l" if platform.system() == "Darwin" else
                               "/usr/bin/time -v"),
            }
            dataset_results.append(dataset_result)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text("\n".join(report) + "\n", encoding="utf-8")
    if args.json_report:
        def command_version(command: list[str]) -> str:
            try:
                process = subprocess.run(command, text=True, stdout=subprocess.PIPE,
                                         stderr=subprocess.STDOUT, check=False, timeout=10)
                return process.stdout.strip().splitlines()[0]
            except (OSError, subprocess.TimeoutExpired, IndexError):
                return "unknown"

        if hasattr(os, "sysconf"):
            try:
                memory_bytes = int(os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE"))
            except (OSError, ValueError):
                memory_bytes = 0
        else:
            memory_bytes = 0
        compiler = command_version([*shlex.split(os.environ.get("CC", "cc")), "--version"])
        lcov_version = command_version([lcov, "--version"]) if lcov else "not installed"
        revision = command_version(["git", "-C", str(ROOT), "rev-parse", "HEAD"])
        worktree = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain"],
                                  text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  check=False).stdout
        tracked_diff = subprocess.run(["git", "-C", str(ROOT), "diff", "--binary", "HEAD"],
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                      check=False).stdout
        method = {
            "runner": "bench/run.py",
            "timeout_seconds": args.timeout,
            "repeat_counts": {
                item["name"]: item.get("measurement", {}).get("repeat_counts", {})
                for item in dataset_results
            },
            "warmup_count": 1 if use_hyperfine else 0,
            "wall_time_summary": "median of measured hyperfine runs" if use_hyperfine else "single run",
            "rss_source": ("/usr/bin/time -v per measured hyperfine sample"
                           if use_hyperfine and platform.system() == "Linux" else
                           "hyperfine memory_peak_resident" if use_hyperfine else
                           "/usr/bin/time -l" if platform.system() == "Darwin" else
                           "/usr/bin/time -v" if use_time_wrapper else "unavailable"),
            "cache_state": "no explicit cache flush; one hyperfine warmup where used; machine load uncontrolled",
            "comparison_method": XXL_COMPARISON_METHOD if "XXL" in names else None,
            "text_report": str(args.report),
        }
        host = {
            "runner_label": f"local-{platform.system().lower()}",
            "os": platform.platform(),
            "kernel": platform.release(),
            "cpu_model": platform.processor() or "unknown",
            "logical_cores": os.cpu_count() or 1,
            "memory_bytes": memory_bytes,
            "compiler": compiler,
            "lcov": lcov_version,
            "lcov_result_merger": command_version([node, "--version"]) if node else "not installed",
        }
        binary_identity = {
            "path": str(binary),
            "sha256": sha256_file(binary),
            "size_bytes": binary.stat().st_size,
            "version": command_version([str(binary), "--version"]),
            "source_revision": revision,
            "source_worktree_clean": not bool(worktree.strip()),
            "build_command": os.environ.get("LCOVMERGE_BUILD_COMMAND", "not recorded"),
        }
        build_environment = {
            name: os.environ.get(name, "project default")
            for name in ("CC", "CFLAGS", "CPPFLAGS", "LDFLAGS")
        }
        args.json_report.parent.mkdir(parents=True, exist_ok=True)
        args.json_report.write_text(json.dumps({
            "schema_version": 1,
            "measurement_date": time.strftime("%Y-%m-%d", time.gmtime()),
            "host": host,
            "source": {"repository": "local/lcovmerge", "revision": revision,
                       "worktree_clean": not bool(worktree.strip())},
            "binary": binary_identity,
            "build_environment": build_environment,
            "tracked_diff_sha256": hashlib.sha256(tracked_diff).hexdigest(),
            "method": method,
            "tool_labels": {
                "lcovmerge": command_version([str(binary), "--version"]),
                "lcov": lcov_version,
            },
            "datasets": dataset_results,
        }, indent=2) + "\n", encoding="utf-8")
    print("temporary benchmark data removed on exit")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
