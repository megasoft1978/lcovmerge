#!/usr/bin/env python3
"""Keep measured coverage locations inside real synthetic source lines."""
import importlib.util
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
