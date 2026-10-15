# lcovmerge

Merge LCOV `.info` shards for a local report or upload.

This Rust CLI and library preserve native behavior. Version 1.2.0 is the first Rust release.

## Is it a fit?

Use when CI leaves shards for a local consumer. See the [decision guide](docs/DECISION.md).

## Try it

Build with stable Rust, Python 3.11+, and Make:

```sh
set -eu
git clone https://github.com/megasoft1978/lcovmerge.git
cd lcovmerge && make
```

Merge and report:

```sh
# The shell expands the wildcard.
./bin/lcovmerge coverage/shard-*.info -o coverage/merged.info
genhtml coverage/merged.info --output-directory coverage/html
```

Experimental opt-in LCOV 2.6 merge profile (quote the glob for profile expansion): `./bin/lcovmerge --lcov-compat=2.6 -a 'coverage/shard-*.info' -o coverage/merged.info`.

This version-pinned merge profile excludes LCOV collection, reset, extraction, report generation, and Perl callbacks; it does not install or replace `lcov`. See the [contract](docs/LCOV-PROFILE.md) and [profile benchmarks](docs/BENCHMARKS.md#isolated-linux-characterization-2026-10-14).

<!-- README-BENCHMARK:START -->
Native Rust · macOS arm64 · 2026-10-14; 19-run medians after one warm-up.

Sorted, 104.6 MB: 207 ms (1 job), 145 ms (4 jobs). Unsorted, 11.7 MB: 64.6 ms (1 job), 64.4 ms (4 jobs).

Outputs matched byte for byte in every run. One host and these fixtures do not predict every workload. Version 1.2.0 is the first Rust release. See the [Rust comparison and raw measurements](docs/BENCHMARKS.md#rust-port-comparison).
<!-- README-BENCHMARK:END -->

## Before switching

Native mode has documented policy differences from `lcov -a`; the experimental profile has a version-pinned merge contract. Compare outputs and reports first. See the [migration checklist](docs/MIGRATING-FROM-LCOV.md) and [compatibility notes](docs/COMPATIBILITY.md).

Build details: [quick start](docs/USAGE.md) · [release requirements](docs/RELEASING.md#rust-cross-build-requirements) · [benchmarks](docs/BENCHMARKS.md) · [private workload measurements](docs/REAL-WORKLOADS.md).

MIT licensed.
