#!/usr/bin/env python3
"""Characterize the merge profile against both pinned LCOV backends."""
import argparse
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def cpu_ticks():
    fields = Path("/proc/stat").read_text().splitlines()[0].split()[1:]
    # guest/guest_nice are already included in user/nice.
    values = [int(n) for n in fields[:8]]
    return sum(values), values[3]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument("--lcov", required=True, type=Path)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--workdir", required=True, type=Path)
    parser.add_argument("--runs", type=int, default=19)
    parser.add_argument("--case", action="append")
    args = parser.parse_args()
    if platform.system() != "Linux" or args.runs < 19:
        parser.error("use a Linux runner and at least 19 measured runs")
    work = args.workdir.resolve()
    if work.exists() and any(work.iterdir()):
        parser.error("workdir must be empty; retain attempts separately")
    work.mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, str(ROOT / "bench/profile-fixtures.py"),
                    "--workdir", str(work / "fixtures")], check=True, timeout=120)
    manifest = json.loads((work / "fixtures/manifest.json").read_text())
    if args.case and set(args.case) - {case["name"] for case in manifest["cases"]}:
        parser.error("unknown requested workload")
    idle = []
    before = cpu_ticks()
    for _ in range(2):
        time.sleep(2)
        after = cpu_ticks()
        idle.append(100 * (after[1] - before[1]) / max(1, after[0] - before[0]))
        before = after
    (work / "idle-preflight.json").write_text(json.dumps({"idle_percent": idle}) + "\n")
    if min(idle) < 90:
        raise RuntimeError("runner is busy; no timing measurements were started")
    failures = []
    for case in manifest["cases"]:
        if args.case and case["name"] not in args.case:
            continue
        for jobs in [1, 4]:
            name = f"{case['name']}-jobs{jobs}"
            argv = [sys.executable, str(ROOT / "bench/profile-comparison.py"),
                    "--binary", str(args.binary.resolve()), "--lcov", str(args.lcov.resolve()),
                    "--source-root", str(args.source_root.resolve()), "--workdir", str(work / name),
                    "--runs", str(args.runs), "--jobs", str(jobs)]
            for flag in ["branch_coverage", "mcdc_coverage"]:
                if case[flag]:
                    argv.append("--" + flag.replace("_", "-"))
            if not case["function_coverage"]:
                argv.append("--no-function-coverage")
            for path in case["inputs"]:
                argv.extend(["--input", path])
            result = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, timeout=3600)
            (work / f"{name}.log").write_text(result.stdout + result.stderr)
            if result.returncode:
                failures.append({"case": name, "status": result.returncode})
    (work / "summary.json").write_text(json.dumps({"failures": failures,
        "measurement_scope": "profile characterization; native timing gates remain separate",
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "platform": platform.platform(), "cores": os.cpu_count()}, indent=2) + "\n")
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
