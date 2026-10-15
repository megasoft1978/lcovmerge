# Benchmarks

[`data/benchmarks.json`](../data/benchmarks.json) is the published measurement record. The generated tables in
this file, the README, and the site data are rendered from it.

The original benchmark tables are historical measurements of the C implementation (versions 1.0.0–1.1.0), collected before the Rust port. They do not describe Rust performance. The separate Rust comparison below preserves those records.

## Rust port comparison

On 2026-10-14, the actual default `make` binary was compared on an Apple M5 Max running macOS 27.0.1. Rust 1.99 used portable profile-guided optimization (PGO), trained on seed 42; the timed fixtures used seed 1. The primary baseline was the retained C v1.1.0 PGO binary at commit `33ced0b26adeffe47ed2d2041c8558a667566a51`, built with Apple Clang 21, `-O2 -mcpu=native`, and its historical dataset-M profile. A second baseline rebuilt C commit `035e35a595edf50d7de6737188f417667dfb4f1a` with `-O3` and no PGO. The raw report includes both baselines. These fixtures and builds are separate from the historical tables.

| Workload | C 1.1 PGO, 1 job | Rust PGO, 1 job | C 1.1 PGO, 4 jobs | Rust PGO, 4 jobs |
| --- | ---: | ---: | ---: | ---: |
| Sorted, 104,570,468 bytes / 8 shards | 210 ms | 207 ms | 210 ms | 145 ms |
| Unsorted, 11,719,548 bytes / 1 shard | 71 ms | 65 ms | 71 ms | 64 ms |

Values are medians of 19 measured runs per variant after one warm-up, interleaved in seeded order with warm filesystem caches, `--mem-limit 64M`, and named output files. Every output was byte-identical across implementations and job counts; temporary directories were empty after each run. Maximum measured resident memory for sorted input was 2.47/2.94 MiB (C PGO/Rust, one job) and 2.47/4.86 MiB (four jobs). For unsorted input it was 18.81/38.75 MiB (one job) and 18.53/38.73 MiB (four jobs). Child CPU and peak memory came from `wait4`; wall time used Python's monotonic clock.

The single-job sorted medians are close; those medians alone do not establish performance parity under the uncertainty gate below. Rust reduced the four-job sorted median by 31% and the unsorted median by 9%. It also beat the rebuilt `-O3` baseline's medians on both fixtures. Unsorted Rust uses more resident memory. Initial unprofiled `cargo build --release` measurements were slower; use `make` for the optimized build. This comparison does not establish a universal speed or memory improvement. [Raw measurements, hashes, fixture parameters, and validation scope](../data/rust-port-validation.json) identify the measured build and its limits.

To repeat the performance gate with two binaries and your own inputs, run:

```sh
python3 bench/compare-builds.py --baseline /path/to/c-lcovmerge \
  --candidate bin/lcovmerge --jobs 1 4 --runs 19 \
  --workdir .cache/comparison -- input1.info input2.info
```

The gate interleaves paired runs, checks every output hash and temporary-run cleanup, and saves raw samples, hashes, resource measurements, and seeded bootstrap uncertainty under `.cache/`. Its acceptance boundary is a candidate/baseline timing ratio of 1. An interval wholly below 1 is an observed pass; one wholly above 1 is a regression; one spanning 1 is inconclusive. Regression and inconclusive results return nonzero. Run on an idle machine and investigate close results; 3% is a diagnostic noise band, not a regression allowance. These intervals describe the measured binaries and workloads, and do not account for arbitrary code layouts or prove universal parity. The earlier `--max-ratio` tolerance switch is removed.

### Clean macOS runner

The manual `bench-windows.yml` workflow also supports `mode=native-macos`.
It builds the candidate on `macos-15` and uses a SHA-256-pinned bundle containing
the original frozen Rust executable from commit `12bf1ec` and the byte-identical
held-out inputs above. C1 commit `33ced0b` is rebuilt on the runner with native
PGO and a recorded seed-42 training set. This is a reconstructed C1 baseline;
the historical executable was tuned for the original host and is not assumed
portable to another CPU.

