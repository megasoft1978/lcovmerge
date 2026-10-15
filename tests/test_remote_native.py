#!/usr/bin/env python3
"""Check that remote benchmark bundles are verified before use."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRATCH = ROOT / ".cache/tests/remote-native"
SCRATCH.mkdir(parents=True, exist_ok=True)
SPEC = importlib.util.spec_from_file_location("remote_native", ROOT / "bench/remote-native.py")
REMOTE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REMOTE)


class BundleTests(unittest.TestCase):
    def bundle(self, root, *, tamper=False, unsafe=None):
        entries = {"bin/frozen-rust": b"baseline"}
        rows = []
        for i in range(9):
            path = f"inputs/{i}.info"
            entries[path] = f"synthetic-{i}".encode()
            rows.append({"path": path, "sha256": hashlib.sha256(entries[path]).hexdigest()})
        anchor = hashlib.sha256(entries["bin/frozen-rust"]).hexdigest()
        manifest = {"schema": 1, "frozen_rust": {"path": "bin/frozen-rust",
                    "sha256": anchor, "commit": REMOTE.FROZEN_COMMIT},
                    "inputs": {"sorted": rows[:8], "unsorted": rows[8:]}}
        if tamper:
            entries["inputs/0.info"] = b"changed"
        entries["manifest.json"] = json.dumps(manifest).encode()
        bundle = root / "bundle.tar.gz"
        with tarfile.open(bundle, "w:gz") as archive:
            for name, data in entries.items():
                entry = tarfile.TarInfo(name)
                entry.size = len(data)
                archive.addfile(entry, io.BytesIO(data))
            if unsafe is not None:
                entry = tarfile.TarInfo(unsafe)
                if unsafe == "symlink":
                    entry.type = tarfile.SYMTYPE
                    entry.linkname = "../outside"
                archive.addfile(entry, io.BytesIO(b""))
        return bundle, anchor

    def test_verified_bundle_round_trip(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH) as td:
            root = Path(td)
            bundle, anchor = self.bundle(root)
            with patch.object(REMOTE, "FROZEN_SHA256", anchor):
                manifest = REMOTE.unpack(bundle, REMOTE.digest(bundle), root / "data")
            self.assertEqual(len(manifest["inputs"]["sorted"]), 8)
            self.assertEqual((root / "data/bin/frozen-rust").read_bytes(), b"baseline")

    def test_wrong_bundle_hash_rejected_before_extraction(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH) as td:
            root = Path(td)
            bundle, _ = self.bundle(root)
            with self.assertRaisesRegex(ValueError, "bundle SHA-256"):
                REMOTE.unpack(bundle, "0" * 64, root / "data")
            self.assertFalse((root / "data").exists())

    def test_paths_and_links_rejected_before_extraction(self):
        for unsafe in ["../outside", "/outside", "symlink"]:
            with self.subTest(unsafe=unsafe), tempfile.TemporaryDirectory(dir=SCRATCH) as td:
                root = Path(td)
                bundle, _ = self.bundle(root, unsafe=unsafe)
                with self.assertRaisesRegex(ValueError, "unsafe bundle member"):
                    REMOTE.unpack(bundle, REMOTE.digest(bundle), root / "data")
                self.assertFalse((root / "data/bin/frozen-rust").exists())

    def test_changed_input_rejected_even_with_valid_archive_hash(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH) as td:
            root = Path(td)
            bundle, anchor = self.bundle(root, tamper=True)
            with patch.object(REMOTE, "FROZEN_SHA256", anchor):
                with self.assertRaisesRegex(ValueError, "file identity mismatch"):
                    REMOTE.unpack(bundle, REMOTE.digest(bundle), root / "data")

    def test_frozen_binary_anchor_cannot_be_replaced_by_manifest(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH) as td:
            root = Path(td)
            bundle, _ = self.bundle(root)
            with self.assertRaisesRegex(ValueError, "originally frozen"):
                REMOTE.unpack(bundle, REMOTE.digest(bundle), root / "data")


class IdleTests(unittest.TestCase):
    def top(self, *idle):
        return "\n".join(f"CPU usage: 0% user, 0% sys, {n}% idle" for n in idle)

    def mock_command(self, work, outputs):
        def sample(argv, *, log):
            self.assertEqual(argv, ["top", "-l", "3", "-s", "2", "-n", "10", "-o", "cpu"])
            previous = len(list(work.glob("idle-preflight-*.log")))
            if previous:
                self.assertEqual(len(json.loads((work / "idle-preflight.json").read_text())), previous)
            output = outputs[previous]
            log.write_text(output)
            return output
        return sample

    def test_busy_then_idle_retains_both_attempts(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH) as td:
            work = Path(td)
            outputs = [self.top(99, 56.35, 66.50), self.top(10, 90, 90)]
            with patch.object(REMOTE, "command", side_effect=self.mock_command(work, outputs)) as command:
                REMOTE.wait_for_idle(work)
            self.assertEqual(command.call_count, 2)
            manifest = json.loads((work / "idle-preflight.json").read_text())
            self.assertEqual(manifest, [
                {"attempt": 1, "idle_percent": [99, 56.35, 66.50],
                 "logfile": "idle-preflight-01.log", "accepted": False},
                {"attempt": 2, "idle_percent": [10, 90, 90],
                 "logfile": "idle-preflight-02.log", "accepted": True},
            ])
            for row, output in zip(manifest, outputs):
                self.assertEqual((work / row["logfile"]).read_text(), output)

    def test_persistent_busy_retains_all_attempts(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH) as td:
            work = Path(td)
            outputs = [self.top(99, 89.99, 99)] * 30
            with patch.object(REMOTE, "command", side_effect=self.mock_command(work, outputs)) as command:
                with self.assertRaisesRegex(RuntimeError, "no timing measurements were started"):
                    REMOTE.wait_for_idle(work)
            self.assertEqual(command.call_count, 30)
            manifest = json.loads((work / "idle-preflight.json").read_text())
            self.assertEqual([row["attempt"] for row in manifest], list(range(1, 31)))
            for row in manifest:
                self.assertFalse(row["accepted"])
                self.assertEqual((work / row["logfile"]).read_text(), outputs[0])

    def test_malformed_or_short_samples_cannot_pass(self):
        for output in ["malformed", self.top(99, 99), self.top("..", 99, 99)]:
            with self.subTest(output=output), tempfile.TemporaryDirectory(dir=SCRATCH) as td:
                work = Path(td)
                with patch.object(REMOTE, "command", side_effect=self.mock_command(work, [output])) as command:
                    with self.assertRaisesRegex(RuntimeError, "no timing measurements were started"):
                        REMOTE.wait_for_idle(work, attempts=1)
                self.assertEqual(command.call_count, 1)
                manifest = json.loads((work / "idle-preflight.json").read_text())
                self.assertEqual(len(manifest), 1)
                self.assertFalse(manifest[0]["accepted"])
                self.assertEqual((work / manifest[0]["logfile"]).read_text(), output)


if __name__ == "__main__":
    unittest.main()
