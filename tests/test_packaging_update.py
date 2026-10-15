#!/usr/bin/env python3
"""Tests that release checksum updates rewrite concrete package manifests."""
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("update_packaging", ROOT / "scripts/update-packaging.py")
UPDATE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(UPDATE)


class PackagingUpdateTests(unittest.TestCase):
    def setUp(self):
        self.formula = (ROOT / "packaging/homebrew/lcovmerge.rb").read_text()
        self.scoop = (ROOT / "packaging/scoop/lcovmerge.json").read_text()
        self.sums = "\n".join(
            f"{char * 64}  lcovmerge-1.2.0-{asset}"
            for char, asset in (("a", "macos-arm64.tar.gz"),
                                ("b", "macos-x86_64.tar.gz"),
                                ("c", "windows-x86_64.zip"))
        )

    def test_rewrites_concrete_versions_urls_and_hashes(self):
        formula, scoop = UPDATE.render(self.formula, self.scoop, self.sums, "1.2.0")
        self.assertIn('version "1.2.0"', formula)
        self.assertIn("lcovmerge-1.2.0-macos-arm64.tar.gz", formula)
        self.assertIn("lcovmerge-1.2.0-macos-x86_64.tar.gz", formula)
        self.assertEqual(formula.count('sha256 "' + "a" * 64 + '"'), 1)
        self.assertEqual(formula.count('sha256 "' + "b" * 64 + '"'), 1)
        self.assertIn('"version": "1.2.0"', scoop)
        self.assertIn("lcovmerge-1.2.0-windows-x86_64.zip", scoop)
        self.assertIn('"hash": "' + "c" * 64 + '"', scoop)

    def test_rejects_missing_assets_before_rendering(self):
        with self.assertRaisesRegex(ValueError, "missing valid SHA-256"):
            UPDATE.render(self.formula, self.scoop, "", "1.2.0")

    def test_rejects_unrecognized_formula_shape(self):
        malformed = self.formula.replace('version "1.2.0"', 'release "1.2.0"')
        with self.assertRaisesRegex(ValueError, "Homebrew version"):
            UPDATE.render(malformed,
                          self.scoop, self.sums, "1.2.0")


if __name__ == "__main__":
    unittest.main()
