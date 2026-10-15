#!/usr/bin/env python3
"""Create deterministic synthetic LCOV profiles and matching C sources."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def put(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")
    return path.resolve()


def source_functions(count: int, lines_per_function: int) -> str:
    lines = []
    for index in range(count):
        lines.append(f"int function_{index}(int value) {{")
        for offset in range(2, lines_per_function):
            if offset == lines_per_function - 1:
                lines.append("    return value;")
            elif offset % 10 == 0:
                lines.append("    if (value < 0 && value % 2) value++;")
            else:
                lines.append("    value = -1;")
        lines.append("}")
    return "\n".join(lines) + "\n"


def profile_section(test: str, source: Path, rows: list[str]) -> str:
    return f"TN:{test}\nSF:{source}\n" + "".join(f"{row}\n" for row in rows) + "end_of_record\n"


def line_rows(total: int) -> list[str]:
    return [f"DA:{line},1" for line in range(1, total + 1)]


def generate_case(root: Path, name: str, shards: int, sources_count: int,
                  lines_per_source: int, kind: str) -> dict:
    case_dir = root / name
    source_paths = [put(case_dir / "sources" / f"source_{i:03}.c",
                        source_functions(lines_per_source // 100, 100)) for i in range(sources_count)]
    inputs: list[Path] = []
    for shard in range(shards):
        sections: list[str] = []
        test = f"case_{shard % 2}"
        for source_index, source in enumerate(source_paths):
            rows = line_rows(lines_per_source)
            if kind == "mixed":
                for index in range(10):
                    start = index * 100 + 1
                    end = start + 99
                    rows.extend((f"FNL:{index},{start},{end}", f"FNA:{index},1,function_{index}"))
                for line in range(10, lines_per_source + 1, 10):
                    if line % 100 == 0:
                        continue
                    rows.extend((f"BRDA:{line},7,0,1", f"BRDA:{line},7,1,0"))
                    rows.extend((f"MCDC:{line},2,t,1,0,value<0", f"MCDC:{line},2,f,0,0,value<0",
                                 f"MCDC:{line},2,t,1,1,value%2", f"MCDC:{line},2,f,0,1,value%2"))
            sections.append(profile_section(test, source, rows))
        inputs.append(put(case_dir / "profiles" / f"shard_{shard:03}.info", "".join(sections)))
    return {
        "name": name,
        "inputs": [str(path) for path in inputs],
        "branch_coverage": kind == "mixed" or kind == "testcases",
        "function_coverage": kind == "mixed" or kind == "testcases",
        "mcdc_coverage": kind == "mixed" or kind == "testcases",
        "sources": [{"path": str(path), "sha256": sha256(path)} for path in source_paths],
        "input_sha256": {str(path): sha256(path) for path in inputs},
    }


def generate_testcases(root: Path) -> dict:
    name = "testcases"
    case_dir = root / name
    source = put(case_dir / "sources" / "source.c", source_functions(1, 50))
    inputs: list[Path] = []
    for shard in range(1):
        sections: list[str] = []
        for tc in range(200):
            rows = [f"DA:{line},1" for line in range(1, 51)]
            rows.extend(("FNL:0,1,50", "FNA:0,1,function_0", "BRDA:10,7,0,1", "BRDA:10,7,1,0",
                         "MCDC:10,2,t,1,0,value<0", "MCDC:10,2,f,0,0,value<0",
                         "MCDC:10,2,t,1,1,value%2", "MCDC:10,2,f,0,1,value%2"))
            sections.append(profile_section(f"tc_{tc:03}", source, rows))
        inputs.append(put(case_dir / "profiles" / f"shard_{shard:03}.info", "".join(sections)))
    return {
        "name": name, "inputs": [str(path) for path in inputs],
        "function_coverage": True,
        "branch_coverage": True, "mcdc_coverage": True,
        "sources": [{"path": str(source), "sha256": sha256(source)}],
        "input_sha256": {str(path): sha256(path) for path in inputs},
    }


def generate_gzip(root: Path, mixed: dict) -> dict:
    inputs = []
    for original in mixed["inputs"]:
        path = root / "gzip" / Path(original).with_suffix(".info.gz").name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(gzip.compress(Path(original).read_bytes(), mtime=0))
        inputs.append(path.resolve())
    return {**mixed, "name": "gzip", "inputs": [str(p) for p in inputs],
            "input_sha256": {str(p): sha256(p) for p in inputs}}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workdir", type=Path, required=True)
    args = parser.parse_args()
    root = args.workdir.resolve()
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        parser.error("--workdir must be empty")
    root.mkdir(parents=True, exist_ok=True)
    cases = [
        generate_case(root, "lines", 8, 20, 1000, "lines"),
        generate_case(root, "mixed", 8, 8, 1000, "mixed"),
        generate_testcases(root),
        generate_case(root, "single-source", 4, 1, 100000, "lines"),
    ]
    cases.extend((generate_case(root, "many-shards", 64, 1, 1000, "lines"),
                  generate_gzip(root, cases[1])))
    manifest = {"generator": "profile-fixtures.py", "configuration": {
        "deterministic": True, "line_hit": 1, "mixed_functions_per_source": 10,
        "mixed_function_lines": 100, "mixed_branch_interval": 10,
        "testcases": 200, "single_source_lines": 100000,
    }, "cases": cases}
    put(root / "manifest.json", json.dumps(manifest, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
