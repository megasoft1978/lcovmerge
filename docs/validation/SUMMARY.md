# Validation summary

Validated source commit: `e026eaccf6a9` (`lcovmerge 1.0.0`). Native validation ran on Apple Silicon macOS 27 with Apple clang 21. LCOV comparison used LCOV 2.6; full tool versions are in [tool-versions.txt](tool-versions.txt).

## Acceptance criteria

| # | Result | Measured outcome | Evidence |
|---|---|---|---|
| 1 | PASS | Native macOS suite and Docker `linux/amd64` and `linux/arm64` suites passed. Docker ran both the Debian GCC build and the matching static target binary. | [native macOS](native-macos.txt), [Docker summary](docker-run.txt), [amd64](docker-linux-amd64.txt), [arm64](docker-linux-arm64.txt) |
| 2 | PASS | 56 hand-written golden cases cover the supported records, options, and malformed boundaries. | [native macOS](native-macos.txt), [test runner](../../tests/run_tests.py) |
| 3 | PASS | 220 seeded random well-formed cases plus 26 malformed oracle cases (246 total); a separate malformed suite covers 14 cases. | [native macOS](native-macos.txt), [test runner](../../tests/run_tests.py) |
| 4 | PASS | 200 generated LCOV cases compared summary counts and merged MC/DC rows with `lcov -a`; LCOV 2.6. | [native macOS](native-macos.txt), [tool versions](tool-versions.txt) |
| 5 | PASS | 2,000,000 ASan/UBSan mutation executions across four seeds, zero findings. | [fuzz run](fuzz-2m.txt), [sanitizer suite](asan.txt) |
| 6 | PASS | Four outputs were byte-identical for `-j1`, `-j2`, `-j8`, and reversed input order. | [native macOS](native-macos.txt), [POSIX runner](posix-runner.txt), [PowerShell runner](powershell-runner.txt) |
| 7 | FAIL | M default was 186.8 MB/s against 250 MB/s; default RSS was 22,855,680 B, under 32 MiB. `-j8` reached 196.2 MB/s but used 39,206,912 B RSS. | [benchmark results](benchmark.txt), [benchmark log](benchmark-run.txt) |
| 8 | PASS | All five target builds and their `.tar.gz`/`.zip` archives passed reproducibility and SHA-256 checks; every stripped executable is below 150 KB. Linux outputs are static musl; Windows passed PE checks. macOS links the OS `libSystem`. | [cross-build](cross-build.txt), [binary sizes](binary-sizes.txt), [macOS dependencies](macos-dependencies.txt) |
| 9 | PASS | `--version` printed `lcovmerge 1.0.0 (git e026eaccf6a9)`. | [native macOS](native-macos.txt) |
| 10 | PASS | Architecture, LCOV semantic differences, known limitations, and this evidence index are documented. | [architecture](../ARCHITECTURE.md), [limitations](../LIMITATIONS.md), this file |

The main core translation unit is 2,197 lines. All production C files and headers total 2,511 lines; platform-layer files account for the difference. See [source line counts](source-lines.txt).

The no-third-party-runtime-dependency goal is met by the static Linux binaries. macOS binaries still link the OS-provided `libSystem.B.dylib`, so they are not fully static. The Windows binary passed build and PE checks, but Wine was unavailable and its runtime was not exercised; the PowerShell runner was tested against the native macOS executable. See [Wine availability](wine.txt).

## Benchmark measurements

Single-run timings on the Apple Silicon host. Throughput is input bytes divided by wall time. Fixtures were regenerated outside the repository from the benchmark repository's generators and removed after the run.

| Workload | Input bytes | Wall time | Throughput | Peak RSS |
|---|---:|---:|---:|---:|
| S | 99,996,906 | 0.361 s | 276.8 MB/s | 22,724,608 B |
| M, default jobs | 1,118,686,233 | 5.987 s | 186.8 MB/s | 22,855,680 B |
| M, `-j1` | 1,118,686,233 | 7.188 s | 155.6 MB/s | 20,529,152 B |
| M, `-j2` | 1,118,686,233 | 6.402 s | 174.7 MB/s | 22,413,312 B |
| M, `-j4` | 1,118,686,233 | 5.918 s | 189.0 MB/s | 23,379,968 B |
| M, `-j8` | 1,118,686,233 | 5.703 s | 196.2 MB/s | 39,206,912 B |
| M, `lcov -a` | 1,118,686,233 | 46.134 s | 24.2 MB/s | 643,039,232 B |
| M, `lcov-result-merger` | 1,118,686,233 | 144.602 s | 7.7 MB/s | 2,402,893,824 B |
| XL-single | 744,992,058 | 3.044 s | 244.7 MB/s | 22,347,776 B |
| PATH-HEAVY | 130,000,000 | 1.997 s | 65.1 MB/s | 19,808,256 B |

`-j4` was 21.5% faster than `-j1` on this run; `-j8` was 26.1% faster and exceeded the 32 MiB RSS target. These are single-run measurements, not a distribution. The implementation did not reach the 250 MB/s M target.

## Platform build matrix

| Target | Result | Stripped binary size | Notes |
|---|---|---:|---|
| linux-x86_64 | PASS | 89,256 B | Static musl; runtime suite passed in Docker. |
| linux-aarch64 | PASS | 85,608 B | Static musl; runtime suite passed in Docker. |
| macos-arm64 | PASS | 87,608 B | Links the OS `libSystem.B.dylib`. |
| macos-x86_64 | PASS | 57,517 B | Links the OS `libSystem.B.dylib`. |
| windows-x86_64 | BUILD PASS | 137,728 B | PE32+ x86_64; Wine unavailable, so Windows runtime remains unverified. |

See [cross-build output](cross-build.txt) and [binary sizes](binary-sizes.txt). The [POSIX](posix-runner.txt) and [PowerShell](powershell-runner.txt) wrappers both passed on macOS using the native binary. The PowerShell run does not verify the Windows executable itself.
