# Benchmarks

[`data/benchmarks.json`](../data/benchmarks.json) is the published measurement record. The generated tables in
this file, the README, and the site data are rendered from it.

The 2026-10-11 v1.0.2 XXL rerun used the `XXL` workload in `bench/run.py`; its JSON, text report, and runner log were retained under ignored `.cache/xxl/`.

For a local rerun of the deterministic S and M fixtures, see the [reproduction instructions](REPRODUCE.md).

## Methodology

<!-- BENCH-METHOD:START -->
Host: macOS 27.0 arm64, Apple M5 Max, 18 cores, 64 GiB RAM; no explicit cache flush; paired hyperfine warmups;
machine load uncontrolled. Measured: 2026-10-11. lcovmerge runs by dataset: S: 10, M: 5, L: 2, XL-single: 5,
PATH-HEAVY: 5, REAL: 0. Comparison runs per tool: 1; Per-command timeout: 30 minutes.
<!-- BENCH-METHOD:END -->

- Synthetic fixtures were generated with `bench/run.py`, `tools/gen-lcov.py`, and `tools/gen-path-heavy.py`. Set
  `TMPDIR=./.luna-tmp`; the harness removes generated inputs when it exits.
- Baseline and final lcovmerge runs used the same generated input per dataset. `hyperfine 2.0.0` performed
  warmup runs and measured repeats; wall time is the median and peak resident memory is the maximum measured
  `memory_peak_resident` value.
  S used three warmups; the other paired cases used one.
- LCOV 2.6 was measured once per dataset by `bench/run.py`. It ran after the baseline merger read the fixtures,
  so this is a fixed-order, cache-warm comparison rather than a cold-cache result. The PATH-HEAVY LCOV command
  exited with status 1.
- Apple `/usr/bin/time -l` could not query `kern.clockrate` under the host policy. lcovmerge peak resident-memory
  values therefore come from hyperfine. Successful LCOV values in the table are from an earlier recorded
  measurement and were not refreshed in this run; PATH-HEAVY current-run peak resident memory is unavailable.
- The generated-fixture table below is not a pairwise semantic comparison against LCOV. The separate
  project-derived validation compares normalized results with LCOV and records genhtml compatibility,
  determinism, and temporary-file sort checks in [real-projects.md](validation/real-projects.md).

## Public CI compatibility investigation

A separate comparison reran three public CI sets on 2026-10-11 with lcovmerge 1.0.2: 66 shards totaling
658 MB. Strict LCOV 2.6 `lcov -a` failed 3/3; lcovmerge 1.0.2 completed 3/3, with 12
joined-boundary recovery warnings on one set. The original lcovmerge 1.0.1 run found the joined-boundary bug;
the affected set's earlier 7 `DA`, 18 `FN`, and 18 `FNDA` differences are gone against diagnostic LCOV.
Across all sets, normalized comparisons showed aggregate residuals of 86 lcovmerge-only `SF`, 713 LCOV-only
`DA`, 2 lcovmerge-only `FN`, 0 `FNDA` differences, and 9,877 `BRDA` key/value differences. The earlier
investigation root-caused these classes to documented policies: LCOV's branch-only `DA` synthesis, empty `SF`
handling, `BRDA` block-ID remapping, and duplicate legacy `FN` handling.

