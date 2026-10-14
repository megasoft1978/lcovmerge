#!/usr/bin/env python3
"""Measure paired, output-checked timings for two native lcovmerge builds."""

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import random
import shutil
import statistics
import subprocess
import sys
import threading
import time

try:
    import resource
except ImportError:  # Windows
    resource = None

RESAMPLES = 10_000
CONFIDENCE = 0.95


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def tree_size(path):
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def percentile(values, fraction):
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    low = math.floor(position)
    high = math.ceil(position)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def bootstrap(ratios, seed):
    rng = random.Random(seed)
    logs = [math.log(value) for value in ratios]
    means = [statistics.mean(rng.choices(logs, k=len(logs))) for _ in range(RESAMPLES)]
    alpha = (1 - CONFIDENCE) / 2
    return [math.exp(percentile(means, alpha)), math.exp(percentile(means, 1 - alpha))]


def child_run(command, stdout_path, stderr_path, timeout, temp_path):
    peak_disk = [tree_size(temp_path)]
    stop = threading.Event()

    def sample_disk():
        while not stop.wait(0.02):
            try:
                peak_disk[0] = max(peak_disk[0], tree_size(temp_path))
            except OSError:
                pass

    sampler = threading.Thread(target=sample_disk, daemon=True)
    sampler.start()
    start = time.perf_counter()
    try:
        with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
            proc = subprocess.Popen(command, stdout=stdout, stderr=stderr)
            if hasattr(os, "wait4"):
                timed_out = threading.Event()

                def expire():
                    timed_out.set()
                    try:
                        proc.kill()
                    except ProcessLookupError:
                        pass

                watchdog = threading.Timer(timeout, expire)
                watchdog.daemon = True
                watchdog.start()
                try:
                    _, status, usage = os.wait4(proc.pid, 0)
                    returncode = os.waitstatus_to_exitcode(status)
                    proc.returncode = returncode
                finally:
                    watchdog.cancel()
                if timed_out.is_set():
                    raise TimeoutError(f"timed out after {timeout:g}s")
            else:
                try:
                    returncode = proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                    raise TimeoutError(f"timed out after {timeout:g}s")
    finally:
        elapsed = time.perf_counter() - start
        stop.set()
        sampler.join()
    cpu = None
    rss = None
    if resource and "usage" in locals():
        cpu = usage.ru_utime + usage.ru_stime
        scale = 1 if sys.platform == "darwin" else 1024
        rss = int(usage.ru_maxrss * scale)
    return {"elapsed_seconds": elapsed, "cpu_seconds": cpu,
            "peak_rss_bytes": rss, "peak_temp_disk_bytes": peak_disk[0],
            "returncode": returncode}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--workdir", type=Path, default=Path(".cache/build-comparison"))
    parser.add_argument("--runs", type=int, default=19)
    parser.add_argument("--seed", type=int, default=20261014)
    parser.add_argument("--jobs", type=int, nargs="+", default=[1, 4])
    parser.add_argument("--mem-limit", default="64M")
    parser.add_argument("--timeout", type=float, default=600)
    parser.add_argument("--source", type=Path, action="append", default=[])
    parser.add_argument("--profile", type=Path, action="append", default=[])
    parser.add_argument("inputs", type=Path, nargs="+")
    args = parser.parse_args()
    if args.runs < 3 or args.timeout <= 0 or any(j < 1 for j in args.jobs):
        parser.error("runs must be at least 3, timeout and jobs must be positive")
    workdir = args.workdir.resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    binaries = {"baseline": args.baseline.resolve(), "candidate": args.candidate.resolve()}
    for name, path in binaries.items():
        if not path.is_file():
            parser.error(f"{name} binary does not exist: {path}")
    inputs = [p.resolve() for p in args.inputs]
    for path in inputs:
        if not path.is_file():
            parser.error(f"input does not exist: {path}")
    rng = random.Random(args.seed)
    samples = {}
    output_hash = None
    failures = []
    variants = [(name, jobs) for jobs in args.jobs for name in binaries]

    def run(name, jobs, iteration, warmup=False):
        nonlocal output_hash
        label = "warmup" if warmup else f"run-{iteration:02d}"
        run_dir = workdir / f"{label}-{name}-jobs{jobs}"
        if run_dir.exists():
            shutil.rmtree(run_dir)
        run_dir.mkdir()
        tmpdir = run_dir / "tmp"
        tmpdir.mkdir()
        out = run_dir / "output.info"
        command = [str(binaries[name]), "--jobs", str(jobs), "--mem-limit", args.mem_limit,
                   "--tmpdir", str(tmpdir), "-o", str(out), *map(str, inputs)]
        record = {"variant": name, "jobs": jobs, "iteration": iteration,
                  "warmup": warmup, "binary": str(binaries[name]), "command": command,
                  "stdout_path": str(run_dir / "stdout.log"),
                  "stderr_path": str(run_dir / "stderr.log")}
        try:
            measured = child_run(command, run_dir / "stdout.log", run_dir / "stderr.log",
                                 args.timeout, tmpdir)
            record.update(measured)
            if measured["returncode"] != 0:
                raise RuntimeError(f"exit status {measured['returncode']}")
            if not out.is_file():
                raise RuntimeError("CLI did not create output")
            actual_hash = digest(out)
            record["output_sha256"] = actual_hash
            if output_hash is None:
                output_hash = actual_hash
            elif actual_hash != output_hash:
                raise RuntimeError(f"output hash mismatch: expected {output_hash}, got {actual_hash}")
            if any(tmpdir.iterdir()):
                raise RuntimeError("CLI left temporary files behind")
            if not warmup:
                samples.setdefault((jobs, iteration, name), {}).update(record)
            else:
                (workdir / "warmups.jsonl").open("a").write(json.dumps(record) + "\n")
            shutil.rmtree(run_dir)
        except Exception as exc:
            record["error"] = str(exc)
            failures.append(record)
            (run_dir / "failure.json").write_text(json.dumps(record, indent=2) + "\n")

    for jobs in args.jobs:
        order = variants.copy()
        rng.shuffle(order)
        for name, variant_jobs in order:
            run(name, variant_jobs, 0, warmup=True)
    for iteration in range(args.runs):
        order = variants.copy()
        rng.shuffle(order)
        for name, jobs in order:
            run(name, jobs, iteration)
    results = []
    for jobs in args.jobs:
        pairs = [(samples.get((jobs, i, "baseline")), samples.get((jobs, i, "candidate")))
                 for i in range(args.runs)]
        if any(a is None or b is None for a, b in pairs):
            continue
        ratios = [b["elapsed_seconds"] / a["elapsed_seconds"] for a, b in pairs]
        interval = bootstrap(ratios, args.seed + jobs)
        median_ratio = statistics.median(ratios)
        status = "regression" if interval[0] > 1 else (
            "observed-pass" if interval[1] < 1 else "inconclusive")
        row = {"jobs": jobs, "paired_ratios_candidate_over_baseline": ratios,
               "median_paired_ratio": median_ratio, "median_log_ratio": statistics.median(map(math.log, ratios)),
               "bootstrap_95_percent_interval": interval, "status": status,
               "diagnostic_noise_band_3_percent": median_ratio <= 1.03}
        for key in ("elapsed_seconds", "cpu_seconds", "peak_rss_bytes", "peak_temp_disk_bytes"):
            row[f"baseline_median_{key}"] = statistics.median([a[key] for a, _ in pairs])
            row[f"candidate_median_{key}"] = statistics.median([b[key] for _, b in pairs])
        results.append(row)
    try:
        rustc = subprocess.run(["rustc", "--version"], capture_output=True, text=True,
                               timeout=5, check=False).stdout.strip() or None
    except (OSError, subprocess.TimeoutExpired):
        rustc = None
    report = {
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "method": {"warmups_per_variant": 1, "seed": args.seed, "runs": args.runs,
                   "interleaved_paired": True, "bootstrap_resamples": RESAMPLES,
                   "interval": "percentile interval of paired log-ratios",
                   "warm_cache_caveat": "warm-up and repeated runs use warm OS caches",
                   "uncertainty": "conditional on these measured binaries, inputs, host, and layout; not a general performance proof",
                   "noise_band": "3% is diagnostic only and never changes acceptance"},
        "host": {"platform": platform.platform(), "python": sys.version,
                 "machine": platform.machine(), "rustc": rustc,
                 "resource_metrics": "per-child CPU and peak RSS where resource module is available; unavailable values are null",
                 "temp_disk_metric": "sampled peak every 20ms; sampling can miss brief peaks"},
        "parameters": {"jobs": args.jobs, "mem_limit": args.mem_limit,
                       "timeout_seconds": args.timeout},
        "binaries": {name: {"path": str(path), "sha256": digest(path)}
                     for name, path in binaries.items()},
        "inputs": [{"path": str(p), "sha256": digest(p), "size_bytes": p.stat().st_size} for p in inputs],
        "source_hashes": [{"path": str(p.resolve()), "sha256": digest(p.resolve())} for p in args.source],
        "profile_hashes": [{"path": str(p.resolve()), "sha256": digest(p.resolve())} for p in args.profile],
        "output_sha256": output_hash, "results": results, "failures": failures,
        "acceptance": "candidate/baseline <= 1; CI entirely above 1 is regression, entirely below 1 observed-pass, otherwise inconclusive",
    }
    report_path = workdir / "report.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"report": str(report_path), "results": results,
                      "failures": len(failures)}, indent=2))
    return 1 if failures or len(results) != len(args.jobs) or any(r["status"] != "observed-pass" for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
