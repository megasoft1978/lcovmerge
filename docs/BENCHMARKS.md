# Benchmarks

[`data/benchmarks.json`](../data/benchmarks.json) is the published measurement record. The generated tables in
this file, the README, and the site data are rendered from it.

The 2026-10-11 benchmark run used `bench/run.py`; its JSON, text report, LLVM profile, PGO binary, and per-workload reports are retained under ignored `.cache/benchmark-20261011/pgo-default/`.

For a local rerun of the deterministic S and M fixtures, see the [reproduction instructions](REPRODUCE.md).

## Methodology

<!-- BENCH-METHOD:START -->
Host: macOS 27.0 arm64, Apple M5 Max, 18 cores, 64 GiB RAM; no explicit cache flush; paired hyperfine warmups;
machine load uncontrolled. Measured: 2026-10-11. lcovmerge runs by dataset: S: 5, M: 5, L: 5, XL-single: 5,
PATH-HEAVY: 5, REAL: 0, XXL: 3. Dataset M-trained PGO; portable release binaries. Comparison runs per tool: 1;
Per-command timeout: 30 minutes.
<!-- BENCH-METHOD:END -->

- Synthetic fixtures were generated with `bench/run.py`, `tools/gen-lcov.py`, and `tools/gen-path-heavy.py`. Set
  `TMPDIR=./.luna-tmp`; the harness removes generated inputs when it exits.
- The PGO profile was trained with one instrumented merge of generated Dataset M, then reused across the listed
  lcovmerge workloads. `hyperfine 2.0.0` used one warmup and measured repeats; wall time is the median and peak
  resident memory is the maximum measured `memory_peak_resident` value. XXL used three measured runs; the other
  generated workloads used five.
- LCOV 2.6 was measured once on XXL in this run. S, M, L, and XL-single LCOV timings and PATH-HEAVY's failed
  run are earlier recorded measurements. The PATH-HEAVY LCOV command exited with status 1; its current-run peak
  resident memory is unavailable.
- lcov-result-merger 6.0.0 on XXL used Node.js 24.18.0 and npm 12.2.0, but did not finish the warmup before the
  60-minute cutoff. Its earlier S/M/L and other workload measurements used Node.js 24.14.0. The current package
  release is 6.0.0; grcov 0.10.8 is listed as current but was not benchmarked.
- Apple `/usr/bin/time -l` could not query `kern.clockrate` under the host policy; lcovmerge peak resident-memory
  values therefore come from hyperfine.
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

### Evidence still needed

The largest published performance result is a generated 20.3 GB workload. Project-derived correctness checks
exist, but their inputs are much smaller. To judge how much the speed and memory gains help real CI, we need
optional reports from varied workflows: input size and shard count, host and operating system, merge time and
peak RSS for the existing merger and lcovmerge, plus whether the downstream report accepted each output.
Small and medium inputs matter too; they show when the simpler existing path is already sufficient.

No coverage file is needed. The [private-input measurement guide](REAL-WORKLOADS.md) explains how to run the
local `REAL` workload and share only reviewed aggregate values. Do not publish logs or machine-readable reports:
they can contain absolute input paths and host details. Real-workload evidence is opt-in and is not a condition
for using the tool.

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
successful exit. All lcovmerge rows use the Apple Clang profile-guided host build trained on Dataset M. On XXL,
it processed 20.3 GB across 128 shards in 80.97 s at 250.4 MB/s and 6.38 MiB peak resident memory; normalized
outputs matched LCOV for 480 SF records and 10,771,200 metric keys. lcov-result-merger 6.0.0 did not finish its
warmup before the 60-minute cutoff.

<!-- BENCHMARKS:START -->
<!-- Generated by tools/render_benchmarks.py from data/benchmarks.json. -->
| Dataset (input size) | lcovmerge 1.0.2 (PGO) | lcov 2.6 | lcov-result-merger 6.0.0 |
| --- | ---: | ---: | ---: |
| XXL (20.3 GB) | 1m 21s / 6.38 MiB / 250.4 MB/s / OK | 18m 0.6s / 2.85 GiB / 18.8 MB/s / OK | 1h 15.8s / 2.34 GiB peak sampled during run; full-run peak resident memory unavailable / n/a / TIMEOUT |
| S (100.0 MB) | 191 ms / 2.48 MiB / 522.6 MB/s / OK | 5.32 s / 229.8 MiB / 18.8 MB/s / OK | 17.52 s / 437.7 MiB / 5.7 MB/s / OK |
| M (1.12 GB) | 2.90 s / 3.22 MiB / 386.1 MB/s / OK | 50.54 s / 610.2 MiB / 22.1 MB/s / OK | 2m 25.2s / 2.33 GiB / 7.7 MB/s / OK |
| L (4.85 GB) | 14.08 s / 4.25 MiB / 344.7 MB/s / OK | 3m 23.2s / 1.40 GiB / 23.9 MB/s / OK | 10m 4.3s / 3.03 GiB / 8.0 MB/s / OK |
| XL-single (745 MB) | 1.14 s / 2.20 MiB / 655.5 MB/s / OK | 37.80 s / 3.49 GiB / 19.7 MB/s / OK | 170 ms / 607.7 MiB / n/a / ERROR_1 |
| PATH-HEAVY (130 MB) | 2.71 s / 17.78 MiB / 47.9 MB/s / OK | 1m 38.8s / peak resident memory unavailable for current failed run / n/a / ERROR_1 | 30m / 842 MiB sampled in final 5m 8s; full-run peak resident memory unavailable / n/a / TIMEOUT |
| REAL (unavailable) | — / — / n/a / NOT MEASURED | — / — / n/a / NOT MEASURED | — / — / n/a / NOT MEASURED |
<!-- BENCHMARKS:END -->

