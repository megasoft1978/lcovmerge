# lcovmerge

lcovmerge combines exported LCOV `.info` files into one for your existing report or upload step.

## The benchmark

<!-- README-BENCHMARK:START -->
**Generated Dataset XXL · 20.3 GB input · 128 shards · macOS 27.0 arm64 · Apple M5 Max · 2026-10-11. Builds: lcovmerge 1.0.2 (PGO) / lcov 2.6 / lcov-result-merger 6.0.0. Runs: lcovmerge 3-run median; lcov 1 timed run; lcov-result-merger timeout at 1h 15.8s.**

| Tool | Time | Peak resident memory | Runs / status |
| --- | ---: | ---: | --- |
| lcovmerge 1.0.2 (PGO) | 1m 21s | 6.38 MiB | 3 measured runs; median time · OK |
| lcov 2.6 | 18m 0.6s | 2.85 GiB | 1 measured run · OK |
| lcov-result-merger 6.0.0 | TIMEOUT · stopped after 1h 15.8s | 2.34 GiB peak sampled during run; full-run peak resident memory unavailable | Not measured · TIMEOUT |

**13.3× faster than lcov 2.6; 458.4× less recorded peak resident memory than lcov 2.6.**

Dataset M-trained PGO; portable release binaries.

Input sizes and durations are rounded for readability; exact measurements are in the [published benchmark record](data/benchmarks.json).
<!-- README-BENCHMARK:END -->

## Try it (Linux x86-64)

```sh
set -eu
asset=lcovmerge-1.1.0-linux-x86_64.tar.gz; base=https://github.com/megasoft1978/lcovmerge/releases/download/v1.1.0; curl -fL "$base/$asset" -o "$asset"; curl -fL "$base/SHA256SUMS" -o SHA256SUMS
awk -v name="$asset" '$2 == name { count++; print } END { if (count != 1) exit 1 }' SHA256SUMS > "$asset.sha256" && sha256sum -c "$asset.sha256" && tar -xzf "$asset"
```

## Usage

```sh
# Merge exported files, then keep your report step.
lcovmerge coverage/shard-*.info -o coverage/merged.info
genhtml coverage/merged.info --output-directory coverage/html
```

## Not for you if

- Your report service accepts each shard.
- You need raw profiles (`.gcda`, `.profraw`); export LCOV first.
- `lcov -a` is already fast enough.

[Real-workload reports](docs/REAL-WORKLOADS.md) welcome.

## Limits

- `--mem-limit` limits memory reserved for coverage records while sorting; process memory may be higher, and sorting needs temporary disk.
- It does not collect coverage or create reports. It is not a drop-in for `lcov -a`; compare records and reports before switching.
- Windows is verified on GitHub-hosted `windows-latest`: both the MSYS2 UCRT64 GCC build and shipped Zig binary pass the full suite and smoke tests. CI records generated-input benchmarks, not LCOV comparisons. Other Windows versions, physical desktops and real-user reports remain unverified; large-dataset CI timings are disk-bound.

## GitHub Actions matrix

[Quick start](docs/USAGE.md) · [CI recipes](docs/RECIPES.md) · [Limits](docs/LIMITATIONS.md) · [Migration](docs/MIGRATING-FROM-LCOV.md) · [Benchmarks](docs/BENCHMARKS.md)
