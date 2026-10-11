# lcovmerge

lcovmerge combines exported LCOV `.info` files into one for your existing report or upload step.

## The benchmark

<!-- README-BENCHMARK:START -->
**Generated Dataset L · 4,853,340,843 input bytes · 64 shards · macOS 27.0 arm64 · Apple silicon (exact model unavailable in this sandbox) · 2026-10-09. Builds: lcovmerge 1.0.0 / lcov 2.6 / lcov-result-merger 6.0.0. Runs: lcovmerge 2-run median; lcov 1 timed run; peak memory from earlier measurement; lcov-result-merger earlier measurement; run count unreported.**

| Tool | Time | Peak resident memory | Runs / status |
| --- | ---: | ---: | --- |
| lcovmerge 1.0.0 | 27.723 s | 26,279,936 B | 2 measured runs; median time · OK |
| lcov 2.6 | 203.235 s | 1,499,709,440 B · earlier measurement | 1 measured run; peak memory from an earlier measurement · OK |
| lcov-result-merger 6.0.0 | 604.280 s | 3,257,008,128 B · earlier measurement | Earlier recorded measurement; run count not reported · OK |

**7.3× faster than lcov 2.6; 57.1× less recorded peak resident memory than lcov 2.6 (its peak memory is from an earlier measurement).**

**21.8× faster than lcov-result-merger 6.0.0; 123.9× less recorded peak resident memory than lcov-result-merger 6.0.0 (its values are from an earlier recorded measurement; run count not reported).**
<!-- README-BENCHMARK:END -->

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