## Recorded timing ratios

These ratios compare current profile-guided lcovmerge medians with earlier portable-build lcovmerge measurements
and the listed LCOV 2.6 results. The inputs and host match, but the measurements were collected in separate runs.
Treat the ratios as descriptive for this machine. PATH-HEAVY is slower with PGO; the LCOV PATH-HEAVY run failed,
so no LCOV ratio is shown for it.

| Dataset | Earlier portable lcovmerge | Profile-guided lcovmerge | vs portable | lcov 2.6 | vs lcov |
| --- | ---: | ---: | ---: | ---: | ---: |
| S | 264 ms | 191 ms | 1.38x faster | 5.32 s | 27.79x |
| M | 3.59 s | 2.90 s | 1.24x faster | 50.54 s | 17.44x |
| L | 28.92 s | 14.08 s | 2.05x faster | 3m 23.2s | 14.43x |
| XL-single | 1.43 s | 1.14 s | 1.26x faster | 37.80 s | 33.26x |
| PATH-HEAVY | 2.23 s | 2.71 s | 0.82x (slower) | 1m 38.8s / ERROR_1 | n/a |

### Dataset M job settings

<!-- SCALING:START -->
| lcovmerge jobs argument | Time | Peak resident memory | Throughput |
| ---: | ---: | ---: | ---: |
| default | 2.90 s | 3.22 MiB | 386.1 MB/s |
| -j1 | 2.89 s | 3.22 MiB | 387.1 MB/s |
| -j2 | 2.91 s | 3.22 MiB | 385.0 MB/s |
| -j4 | 2.88 s | 3.22 MiB | 388.6 MB/s |
| -j8 | 2.91 s | 3.22 MiB | 384.7 MB/s |
<!-- SCALING:END -->

The sorted regular-file fast path streams with one job and ignores the jobs setting. These measurements record the
observed command variation for the requested values; they do not measure parallel sorting across input files.

## Caveats

<!-- BENCH-CAVEATS:START -->
- Measurements cover one macOS 27 arm64 Apple M5 Max host; results are not a cross-platform claim.
- S, M, L, XL-single, and PATH-HEAVY are generated fixtures. REAL was not measured because no project-derived
  capture was available.
- All displayed lcovmerge measurements use a Dataset M-trained Apple Clang profile-guided host build. Release
  binaries remain portable target builds; this is not a cross-platform speed claim.
- lcovmerge timing uses one hyperfine warmup and five measured runs for S, M, L, XL-single, and PATH-HEAVY;
  XXL uses one warmup and three measured runs. Cache warmups were used, but no explicit cache flush was
  applied.
- lcov 2.6 ran once on the XXL input and completed in 18m 0.6s. Its PATH-HEAVY run exited with status 1 after
  1m 38.8s; no valid speedup or current-run peak resident memory is reported for that failure.
- lcov-result-merger 6.0.0 did not finish its XXL warmup before the 60-minute cutoff. It was stopped with
  SIGTERM after 1h 15.8s wall time; no measured run completed. Periodic peak resident memory samples peaked at
  2.34 GiB, but the full-run peak is unavailable. Other lcov-result-merger rows are earlier measurements made
  with Node v24.14.0.
- Dataset M's sorted regular inputs use one merge worker; --jobs results show run variation, not the effect of
  adding workers.
- grcov 0.10.8 is identified as the current release but was not included in the measured benchmark runs.
- The external Bazel issue is context only and was not measured with these commands.
<!-- BENCH-CAVEATS:END -->

The benchmark set is not a conformance suite. It cannot establish equivalence across every LCOV extension or
project workload. lcovmerge preserves and combines MC/DC rows but does not promise LCOV-equivalent MC/DC counts.
The [Bazel issue](https://github.com/bazelbuild/bazel/issues/26383) describes a separate memory-pressure case; it
is not a benchmark result for this repository.
