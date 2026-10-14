#!/usr/bin/env python3
"""Differential LCOV 2.6 characterization runner; see CONTRACT.md."""
import argparse
import base64
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
        "count-max.info": f"TN:alpha\nSF:{srcname}\nDA:1,9223372036854775807\nDA:2,1\nFN:1,sample\nFNDA:9223372036854775807,sample\nBRDA:2,1,0,9223372036854775807\nend_of_record\n",
        "count-overflow-a.info": f"TN:alpha\nSF:{srcname}\nDA:1,9223372036854775807\nend_of_record\n",
        "count-overflow-b.info": f"TN:alpha\nSF:{srcname}\nDA:1,1\nend_of_record\n",
        "count-negative.info": f"TN:alpha\nSF:{srcname}\nDA:1,-1\nend_of_record\n",
        "count-fraction.info": f"TN:alpha\nSF:{srcname}\nDA:1,1.9\nend_of_record\n",
        "checksum-valid.info": f"TN:alpha\nSF:{srcname}\nDA:1,1,PLACEHOLDER\nend_of_record\n",
        "checksum-mismatch.info": f"TN:alpha\nSF:{srcname}\nDA:1,1,AAAA\nend_of_record\n",
        "checksum-missing.info": f"TN:alpha\nSF:{srcname}\nDA:1,1\nend_of_record\n",
        "function-conflict-a.info": f"TN:alpha\nSF:{srcname}\nDA:1,1\nFN:1,3,dupe\nFNDA:1,dupe\nend_of_record\n",
        "function-conflict-b.info": f"TN:beta\nSF:{srcname}\nDA:1,1\nFN:1,4,dupe\nFNDA:2,dupe\nend_of_record\n",
        "mcdc-conflict-a.info": f"TN:alpha\nSF:{srcname}\nDA:10,1\nMCDC:10,2,t,1,0,first\nend_of_record\n",
        "mcdc-conflict-b.info": f"TN:beta\nSF:{srcname}\nDA:10,1\nMCDC:10,2,t,2,0,second\nend_of_record\n",
        "version-a.info": f"TN:alpha\nSF:{srcname}\nVER:1.0\nDA:1,1\nend_of_record\n",
        "version-b.info": f"TN:beta\nSF:{srcname}\nVER:2.0\nDA:1,1\nend_of_record\n",
        "malformed-branch-disabled.info": f"TN:alpha\nSF:{srcname}\nBRDA:bad\nDA:1,1\nend_of_record\n",
        "malformed-function-disabled.info": f"TN:alpha\nSF:{srcname}\nFN:bad\nDA:1,1\nend_of_record\n",
        "mcdc-layout.info": f"TN:alpha\nSF:{srcname}\nDA:10,1\nMCDC:10,2,t,1,0,A\nMCDC:10,2,f,0,0,A\nMCDC:10,U2,t,0,1,B\nMCDC:10,U2,f,1,1,B\nend_of_record\n",
        "mcdc-duplicate-a.info": f"TN:alpha\nSF:{srcname}\nDA:10,1\nMCDC:10,2,t,2,0,A\nend_of_record\n",
        "mcdc-duplicate-b.info": f"TN:alpha\nSF:{srcname}\nDA:10,1\nMCDC:10,2,f,1,0,A\nMCDC:10,2,t,1,0,A\nend_of_record\n",
        "checksum-cross-a.info": f"TN:alpha\nSF:{srcname}\nDA:1,1,{base64.b64encode(hashlib.md5(b'int sample_1 = 1;').digest()).decode().rstrip('=')}\nend_of_record\n",
        "checksum-cross-b.info": f"TN:beta\nSF:{srcname}\nDA:1,2\nend_of_record\n",
        "branch-only-recovery.info": f"TN:alpha\nSF:{srcname}\nDA:2,0\nBRDA:2,4,0,2\nBRDA:2,4,1,4\nend_of_record\n",
        "derive-cross-a.info": f"TN:alpha\nSF:{str((directory/'derive.cpp').resolve())}\nDA:3,1\nFN:3,derive_me\nFNDA:1,derive_me\nend_of_record\n",
        "derive-cross-b.info": f"TN:beta\nSF:{str((directory/'derive.cpp').resolve())}\nDA:7,0\nend_of_record\n",
        "lambda-cpp.info": f"TN:alpha\nSF:{str((directory/'lambda.cpp').resolve())}\nDA:2,1\nFN:2,outer{{lambda(0)}}\nFNDA:1,outer{{lambda(0)}}\nend_of_record\n",
        "lambda-python.info": f"TN:alpha\nSF:{str((directory/'lambda.py').resolve())}\nDA:2,1\nFN:2,outer.<locals>.inner\nFNDA:1,outer.<locals>.inner\nend_of_record\n",
        "aliases-cross-a.info": f"TN:alpha\nSF:{srcname}\nDA:3,1\nFN:3,short_name\nFNDA:2,short_name\nend_of_record\n",
        "aliases-cross-b.info": f"TN:beta\nSF:{srcname}\nDA:3,1\nFN:3,long_name\nFNDA:3,long_name\nend_of_record\n",
        "fn-comma.info": f"TN:alpha\nSF:{srcname}\nDA:1,1\nFN:1,name,with,commas\nFNDA:2,name,with,commas\nend_of_record\n",
        "modern-reused-id-a.info": f"TN:alpha\nSF:{srcname}\nDA:1,1\nFNL:9,1,1\nFNA:9,2,first_alias\nFNL:3,1,1\nFNA:3,1,third_alias\nend_of_record\n",
        "modern-reused-id-b.info": f"TN:beta\nSF:{srcname}\nDA:1,1\nFNL:3,1,1\nFNA:3,4,reused_alias\nFNL:9,1,1\nFNA:9,3,last_alias\nend_of_record\n",
        "branch-checksum-conflict-a.info": f"TN:alpha\nSF:{srcname}\nDA:1,1,aaaa\nBRDA:1,7,0,1\nend_of_record\n",
        "branch-checksum-conflict-b.info": f"TN:beta\nSF:{srcname}\nDA:1,2,bbbb\nBRDA:1,9,0,2\nend_of_record\n",
        "branch-duplicate-da-max.info": f"TN:alpha\nSF:{srcname}\nDA:1,9223372036854775807\nDA:1,9223372036854775807\nDA:1,9223372036854775807\nBRDA:1,7,0,1\nend_of_record\n",
        "fractional-fna.info": f"TN:alpha\nSF:{srcname}\nFNL:7,1,1\nFNA:7,1.5,fractional\nDA:1,2\nend_of_record\n",
        "fractional-fna-collector.info": f"TN:alpha\nSF:{srcname}\nFNL:7,1,1\nFNA:7,1.5,fractional\nBRDA:1,7,0,1\nDA:1,2\nend_of_record\n",
        "negative-fna.info": f"TN:alpha\nSF:{srcname}\nFNL:7,1,1\nFNA:7,-1,negative\nDA:1,0\nend_of_record\n",
        "same-start-different-end.info": f"TN:alpha\nSF:{srcname}\nDA:2,1\nDA:5,1\nFNL:0,2,5\nFNA:0,1,short_end\nFNL:1,2,7\nFNA:1,1,long_end\nend_of_record\n",
        "modern-fna-line-mismatch-zero.info": f"TN:alpha\nSF:{srcname}\nDA:2,1\nFNL:7,1,3\nFNA:7,0,fn\nend_of_record\n",
        "modern-fna-line-mismatch-positive.info": f"TN:alpha\nSF:{srcname}\nDA:2,0\nFNL:7,1,3\nFNA:7,1,fn\nend_of_record\n",
        "modern-fna-overlap-end-range.info": f"TN:alpha\nSF:{srcname}\nDA:2,1\nFNL:0,1,3\nFNA:0,0,first\nFNL:1,2,3\nFNA:1,0,second\nend_of_record\n",
        "modern-fna-aliases-line-match.info": f"TN:alpha\nSF:{srcname}\nDA:2,1\nFNL:7,1,3\nFNA:7,0,fn\nFNA:7,1,alias\nend_of_record\n",
        "lambda-modern-fna-line-mismatch.info": f"TN:alpha\nSF:{srcname}\nDA:1,1\nFNL:7,1,3\nFNA:7,0,{{lambda()#1}}\nend_of_record\n",
        "fn-zero-cross-testcase-same-input.info": f"TN:a\nSF:{srcname}\nFN:1,zero_fn\nFNDA:0,zero_fn\nDA:1,0\nend_of_record\nTN:b\nSF:{srcname}\nDA:1,1\nend_of_record\n",
        "fn-hit-cross-testcase-same-input.info": f"TN:a\nSF:{srcname}\nFN:1,hit_fn\nFNDA:1,hit_fn\nDA:1,1\nend_of_record\nTN:b\nSF:{srcname}\nDA:1,0\nend_of_record\n",
        "fn-zero-cross-input.info": f"TN:a\nSF:{srcname}\nFN:1,zero_fn\nFNDA:0,zero_fn\nDA:1,0\nend_of_record\n",
        "fn-positive-later-input.info": f"TN:b\nSF:{srcname}\nDA:1,1\nend_of_record\n",
        "fn-explicit-beyond-last-da.info": f"TN:alpha\nSF:{srcname}\nFNL:7,4,6\nFNA:7,1,late_fn\nDA:1,1\nend_of_record\n",
        "fn-explicit-no-da.info": f"TN:alpha\nSF:{srcname}\nFNL:7,1,3\nFNA:7,0,empty_fn\nend_of_record\n",
        "fn-lambda-mixed-alias.info": f"TN:alpha\nSF:{str((directory/'lambda.c').resolve())}\nDA:1,1\nFNL:7,1,3\nFNA:7,0,{{lambda(0)}}\nFNA:7,0,ordinary_alias\nend_of_record\n",
        "fn-cpp-lambda-first-line.info": f"TN:alpha\nSF:{str((directory/'lambda.cpp').resolve())}\nDA:1,1\nDA:2,0\nFNL:7,1,2\nFNA:7,0,{{lambda()#1}}\nend_of_record\n",
        "fn-cpp-lambda-later-line.info": f"TN:alpha\nSF:{str((directory/'lambda.cpp').resolve())}\nDA:1,0\nDA:2,1\nFNL:7,1,2\nFNA:7,0,{{lambda()#1}}\nend_of_record\n",
        "fn-mangled-nested.info": f"TN:alpha\nSF:{srcname}\nDA:1,1\nFNL:7,1,3\nFNA:7,0,_ZZ4funcvENKUlvE_clEv\nend_of_record\n",
        "derive-source-wide-a.info": f"TN:a\nSF:{srcname}\nFN:1,foo\nFNDA:1,foo\nDA:1,1\nend_of_record\n",
        "derive-source-wide-b.info": f"TN:b\nSF:{srcname}\nDA:5,1\nend_of_record\n",
        "derive-source-wide-explicit-a.info": f"TN:a\nSF:{srcname}\nFNL:0,1,5\nFNA:0,1,foo\nDA:1,0\nend_of_record\n",
        "derive-source-wide-explicit-b.info": f"TN:b\nSF:{srcname}\nDA:5,1\nend_of_record\n",
        "missing-ends-a.info": f"TN:alpha\nSF:{srcname}\nDA:1,1\nFNL:4,1\nFNA:4,1,without_end\nend_of_record\n",
        "missing-ends-b.info": f"TN:beta\nSF:{srcname}\nDA:2,1\nFNL:8,2,3\nFNA:8,1,with_end\nend_of_record\n",
        "orphan-function-hit.info": f"TN:alpha\nSF:{srcname}\nDA:1,1\nFNDA:3,orphan_name\nend_of_record\n",
        "other.info": f"TN:alpha\nSF:{othername}\nDA:1,1\nend_of_record\n",
    }
    for name, content in rows.items():
        (directory / name).write_text(content)
    (directory / "blank.c").write_bytes(b"")
    (directory / "derive.cpp").write_text("\n" * 8)
    (directory / "lambda.cpp").write_text("\n" * 5)
    (directory / "lambda.c").write_text("\n" * 5)
    (directory / "lambda.py").write_text("\n" * 5)
    (directory / "checksum-valid.info").write_text(rows["checksum-valid.info"].replace(
        "PLACEHOLDER", base64.b64encode(hashlib.md5(b"int sample_1 = 1;").digest()).decode().rstrip("=")))
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
    ("integer-exact-above-f64", ["--no-function-coverage", "-a", "integer-exact.info"], 0),
    ("tn-split", ["--no-function-coverage", "-a", "one.info", "-a", "two.info"], 0),
    ("duplicate-input", ["--no-function-coverage", "-a", "one.info", "-a", "one.info"], 0),
    ("true-empty-source", ["--no-function-coverage", "-a", "true-empty.info"], 0),
    ("uncovered-line", ["--no-function-coverage", "-a", "uncovered.info"], 0),
    ("branch-canonical", ["--branch-coverage", "--no-function-coverage", "-a", "branches-a.info", "-a", "branches-b.info"], 0),
    ("branch-duplicate-da-max-saturation", ["--branch-coverage", "--no-function-coverage", "-a", "branch-duplicate-da-max.info"], 0),
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
    ("checksum-conflict-ignored-branch-collector", ["--branch-coverage", "--no-function-coverage", "-a", "branch-checksum-conflict-a.info", "-a", "branch-checksum-conflict-b.info"], 0),
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
    ("signed-max-count", ["--branch-coverage", "--function-coverage", "-a", "count-max.info"], 0),
    ("count-overflow-saturation", ["--no-function-coverage", "-a", "count-overflow-a.info", "-a", "count-overflow-b.info"], 0),
    ("negative-count-strict", ["--no-function-coverage", "-a", "count-negative.info"], 1),
    ("negative-count-recovery", ["--ignore-errors", "negative", "--no-function-coverage", "-a", "count-negative.info"], 0),
    ("fraction-count", ["--no-function-coverage", "-a", "count-fraction.info"], 0),
    ("negative-count-recovery-disabled-consistency", ["--ignore-errors", "negative", "--rc", "check_data_consistency=0", "--no-function-coverage", "-a", "count-negative.info"], 0),
    ("fractional-fna", ["-a", "fractional-fna.info"], 0),
    ("fractional-fna-collector", ["--branch-coverage", "-a", "fractional-fna-collector.info"], 0),
    ("negative-fna-recovery-disabled-consistency", ["--ignore-errors", "negative", "--rc", "check_data_consistency=0", "-a", "negative-fna.info"], 0),
    ("checksum-valid", ["--checksum", "--no-function-coverage", "-a", "checksum-valid.info"], 0),
    ("checksum-mismatch", ["--checksum", "--no-function-coverage", "-a", "checksum-mismatch.info"], 1),
    ("checksum-mismatch-recovery", ["--ignore-errors", "version", "--checksum", "--no-function-coverage", "-a", "checksum-mismatch.info"], 0),
    ("checksum-default-ignored", ["--no-function-coverage", "-a", "checksum-valid.info"], 0),
    ("checksum-no-flag-precedence", ["--checksum", "--no-checksum", "--no-function-coverage", "-a", "checksum-missing.info"], 0),
    ("function-conflict-strict", ["--function-coverage", "-a", "function-conflict-a.info", "-a", "function-conflict-b.info"], 0),
    ("mcdc-expression-strict", ["--mcdc-coverage", "--no-function-coverage", "-a", "mcdc-conflict-a.info", "-a", "mcdc-conflict-b.info"], 1),
    ("mcdc-expression-recovery", ["--ignore-errors", "inconsistent", "--mcdc-coverage", "--no-function-coverage", "-a", "mcdc-conflict-a.info", "-a", "mcdc-conflict-b.info"], 0),
    ("mcdc-layout-summary", ["--mcdc-coverage", "--no-function-coverage", "-a", "mcdc-layout.info"], 0),
    ("mcdc-duplicate-permuted", ["--mcdc-coverage", "--no-function-coverage", "-a", "mcdc-duplicate-b.info", "-a", "mcdc-duplicate-a.info"], 0),
    ("source-version-split", ["--no-function-coverage", "-a", "version-a.info", "-a", "version-b.info"], 1),
    ("source-version-recovery", ["--ignore-errors", "version", "--no-function-coverage", "-a", "version-a.info", "-a", "version-b.info"], 0),
    ("malformed-branch-disabled", ["--no-branch-coverage", "--no-function-coverage", "-a", "malformed-branch-disabled.info"], 0),
    ("malformed-function-disabled", ["--no-function-coverage", "-a", "malformed-function-disabled.info"], 0),
    ("consistency-enabled-success", ["--rc", "check_data_consistency=1", "--function-coverage", "-a", "one.info"], 0),
    ("derive-endline-disabled", ["--rc", "derive_function_end_line=0", "--function-coverage", "-a", "functions.info"], 0),
    ("checksum-cross-testcase", ["--ignore-errors", "version", "--checksum", "--no-function-coverage", "-a", "checksum-cross-a.info", "-a", "checksum-cross-b.info"], 0),
    ("branch-only-recovery-summary", ["--ignore-errors", "inconsistent", "--rc", "check_data_consistency=1", "--branch-coverage", "--no-function-coverage", "-a", "branch-only-recovery.info"], 0),
    ("derive-endline-cross-testcase", ["--function-coverage", "--rc", "derive_function_end_line=1", "-a", "derive-cross-a.info", "-a", "derive-cross-b.info"], 0),
    ("lambda-cpp-no-inference", ["--function-coverage", "--rc", "derive_function_end_line=1", "-a", "lambda-cpp.info"], 0),
    ("function-aliases-cross-testcase", ["--function-coverage", "-a", "aliases-cross-a.info", "-a", "aliases-cross-b.info"], 0),
    ("function-name-commas", ["--function-coverage", "-a", "fn-comma.info"], 0),
    ("modern-reused-id-input-order", ["--function-coverage", "-a", "modern-reused-id-a.info", "-a", "modern-reused-id-b.info"], 0),
    ("modern-alias-same-start-different-end", ["--function-coverage", "-a", "same-start-different-end.info"], 1),
    ("modern-alias-same-start-different-end-recovery", ["--function-coverage", "--ignore-errors", "inconsistent", "-a", "same-start-different-end.info"], 0),
    ("modern-fna-line-mismatch-zero", ["--function-coverage", "--rc", "check_data_consistency=1", "-a", "modern-fna-line-mismatch-zero.info"], 1),
    ("modern-fna-line-mismatch-positive", ["--function-coverage", "--rc", "check_data_consistency=1", "-a", "modern-fna-line-mismatch-positive.info"], 1),
    ("modern-fna-overlap-end-range", ["--function-coverage", "--rc", "check_data_consistency=1", "-a", "modern-fna-overlap-end-range.info"], 1),
    ("modern-fna-line-mismatch-zero-recovery", ["--function-coverage", "--rc", "check_data_consistency=1", "--ignore-errors", "inconsistent", "-a", "modern-fna-line-mismatch-zero.info"], 0),
    ("modern-fna-line-mismatch-positive-recovery", ["--function-coverage", "--rc", "check_data_consistency=1", "--ignore-errors", "inconsistent", "-a", "modern-fna-line-mismatch-positive.info"], 0),
    ("modern-fna-line-mismatch-zero-consistency-disabled", ["--function-coverage", "--rc", "check_data_consistency=0", "-a", "modern-fna-line-mismatch-zero.info"], 0),
    ("modern-fna-line-mismatch-positive-consistency-disabled", ["--function-coverage", "--rc", "check_data_consistency=0", "-a", "modern-fna-line-mismatch-positive.info"], 0),
    ("modern-fna-aliases-line-match", ["--function-coverage", "--rc", "check_data_consistency=1", "-a", "modern-fna-aliases-line-match.info"], 0),
    ("lambda-modern-fna-line-mismatch", ["--function-coverage", "--rc", "check_data_consistency=1", "-a", "lambda-modern-fna-line-mismatch.info"], 0),
    ("derive-endline-source-wide", ["--function-coverage", "--rc", "derive_function_end_line=1", "-a", "derive-source-wide-a.info", "-a", "derive-source-wide-b.info"], 0),
    ("modern-fna-source-wide-consistency", ["--function-coverage", "--rc", "check_data_consistency=1", "-a", "derive-source-wide-explicit-a.info", "-a", "derive-source-wide-explicit-b.info"], 1),
    ("fn-zero-line-hit-same-input-testcases", ["--function-coverage", "--rc", "check_data_consistency=1", "-a", "fn-zero-cross-testcase-same-input.info"], 1),
    ("fn-hit-line-zero-same-input-testcases", ["--function-coverage", "--rc", "check_data_consistency=1", "-a", "fn-hit-cross-testcase-same-input.info"], 0),
    ("fn-zero-line-hit-separate-inputs", ["--function-coverage", "--rc", "check_data_consistency=1", "-a", "fn-zero-cross-input.info", "-a", "fn-positive-later-input.info"], 1),
    ("fn-explicit-start-beyond-last-da", ["--function-coverage", "--rc", "check_data_consistency=1", "-a", "fn-explicit-beyond-last-da.info"], 1),
    ("fn-explicit-no-da-empty", ["--function-coverage", "--rc", "check_data_consistency=1", "--ignore-errors", "inconsistent,empty", "-a", "fn-explicit-no-da.info"], 0),
    ("fn-lambda-mixed-alias-not-exempt", ["--function-coverage", "--rc", "check_data_consistency=1", "-a", "fn-lambda-mixed-alias.info"], 1),
    ("fn-cpp-lambda-first-line-exempt", ["--function-coverage", "--rc", "check_data_consistency=1", "-a", "fn-cpp-lambda-first-line.info"], 0),
    ("fn-cpp-lambda-later-line-error", ["--function-coverage", "--rc", "check_data_consistency=1", "-a", "fn-cpp-lambda-later-line.info"], 1),
    ("fn-mangled-nested-not-lambda", ["--function-coverage", "--rc", "check_data_consistency=1", "-a", "fn-mangled-nested.info"], 1),
    ("modern-missing-end-cross-input", ["--function-coverage", "-a", "missing-ends-a.info", "-a", "missing-ends-b.info"], 0),
    ("orphan-function-hit-strict", ["--function-coverage", "-a", "orphan-function-hit.info"], 1),
    ("orphan-function-hit-recovery", ["--function-coverage", "--ignore-errors", "mismatch,empty", "-a", "orphan-function-hit.info"], 0),
]


