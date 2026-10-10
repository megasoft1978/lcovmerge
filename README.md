# lcovmerge

[![CI](https://github.com/megasoft1978/lcovmerge/actions/workflows/ci.yml/badge.svg)](https://github.com/megasoft1978/lcovmerge/actions/workflows/ci.yml)
[![Release v1.0.2](docs/site/badges/release.svg)](https://github.com/megasoft1978/lcovmerge/releases/latest)
[![MIT License](docs/site/badges/license.svg)](LICENSE)

**What:** lcovmerge combines LCOV `.info` files—plain-text records of covered lines, functions, and branches—into one file.

**Who:** Use it after parallel test jobs export `.info` files when a later local step requires one file.

**When not:** Skip it if the consumer accepts shards, native merging is available, a hosted service combines reports, or `lcov -a` is already fast enough.

## GitHub Actions matrix

Replace the matrix patterns and test/export command for your project. Each job uploads one uniquely named LCOV artifact. This complete workflow is canonical in [CI and exporter recipes](docs/RECIPES.md#github-actions). The 256M setting is an example record-memory budget, not an RSS cap; sorting also needs temporary disk.

```yaml
name: coverage

on: [push, pull_request]

permissions:
  contents: read

jobs:
  test-shard:
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        include:
          - shard: unit
            pattern: "test_unit*.py"
          - shard: integration
            pattern: "test_integration*.py"
    env:
      COVERAGE_FILE: .coverage-${{ matrix.shard }}
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
      - uses: actions/setup-python@v7
        with:
          python-version: "3.12"
      - run: python -m pip install coverage
      - name: Run tests and export LCOV
        run: |
          mkdir -p coverage/shards
          python -m coverage run -m unittest discover -s tests -p "${{ matrix.pattern }}"
          python -m coverage lcov -o "coverage/shards/${{ matrix.shard }}.info"
      - uses: actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9 # v7.0.2
        with:
          name: coverage-${{ matrix.shard }}
          path: coverage/shards/${{ matrix.shard }}.info
          if-no-files-found: error

  merge:
    needs: test-shard
    runs-on: ubuntu-latest
    steps:
      - uses: actions/download-artifact@9000827ccba6bdab643e8b6fd33ac0654aef8333 # v8.0.2
        with:
          pattern: coverage-*
          path: coverage/shards
          merge-multiple: true
      - name: Merge LCOV shards
        uses: megasoft1978/lcovmerge@v1.0.2 # For immutable pinning, replace the tag with the reviewed release commit SHA.
        with:
          files: coverage/shards/*.info
          output: coverage/merged.info
          mem-limit: 256M
      - uses: actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9 # v7.0.2
        with:
          name: merged-coverage
          path: coverage/merged.info
          if-no-files-found: error
```

Connect your existing report step to the merged artifact.

## Try it (Linux x86-64)

This fail-closed installer selects a Linux or macOS archive and verifies one checksum before extraction. Windows runtime verification is pending; see [platform limits](docs/LIMITATIONS.md).

```sh
set -eu
case "$(uname -s):$(uname -m)" in
  Linux:x86_64) target=linux-x86_64 ;;
  Linux:aarch64|Linux:arm64) target=linux-aarch64 ;;
  Darwin:x86_64) target=macos-x86_64 ;;
  Darwin:arm64) target=macos-arm64 ;;
  *) printf 'No release archive for %s/%s\n' "$(uname -s)" "$(uname -m)" >&2; exit 1 ;;
esac
asset="lcovmerge-1.0.2-$target.tar.gz"
base=https://github.com/megasoft1978/lcovmerge/releases/download/v1.0.2
curl -fL "$base/$asset" -o "$asset"
curl -fL "$base/SHA256SUMS" -o SHA256SUMS
awk -v name="$asset" '$2 == name { count++; print } END { if (count != 1) exit 1 }' SHA256SUMS > "$asset.sha256"
if command -v sha256sum >/dev/null 2>&1; then
  sha256sum -c "$asset.sha256"
else
  shasum -a 256 -c "$asset.sha256"
fi
tar -xzf "$asset"
./lcovmerge --version
```

## Does this fit?

Use lcovmerge only when a later local step requires one file. Otherwise keep the shards, merge native profiles with their tool, use hosted aggregation, or keep `lcov -a` when it is fast enough. Export raw profiles to LCOV first.

## Evidence

Eight small project-derived captures (0.051–6.091 MB each; 11.225 MB total) passed normalized checks and genhtml on one macOS host. This is small-input compatibility evidence.

Strict LCOV 2.6 failed on three real public CI datasets. lcovmerge merged them, but normalized output differed from LCOV's diagnostic `--ignore-errors` output on all three; not all differences were isolated to documented policy. This is not a compatibility or speedup claim. One bug found in the investigation was fixed in v1.0.2. See the [migration guide](docs/MIGRATING-FROM-LCOV.md) and [record policies](docs/LIMITATIONS.md).

Generated benchmarks use synthetic inputs and lcovmerge build 1.0.0, while the current release is v1.0.2. Outputs were not compared; timings are host-specific, and timeouts are not speedups. See the [benchmark report](docs/BENCHMARKS.md) for method and statuses.

### Generated benchmark records

<!-- markdownlint-disable MD033 -->
<!-- HERO-PROOF:START -->
> **Dataset M · generated, 32 shards · 1,118,686,233 input bytes**<br>
> lcovmerge 1.0.0: **3.488279 s**, **5,373,952 B peak resident memory** (median of 5 runs).<br>
> lcov 2.6: **50.542 s**, **639,844,352 B peak resident memory** (one run; peak resident memory from an earlier recorded measurement).<br>
> One macOS 27.0 arm64 Apple silicon (exact model unavailable in this sandbox) host; no explicit cache flush; paired hyperfine warmups; machine load uncontrolled.
<!-- HERO-PROOF:END -->
<!-- markdownlint-enable MD033 -->

<!-- BENCH-CONTEXT:START -->
Host: macOS 27.0 arm64, Apple silicon (exact model unavailable in this sandbox), 18 cores, 64 GiB RAM; no
explicit cache flush; paired hyperfine warmups; machine load uncontrolled. Measured: 2026-10-09. lcovmerge
runs by dataset: S: 10, M: 5, L: 2, XL-single: 5, PATH-HEAVY: 5, REAL: 0. Each comparison tool ran 1 time(s)
per dataset. Per-command timeout: 30 minutes.
<!-- BENCH-CONTEXT:END -->

<!-- BENCHMARKS:START -->
<!-- Generated by tools/render_benchmarks.py from data/benchmarks.json. -->
| Dataset (input bytes) | lcovmerge 1.0.0 | lcov 2.6 | lcov-result-merger 6.0.0 |
| --- | ---: | ---: | ---: |
| M (1,118,686,233) | 3.488 s / 5.12 MiB / 320.7 MB/s / OK | 50.542 s / 610.20 MiB / 22.1 MB/s / OK | 145.171 s / 2,381.97 MiB / 7.7 MB/s / OK |
| L (4,853,340,843) | 27.723 s / 25.06 MiB / 175.1 MB/s / OK | 203.235 s / 1,430.23 MiB / 23.9 MB/s / OK | 604.280 s / 3,106.12 MiB / 8.0 MB/s / OK |
| XL-single (744,992,058) | 1.456 s / 2.28 MiB / 511.8 MB/s / OK | 37.803 s / 3,577.38 MiB / 19.7 MB/s / OK | 0.170 s / 607.67 MiB / n/a / ERROR_1 |
| PATH-HEAVY (130,000,000) | 2.122 s / 20.23 MiB / 61.3 MB/s / OK | 98.805 s / peak resident memory unavailable for current failed run / n/a / ERROR_1 | 1,800.002 s / 842 MiB sampled in final 308 s; full-run peak resident memory unavailable / n/a / TIMEOUT |
<!-- BENCHMARKS:END -->

### Small project-derived record

See the [validation record](docs/validation/real-projects.md) for scope, exclusions, and capture warnings.

<!-- REAL-PROJECTS:START -->
8 small project-derived LCOV captures were checked on one macOS 27.0 arm64 host. Each project used multiple shards,
with inputs from 0.051 MB to 6.091 MB; the composite was 11.225 MB across 15 shards. These are
small compatibility checks, not large production workloads. Normalized record comparisons and genhtml
passed for each project. The separate 34-input temporary-file sorting checks and bytewise checks across input order and worker settings passed.
See [the validation record](docs/validation/real-projects.md) for toolchain, capture warnings, and method details.

| Project | Input | lcovmerge time / peak resident memory | LCOV 2.6 time / peak resident memory | Comparison / genhtml |
| --- | ---: | ---: | ---: | --- |
| zlib | 0.180 MB · 2 shards | 0.005 s · 4.08 MiB | 0.108 s · 42.42 MiB | PASS / PASS |
| lua | 0.579 MB · 2 shards | 0.008 s · 6.77 MiB | 0.173 s · 46.78 MiB | PASS / PASS |
| cjson | 0.203 MB · 2 shards | 0.004 s · 4.22 MiB | 0.116 s · 42.83 MiB | PASS / PASS |
| json-c | 0.210 MB · 2 shards | 0.005 s · 4.28 MiB | 0.116 s · 42.95 MiB | PASS / PASS |
| libyaml | 0.051 MB · 2 shards | 0.003 s · 2.86 MiB | 0.093 s · 40.88 MiB | PASS / PASS |
| tinyxml2 | 0.281 MB · 2 shards | 0.005 s · 4.47 MiB | 0.129 s · 43.73 MiB | PASS / PASS |
| sqlite | 6.091 MB · 1 shards | 0.070 s · 16.38 MiB | 0.898 s · 90.41 MiB | PASS / PASS |
| libarchive | 3.631 MB · 2 shards | 0.036 s · 16.83 MiB | 0.580 s · 78.28 MiB | PASS / PASS |
| REAL composite | 11.225 MB · 15 shards | 0.078 s · 20.11 MiB | 1.635 s · 139.55 MiB | PASS / PASS |
<!-- REAL-PROJECTS:END -->

### Separate Bazel context

This issue concerns Bazel's CoverageOutputGenerator, not lcovmerge; it is not evidence that lcovmerge fixes that generator.

<!-- BAZEL-EVIDENCE:START -->
Large LCOV .info files can make a merge job the most memory hungry part of a coverage pipeline. The Bazel
CoverageOutputGenerator issue reports that combining two LCOV files of several hundred megabytes required a
Java heap above 10 GB. It also shows a 745 MB single-file case failing with a heap cap of 5 GB. This is one
reported workload, not a universal result for Bazel.
<!-- BAZEL-EVIDENCE:END -->

## Limits before switching

- `--mem-limit` budgets record memory, not total RSS; sorting needs temporary disk.
- The same build and options produce stable bytes across shard order and worker settings; output is not byte-identical to `lcov -a`.
- lcovmerge merges exported LCOV only. Compare your records and downstream report before switching.

## More

- [Documentation index](docs/README.md)
- [Command-line reference](docs/USAGE.md)
- [CI and exporter recipes](docs/RECIPES.md)
- [Migration guide](docs/MIGRATING-FROM-LCOV.md)
- [Limits and intentional differences](docs/LIMITATIONS.md)
- [Open an issue](https://github.com/megasoft1978/lcovmerge/issues)
- [MIT license](LICENSE)
- [Release verification notes](docs/RECIPES.md#release-archives-and-provenance)
