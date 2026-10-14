"""Focused guards for the LCOV profile comparison harness."""
import importlib.util
from pathlib import Path
import sys
import os
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "bench" / "profile-comparison.py"


def main():
    spec = importlib.util.spec_from_file_location("profile_comparison", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for option, value in (("--runs", "0"), ("--jobs", "-1")):
        try:
            module.main(["--binary", "missing", "--lcov", "lcov", "--source-root", ".",
                         "--workdir", ".cache/profile-test", "--input", "missing.info", "--jobs", "1",
                         option, value])
        except SystemExit as exc:
            if exc.code != 2:
                raise
        else:
            raise AssertionError(f"accepted invalid {option} {value}")
    oracle = module.load("profile_test_oracle", ROOT / "tests/compat/oracle.py")
    data = (b"TN:z\r\nSF:x\r\nFN:2,b\r\nFN:1,a\r\nFNDA:0,b\r\nFNDA:1,a\r\n"
            b"DA:2,3\r\nend_of_record\r\n")
    normalized = oracle.normalized(data)
    if normalized != oracle.normalized(data.replace(b"\r\n", b"\n")):
        raise AssertionError("line ending normalization changed")
    changed = data.replace(b"DA:2,3", b"DA:2,4")
    if normalized == oracle.normalized(changed):
        raise AssertionError("normalization discarded a supported line count")
    if normalized == oracle.normalized(data + b"unclosed trailing record\n"):
        raise AssertionError("normalization discarded corrupt trailing data")
    compare = module.load("profile_measurement", ROOT / "bench/compare-builds.py")
    (ROOT / ".cache").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=ROOT / ".cache", prefix="profile-harness-test-") as td:
        directory = Path(td)
        env = dict(os.environ, LCOV_PURE_PERL="1")
        result = compare.child_run(
            [sys.executable, "-c", "import os; print(os.environ['LCOV_PURE_PERL'])"],
            directory / "stdout", directory / "stderr", 5, directory, env=env)
        if result["returncode"] or (directory / "stdout").read_text().strip() != "1":
            raise AssertionError("measurement did not pass backend selection to its child")
    print("profile_comparison=PASS")


if __name__ == "__main__":
    main()