The disposable macOS benchmark VM disables Spotlight indexing before building;
the setup log is retained. The job checks an idle-runner preflight after
compilation. It allows up to 30
preflight attempts for build activity to settle, retaining every diagnostic log;
the last two CPU samples must both show at least 90% idle before any timing starts.
It then runs 99 seeded
paired measurements for each baseline, sorted/unsorted workload and worker count
(1 and 4). This higher sample count is selected before measurement to improve
precision after earlier inconclusive comparisons. The acceptance rule is unchanged.
Reports retain binaries, input hashes/bundle, training inputs/profile, compiler and
host identities, raw samples, resource metrics and uncertainty intervals for 90 days.
A separate compact reports artifact makes results available without downloading
the retained binaries and input bundle.
The temporary unpublished transport release is removed after those artifacts are
retained. Results on a different host are reported separately from earlier local
measurements, including inconclusive or losing results.

### Isolated native parity, 2026-10-14

Candidate `9c3421c` was measured on an Apple M1 virtual macOS runner using the
original frozen Rust binary and held-out inputs, plus a runner-native PGO rebuild
of C1. Five preflight attempts were retained; only the fifth met the unchanged
90% idle requirement. Each combination then used 99 seeded paired runs. Ratios
are candidate elapsed time divided by baseline elapsed time; the acceptance
rule requires the entire 95% interval to be strictly below 1. A bound exactly
at 1 remains inconclusive, matching the comparison harness.

| Baseline | Input | Jobs | Rust / baseline (95% interval) | Result |
| --- | --- | ---: | ---: | --- |
| C1 | sorted | 1 | 0.990 [0.984, 0.997] | observed-pass |
| C1 | sorted | 4 | 0.697 [0.689, 0.711] | observed-pass |
| C1 | unsorted | 1 | 0.848 [0.844, 0.852] | observed-pass |
| C1 | unsorted | 4 | 0.854 [0.849, 0.862] | observed-pass |
| Frozen Rust | sorted | 1 | 1.009 [0.997, 1.015] | inconclusive |
| Frozen Rust | sorted | 4 | 1.016 [1.007, 1.024] | regression |
| Frozen Rust | unsorted | 1 | 0.987 [0.982, 0.992] | observed-pass |
| Frozen Rust | unsorted | 4 | 0.985 [0.981, 0.992] | observed-pass |

All C1 comparisons passed. Frozen-Rust sorted single-job parity remains
inconclusive, and sorted four-job performance regressed by about 1.6%; the
candidate therefore did not pass the full native release gate. Unsorted Rust
median RSS was about 44 MiB versus C1's 18 MiB. Four-job sorted merging used
about 0.50 CPU seconds versus C1's 0.37 while reducing elapsed time. These are
measured trade-offs, not universal speed or memory guarantees.

