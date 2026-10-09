# lcovmerge

**Merge every LCOV shard. Keep the CI runner's memory under control.**

`lcovmerge` is a focused C11 command-line merger for teams that already produce LCOV tracefiles and need to
combine them after tests finish. It fits monorepos and sharded CI, including Bazel, CMake, and Gradle C++
builds when each job emits `.info` files. It handles the merge step; your collector and report or upload tools
keep their jobs.

[Get the release](https://github.com/megasoft1978/lcovmerge/releases/latest) ·
[Read the usage guide](docs/USAGE.md) ·
[See what it does not promise](docs/LIMITATIONS.md)

## Why use lcovmerge?

| Memory-bounded | Deterministic | Fast and portable |
| --- | --- | --- |
| Streams tracefiles, caps the record arena, and spills sort runs to temporary disk. | Emits canonical output that is byte-identical across input order and job settings when options and inputs are the same. | One small C11 executable per target; no Node.js or JVM runtime in the merge job. Linux builds are static musl binaries. |

The memory option limits the record arena, not total process RSS. Leave room for process overhead and temporary
disk space. See [limits and compatibility](docs/LIMITATIONS.md) before switching an established workflow.

## Replace the merge command

If your pipeline only needs to combine existing `.info` files, the merge step can look like this:

```sh
# Before
lcov -a shard-a.info -a shard-b.info -o coverage/merged.info

# After
lcovmerge shard-*.info -o coverage/merged.info

# Keep the report step
genhtml coverage/merged.info --output-directory coverage/html
```

lcovmerge writes an LCOV tracefile for downstream tools that consume LCOV, including `genhtml`, Codecov, and
Coveralls. Some SonarQube analyzers also accept LCOV; SonarQube's C/C++ analyzer expects gcov or llvm-cov
reports, so keep that compiler-specific flow for C/C++ analysis. This is a focused merge step, not a promise
that every `lcov -a` option or output byte is interchangeable. See [the comparison](docs/COMPARISON.md) and
[FAQ](docs/FAQ.md#is-the-output-identical-to-lcov--a).

## Try it in 30 seconds

Download the archive for your platform from the [latest release](https://github.com/megasoft1978/lcovmerge/releases/latest).
For Linux x86-64, verify the checksum, unpack the binary, and merge:

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

On macOS, replace `sha256sum -c` with `shasum -a 256 -c` and select the `macos-arm64` or `macos-x86_64`
archive. Windows uses the `windows-x86_64` zip. The release page provides the available assets; if a release
archive is not available yet, [build from source](#build-from-source).

## CI and distribution

### GitHub Actions

After downloading each test shard as an artifact, use the reusable action to merge them. The action downloads
the selected release and verifies its SHA-256 checksum before running it.

```yaml
- name: Merge coverage
  uses: megasoft1978/lcovmerge@v1
  with:
    files: |
      coverage/unit/*.info
      coverage/integration/*.info
    output: coverage/merged.info
    mem-limit: 256M
```

See the complete [GitHub Actions recipe](docs/INTEGRATIONS.md#github-actions) and examples for
[GitLab](docs/INTEGRATIONS.md#gitlab-ci), [Bazel](docs/INTEGRATIONS.md#bazel), and
[CMake with gcov](docs/INTEGRATIONS.md#cmake-and-gcov).

### Docker

Release tags publish a multi-architecture image to GitHub Container Registry. Mount the workspace so the
container can read shard files and write its result:

```sh
docker run --rm -v "$PWD:/work" -w /work ghcr.io/megasoft1978/lcovmerge:v1.0.0 \
  --tmpdir /tmp coverage/shard-*.info -o coverage/merged.info
```

### Homebrew and Scoop

Formula and manifest templates live in the repository. The release workflow opens a package-update pull
request when its tap token is configured; package-manager installation depends on that update being merged.
Check the [Homebrew/Scoop tap](https://github.com/megasoft1978/homebrew-tap) for the current manifest. Once it
contains the release you need, install with:

```sh
brew install megasoft1978/homebrew-tap/lcovmerge
scoop bucket add lcovmerge https://github.com/megasoft1978/homebrew-tap
scoop install lcovmerge
```

If the package is not present there yet, use the verified release archive instead.

## What the benchmarks show

These generated-input comparisons give one concrete view of memory and elapsed time. The table is rendered
from [`data/benchmarks.json`](data/benchmarks.json); failed and timed-out runs stay visible, and the report
explains the limits of the comparison.

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

These measurements are not a promise for every project or machine. The current set uses generated inputs; no
project-derived REAL capture was measured. See the [full benchmark report](docs/BENCHMARKS.md) for tool
versions, run counts, methodology, and caveats.

<!-- BAZEL-EVIDENCE:START -->
Large tracefiles can make a merge job the most memory hungry part of a coverage pipeline. The Bazel
CoverageOutputGenerator issue reports that combining two LCOV files of several hundred megabytes required a
Java heap above 10 GB. It also shows a 745 MB single-file case failing with a heap cap of 5 GB. This is one
reported workload, not a universal result for Bazel.
<!-- BAZEL-EVIDENCE:END -->

The [Bazel issue #26383](https://github.com/bazelbuild/bazel/issues/26383) is useful context for the
memory cost of a large coverage merge. It is a report about Bazel's own `CoverageOutputGenerator`, not a
benchmark of lcovmerge or a claim that this tool changes Bazel's implementation.

## When not to use it

- Keep `lcov -a` when you depend on its testcase, checksum, MC/DC, or configuration semantics, or need output
  identical to lcov. lcovmerge canonicalizes records and has documented semantic differences.
- Keep your existing collector when you need to capture raw `.gcda`, `.profraw`, or other compiler/runtime
  data. lcovmerge accepts already-generated LCOV tracefiles only.
- Keep a tool with MC/DC support when reports rely on MC/DC accounting semantics. lcovmerge does not make
  that guarantee.
- Check the worker's free disk and temporary directory. External sorting can use temporary storage
  proportional to the input; `--mem-limit` is not a total RSS or disk cap.
- Do not expect `-j` to speed up one large input file. Parallel parsing is across files, and the merge stage
  itself is single-threaded.

See [all limitations and intentional differences](docs/LIMITATIONS.md) and the
[comparison](docs/COMPARISON.md).

## Trust and maintenance

- CI runs the golden, malformed-input, differential, determinism, and documentation checks; separate jobs run
  ASan/UBSan and a fuzz smoke test. See the [CI workflow](.github/workflows/ci.yml).
- Run the same local checks with `make test`, `make asan`, and `make fuzz`.
- Release assets include `SHA256SUMS` and a CycloneDX SBOM, with GitHub build provenance. The Action verifies
  the matching release checksum before execution. See the [release workflow](.github/workflows/release.yml).
- The project is MIT licensed and does not bundle an external font, analytics script, or tracking pixel on
  its static site.

## Build from source

Requires a C11 compiler and `make`:

```sh
git clone https://github.com/megasoft1978/lcovmerge.git
cd lcovmerge
make
```

Then run `bin/lcovmerge --help`. The [usage guide](docs/USAGE.md) documents the full CLI, including path
rebasing, filtering, list files, temporary-directory selection, and strict checksum handling.
