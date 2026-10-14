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
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bundle, anchor = self.bundle(root)
            with patch.object(REMOTE, "FROZEN_SHA256", anchor):
                manifest = REMOTE.unpack(bundle, REMOTE.digest(bundle), root / "data")
            self.assertEqual(len(manifest["inputs"]["sorted"]), 8)
            self.assertEqual((root / "data/bin/frozen-rust").read_bytes(), b"baseline")

    def test_wrong_bundle_hash_rejected_before_extraction(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bundle, _ = self.bundle(root)
            with self.assertRaisesRegex(ValueError, "bundle SHA-256"):
                REMOTE.unpack(bundle, "0" * 64, root / "data")
            self.assertFalse((root / "data").exists())

    def test_paths_and_links_rejected_before_extraction(self):
        for unsafe in ["../outside", "/outside", "symlink"]:
            with self.subTest(unsafe=unsafe), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                bundle, _ = self.bundle(root, unsafe=unsafe)
                with self.assertRaisesRegex(ValueError, "unsafe bundle member"):
                    REMOTE.unpack(bundle, REMOTE.digest(bundle), root / "data")
                self.assertFalse((root / "data/bin/frozen-rust").exists())

    def test_changed_input_rejected_even_with_valid_archive_hash(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bundle, anchor = self.bundle(root, tamper=True)
            with patch.object(REMOTE, "FROZEN_SHA256", anchor):
                with self.assertRaisesRegex(ValueError, "file identity mismatch"):
                    REMOTE.unpack(bundle, REMOTE.digest(bundle), root / "data")

    def test_frozen_binary_anchor_cannot_be_replaced_by_manifest(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bundle, _ = self.bundle(root)
            with self.assertRaisesRegex(ValueError, "originally frozen"):
                REMOTE.unpack(bundle, REMOTE.digest(bundle), root / "data")


if __name__ == "__main__":
    unittest.main()
