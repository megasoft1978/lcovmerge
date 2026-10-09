#!/usr/bin/env python3
"""Generate benchmark fixtures outside the repo and measure available mergers."""

from __future__ import annotations

import argparse
import os
import pathlib
import platform
import shutil
import subprocess
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
BENCH_REPO = pathlib.Path(os.environ.get(
    "LCOVMERGE_BENCH_REPO", "/Users/megasoft78/Desktop/Freelance/lcov-merge-bench"))


def timed(command: list[str], log: pathlib.Path, timeout: int = 1800) -> tuple[int, float, str]:
    start = time.perf_counter()
    try:
        if platform.system() == "Darwin":
            wrapped = ["/usr/bin/time", "-l", *command]
        elif shutil.which("/usr/bin/time"):
            wrapped = ["/usr/bin/time", "-v", *command]
        else:
            wrapped = command
        result = subprocess.run(wrapped, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                timeout=timeout, check=False)
        elapsed = time.perf_counter() - start
        payload = result.stdout.decode("utf-8", errors="replace")
        log.write_text(payload, encoding="utf-8")
        return result.returncode, elapsed, payload
    except subprocess.TimeoutExpired as error:
        elapsed = time.perf_counter() - start
        payload = (error.stdout or b"").decode("utf-8", errors="replace") + "\nTIMEOUT\n"
        log.write_text(payload, encoding="utf-8")
        return 124, elapsed, payload


def generate(generator: pathlib.Path, path_heavy: pathlib.Path, out: pathlib.Path) -> dict[str, list[pathlib.Path]]:
    cc = os.environ.get("CC", "cc")
    generated = out / "generated"
    generated.mkdir()
    generator_binary = out / "gen-lcov"
    subprocess.run([cc, "-std=c11", "-O2", "-Wall", "-Wextra", str(generator), "-o", str(generator_binary)], check=True)
    workloads = {
        "S": (8, 64, 23000),
        "M": (32, 256, 23000),
        "XL-single": (1, 1, 30340000),
    }
    inputs: dict[str, list[pathlib.Path]] = {}
    for name, (shards, files, lines) in workloads.items():
        directory = generated / name
        subprocess.run([str(generator_binary), "--out", str(directory), "--shards", str(shards),
                        "--files", str(files), "--lines", str(lines), "--seed", "1", "--checksums"], check=True)
        inputs[name] = sorted(directory.glob("*.info"))
    heavy = generated / "path-heavy.info"
    subprocess.run(["python3", str(path_heavy), "--count", "2000000", "--output", str(heavy)], check=True)
    inputs["PATH-HEAVY"] = [heavy]
    return inputs


def size_of(inputs: list[pathlib.Path]) -> int:
    return sum(path.stat().st_size for path in inputs)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bench-repo", type=pathlib.Path, default=BENCH_REPO)
    parser.add_argument("--workload", choices=("all", "S", "M", "XL-single", "PATH-HEAVY"), default="all")
    parser.add_argument("--timeout", type=int, default=1800)
    args = parser.parse_args()
    generator = args.bench_repo / "tools/gen-lcov.c"
    path_heavy = args.bench_repo / "tools/gen-path-heavy.py"
    if not generator.is_file() or not path_heavy.is_file():
        parser.error(f"benchmark generators not found under {args.bench_repo}; set --bench-repo")
    binary = ROOT / "bin/lcovmerge"
    if not binary.is_file():
        parser.error("build lcovmerge first with make")

    report = [f"host={platform.platform()}", f"bench_repo={args.bench_repo}", f"timeout_seconds={args.timeout}"]
    exit_code = 0
    with tempfile.TemporaryDirectory(prefix="lcovmerge-bench-") as directory:
        work = pathlib.Path(directory)
        inputs = generate(generator, path_heavy, work)
        names = list(inputs) if args.workload == "all" else [args.workload]
        report.append(f"generator={generator}")
        report.append("temporary_data=outside repository; removed automatically")
        for name in names:
            paths = inputs[name]
            output = work / f"{name}.lcovmerge.info"
            command = [str(binary), "-o", str(output), *map(str, paths)]
            status, elapsed, log = timed(command, work / f"{name}.lcovmerge-time.txt", args.timeout)
            total_bytes = size_of(paths)
            throughput = total_bytes / elapsed / 1_000_000 if elapsed else 0.0
            rss_marker = "maximum resident set size" if platform.system() == "Darwin" else "Maximum resident set size"
            rss_line = next((line.strip() for line in log.splitlines() if rss_marker in line), "RSS unavailable")
            row = f"{name}: input_bytes={total_bytes} wall_seconds={elapsed:.3f} throughput_MB_s={throughput:.1f} status={status} {rss_line}"
            print(row)
            report.append(row)
            if status != 0:
                exit_code = status
                break
            if name == "M":
                for jobs in (1, 2, 8):
                    job_output = work / f"M-j{jobs}.info"
                    job_command = [str(binary), "--jobs", str(jobs), "-o", str(job_output), *map(str, paths)]
                    code, duration, job_log = timed(job_command, work / f"M-j{jobs}-time.txt", args.timeout)
                    rss_line = next((line.strip() for line in job_log.splitlines() if rss_marker in line), "RSS unavailable")
                    row = f"M jobs={jobs}: input_bytes={total_bytes} wall_seconds={duration:.3f} throughput_MB_s={total_bytes / duration / 1_000_000:.1f} status={code} {rss_line}"
                    print(row)
                    report.append(row)
                lcov = shutil.which("lcov")
                node = next((candidate for candidate in (
                    shutil.which("lcov-result-merger"),
                    args.bench_repo / "node_modules/.bin/lcov-result-merger") if candidate), None)
                if lcov:
                    lcov_output = work / "M.lcov.info"
                    lcov_command = [lcov, "--branch-coverage"]
                    for item in paths:
                        lcov_command.extend(("-a", str(item)))
                    lcov_command.extend(("-o", str(lcov_output)))
                    code, duration, log = timed(lcov_command, work / "M.lcov-time.txt", args.timeout)
                    rss_line = next((line.strip() for line in log.splitlines() if rss_marker in line), "RSS unavailable")
                    row = f"M vs lcov: input_bytes={total_bytes} wall_seconds={duration:.3f} throughput_MB_s={total_bytes / duration / 1_000_000:.1f} status={code} {rss_line}"
                    print(row)
                    report.append(row)
                else:
                    report.append("M vs lcov: unavailable")
                if node and pathlib.Path(node).exists():
                    node_output = work / "M.node.info"
                    pattern = str(inputs["M"][0].parent / "*.info")
                    code, duration, log = timed([str(node), pattern, str(node_output)],
                                                work / "M.node-time.txt", args.timeout)
                    rss_line = next((line.strip() for line in log.splitlines() if rss_marker in line), "RSS unavailable")
                    row = f"M vs lcov-result-merger: input_bytes={total_bytes} wall_seconds={duration:.3f} throughput_MB_s={total_bytes / duration / 1_000_000:.1f} status={code} {rss_line}"
                    print(row)
                    report.append(row)
                else:
                    report.append("M vs lcov-result-merger: unavailable")
        print("temporary benchmark data removed on exit")
    validation = ROOT / "docs/validation"
    validation.mkdir(parents=True, exist_ok=True)
    (validation / "benchmark.txt").write_text("\n".join(report) + "\n", encoding="utf-8")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
