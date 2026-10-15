#!/usr/bin/env python3
"""Offline identity, fixed-stage and gate tests; never execute benchmark children."""
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRATCH = ROOT / ".cache/tests/native-confirmation"
SCRATCH.mkdir(parents=True, exist_ok=True)
SPEC = importlib.util.spec_from_file_location("native_confirmation", ROOT / "bench/confirm-native.py")
CONFIRM = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CONFIRM)


def sha(data):
    return hashlib.sha256(data).hexdigest()


class ConfirmationTests(unittest.TestCase):
    def setUp(self):
        self.temp = self.enterContext(tempfile.TemporaryDirectory(dir=SCRATCH))
        self.root = Path(self.temp)
        self.source = self.root / "artifact"
        self.work = self.root / "confirmation"
        self.commit = "4fa94271cfb3d359e9261199baeb40823f4251ec"
        self.candidate_sha = sha(b"candidate")
        self.frozen_sha = sha(b"frozen")
        self.enterContext(patch.object(CONFIRM.REMOTE, "FROZEN_SHA256", self.frozen_sha))
        self.child = self.enterContext(patch.object(CONFIRM.subprocess, "run", side_effect=AssertionError("unexpected child")))
        self.command = self.enterContext(patch.object(CONFIRM.REMOTE, "command", side_effect=AssertionError("unexpected command")))
        self.fixture()

    def put(self, name, data):
        path = self.source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def put_json(self, name, data):
        return self.put(name, (json.dumps(data) + "\n").encode())

    def fixture(self):
        entries = {"bin/frozen-rust": b"frozen"}
        inputs = []
        for i in range(9):
            name = f"inputs/{i}.info"
            entries[name] = f"synthetic-{i}".encode()
            inputs.append({"path": name, "sha256": sha(entries[name]), "size_bytes": len(entries[name])})
        self.manifest = {"schema": 1, "frozen_rust": {"path": "bin/frozen-rust", "sha256": self.frozen_sha,
                         "commit": CONFIRM.REMOTE.FROZEN_COMMIT},
                         "inputs": {"sorted": inputs[:8], "unsorted": inputs[8:]}}
        for name, data in entries.items():
            self.put("remote-native/data/" + name, data)
        entries["manifest.json"] = json.dumps(self.manifest).encode()
        bundle = self.put("remote-native-download/native-benchmark-bundle.tar.gz", b"")
        with tarfile.open(bundle, "w:gz") as archive:
            for name, data in entries.items():
                member = tarfile.TarInfo(name)
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
        self.enterContext(patch.object(CONFIRM, "BUNDLE_SHA256", CONFIRM.REMOTE.digest(bundle)))
        self.put("remote-native/candidate", b"candidate").chmod(0o644)
        self.put("remote-native/c1", b"c1")
        self.put("remote-native/c1.profdata", b"c1-profile")
        self.put("optimized/profile.profdata", b"profile")
        self.build = {"commit": self.commit[:12], "binary_sha256": self.candidate_sha,
                      "profile_sha256": sha(b"profile"), "compiler": "fixture rustc\n", "target": "aarch64-apple-darwin",
                      "sources": {name: CONFIRM.REMOTE.digest(ROOT / name) for name in CONFIRM.required_sources()}}
        build_path = self.put_json("optimized/build.json", self.build)
        self.original = {"candidate_commit": self.commit, "candidate_sha256": self.candidate_sha,
                         "candidate_version": "fixture version", "candidate_build_manifest_sha256": CONFIRM.REMOTE.digest(build_path),
                         "candidate_pgo_sha256": sha(b"profile"), "frozen_manifest": self.manifest,
                         "c1": {"commit": CONFIRM.REMOTE.C1_COMMIT, "binary_sha256": sha(b"c1"), "profile_sha256": sha(b"c1-profile")},
                         "runs": 99, "comparison_seed": 20261014, "platform": "fixture macOS", "cpu": "fixture CPU", "rustc": "fixture rustc"}
        self.put_json("remote-native/provenance.json", self.original)
        for baseline in ("c1", "frozen-rust"):
            for shape in ("sorted", "unsorted"):
                self.put_json(f"remote-native/results/{baseline}-{shape}/report.json", self.report(baseline, shape))

    def report(self, baseline="frozen-rust", shape="sorted", runs=99, jobs=None, new=False):
        jobs = [1, 4] if jobs is None else jobs
        rows = []
        for j in jobs:
            passed = new or baseline == "c1" or (shape == "sorted" and j == 1)
            rows.append({"jobs": j, "paired_ratios_candidate_over_baseline": [0.98] * runs,
                         "median_paired_ratio": 0.98, "bootstrap_95_percent_interval": [0.97, 0.99 if passed else 1.01],
                         "status": "observed-pass" if passed else "inconclusive"})
        return {"method": {"runs": runs, "seed": 20261014, "warmups_per_variant": 1, "interleaved_paired": True,
                           "bootstrap_resamples": 10000, "interval": "percentile interval for the median paired log-ratio"},
                "host": {"platform": self.original["platform"], "machine": "arm64"},
                "parameters": {"jobs": jobs, "mem_limit": "64M", "timeout_seconds": 600},
                "binaries": {"candidate": {"sha256": self.candidate_sha},
                             "baseline": {"sha256": sha(b"c1") if baseline == "c1" else self.frozen_sha}},
                "inputs": self.manifest["inputs"][shape], "profile_hashes": [{"sha256": sha(b"profile")}]
                + ([{"sha256": sha(b"c1-profile")}] if baseline == "c1" else []),
                "output_sha256": sha(shape.encode()), "results": rows, "failures": []}

    def prepare(self, case="sorted-jobs4"):
        return CONFIRM.prepare(self.source, self.commit, self.candidate_sha, case, self.work)

    def snapshot(self):
        return {str(p.relative_to(self.source)): (p.read_bytes(), p.stat().st_mode) for p in self.source.rglob("*") if p.is_file()}

    def update_build(self):
        path = self.put_json("optimized/build.json", self.build)
        self.original["candidate_build_manifest_sha256"] = CONFIRM.REMOTE.digest(path)
        self.put_json("remote-native/provenance.json", self.original)

    def test_valid_preparation_preserves_artifact_and_fixed_commands(self):
        before = self.snapshot()
        for case, shape, count, jobs in [("sorted-jobs4", "sorted", "2001", ["4"]), ("unsorted", "unsorted", "1001", ["1", "4"])]:
            self.work = self.root / case
            prepared = self.prepare(case)
            argv = prepared["argv"]
            self.assertEqual(argv[argv.index("--runs") + 1], count)
            self.assertEqual(argv[argv.index("--jobs") + 1:argv.index("--profile")], jobs)
            self.assertEqual(argv[argv.index("--profile") + 1], str(self.source / "optimized/profile.profdata"))
            self.assertEqual(argv[argv.index("--profile") + 2:], [str(self.work / "data" / r["path"]) for r in self.manifest["inputs"][shape]])
        if os.name != "nt":  # Windows chmod does not expose POSIX execute bits.
            self.assertEqual(prepared["candidate"].stat().st_mode & 0o777, 0o755)
            self.assertEqual((self.work / "data/bin/frozen-rust").stat().st_mode & 0o777, 0o755)
            self.assertNotIn("--seed", argv)
            self.assertNotIn("--timeout", argv)
        self.assertEqual(before, self.snapshot())
        self.child.assert_not_called()
        self.command.assert_not_called()

    def test_tampering_rejected_before_any_child(self):
        for name in ("remote-native/candidate", "optimized/profile.profdata", "optimized/build.json", "remote-native/data/inputs/0.info"):
            with self.subTest(name=name):
                path = self.source / name
                original = path.read_bytes()
                path.write_bytes(b"tampered")
                with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                    self.prepare()
                self.assertTrue((self.work / "validation-failure.json").is_file())
                path.write_bytes(original)
                shutil.rmtree(self.work)
        self.child.assert_not_called()
        self.command.assert_not_called()

    def test_source_hash_coverage_and_escaping_rejected(self):
        sources = copy.deepcopy(self.build["sources"])
        for mutation in ("hash", "missing", "escape", "absolute"):
            with self.subTest(mutation=mutation):
                self.build["sources"] = copy.deepcopy(sources)
                if mutation == "hash":
                    self.build["sources"]["src/main.rs"] = "0" * 64
                elif mutation == "missing":
                    del self.build["sources"]["src/main.rs"]
                else:
                    self.build["sources"]["../outside" if mutation == "escape" else str(ROOT / "Cargo.toml")] = "0" * 64
                self.update_build()
                with self.assertRaises(ValueError):
                    self.prepare()
                shutil.rmtree(self.work)
        self.child.assert_not_called()
        self.command.assert_not_called()

    def test_wrong_original_status_and_method_rejected(self):
        path = self.source / "remote-native/results/frozen-rust-unsorted/report.json"
        original = path.read_text()
        for mutation in ("status", "method", "correctness", "binary"):
            report = json.loads(original)
            if mutation == "status":
                report["results"][0]["status"] = "observed-pass"
            elif mutation == "method":
                report["method"]["runs"] = 19
            elif mutation == "correctness":
                report["failures"] = [{"error": "bad bytes"}]
            else:
                report["binaries"]["candidate"]["sha256"] = "0" * 64
            path.write_text(json.dumps(report))
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                self.prepare()
            shutil.rmtree(self.work)
        self.child.assert_not_called()

    def test_bad_expected_identity_and_nonempty_work_rejected(self):
        for commit, digest in [("bad", self.candidate_sha), (self.commit, "G" * 64), ("0" * 40, self.candidate_sha)]:
            with self.assertRaises(ValueError):
                CONFIRM.prepare(self.source, commit, digest, "unsorted", self.work)
            shutil.rmtree(self.work)
        self.work.mkdir()
        marker = self.work / "keep"
        marker.write_text("retained")
        with self.assertRaisesRegex(ValueError, "must be empty"):
            self.prepare()
        self.assertEqual(marker.read_text(), "retained")
        self.child.assert_not_called()

    def test_wrong_host_refused_before_candidate_execution(self):
        prepared = self.prepare()
        self.command.side_effect = lambda argv: "wrong CPU"
        with patch.object(CONFIRM.platform, "system", return_value="Darwin"), patch.object(CONFIRM.platform, "machine", return_value="arm64"), patch.object(CONFIRM.platform, "platform", return_value=self.original["platform"]):
            with self.assertRaisesRegex(ValueError, "CPU/platform"):
                CONFIRM.confirm(prepared, self.work)
        self.assertEqual(self.command.call_args_list[0].args[0], ["sysctl", "-n", "machdep.cpu.brand_string"])
        self.child.assert_not_called()

    def test_new_gate_and_original_output_comparison(self):
        prepared = self.prepare()
        good = self.report(runs=2001, jobs=[4], new=True)
        for mutation in (None, "output", "inconclusive", "boundary", "exit"):
            report = copy.deepcopy(good)
            if mutation == "output":
                report["output_sha256"] = "0" * 64
            elif mutation == "inconclusive":
                report["results"][0].update(status="inconclusive", bootstrap_95_percent_interval=[0.97, 1.01])
            elif mutation == "boundary":
                report["results"][0].update(status="inconclusive", bootstrap_95_percent_interval=[0.97, 1.0])
            def fake_run(argv, **kwargs):
                self.assertEqual(argv, prepared["argv"])
                self.assertEqual(kwargs["timeout"], 2400)
                (self.work / "results").mkdir(exist_ok=True)
                CONFIRM.write_json(self.work / "results/report.json", report)
                return SimpleNamespace(returncode=1 if mutation == "exit" else 0)
            with self.subTest(mutation=mutation), patch.object(CONFIRM, "validate_host", return_value=self.original), patch.object(CONFIRM.REMOTE, "wait_for_idle") as idle:
                self.command.side_effect = None
                self.command.return_value = self.original["candidate_version"]
                self.child.side_effect = fake_run
                if mutation is None:
                    CONFIRM.confirm(prepared, self.work)
                    self.assertEqual(CONFIRM.read_json(self.work / "summary.json")["status"], "observed-pass")
                else:
                    with self.assertRaises(ValueError):
                        CONFIRM.confirm(prepared, self.work)
                idle.assert_called_once_with(self.work)

    def test_cli_retains_preparation_failure(self):
        (self.source / "remote-native/candidate").write_bytes(b"changed")
        argv = ["confirm-native.py", "--source-artifact", str(self.source), "--candidate-commit", self.commit,
                "--candidate-sha256", self.candidate_sha, "--case", "unsorted", "--workdir", str(self.work)]
        with patch.object(CONFIRM.sys, "argv", argv), patch("sys.stderr", new=io.StringIO()):
            self.assertEqual(CONFIRM.main(), 1)
        self.assertEqual(CONFIRM.read_json(self.work / "validation-failure.json")["stage"], "preparation")
        self.child.assert_not_called()

    def test_busy_host_retains_failure_without_starting_comparison(self):
        argv = ["confirm-native.py", "--source-artifact", str(self.source), "--candidate-commit", self.commit,
                "--candidate-sha256", self.candidate_sha, "--case", "unsorted", "--workdir", str(self.work)]
        self.command.side_effect = None
        self.command.return_value = self.original["candidate_version"]
        with patch.object(CONFIRM.sys, "argv", argv), patch("sys.stderr", new=io.StringIO()), patch.object(CONFIRM, "validate_host", return_value={"cpu": "fixture CPU", "platform": "fixture macOS"}), patch.object(CONFIRM.REMOTE, "wait_for_idle", side_effect=RuntimeError("runner is busy")):
            self.assertEqual(CONFIRM.main(), 1)
        self.assertEqual(CONFIRM.read_json(self.work / "validation-failure.json")["stage"], "confirmation")
        self.assertEqual(CONFIRM.read_json(self.work / "summary.json")["status"], "failed")
        self.child.assert_not_called()


if __name__ == "__main__":
    unittest.main()
