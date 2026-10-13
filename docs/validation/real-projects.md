# Real project validation

## Toolchain

| Host | Compiler | `gcc` command | Coverage flags | lcov | genhtml | Measurement |
| --- | --- | --- | --- | --- | --- | --- |
| macOS 27.0.1 arm64 | Apple clang version 21.0.0 (clang-2100.3.34.2) | Apple clang version 21.0.0 (clang-2100.3.34.2) | `--coverage` | lcov: LCOV version 2.6-0 | genhtml: LCOV version 2.6-0 | `time.perf_counter and isolated resource.getrusage(RUSAGE_CHILDREN); /usr/bin/time -l available` |

## Results

| Project | Tag | Commit | Shards | Input MB | Output MB | lcovmerge seconds / peak RSS MiB | lcov seconds / peak RSS MiB | lcov verdict | Tests | genhtml | Order / -j | Rewrites | 8 MiB sort |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- | --- | --- | --- |
| zlib | — | 51b7f2abdade71cd9bb0e7a373ef2610ec6f9daf | 2 | 0.180 | 0.091 | 0.008 / 3.98 | 0.122 / 42.34 | PASS | PASS x2 | PASS | PASS | PASS | PASS |
| lua | — | 1ab3208a1fceb12fca8f24ba57d6e13c5bff15e3 | 2 | 0.578 | 0.292 | 0.010 / 6.75 | 0.210 / 46.97 | PASS | PASS x2 (portable mode; upstream skips nonportable tests) | PASS | PASS | PASS | PASS |
| cjson | — | acc76239bee01d8e9c858ae2cab296704e52d916 | 2 | 0.202 | 0.102 | 0.006 / 3.92 | 0.138 / 42.8 | PASS | PASS x2 | PASS | PASS | PASS | PASS |
| json-c | — | 41a55cfcedb54d9c1874f2f0eb07b504091d7e37 | 2 | 0.208 | 0.106 | 0.007 / 4.23 | 0.147 / 42.81 | PASS | PASS x2 | PASS | PASS | PASS | PASS |
| libyaml | — | 2c891fc7a770e8ba2fec34fc6b545c672beb37e6 | 2 | 0.050 | 0.025 | 0.005 / 2.7 | 0.113 / 40.8 | PASS | PASS x2 | PASS | PASS | PASS | PASS |
| tinyxml2 | — | 9148bdf719e997d1f474be6bcc7943881046dba1 | 2 | 0.281 | 0.141 | 0.006 / 4.38 | 0.155 / 43.92 | PASS | PASS x2 | PASS | PASS | PASS | PASS |
| sqlite | version-3.50.4 | 8ed5e7365e6f12f427910188bbf6b254daad2ef6 | 1 | 6.089 | 6.090 | 0.074 / 16.02 | 1.032 / 90.08 | PASS | PASS x1 | PASS | PASS | PASS | PASS |
| libarchive | v3.8.1 | 9525f90ca4bd14c7b335e2f8c84a4607b0af6bdf | 2 | 3.608 | 1.825 | 0.041 / 16.31 | 0.693 / 77.62 | PASS | PASS x2; 2 known platform/toolchain tests excluded | PASS | PASS | PASS | PASS |
| REAL composite | — | 8 pinned commits | 15 | 11.196 | 8.671 | 0.085 / 15.94 | 2.043 / 140.27 | PASS | PASS | PASS | PASS | n/a | per-project runs PASS |

## Capture notices

