#!/usr/bin/env python3
"""Small unit checks for benchmark parsing and measurement metadata."""

import importlib.util
import json
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("lcovmerge_bench_runner", ROOT / "bench/run.py")
if SPEC is None or SPEC.loader is None:
    raise SystemExit("cannot load benchmark runner")
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


def main() -> int:
    cases = (
        ("34701312  maximum resident set size", 34_701_312),
        ("Maximum resident set size (kbytes): 33899", 34_712_576),
        ("Maximum resident set size: 34701312 bytes", 34_701_312),
    )
    for payload, expected in cases:
        actual = runner.rss_bytes(payload)
        if actual != expected:
            raise AssertionError(f"RSS parse mismatch: {payload!r}: {actual!r} != {expected!r}")

    with tempfile.TemporaryDirectory(prefix="lcovmerge-bench-meta-") as directory:
        path = Path(directory) / "input.info"
        path.write_bytes(b"SF:/synthetic.c\nDA:1,1\n")
        manifest = runner.input_manifest([path])
        if manifest[0]["size_bytes"] != path.stat().st_size or len(manifest[0]["sha256"]) != 64:
            raise AssertionError("input manifest omitted exact size or SHA-256")

        package = Path(directory) / "node_modules" / "lcov-result-merger"
        binary = package / "bin" / "lcov-result-merger"
        binary.parent.mkdir(parents=True)
        binary.write_text("#!/usr/bin/env node\n", encoding="utf-8")
        (package / "package.json").write_text(json.dumps({
            "name": "lcov-result-merger", "version": "6.0.0"
        }), encoding="utf-8")
        if runner.package_version(binary, "lcov-result-merger") != "6.0.0":
            raise AssertionError("npm package version was not read from the installed CLI")

    counts = runner.repeat_counts({
        "lcovmerge": {"run_count": 3}, "lcov": {"run_count": 1},
        "lcov-result-merger": {"run_count": 0},
    })
    if counts != {"lcovmerge": 3, "lcov": 1, "lcov-result-merger": 0}:
        raise AssertionError(f"repeat counts included unmeasured tools: {counts!r}")
    print("benchmark_runner_metadata=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
