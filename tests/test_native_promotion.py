#!/usr/bin/env python3
"""Offline gates for exact-binary native release promotion."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRATCH = ROOT / ".cache/tests/native-promotion"
SCRATCH.mkdir(parents=True, exist_ok=True)
SPEC = importlib.util.spec_from_file_location("native_promotion", ROOT / "scripts/promote-native-candidate.py")
PROMOTION = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROMOTION)


def sha(data):
    return hashlib.sha256(data).hexdigest()


class PromotionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.candidate = self.root / "candidate"
        self.candidate.write_bytes(b"pinned candidate")
        self.digest = sha(self.candidate.read_bytes())
        self.input_rows = [{"sha256": sha(b"input"), "size_bytes": 5}]
        self.original = {
            "candidate_version": "lcovmerge 1.2.0 (commit fixture)",
            "candidate_build_manifest_sha256": sha(b"build"),
            "candidate_pgo_sha256": sha(b"profile"),
            "platform": "macOS fixture",
            "c1": {"binary_sha256": "627f9a14734f2cc24c3869c7943e115307a9bfd136efed2ca426099bcb325a43",
                   "profile_sha256": sha(b"c1-profile")},
            "frozen_manifest": {"inputs": {"sorted": self.input_rows,
                                             "unsorted": self.input_rows}},
        }
        provenance = {"source_provenance_sha256": sha(b"provenance")}
        self.prepared = {"candidate": self.candidate, "original": self.original,
                         "provenance": provenance}
        self.source = self.root / "artifact"
        self.source.mkdir()
        self.reports = []
        for baseline in ("c1", "frozen-rust"):
            for workload in ("sorted", "unsorted"):
                self.reports.append(self.report(baseline, workload))
        self.output = self.root / "dist/lcovmerge-1.2.0-macos-arm64"
        self.work = self.root / "work"

    def report(self, baseline, workload):
        profiles = [{"sha256": self.original["candidate_pgo_sha256"]}]
        if baseline == "c1":
            profiles.append({"sha256": self.original["c1"]["profile_sha256"]})
        value = {
            "method": {"runs": 99, "seed": 20261014, "warmups_per_variant": 1,
                       "interleaved_paired": True, "bootstrap_resamples": 10000,
                       "interval": "percentile interval for the median paired log-ratio"},
            "parameters": {"jobs": [1, 4], "mem_limit": "64M", "timeout_seconds": 600},
            "failures": [], "host": {"platform": self.original["platform"], "machine": "arm64"},
            "binaries": {"candidate": {"sha256": self.digest},
                         "baseline": {"sha256": (self.original["c1"]["binary_sha256"]
                                                   if baseline == "c1"
                                                   else PROMOTION.CONFIRM.REMOTE.FROZEN_SHA256)}},
            "inputs": self.input_rows, "profile_hashes": profiles,
            "output_sha256": sha(workload.encode()),
            "results": [{"jobs": job, "paired_ratios_candidate_over_baseline": [0.8] * 99,
                         "median_paired_ratio": 0.8, "bootstrap_95_percent_interval": [0.7, 0.9],
                         "status": "observed-pass"} for job in (1, 4)],
        }
        path = self.source / "remote-native" / "results" / f"{baseline}-{workload}" / "report.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(value))
        return path

    def invoke(self):
        with (patch.object(PROMOTION, "CANDIDATE_SHA", self.digest),
              patch.object(PROMOTION.CONFIRM.REMOTE, "command",
                           return_value=self.original["candidate_version"]),
              patch.object(PROMOTION.CONFIRM, "prepare", return_value=self.prepared)):
            return PROMOTION.promote(self.source, "1.2.0", self.output, self.work)

    def test_valid_evidence_publishes_exact_candidate(self):
        provenance = self.invoke()
        self.assertEqual(self.output.read_bytes(), self.candidate.read_bytes())
        self.assertEqual(provenance["promoted_binary_sha256"], self.digest)
        self.assertEqual([row["case"] for row in provenance["comparisons"]],
                         ["c1-sorted", "c1-unsorted", "frozen-rust-sorted", "frozen-rust-unsorted"])

    def test_inconclusive_report_cannot_publish(self):
        report = json.loads(self.reports[2].read_text())
        report["results"][1]["status"] = "inconclusive"
        self.reports[2].write_text(json.dumps(report))
        with self.assertRaisesRegex(ValueError, "unexpected result status"):
            self.invoke()
        self.assertFalse(self.output.exists())

    def test_corrupt_candidate_hash_cannot_publish(self):
        report = json.loads(self.reports[0].read_text())
        report["binaries"]["candidate"]["sha256"] = "0" * 64
        self.reports[0].write_text(json.dumps(report))
        with self.assertRaisesRegex(ValueError, "report binary mismatch"):
            self.invoke()
        self.assertFalse(self.output.exists())

    def test_wrong_output_hash_cannot_publish(self):
        report = json.loads(self.reports[1].read_text())
        report["output_sha256"] = sha(b"different output")
        self.reports[1].write_text(json.dumps(report))
        with self.assertRaisesRegex(ValueError, "baseline outputs disagree"):
            self.invoke()
        self.assertFalse(self.output.exists())

    def test_source_validation_failure_cannot_publish(self):
        with patch.object(PROMOTION.CONFIRM, "prepare",
                          side_effect=ValueError("source SHA-256 mismatch")):
            with self.assertRaisesRegex(ValueError, "source SHA-256 mismatch"):
                PROMOTION.promote(self.source, "1.2.0", self.output, self.work)
        self.assertFalse(self.output.exists())

    def test_corrupted_candidate_copy_cannot_publish(self):
        self.candidate.write_bytes(b"corrupted copy")
        with self.assertRaisesRegex(ValueError, "candidate copy hash mismatch"):
            self.invoke()
        self.assertFalse(self.output.exists())

    def test_existing_output_and_wrong_version_are_rejected(self):
        self.output.parent.mkdir(parents=True)
        self.output.write_bytes(b"keep")
        with self.assertRaisesRegex(ValueError, "overwrite"):
            self.invoke()
        self.output.unlink()
        with self.assertRaisesRegex(ValueError, "pinned to version"):
            PROMOTION.promote(self.source, "1.2.1", self.output, self.work)
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