| Observation |
| --- |
| tinyxml2 LCOV capture bypassed inconsistent function-boundary checks from Apple gcov. |
| SQLite capture used LCOV --ignore-errors inconsistent because gcov reported duplicate sqlite3OsFetch function metadata in the generated sqlite3.c amalgamation; its normalized merge comparison remained in the run. |
| libarchive capture used LCOV --ignore-errors inconsistent after gcov reported a DA line with no branch data in archive_write_set_format_shar.c; its normalized merge comparison remained in the run. |
| curl 8.15.0 was excluded: its CMake/CTest configuration returned success but emitted no .gcda counters, so it did not contribute coverage data. |
| libarchive 3.8.1 ran with two named CTest exclusions after reproducible failures on this macOS arm64 toolchain; the exact failures and reproducer are recorded below. |
| Apple gcov/lcov emitted non-fatal unsupported function-boundary notices during capture. |
| Additional non-fatal LCOV capture warnings occurred. |
| `gcc` reports Apple Clang too; a distinct GNU compiler comparison was unavailable on this host. |
| Lua ran with its documented `_port=true` mode; its upstream suite skips nonportable tests in this mode. |
| Each project used two independent full-suite captures; external-sort checks used 34 inputs (above the 32-input direct-path limit), comparing 8 MiB -j1 with LCOV and reverse-order / 32 MiB -j4 outputs bytewise. |
| The separate libpng 1.6.50 test run passed, but Apple gcov's DA/BRDA consistency mismatch prevented standard downstream genhtml validation; its synthetic reproducer was generated under .cache/real-projects-run and removed after validation. |

## Comparison

| Normalized records compared | Composite verdict | Overall verdict |
| --- | --- | --- |
| `SF sets and normalized DA, FN, FNDA, BRDA keyed records and counts` | PASS | PASS WITH TEST EXCLUSIONS |

## Real-derived workload

The captured real shard set totals 11,196,263 bytes. Scaling note: real captures were below 50 MB; re-rooted copies raise the measured input size. The measured set uses captured shards re-rooted under distinct `/real-scaled/` prefixes when expansion was required; this is synthetic scaling and is not presented as real project input.

| Label | Source bytes | Measured bytes | Prefixes | Shards | lcovmerge s / MiB RSS / MB/s | lcov s / MiB RSS / MB/s | Correctness | External sort | Order / -j |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |
| REAL-DERIVED SCALED | 11,196,263 | 55,483,440 | 5 | 75 | 0.449 / 19.19 / 123.6 | 9.074 / 454.72 / 6.1 | PASS | PASS (8 MiB, --jobs 1, more than 32 inputs) | PASS |

Output record presentation order may differ from LCOV by design, as described in `docs/LIMITATIONS.md`. Comparisons normalize SF paths and compare keyed DA, FN, FNDA, and BRDA records and counts.


## Minimal compatibility reproducer

On this Apple gcov and LCOV toolchain, genhtml rejects the trace because line 1 is marked unhit while a branch on that line is hit. The trace exercises a capture-consistency issue; no lcovmerge source change was made.

From the repository root, recreate the source and trace under scratch and run genhtml:

```sh
mkdir -p .cache/real-projects-run/branch-repro/src
printf '%s\n' 'int branch(int x) { if (x) return 1; return 0; }' > .cache/real-projects-run/branch-repro/src/branch.c
cat > .cache/real-projects-run/branch-repro/input.info <<'EOF'
TN:
SF:src/branch.c
DA:1,0
BRDA:1,0,0,1
BRF:1
BRH:1
LF:1
LH:0
end_of_record
EOF
genhtml --branch-coverage --quiet --source-directory .cache/real-projects-run/branch-repro \
  --output-directory .cache/real-projects-run/branch-repro-html .cache/real-projects-run/branch-repro/input.info
```

```text
TN:
SF:src/branch.c
DA:1,0
BRDA:1,0,0,1
BRF:1
BRH:1
LF:1
LH:0
end_of_record
```


## Excluded upstream tests

The `libarchive` `v3.8.1` checkout (`9525f90ca4bd14c7b335e2f8c84a4607b0af6bdf`) excludes the following two tests from the CTest run. The rest of the configured suite passes and contributes coverage.

| Test | Observed failure |
| --- | --- |
| `libarchive_test_read_format_7zip_lzma2_riscv` | `archive_read_data` returned -30 instead of 8,488 bytes; computed CRC 433,319,548 differed from expected 4,159,513,831. |
| `libarchive_test_write_disk_perms` | The test observed mode 0742 where it expected setuid mode 04742 and setgid mode 02742. |

Reproduce both observations from the built test tree with:

```sh
ctest --test-dir build -R '^(libarchive_test_read_format_7zip_lzma2_riscv|libarchive_test_write_disk_perms)$' --output-on-failure -V
```
