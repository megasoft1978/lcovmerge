#!/usr/bin/env python3
"""Promote the pinned macOS ARM candidate after offline evidence validation."""
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
COMMIT = "4fa94271cfb3d359e9261199baeb40823f4251ec"
CANDIDATE_SHA = "0de34926704b67775957d48e548a254f89b534506b4d5d9f8598b37f5480896d"
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


def promote(source, sorted_report, unsorted_report, version, output, work):
    require(version == VERSION, "native candidate is pinned to version 1.2.0")
    require(not output.exists(), f"refusing to overwrite existing output: {output}")
    require(output.parent.resolve() != work.resolve(), "workdir cannot be output directory")
    prepared = CONFIRM.prepare(source, COMMIT, CANDIDATE_SHA, "sorted-jobs4", work / "prepare-sorted")
    # The second call independently checks the fixed unsorted original evidence.
    prepared_unsorted = CONFIRM.prepare(source, COMMIT, CANDIDATE_SHA, "unsorted", work / "prepare-unsorted")
    original = prepared["original"]
    require(original["candidate_version"].split()[:2] == ["lcovmerge", VERSION], "candidate version mismatch")
    cases = ((sorted_report, "sorted", 2001, [4], prepared),
             (unsorted_report, "unsorted", 1001, [1, 4], prepared_unsorted))
    report_records = []
    for path, shape, runs, jobs, case_prepared in cases:
        report = CONFIRM.read_json(path)
        CONFIRM.validate_report(
            report, runs=runs, jobs=jobs, candidate_sha=CANDIDATE_SHA,
            baseline_sha=CONFIRM.REMOTE.FROZEN_SHA256,
            inputs=prepared["provenance"]["frozen_manifest"]["inputs"][shape],
            profiles=[original["candidate_pgo_sha256"]], host_platform=original["platform"],
            statuses={job: "observed-pass" for job in jobs},
            output_sha=case_prepared["output_sha256"],
        )
        report_records.append({"case": "sorted-jobs4" if shape == "sorted" else shape,
                              "path": str(path), "sha256": digest(path),
                              "output_sha256": report["output_sha256"], "runs": runs, "jobs": jobs})
    candidate = prepared["candidate"]
    require(digest(candidate) == CANDIDATE_SHA, "candidate copy hash mismatch")
    require(CONFIRM.REMOTE.command([candidate, "--version"]) == original["candidate_version"],
            "candidate runtime version mismatch")
    require(not output.exists(), f"refusing to overwrite existing output: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(candidate, output)
    output.chmod(0o755)
    provenance = {
        "schema": 1, "version": VERSION, "candidate_commit": COMMIT,
        "candidate_sha256": CANDIDATE_SHA,
        "source_provenance_sha256": prepared["provenance"]["source_provenance_sha256"],
        "original_build_manifest_sha256": original["candidate_build_manifest_sha256"],
        "original_profile_sha256": original["candidate_pgo_sha256"],
        "confirmation_reports": report_records,
        "promoted_binary_sha256": digest(output),
    }
    require(provenance["promoted_binary_sha256"] == CANDIDATE_SHA, "published copy hash mismatch")
    return provenance


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--sorted-report", type=Path, required=True)
    parser.add_argument("--unsorted-report", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--provenance", type=Path, required=True)
    args = parser.parse_args()
    try:
        require(not args.provenance.exists(), f"refusing to overwrite provenance: {args.provenance}")
        provenance = promote(args.source, args.sorted_report, args.unsorted_report,
                             args.version, args.output, args.workdir)
        args.provenance.parent.mkdir(parents=True, exist_ok=True)
        args.provenance.write_text(json.dumps(provenance, indent=2) + "\n")
    except Exception as exc:
        print(f"native promotion refused: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
