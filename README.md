# lcovmerge

**Merge LCOV tracefiles without keeping the whole coverage map in memory.**

`lcovmerge` is a small C11 command-line tool for combining `.info` files from parallel test runs, build
shards, or large generated coverage reports. It streams inputs, uses bounded record memory, spills sorting
work to temporary files, and writes an LCOV tracefile that existing report tools can consume.

```sh
lcovmerge shard-*.info -o coverage/merged.info
genhtml coverage/merged.info --output-directory coverage/html
```

## 20-second quickstart

Once published, download and unpack the archive for your operating system from
the [v1.0.0 release](https://github.com/megasoft1978/lcovmerge/releases/tag/v1.0.0),
then put `lcovmerge` on `PATH`. For Linux x86-64:

```sh
asset=lcovmerge-1.0.0-linux-x86_64.tar.gz
base=https://github.com/megasoft1978/lcovmerge/releases/download/v1.0.0
curl -fL "$base/$asset" -o "$asset"
curl -fL "$base/SHA256SUMS" -o SHA256SUMS
grep " $asset$" SHA256SUMS | sha256sum -c -
tar -xzf "$asset"
./lcovmerge --version
./lcovmerge coverage/shard-*.info -o coverage/merged.info
```

This example requires the release files to be published. If the release page is not available yet, build from
the source repository after it is published; package-manager and container distributions are not published at
this documentation snapshot.

## Why another LCOV merger?

<!-- BAZEL-EVIDENCE:START -->
Large tracefiles can make a merge job the most memory hungry part of a coverage pipeline. The Bazel
CoverageOutputGenerator issue reports that combining two LCOV files of several hundred megabytes required a
Java heap above 10 GB. It also shows a 745 MB single-file case failing with a heap cap of 5 GB. This is one
reported workload, not a universal result for Bazel.
<!-- BAZEL-EVIDENCE:END -->

See [Bazel issue 26383](https://github.com/bazelbuild/bazel/issues/26383).

Mozilla also reported an aggregation worker killed near its memory limit while running grcov. That issue was
resolved by increasing the worker size; it does not establish that grcov is generally unreliable or that
lcovmerge would fix that pipeline. [Mozilla Bug 2070106](https://bugzilla.mozilla.org/show_bug.cgi?id=2070106)

The benchmarks below compare generated inputs on one local machine. No project-derived capture was available,
so REAL is marked not measured. These results apply to those inputs and that host only; see [methodology and
caveats](docs/BENCHMARKS.md).

## Install

Release assets are named `lcovmerge-1.0.0-<os>-<arch>.tar.gz`; Windows uses `.zip`. Check the published
[release page](https://github.com/megasoft1978/lcovmerge/releases) and verify downloads against `SHA256SUMS`
before installing.

| Platform | Asset |
| --- | --- |
| Linux x86-64 | `lcovmerge-1.0.0-linux-x86_64.tar.gz` (static musl) |
| Linux ARM64 | `lcovmerge-1.0.0-linux-aarch64.tar.gz` (static musl) |
| macOS Apple Silicon | `lcovmerge-1.0.0-macos-arm64.tar.gz` |
| macOS Intel | `lcovmerge-1.0.0-macos-x86_64.tar.gz` |
| Windows x86-64 | `lcovmerge-1.0.0-windows-x86_64.zip` |

### Linux and macOS

Replace `linux-x86_64` below with the matching asset target from the table. The archives contain the
executable at their root.

```sh
asset=lcovmerge-1.0.0-linux-x86_64.tar.gz
base=https://github.com/megasoft1978/lcovmerge/releases/download/v1.0.0
curl -fL "$base/$asset" -o "$asset"
curl -fL "$base/SHA256SUMS" -o SHA256SUMS
grep " $asset$" SHA256SUMS | sha256sum -c -
tar -xzf "$asset"
install -Dm755 lcovmerge "$HOME/.local/bin/lcovmerge"
```

On macOS, use `shasum -a 256 -c` in place of `sha256sum -c` if GNU coreutils are not installed. macOS releases
are native binaries, not Linux binaries.

### Windows

Download the Windows zip and `SHA256SUMS` from the release page. In PowerShell, verify the file digest against
the matching line in `SHA256SUMS`, then extract it:

```powershell
$asset = 'lcovmerge-1.0.0-windows-x86_64.zip'
$base = 'https://github.com/megasoft1978/lcovmerge/releases/download/v1.0.0'
Invoke-WebRequest "$base/$asset" -OutFile $asset
Invoke-WebRequest "$base/SHA256SUMS" -OutFile SHA256SUMS
Get-FileHash $asset -Algorithm SHA256
Expand-Archive $asset -DestinationPath "$env:LOCALAPPDATA\lcovmerge"
```

Compare the printed hash with the Windows asset entry before extraction. Add the destination directory to
`PATH` if desired.

### Homebrew, Scoop, Docker, GitHub Action, and install script

- **Homebrew:** no official formula or tap is published yet; use the macOS archive.
- **Scoop:** no official bucket or manifest is published yet; use the Windows zip.
- **Docker:** no official image is published. You can package the static Linux
  binary in a local `scratch` image:

  ```dockerfile
  FROM scratch
  COPY lcovmerge /lcovmerge
  ENTRYPOINT ["/lcovmerge"]
  ```

  Build from the matching Linux archive, then run it with your coverage files
  mounted into the container:

  ```sh
  docker build -t local/lcovmerge .
  mkdir -p coverage
  docker run --rm -v "$PWD:/work" -w /work local/lcovmerge \
    --tmpdir /work/coverage coverage/*.info -o coverage/merged.info
  ```

- **GitHub Action:** no reusable Action is published. The [GitHub Actions
  example](docs/INTEGRATIONS.md#github-actions-sharded-tests) downloads and
  verifies the Linux archive in a workflow.
- **Install script:** no official remote installer is published. Download the
  matching archive and verify `SHA256SUMS` before installing it.

Do not treat third-party packages as official distributions.

### Build from source

The source repository is [github.com/megasoft1978/lcovmerge](https://github.com/megasoft1978/lcovmerge). After
it is available, clone it and build from its root with a C11 compiler and `make`:

```sh
git clone https://github.com/megasoft1978/lcovmerge.git
cd lcovmerge
make
```

Linux release binaries are statically linked against musl. macOS binaries dynamically link Apple's
`libSystem.B.dylib`. Windows uses static linking for runtime libraries and calls Windows system APIs. Windows
runtime verification is pending a passing Windows CI run; local validation includes a Wine test run, the PE
format, and the cross-build.

## Usage

```text
lcovmerge [options] a.info b.info ... -o out.info
```

Inputs may be LCOV tracefiles, `-` for standard input, or `@listfile` for a list of tracefile paths. Use `-o
-` to write the merged tracefile to standard output. See the full [option reference](docs/USAGE.md) and [man
page](man/lcovmerge.1).

### Merge sharded test outputs

Keep each shard's LCOV output as an artifact, then merge once in a final CI job:

```sh
lcovmerge artifacts/coverage/*.info -o coverage/merged.info
genhtml coverage/merged.info --output-directory coverage/html
```

The report tools remain responsible for HTML and coverage summaries. lcovmerge only combines tracefiles.

### Rebase source paths

When workers write different checkout roots into `SF:` records, map them before merging:

```sh
lcovmerge \
  --rebase /build/agent-01/project=/workspace/project \
  --rebase /build/agent-02/project=/workspace/project \
  artifacts/coverage/*.info \
  -o coverage/merged.info
```

Use `--include` and `--exclude` to select source-file paths after rebasing. Patterns are matched against `SF:`
paths; quote globs so the shell does not expand them first.

### Bazel output

For Bazel configurations that produce a combined LCOV report, merge additional LCOV files or shard outputs
with lcovmerge before passing the result to `genhtml` or an upload step:

```sh
bazel coverage --combined_report=lcov //...
mkdir -p coverage
lcovmerge bazel-out/_coverage/_coverage_report.dat extra-shard.info -o coverage/combined.info
```

The exact Bazel flags and report location depend on the Bazel version and rules in use. lcovmerge does not
replace Bazel's coverage instrumentation or generator. See [Bazel integration
notes](docs/INTEGRATIONS.md#bazel).

## Benchmarks

The following table is generated from [`data/benchmarks.json`](data/benchmarks.json). Cells show wall time,
peak RSS, and throughput when an output completed. Synthetic datasets and comparison caveats are described in
[docs/BENCHMARKS.md](docs/BENCHMARKS.md).

<!-- BENCH-CONTEXT:START -->
Host: macOS 27.0 arm64, Apple M5 Max, 18 cores, 64 GiB RAM; uncontrolled; fixtures generated immediately
before measurements. Measured: 2026-10-09. lcovmerge runs by dataset: S: 1, M: 5, L: 1, XL-single: 1,
PATH-HEAVY: 1, REAL: 0. Each comparison tool ran 1 time(s) per dataset. Per-command timeout: 30 minutes.
<!-- BENCH-CONTEXT:END -->

<!-- BENCHMARKS:START -->
<!-- Generated by tools/render_benchmarks.py from data/benchmarks.json. -->
| Dataset (input bytes) | lcovmerge 1.0.0 | lcov 2.6 | lcov-result-merger 6.0.0 |
| --- | ---: | ---: | ---: |
| M (1,118,686,233) | 3.316 s / 5.12 MiB / 337.4 MB/s / OK | 43.352 s / 610.20 MiB / 25.8 MB/s / OK | 145.171 s / 2,381.97 MiB / 7.7 MB/s / OK |
| L (4,853,340,843) | 27.657 s / 25.67 MiB / 175.5 MB/s / OK | 195.144 s / 1,430.23 MiB / 24.9 MB/s / OK | 604.280 s / 3,106.12 MiB / 8.0 MB/s / OK |
| XL-single (744,992,058) | 1.370 s / 2.27 MiB / 543.7 MB/s / OK | 35.378 s / 3,577.38 MiB / 21.1 MB/s / OK | 0.170 s / 607.67 MiB / n/a / ERROR_1 |
| PATH-HEAVY (130,000,000) | 1.912 s / 19.69 MiB / 68.0 MB/s / OK | 89.383 s / 6,711.17 MiB / n/a / ERROR_1 | 1,800.002 s / 842 MiB sampled in final 308 s; full-run peak unavailable / n/a / TIMEOUT |
<!-- BENCHMARKS:END -->

The comparator results and failed cases apply only to these generated fixtures and this host. The full report
records exact sizes, command status, measurement limits, and unavailable data.

## How it works

1. Read each tracefile incrementally and parse LCOV records.
2. Apply source-path rewriting and include/exclude filters.
3. Accumulate a bounded amount of record data in memory.
4. Spill sortable runs to temporary storage and merge equal coverage keys.
5. Recompute summary rows and stream the result to the output.

```text
  shard A       shard B       shard C
     \             |             /
      +------ streaming parser ------+
                      |
              bounded record arena
                      |
        temporary sorted runs (tmpdir)
                      |
             merge equal coverage keys
                      |
              merged tracefile
                      |
                genhtml / CI
```

The memory limit applies to the record arena. Process overhead and temporary-file I/O require additional
resources.

## Comparison

| Tool | Main role | What it provides | Fit for merging large `.info` files |
| --- | --- | --- | --- |
| **lcovmerge** | LCOV tracefile merger | Bounded-memory merge, path rebasing and filtering, LCOV output | Focused on combining existing tracefiles; no capture or HTML reporting; no MC/DC semantics. |
| **lcov 2.x** | Coverage collection and tracefile/report tooling | `lcov -a` aggregates counts by matching test and filename; capture, filtering, summaries, branch and MC/DC support across the suite | Use when you need lcov's capture/configuration/report semantics. The benchmark compares only `lcov -a` on the listed fixture set. |
| **lcov-result-merger** | Node.js LCOV file merger | Glob-based input and output path normalization | Simple fit for Node pipelines. The benchmark measured version 6.0.0 with this harness and reports fixture-specific outcomes. |
| **grcov** | Coverage collection, conversion, aggregation, and reporting | Processes multiple coverage formats and emits LCOV, HTML, Cobertura, and other outputs | Broader than a tracefile-only merger; useful when collecting/compiler-format conversion is needed. Benchmark fixture compatibility varied. |
| **Bazel CoverageOutputGenerator / LcovMerger** | Bazel coverage report generation | Combines coverage reports in Bazel's coverage workflow | Integrated with Bazel's action pipeline. It was not included in the benchmark; see the reported memory issue linked above. |

See [Comparison](docs/COMPARISON.md) for source links and scope details.

## Limits

- lcovmerge merges existing LCOV tracefiles; it does not collect `.gcda`, `.profraw`, or other raw coverage
data and does not generate HTML.
- It does not implement MC/DC semantics. Unknown extension records are not interpreted as coverage semantics;
`--warn-unknown` can report them.
- Input lines, including source paths and function names, are limited to 1 MiB.
- `--jobs` accepts 1 through 32; by default the worker count is the lesser of four and the detected processor
  count, constrained by the memory limit. Sorted regular-file inputs use the single-threaded stream merge; if
  ordering, input type, or stream-path memory is unsuitable, the configured external-sort workers are used.
- `--mem-limit` bounds the record arena. It does not include process overhead or temporary sort files.
- Conflicting non-empty checksums warn by default and select the lexicographically smallest checksum. Use
  `--strict-checksum` when a disagreement should return input-error status 2.
- Performance measurements are from one macOS host and generated inputs; `REAL` was not measured. Windows
  runtime verification remains pending a passing Windows CI run.

See [FAQ](docs/FAQ.md) for troubleshooting and [usage](docs/USAGE.md) for exact options.

## Contributing

Bug reports and feature requests belong in the [issue
tracker](https://github.com/megasoft1978/lcovmerge/issues). Before changing behavior, describe the input shape
and expected LCOV output. See [CONTRIBUTING.md](CONTRIBUTING.md), [Code of Conduct](CODE_OF_CONDUCT.md), and
[Security](SECURITY.md).

## License

MIT. See [LICENSE](LICENSE).
