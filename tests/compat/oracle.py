#!/usr/bin/env python3
"""Differential LCOV 2.6 characterization runner; see CONTRACT.md."""
import argparse
import glob
import gzip
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_SOURCE = next((p / ".cache/lcov-compat-implementation/oracle-source"
                       for p in (ROOT, *ROOT.parents)
                       if (p / ".cache/lcov-compat-implementation/oracle-source").is_dir()),
                      ROOT / ".cache/lcov-compat-implementation/oracle-source")
TIMEOUT = 30


def digest(data):
    return hashlib.sha256(data).hexdigest()


def normalized(data):
    """Normalize only line endings and unordered record families within sections."""
    text = data.decode("utf-8", "surrogateescape").replace("\r\n", "\n").replace("\r", "\n")
    output, section = [], []
    for line in text.splitlines():
        if line == "end_of_record":
            lead = [x for x in section if x.startswith(("TN:", "SF:", "VER:"))]
            funcs = [x for x in section if x.startswith(("FN:", "FNDA:", "FNL:", "FNA:"))]
            rest = sorted(x for x in section if x not in lead and x not in funcs)
            output.append(tuple(lead + funcs + rest))
            section = []
        else:
            section.append(line)
    if section:
        output.append(tuple(section))
    return tuple(output)


def remove(path):
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def run(command, cwd, env, timeout=TIMEOUT):
    start = time.monotonic()
    try:
        p = subprocess.run(command, cwd=cwd, env=env, capture_output=True, timeout=timeout)
        return {"status": p.returncode, "stdout": p.stdout, "stderr": p.stderr,
                "timed_out": False, "duration_seconds": round(time.monotonic() - start, 4)}
    except subprocess.TimeoutExpired as e:
        return {"status": None, "stdout": e.stdout or b"", "stderr": e.stderr or b"",
                "timed_out": True, "duration_seconds": round(time.monotonic() - start, 4)}
    except OSError as e:
        return {"status": None, "stdout": b"", "stderr": str(e).encode(),
                "timed_out": False, "duration_seconds": round(time.monotonic() - start, 4)}


def record(result):
    return {"status": result["status"], "timed_out": result["timed_out"],
            "duration_seconds": result["duration_seconds"],
            "stdout": result["stdout"].decode("utf-8", "surrogateescape"),
            "stdout_sha256": digest(result["stdout"]),
            "stderr": result["stderr"].decode("utf-8", "surrogateescape"),
            "stderr_sha256": digest(result["stderr"])}


def source_text(lines=16):
    return "\n".join(f"int sample_{i} = {i};" for i in range(1, lines + 1)) + "\n"


