#!/usr/bin/env python3
"""Run the predeclared, fixed-size native precision confirmation stage."""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
from pathlib import Path
import platform
import re
import shutil
import statistics
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("confirmation_remote_native", ROOT / "bench/remote-native.py")
REMOTE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REMOTE)
BUNDLE_SHA256 = "67e56523d3e3b348a0da2c8ea56b65eb623f8b1f04f016b7b1531bb73d9c10da"
CASES = {"sorted-jobs4": ("sorted", 2001, [4]), "unsorted": ("unsorted", 1001, [1, 4])}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def read_json(path):
    return json.loads(path.read_text())


def inside(root, name):
    relative = Path(name)
    require(not relative.is_absolute() and ".." not in relative.parts, "path must be relative inside root")
    path = (root / relative).resolve()
    require(path.is_relative_to(root.resolve()), "path escapes root")
    return path


def verified(path, expected, label):
    require(isinstance(expected, str) and re.fullmatch(r"[0-9a-f]{64}", expected), f"invalid {label} SHA-256")
    require(REMOTE.digest(path) == expected, f"{label} SHA-256 mismatch")


def required_sources():
    return {"Cargo.toml", "Cargo.lock", "build.rs", "scripts/build-optimized.py", "tools/gen-lcov.py",
            *[p.relative_to(ROOT).as_posix() for p in (ROOT / "src").rglob("*.rs")],
            *[p.relative_to(ROOT).as_posix() for p in (ROOT / ".cargo").rglob("*") if p.is_file()]}


def validate_report(report, *, runs, jobs, candidate_sha, baseline_sha, inputs,
                    profiles, host_platform, statuses, output_sha=None):
    method = report["method"]
    require(all(method.get(k) == v for k, v in {
        "runs": runs, "seed": 20261014, "warmups_per_variant": 1,
        "interleaved_paired": True, "bootstrap_resamples": 10000,
        "interval": "percentile interval for the median paired log-ratio",
    }.items()), "unexpected comparison method")
    require(report["parameters"] == {"jobs": jobs, "mem_limit": "64M", "timeout_seconds": 600},
            "unexpected comparison parameters")
    require(report["failures"] == [], "comparison correctness failures")
    require(report["host"]["platform"] == host_platform and report["host"]["machine"] == "arm64",
            "report host mismatch")
    require(report["binaries"]["candidate"]["sha256"] == candidate_sha
            and report["binaries"]["baseline"]["sha256"] == baseline_sha, "report binary mismatch")
    require([(r["sha256"], r["size_bytes"]) for r in report["inputs"]]
            == [(r["sha256"], r["size_bytes"]) for r in inputs], "report input mismatch")
    require([r["sha256"] for r in report["profile_hashes"]] == profiles, "report profile mismatch")
    require(re.fullmatch(r"[0-9a-f]{64}", report["output_sha256"]) is not None, "invalid output SHA-256")
    if output_sha is not None:
        require(report["output_sha256"] == output_sha, "output SHA-256 differs from original")
    rows = report["results"]
    require([row["jobs"] for row in rows] == jobs, "unexpected result jobs")
    for row in rows:
        ratios = row["paired_ratios_candidate_over_baseline"]
        require(len(ratios) == runs and all(math.isfinite(r) and r > 0 for r in ratios), "invalid paired samples")
        require(math.isclose(row["median_paired_ratio"], statistics.median(ratios), rel_tol=1e-12),
                "reported median does not match paired samples")
        low, high = row["bootstrap_95_percent_interval"]
        require(math.isfinite(low) and math.isfinite(high) and 0 < low <= high, "invalid interval")
        status = "regression" if low > 1 else "observed-pass" if high < 1 else "inconclusive"
        require(row["status"] == status == statuses[row["jobs"]], "unexpected result status")


def prepare(source, candidate_commit, candidate_sha, case, work, *, require_all_pass=False):
    """Validate and copy inputs without executing any child process or artifact binary."""
    source, work = source.resolve(), work.resolve()
    require(not work.is_relative_to(source) and not source.is_relative_to(work), "source and workdir must be separate")
    require(not work.exists() or (work.is_dir() and not any(work.iterdir())), "workdir must be empty")
    work.mkdir(parents=True, exist_ok=True)
    try:
        return _prepare(source, candidate_commit, candidate_sha, case, work,
                        require_all_pass=require_all_pass)
    except Exception as exc:
        write_json(work / "validation-failure.json", {"stage": "preparation", "error": str(exc)})
        raise


