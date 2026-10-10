# Soak validation

- Date: 2026-10-10
- Source commit: `1e41a49ce75d`
- Environment: Darwin 27.0.0 arm64, Apple clang 21.0.0, Python 3.12.8.

Functional, differential, ASan/UBSan, and fuzz checks passed. No crash, output mismatch, or sanitizer finding occurred, so no source fix, regression fixture, or Unreleased changelog entry was needed. Dedicated leak detection was unavailable on this host (details below). Generated traces, outputs, and logs were kept under `.luna-tmp` and removed after validation.

## Commands and results

- `/usr/bin/time -p env TMPDIR=./.luna-tmp SOAK=1 make test > .luna-tmp/soak-test.log 2>&1` — PASS, 16.71 s. Unit tests passed with 698,548 assertions. The suite reported 63 golden, 16 malformed, 246 oracle, 128 randomized sets / 2,176 randomized runs, 4 determinism, 7 I/O, 4 listfile, 21 limit-I/O, 3 signal-cleanup, 1 closed-pipe, 2 interrupted-output, 1 many-path, 19 interruption-document, and 200 lcov cases. Documentation checks passed for 20 options and 4 exit statuses.
- `/usr/bin/time -p env TMPDIR=./.luna-tmp make asan > .luna-tmp/asan-suite.log 2>&1` — PASS, 20.25 s. ASan/UBSan unit and core suites passed: 63 golden, 16 malformed, 246 oracle, 32 randomized sets / 544 randomized runs, 4 determinism, and the remaining core cases; `lcov` was intentionally disabled by the target.
- `/usr/bin/time -p env TMPDIR=./.luna-tmp FUZZ_RUNS=100000 make fuzz > .luna-tmp/fuzz-calibration.log 2>&1` — PASS, 100,000 executions across 11 corpus seeds, 40.18 s.
- `/usr/bin/time -p env TMPDIR=./.luna-tmp FUZZ_RUNS=5000000 make fuzz > .luna-tmp/fuzz-soak.log 2>&1` — PASS, 5,000,000 executions across the same 11 seeds, 2,087.00 s (34m 47s). Combined fuzz time was 2,127.18 s (35m 27.18s). Seeds: `basic.info`, `branch-dash.info`, `crlf-eof.info`, `empty.info`, `invalid-utf8.info`, `invalid.info`, `mcdc.info`, `nul-byte.info`, `truncated.info`, `u64-max.info`, and `unknown-checksum.info`.
- `/usr/bin/time -p env TMPDIR=./.luna-tmp ASAN_OPTIONS=halt_on_error=1 UBSAN_OPTIONS=halt_on_error=1 python3 -I .luna-tmp/asan_stress.py bin/lcovmerge-asan > .luna-tmp/asan-stress.log 2>&1` — PASS, 170 invocations in 6.33 s. The generated workload had 32 input files, 65,536 sections, and 4,766,368 input bytes. External sort was confirmed for worker counts 1–32; the one-worker run used the minimum `--mem-limit 8M`, and each worker received 8 MiB in the other runs. All 32 outputs had the same SHA-256: `a960f0b91c9bb16097524955aaf2b4516f0cb32fa0074a3a6b5308bd8fc9fb36`. The run also covered 64 bit-flip variants and 64 truncations at random offsets (PRNG seed `0x5A0C2026`), line lengths 1,048,560 / 1,048,575 / 1,048,576 / 1,048,577 / 1,052,672 bytes, one saturating `UINT64_MAX` merge, and four over-limit count rejections. Sort temporary files were absent after each invocation.
- `/usr/bin/time -p python3 -I .luna-tmp/differential_new_seeds.py bin/lcovmerge > .luna-tmp/differential-new-seeds.log 2>&1` — PASS, 35.42 s. Compared C and `tools/oracle.py` on 1,024 generated cases, 32 cases for each of 32 new seeds. Seeds were `0xa11ce000 + i * 7919` for `i = 0..31`; all 1,024 exit statuses matched and all successful outputs were byte-identical.

## Leak-check limitation

The normal ASan/UBSan runs passed. A separate LeakSanitizer check could not run on this host: `ASAN_OPTIONS=detect_leaks=1:halt_on_error=1 UBSAN_OPTIONS=halt_on_error=1 bin/lcovmerge-unit-asan` reported `AddressSanitizer: detect_leaks is not supported on this platform`. The fallback command `ASAN_OPTIONS=detect_leaks=0:halt_on_error=1 UBSAN_OPTIONS=halt_on_error=1 leaks --atExit -- ./bin/lcovmerge-unit-asan` exited before inspecting the process with `Couldn't get task port for pid ...` under this sandbox. Heap-leak status is therefore unverified here.
