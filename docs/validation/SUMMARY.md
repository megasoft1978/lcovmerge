# Validation summary

Validation was run from this release-candidate tree on 2026-10-09. The host was an Apple M5 Max with macOS 27, arm64, 18 CPU cores, and 64 GiB RAM. Tool versions are in [tool-versions.txt](tool-versions.txt). Benchmark inputs were generated outside the repository and removed after the runs.

## Acceptance criteria

| Area | Result | Evidence |
| --- | --- | --- |
| Performance target | PASS | M is 1,118,686,233 bytes (1.12 GB decimal, 32 shards). Default single-threaded streaming measured 337.4 MB/s and 5,373,952 B peak RSS, meeting the 300 MB/s and 32 MiB limits. The v1.0 baseline was 186.8 MB/s and 22,855,680 B. See [M benchmark](benchmark-final-M.txt) and [optimization log](optimization-log.md). |
| Workload benchmark matrix | PARTIAL | S, M, L, XL-single, and PATH-HEAVY are recorded against lcov 2.6 and npm lcov-result-merger 6.0.0. The REAL workload is NOT MEASURED because no project capture set was supplied. PATH-HEAVY npm timed out at 1,800 s; lcov errored. See the [benchmark results](benchmark.txt) and [REAL status](benchmark-REAL.txt). |
| Semantics and determinism | PASS | `make clean && make test`: 56 golden, 14 malformed, 246 oracle, 4 determinism, 5 I/O, 1 many-path, and 200 lcov-differential cases passed. `make asan` passed the native cases. The recorded 2,000,000-execution, four-seed ASan/UBSan fuzz run completed without findings. See [native test](native-macos.txt), [ASan](asan.txt), and [fuzz](fuzz-2m.txt). |
| CLI and documentation agreement | PASS | `tests/doc-consistency.sh` compares the binary help options with the man page and `docs/USAGE.md`; all 20 options match. `mandoc -Tlint` passed. The canonical man page is [lcovmerge.1](../../man/lcovmerge.1). |
| Platform claims | PASS WITH LIMIT | Linux builds are static musl; macOS links Apple `libSystem`; Windows is statically linked to its runtime and uses Windows system APIs. The Windows executable passed the suite under Wine 8.0; native Windows runtime remains pending a passing Windows CI run. See [dependency check](macos-dependencies.txt), [Wine run](wine.txt), and [cross-build](cross-build.txt). |
| Release packaging and build contract | PASS LOCALLY | Five target binaries and reproducible archives, SHA256SUMS, CycloneDX SBOM, changelog extraction, size budget, reproducible build, local installer success/corrupt-checksum rejection, and local Action entrypoint passed. `actionlint`, `shellcheck`, workflow YAML parsing, Python compilation, and all pinned-action SHA checks passed. See [cross-build](cross-build.txt), [install](install.txt), [Action pins](action-pins.txt), and the package validation files. |
| Container and CI contract | PASS LOCALLY | Docker images built and ran sample data for Linux amd64 and arm64. CI targets exist and run the expected test, sanitizer, and fuzz commands. GitHub-hosted jobs and native Windows CI were not run from this local checkout. See [Docker results](docker-run.txt). |
| Website | PASS | Lighthouse reported 100/100 for performance, accessibility, best practices, and SEO on index, docs, and benchmarks pages. `/lcovmerge/` subpath assets/data work, the nested not-found route returns 404, and Pages uploads `docs/site`. See [site QA](site-qa.txt). |
| Source hygiene | PASS | The final checks include Markdown lint, local link validation, man lint, shell/YAML checks, `git diff --check`, and scans for personal paths, email addresses, tokens, and AI attribution. External links to this unpublished repository cannot resolve until the owner publishes it. |

Superseded 2026-10-09: the 337.4 MB/s M result above is superseded by the refreshed 320.7 MB/s paired result recorded in [`data/benchmarks.json`](../../data/benchmarks.json) and [benchmark-final-M.txt](benchmark-final-M.txt). The historical table is retained as recorded.

## Benchmark matrix

Input sizes are exact byte counts; decimal MB/s is bytes divided by 1,000,000 and elapsed wall time. RSS is peak resident memory from `/usr/bin/time`. The PATH-HEAVY npm process timed out, so it has no completed-run peak RSS; a one-second sample reached 841,888 KiB before it was stopped. The XL-single npm tool exited with status 1. lcov exited with status 1 on PATH-HEAVY. These errors are retained as measurements rather than converted into throughput.

| Workload | Bytes (shards) | lcovmerge time / MB/s / RSS | lcov 2.6 time / MB/s / RSS | npm merger time / MB/s / RSS |
| --- | ---: | ---: | ---: | ---: |
| S | 99,996,906 (8) | 0.237 s / 422.0 / 3,080,192 B | 4.365 s / 22.9 / 240,943,104 B | 17.521 s / 5.7 / 458,964,992 B |
| M | 1,118,686,233 (32) | 3.316 s / 337.4 / 5,373,952 B | 43.352 s / 25.8 / 639,844,352 B | 145.171 s / 7.7 / 2,497,675,264 B |
| L | 4,853,340,843 (64) | 27.657 s / 175.5 / 26,918,912 B | 195.144 s / 24.9 / 1,499,709,440 B | 604.280 s / 8.0 / 3,257,008,128 B |
| XL-single | 744,992,058 (1) | 1.370 s / 543.7 / 2,375,680 B | 35.378 s / 21.1 / 3,751,149,568 B | 0.170 s / error 1 / 637,190,144 B |
| PATH-HEAVY | 130,000,000 (1) | 1.912 s / 68.0 / 20,643,840 B | 89.383 s / error 1 / 7,037,177,600 B | 1,800.002 s / timeout / RSS unavailable |
| REAL | — | NOT MEASURED | NOT MEASURED | NOT MEASURED |

M job-count runs were 3.388 s at `-j1` (330.2 MB/s, 5,390,336 B), 3.314 s at `-j2` (337.5 MB/s, 5,373,952 B), 3.302 s at `-j4` (338.8 MB/s, 5,357,568 B), and 3.356 s at `-j8` (333.3 MB/s, 5,373,952 B). The sorted streaming fast path does not use worker parallelism; these close results are run-to-run variation, not parallel scaling. Full raw results are in [S](benchmark-final-S.txt), [M](benchmark-final-M.txt), [L](benchmark-final-L.txt), [XL-single](benchmark-final-XL-single.txt), and [PATH-HEAVY](benchmark-final-PATH-HEAVY.txt).

## Release and verification limits

Benchmark system and tool metadata are included in [`data/benchmarks.json`](../../data/benchmarks.json). Measurements are single runs on this host, with cache state uncontrolled; they are not a cross-machine performance guarantee. The REAL row remains unavailable until representative project captures are provided. Native Windows execution and hosted GitHub Actions remain unverified locally. Windows runtime documentation therefore remains conditional on passing CI. No push, GitHub release, package-update PR, or other GitHub resource was created during validation.
