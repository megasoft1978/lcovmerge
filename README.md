# lcovmerge

lcovmerge combines exported LCOV `.info` files into one for your existing report or upload step.

## The benchmark

<!-- README-BENCHMARK:START -->
**Generated Dataset XXL · 20.3 GB input · 128 shards · macOS 27.0 arm64 · Apple M5 Max · 2026-10-11. Builds: lcovmerge 1.0.2 / lcov 2.6 / lcov-result-merger 6.0.0. Runs: lcovmerge 3-run median; lcov 1 timed run; lcov-result-merger 1 measured run.**

| Tool | Time | Peak resident memory | Runs / status |
| --- | ---: | ---: | --- |
| lcovmerge 1.0.2 | 2m 34.7s | 31.05 MiB | 3 measured runs; median time · OK |
| lcov 2.6 | 14m 53.5s | 2.66 GiB | 1 measured run · OK |
| lcov-result-merger 6.0.0 | Not measured | — | Not measured · Not measured |

**5.8× faster than lcov 2.6; 87.6× less recorded peak resident memory than lcov 2.6.**

Input sizes and durations are rounded for readability; exact measurements are in the [published benchmark record](data/benchmarks.json).
<!-- README-BENCHMARK:END -->

<!-- README-OPTIMIZATION:START -->
### Experimental profile-guided build

A separate Apple M5 Max experiment trained an Apple Clang profile on Dataset M and reused it on S and M. Against the standard portable `-O2` build, the tuned build was 14.1% faster on S and 10.7% faster on M, with peak resident memory increasing by 16 KiB on each dataset. The M-trained profile also beat the same native `-O2` build by 10.5% on M. Outputs matched byte for byte.

| Dataset | Standard `-O2` | Profile-guided build | Time change | Peak memory change |
| --- | ---: | ---: | ---: | ---: |
| S (100 MB) | 248 ms / 3.00 MiB | 213 ms / 3.02 MiB | 14.1% faster | +16 KiB |
| M (1.12 GB) | 3.66 s / 5.28 MiB | 3.26 s / 5.30 MiB | 10.7% faster | +16 KiB |

This is a compiler-tuning experiment on one host, separate from the standard tool comparison above; it is not a release-build or cross-platform claim. The [benchmark report](docs/BENCHMARKS.md#experimental-profile-guided-build) records compiler flags, run counts, and hashes.
<!-- README-OPTIMIZATION:END -->

## Try it (Linux x86-64)

```sh
set -eu
asset=lcovmerge-1.0.2-linux-x86_64.tar.gz; base=https://github.com/megasoft1978/lcovmerge/releases/download/v1.0.2; curl -fL "$base/$asset" -o "$asset"; curl -fL "$base/SHA256SUMS" -o SHA256SUMS
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

## Limits

- `--mem-limit` limits memory reserved for coverage records while sorting; process memory may be higher, and sorting needs temporary disk.
- It does not collect coverage or create reports. It is not a drop-in for `lcov -a`; compare records and reports before switching.
- Windows is verified on GitHub-hosted `windows-latest`: both the MSYS2 UCRT64 GCC build and shipped Zig binary pass the full suite and smoke tests. CI records generated-input benchmarks, not LCOV comparisons. Other Windows versions, physical desktops and real-user reports remain unverified; large-dataset CI timings are disk-bound.

## GitHub Actions matrix

[Quick start](docs/USAGE.md) · [CI recipes](docs/RECIPES.md) · [Limits](docs/LIMITATIONS.md) · [Migration](docs/MIGRATING-FROM-LCOV.md) · [Benchmarks](docs/BENCHMARKS.md)
