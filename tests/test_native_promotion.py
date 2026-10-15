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
        self.original = {"candidate_version": "lcovmerge 1.2.0 (commit fixture)",
                         "candidate_build_manifest_sha256": sha(b"build"),
                         "candidate_pgo_sha256": sha(b"profile"), "platform": "macOS fixture"}
        provenance = {"source_provenance_sha256": sha(b"provenance"),
                      "frozen_manifest": {"inputs": {"sorted": self.input_rows,
                                                       "unsorted": self.input_rows}}}
        self.prepared = {"candidate": self.candidate, "original": self.original,
                         "provenance": provenance, "output_sha256": sha(b"sorted")}
        self.prepared_unsorted = {**self.prepared, "output_sha256": sha(b"unsorted")}
        self.source = self.root / "artifact"
        self.source.mkdir()
        self.sorted = self.report("sorted", 2001, [4])
        self.unsorted = self.report("unsorted", 1001, [1, 4])
        self.output = self.root / "dist/lcovmerge-1.2.0-macos-arm64"
        self.work = self.root / "work"

    def report(self, shape, runs, jobs):
        rows = [{"jobs": job, "paired_ratios_candidate_over_baseline": [0.8] * runs,
                 "median_paired_ratio": 0.8, "bootstrap_95_percent_interval": [0.7, 0.9],
                 "status": "observed-pass"} for job in jobs]
        value = {"method": {"runs": runs, "seed": 20261014, "warmups_per_variant": 1,
                            "interleaved_paired": True, "bootstrap_resamples": 10000,
                            "interval": "percentile interval for the median paired log-ratio"},
                 "parameters": {"jobs": jobs, "mem_limit": "64M", "timeout_seconds": 600},
                 "failures": [], "host": {"platform": self.original["platform"], "machine": "arm64"},
                 "binaries": {"candidate": {"sha256": self.digest},
                              "baseline": {"sha256": PROMOTION.CONFIRM.REMOTE.FROZEN_SHA256}},
                 "inputs": self.input_rows,
                 "profile_hashes": [{"sha256": self.original["candidate_pgo_sha256"]}],
                 "output_sha256": sha(shape.encode()), "results": rows}
        path = self.root / shape / "native-confirmation/results/report.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(value))
        return path

    def invoke(self):
        with (patch.object(PROMOTION, "CANDIDATE_SHA", self.digest),
                 patch.object(PROMOTION.CONFIRM.REMOTE, "command", return_value=self.original["candidate_version"]),
                 patch.object(PROMOTION.CONFIRM, "prepare", side_effect=[self.prepared, self.prepared_unsorted])):
            return PROMOTION.promote(self.source, self.sorted, self.unsorted, "1.2.0",
                                     self.output, self.work)

    def test_valid_evidence_publishes_exact_candidate(self):
        provenance = self.invoke()
        self.assertEqual(self.output.read_bytes(), self.candidate.read_bytes())
        self.assertEqual(provenance["promoted_binary_sha256"], self.digest)
        self.assertEqual([r["runs"] for r in provenance["confirmation_reports"]], [2001, 1001])

    def test_inconclusive_report_cannot_publish(self):
        report = json.loads(self.sorted.read_text())
        report["results"][0]["status"] = "inconclusive"
        self.sorted.write_text(json.dumps(report))
        with self.assertRaisesRegex(ValueError, "unexpected result status"):
            self.invoke()
        self.assertFalse(self.output.exists())

    def test_corrupt_confirmation_hash_cannot_publish(self):
        self.sorted.write_text(self.sorted.read_text().replace(self.digest, "0" * 64))
        with self.assertRaisesRegex(ValueError, "report binary mismatch"):
            self.invoke()
        self.assertFalse(self.output.exists())

    def test_source_validation_failure_cannot_publish(self):
        with (patch.object(PROMOTION, "CANDIDATE_SHA", self.digest),
              patch.object(PROMOTION.CONFIRM, "prepare", side_effect=ValueError("source SHA-256 mismatch"))):
            with self.assertRaisesRegex(ValueError, "source SHA-256 mismatch"):
                PROMOTION.promote(self.source, self.sorted, self.unsorted, "1.2.0", self.output, self.work)
        self.assertFalse(self.output.exists())

    def test_wrong_output_cannot_publish(self):
        report = json.loads(self.unsorted.read_text())
        report["output_sha256"] = sha(b"different output")
        self.unsorted.write_text(json.dumps(report))
        with self.assertRaisesRegex(ValueError, "output SHA-256 differs"):
            self.invoke()
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
            PROMOTION.promote(self.source, self.sorted, self.unsorted, "1.2.1", self.output, self.work)
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
