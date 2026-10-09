# Validation summary

Validated source commit: `5298d7f59aad` (`lcovmerge 1.0.0`). Native validation ran on Apple Silicon macOS 27 with Apple clang 21; lcov differential used LCOV 2.6. See [tool versions](tool-versions.txt).

## Acceptance criteria

| # | Result | Measured outcome | Evidence |
|---|---|---|---|
| 1 | PASS | Native macOS `make clean all test`; Docker `linux/amd64` and `linux/arm64` each passed native and static-binary suites. | [native macOS](native-macos.txt), [Docker summary](docker-run.txt), [amd64](docker-linux-amd64.txt), [arm64](docker-linux-arm64.txt) |
| 2 | PASS | 56 hand-written golden cases. | [native macOS](native-macos.txt) |
| 3 | PASS | 220 seeded random well-formed cases plus 26 malformed oracle cases (246 total), including 20 seeded malformed inputs; the separate malformed suite covers 14 cases. | [native macOS](native-macos.txt), [test runner](../../tests/run_tests.py) |
| 4 | PASS | 200 generated LCOV cases compared summary counts and MC/DC merged rows against `lcov -a`; LCOV 2.6. | [native macOS](native-macos.txt), [tool versions](tool-versions.txt) |
| 5 | PASS | 2,000,000 ASan/UBSan mutation executions, four seed files, zero findings. | [fuzz run](fuzz-2m.txt), [sanitizer suite](asan.txt) |
| 6 | PASS | Four output comparisons: `-j1`, `-j2`, `-j8`, and reversed input order were byte-identical. | [native macOS](native-macos.txt), [PowerShell runner](powershell-runner.txt) |
| 7 | FAIL | Default M RSS met the 32 MiB cap at 25,395,200 bytes, but M throughput was 167.3 MB/s (target: 250 MB/s). `-j8` used 38,993,920 bytes RSS. | [benchmark](benchmark.txt), [benchmark log](benchmark-run.txt) |
| 8 | PASS | All five Zig targets and archives built; repeated package checksums matched; Linux images are static musl and the Windows PE check passed. Every executable is below 150 KB. macOS uses system libSystem. | [cross-build](cross-build.txt), [binary sizes](binary-sizes.txt), [macOS dependencies](macos-dependencies.txt) |
| 9 | PASS | `--version` printed `lcovmerge 1.0.0 (git 554997886973)`. | [native macOS](native-macos.txt) |
| 10 | PASS | Architecture, semantics/limitations, and this evidence index are documented. | [architecture](../ARCHITECTURE.md), [limitations](../LIMITATIONS.md), this file |

## Benchmark measurements

Single-run timings on the Apple Silicon host. Throughput is total input bytes divided by wall time. The benchmark generated the fixtures in a temporary directory outside the repository and removed them after the run.

| Workload | Input bytes | Wall time | Throughput | Peak RSS |
|---|---:|---:|---:|---:|
| S | 99,996,906 | 0.450 s | 222.1 MB/s | 23,216,128 B |
| M, default jobs | 1,118,686,233 | 6.687 s | 167.3 MB/s | 25,395,200 B |
| M, `-j1` | 1,118,686,233 | 6.533 s | 171.2 MB/s | 21,512,192 B |
| M, `-j2` | 1,118,686,233 | 6.773 s | 165.2 MB/s | 23,560,192 B |
| M, `-j4` | 1,118,686,233 | 6.868 s | 162.9 MB/s | 24,035,328 B |
| M, `-j8` | 1,118,686,233 | 6.364 s | 175.8 MB/s | 38,993,920 B |
| M, `lcov -a` | 1,118,686,233 | 43.037 s | 26.0 MB/s | 642,662,400 B |
| M, `lcov-result-merger` | 1,118,686,233 | 139.030 s | 8.0 MB/s | 2,399,617,024 B |
| XL-single | 744,992,058 | 3.184 s | 234.0 MB/s | 23,953,408 B |
| PATH-HEAVY | 130,000,000 | 1.964 s | 66.2 MB/s | 20,545,536 B |

The measured M scaling is modest and inconsistent across job counts: `-j8` was 2.7% faster than `-j1`, while `-j2` and `-j4` were slower. The flag remains correct and deterministic; see [limitations](../LIMITATIONS.md) for the observed trade-off.

## Platform build matrix

| Target | Result | Stripped binary size | Notes |
|---|---|---:|---|
| linux-x86_64 | PASS | 87,704 B | Static musl; runtime suite passed in Docker. |
| linux-aarch64 | PASS | 84,168 B | Static musl; runtime suite passed in Docker. |
| macos-arm64 | PASS | 87,544 B | Links the OS `libSystem.B.dylib`. |
| macos-x86_64 | PASS | 57,469 B | Links the OS `libSystem.B.dylib`. |
| windows-x86_64 | BUILD PASS | 136,704 B | PE32+ x86_64; Wine unavailable, so Windows runtime remains unverified. |

See [cross-build output](cross-build.txt) and [sizes](binary-sizes.txt). `make test` passed with strict warnings on Apple clang and Debian GCC; Zig cross-builds also use `-Werror`. The [POSIX](posix-runner.txt) and [PowerShell](powershell-runner.txt) wrappers both passed. PowerShell ran on macOS using the native binary, so it does not verify the Windows executable itself.

Production C sources total 2,374 lines, excluding tests; see [source line counts](source-lines.txt).