[Raw comparison reports, paired ratios, metrics, build/input/profile hashes and
preflight history](../data/native-parity-validation.json) retain the evidence.
The [full artifacts](https://github.com/megasoft1978/lcovmerge/actions/runs/38635325400)
include binaries, training data, logs and per-invocation measurements. Temporary
disk metrics sample the selected temporary directory only; output-parent staging
files are excluded, and short-lived peaks can be missed.

The subsequent `ec8dac0` candidate also missed the gate. Its frozen-Rust sorted
one-job ratio was 1.022 [1.008, 1.033]; the four-job sorted comparison and both
unsorted comparisons were inconclusive. Against C1, one-job sorted parity was
inconclusive, while the other three comparisons passed. All outputs matched.
[The complete retained report](../data/native-parity-ec8dac0.json) includes every
comparison. Further runs control Spotlight indexing explicitly; these earlier
results remain part of the record.

With Spotlight disabled, `92cbd7a` passed all four C1 comparisons and both
unsorted frozen-Rust comparisons. The frozen-Rust sorted ratios still missed
the gate: 1.005 [1.001, 1.011] at one job and 1.018 [1.006, 1.039] at four jobs.
All outputs matched. [The controlled-run report](../data/native-parity-92cbd7a.json)
retains all eight results and the indexing/preflight diagnostics; the release
gate remains unmet.

The `4fa9427` byte-search candidate had paired median ratios below 1 in all eight
comparisons. All four C1 comparisons and frozen-Rust sorted one-job comparison
passed. Frozen-Rust sorted four-job and both unsorted comparisons remained
inconclusive. [Every result and interval](../data/native-parity-4fa9427.json) is
retained; this is not a completed native gate.

Before collecting further timings, a fixed precision confirmation is specified
for those three inconclusive rows: 2,001 new pairs for sorted four-job input and
1,001 new pairs for each unsorted job count. Two independent macOS VMs run these
cases in parallel. They reuse the exact candidate, frozen baseline, profiles and
held-out input bundle from the original artifact, verifying their hashes and the
unchanged Rust/build sources. There is no rebuild or profile training. The other
five comparisons retain their original passing evidence. New samples are neither
pooled with the original samples nor stopped early; all three new rows must pass
the unchanged rule. This follows the sampling design's allowance for additional
precision after inconclusive measurements.

The fixed confirmation completed on 2026-10-15. Both unsorted rows passed:
0.98245 [0.97726, 0.98774] at one job and 0.98335 [0.97843, 0.98759]
at four jobs. Sorted four-job input remained inconclusive after all 2,001
pairs: 0.99832 [0.99326, 1.00300]. Together with the original five passes,
seven of eight comparisons pass; the native release gate remains unmet.
[The complete confirmation reports](../data/native-parity-confirmation-4fa9427.json)
retain both cases, raw paired ratios, provenance, artifact identities and setup
diagnostics. No samples were pooled or discarded and no boundary was relaxed.

The unsorted RSS increase versus C1 is a storage-layout trade-off: the former
byte arena becomes a capacity-sized Rust record vector with separately owned
strings. The external-sort path retains vector capacity across flushes; string
allocations and allocator/runtime overhead also contribute to resident memory.
The record budget accounts for vector capacity and string storage, rather than
capping process RSS. Sorted parallel merging also spends additional CPU on
workers and staged output assembly to reduce elapsed time. These costs remain
part of the comparison; an allocation-level attribution has not been measured.

## LCOV profile comparison

The opt-in merge profile requires separate measurements from native mode. Use
the pinned LCOV source/install identity described in
[the conformance contract](../tests/compat/CONTRACT.md), valid synthetic inputs,
and an idle host:

```sh
python3 bench/profile-comparison.py --binary bin/lcovmerge --lcov lcov \
  --source-root .cache/lcov-compat-implementation/oracle-source \
  --workdir .cache/profile-comparison-j1 --jobs 1 --runs 19 \
  --input /absolute/path/to/synthetic.info
```

Repeat `--input` for multiple tracefiles. Add `--branch-coverage` or
`--mcdc-coverage` when measuring those families. The harness compares every
warmup and seeded paired run with confirmed XS and pure-Perl LCOV backends,
preserving testcase, function, branch, MC/DC, checksum, version, and summary
semantics. LCOV emits profiling JSON on each invocation so backend selection
can be verified; its exact command is retained. Both tools use an explicit
empty configuration and the requested worker count.

Use `--no-function-coverage` for line-only datasets so LCOV does not require
absent function coverpoints. The option is passed to both tools and recorded.
The manual workflow's `profile-linux` mode generates six cases: line-only,
mixed records, testcase-heavy, large single source, 64-shard single source, and
gzip-compressed mixed records. It characterizes all six with 19 paired runs at
one and four workers against XS and pure Perl. `profile-extra` selects only the
last two cases. `all-performance` runs all six Linux cases alongside the native
macOS job on separate runners. Fixture smoke checks with a single sample verify
semantics only and are excluded from performance evidence.

Reports retain commands, input/binary hashes, output hashes, process RSS,
temporary disk peaks, and bootstrap intervals for median log timing ratios.
Inputs and per-run outputs/logs remain under the supplied work directory.
One-run functional smoke checks do not establish performance; the report marks
sample counts below 19 accordingly. This comparison does not replace the
native C1 and frozen-Rust gates or establish universal speed parity.

For deterministic sort work and I/O counters, run the ignored Rust probe:

```sh
cargo test compat::run::sort::tests::metadata_single_source_io_probe -- --ignored --nocapture
```

It sorts 100,000 descending rows with a 16-byte source prefix plus 8-byte line
key and 256-byte value, using a 1 MiB component budget and one and four jobs.
The JSON output reports runs, passes, merge bytes read, bytes written, fan-in,
and workers. Read counters cover sort I/O and exclude the consumer's final
cursor reads. This deterministic probe is not timing evidence.

### Isolated Linux characterization, 2026-10-14

Rust commit `31be2d8` was measured on an otherwise idle four-core Linux Azure
runner against pinned LCOV 2.6 XS and pure Perl. Each row uses 19 seeded paired
runs after warmup, with matching normalized output and verified backend selection
on every invocation. Ratios are Rust elapsed time divided by reference elapsed
time; below 1 is faster. Brackets are 95% bootstrap intervals for the median
paired ratio.

| Workload | Jobs | Rust / XS (95% interval) | Rust / Perl (95% interval) |
| --- | ---: | ---: | ---: |
| lines | 1 | 0.534 [0.530, 0.542] | 0.290 [0.287, 0.293] |
| lines | 4 | 0.783 [0.777, 0.797] | 0.374 [0.368, 0.377] |
| mixed | 1 | 1.956 [1.943, 1.978] | 0.822 [0.813, 0.835] |
| mixed | 4 | 2.441 [2.426, 2.469] | 0.970 [0.967, 0.979] |
| testcases | 1 | 0.331 [0.326, 0.335] | 0.175 [0.172, 0.177] |
| testcases | 4 | 0.326 [0.324, 0.331] | 0.173 [0.173, 0.175] |
| single-source | 1 | 0.697 [0.685, 0.701] | 0.301 [0.298, 0.306] |
| single-source | 4 | 1.239 [1.231, 1.254] | 0.462 [0.459, 0.469] |
| many-shards | 1 | 0.336 [0.334, 0.340] | 0.214 [0.212, 0.218] |
| many-shards | 4 | 0.381 [0.374, 0.388] | 0.280 [0.278, 0.284] |
| gzip | 1 | 1.769 [1.763, 1.787] | 0.770 [0.760, 0.781] |
| gzip | 4 | 2.329 [2.307, 2.345] | 0.947 [0.945, 0.962] |

Rust was faster than pure Perl in these measured cases. XS was faster for mixed
records and gzip at both job counts, and for the large single source at four
jobs. These results characterize these synthetic inputs and this runner; they
establish neither universal superiority nor native-mode parity with C1.

[Measured samples, input/binary/profile hashes, commands, resource metrics and
oracle identity](../data/lcov-profile-validation.json) preserve the provenance.
Wall and CPU measurements include CLI startup; RSS is `wait4`'s `ru_maxrss`,
not the simultaneous sum of a process tree. Temporary disk peaks sample only the selected
temporary directory every 20 ms; output-parent staging files are excluded, and
shorter peaks can be missed. The
[workflow artifacts](https://github.com/megasoft1978/lcovmerge/actions/runs/38633742948)
retain full outputs, logs, source fixtures and PGO feedback for 90 days.

## Historical measurements

The C lcovmerge 1.1.0 benchmark run on 2026-10-12 used `bench/run.py`; its JSON, text reports, LLVM profile, PGO binary, and per-workload reports are retained under ignored `.cache/bench-1.1.0/`. Comparator values not rerun in that run come from the earlier recorded 2026-10-11 run and before, as the caveats below state.

For a local rerun of the deterministic S and M fixtures, see the [reproduction instructions](REPRODUCE.md).

## Methodology

<!-- BENCH-METHOD:START -->
Host: macOS 27.0 arm64, Apple M5 Max, 18 cores, 64 GiB RAM; no explicit cache flush; paired hyperfine warmups;
machine load uncontrolled. Measured: 2026-10-12. lcovmerge runs by dataset: S: 5, M: 5, L: 5, XL-single: 5,
PATH-HEAVY: 5, REAL: 0, XXL: 3. Dataset M-trained PGO; portable release binaries. Comparison runs per tool: 1;
Per-command timeout: 30 minutes.
<!-- BENCH-METHOD:END -->

- Synthetic fixtures were generated with `bench/run.py`, `tools/gen-lcov.py`, and `tools/gen-path-heavy.py`. Set
  `TMPDIR=./.cache`; the harness removes generated inputs when it exits.
- The PGO profile was trained with one instrumented merge of generated Dataset M, then reused across the listed
  lcovmerge workloads. `hyperfine 2.0.0` used one warmup and measured repeats; wall time is the median and peak
  resident memory is the maximum measured `memory_peak_resident` value. XXL used three measured runs; the other
  generated workloads used five.
- LCOV 2.6 was measured once on XXL in this run. S, M, L, and XL-single LCOV timings and PATH-HEAVY's failed
  run are earlier recorded measurements. The PATH-HEAVY LCOV command exited with status 1; its current-run peak
  resident memory is unavailable.
- lcov-result-merger 6.0.0 on XXL used Node.js 24.18.0 and npm 12.2.0 in the 2026-10-11 run, but did not finish the
  warmup before the 60-minute cutoff; it was not rerun for lcovmerge 1.1.0. Its earlier S/M/L and other workload
  measurements used Node.js 24.14.0. The current package release is 6.0.0; grcov 0.10.8 is listed as current but was not benchmarked.
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
text reports; generated tracefiles stay in the runner's ignored `.cache` directory and are removed by the
benchmark runner.

After running the workflow, download its `linux-benchmark-<run-id>-<attempt>` artifact into an isolated directory
under `.cache`, then merge the aggregate JSON with:

```sh
python3 -I bench/ingest-ci-results.py .cache/<download-dir>/benchmark.json
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

Download a completed `windows-benchmark-<run-id>-<attempt>` artifact into `.cache`, then merge it with:

```sh
python3 -I bench/ingest-ci-results.py .cache/<download-dir>/benchmark.json
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

The largest published performance result is a generated 20.3 GB workload. Project-derived correctness and
performance checks cover eight pinned public projects; their composite is 11.2 MB and was measured on one
host. To judge how much the speed and memory gains help real CI, we need
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
TMPDIR=./.cache python3 -I bench/run.py --workload M --hyperfine-runs 5 \
  --skip-lcov --report .cache/current-M.txt
```

The optional `--binary` argument selects another built executable, and `--skip-lcov` avoids rerunning a comparator
whose result is already recorded. `--hyperfine-runs N` adds one warmup followed by N measured runs.

## Results

Each tool cell is elapsed time / peak resident memory / throughput / command status. Rounded input sizes are
shown in the first column; exact measurements remain in data/benchmarks.json. Throughput is shown only after a
successful exit. All lcovmerge rows use the Apple Clang profile-guided host build trained on Dataset M. On XXL,
lcovmerge 1.1.0 processed 20.3 GB across 128 shards in 68.27 s at 297.0 MB/s and 6.33 MiB peak resident
memory; normalized outputs matched LCOV for 480 SF records and 10,771,200 metric keys. lcov-result-merger 6.0.0 did not finish its
warmup before the 60-minute cutoff.

<!-- BENCHMARKS:START -->
<!-- Generated by tools/render_benchmarks.py from data/benchmarks.json. -->
| Dataset (input size) | lcovmerge 1.1.0 (PGO) | lcov 2.6 | lcov-result-merger 6.0.0 |
| --- | ---: | ---: | ---: |
| XXL (20.3 GB) | 1m 8.3s / 6.33 MiB / 297.0 MB/s / OK | 13m 47.6s / 2.69 GiB / 24.5 MB/s / OK | 1h 15.8s / 2.34 GiB peak sampled during run; full-run peak resident memory unavailable / n/a / TIMEOUT |
| S (100.0 MB) | 197 ms / 2.48 MiB / 508.6 MB/s / OK | 5.32 s / 229.8 MiB / 18.8 MB/s / OK | 17.52 s / 437.7 MiB / 5.7 MB/s / OK |
| M (1.12 GB) | 2.94 s / 3.19 MiB / 380.5 MB/s / OK | 50.54 s / 610.2 MiB / 22.1 MB/s / OK | 2m 25.2s / 2.33 GiB / 7.7 MB/s / OK |
| L (4.85 GB) | 14.01 s / 4.20 MiB / 346.5 MB/s / OK | 3m 23.2s / 1.40 GiB / 23.9 MB/s / OK | 10m 4.3s / 3.03 GiB / 8.0 MB/s / OK |
| XL-single (745 MB) | 1.18 s / 2.20 MiB / 633.7 MB/s / OK | 37.80 s / 3.49 GiB / 19.7 MB/s / OK | 170 ms / 607.7 MiB / n/a / ERROR_1 |
| PATH-HEAVY (130 MB) | 2.76 s / 17.78 MiB / 47.2 MB/s / OK | 1m 38.8s / peak resident memory unavailable for current failed run / n/a / ERROR_1 | 30m / 842 MiB sampled in final 5m 8s; full-run peak resident memory unavailable / n/a / TIMEOUT |
| REAL (unavailable) | — / — / n/a / NOT MEASURED | — / — / n/a / NOT MEASURED | — / — / n/a / NOT MEASURED |
<!-- BENCHMARKS:END -->

## Recorded timing ratios

These ratios compare current (2026-10-12, lcovmerge 1.1.0) profile-guided lcovmerge medians with earlier
portable-build lcovmerge measurements and the listed LCOV 2.6 results, which are earlier recorded measurements. The inputs and host match, but the measurements were collected in separate runs.
Treat the ratios as descriptive for this machine. PATH-HEAVY is slower with PGO; the LCOV PATH-HEAVY run failed,
so no LCOV ratio is shown for it.

| Dataset | Earlier portable lcovmerge | Profile-guided lcovmerge | vs portable | lcov 2.6 | vs lcov |
| --- | ---: | ---: | ---: | ---: | ---: |
| S | 264 ms | 197 ms | 1.34x faster | 5.32 s | 27.04x |
| M | 3.59 s | 2.94 s | 1.22x faster | 50.54 s | 17.19x |
| L | 28.92 s | 14.01 s | 2.06x faster | 3m 23.2s | 14.51x |
| XL-single | 1.43 s | 1.18 s | 1.22x faster | 37.80 s | 32.16x |
| PATH-HEAVY | 2.23 s | 2.76 s | 0.81x (slower) | 1m 38.8s / ERROR_1 | n/a |

### Dataset M job settings

<!-- SCALING:START -->
| lcovmerge jobs argument | Time | Peak resident memory | Throughput |
| ---: | ---: | ---: | ---: |
| default | 2.94 s | 3.19 MiB | 380.5 MB/s |
| -j1 | 2.91 s | 3.20 MiB | 385.0 MB/s |
| -j2 | 3.03 s | 3.20 MiB | 369.7 MB/s |
| -j4 | 2.91 s | 3.20 MiB | 383.8 MB/s |
| -j8 | 2.87 s | 3.20 MiB | 389.2 MB/s |
<!-- SCALING:END -->

In the measured 1.1.0 build the sorted regular-file fast path streams with one job and ignores the jobs setting; later builds thread it only for an explicit `-j N` of 2 or more (see [USAGE](USAGE.md#-j-n---jobs-n)). These measurements record the
observed command variation for the requested values; they do not measure parallel sorting across input files.

## Caveats

<!-- BENCH-CAVEATS:START -->
- Measurements cover one macOS 27 arm64 Apple M5 Max host; results are not a cross-platform claim.
- S, M, L, XL-single, and PATH-HEAVY are generated fixtures. The generated benchmark matrix REAL row remains
  unmeasured; separate public-project captures and comparisons are recorded in
  docs/validation/real-projects.md.
- All displayed lcovmerge measurements use a Dataset M-trained Apple Clang profile-guided host build. Release
  binaries remain portable target builds; this is not a cross-platform speed claim.
- lcovmerge timing uses one hyperfine warmup and five measured runs for S, M, L, XL-single, and PATH-HEAVY;
  XXL uses one warmup and three measured runs. Cache warmups were used, but no explicit cache flush was
  applied.
- lcov 2.6 ran once on the XXL input on 2026-10-12 and completed in 13m 47.6s; the 2026-10-11 run recorded 18m
  0.6s on the same host with machine load uncontrolled. Its S, M, L, XL-single, and PATH-HEAVY values are
  earlier recorded measurements that were not rerun; the PATH-HEAVY run exited with status 1 after 1m 38.8s,
  so no valid speedup or current-run peak resident memory is reported for that failure.
- lcov-result-merger 6.0.0 did not finish its XXL warmup before the 60-minute cutoff in the 2026-10-11 run and
  was not rerun for 1.1.0. It was stopped with SIGTERM after 1h 15.8s wall time; no measured run completed.
  Periodic peak resident memory samples peaked at 2.34 GiB, but the full-run peak is unavailable. Other
  lcov-result-merger rows are earlier measurements made with Node v24.14.0.
- Dataset M's sorted regular inputs use one merge worker; --jobs results show run variation, not the effect of
  adding workers.
- grcov 0.10.8 is identified as the current release but was not included in the measured benchmark runs.
- The external Bazel issue is context only and was not measured with these commands.
<!-- BENCH-CAVEATS:END -->

The benchmark set is not a conformance suite. It cannot establish equivalence across every LCOV extension or
project workload. lcovmerge preserves and combines MC/DC rows but does not promise LCOV-equivalent MC/DC counts.
The [Bazel issue](https://github.com/bazelbuild/bazel/issues/26383) describes a separate memory-pressure case; it
is not a benchmark result for this repository.