def fixtures(directory):
    src = directory / "sample.c"
    src.write_text(source_text())
    other = directory / "other.c"
    other.write_text("int other = 1;\n")
    bad_source = directory / "missing.c"
    srcname = str(src.resolve())
    othername = str(other.resolve())
    missing = str(bad_source.resolve())
    rows = {
        "one.info": f"TN:alpha\nSF:{srcname}\nFN:1,sample\nFNDA:1,sample\nDA:1,1\nDA:2,0\nend_of_record\n",
        "two.info": f"TN:beta\nSF:{srcname}\nFN:1,sample\nFNDA:2,sample\nDA:1,2\nDA:2,1\nend_of_record\n",
        "uncovered.info": f"TN:alpha\nSF:{srcname}\nDA:1,0\nend_of_record\n",
        "true-empty.info": f"TN:alpha\nSF:{str((directory/'blank.c').resolve())}\nDA:1,1\nend_of_record\n",
        "branches-a.info": f"TN:alpha\nSF:{srcname}\nDA:1,1\nDA:2,1\nBRDA:2,7,3,1\nBRDA:2,7,8,0\nend_of_record\n",
        "branches-b.info": f"TN:alpha\nSF:{srcname}\nDA:1,1\nDA:2,1\nBRDA:2,91,3,2\nBRDA:2,91,8,4\nend_of_record\n",
        "branch-duplicate.info": f"TN:alpha\nSF:{srcname}\nDA:1,1\nDA:2,1\nBRDA:2,7,3,1\nBRDA:2,7,3,4\nend_of_record\n",
        "branch-absent.info": f"TN:alpha\nSF:{srcname}\nDA:1,1\nDA:2,0\nBRDA:2,7,3,-\nBRDA:2,7,8,-\nend_of_record\n",
        "branch-inconsistent.info": f"TN:alpha\nSF:{srcname}\nDA:1,1\nBRDA:2,7,3,1\nend_of_record\n",
        "functions.info": f"TN:alpha\nSF:{srcname}\nDA:1,1\nDA:3,6\nDA:7,6\nFN:1,legacy\nFNDA:2,legacy\nFNL:0,3,7\nFNA:0,2,first_alias\nFNA:0,4,second_alias\nend_of_record\n",
        "mcdc-a.info": f"TN:alpha\nSF:{srcname}\nDA:10,1\nMCDC:10,2,f,0,0,enable\nMCDC:10,2,t,1,0,enable\nend_of_record\n",
        "mcdc-bad.info": f"TN:alpha\nSF:{srcname}\nDA:10,1\nMCDC:bad\nend_of_record\n",
        "checksum-a.info": f"TN:alpha\nSF:{srcname}\nDA:1,1,aaaa\nend_of_record\n",
        "checksum-b.info": f"TN:alpha\nSF:{srcname}\nDA:1,2,bbbb\nend_of_record\n",
        "source-missing.info": f"TN:alpha\nSF:{missing}\nDA:1,1\nend_of_record\n",
        "version-bad.info": f"TN:alpha\nSF:{srcname}\nVER:9.9\nDA:1,1\nend_of_record\n",
        "malformed-da.info": f"TN:alpha\nSF:{srcname}\nDA:x,1\nend_of_record\n",
        "malformed-branch.info": f"TN:alpha\nSF:{srcname}\nDA:2,1\nBRDA:bad\nend_of_record\n",
        "malformed-function.info": f"TN:alpha\nSF:{srcname}\nFN:bad\nend_of_record\n",
        "malformed-mcdc.info": f"TN:alpha\nSF:{srcname}\nDA:10,1\nMCDC:bad\nend_of_record\n",
        "other.info": f"TN:alpha\nSF:{othername}\nDA:1,1\nend_of_record\n",
    }
    for name, content in rows.items():
        (directory / name).write_text(content)
    (directory / "blank.c").write_bytes(b"")
    (directory / "nested").mkdir(exist_ok=True)
    (directory / "nested" / "inner.info").write_text(rows["one.info"])
    (directory / "shard-a.info").write_text(rows["one.info"])
    (directory / "shard-b.info").write_text(rows["two.info"])
    (directory / "shard-x.info").write_text(rows["other.info"])
    (directory / "bracket-1.info").write_text(rows["one.info"])
    (directory / "bracket-a.info").write_text(rows["other.info"])
    return src


