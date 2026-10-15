#!/usr/bin/env python3
"""Validate a downloaded release on its native runner; retain audit artifacts."""

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "published_release_action", ROOT / "action/scripts/entrypoint.py"
)
action = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(action)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True, help="Release version without leading v")
    parser.add_argument("--workdir", required=True, type=Path, help="Empty artifact directory")
    args = parser.parse_args(argv)
    workdir = args.workdir.resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    if any(workdir.iterdir()):
        parser.error("--workdir must be empty")
    report = {
        "version": args.version,
        "platform": {"os": os.environ.get("RUNNER_OS"), "arch": os.environ.get("RUNNER_ARCH")},
        "status": "failed", "commands": [], "archive_sha256": None, "binary_sha256": None,
    }

    def run(name, command):
        record = {"command": command, "status": "failed",
                  "stdout": f"{name}.stdout.log", "stderr": f"{name}.stderr.log"}
        report["commands"].append(record)
        with (workdir / record["stdout"]).open("wb") as stdout, \
                (workdir / record["stderr"]).open("wb") as stderr:
            try:
                result = subprocess.run(command, cwd=ROOT, stdout=stdout, stderr=stderr,
                                        timeout=180, check=False)
            except subprocess.TimeoutExpired:
                record["status"] = "timeout"
                raise
        record["returncode"] = result.returncode
        if result.returncode:
            raise RuntimeError(f"{name} exited with status {result.returncode}")
        record["status"] = "passed"
        return (workdir / record["stdout"]).read_text(encoding="utf-8", errors="replace")

    try:
        if args.version.startswith("v") or args.version != args.version.strip():
            raise ValueError("--version must omit leading v and surrounding whitespace")
        os.environ["INPUT_VERSION"] = args.version
        version, asset, _ = action.release_coordinates()
        # Never honor the Action's test/mirror URL override for published validation.
        base_url = f"https://github.com/{action.REPOSITORY}/releases/download/v{version}"
        archive, sums = workdir / asset, workdir / "SHA256SUMS"
        report.update(archive=str(archive), base_url=base_url)
        action.download(f"{base_url}/{asset}", archive)
        report["archive_sha256"] = hashlib.sha256(archive.read_bytes()).hexdigest()
        action.download(f"{base_url}/SHA256SUMS", sums)
        action.verify_archive(asset, archive, sums)
        binary = action.extract_binary(
            asset, archive, workdir / ("lcovmerge.exe" if asset.endswith(".zip") else "lcovmerge")
        ).resolve()
        report.update(binary=str(binary), binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest())
        output = run("version", [str(binary), "--version"])
        tokens = output.split()
        if len(tokens) < 2 or tokens[:2] != ["lcovmerge", version]:
            raise ValueError(f"Downloaded binary version does not match {version}: {output!r}")
        run("cli-tests", [sys.executable, str(ROOT / "tests/run_tests.py"),
                          "--binary", str(binary), "--no-lcov"])
        run("compat-operations", [sys.executable, str(ROOT / "tests/compat_operations.py"),
                                  "--binary", str(binary)])
        report["status"] = "passed"
    except (Exception, SystemExit) as exc:
        report["error"] = str(exc)
        print(f"Published release validation failed: {exc}", file=sys.stderr)
    finally:
        (workdir / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
