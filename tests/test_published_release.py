#!/usr/bin/env python3
"""Offline checks of the published-release validation boundary and artifacts."""

import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRATCH = ROOT / ".cache/tests/published-release"
SCRATCH.mkdir(parents=True, exist_ok=True)
SPEC = importlib.util.spec_from_file_location("published_release", ROOT / "scripts/verify-published-release.py")
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)
VERSION = "1.2.3"
BINARY = b"synthetic downloaded executable"


class PublishedReleaseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.addCleanup(temporary.cleanup)
        self.workdir = Path(temporary.name) / "downloaded"
        self.urls = []
        self.commands = []
        self.version_output = f"lcovmerge {VERSION} (git fixture)\n".encode()
        self.bad_hash = False
        self.failure = 0
        environment = patch.dict(os.environ, {
            "RUNNER_OS": "Linux", "RUNNER_ARCH": "X64",
            "LCOVMERGE_BASE_URL": "https://untrusted.invalid",
        }, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        mock = patch.object(release.action, "download", side_effect=self.download)
        mock.start()
        self.addCleanup(mock.stop)
        mock = patch.object(release.subprocess, "run", side_effect=self.run_command)
        mock.start()
        self.addCleanup(mock.stop)

    def download(self, url, destination):
        self.urls.append(url)
        if destination.name == "SHA256SUMS":
            archive = next(self.workdir.glob("*.tar.gz"))
            digest = "0" * 64 if self.bad_hash else hashlib.sha256(archive.read_bytes()).hexdigest()
            destination.write_text(f"{digest}  {archive.name}\n", encoding="ascii")
        else:
            with tarfile.open(destination, "w:gz") as archive:
                entry = tarfile.TarInfo("lcovmerge")
                entry.size = len(BINARY)
                archive.addfile(entry, io.BytesIO(BINARY))

    def run_command(self, command, *, cwd, stdout, stderr, timeout, check):
        self.assertEqual(cwd, ROOT)
        self.assertEqual(timeout, 180)
        self.assertFalse(check)
        self.commands.append(command)
        stdout.write(self.version_output if command[-1] == "--version" else b"suite passed\n")
        stderr.write(b"fixture diagnostic\n")
        if len(self.commands) == 2 and isinstance(self.failure, Exception):
            raise self.failure
        return subprocess.CompletedProcess(command, self.failure if len(self.commands) == 2 else 0)

    def validate(self):
        status = release.main(["--version", VERSION, "--workdir", str(self.workdir)])
        return status, json.loads((self.workdir / "report.json").read_text())

    def test_success_retains_downloads_hashes_logs_and_exact_suites(self):
        status, report = self.validate()
        self.assertEqual(status, 0)
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["version"], VERSION)
        self.assertEqual(report["platform"], {"os": "Linux", "arch": "X64"})
        self.assertEqual(os.environ["INPUT_VERSION"], VERSION)
        archive = Path(report["archive"])
        binary = str((self.workdir / "lcovmerge").resolve())
        self.assertEqual(report["binary"], binary)
        self.assertEqual(Path(binary).read_bytes(), BINARY)
        self.assertTrue((self.workdir / "SHA256SUMS").is_file())
        self.assertEqual(report["archive_sha256"], hashlib.sha256(archive.read_bytes()).hexdigest())
        self.assertEqual(report["binary_sha256"], hashlib.sha256(BINARY).hexdigest())
        self.assertEqual(self.urls, [
            f"https://github.com/megasoft1978/lcovmerge/releases/download/v{VERSION}/{archive.name}",
            f"https://github.com/megasoft1978/lcovmerge/releases/download/v{VERSION}/SHA256SUMS",
        ])
        self.assertEqual(self.commands, [
            [binary, "--version"],
            [sys.executable, str(ROOT / "tests/run_tests.py"), "--binary", binary, "--no-lcov"],
            [sys.executable, str(ROOT / "tests/compat_operations.py"), "--binary", binary],
        ])
        self.assertEqual(len(report["commands"]), 3)
        for command in report["commands"]:
            self.assertEqual(command["status"], "passed")
            self.assertEqual(command["returncode"], 0)
            self.assertTrue((self.workdir / command["stdout"]).read_bytes())
            self.assertEqual((self.workdir / command["stderr"]).read_bytes(), b"fixture diagnostic\n")

    def test_hash_mismatch_never_extracts_or_executes(self):
        self.bad_hash = True
        status, report = self.validate()
        self.assertEqual(status, 1)
        self.assertEqual(report["status"], "failed")
        self.assertIn("SHA-256 verification failed", report["error"])
        self.assertEqual(self.commands, [])
        self.assertFalse((self.workdir / "lcovmerge").exists())
        self.assertIsNotNone(report["archive_sha256"])
        self.assertIsNone(report["binary_sha256"])

    def test_wrong_version_token_never_runs_suites(self):
        for version in ("1.2.30", "1.2.3-dev", "v1.2.3"):
            with self.subTest(version=version):
                self.workdir = self.workdir.parent / version
                self.commands.clear()
                self.version_output = f"lcovmerge {version} (git fixture)\n".encode()
                status, report = self.validate()
                self.assertEqual(status, 1)
                self.assertEqual(len(self.commands), 1)
                self.assertIn("version does not match", report["error"])
                self.assertTrue((self.workdir / "version.stdout.log").exists())

    def test_failed_or_timed_out_suite_retains_report_and_partial_logs(self):
        for failure in (7, subprocess.TimeoutExpired("suite", 180)):
            with self.subTest(failure=failure):
                self.workdir = self.workdir.parent / str(type(failure).__name__)
                self.commands.clear()
                self.failure = failure
                status, report = self.validate()
                self.assertEqual(status, 1)
                self.assertEqual(report["status"], "failed")
                self.assertEqual(len(self.commands), 2)
                self.assertEqual(report["commands"][-1]["status"],
                                 "timeout" if isinstance(failure, Exception) else "failed")
                self.assertEqual((self.workdir / "cli-tests.stdout.log").read_bytes(), b"suite passed\n")
                self.assertTrue((self.workdir / "cli-tests.stderr.log").is_file())

    def test_nonempty_workdir_is_preserved(self):
        self.workdir.mkdir()
        sentinel = self.workdir / "keep"
        sentinel.write_bytes(b"existing")
        with self.assertRaises(SystemExit):
            self.validate()
        self.assertEqual(list(self.workdir.iterdir()), [sentinel])
        self.assertEqual(sentinel.read_bytes(), b"existing")
        self.assertEqual(self.urls, [])

    def test_prefixed_version_is_rejected_before_download(self):
        self.assertEqual(release.main(["--version", "v1.2.3", "--workdir", str(self.workdir)]), 1)
        self.assertEqual(self.urls, [])
        self.assertEqual(json.loads((self.workdir / "report.json").read_text())["status"], "failed")


if __name__ == "__main__":
    unittest.main()