# (name, argv options, expected status). Each required row is asserted against
# both verified LCOV backends before candidate comparison is considered.
CASES = [
    ("tn-split", ["--no-function-coverage", "-a", "one.info", "-a", "two.info"], 0),
    ("duplicate-input", ["--no-function-coverage", "-a", "one.info", "-a", "one.info"], 0),
    ("true-empty-source", ["--no-function-coverage", "-a", "true-empty.info"], 0),
    ("uncovered-line", ["--no-function-coverage", "-a", "uncovered.info"], 0),
    ("branch-canonical", ["--branch-coverage", "--no-function-coverage", "-a", "branches-a.info", "-a", "branches-b.info"], 0),
    ("branch-duplicate-signature", ["--branch-coverage", "--no-function-coverage", "-a", "branch-duplicate.info"], 0),
    ("branch-strict-inconsistent", ["--rc", "check_data_consistency=1", "--branch-coverage", "--no-function-coverage", "-a", "branch-inconsistent.info"], 1),
    ("branch-recovery-inconsistent", ["--ignore-errors", "inconsistent", "--branch-coverage", "--no-function-coverage", "-a", "branch-inconsistent.info"], 0),
    ("branch-absent-counts", ["--branch-coverage", "--ignore-errors", "inconsistent", "--no-function-coverage", "-a", "branch-absent.info"], 0),
    ("function-alias-group-endline", ["--function-coverage", "--no-branch-coverage", "-a", "functions.info"], 0),
    ("mcdc-enabled", ["--mcdc-coverage", "--no-function-coverage", "--no-branch-coverage", "-a", "mcdc-a.info"], 0),
    ("mcdc-strict-malformed", ["--mcdc-coverage", "--no-function-coverage", "-a", "malformed-mcdc.info"], 1),
    ("mcdc-recovery-malformed", ["--mcdc-coverage", "--ignore-errors", "format,empty", "--no-function-coverage", "-a", "malformed-mcdc.info"], 0),
    ("mcdc-disabled-malformed", ["--no-function-coverage", "-a", "malformed-mcdc.info"], 0),
    ("checksum-conflict", ["--checksum", "--no-function-coverage", "-a", "checksum-a.info", "-a", "checksum-b.info"], 1),
    ("checksum-source-error", ["--checksum", "--no-function-coverage", "-a", "source-missing.info"], 1),
    ("version-error", ["--checksum", "--no-function-coverage", "-a", "version-bad.info"], 1),
    ("defaults", ["-a", "one.info"], 0),
    ("toggle-precedence", ["--no-branch-coverage", "--branch-coverage", "--no-function-coverage", "-a", "branches-a.info"], 0),
    ("forget-names", ["--forget-test-names", "--no-function-coverage", "-a", "one.info", "-a", "two.info"], 0),
    ("include-source", ["--include", "*/sample.c", "--ignore-errors", "empty", "--no-function-coverage", "-a", "one.info", "-a", "other.info"], 0),
    ("exclude-source", ["--exclude", "*/sample.c", "--ignore-errors", "empty", "--no-function-coverage", "-a", "one.info", "-a", "other.info"], 0),
    ("rc-enable-branch", ["--rc", "branch_coverage=1", "--no-function-coverage", "-a", "branches-a.info"], 0),
    ("explicit-config-precedence", ["--rc", "branch_coverage=0", "--branch-coverage", "--no-function-coverage", "-a", "branches-a.info"], 0),
    ("glob-flat", ["--no-function-coverage", "-a", "shard-*.info"], 0),
    ("glob-bracket", ["--no-function-coverage", "-a", "bracket-[1].info"], 0),
    ("glob-nested", ["--no-function-coverage", "-a", "nested/*.info"], 0),
    ("stdout-output", ["--no-function-coverage", "-a", "one.info"], 0, "stdout"),
    ("gzip-output", ["--no-function-coverage", "-a", "one.info"], 0, "gzip"),
    ("truncated-gzip-input", ["--no-function-coverage", "-a", "truncated.info.gz"], 1),
    ("malformed-da", ["--no-function-coverage", "-a", "malformed-da.info"], 1),
    ("malformed-branch", ["--branch-coverage", "--no-function-coverage", "-a", "malformed-branch.info"], 1),
    ("malformed-function", ["--function-coverage", "-a", "malformed-function.info"], 1),
]


def make_inputs(directory):
    fixtures(directory)
    (directory / "truncated.info.gz").write_bytes(gzip.compress(b"TN:x\nSF:x.c\nDA:1,1\nend_of_record\n")[:-8])


def matched_input_names(opts, directory):
    names = set()
    for i, option in enumerate(opts[:-1]):
        if option not in ("-a", "--add-tracefile"):
            continue
        names.update(glob.glob(opts[i + 1], root_dir=directory, recursive=True))
    return sorted(name for name in names if (directory / name).is_file())


def summarize_html(result):
    text = (result["stdout"] + result["stderr"]).decode("utf-8", "replace")
    found = re.findall(r"((?:lines|functions|branches|MC/DC)\.*:\s*[^\n]+)", text, re.I)
    return found


