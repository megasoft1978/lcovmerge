#!/usr/bin/env python3
"""Promote the exact macOS ARM binary that passed the complete native gate."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
VERSION = "1.2.0"
COMMIT = "90d2d072a3cc80de2da21ce8fed4d9ecef0e86a4"
CANDIDATE_SHA = "d5545a93f5fe7b1560a421ce33975b309ff198ca2b06d48caa884e4c8303d245"
RUN_ID = 38685796544
SPEC = importlib.util.spec_from_file_location("native_confirmation", ROOT / "bench/confirm-native.py")
CONFIRM = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CONFIRM)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def promote(source, version, output, work):
    require(version == VERSION, "native candidate is pinned to version 1.2.0")
    require(not output.exists(), f"refusing to overwrite existing output: {output}")
    require(output.parent.resolve() != work.resolve(), "workdir cannot be output directory")
    prepared = CONFIRM.prepare(source, COMMIT, CANDIDATE_SHA, "sorted-jobs4", work,
                              require_all_pass=True)
    original = prepared["original"]
    require(original["candidate_version"].split()[:2] == ["lcovmerge", VERSION],
            "candidate version mismatch")

    reports = []
    outputs = {}
    for baseline in ("c1", "frozen-rust"):
        for workload in ("sorted", "unsorted"):
            name = f"{baseline}-{workload}"
            path = source / "remote-native" / "results" / name / "report.json"
            report = CONFIRM.read_json(path)
            expected_baseline = (original["c1"]["binary_sha256"] if baseline == "c1"
                                 else CONFIRM.REMOTE.FROZEN_SHA256)
            input_rows = original["frozen_manifest"]["inputs"][workload]
            profiles = [original["candidate_pgo_sha256"]]
            if baseline == "c1":
                profiles.append(original["c1"]["profile_sha256"])
            CONFIRM.validate_report(
                report, runs=99, jobs=[1, 4], candidate_sha=CANDIDATE_SHA,
                baseline_sha=expected_baseline, inputs=input_rows, profiles=profiles,
                host_platform=original["platform"],
                statuses={1: "observed-pass", 4: "observed-pass"},
            )
            outputs.setdefault(workload, report["output_sha256"])
            require(outputs[workload] == report["output_sha256"],
                    f"{workload} baseline outputs disagree")
            reports.append({"case": name, "sha256": digest(path),
                            "output_sha256": report["output_sha256"],
                            "median_ratios": {str(row["jobs"]): row["median_paired_ratio"]
                                              for row in report["results"]},
                            "intervals": {str(row["jobs"]): row["bootstrap_95_percent_interval"]
                                          for row in report["results"]}})

    candidate = prepared["candidate"]
    require(digest(candidate) == CANDIDATE_SHA, "candidate copy hash mismatch")
    require(CONFIRM.REMOTE.command([candidate, "--version"]) == original["candidate_version"],
            "candidate runtime version mismatch")
    require(not output.exists(), f"refusing to overwrite existing output: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(candidate, output)
    output.chmod(0o755)
    provenance = {
        "schema": 1, "version": VERSION, "benchmark_run_id": RUN_ID,
        "candidate_commit": COMMIT, "candidate_sha256": CANDIDATE_SHA,
        "source_provenance_sha256": prepared["provenance"]["source_provenance_sha256"],
        "original_build_manifest_sha256": original["candidate_build_manifest_sha256"],
        "original_profile_sha256": original["candidate_pgo_sha256"],
        "comparisons": reports, "promoted_binary_sha256": digest(output),
    }
    require(provenance["promoted_binary_sha256"] == CANDIDATE_SHA,
            "published copy hash mismatch")
    return provenance


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--provenance", type=Path, required=True)
    args = parser.parse_args()
    try:
        require(not args.provenance.exists(), f"refusing to overwrite provenance: {args.provenance}")
        provenance = promote(args.source, args.version, args.output, args.workdir)
        args.provenance.parent.mkdir(parents=True, exist_ok=True)
        args.provenance.write_text(json.dumps(provenance, indent=2) + "\n")
    except Exception as exc:
        print(f"native promotion refused: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
