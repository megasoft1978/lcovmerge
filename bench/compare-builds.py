#!/usr/bin/env python3
"""Compare two builds on identical inputs; reject output or median-time regressions."""

import argparse
import hashlib
import json
from pathlib import Path
import random
import statistics
import subprocess
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--jobs", type=int, nargs="+", default=[1, 4])
    parser.add_argument("--runs", type=int, default=19)
    parser.add_argument("--max-ratio", type=float, default=1.0)
    parser.add_argument("--workdir", type=Path, default=Path(".cache/build-comparison"))
    parser.add_argument("inputs", type=Path, nargs="+")
    args = parser.parse_args()
    if args.runs < 3 or any(j < 1 for j in args.jobs) or args.max_ratio <= 0:
        parser.error("require at least three runs, positive jobs and a positive ratio")
    args.workdir.mkdir(parents=True, exist_ok=True)
    binaries = {"baseline": args.baseline.resolve(), "candidate": args.candidate.resolve()}
    variants = [(name, jobs) for name in binaries for jobs in args.jobs]
    samples = {variant: [] for variant in variants}
    expected = None
    output = args.workdir / "output.info"
    tmpdir = args.workdir / "tmp"
    tmpdir.mkdir(exist_ok=True)
    if any(tmpdir.iterdir()):
        parser.error("comparison temporary directory must be empty")
    for iteration in range(args.runs + 1):
        order = variants.copy()
        random.Random(iteration).shuffle(order)
        for name, jobs in order:
            command = [str(binaries[name]), "--jobs", str(jobs), "--mem-limit", "64M",
                       "--tmpdir", str(tmpdir), "-o", str(output), *map(str, args.inputs)]
            with (args.workdir / "stderr.log").open("wb") as stderr:
                start = time.perf_counter()
                subprocess.run(command, stdout=subprocess.DEVNULL, stderr=stderr, check=True)
                elapsed = time.perf_counter() - start
            digest = hashlib.sha256(output.read_bytes()).hexdigest()
            expected = expected or digest
            if digest != expected or any(tmpdir.iterdir()):
                raise RuntimeError(f"output mismatch or temporary-run leak: {name}, jobs={jobs}")
            if iteration:
                samples[name, jobs].append(elapsed)
    report = {
        "method": "One warm-up, seeded interleaved runs, warm cache, named output, 64M budget",
        "output_sha256": expected,
        "binaries_sha256": {name: hashlib.sha256(path.read_bytes()).hexdigest()
                            for name, path in binaries.items()},
        "inputs_sha256": [hashlib.sha256(path.read_bytes()).hexdigest() for path in args.inputs],
        "max_ratio": args.max_ratio,
        "results": [],
    }
    for jobs in args.jobs:
        baseline = statistics.median(samples["baseline", jobs])
        candidate = statistics.median(samples["candidate", jobs])
        row = {"jobs": jobs, "baseline_seconds": baseline, "candidate_seconds": candidate,
               "ratio": candidate / baseline, "passed": candidate / baseline <= args.max_ratio,
               "baseline_runs": samples["baseline", jobs], "candidate_runs": samples["candidate", jobs]}
        report["results"].append(row)
        print(f"jobs={jobs}: baseline={baseline:.6f}s candidate={candidate:.6f}s "
              f"ratio={row['ratio']:.3f} {'PASS' if row['passed'] else 'FAIL'}")
    (args.workdir / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return 0 if all(row["passed"] for row in report["results"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
