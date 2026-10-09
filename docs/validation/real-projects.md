# Real project validation

## Toolchain

| Host | Compiler | `gcc` command | Coverage flags | lcov | genhtml | Measurement |
| --- | --- | --- | --- | --- | --- | --- |
| macOS 27.0 arm64 | Apple clang version 21.0.0 (clang-2100.3.34.2) | Apple clang version 21.0.0 (clang-2100.3.34.2) | `--coverage` | lcov: LCOV version 2.6-0 | genhtml: LCOV version 2.6-0 | `time.perf_counter and isolated resource.getrusage(RUSAGE_CHILDREN); /usr/bin/time -l blocked: sysctl kern.clockrate access denied` |

## Results

| Project | Commit | Shards | Input MB | Output MB | lcovmerge seconds / peak RSS MiB | lcov seconds / peak RSS MiB | lcov verdict | Tests | genhtml | Order / -j | Rewrites | 8 MiB sort |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- | --- | --- | --- |
| zlib | 51b7f2abdade71cd9bb0e7a373ef2610ec6f9daf | 2 | 0.180 | 0.091 | 0.008 / 4.08 | 0.136 / 42.7 | PASS | PASS x2 | PASS | PASS | PASS | PASS |
| lua | 1ab3208a1fceb12fca8f24ba57d6e13c5bff15e3 | 2 | 0.578 | 0.293 | 0.010 / 6.77 | 0.206 / 47.11 | PASS | PASS x2 (portable mode; upstream skips nonportable tests) | PASS | PASS | PASS | PASS |
| cjson | acc76239bee01d8e9c858ae2cab296704e52d916 | 2 | 0.202 | 0.102 | 0.007 / 4.41 | 0.178 / 43.05 | PASS | PASS x2 | PASS | PASS | PASS | PASS |
| json-c | 41a55cfcedb54d9c1874f2f0eb07b504091d7e37 | 2 | 0.209 | 0.106 | 0.007 / 4.41 | 0.169 / 43.05 | PASS | PASS x2 | PASS | PASS | PASS | PASS |
| libyaml | 2c891fc7a770e8ba2fec34fc6b545c672beb37e6 | 2 | 0.050 | 0.025 | 0.030 / 3.11 | 0.139 / 40.91 | PASS | PASS x2 | PASS | PASS | PASS | PASS |
| tinyxml2 | 9148bdf719e997d1f474be6bcc7943881046dba1 | 2 | 0.281 | 0.141 | 0.006 / 4.47 | 0.157 / 43.91 | PASS | PASS x2 | PASS | PASS | PASS | PASS |
| REAL composite | six pinned commits | 12 | 1.500 | 0.757 | 0.021 / 10.08 | 0.398 / 56.42 | PASS | PASS | PASS | PASS | n/a | per-project runs PASS |

## Capture notices

| Observation |
| --- |
| tinyxml2 LCOV capture bypassed inconsistent function-boundary checks from Apple gcov. |
| Apple gcov/lcov emitted non-fatal unsupported function-boundary notices during capture. |
| Additional non-fatal LCOV capture warnings occurred. |
| `gcc` reports Apple Clang too; a distinct GNU compiler comparison was unavailable on this host. |
| Lua ran with its documented `_port=true` mode; its upstream suite skips nonportable tests in this mode. |
| Each project used two independent full-suite captures; external-sort checks used 34 inputs (above the 32-input direct-path limit), comparing 8 MiB -j1 with LCOV and reverse-order / 32 MiB -j4 outputs bytewise. |
| A separate libpng 1.6.50 test run passed, but Apple gcov's DA/BRDA consistency mismatch prevented standard downstream genhtml validation; a synthetic path-scrubbed repro is retained under scratch. |
| `/usr/bin/time -l` was blocked reading kern.clockrate; elapsed time and peak RSS came from an isolated Python resource sampler. |

## Comparison

| Normalized records compared | Composite verdict | Overall verdict |
| --- | --- | --- |
| `SF sets and normalized DA, FN, FNDA, BRDA keyed records and counts` | PASS | PASS |

Output record presentation order may differ from LCOV by design, as described in `docs/LIMITATIONS.md`. Comparisons normalize SF paths and compare keyed DA, FN, FNDA, and BRDA records and counts.
