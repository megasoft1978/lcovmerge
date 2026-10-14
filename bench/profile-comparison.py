#!/usr/bin/env python3
"""Compare the opt-in LCOV 2.6 profile with pinned LCOV XS and Perl backends."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import hashlib
import math
import platform
import shutil
import statistics

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--binary", type=Path, required=True)
    p.add_argument("--lcov", required=True)
    p.add_argument("--source-root", type=Path, required=True)
    p.add_argument("--workdir", type=Path, required=True)
    p.add_argument("--input", type=Path, action="append", required=True)
    p.add_argument("--runs", type=int, default=19)
    p.add_argument("--jobs", type=int, required=True)
    p.add_argument("--branch-coverage", action="store_true")
    p.add_argument("--mcdc-coverage", action="store_true")
    return p


def main(argv=None):
    ap = parser()
    args = ap.parse_args(argv)
    if args.runs < 1 or args.jobs < 1:
        ap.error("runs and jobs must be positive")
    binary, source = args.binary.resolve(), args.source_root.resolve()
    inputs = [p.resolve() for p in args.input]
    for path, label in [(binary, "binary"), *[(p, "input") for p in inputs]]:
        if not path.is_file():
            ap.error(f"{label} does not exist: {path}")
    work = args.workdir.resolve()
    work.mkdir(parents=True, exist_ok=True)
    if (work / "report.json").exists():
        ap.error(f"refusing to overwrite existing report: {work / 'report.json'}")
    lcov = shutil.which(args.lcov)
    if lcov is None:
        ap.error(f"LCOV executable not found: {args.lcov}")
    identity_cmd = [sys.executable, str(ROOT / "tools/lcov-oracle-identity.py"),
                    "--lcov", lcov, "--source-root", str(source), "--strict"]
    identity_run = subprocess.run(identity_cmd, capture_output=True, text=True)
    if identity_run.returncode:
        print(identity_run.stdout + identity_run.stderr, file=sys.stderr)
        return 2
    identity = json.loads(identity_run.stdout)
    oracle = load("compat_oracle", ROOT / "tests/compat/oracle.py")
    compare = load("compare_builds", ROOT / "bench/compare-builds.py")
    config = work / "empty.lcovrc"
    config.write_text("", encoding="utf-8")
    family_flags = (["--branch-coverage"] if args.branch_coverage else []) + (["--mcdc-coverage"] if args.mcdc_coverage else [])
    seed = 20261014
    rng = random.Random(seed)
    variants = [("lcov-xs", None), ("lcov-perl", "1"), ("candidate", None)]
    samples, records, expected = {}, [], None

    def run(name, pure, iteration, warmup):
        nonlocal expected
        label = "warmup" if warmup else f"run-{iteration:02d}"
        d = work / f"{label}-{name}"
        if d.exists():
            raise RuntimeError(f"refusing to overwrite existing run records: {d}")
        d.mkdir()
        tmp = d / "tmp"; tmp.mkdir()
        out, profile = d / "output.info", d / "profile.json"
        if name == "candidate":
            cmd = [str(binary), "--lcov-compat=2.6", "--quiet", "--config-file", str(config), "--parallel", str(args.jobs),
                   *family_flags, "--tempdir", str(tmp), "-o", str(out)]
            for p in inputs: cmd += ["-a", str(p)]
        else:
            cmd = [lcov, "--quiet", "--config-file", str(config), "--profile", str(profile),
                   "--parallel", str(args.jobs), "--tempdir", str(tmp), *family_flags,
                   *sum((["-a", str(p)] for p in inputs), []), "-o", str(out)]
        env = dict(os.environ)
        env.pop("LCOV_CONFIG", None)
        env.pop("LCOV_CONFIG_FILE", None)
        if pure is None: env.pop("LCOV_PURE_PERL", None)
        else: env["LCOV_PURE_PERL"] = pure
        meas = compare.child_run(cmd, d / "stdout", d / "stderr", 600, tmp, env=env)
        row = {"variant": name, "iteration": iteration, "warmup": warmup,
               "argv": cmd,
               "environment": {key: env.get(key) for key in
                               ("LCOV_PURE_PERL", "LCOV_CONFIG", "LCOV_CONFIG_FILE")}, **meas}
        if meas["returncode"] or not out.is_file(): raise RuntimeError(f"{name} failed; see {d}")
        if name != "candidate":
            observed = json.loads(profile.read_text()).get("config", {}).get("xs")
            wanted = 0 if pure else 1
            if observed != wanted: raise RuntimeError(f"{name} backend mismatch (xs={observed!r})")
        norm = oracle.normalized(out.read_bytes())
        if expected is None: expected = norm
        elif norm != expected: raise RuntimeError(f"normalized output mismatch for {name}")
        if any(tmp.iterdir()): raise RuntimeError(f"temporary files remain for {name}: {tmp}")
        row["output_sha256"] = compare.digest(out)
        row["semantic_sha256"] = hashlib.sha256(repr(norm).encode("utf-8", "surrogateescape")).hexdigest()
        if not warmup: samples[(iteration, name)] = row
        records.append(row)

    try:
        for name, pure in variants: run(name, pure, -1, True)
        for i in range(args.runs):
            order = variants[:]; rng.shuffle(order)
            for name, pure in order: run(name, pure, i, False)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        ap.error(str(exc))
    results = {}
    for baseline in ("lcov-xs", "lcov-perl"):
        ratios = [samples[i, "candidate"]["elapsed_seconds"] / samples[i, baseline]["elapsed_seconds"] for i in range(args.runs)]
        results[baseline] = {"median_log_ratio": statistics.median(map(math.log, ratios)),
                             "median_ratio": math.exp(statistics.median(map(math.log, ratios))),
                             "bootstrap_95_percent_interval": compare.bootstrap(ratios, seed),
                             "paired_ratios": ratios}
    digest = compare.digest
    report = {"identity": identity, "inputs": [{"path": str(p), "sha256": digest(p), "size_bytes": p.stat().st_size} for p in inputs],
              "parameters": {"runs": args.runs, "jobs": args.jobs, "seed": seed,
                             "branches": args.branch_coverage, "mcdc": args.mcdc_coverage,
                             "timing_gate_sample_count": args.runs >= 19},
              "binaries": {"candidate": {"path": str(binary), "sha256": digest(binary)}, "lcov": lcov},
              "host": {"platform": platform.platform(), "python": sys.version},
              "records": records, "results": results}
    report_path = work / "report.json"
    if report_path.exists():
        ap.error(f"refusing to overwrite existing report: {report_path}")
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(work / "report.json"), "results": results}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
