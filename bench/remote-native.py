#!/usr/bin/env python3
"""Compare pinned native baselines on an otherwise idle macOS ARM runner."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
C1_COMMIT = "33ced0b26adeffe47ed2d2041c8558a667566a51"
FROZEN_COMMIT = "12bf1ec0eae0c6d861edb1b96525650dcfb6f4cf"
FROZEN_SHA256 = "1fce2cd5fb5bbcd286fb730b6be9afadaf0bcd67d3fc8a434746437b43d18447"


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def command(argv, *, cwd=ROOT, env=None, log=None):
    result = subprocess.run(list(map(str, argv)), cwd=cwd, env=env,
                            capture_output=True, text=True, timeout=900)
    if log:
        log.write_text(result.stdout + result.stderr)
    if result.returncode:
        raise RuntimeError(f"command failed ({result.returncode}): {argv[0]}; {result.stderr[-1500:]}")
    return result.stdout.strip()


def wait_for_idle(work: Path, attempts=30):
    samples = []
    for attempt in range(1, attempts + 1):
        logfile = f"idle-preflight-{attempt:02d}.log"
        preflight = command(["top", "-l", "3", "-s", "2", "-n", "10", "-o", "cpu"],
                            log=work / logfile)
        try:
            idle = [float(n) for n in re.findall(r"([0-9.]+)% idle", preflight)]
        except ValueError:
            idle = []
        accepted = len(idle) >= 3 and all(n >= 90 for n in idle[-2:])
        samples.append({"attempt": attempt, "idle_percent": idle,
                        "logfile": logfile, "accepted": accepted})
        (work / "idle-preflight.json").write_text(json.dumps(samples, indent=2) + "\n")
        if accepted:
            return
    raise RuntimeError("runner is busy; no timing measurements were started")


def unpack(bundle: Path, expected: str, destination: Path) -> dict:
    if not re.fullmatch(r"[0-9a-f]{64}", expected) or digest(bundle) != expected:
        raise ValueError("bundle SHA-256 does not match the dispatch input")
    with tarfile.open(bundle, "r:gz") as archive:
        members = archive.getmembers()
        targets = []
        for member in members:
            target = (destination / member.name).resolve()
            if not target.is_relative_to(destination.resolve()) or not (member.isfile() or member.isdir()):
                raise ValueError("unsafe bundle member")
            targets.append(target)
        for member, target in zip(members, targets):
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(member) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)
    manifest = json.loads((destination / "manifest.json").read_text())
    if manifest["schema"] != 1 or manifest["frozen_rust"]["commit"] != FROZEN_COMMIT:
        raise ValueError("unexpected frozen-baseline identity")
    if manifest["frozen_rust"]["sha256"] != FROZEN_SHA256:
        raise ValueError("bundle does not contain the originally frozen Rust binary")
    if len(manifest["inputs"]["sorted"]) != 8 or len(manifest["inputs"]["unsorted"]) != 1:
        raise ValueError("held-out workload file count mismatch")
    for row in [manifest["frozen_rust"], *manifest["inputs"]["sorted"], *manifest["inputs"]["unsorted"]]:
        path = (destination / row["path"]).resolve()
        if not path.is_relative_to(destination.resolve()) or digest(path) != row["sha256"]:
            raise ValueError("bundle file identity mismatch")
    return manifest


def build_c1(source: Path, work: Path) -> tuple[Path, dict]:
    if command(["git", "rev-parse", "HEAD"], cwd=source) != C1_COMMIT:
        raise ValueError("C1 source checkout is not the pinned benchmark revision")
    if command(["git", "status", "--porcelain", "--untracked-files=no"], cwd=source):
        raise ValueError("C1 source checkout has tracked modifications")
    training = work / "training"
    training.mkdir()
    generation = [sys.executable, source / "tools/gen-lcov.py", "--out", training / "inputs",
                  "--shards", "8", "--files", "40", "--lines", "40000", "--seed", "42",
                  "--benchmark-compatible", "--lcov-valid", "--checksums"]
    command(generation, log=work / "training-generation.log")
    inputs = sorted((training / "inputs").glob("shard-*.info"))
    if len(inputs) != 8:
        raise ValueError("C1 training shard count mismatch")
    flags = ["-O2", "-mcpu=native", "-std=c11", "-Wall", "-Wextra", "-Wpedantic",
             "-Wconversion", "-Wshadow", "-Wstrict-prototypes", "-Werror",
             "-fstack-protector-strong", "-D_FORTIFY_SOURCE=2", "-Iinclude",
             f'-DLCOVMERGE_GIT_COMMIT="{C1_COMMIT[:12]}"']
    sources = ["src/lcovmerge.c", "src/platform_posix.c", "src/platform_entry.c"]
    generated, binary = work / "c1-training", work / "c1"
    command(["cc", *flags, "-fprofile-instr-generate", *sources, "-pthread", "-o", generated],
            cwd=source, log=work / "c1-generate-build.log")
    env = dict(os.environ, LLVM_PROFILE_FILE=str(training / "counts.profraw"))
    train_command = [generated, "--jobs", "1", "--tmpdir", training,
                     "-o", training / "output.info", *inputs]
    command(train_command, env=env, log=work / "c1-training.log")
    profdata = command(["xcrun", "-f", "llvm-profdata"])
    profile = work / "c1.profdata"
    command([profdata, "merge", "-output=" + str(profile), training / "counts.profraw"])
    build = ["cc", *flags, "-fprofile-instr-use=" + str(profile), *sources, "-pthread", "-o", binary]
    command(build, cwd=source, log=work / "c1-use-build.log")
    command([binary, "--jobs", "1", "--tmpdir", training,
             "-o", training / "validated.info", *inputs], log=work / "c1-validation.log")
    training_hash = digest(training / "output.info")
    if digest(training / "validated.info") != training_hash:
        raise ValueError("C1 PGO changed the training output")
    metadata = {"kind": "C1 reconstructed with runner-native PGO; not the historical host-specific binary",
                "commit": C1_COMMIT, "compiler": command(["cc", "--version"]),
                "build_command": list(map(str, build)), "training_command": list(map(str, train_command)),
                "generation_command": list(map(str, generation)), "profile_sha256": digest(profile),
                "training_output_sha256": training_hash,
                "training_inputs": [{"path": p.name, "sha256": digest(p)} for p in inputs],
                "source_hashes": {p: digest(source / p) for p in [*sources, "include/platform.h", "include/version.h"]},
                "binary_sha256": digest(binary), "version": command([binary, "--version"])}
    with tarfile.open(work / "c1-training-inputs.tar.gz", "w:gz") as archive:
        archive.add(training / "inputs", arcname="inputs")
    shutil.rmtree(training)
    generated.unlink()
    return binary, metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--bundle-sha256", required=True)
    parser.add_argument("--c1-source", required=True, type=Path)
    parser.add_argument("--workdir", required=True, type=Path)
    parser.add_argument("--runs", type=int, default=99)
    args = parser.parse_args()
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        parser.error("this bundle requires macOS ARM64")
    if args.runs < 19:
        parser.error("release comparisons require at least 19 measured runs")
    work = args.workdir.resolve()
    if work.exists() and any(work.iterdir()):
        parser.error("workdir must be empty; retain each attempt separately")
    work.mkdir(parents=True, exist_ok=True)
    data = work / "data"
    data.mkdir()
    manifest = unpack(args.bundle.resolve(), args.bundle_sha256, data)
    candidate = work / "candidate"
    shutil.copy2(ROOT / "bin/lcovmerge", candidate)
    frozen = data / manifest["frozen_rust"]["path"]
    frozen.chmod(0o755)
    frozen_version = command([frozen, "--version"])
    c1, c1_metadata = build_c1(args.c1_source.resolve(), work)
    metadata = {"candidate_commit": command(["git", "rev-parse", "HEAD"]),
                "candidate_sha256": digest(candidate), "candidate_version": command([candidate, "--version"]),
                "candidate_build_manifest_sha256": digest(ROOT / ".cache/optimized/build.json"),
                "candidate_pgo_sha256": digest(ROOT / ".cache/optimized/profile.profdata"),
                "frozen_manifest": manifest, "frozen_version": frozen_version,
                "c1": c1_metadata, "runs": args.runs,
                "platform": platform.platform(), "cpu": command(["sysctl", "-n", "machdep.cpu.brand_string"]),
                "rustc": command(["rustc", "-vV"]), "comparison_seed": 20261014}
    (work / "provenance.json").write_text(json.dumps(metadata, indent=2) + "\n")
    wait_for_idle(work)
    failures = []
    for name, baseline in [("c1", c1), ("frozen-rust", frozen)]:
        for shape in ["sorted", "unsorted"]:
            result_dir = work / "results" / f"{name}-{shape}"
            argv = [sys.executable, ROOT / "bench/compare-builds.py", "--baseline", baseline,
                    "--candidate", candidate, "--workdir", result_dir, "--runs", str(args.runs),
                    "--jobs", "1", "4", "--profile", ROOT / ".cache/optimized/profile.profdata",
                    *(["--profile", work / "c1.profdata"] if name == "c1" else []),
                    *[data / row["path"] for row in manifest["inputs"][shape]]]
            result = subprocess.run(list(map(str, argv)), cwd=ROOT, capture_output=True, text=True, timeout=3600)
            (work / f"{name}-{shape}.log").write_text(result.stdout + result.stderr)
            if result.returncode:
                failures.append({"comparison": f"{name}-{shape}", "status": result.returncode})
    (work / "summary.json").write_text(json.dumps({"failures": failures}, indent=2) + "\n")
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
