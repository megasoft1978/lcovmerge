# lcovmerge

**Parallel shards. One coverage file.**

Merge exported `.info` shards into one deterministic file for your report step.

This Rust CLI and library are also a GitHub Action. They merge exported LCOV only. Version 1.2.0 is the first Rust release.

## GitHub Actions

Download artifacts, then merge before the report step:

```yaml
- name: Merge coverage
  uses: megasoft1978/lcovmerge@v1.2.0
  with:
    files: coverage/shards/*.info
    output: coverage/merged.info

- name: Generate report
  run: genhtml coverage/merged.info --output-directory coverage/html
```

The Action checksums its binary; see the [workflow recipe](docs/RECIPES.md#github-actions).

## Build from source

Stable Rust, Python 3.11+, Make:

```sh
set -eu
git clone https://github.com/megasoft1978/lcovmerge.git
cd lcovmerge && make
```

## Use a release binary

Download a [checksummed release](https://github.com/megasoft1978/lcovmerge/releases/tag/v1.2.0) or use the [install guide](docs/USAGE.md#install).

```sh
# The shell expands the pattern; lcovmerge receives the matching files.
./lcovmerge coverage/shards/*.info -o coverage/merged.info
genhtml coverage/merged.info --output-directory coverage/html
```

## Why add a merge step?

- **One handoff:** one tracefile for the next job.
- **Repeatable:** same output bytes across input order and job count.
- **Resource controls:** stream sorted files or budget sorting memory and temp space.

<!-- README-BENCHMARK:START -->
Native Rust · macOS arm64 · 2026-10-14; 19-run medians after one warm-up.

Sorted, 104.6 MB: 207 ms (1 job), 145 ms (4 jobs). Unsorted, 11.7 MB: 64.6 ms (1 job), 64.4 ms (4 jobs).

Outputs matched byte for byte in every run. One host and these fixtures do not predict every workload. Version 1.2.0 is the first Rust release. See the [Rust comparison and raw measurements](docs/BENCHMARKS.md#rust-port-comparison).
<!-- README-BENCHMARK:END -->

## Compatibility

Native mode differs from `lcov -a`. The experimental LCOV 2.6 profile covers merge only; it is not a full LCOV replacement. See the [contract](docs/LCOV-PROFILE.md) and [migration guide](docs/MIGRATING-FROM-LCOV.md).

[Usage](docs/USAGE.md) · [Action](action/README.md) · [Benchmarks](docs/BENCHMARKS.md) · [Website](https://megasoft1978.github.io/lcovmerge/)

MIT licensed.
