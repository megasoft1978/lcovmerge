# lcovmerge

Merge LCOV `.info` shards for a local report or upload.

This Rust CLI and library target lcovmerge's established command-line and output behavior. Published v1.1.0 binaries and benchmarks are from the former C implementation.

## Is it a fit?

Use when CI leaves shards and a local consumer needs one file. See the [decision guide](docs/DECISION.md).

## Try it

Download and verify the [Linux x86-64 release](https://github.com/megasoft1978/lcovmerge/releases):

```sh
set -eu
asset=lcovmerge-1.1.0-linux-x86_64.tar.gz; base=https://github.com/megasoft1978/lcovmerge/releases/download/v1.1.0; curl -fL "$base/$asset" -o "$asset"; curl -fL "$base/SHA256SUMS" -o SHA256SUMS
awk -v name="$asset" '$2 == name { count++; print } END { if (count != 1) exit 1 }' SHA256SUMS > "$asset.sha256" && sha256sum -c "$asset.sha256" && tar -xzf "$asset"
```

Merge and report:

```sh
# The shell expands the wildcard.
lcovmerge coverage/shard-*.info -o coverage/merged.info
genhtml coverage/merged.info --output-directory coverage/html
```

## Performance evidence

See the [Rust port comparison](docs/BENCHMARKS.md#rust-port-comparison) for measured speed and memory trade-offs.

<!-- README-BENCHMARK:START -->
Historical C implementation measurements; Rust results are reported separately.

**Generated Dataset XXL · 20.3 GB input · 128 shards · macOS 27.0 arm64 · Apple M5 Max · 2026-10-12. Builds: lcovmerge 1.1.0 (PGO) / lcov 2.6 / lcov-result-merger 6.0.0. Runs: lcovmerge 3-run median; lcov 1 timed run; lcov-result-merger timeout at 1h 15.8s.**

lcovmerge 1.1.0 (PGO): 1m 8.3s / 6.33 MiB peak resident memory (3 measured runs; median time); lcov 2.6: 13m 47.6s / 2.69 GiB peak resident memory (1 measured run).
lcov-result-merger 6.0.0: timed out after 1h 15.8s; 2.34 GiB peak sampled during run; full-run peak resident memory unavailable. No measured run completed.

Dataset M-trained PGO; portable release binaries.

Generated input measured on one host; this is not an estimate for every workload.
Public-project check: 8 pinned projects, 15 LCOV shards, 11.2 MB composite. lcovmerge 0.085 s / 15.94 MiB; lcov 2.6: 2.043 s / 140.27 MiB. One host; small input, not a production forecast. See [the full project results](docs/validation/real-projects.md).
Rounded figures; see the [full benchmark record](data/benchmarks.json) for methods, run statuses, and exact measurements.
<!-- README-BENCHMARK:END -->

See [all results and methods](docs/BENCHMARKS.md) or [measure private inputs](docs/REAL-WORKLOADS.md).

## Before switching

Not a drop-in replacement for `lcov -a`; compare output and reports before switching. See the [migration checklist](docs/MIGRATING-FROM-LCOV.md) and [limits](docs/LIMITATIONS.md).

## Build from source

Install stable Rust and Python 3.11+. `make` builds a portable PGO CLI and installs `llvm-tools` when needed; `make test` verifies it. See [release requirements](docs/RELEASING.md#rust-cross-build-requirements).

## Documentation

[Quick start](docs/USAGE.md) · [CI recipes](docs/RECIPES.md) · [decision guide](docs/DECISION.md) · [benchmark results](docs/BENCHMARKS.md) · [project direction](docs/ROADMAP.md)

MIT licensed.
