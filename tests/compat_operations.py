#!/usr/bin/env python3
"""Small end-to-end checks for LCOV 2.6 CLI operations."""
from __future__ import annotations

import argparse
import gzip
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import zlib

VALID = ("TN:alpha\nSF:{source}\nFNL:8,5,10\nFNA:8,3,zeta\nFNL:9,5,10\nFNA:9,0,alpha\n"
         "BRDA:5,0,0,2\nBRDA:5,0,1,0\nMCDC:5,2,t,1,0,A\nMCDC:5,2,f,0,0,A\nDA:5,2\nend_of_record\n")


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, timeout=8, **kw)


def check(ok, message):
    if not ok:
        raise AssertionError(message)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--binary", required=True, type=Path)
    args = ap.parse_args()
    binary = str(args.binary.resolve())
    with tempfile.TemporaryDirectory(prefix="lcov-ops-") as td:
        root = Path(td)
        source = root / "src" / "sample.c"
        source.parent.mkdir()
        source.write_text("int entry(void) { return 0; }\n", encoding="utf-8")
        a, b = root / "a.info", root / "b.info"
        a.write_text(VALID.format(source=source), encoding="utf-8")
        b.write_text(VALID.format(source=source).replace("DA:1,1", "DA:1,2"), encoding="utf-8")
        bad = root / "bad.info"
        bad.write_text("TN:late\nSF:x.c\nDA:1,1\nend_of_record\nTN:x\nDA:oops\n", encoding="utf-8")
        config = root / "lcovrc"
        config.write_bytes(b"")
        tmpdir = root / "runs"
        tmpdir.mkdir()
        prefix = [binary, "--lcov-compat=2.6", "--quiet", "--config-file", str(config), "--tempdir", str(tmpdir)]
        # Default stdout and explicit stdout; gzip output has a valid CRC and concatenated input is accepted.
        r = run(prefix + ["-a", str(a)])
        check(r.returncode == 0 and b"SF:" in r.stdout, f"default stdout failed: {r.returncode} {r.stderr!r}")
        r2 = run(prefix + ["-a", str(a), "-o", "-"])
        check(r2.returncode == 0 and r2.stdout == r.stdout, "explicit stdout differs from default")
        gzout = root / "out.info.gz"
        r = run(prefix + ["-a", str(a), "-o", str(gzout)])
        check(r.returncode == 0, f"gzip output failed: {r.stderr!r}")
        gzdata = gzout.read_bytes()
        check(gzip.decompress(gzdata).startswith(b"TN:"), "gzip output is invalid")
        # Python gzip emits concatenated members; both are presented as one input path.
        gzinput = root / "concat.info.gz"
        gzinput.write_bytes(gzip.compress(a.read_bytes()) + gzip.compress(b.read_bytes()))
        r = run(prefix + ["-a", str(gzinput)])
        check(r.returncode == 0 and b"SF:" in r.stdout, f"concatenated gzip input failed: {r.stderr!r}")
        # A sole stdin input uses disk staging and matches regular-file output.
        from_file = run(prefix + ["-a", str(a)])
        from_stdin = run(prefix + ["-a", "-"], input=a.read_bytes())
        check(from_stdin.returncode == 0 and from_stdin.stdout == from_file.stdout,
              f"stdin/file output differs: {from_stdin.stderr!r}")
        check(not list(tmpdir.iterdir()), "stdin staging files remain after success")
        dest_stdin = root / "stdin-error.info"
        dest_stdin.write_bytes(b"keep-me")
        invalid_stdin = run(prefix + ["-a", "-", "-o", str(dest_stdin)], input=bad.read_bytes())
        check(invalid_stdin.returncode == 1 and b"-:" in invalid_stdin.stderr,
              f"stdin diagnostics lost input identity: {invalid_stdin.stderr!r}")
        check(dest_stdin.read_bytes() == b"keep-me" and not list(tmpdir.iterdir()),
              "failed stdin merge changed destination or retained staging files")
        # Duplicate input and forgetting testcase identities.
        r = run(prefix + ["-a", str(a), "-a", str(a)])
        check(r.returncode == 0 and r.stdout.count(b"end_of_record") == 1, "duplicate input aggregation failed")
        r = run(prefix + ["--forget-test-names", "-a", str(a), "-a", str(b)])
        check(r.returncode == 0 and b"TN:\n" in r.stdout and b"TN:alpha" not in r.stdout,
              "forget-test-names failed to clear testcase identity")
        # Repeated output and selector position are parsed in either order.
        first, last = root / "first.info", root / "last.info"
        r = run([binary, "--lcov-compat=2.6", "--quiet", "--config-file", str(config), "-a", str(a), "-o", str(first), "-o", str(last)])
        check(r.returncode == 0 and not first.exists() and last.exists(), "last output option did not win")
        r = run([binary, "-a", str(a), "--lcov-compat=2.6", "--quiet", "--config-file", str(config)])
        check(r.returncode == 0, f"selector position failed: {r.stderr!r}")
        # Malformed late input must preserve an existing destination.
        dest = root / "preserve.info"
        dest.write_bytes(b"keep-me")
        r = run(prefix + ["-a", str(a), "-a", str(bad), "-o", str(dest)])
        check(r.returncode != 0 and dest.read_bytes() == b"keep-me", "late malformed input replaced destination")
        # A genuinely unopenable input checks the I/O path and staging.
        r = run(prefix + ["-a", str(a), "-a", str(root / "absent.info"), "-o", str(dest)])
        check(r.returncode != 0 and dest.read_bytes() == b"keep-me", "input I/O failure replaced destination")
        # Worker counts must be behaviorally identical. A multi-input set avoids a vacuous one-job case.
        outs = []
        for jobs in (1, 2, 4):
            out = root / f"jobs-{jobs}.info"
            r = run(prefix + ["--parallel", str(jobs), "-a", str(a), "-a", str(b), "-o", str(out)])
            check(r.returncode == 0, f"parallel {jobs} failed: {r.stderr!r}")
            outs.append(out.read_bytes())
        check(outs[0] == outs[1] == outs[2], "worker count outputs differ")
        # Native mode remains selected by default and accepts its own option contract.
        native = run([binary, str(a), "-o", str(root / "native.info")])
        check(native.returncode == 0 and (root / "native.info").exists(), f"native CLI changed: {native.stderr!r}")
        # POSIX resource and cancellation probes. Explicit skips are limited to unsupported platforms.
        if os.name == "posix" and sys.platform in ("linux", "darwin"):
            import resource
            # Force a caught output write failure without relying on /dev/full.
            def fsize_limit():
                signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
                resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
            dest.write_bytes(b"keep-me")
            r = subprocess.run(prefix + ["-a", str(a), "-o", str(dest)], capture_output=True, timeout=8, preexec_fn=fsize_limit)
            check(r.returncode != 0 and dest.read_bytes() == b"keep-me", "RLIMIT_FSIZE write failure did not preserve destination")
            # Interrupt while stdin is open but blocked; timeout is the guard against failed cancellation.
            proc = subprocess.Popen([p for p in prefix if p != "--quiet"] + ["--verbose", "-a", "-", "-o", str(root / "cancel.info")], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                # The input diagnostic is emitted after handler installation.
                # Slow sanitizer startup must not turn this into an uncaught signal.
                import select
                deadline = time.monotonic() + 8
                ready_output = bytearray()
                marker = b"lcovmerge: reading LCOV input -\n"
                while marker not in ready_output:
                    remaining = deadline - time.monotonic()
                    check(remaining > 0, "blocked-stdin input readiness timed out")
                    readable, _, _ = select.select([proc.stderr], [], [], remaining)
                    check(bool(readable), "blocked-stdin input readiness timed out")
                    chunk = os.read(proc.stderr.fileno(), 4096)
                    check(bool(chunk), f"blocked-stdin exited before readiness: {bytes(ready_output)!r}")
                    ready_output.extend(chunk)
                proc.send_signal(signal.SIGINT)
                # Keep stdin open until cancellation completes. communicate()
                # would close it and could hide an interrupted-read retry bug.
                proc.wait(timeout=4)
                _, err = proc.communicate(timeout=2)
                err = bytes(ready_output) + err
                check(proc.returncode == 1 and b"interrupted" in err, f"blocked-stdin signal was not caught cleanly: {proc.returncode} {err!r}")
                check(not list(tmpdir.iterdir()), "interruption left temporary runs")
            finally:
                if proc.poll() is None:
                    proc.kill(); proc.communicate(timeout=2)
            # A low descriptor limit should be handled as a normal input/resource error.
            def nofile_limit():
                soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
                resource.setrlimit(resource.RLIMIT_NOFILE, (min(16, hard), hard))
            many = ["-a", str(a)] * 24
            r = subprocess.run(prefix + many, capture_output=True, timeout=8, preexec_fn=nofile_limit)
            check(r.returncode in (0, 1), f"low RLIMIT_NOFILE was not handled: {r.returncode} {r.stderr!r}")
        else:
            print(f"compat operations: POSIX signal/resource cases skipped on {sys.platform}")
        # A bounded but nontrivial valid input makes the memory limit observable.
        dest.write_bytes(b"keep-me")
        heavy = root / "bounded.info"
        heavy.write_text("".join(f"TN:t{i}\nSF:{source}\nDA:{i + 1},1\nend_of_record\n" for i in range(32)), encoding="utf-8")
        r = run(prefix + ["--mem-limit", "1", "-a", str(heavy), "-o", str(dest)])
        check(r.returncode != 0 and dest.read_bytes() == b"keep-me",
              f"memory limit boundary did not fail atomically (exit={r.returncode}, stderr={r.stderr!r})")
    print("compat operations: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
