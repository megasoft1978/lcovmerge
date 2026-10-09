#!/usr/bin/env python3
"""Generate benchmark fixtures outside the repo and measure LCOV mergers."""

from __future__ import annotations

import argparse
import os
import pathlib
import platform
import signal
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


def timed(command: list[str], log: pathlib.Path, timeout: int) -> tuple[int, float, str]:
    start = time.perf_counter()
    if platform.system() == "Darwin":
        wrapped = ["/usr/bin/time", "-l", *command]
    elif pathlib.Path("/usr/bin/time").is_file():
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
    return next((line.strip() for line in payload.splitlines() if marker in line), "RSS unavailable")


def measurement(name: str, inputs: list[pathlib.Path], command: list[str], output: pathlib.Path,
                work: pathlib.Path, timeout: int, tool_label: str) -> str:
    status, elapsed, payload = timed(command, work / f"{name}-{tool_label}-time.txt", timeout)
    input_bytes = size_of(inputs)
    result_status = "OK" if status == 0 else ("TIMEOUT" if status == 124 else f"ERROR_{status}")
    throughput = f"{input_bytes / elapsed / 1_000_000:.1f} MB/s" if status == 0 and elapsed else "n/a"
    row = (
        f"{name} ({tool_label}): input_bytes={input_bytes} wall_seconds={elapsed:.3f} "
        f"throughput={throughput} status={result_status} {rss_line(payload)}"
    )
    print(row, flush=True)
    return row


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workload", choices=("all", *WORKLOAD_NAMES), default="all")
    parser.add_argument("--real-dir", type=pathlib.Path)
    parser.add_argument("--node-bin", type=pathlib.Path)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--report", type=pathlib.Path, default=ROOT / "docs/validation/benchmark.txt")
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    if args.workload == "all" and args.real_dir is None:
        parser.error("--real-dir is required when --workload all or REAL")
    if args.workload == "REAL" and args.real_dir is None:
        parser.error("--real-dir is required for REAL")

    binary = ROOT / "bin/lcovmerge"
    if not binary.is_file():
        parser.error("build lcovmerge first with make")
    node_candidate = args.node_bin or (pathlib.Path(shutil.which("lcov-result-merger"))
                                       if shutil.which("lcov-result-merger") else None)
    node = str(node_candidate) if node_candidate and node_candidate.is_file() else None
    lcov = shutil.which("lcov")
    names = WORKLOAD_NAMES if args.workload == "all" else (args.workload,)
    report = [
        f"host={platform.platform()}",
        f"timeout_seconds={args.timeout}",
        "fixture_generator=tools/gen-lcov.py --benchmark-compatible",
        "temporary_data=outside repository; generated inputs removed automatically",
        "measurement_date=" + time.strftime("%Y-%m-%d", time.gmtime()),
    ]
    exit_code = 0
    with tempfile.TemporaryDirectory(prefix="lcovmerge-bench-") as directory:
        work = pathlib.Path(directory)
        for name in names:
            try:
                inputs = generate(name, work, args.real_dir)
            except (OSError, ValueError, subprocess.CalledProcessError) as error:
                parser.error(str(error))
            total_bytes = size_of(inputs)
            report.append(f"{name}: shards={len(inputs)} input_bytes={total_bytes}")
            output = work / f"{name}.lcovmerge.info"
            command = [str(binary), "-o", str(output), *map(str, inputs)]
            row = measurement(name, inputs, command, output, work, args.timeout, "lcovmerge-default")
            report.append(row)
            if "status=OK" not in row:
                exit_code = 1

            if name == "M":
                for jobs in (1, 2, 4, 8):
                    job_output = work / f"M-j{jobs}.info"
                    job_command = [str(binary), "--jobs", str(jobs), "-o", str(job_output), *map(str, inputs)]
                    report.append(measurement(name, inputs, job_command, job_output, work, args.timeout,
                                              f"lcovmerge-j{jobs}"))

            if lcov:
                lcov_output = work / f"{name}.lcov.info"
                lcov_command = [lcov, "--branch-coverage"]
                for item in inputs:
                    lcov_command.extend(("-a", str(item)))
                lcov_command.extend(("-o", str(lcov_output)))
                report.append(measurement(name, inputs, lcov_command, lcov_output, work, args.timeout, "lcov-2.6"))
            else:
                report.append(f"{name} (lcov-2.6): unavailable")

            if node:
                node_output = work / f"{name}.node.info"
                pattern = str(inputs[0] if name == "PATH-HEAVY" else inputs[0].parent / "*.info")
                report.append(measurement(name, inputs, [node, pattern, str(node_output)], node_output,
                                          work, args.timeout, "lcov-result-merger"))
            else:
                report.append(f"{name} (lcov-result-merger): unavailable")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text("\n".join(report) + "\n", encoding="utf-8")
    print("temporary benchmark data removed on exit")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
