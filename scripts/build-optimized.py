#!/usr/bin/env python3
"""Build a portable Rust release with cached, synthetic PGO training."""
from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


@contextlib.contextmanager
def build_lock(path: Path):
    with path.open("a+b") as handle:
        if not path.stat().st_size:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            while True:
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.2)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            yield


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target")
    parser.add_argument("--workdir", type=Path, default=ROOT / ".cache" / "optimized")
    parser.add_argument("--cargo", default="cargo")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    os.chdir(ROOT)
    work = args.workdir.resolve()
    work.mkdir(parents=True, exist_ok=True)
    compiler = subprocess.check_output(["rustc", "-vV"], text=True)
    host = compiler.split("host: ", 1)[1].splitlines()[0]
    target = args.target or host
    if target != host and not ("linux" in host and "linux" in target and host.split("-")[0] == target.split("-")[0]):
        parser.error("PGO training requires a native runnable target; use build-all.sh for cross builds")
    flags = os.environ.get("CARGO_ENCODED_RUSTFLAGS")
    flags = flags.split("\x1f") if flags else shlex.split(os.environ.get("RUSTFLAGS", ""))
    if target.endswith("windows-msvc"):
        flags += ["-C", "target-feature=+crt-static"]
    sources = [ROOT / "Cargo.toml", ROOT / "Cargo.lock", ROOT / "build.rs", Path(__file__), ROOT / "tools/gen-lcov.py", *sorted((ROOT / "src").rglob("*.rs")), *sorted((ROOT / ".cargo").glob("*"))]
    commit = os.environ.get("LCOVMERGE_GIT_COMMIT")
    if not commit:
        try:
            result = subprocess.run(["git", "rev-parse", "--short=12", "HEAD"], capture_output=True, text=True)
            commit = result.stdout.strip() if result.returncode == 0 else "unknown"
        except OSError:
            commit = "unknown"
    identity = {"compiler": compiler, "target": target, "flags": flags, "commit": commit, "sources": {str(path.relative_to(ROOT)): digest(path) for path in sources}}
    fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    executable = "lcovmerge.exe" if "windows" in target else "lcovmerge"
    profile = work / "profile.profdata"
    manifest = work / "build.json"
    with build_lock(work / "build.lock"):
        try:
            previous = json.loads(manifest.read_text()) if manifest.exists() else {}
        except (ValueError, OSError):
            previous = {}

        def build(phase: str) -> Path:
            option = "-Cprofile-generate=" + str(work / "raw") if phase == "generate" else "-Cprofile-use=" + str(frozen_profile)
            env = dict(os.environ, CARGO_ENCODED_RUSTFLAGS="\x1f".join([*flags, option]))
            with (work / (phase + ".log")).open("w") as log:
                result = subprocess.run([args.cargo, "build", "--locked", "--release", "--target", target, "--target-dir", str(work / phase)], env=env, stdout=log, stderr=subprocess.STDOUT)
            if result.returncode:
                raise RuntimeError((work / (phase + ".log")).read_text()[-8000:])
            return work / phase / target / "release" / executable

        if previous.get("fingerprint") != fingerprint or not profile.exists() or previous.get("profile_sha256") != digest(profile):
            print("Training portable Rust PGO on synthetic seed 42 inputs...", flush=True)
            sysroot = Path(subprocess.check_output(["rustc", "--print", "sysroot"], text=True).strip())
            tool = sysroot / "lib/rustlib" / host / "bin" / ("llvm-profdata.exe" if os.name == "nt" else "llvm-profdata")
            if not tool.exists():
                subprocess.run(["rustup", "component", "add", "llvm-tools"], check=True)
            if (work / "raw").exists():
                (work / "raw").rename(work / ("raw-retained-" + str(time.time_ns())))
            (work / "raw").mkdir()
            binary = build("generate")
            train = work / "inputs"
            subprocess.run([sys.executable, "tools/gen-lcov.py", "--out", str(train), "--shards", "8", "--files", "40", "--lines", "40000", "--seed", "42", "--benchmark-compatible", "--lcov-valid", "--checksums"], check=True)
            inputs = sorted(train.glob("shard-*.info"))
            parts = inputs[0].read_bytes().split(b"end_of_record\n")
            assert not parts[-1].strip()
            unsorted = work / "unsorted.info"
            unsorted.write_bytes(b"".join(part + b"end_of_record\n" for part in reversed(parts[:-1])))
            env = dict(os.environ, LLVM_PROFILE_FILE=str(work / "raw" / "train-%p.profraw"))
            for name, files in [("sorted", inputs), ("unsorted", [unsorted])]:
                hashes = []
                for jobs in [1, 4]:
                    output = work / f"{name}-{jobs}.info"
                    subprocess.run([str(binary), "--jobs", str(jobs), "--tmpdir", str(work), "-o", str(output), *map(str, files)], env=env, check=True)
                    hashes.append(digest(output))
                if hashes[0] != hashes[1]:
                    raise RuntimeError(f"PGO training output differed by job count: {name}")
            compat = work / "compat-inputs"
            compat.mkdir(exist_ok=True)
            source = compat / "sample.c"
            source_lines = ["int f(void) {", "    int a = 1;", "    return a;", "}"]
            source.write_text("\n".join(source_lines) + "\n", encoding="utf-8")
            config = compat / "lcovrc"
            config.write_text("", encoding="utf-8")
            compat_inputs = []
            for shard in range(4):
                rows = [f"TN:test-{shard % 2}", f"SF:{source}", "VER:training-43",
                        "FNL:0,1,4", f"FNA:0,{shard + 1},f", "FNA:0,0,alias",
                        "BRDA:2,0,0,1", "BRDA:2,0,1,0",
                        "MCDC:2,2,t,1,0,a", "MCDC:2,2,f,0,0,a",
                        "MCDC:2,2,t,1,1,b", "MCDC:2,2,f,0,1,b"]
                for number, text in enumerate(source_lines, 1):
                    checksum = base64.b64encode(hashlib.md5(text.encode()).digest()).decode().rstrip("=")
                    rows.append(f"DA:{number},1,{checksum}")
                rows.append("end_of_record")
                path = compat / f"shard-{shard}.info"
                path.write_text("\n".join(rows) + "\n", encoding="utf-8")
                compat_inputs.append(path)
            compat_hashes = []
            for jobs in [1, 4]:
                output = work / f"compat-{jobs}.info"
                command = [str(binary), "--lcov-compat=2.6", "--config-file", str(config),
                           "--branch-coverage", "--mcdc-coverage", "--checksum", "--parallel", str(jobs),
                           "--tempdir", str(work), "-o", str(output)]
                for path in compat_inputs:
                    command.extend(["-a", str(path)])
                subprocess.run(command, env=env, check=True)
                compat_hashes.append(digest(output))
            if compat_hashes[0] != compat_hashes[1]:
                raise RuntimeError("Compatibility PGO output differed by job count")
            subprocess.run([str(tool), "merge", "-o", str(profile), *map(str, (work / "raw").glob("*.profraw"))], check=True)
        frozen_profile = work / ("profile-" + digest(profile) + ".profdata")
        if not frozen_profile.exists():
            shutil.copyfile(profile, frozen_profile)
        binary = build("use")
        identity.update(fingerprint=fingerprint, profile_sha256=digest(profile), binary_sha256=digest(binary), binary=str(binary))
        manifest_stage = manifest.with_suffix(".tmp")
        manifest_stage.write_text(json.dumps(identity, indent=2) + "\n")
        manifest_stage.replace(manifest)
        if args.output:
            output = args.output.resolve()
            output.parent.mkdir(parents=True, exist_ok=True)
            stage = output.with_name(output.name + "." + str(os.getpid()))
            try:
                shutil.copy2(binary, stage)
                stage.chmod(0o755)
                stage.replace(output)
            finally:
                stage.unlink(missing_ok=True)
        print(binary)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        raise SystemExit(str(exc)) from exc