def artifact_hashes(directory):
    if not directory.is_dir():
        return {}
    return {str(path.relative_to(directory)): digest(path.read_bytes())
            for path in sorted(directory.rglob("*")) if path.is_file()}


def stdout_trace(data):
    """LCOV writes trace records before its human-readable status text."""
    lines = data.splitlines(keepends=True)
    end = next((i for i, line in enumerate(lines) if line.strip() == b"end_of_record"), None)
    return b"".join(lines[:end + 1]) if end is not None else data


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--binary", help="candidate binary (resolved to an absolute path)")
    ap.add_argument("--oracle-only", action="store_true")
    ap.add_argument("--workdir", default=".cache/lcov-compat-oracle")
    ap.add_argument("--lcov", help="oracle executable; defaults to PATH lcov, then source-root/bin/lcov")
    ap.add_argument("--source-root", default=str(DEFAULT_SOURCE))
    ap.add_argument("--genhtml", help="genhtml executable; defaults beside selected lcov or on PATH")
    ap.add_argument("--timeout", type=int, default=TIMEOUT)
    args = ap.parse_args()
    if not args.oracle_only and not args.binary:
        ap.error("use --binary PATH or --oracle-only")
    if args.timeout < 1:
        ap.error("--timeout must be positive")
    source = pathlib.Path(args.source_root).resolve()
    oracle_exe = pathlib.Path(args.lcov or shutil.which("lcov") or source / "bin/lcov").expanduser().resolve()
    if not oracle_exe.is_file():
        ap.error(f"LCOV executable not found: {oracle_exe}")
    candidate = pathlib.Path(args.binary).expanduser().resolve() if args.binary else None
    genhtml = pathlib.Path(args.genhtml).expanduser().resolve() if args.genhtml else None
    if genhtml is None:
        sibling = oracle_exe.parent / "genhtml"
        genhtml = sibling if sibling.exists() else pathlib.Path(shutil.which("genhtml") or "genhtml").resolve()
    identity_cmd = [sys.executable, str(ROOT / "tools/lcov-oracle-identity.py"),
                    "--lcov", str(oracle_exe), "--source-root", str(source), "--strict"]
    identity_run = subprocess.run(identity_cmd, cwd=ROOT, capture_output=True, text=True)
    if identity_run.returncode:
        sys.stderr.write(identity_run.stdout + identity_run.stderr)
        print("oracle identity failed; required backends were not verified", file=sys.stderr)
        return 2
    identity = json.loads(identity_run.stdout)

    workdir = pathlib.Path(args.workdir)
    if not workdir.is_absolute():
        workdir = ROOT / workdir
    workdir.mkdir(parents=True, exist_ok=True)
    manifest = {"identity": identity, "source_root": str(source), "oracle_executable": str(oracle_exe),
                "candidate_executable": str(candidate) if candidate else None,
                "genhtml_executable": str(genhtml), "timeout_seconds": args.timeout,
                "required_cases": len(CASES), "cases": []}
    failures = 0
    backends = (("xs", None), ("pure-perl", "1"))
    for case in CASES:
        name, opts, expected = case[:3]
        mode = case[3] if len(case) == 4 else "file"
        case_dir = workdir / name
        case_dir.mkdir(parents=True, exist_ok=True)
        make_inputs(case_dir)
        config = case_dir / "lcovrc"
        config.write_text("branch_coverage=0\n" if name == "explicit-config-precedence" else "# Explicit config for reproducibility.\n")
        source_file = case_dir / "sample.c"
        input_names = matched_input_names(opts, case_dir)
        source_refs = set()
        for input_name in input_names:
            try:
                data = (case_dir / input_name).read_bytes()
                if input_name.endswith(".gz"):
                    data = gzip.decompress(data)
                for line in data.decode("utf-8", "surrogateescape").splitlines():
                    if line.startswith("SF:"):
                        source_refs.add(line[3:])
            except (OSError, EOFError):
                continue
        source_records = {}
        for source_ref in sorted(source_refs):
            source_path = pathlib.Path(source_ref)
            source_records[source_ref] = {"exists": source_path.is_file(),
                                         "sha256": digest(source_path.read_bytes()) if source_path.is_file() else None}
        entry = {"name": name, "required": True, "expected_oracle_status": expected,
                 "config": str(config), "config_sha256": digest(config.read_bytes()),
                 "source": str(source_file), "source_sha256": digest(source_file.read_bytes()),
                 "source_hashes": {str(p): digest(p.read_bytes()) for p in sorted(case_dir.glob("*.c"))},
                 "source_references": source_records,
                 "mode": mode, "backends": []}
        for backend, pure in backends:
            env = dict(os.environ)
            if pure:
                env["LCOV_PURE_PERL"] = pure
            else:
                env.pop("LCOV_PURE_PERL", None)
            ext = ".gz" if mode == "gzip" else ".info"
            oracle_out = case_dir / f"oracle-{backend}{ext}"
            oracle_log = case_dir / f"oracle-{backend}.log"
            profile = case_dir / f"oracle-{backend}.profile.json"
            remove(oracle_out)
            remove(profile)
            argv = [str(oracle_exe), "--quiet", "--config-file", str(config), "--profile", str(profile), *opts]
            if mode == "stdout":
                argv += ["-o", "/dev/stdout"]
            else:
                argv += ["-o", str(oracle_out)]
            used = input_names
            input_hashes = {name: digest((case_dir / name).read_bytes()) for name in used}
            oracle = run(argv, case_dir, env, args.timeout)
            oracle_log.write_bytes(oracle["stdout"] + b"\n--- STDERR ---\n" + oracle["stderr"])
            profile_data = None
            if profile.is_file():
                try:
                    profile_data = json.loads(profile.read_text())
                except (OSError, json.JSONDecodeError):
                    profile_data = None
            expected_xs = 0 if pure else 1
            observed_xs = profile_data.get("config", {}).get("xs") if profile_data else None
            outbytes = oracle_out.read_bytes() if oracle_out.is_file() else b""
            if mode == "gzip" and oracle["status"] == 0:
                try:
                    semantic_oracle = gzip.decompress(outbytes)
                except OSError:
                    semantic_oracle = b""
            else:
                semantic_oracle = stdout_trace(oracle["stdout"]) if mode == "stdout" else outbytes
            backend_row = {"name": backend, "oracle_argv": argv, "oracle": record(oracle),
                           "oracle_log": str(oracle_log), "oracle_output_path": str(oracle_out),
                           "profile_path": str(profile), "profile_sha256": digest(profile.read_bytes()) if profile.is_file() else None,
                           "observed_xs_backend": observed_xs, "expected_xs_backend": expected_xs,
                           "backend_confirmed_for_invocation": observed_xs == expected_xs,
                           "oracle_output_sha256": digest(outbytes), "semantic_output_sha256": digest(semantic_oracle),
                           "oracle_output": semantic_oracle.decode("utf-8", "surrogateescape") if oracle["status"] == 0 else None,
                           "oracle_input_hashes": input_hashes,
                           "oracle_expected_status_match": oracle["status"] == expected and not oracle["timed_out"]}
            if not backend_row["oracle_expected_status_match"] or not backend_row["backend_confirmed_for_invocation"]:
                failures += 1
                print(f"FAIL oracle {backend}/{name}: status={oracle['status']} xs={observed_xs} expected_xs={expected_xs}", file=sys.stderr)
            if expected == 0:
                html_dir = case_dir / f"html-{backend}"
                remove(html_dir)
                html_argv = [str(genhtml), "--ignore-errors", "source", "--output-directory", str(html_dir),
                            str(oracle_out) if mode != "stdout" else str((case_dir / "stdout-source.info"))]
                if mode == "stdout":
                    (case_dir / "stdout-source.info").write_bytes(semantic_oracle)
                html = run(html_argv, case_dir, env, args.timeout)
                backend_row["genhtml"] = {"argv": html_argv, **record(html), "coverage_statistics": summarize_html(html),
                                          "html_artifact_hashes": artifact_hashes(html_dir)}
                if html["status"] != 0 or html["timed_out"] or not any(x.lower().startswith("lines") for x in backend_row["genhtml"]["coverage_statistics"]):
                    failures += 1
                    print(f"FAIL genhtml {backend}/{name}: status={html['status']} stats={backend_row['genhtml']['coverage_statistics']}", file=sys.stderr)
            if candidate:
                cand_out = case_dir / f"candidate-{backend}{ext}"
                cand_log = case_dir / f"candidate-{backend}.log"
                remove(cand_out)
                cargv = [str(candidate), "--lcov-compat=2.6", "--quiet", "--config-file", str(config), *opts]
                if mode == "stdout":
                    pass  # Omitted output is the profile stdout contract.
                else:
                    cargv += ["-o", str(cand_out)]
                cr = run(cargv, case_dir, env, args.timeout)
                cand_log.write_bytes(cr["stdout"] + b"\n--- STDERR ---\n" + cr["stderr"])
                cb = cand_out.read_bytes() if cand_out.is_file() else b""
                if mode == "gzip" and cr["status"] == 0:
                    try:
                        sem_cand = gzip.decompress(cb)
                    except OSError:
                        sem_cand = b""
                else:
                    sem_cand = stdout_trace(cr["stdout"]) if mode == "stdout" else cb
                same_failure_surface = (oracle_out.exists() == cand_out.exists()) and (oracle["stdout"] == cr["stdout"])
                ok = (cr["status"] == oracle["status"] and not cr["timed_out"] and
                      ((expected != 0 and same_failure_surface) or
                       (expected == 0 and normalized(sem_cand) == normalized(semantic_oracle))))
                cand_row = {"argv": cargv, "result": record(cr), "log": str(cand_log),
                            "output_path": str(cand_out), "output_sha256": digest(cb),
                            "semantic_output_sha256": digest(sem_cand),
                            "output": sem_cand.decode("utf-8", "surrogateescape") if cr["status"] == 0 else None,
                            "failure_surface_match": same_failure_surface,
                            "semantic_match": expected != 0 or normalized(sem_cand) == normalized(semantic_oracle),
                            "match": ok}
                if expected == 0:
                    html_dir = case_dir / f"html-candidate-{backend}"
                    remove(html_dir)
                    html_argv = [str(genhtml), "--ignore-errors", "source", "--output-directory", str(html_dir),
                                 str(cand_out) if mode != "stdout" else str((case_dir / "candidate-stdout-source.info"))]
                    if mode == "stdout":
                        (case_dir / "candidate-stdout-source.info").write_bytes(sem_cand)
                    html = run(html_argv, case_dir, env, args.timeout)
                    stats = summarize_html(html)
                    cand_row["genhtml"] = {"argv": html_argv, **record(html), "coverage_statistics": stats,
                                            "html_artifact_hashes": artifact_hashes(html_dir)}
                    oracle_stats = backend_row["genhtml"]["coverage_statistics"]
                    cand_row["genhtml_match"] = html["status"] == 0 and any(x.lower().startswith("lines") for x in stats) and stats == oracle_stats
                    if not cand_row["genhtml_match"]:
                        failures += 1
                        print(f"FAIL candidate genhtml {backend}/{name}", file=sys.stderr)
                if not ok:
                    failures += 1
                    print(f"FAIL candidate {backend}/{name}: oracle={oracle['status']}, candidate={cr['status']}", file=sys.stderr)
                backend_row["candidate"] = cand_row
            entry["backends"].append(backend_row)
        manifest["cases"].append(entry)
    manifest["failures"] = failures
    manifest_path = workdir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({"workdir": str(workdir), "required_cases": len(CASES), "backend_runs": len(CASES) * 2,
                      "failures": failures, "manifest": str(manifest_path)}, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
