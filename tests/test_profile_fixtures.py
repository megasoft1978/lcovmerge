#!/usr/bin/env python3
"""Keep measured coverage locations inside real synthetic source lines."""
import importlib.util
import gzip
import hashlib
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("profile_fixtures", ROOT / "bench/profile-fixtures.py")
FIXTURES = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FIXTURES)


class FixtureTests(unittest.TestCase):
    def test_function_boundaries_have_exact_line_positions(self):
        lines = FIXTURES.source_functions(3, 100).splitlines()
        self.assertEqual(len(lines), 300)
        for index in range(3):
            self.assertEqual(lines[index * 100], f"int function_{index}(int value) {{")
            self.assertEqual(lines[index * 100 + 99], "}")

    def test_mixed_coverpoints_match_source_functions_and_conditionals(self):
        with tempfile.TemporaryDirectory() as td:
            case = FIXTURES.generate_case(Path(td), "mixed", 1, 1, 1000, "mixed")
            source = Path(case["sources"][0]["path"]).read_text().splitlines()
            for row in Path(case["inputs"][0]).read_text().splitlines():
                if row.startswith("FNL:"):
                    index, start, end = map(int, row[4:].split(","))
                    self.assertEqual(source[start - 1], f"int function_{index}(int value) {{")
                    self.assertEqual(source[end - 1], "}")
                elif row.startswith(("BRDA:", "MCDC:")):
                    line = int(row.split(":", 1)[1].split(",", 1)[0])
                    self.assertTrue(source[line - 1].strip().startswith("if ("))

    def test_gzip_fixture_preserves_shards_metadata_hashes_and_is_deterministic(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            mixed = FIXTURES.generate_case(root, "mixed", 2, 2, 1000, "mixed")
            compressed = FIXTURES.generate_gzip(root, mixed)

            self.assertEqual(compressed["name"], "gzip")
            for original_name in ("branch_coverage", "function_coverage", "mcdc_coverage", "sources"):
                self.assertEqual(compressed[original_name], mixed[original_name])
            self.assertEqual(len(compressed["inputs"]), len(mixed["inputs"]))
            for original_path, compressed_path in zip(mixed["inputs"], compressed["inputs"]):
                original_bytes = Path(original_path).read_bytes()
                compressed_bytes = Path(compressed_path).read_bytes()
                self.assertEqual(gzip.decompress(compressed_bytes), original_bytes)
                self.assertEqual(
                    compressed["input_sha256"][compressed_path],
                    hashlib.sha256(compressed_bytes).hexdigest(),
                )

            repeated = FIXTURES.generate_gzip(root, mixed)
            self.assertEqual(repeated["sources"], compressed["sources"])
            self.assertEqual(repeated["branch_coverage"], compressed["branch_coverage"])
            self.assertEqual(repeated["function_coverage"], compressed["function_coverage"])
            self.assertEqual(repeated["mcdc_coverage"], compressed["mcdc_coverage"])
            self.assertEqual(repeated["inputs"], compressed["inputs"])
            self.assertEqual(repeated["input_sha256"], compressed["input_sha256"])
            for path in compressed["inputs"]:
                self.assertEqual(Path(path).read_bytes(), gzip.compress(
                    Path(mixed["inputs"][compressed["inputs"].index(path)]).read_bytes(), mtime=0
                ))

    def test_large_source_and_testcases_cover_their_declared_lines(self):
        with tempfile.TemporaryDirectory() as td:
            large = FIXTURES.generate_case(Path(td), "large", 1, 1, 100000, "lines")
            self.assertEqual(len(Path(large["sources"][0]["path"]).read_text().splitlines()), 100000)
            case = FIXTURES.generate_testcases(Path(td))
            self.assertEqual(len(Path(case["sources"][0]["path"]).read_text().splitlines()), 50)
            self.assertEqual(len(case["inputs"]), 1)
            rows = Path(case["inputs"][0]).read_text().splitlines()
            self.assertEqual(sum(row.startswith("TN:") for row in rows), 200)
            self.assertEqual(sum(row == "FNL:0,1,50" for row in rows), 200)


if __name__ == "__main__":
    unittest.main()
