#!/usr/bin/env python3
"""Threaded sorted-input direct path: every case must equal the single-job run exactly.

Each case runs the plain binary (no partitioning) and the test-hook binary with
LCOVMERGE_TEST_PARTITION=P forcing P partitions, then compares exit code, stdout, stderr, output
bytes and the leftover directory listing. The only normalised difference is the -v job-count text
when more than one job was used.
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import tempfile
import time
from pathlib import Path

PARTS = (2, 3, 4, 8)
JOBS_TEXT = re.compile(rb"using (\d+) jobs \(sorted-input fast path\)")


def section(path: str, lines=("DA:1,1",), *, tn=None, close=True, eol="\n", head="SF") -> str:
    text = f"TN:{tn}{eol}" if tn is not None else ""
    text += f"{head}:{path}{eol}" + "".join(f"{line}{eol}" for line in lines)
    return text + ("end_of_record" + eol if close else "")


def paths(count: int, prefix: str = "/t/p") -> list[str]:
    return [f"{prefix}{i:04d}.c" for i in range(count)]


def trace(path_list, *, body=lambda i: (f"DA:{i % 9 + 1},{i % 5}", f"DA:{i % 9 + 20},1"), **kw) -> str:
    return "".join(section(p, body(i), **kw) for i, p in enumerate(path_list))


def _run(command, env, stdin=None):
    return subprocess.run(command, input=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          env=env, check=False)


def _listing(directory: Path):
    return sorted(p.name for p in directory.iterdir())


def compare(binary: Path, ptest: Path, base: Path, name: str, inputs: list[bytes | str], *,
            options=(), stdin: bytes | None = None, extra_inputs=(), output="out.info",
            parts=PARTS, engaged: bool | None = None, tmpdir_option: bool = False) -> int:
    """Runs one case for every P; returns the number of comparisons."""
    runs = 0
    for parts_count in parts:
        case = base / f"{name}-p{parts_count}"
        case.mkdir()
        files = []
        for index, content in enumerate(inputs):
            path = case / f"in{index:02d}.info"
            path.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
            files.append(str(path))
        files.extend(str(case / item) if not os.path.isabs(item) else item for item in extra_inputs)
        out_path = output if output == "-" else str(case / output)
        opts = list(options)
        if engaged:
            opts.append("-v")
        if tmpdir_option:
            (case / "tmp").mkdir()
            opts += ["--tmpdir", str(case / "tmp")]
        command_tail = [*opts, *files, "-o", out_path]
        results = []
        for which in ("ref", "new"):
            env = dict(os.environ)
            env.pop("LCOVMERGE_TEST_PARTITION", None)
            exe = binary
            if which == "new":
                env["LCOVMERGE_TEST_PARTITION"] = str(parts_count)
                exe = ptest
            if out_path != "-" and Path(out_path).exists():
                Path(out_path).unlink()
            completed = _run([str(exe), *command_tail], env, stdin)
            produced = Path(out_path).read_bytes() if out_path != "-" and Path(out_path).exists() else None
            listing = _listing(case) + (_listing(case / "tmp") if tmpdir_option else [])
            results.append((completed, produced, listing))
        (ref, ref_out, ref_list), (new, new_out, new_list) = results
        new_err = JOBS_TEXT.sub(b"using 1 job (sorted-input fast path)", new.stderr)
        if engaged and not JOBS_TEXT.search(new.stderr):
            raise AssertionError(f"partition case {name} P={parts_count} did not use the threaded path: {new.stderr!r}")
        problems = []
        if ref.returncode != new.returncode:
            problems.append(f"exit {ref.returncode} != {new.returncode}")
        if ref.stdout != new.stdout:
            problems.append("stdout differs")
        if ref.stderr != new_err:
            problems.append(f"stderr differs\nref: {ref.stderr[-600:]!r}\nnew: {new_err[-600:]!r}")
        if ref_out != new_out:
            problems.append("output bytes differ")
        if ref_list != new_list:
            problems.append(f"leftover listing differs: {ref_list} vs {new_list}")
        if problems:
            raise AssertionError(f"partition case {name} P={parts_count}: " + "; ".join(problems))
        runs += 1
    return runs


def _big_line_trace(count: int, big_index: int) -> str:
    out = []
    for i, p in enumerate(paths(count)):
        lines = ["DA:1,1"]
        if i == big_index:
            lines.append("X:" + "a" * ((1 << 20) - 3))  # exactly 1 MiB - 1 line
        out.append(section(p, lines))
    return "".join(out)


def partition_tests(binary: Path, ptest: Path, temporary: Path) -> int:
    if os.name == "nt":
        return 0
    base = temporary / "partition"
    base.mkdir()
    n = 0
    sixty = paths(60)

    # Plain successes that must actually use threads.
    a = trace(sixty)
    b = trace(paths(60, "/t/p")[10:] + paths(20, "/t/q"), tn="alpha")
    c = trace([p for p in sixty if int(p[-6:-2]) % 3 == 0], tn="beta")
    n += compare(binary, ptest, base, "plain", [a, b, c], engaged=True)
    n += compare(binary, ptest, base, "plain-stats", [a, b], options=["--stats", "-q"])
    n += compare(binary, ptest, base, "many-tiny-sections",
                 [trace(paths(500), body=lambda i: ("DA:1,1",)),
                  trace(paths(500)[::2], body=lambda i: ("DA:1,2",)),
                  trace(paths(500)[1::3], body=lambda i: ("DA:2,2",))], engaged=True)
    n += compare(binary, ptest, base, "one-huge-section",
                 [section("/t/huge.c", [f"DA:{i},1" for i in range(1, 30000)])])
    n += compare(binary, ptest, base, "dup-path-straddle",
                 ["".join(section(p, (f"DA:{k},1",)) for p in sixty for k in (1, 2)), a])
    n += compare(binary, ptest, base, "single-input", [a], engaged=True)
    n += compare(binary, ptest, base, "same-input-many", [a] * 12, engaged=True)

    # Inputs lacking some splitter ranges, empty inputs, empty files.
    n += compare(binary, ptest, base, "disjoint-ranges",
                 [trace(sixty[:30]), trace(sixty[30:]), trace(sixty[25:35])], engaged=True)
    n += compare(binary, ptest, base, "empty-inputs", [b"", a, b"", b""])
    n += compare(binary, ptest, base, "all-empty", [b"", b""])
    n += compare(binary, ptest, base, "comments-only", ["# nothing\n\n", "\n\n# x\n"])

    # TN handling around the split points: blank/comment lines, CRLF, TN only before some SFs.
    tn_mixed = "".join(
        (f"TN:t{i}\n# c\n\n" if i % 2 else "") + section(p, ("DA:1,1",)) for i, p in enumerate(sixty))
    n += compare(binary, ptest, base, "tn-comment-blank", [tn_mixed, a], engaged=True)
    n += compare(binary, ptest, base, "tn-crlf", [trace(sixty, tn="crlf", eol="\r\n"), a], engaged=True)
    n += compare(binary, ptest, base, "tn-two-before-sf",
                 [a, "".join((("TN:a\nTN:b\n" if i == 37 else "") + section(p)) for i, p in enumerate(sixty))])
    n += compare(binary, ptest, base, "tn-before-end-of-file-garbage", [a, a + "TN:dangling\n"])

    # Joined end_of_record/SF lines exactly at (and away from) the split points.
    joined = "".join(
        (section(p, close=False) + "end_of_record" if i % 5 == 0 else section(p)) if False else
        section(p, close=(i % 5 != 4)) if i % 5 != 4 else section(p, close=False).rstrip("\n") + "\nend_of_record"
        for i, p in enumerate(sixty))
    joined_input = ""
    for i, p in enumerate(sixty):
        body = f"SF:{p}\nDA:1,1\n"
        if i % 4 == 3:  # lose the newline after end_of_record so the next SF is joined to it
            joined_input += body + "end_of_record"
        else:
            joined_input += body + "end_of_record\n"
    n += compare(binary, ptest, base, "joined-sf-everywhere", [joined_input, a], options=["--warn-unknown"])
    n += compare(binary, ptest, base, "joined-sf-everywhere-v", [joined_input], options=["-v", "--stats"])
    n += compare(binary, ptest, base, "joined-kf", [joined_input.replace("SF:", "KF:"), a])
    n += compare(binary, ptest, base, "joined-without-open-section",
                 [a + "end_of_recordSF:/t/zzz.c\nDA:1,1\nend_of_record\n"])
    n += compare(binary, ptest, base, "joined-first-line", ["end_of_recordSF:/t/p0000.c\nDA:1,1\nend_of_record\n", a])

    # Errors on and around partition boundaries (each must reproduce the single-job diagnostics).
    n += compare(binary, ptest, base, "sf-before-end-of-record",
                 [a, "".join(section(p, close=(i != 41)) for i, p in enumerate(sixty))])
    n += compare(binary, ptest, base, "err-last-line-of-range",
                 [a, "".join(section(p) + ("end_of_record\n" if i == 29 else "") for i, p in enumerate(sixty))])
    n += compare(binary, ptest, base, "err-first-input-first-line", ["DA:1,1\n" + a, a])
    n += compare(binary, ptest, base, "err-bad-da-late",
                 [a, "".join(section(p, ("DA:1,x",) if i == 55 else ("DA:1,1",)) for i, p in enumerate(sixty))])
    n += compare(binary, ptest, base, "err-bad-da-early",
                 ["".join(section(p, ("DA:y,1",) if i == 1 else ("DA:1,1",)) for i, p in enumerate(sixty)), a])
    n += compare(binary, ptest, base, "err-two-inputs",
                 ["".join(section(p, ("DA:1,x",) if i == 10 else ("DA:1,1",)) for i, p in enumerate(sixty)),
                  "".join(section(p, ("DA:y,1",) if i == 50 else ("DA:1,1",)) for i, p in enumerate(sixty))])
    warn_rows = "".join(section(p, ("DA:1,1", "ZZ:unknown row") if i < 20 else ("DA:1,1",)) for i, p in enumerate(sixty))
    n += compare(binary, ptest, base, "warn-early-error-late",
                 [warn_rows, "".join(section(p, ("DA:1,x",) if i == 45 else ("DA:1,1",)) for i, p in enumerate(sixty))],
                 options=["--warn-unknown"])
    n += compare(binary, ptest, base, "warn-unknown-only", [warn_rows, a], options=["--warn-unknown", "-v", "--stats"])
    n += compare(binary, ptest, base, "binary-nul-late", [a, a + "SF:/t/zz.c\nX:\x00\n"])
    n += compare(binary, ptest, base, "invalid-utf8-mid",
                 [a.replace("DA:1,1", "X:\u00e9", 1).encode("utf-8").replace(b"\xc3\xa9", b"\xff"), a])
    n += compare(binary, ptest, base, "truncated-last-line", [a, a[:-8]])
    n += compare(binary, ptest, base, "no-final-newline", [a.rstrip("\n"), a])
    n += compare(binary, ptest, base, "crlf-everywhere", [trace(sixty, eol="\r\n"), a], engaged=True)

    # Ordering problems: only the last partition, or an input that ignores the splitters entirely.
    swapped = list(sixty)
    swapped[-3], swapped[-1] = swapped[-1], swapped[-3]
    n += compare(binary, ptest, base, "ooo-last-partition", [a, trace(swapped)])
    n += compare(binary, ptest, base, "ooo-reversed", [a, trace(sixty[::-1])])
    shuffled = sixty[7:] + sixty[:7]
    n += compare(binary, ptest, base, "ooo-rotated", [a, trace(shuffled)])
    n += compare(binary, ptest, base, "ooo-record-in-section",
                 [a, "".join(section(p, ("DA:5,1", "DA:2,1") if i == 33 else ("DA:1,1",)) for i, p in enumerate(sixty))])
    for position in range(2, 60, 3):
        dipped = list(sixty)
        dipped[position] = "/t/a0000.c"  # a single section far below its neighbours
        n += compare(binary, ptest, base, f"ooo-low-section-{position}", [a, trace(dipped)])
        raised = list(sixty)
        raised[position] = "/t/zzzz.c"  # and one far above them
        n += compare(binary, ptest, base, f"ooo-high-section-{position}", [trace(raised), a], parts=(2, 4))
    n += compare(binary, ptest, base, "ooo-mem-8M", [a, trace(sixty[::-1])], options=["--mem-limit", "8M"], tmpdir_option=True)

    # Checksums: conflict in a later partition, warning and --strict-checksum (exit 2).
    cs1 = "".join(section(p, (f"DA:1,1,aa{i}",)) for i, p in enumerate(sixty))
    cs2 = "".join(section(p, (f"DA:1,2,bb{i}" if i == 48 else f"DA:1,2,aa{i}",)) for i, p in enumerate(sixty))
    n += compare(binary, ptest, base, "checksum-conflict-warn", [cs1, cs2])
    n += compare(binary, ptest, base, "checksum-conflict-strict", [cs1, cs2], options=["--strict-checksum"])
    n += compare(binary, ptest, base, "checksum-clean", [cs1, cs1], options=["--strict-checksum"], engaged=True)
    mcdc1 = "".join(section(p, ("MCDC:3,2,t,1,0,a",)) for p in sixty)
    mcdc2 = "".join(section(p, ("MCDC:3,2,t,1,0,b" if p.endswith("0052.c") else "MCDC:3,2,t,1,0,a",)) for p in sixty)
    n += compare(binary, ptest, base, "mcdc-mismatch-late", [mcdc1, mcdc2])

    # Path rewriting and filters decide both order and splitters.
    rev_named = [f"/z/{i:04d}.c" for i in range(60)][::-1]
    n += compare(binary, ptest, base, "rebase-reorders", [trace(rev_named), trace(rev_named[::2])],
                 options=["--rebase", "/z=/a/b"])
    n += compare(binary, ptest, base, "rebase-unsorted-after", [trace(rev_named), a], options=["--rebase", "/z=/a/b"])
    n += compare(binary, ptest, base, "prefix-strip", [a, b], options=["--prefix-strip", "/t"], engaged=True)
    n += compare(binary, ptest, base, "include-one-of-many", [a, b], options=["--include", "*p0031*"])
    n += compare(binary, ptest, base, "include-none", [a, b], options=["--include", "*nomatch*"])
    n += compare(binary, ptest, base, "exclude-half", [a, b], options=["--exclude", "*[02468].c"], engaged=True)
    excluded_junk = "".join(
        (section(p, ("QQ:junk", "DA:9,9")) if i % 3 == 0 else section(p)) for i, p in enumerate(sixty))
    n += compare(binary, ptest, base, "excluded-sections-junk-warn", [excluded_junk, a],
                 options=["--exclude", "*[036]?.c", "--warn-unknown"])
    unsorted_excluded = "".join(section(p) for p in sixty[::-1])
    n += compare(binary, ptest, base, "excluded-unsorted-ok", [a, unsorted_excluded],
                 options=["--include", "/nothing/*"])
    n += compare(binary, ptest, base, "branch-off", [a, b], options=["--branch-coverage", "off"])
    n += compare(binary, ptest, base, "no-function-data", [a, b], options=["--no-function-data", "--stats"])
    n += compare(binary, ptest, base, "function-rows",
                 ["".join(section(p, (f"FN:{i + 1},f{i}", f"FNDA:{i},f{i}", "FNF:1", "FNH:1", f"DA:{i + 1},1"))
                          for i, p in enumerate(sixty)), a], engaged=True)
    n += compare(binary, ptest, base, "branch-rows",
                 ["".join(section(p, (f"BRDA:{i + 1},0,0,{i}", f"BRDA:{i + 1},0,1,-")) for i, p in enumerate(sixty)), a],
                 engaged=True)

    # Memory-budget overflow: nine inputs each carrying a 1 MiB-class line in a late partition.
    big = [_big_line_trace(40, 35 + (i % 3)) for i in range(9)]
    n += compare(binary, ptest, base, "budget-overflow-late-line", big, options=["--mem-limit", "16M"],
                 tmpdir_option=True, parts=(2, 3))
    n += compare(binary, ptest, base, "budget-ok-line", big[:2], options=["--mem-limit", "64M"], parts=(2, 3))

    # Destination, stdio and file-count edge cases.
    n += compare(binary, ptest, base, "unwritable-destination", [a, b], output="no-such-dir/out.info")
    n += compare(binary, ptest, base, "stdout-output", [a, b], output="-")
    n += compare(binary, ptest, base, "stdin-input", [a], stdin=a.encode(), extra_inputs=["-"] if False else ())
    n += compare(binary, ptest, base, "stdin-dash", [a], stdin=b.encode(), extra_inputs=["-"])
    n += compare(binary, ptest, base, "forty-inputs", [trace(sixty[i % 7::7]) for i in range(40)], options=["-q"],
                 parts=(2, 4))
    n += compare(binary, ptest, base, "missing-input", [a], extra_inputs=["does-not-exist.info"])
    n += compare(binary, ptest, base, "directory-input", [a], extra_inputs=[str(base)])
    n += compare(binary, ptest, base, "verbose-stats", [a, b], options=["-v", "--stats"], engaged=True)
    return n


def partition_signal_tests(ptest: Path) -> int:
    """SIGTERM/SIGINT/SIGHUP while partitions are running: exit 3, 'interrupted', no staged files."""
    if os.name == "nt" or not hasattr(signal, "SIGSTOP"):
        return 0
    text = trace([f"/t/p{i:06d}.c" for i in range(60000)], body=lambda i: tuple(f"DA:{k},{i % 7}" for k in range(1, 60)))
    count = 0
    with tempfile.TemporaryDirectory(prefix="partition-signal-") as work_dir:
        work = Path(work_dir)
        inputs = []
        for index in range(4):
            path = work / f"in{index}.info"
            path.write_text(text, encoding="utf-8")
            inputs.append(str(path))
        for name in ("SIGTERM", "SIGINT", "SIGHUP"):
            last = None
            for attempt in range(8):
                out_dir = work / f"{name.lower()}-{attempt}"
                out_dir.mkdir()
                output = out_dir / "out.info"
                env = dict(os.environ, LCOVMERGE_TEST_PARTITION="4")
                process = subprocess.Popen([str(ptest), "-j", "4", *inputs, "-o", str(output)],
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
                try:
                    deadline = time.monotonic() + 30
                    staged = []
                    while time.monotonic() < deadline and len(staged) < 4:
                        staged = os.listdir(out_dir)
                        if process.poll() is not None:
                            break
                    if process.poll() is not None or len(staged) < 4:
                        process.communicate()
                        last = _RaceLost(f"{name}: run finished before staged files were observed")
                        continue
                    process.send_signal(signal.SIGSTOP)
                    time.sleep(0.02)
                    process.send_signal(getattr(signal, name))
                    process.send_signal(signal.SIGCONT)
                    stdout, stderr = process.communicate(timeout=60)
                except BaseException:
                    if process.poll() is None:
                        process.kill()
                        process.communicate()
                    raise
                left = sorted(p.name for p in out_dir.iterdir())
                if process.returncode == 0:
                    last = _RaceLost(f"{name}: run completed before the signal arrived")
                    continue
                if process.returncode != 3 or b"interrupted" not in stderr or left:
                    raise AssertionError(
                        f"partitioned {name}: status={process.returncode}, leftover={left}, stderr={stderr!r}")
                count += 1
                break
            else:
                raise AssertionError(f"partition signal window never observed: {last}")
    return count


class _RaceLost(Exception):
    pass