def _prepare(source, candidate_commit, candidate_sha, case, work, *, require_all_pass=False):
    require(re.fullmatch(r"[0-9a-f]{40}", candidate_commit) is not None, "invalid candidate commit")
    require(re.fullmatch(r"[0-9a-f]{64}", candidate_sha) is not None, "invalid candidate SHA-256")
    shape, runs, jobs = CASES[case]
    original = read_json(inside(source, "remote-native/provenance.json"))
    require(original["candidate_commit"] == candidate_commit and original["candidate_sha256"] == candidate_sha,
            "candidate provenance mismatch")
    candidate_source = inside(source, "remote-native/candidate")
    verified(candidate_source, candidate_sha, "candidate")
    build_path = inside(source, "optimized/build.json")
    profile = inside(source, "optimized/profile.profdata")
    verified(build_path, original["candidate_build_manifest_sha256"], "build manifest")
    verified(profile, original["candidate_pgo_sha256"], "profile")
    build = read_json(build_path)
    require(build["binary_sha256"] == candidate_sha and build["profile_sha256"] == original["candidate_pgo_sha256"]
            and build["commit"] in (candidate_commit, candidate_commit[:12]), "build identity mismatch")
    require(build["compiler"].strip() == original["rustc"].strip()
            and build["target"] == "aarch64-apple-darwin", "build toolchain mismatch")
    require(required_sources() <= build["sources"].keys(), "build sources missing required files")
    for name, sha in build["sources"].items():
        verified(inside(ROOT, name), sha, f"source {name}")
    require(original["runs"] == 99 and original["comparison_seed"] == 20261014, "unexpected original method")
    c1 = original["c1"]
    require(c1["commit"] == REMOTE.C1_COMMIT, "C1 commit mismatch")
    verified(inside(source, "remote-native/c1"), c1["binary_sha256"], "C1")
    verified(inside(source, "remote-native/c1.profdata"), c1["profile_sha256"], "C1 profile")
    data = work / "data"
    manifest = REMOTE.unpack(inside(source, "remote-native-download/native-benchmark-bundle.tar.gz"), BUNDLE_SHA256, data)
    require(manifest == original["frozen_manifest"], "original frozen manifest mismatch")
    # Validate the original artifact's copies too; all measurements use the fresh extraction.
    for row in [manifest["frozen_rust"], *manifest["inputs"]["sorted"], *manifest["inputs"]["unsorted"]]:
        verified(inside(source, "remote-native/data/" + row["path"]), row["sha256"], "original bundle file")
    for rows in manifest["inputs"].values():
        for row in rows:
            require(inside(data, row["path"]).stat().st_size == row["size_bytes"], "input size mismatch")
    reports = {}
    for baseline in ("c1", "frozen-rust"):
        for workload in ("sorted", "unsorted"):
            name = f"{baseline}-{workload}"
            path = inside(source, f"remote-native/results/{name}/report.json")
            report = read_json(path)
            statuses = ({j: "observed-pass" for j in (1, 4)} if require_all_pass else
                        {j: ("observed-pass" if baseline == "c1" or (workload == "sorted" and j == 1)
                             else "inconclusive") for j in (1, 4)})
            validate_report(report, runs=99, jobs=[1, 4], candidate_sha=candidate_sha,
                            baseline_sha=c1["binary_sha256"] if baseline == "c1" else REMOTE.FROZEN_SHA256,
                            inputs=manifest["inputs"][workload], profiles=[original["candidate_pgo_sha256"]]
                            + ([c1["profile_sha256"]] if baseline == "c1" else []),
                            host_platform=original["platform"], statuses=statuses)
            reports[name] = {"path": str(path), "sha256": REMOTE.digest(path), "output_sha256": report["output_sha256"]}
    for workload in ("sorted", "unsorted"):
        require(reports[f"c1-{workload}"]["output_sha256"] == reports[f"frozen-rust-{workload}"]["output_sha256"],
                "original baseline outputs disagree")
    candidate = work / "candidate"
    shutil.copyfile(candidate_source, candidate)
    candidate.chmod(0o755)
    verified(candidate, candidate_sha, "copied candidate")
    frozen = inside(data, manifest["frozen_rust"]["path"])
    frozen.chmod(0o755)
    argv = [sys.executable, ROOT / "bench/compare-builds.py", "--baseline", frozen,
            "--candidate", candidate, "--workdir", work / "results", "--runs", str(runs),
            "--jobs", *map(str, jobs), "--profile", profile,
            *[inside(data, row["path"]) for row in manifest["inputs"][shape]]]
    provenance = {"stage_purpose": "Predeclared fixed precision confirmation of three previously inconclusive rows; "
                  "five original observed-pass rows retained. No pooling, sample selection, adaptive counts or early stopping.",
                  "source_artifact": str(source), "source_provenance_sha256": REMOTE.digest(inside(source, "remote-native/provenance.json")),
                  "candidate_commit": candidate_commit, "candidate_sha256": candidate_sha,
                  "candidate_version": original["candidate_version"],
                  "candidate_build_manifest_sha256": original["candidate_build_manifest_sha256"],
                  "candidate_pgo_sha256": original["candidate_pgo_sha256"], "bundle_sha256": BUNDLE_SHA256,
                  "frozen_manifest": manifest, "c1": c1, "original_reports": reports,
                  "case": case, "runs": runs, "jobs": jobs, "command": list(map(str, argv)),
                  "original_host": {"platform": original["platform"], "cpu": original["cpu"]}}
    write_json(work / "provenance.json", provenance)
    return {"original": original, "provenance": provenance, "argv": list(map(str, argv)),
            "candidate": candidate, "shape": shape, "runs": runs, "jobs": jobs,
            "inputs": manifest["inputs"][shape], "output_sha256": reports[f"frozen-rust-{shape}"]["output_sha256"]}


