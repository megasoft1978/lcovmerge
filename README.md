# lcovmerge

Merge LCOV `.info` shards for a local report or upload.

This Rust CLI and library target lcovmerge's established command-line and output behavior. Published v1.1.0 downloads predate the Rust migration.

## Is it a fit?

Use when CI leaves shards and a local consumer needs one file. See the [decision guide](docs/DECISION.md).

## Try it

Build the Rust CLI with stable Rust, Python 3.11+, and Make:

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

## Performance evidence

See the [Rust port comparison](docs/BENCHMARKS.md#rust-port-comparison) for measured speed and memory trade-offs.

<!-- README-BENCHMARK:START -->
Native Rust · macOS arm64 · 2026-10-14; 19-run medians after one warm-up.

Sorted, 104.6 MB: 207 ms (1 job), 145 ms (4 jobs). Unsorted, 11.7 MB: 64.6 ms (1 job), 64.4 ms (4 jobs).

Outputs matched byte for byte in every run. One host and these fixtures do not predict every workload. Published v1.1.0 downloads predate the Rust migration. See the [Rust comparison and raw measurements](docs/BENCHMARKS.md#rust-port-comparison).
<!-- README-BENCHMARK:END -->

See [all results and methods](docs/BENCHMARKS.md) or [measure private inputs](docs/REAL-WORKLOADS.md).

## Before switching

Not a drop-in replacement for `lcov -a`; compare output and reports before switching. See the [migration checklist](docs/MIGRATING-FROM-LCOV.md) and [limits](docs/LIMITATIONS.md).

## Build from source

Install stable Rust and Python 3.11+. `make` builds a portable PGO CLI and installs `llvm-tools` when needed; `make test` verifies it. See [release requirements](docs/RELEASING.md#rust-cross-build-requirements).

## Documentation

[Quick start](docs/USAGE.md) · [CI recipes](docs/RECIPES.md) · [decision guide](docs/DECISION.md) · [benchmark results](docs/BENCHMARKS.md) · [project direction](docs/ROADMAP.md)

MIT licensed.
