#!/usr/bin/env python3
"""Verify the pinned LCOV source tree and report the selected runtime identity."""
import argparse
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess

PINNED_COMMIT = "a17443e7dbb31b5b7bff6beac32e7b9b02ec3342"
REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


def default_source_root():
    for parent in (REPO_ROOT, *REPO_ROOT.parents):
        candidate = parent / ".cache/lcov-compat-implementation/oracle-source"
        if candidate.is_dir():
            return candidate
    return REPO_ROOT / ".cache/lcov-compat-implementation/oracle-source"


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def run(command, env):
    try:
        p = subprocess.run(command, capture_output=True, text=True, env=env, timeout=20)
        return {"command": command, "status": p.returncode, "stdout": p.stdout.strip(),
                "stderr": p.stderr.strip()}
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"command": command, "status": None, "stdout": "", "stderr": str(e)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lcov", help="LCOV executable to inspect (defaults to source-root/bin/lcov, then PATH)")
    ap.add_argument("--source-root", default=str(default_source_root()), help="pinned LCOV source checkout")
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args()

    source = pathlib.Path(args.source_root).resolve()
    executable = pathlib.Path(args.lcov).expanduser() if args.lcov else None
    if executable is None:
        candidate = source / "bin/lcov"
        executable = candidate if candidate.exists() else pathlib.Path(shutil.which("lcov") or "lcov")
    executable = executable.expanduser().absolute()
    resolved = pathlib.Path(os.path.realpath(executable))
    implementation = resolved
    try:
        launcher_text = resolved.read_text(errors="replace")
        match = re.search(r"\bexec\s+[\"']([^\"']+)[\"']", launcher_text)
        if match:
            wrapped = pathlib.Path(match.group(1))
            if not wrapped.is_absolute():
                wrapped = resolved.parent / wrapped
            if wrapped.is_file():
                implementation = wrapped.resolve()
    except (OSError, UnicodeError):
        pass
    source_lib = source / "lib"
    # Probe the library the selected installation ships. A source checkout has
    # no built XS module by default; installed packages may ship one alongside
    # an otherwise byte-for-byte source copy.
    selected_lib = source_lib
    if not (source / "bin/lcov").resolve() == resolved:
        for parent in (resolved.parent, *resolved.parents):
            for candidate in (parent / "lib/lcov", parent / "libexec/../lib/lcov"):
                if (candidate / "lcovutil.pm").is_file():
                    selected_lib = candidate.resolve()
                    break
            if selected_lib != source_lib:
                break
    library = selected_lib / "lcovutil.pm"
    env = dict(os.environ)
    old_perllib = env.get("PERL5LIB", "")
    env["PERL5LIB"] = os.pathsep.join(x for x in (str(selected_lib), old_perllib) if x)

    commit = subprocess.run(["git", "-C", str(source), "rev-parse", "HEAD"], capture_output=True, text=True)
    tracked = {}
    changed = []
    if commit.returncode == 0:
        listing = subprocess.run(["git", "-C", str(source), "ls-files", "-z"], capture_output=True)
        if listing.returncode == 0:
            for raw in listing.stdout.split(b"\0"):
                if not raw:
                    continue
                name = os.fsdecode(raw)
                path = source / name
                if not path.is_file():
                    changed.append(name)
                    continue
                digest = sha(path)
                tracked[name] = digest
                blob = subprocess.run(["git", "-C", str(source), "show", f"HEAD:{name}"], capture_output=True)
                if blob.returncode or hashlib.sha256(blob.stdout).hexdigest() != digest:
                    changed.append(name)

    version = run([str(executable), "--version"], env)
    # Import the module so LCOV's own loader runs. Requiring the filename alone
    # skips its normal XS initialization and gives a false empty value.
    probe = ["perl", "-I", str(selected_lib), "-Mlcovutil", "-e",
             "print $lcovutil::XS_LOADED, \"\\n\"; print $lcovutil::XS_LOAD_ERROR"]
    xs_env = dict(env)
    xs_env.pop("LCOV_PURE_PERL", None)
    pure_env = dict(env, LCOV_PURE_PERL="1")
    xs = run(probe, xs_env)
    pure = run(probe, pure_env)
    xs_files = []
    xs_dir = selected_lib / "LcovUtil" / "blib" / "arch" / "auto" / "LcovUtil"
    if xs_dir.is_dir():
        xs_files = [{"path": str(p), "sha256": sha(p)} for p in sorted(xs_dir.iterdir()) if p.is_file()]

    package_lib_files = [library, selected_lib / "LcovUtil" / "LcovUtil.pm",
                         selected_lib / "LcovUtil" / "blib" / "lib" / "LcovUtil.pm"]
    selected_library_hashes = {str(p): sha(p) for p in package_lib_files if p.is_file()}
    source_xs_loader = tracked.get("lib/LcovUtil/LcovUtil.pm")
    selected_xs_loader = selected_library_hashes.get(str(selected_lib / "LcovUtil" / "LcovUtil.pm"))
    selected_xs_loader = selected_xs_loader or selected_library_hashes.get(
        str(selected_lib / "LcovUtil" / "blib" / "lib" / "LcovUtil.pm"))

    source_bin_hash = tracked.get("bin/lcov")
    source_lib_hash = tracked.get("lib/lcovutil.pm")
    observed_bin_hash = sha(resolved) if resolved.is_file() else None
    observed_implementation_hash = sha(implementation) if implementation.is_file() else None
    observed_lib_hash = sha(library) if library.is_file() else None
    perl_version = run(["perl", "-v"], env)
    result = {
        "pinned_commit": PINNED_COMMIT,
        "source_root": str(source),
        "source_commit": commit.stdout.strip() if commit.returncode == 0 else None,
        "source_tree_matches_pin": commit.returncode == 0 and commit.stdout.strip() == PINNED_COMMIT and not changed,
        "tracked_file_count": len(tracked),
        "tracked_source_sha256": tracked,
        "tracked_files_differ_from_commit": changed,
        "executable_argument": str(executable),
        "resolved_executable": str(resolved),
        "implementation_executable": str(implementation),
        "version": version["stdout"],
        "version_probe": version,
        "source_executable_sha256": source_bin_hash,
        "selected_executable_sha256": observed_bin_hash,
        "implementation_executable_sha256": observed_implementation_hash,
        "source_library_sha256": source_lib_hash,
        "selected_source_library_sha256": observed_lib_hash,
        "selected_library_hashes": selected_library_hashes,
        "selected_library": str(selected_lib),
        "package_deviations": {
            "executable_matches_source": observed_bin_hash is not None and observed_bin_hash == source_bin_hash,
            "implementation_matches_source": observed_implementation_hash is not None and observed_implementation_hash == source_bin_hash,
            "library_matches_source": observed_lib_hash is not None and observed_lib_hash == source_lib_hash,
            "xs_loader_matches_source": selected_xs_loader is not None and selected_xs_loader == source_xs_loader,
        },
        "perl_runtime": perl_version["stdout"],
        "perl_environment": {"PERL5LIB": env.get("PERL5LIB", ""),
                             "LCOV_PURE_PERL": os.environ.get("LCOV_PURE_PERL"),
                             "PERL5OPT": os.environ.get("PERL5OPT", "")},
        "backend_probes": {"xs": xs, "pure_perl": pure,
                           "xs_binary_hashes": xs_files,
                           "verified": xs["status"] == 0 and xs["stdout"].splitlines()[:1] == ["1"]
                                       and pure["status"] == 0 and pure["stdout"].splitlines()[:1] == ["0"]},
    }
    print(json.dumps(result, indent=2))
    if args.strict and (not result["source_tree_matches_pin"] or version["status"] != 0
                        or "2.6-0" not in version["stdout"]
                        or not result["backend_probes"]["verified"]):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
