# Real project validation

## Toolchain

| Host | Compiler | `gcc` command | Coverage flags | lcov | genhtml | Measurement |
| --- | --- | --- | --- | --- | --- | --- |
| macOS 27.0 arm64 | Apple clang version 21.0.0 (clang-2100.3.34.2) | Apple clang version 21.0.0 (clang-2100.3.34.2) | `--coverage` | lcov: LCOV version 2.6-0 | genhtml: LCOV version 2.6-0 | `time.perf_counter and isolated resource.getrusage(RUSAGE_CHILDREN); /usr/bin/time -l blocked: sysctl kern.clockrate access denied` |

## Results

| Project | Tag | Commit | Shards | Input MB | Output MB | lcovmerge seconds / peak RSS MiB | lcov seconds / peak RSS MiB | lcov verdict | Tests | genhtml | Order / -j | Rewrites | 8 MiB sort |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- | --- | --- | --- |
| zlib | — | 51b7f2abdade71cd9bb0e7a373ef2610ec6f9daf | 2 | 0.180 | 0.091 | 0.005 / 4.08 | 0.108 / 42.42 | PASS | PASS x2 | PASS | PASS | PASS | PASS |
| lua | — | 1ab3208a1fceb12fca8f24ba57d6e13c5bff15e3 | 2 | 0.579 | 0.293 | 0.008 / 6.77 | 0.173 / 46.78 | PASS | PASS x2 (portable mode; upstream skips nonportable tests) | PASS | PASS | PASS | PASS |
| cjson | — | acc76239bee01d8e9c858ae2cab296704e52d916 | 2 | 0.203 | 0.103 | 0.004 / 4.22 | 0.116 / 42.83 | PASS | PASS x2 | PASS | PASS | PASS | PASS |
| json-c | — | 41a55cfcedb54d9c1874f2f0eb07b504091d7e37 | 2 | 0.210 | 0.106 | 0.005 / 4.28 | 0.116 / 42.95 | PASS | PASS x2 | PASS | PASS | PASS | PASS |
| libyaml | — | 2c891fc7a770e8ba2fec34fc6b545c672beb37e6 | 2 | 0.051 | 0.025 | 0.003 / 2.86 | 0.093 / 40.88 | PASS | PASS x2 | PASS | PASS | PASS | PASS |
| tinyxml2 | — | 9148bdf719e997d1f474be6bcc7943881046dba1 | 2 | 0.281 | 0.141 | 0.005 / 4.47 | 0.129 / 43.73 | PASS | PASS x2 | PASS | PASS | PASS | PASS |
| sqlite | version-3.50.4 | 8ed5e7365e6f12f427910188bbf6b254daad2ef6 | 1 | 6.091 | 6.092 | 0.070 / 16.38 | 0.898 / 90.41 | PASS | PASS x1 | PASS | PASS | PASS | PASS |
| libarchive | v3.8.1 | 9525f90ca4bd14c7b335e2f8c84a4607b0af6bdf | 2 | 3.631 | 1.839 | 0.036 / 16.83 | 0.580 / 78.28 | PASS | PASS x2; 2 known platform/toolchain tests excluded | PASS | PASS | PASS | PASS |
| REAL composite | — | 8 pinned commits | 15 | 11.225 | 8.690 | 0.078 / 20.11 | 1.635 / 139.55 | PASS | PASS | PASS | PASS | n/a | per-project runs PASS |

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
| Projects used the capture counts recorded in the manifest; SQLite used one full-suite capture because its run took several minutes. External-sort checks used 34 inputs (above the 32-input direct-path limit), comparing 8 MiB -j1 with LCOV and reverse-order / 32 MiB -j4 outputs bytewise. |
| The separate libpng 1.6.50 test run passed, but Apple gcov's DA/BRDA consistency mismatch prevented standard downstream genhtml validation; its synthetic reproducer was generated under .luna-tmp and removed after validation. |
| `/usr/bin/time -l` was blocked reading kern.clockrate; elapsed time and peak RSS came from an isolated Python resource sampler. |

## Comparison

| Normalized records compared | Composite verdict | Overall verdict |
| --- | --- | --- |
| `SF sets and normalized DA, FN, FNDA, BRDA keyed records and counts` | PASS | PASS WITH TEST EXCLUSIONS |

## Real-derived workload

The captured real shard set totals 11,225,342 bytes. Scaling note: real captures were below 50 MB; re-rooted copies raise the measured input size. The measured set uses captured shards re-rooted under distinct `/real-scaled/` prefixes when expansion was required; this is synthetic scaling and is not presented as real project input.

| Label | Source bytes | Measured bytes | Prefixes | Shards | lcovmerge s / MiB RSS / MB/s | lcov s / MiB RSS / MB/s | Correctness | External sort | Order / -j |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |
| REAL-DERIVED SCALED | 11,225,342 | 55,482,820 | 5 | 75 | 0.433 / 19.42 / 128.2 | 8.091 / 454.27 / 6.9 | PASS | PASS (8 MiB, --jobs 1, more than 32 inputs) | PASS |

Output record presentation order may differ from LCOV by design, as described in `docs/LIMITATIONS.md`. Comparisons normalize SF paths and compare keyed DA, FN, FNDA, and BRDA records and counts.


## Minimal compatibility reproducer

On this Apple gcov and LCOV toolchain, genhtml rejects the trace because line 1 is marked unhit while a branch on that line is hit. The trace exercises a capture-consistency issue; no lcovmerge source change was made.

From the repository root, recreate the source and trace under scratch and run genhtml:

```sh
mkdir -p .luna-tmp/branch-repro/src
printf '%s\n' 'int branch(int x) { if (x) return 1; return 0; }' > .luna-tmp/branch-repro/src/branch.c
cat > .luna-tmp/branch-repro/input.info <<'EOF'
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
genhtml --branch-coverage --quiet --source-directory .luna-tmp/branch-repro \
  --output-directory .luna-tmp/branch-repro-html .luna-tmp/branch-repro/input.info
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
