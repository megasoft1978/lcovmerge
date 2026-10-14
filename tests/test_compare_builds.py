#!/usr/bin/env python3
"""Focused checks for the paired build comparison harness."""

import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "bench" / "compare-builds.py"


def executable(path: Path, contents: bytes) -> None:
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import pathlib, sys\n"
        "args = sys.argv[1:]\n"
        "out = pathlib.Path(args[args.index('-o') + 1])\n"
        f"out.write_bytes({contents!r})\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def main() -> int:
    spec_source = SCRIPT.read_text(encoding="utf-8")
    if "def bootstrap(" not in spec_source:
        raise AssertionError("bootstrap interval calculation is missing")

    with tempfile.TemporaryDirectory(prefix="compare-builds-test-") as temporary:
        root = Path(temporary)
        baseline, candidate = root / "baseline", root / "candidate"
        executable(baseline, b"identical output\n")
        executable(candidate, b"different output\n")
        trace = root / "input.info"
        trace.write_text("SF:/synthetic.c\nDA:1,1\n", encoding="utf-8")
        workdir = root / ".cache" / "run"
        command = [sys.executable, str(SCRIPT), "--baseline", str(baseline),
                   "--candidate", str(candidate), "--workdir", str(workdir),
                   "--jobs", "1", "--runs", "3", "--seed", "7", str(trace)]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode == 0:
            raise AssertionError("mismatching CLI outputs were accepted")
        report = json.loads((workdir / "report.json").read_text(encoding="utf-8"))
        if not report["failures"]:
            raise AssertionError("output mismatch was not recorded")
        if not list(workdir.glob("run-*-candidate-jobs1/failure.json")):
            raise AssertionError("failed run evidence was not retained")
        if list(workdir.glob("run-*-baseline-jobs1")):
            raise AssertionError("successful run scratch directory was not cleaned")

    # Deterministic paired bootstrap and log-space ratio behavior.
    import importlib.util
    spec = importlib.util.spec_from_file_location("compare_builds", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    interval_a = module.bootstrap([0.9, 1.0, 1.1, 1.05], 12)
    interval_b = module.bootstrap([0.9, 1.0, 1.1, 1.05], 12)
    if interval_a != interval_b or not (interval_a[0] <= math.exp(sum(map(math.log, [0.9, 1, 1.1, 1.05])) / 4) <= interval_a[1]):
        raise AssertionError("seeded bootstrap interval is not reproducible or well formed")
    print("compare_builds=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