The rerun compared normalized `SF` keys and keyed `DA`, `FN`, `FNDA`, and `BRDA` values/counts against
diagnostic LCOV outputs. It did not make strict LCOV succeed, did not compare byte-for-byte, did not cover
summary, testcase, extension, or other unlisted records, and did not newly isolate those causes. This is
compatibility evidence, not proof of equivalence or a performance result. See [the compatibility notes](COMPATIBILITY.md#what-has-been-verified)
for the aggregate counts and policy table.

## Linux measurements

The manually triggered `Linux benchmark` workflow runs S, M, and L on `ubuntu-24.04`, with an optional
PATH-HEAVY run. Each tool gets one hyperfine warmup and repeat measurements. On Linux, peak resident memory comes
from GNU `/usr/bin/time -v` for each measured run. The workflow artifact contains host metadata and aggregate JSON and
text reports; generated tracefiles stay in the runner's ignored `.luna-tmp` directory and are removed by the
benchmark runner.

After running the workflow, download its `linux-benchmark-<run-id>-<attempt>` artifact into an isolated directory
under `.luna-tmp`, then merge the aggregate JSON with:

```sh
python3 -I bench/ingest-ci-results.py .luna-tmp/<download-dir>/benchmark.json
```

The ingester appends or replaces that run under the additive `host_results` field in the published measurement record and the
site data mirror. It leaves the existing macOS host and measurements intact. The workflow runs only on
`workflow_dispatch`.

## Windows measurements

The manually triggered `Windows benchmark` workflow runs S, M, and L on `windows-latest`. It rebuilds the
shipped Windows release executable with checksum-verified Zig 0.17.0 and `scripts/build-all.sh`, then builds a
second UCRT64 executable with MSYS2 GCC at `-O2`. Native Windows Python generates the same S/M/L fixture
parameters with `tools/gen-lcov.py --benchmark-compatible --checksums`; the generated inputs are shared by both
binaries. Each binary gets one warmup and then 5 S runs, 3 M runs, and 2 L runs. The JSON and text artifact list
the median and minimum wall time for each binary. Peak resident memory uses Windows `GetProcessMemoryInfo`; it is marked
unknown if any measured run cannot be sampled. No LCOV comparator is run, and these Windows measurements are a
separate host entry rather than a cross-platform speed claim. The large-dataset timings are disk-bound CI numbers
that describe this runner and I/O path, not Windows desktop performance generally.

Download a completed `windows-benchmark-<run-id>-<attempt>` artifact into `.luna-tmp`, then merge it with:

```sh
python3 -I bench/ingest-ci-results.py .luna-tmp/<download-dir>/benchmark.json
```

The ingester adds the Windows build comparison as a separately labeled entry under `host_results` in the
canonical data and site mirror. The workflow runs only on `workflow_dispatch`; trigger it manually when a fresh
Windows measurement is needed.

## Real-project validation

The project-derived captures and LCOV comparisons are tracked separately from the generated-fixture benchmark
table in [the validation report](validation/real-projects.md) and [`data/real-projects.json`](../data/real-projects.json).
The current real shard set is 11.2 MB. Its 55.5 MB `REAL-DERIVED SCALED` run uses re-rooted
copies to increase path diversity and input size; it is explicitly synthetic scaling, not real project data.
The separate public-CI compatibility investigation is summarized in [COMPATIBILITY.md](COMPATIBILITY.md); it is
not part of the generated `REAL` benchmark row or the measured project-derived workload below.

To reproduce a final lcovmerge measurement for M:

```sh
make
TMPDIR=./.luna-tmp python3 -I bench/run.py --workload M --hyperfine-runs 5 \
  --skip-lcov --report .luna-tmp/current-M.txt
```

The optional `--binary` argument selects another built executable, and `--skip-lcov` avoids rerunning a comparator
whose result is already recorded. `--hyperfine-runs N` adds one warmup followed by N measured runs.

## Results

Each tool cell is elapsed time / peak resident memory / throughput / command status. Rounded input sizes are
shown in the first column; exact measurements remain in data/benchmarks.json. Throughput is shown only after a
successful exit. The XXL run processed 20.3 GB across 128 shards. lcovmerge 1.0.2 reached 131.1 MB/s at
31.05 MiB peak resident memory; normalized outputs matched LCOV for 480 SF records and 10,771,200 metric keys.

<!-- BENCHMARKS:START -->
<!-- Generated by tools/render_benchmarks.py from data/benchmarks.json. -->
| Dataset (input size) | lcovmerge 1.0.2 | lcov 2.6 | lcov-result-merger 6.0.0 |
| --- | ---: | ---: | ---: |
| XXL (20.3 GB) | 2m 34.7s / 31.05 MiB / 131.1 MB/s / OK | 14m 53.5s / 2.66 GiB / 22.7 MB/s / OK | — / — / n/a / Not measured |
| S (100.0 MB) | 260 ms / 2.95 MiB / 384.6 MB/s / OK | 5.32 s / 229.8 MiB / 18.8 MB/s / OK | 17.52 s / 437.7 MiB / 5.7 MB/s / OK |
| M (1.12 GB) | 3.49 s / 5.12 MiB / 320.7 MB/s / OK | 50.54 s / 610.2 MiB / 22.1 MB/s / OK | 2m 25.2s / 2.33 GiB / 7.7 MB/s / OK |
| L (4.85 GB) | 27.72 s / 25.06 MiB / 175.1 MB/s / OK | 3m 23.2s / 1.40 GiB / 23.9 MB/s / OK | 10m 4.3s / 3.03 GiB / 8.0 MB/s / OK |
| XL-single (745 MB) | 1.46 s / 2.28 MiB / 511.8 MB/s / OK | 37.80 s / 3.49 GiB / 19.7 MB/s / OK | 170 ms / 607.7 MiB / n/a / ERROR_1 |
| PATH-HEAVY (130 MB) | 2.12 s / 20.23 MiB / 61.3 MB/s / OK | 1m 38.8s / peak resident memory unavailable for current failed run / n/a / ERROR_1 | 30m / 842 MiB sampled in final 5m 8s; full-run peak resident memory unavailable / n/a / TIMEOUT |
| REAL (unavailable) | — / — / n/a / NOT MEASURED | — / — / n/a / NOT MEASURED | — / — / n/a / NOT MEASURED |
<!-- BENCHMARKS:END -->

## Experimental profile-guided build

This compiler-tuning experiment is separate from the standard tool comparisons above. It used the current
checkout (`91da2e967ecf`) on a Mac Studio with an Apple M5 Max, macOS 27.0 arm64, and Apple clang 21.0.0.
An instrumented binary processed generated Dataset M once to produce an LLVM profile. The profile was then
used to build with `-O2 -mcpu=native -fprofile-instr-use`; that same profile was tested on S and M. Each
configuration had one warmup and three measured hyperfine runs. The table reports median wall time and median
peak resident memory.

| Dataset | Portable `-O2` | Native `-O2`, no PGO | Native `-O2` with M-trained PGO | Speed change vs portable | Peak-memory change vs portable |
| --- | ---: | ---: | ---: | ---: | ---: |
| S (100.0 MB) | 247.6 ms / 3.00 MiB | — | 212.8 ms / 3.02 MiB | 14.1% faster | +16 KiB |
| M (1.12 GB) | 3.655 s / 5.28 MiB | 3.645 s / 5.30 MiB | 3.263 s / 5.30 MiB | 10.7% faster | +16 KiB |

The M PGO build was 10.5% faster than the native `-O2` build without PGO. Output files had identical SHA-256
hashes for each compared pair. These are two generated workloads on one host; the optimized executable is
machine-specific and was not used in the standard comparator table. This experiment does not establish a
cross-platform or general speed claim. The exact run records, profile input, flags, and output hashes are in
[`data/benchmarks.json`](../data/benchmarks.json); temporary binaries and profiles remain in ignored `.cache/`.

### Recorded timing ratios

Baseline and final lcovmerge binaries processed the same generated input for each paired hyperfine run. lcov 2.6
ran once per dataset. These ratios describe the listed runs on this host; they are not a general speed claim. The
LCOV PATH-HEAVY run failed, so no ratio is shown for it.

| Dataset | Baseline lcovmerge | Final lcovmerge | vs baseline | lcov 2.6 | vs lcov |
| --- | ---: | ---: | ---: | ---: | ---: |
| S | 264 ms | 260 ms | 1.02x | 5.32 s | 20.45x |
| M | 3.59 s | 3.49 s | 1.03x | 50.54 s | 14.49x |
| L | 28.92 s | 27.72 s | 1.04x | 3m 23.2s | 7.33x |
| XL-single | 1.43 s | 1.46 s | 0.98x | 37.80 s | 25.97x |
| PATH-HEAVY | 2.23 s | 2.12 s | 1.05x | 1m 38.8s / ERROR_1 | n/a |

### Dataset M job settings

<!-- SCALING:START -->
| lcovmerge jobs argument | Time | Peak resident memory | Throughput |
| ---: | ---: | ---: | ---: |
| default | 3.62 s | 5.16 MiB | 309.1 MB/s |
| -j1 | 4.16 s | 5.16 MiB | 268.9 MB/s |
| -j2 | 3.70 s | 5.17 MiB | 302.1 MB/s |
| -j4 | 3.70 s | 5.17 MiB | 302.7 MB/s |
| -j8 | 3.73 s | 5.16 MiB | 300.2 MB/s |
<!-- SCALING:END -->

The sorted regular-file fast path streams with one job and ignores the jobs setting. These measurements record the
observed command variation for the requested values; they do not measure parallel sorting across input files.

## Caveats

<!-- BENCH-CAVEATS:START -->
- Measurements cover one macOS 27 arm64 Apple M5 Max host; results are not a cross-platform claim.
- S, M, L, XL-single, and PATH-HEAVY are generated fixtures. REAL was not measured because no project-derived
  capture was available.
- lcovmerge baseline and final times use paired hyperfine runs on the same deterministic fixture; run counts
  are listed per workload. Cache warmups were used, but no explicit cache flush was applied.
- lcov 2.6 ran once per dataset after the baseline merge. Its PATH-HEAVY run exited with status 1 after 1m
  38.8s; no valid speedup or current-run peak resident memory is reported for that failure.
- The XXL lcov comparator time and peak resident memory were measured in the 2026-10-11 rerun; successful lcov
  memory figures for earlier datasets remain from the earlier recorded benchmark.
- lcov-result-merger was not measured in the XXL rerun; earlier datasets retain their separately recorded
  measurements.
- Dataset M's sorted regular inputs use one merge worker; --jobs results show run variation, not the effect of
  adding workers.
- The external Bazel issue is context only and was not measured with these commands.
<!-- BENCH-CAVEATS:END -->

The benchmark set is not a conformance suite. It cannot establish equivalence across every LCOV extension or
project workload. lcovmerge preserves and combines MC/DC rows but does not promise LCOV-equivalent MC/DC counts.
The [Bazel issue](https://github.com/bazelbuild/bazel/issues/26383) describes a separate memory-pressure case; it
is not a benchmark result for this repository.