def validate_host(original):
    require(platform.system() == "Darwin" and platform.machine() == "arm64", "requires macOS ARM64")
    host = {"platform": platform.platform(), "cpu": REMOTE.command(["sysctl", "-n", "machdep.cpu.brand_string"])}
    require(host == {"platform": original["platform"], "cpu": original["cpu"]}, "host CPU/platform differs from original")
    return host


def confirm(prepared, work):
    original, provenance = prepared["original"], prepared["provenance"]
    provenance["host"] = validate_host(original)
    write_json(work / "provenance.json", provenance)
    require(REMOTE.command([prepared["candidate"], "--version"]) == original["candidate_version"], "candidate version mismatch")
    REMOTE.wait_for_idle(work)
    # Stream both outputs to disk, including partial output when the fixed stage times out.
    with (work / "comparison.log").open("w") as log:
        result = subprocess.run(prepared["argv"], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, timeout=2400)
    report_path = work / "results/report.json"
    report = read_json(report_path)
    validate_report(report, runs=prepared["runs"], jobs=prepared["jobs"], candidate_sha=original["candidate_sha256"],
                    baseline_sha=REMOTE.FROZEN_SHA256, inputs=prepared["inputs"], profiles=[original["candidate_pgo_sha256"]],
                    host_platform=original["platform"], statuses={j: "observed-pass" for j in prepared["jobs"]},
                    output_sha=prepared["output_sha256"])
    require(result.returncode == 0, "comparison harness returned failure")
    write_json(work / "summary.json", {"case": provenance["case"], "status": "observed-pass",
               "original_confirmed_rows": 5, "new_required_rows": len(prepared["jobs"]),
               "report": str(report_path), "report_sha256": REMOTE.digest(report_path)})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-artifact", required=True, type=Path)
    parser.add_argument("--candidate-commit", required=True)
    parser.add_argument("--candidate-sha256", required=True)
    parser.add_argument("--case", required=True, choices=CASES)
    parser.add_argument("--workdir", required=True, type=Path)
    args = parser.parse_args()
    prepared = None
    try:
        prepared = prepare(args.source_artifact, args.candidate_commit, args.candidate_sha256, args.case, args.workdir)
        confirm(prepared, args.workdir.resolve())
    except Exception as exc:
        if prepared is not None:
            write_json(args.workdir / "validation-failure.json", {"stage": "confirmation", "error": str(exc)})
            write_json(args.workdir / "summary.json", {"case": args.case, "status": "failed", "error": str(exc),
                       "report": str(args.workdir / "results/report.json")})
        print(f"confirmation refused: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"status": "observed-pass", "summary": str(args.workdir / "summary.json")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