def make_inputs(directory):
    fixtures(directory)
    (directory / "integer-exact.info").write_text(f"TN:alpha\nSF:{directory / 'sample.c'}\nDA:1,9007199254740993\nend_of_record\n")
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
    ap.add_argument("--case", action="append", help="run only this required case (repeatable)")
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

    selected_cases = [case for case in CASES if not args.case or case[0] in args.case]
    unknown_cases = sorted(set(args.case or ()) - {case[0] for case in CASES})
    if unknown_cases:
        ap.error("unknown --case: " + ", ".join(unknown_cases))

    workdir = pathlib.Path(args.workdir)
    if not workdir.is_absolute():
        workdir = ROOT / workdir
    workdir.mkdir(parents=True, exist_ok=True)
    manifest = {"identity": identity, "source_root": str(source), "oracle_executable": str(oracle_exe),
                "candidate_executable": str(candidate) if candidate else None,
                "genhtml_executable": str(genhtml), "timeout_seconds": args.timeout,
                "required_cases": len(selected_cases), "cases": []}
    failures = 0
    reference_differences = 0
    backends = (("xs", None), ("pure-perl", "1"))
    for case in selected_cases:
        name, opts, expected = case[:3]
        mode = case[3] if len(case) == 4 else "file"
        html_ignored_errors = ["source"]
        if expected == 0 and ("inconsistent" in opts or "check_data_consistency=0" in opts):
            html_ignored_errors.append("inconsistent")
        if name == "fn-explicit-no-da-empty":
            html_ignored_errors.append("empty")
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
            if expected == 0 and name != "fn-explicit-no-da-empty":
                html_dir = case_dir / f"html-{backend}"
                remove(html_dir)
                html_argv = [str(genhtml), "--ignore-errors", ",".join(html_ignored_errors), "--output-directory", str(html_dir),
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
                known_reference_difference = (
                    name == "integer-exact-above-f64" and backend == "xs" and expected == 0
                    and cr["status"] == 0 and not cr["timed_out"]
                    and b"DA:1,9007199254740992\n" in semantic_oracle
                    and normalized(semantic_oracle.replace(b"DA:1,9007199254740992\n", b"DA:1,9007199254740993\n")) == normalized(sem_cand)
                )
                if known_reference_difference:
                    reference_differences += 1
                cand_row = {"argv": cargv, "result": record(cr), "log": str(cand_log),
                            "output_path": str(cand_out), "output_sha256": digest(cb),
                            "semantic_output_sha256": digest(sem_cand),
                            "output": sem_cand.decode("utf-8", "surrogateescape") if cr["status"] == 0 else None,
                            "failure_surface_match": same_failure_surface,
                            "semantic_match": expected != 0 or normalized(sem_cand) == normalized(semantic_oracle),
                            "match": ok, "expected_reference_difference": known_reference_difference,
                            "target_match": ok or known_reference_difference}
                if expected == 0 and name != "fn-explicit-no-da-empty":
                    html_dir = case_dir / f"html-candidate-{backend}"
                    remove(html_dir)
                    html_argv = [str(genhtml), "--ignore-errors", ",".join(html_ignored_errors), "--output-directory", str(html_dir),
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
                if not ok and not known_reference_difference:
                    failures += 1
                    print(f"FAIL candidate {backend}/{name}: oracle={oracle['status']}, candidate={cr['status']}", file=sys.stderr)
                backend_row["candidate"] = cand_row
            entry["backends"].append(backend_row)
        manifest["cases"].append(entry)
    manifest["failures"] = failures
    manifest["expected_reference_differences"] = reference_differences
    manifest["integer_policy"] = "Exact integer parsing; pure-Perl reference applies to the documented XS input-rounding fixture."
    manifest_path = workdir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({"workdir": str(workdir), "required_cases": len(selected_cases), "backend_runs": len(selected_cases) * 2, "expected_reference_differences": reference_differences,
                      "failures": failures, "manifest": str(manifest_path)}, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
