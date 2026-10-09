# Limitations and intentional differences

## LCOV semantic differences

- Output is canonicalized: SF paths, TN rows, FNL/FNA groups, legacy functions, FNDA rows, then DA, BRDA, MC/DC, and extension rows. Each numeric record family is sorted by its LCOV key (including line number). LCOV tools may preserve input order or choose a different presentation order. Input summary rows are discarded and recomputed; output includes MCF/MCH rows even when no MC/DC rows exist.
- Counts use unsigned 64-bit values and saturate at UINT64_MAX. LCOV versions may use different numeric bounds internally.
- For conflicting non-empty DA checksums, lcovmerge warns and emits the lexicographically smallest checksum. --strict-checksum changes the warning into input error status 2. The policy makes output independent of input ordering.
- Orphan FNDA rows are preserved and their nonzero values contribute to FNH. LCOV 2.6 may reject or drop orphan hits during summary calculation; this merger keeps the supplied row.
- FNL is counted as one function group. Every FNA alias is retained and its FNDA count is merged by index and name. LCOV 2.6's summary command may count aliases as separate functions in some tracefiles even though FNF describes function locations.
- MC/DC records are keyed by line, group size, sense, expression index, and reachability flag. Their taken counts are saturating-summed, matching LCOV's count merge. If the same key has different expression text, lcovmerge warns and keeps the lexicographically smallest expression for deterministic output; lcov 2.6 reports inconsistent expression text. A reachable and U-unreachable row remain separate keys; LCOV may report an unreachable-flag mismatch instead.
- For branch rows, numeric count rows sum; - means no count and yields to a numeric count if one is present. Reachability/exception/fall-through markers remain part of the key. Unreachable branches are excluded from BRF/BRH.
- Unknown colon-delimited rows inside SF sections are not interpreted. They are retained with duplicates and sorted lexicographically; --warn-unknown reports each one. Their application-specific merge meaning is unknown.

The record field definitions and MC/DC behavior were checked against the LCOV 2.6 geninfo manual and merge implementation: [geninfo tracefile format](https://github.com/linux-test-project/lcov/blob/v2.6/docs/man/geninfo.rst) and [lcovutil.pm](https://github.com/linux-test-project/lcov/blob/v2.6/lib/lcovutil.pm).

## Input and operational bounds

- Maximum input line: 1 MiB. Maximum @listfile nesting: 8. Maximum workers: 32, with an 8 MiB minimum arena budget per worker.
- --mem-limit bounds worker record arenas, not total process RSS. Up to 24 MiB of the setting is reserved for parser lines, I/O buffers, merge bookkeeping, thread stacks, allocator metadata, and runtime/library state, while preserving at least 8 MiB per worker.
- External sorting consumes temporary disk space proportional to input plus intermediate runs. A named run file is necessary because the merge performs bounded-fan-in passes and reopens runs. --tmpdir selects their directory. Files are created privately on POSIX and removed on success, handled errors, and caught POSIX interruptions (SIGINT, SIGTERM, and SIGHUP). SIGKILL or power loss can leave named run files behind.
- On POSIX, lcovmerge ignores SIGPIPE so a write to a closed pipe reports EPIPE and follows the normal I/O-failure cleanup path. This returns status 3.
- Output sent to stdout with `-o -` cannot be rolled back. A later caught signal or write failure can leave partial output in the stream, even though temporary files are cleaned up.
- The Windows implementation uses synchronous ReadFile/WriteFile calls and blocking worker-thread joins, with no cancellation path for them. A blocked operation may delay interruption and cleanup; the interruption and cleanup guarantee above applies to POSIX only.
- Parallel parsing is across input files. -j gives no parser scaling when the workload contains only one input file. The merge stage itself is single-threaded.
- The CLI does not expand glob patterns itself. The caller's shell should expand them; use @listfile for explicit lists. Listfile paths are interpreted relative to the process working directory.
- A directory path containing unsupported native Windows path length forms may fail in the Win32 temp helper, which uses GetTempFileNameW and its MAX_PATH buffer.

## Performance and packaging limits

- Dataset M measured 320.7 MB/s and 5,373,952 B peak RSS at default settings. The paired baseline measured 311.4 MB/s and 5,390,336 B; the final build was 1.03x faster on the median. LCOV 2.6 took 50.542 s for M, 14.49x the final merger time. Across the generated datasets, the final measured RSS stayed below 32 MiB; L was the highest at 26,279,936 B (25.06 MiB). The paired results and intermediate experiments are recorded in [the optimization log](validation/optimization-log.md) and [the benchmark report](validation/benchmark.txt).
- The M `-j1`, `-j2`, `-j4`, and `-j8` runs measured 268.9, 302.1, 302.7, and 300.2 MB/s. Sorted regular files use the single-threaded stream path, so these results show run variation rather than worker scaling. Determinism was checked across job settings and input order.
- Linux release binaries are statically linked against musl. macOS binaries dynamically link Apple's `libSystem.B.dylib`. Windows is built with static linking, but still uses Windows system APIs; executable runtime behavior remains unverified until a Windows CI run passes.
- Benchmark measurements cover one macOS arm64 host and generated inputs. `REAL` was not measured because no project-derived capture was available. LCOV 2.6 exited with status 1 on PATH-HEAVY after 98.805 s, so no speedup is reported for that failed run. Apple `/usr/bin/time -l` could not query RSS under the host policy; lcovmerge peak RSS came from hyperfine, while successful LCOV comparator RSS values remain from the prior canonical measurements.

Benchmark and dependency evidence: [benchmark results](validation/benchmark.txt), [macOS dependencies](validation/macos-dependencies.txt), and [cross-build checks](validation/cross-build.txt).

## Verification status

Platform-specific and performance claims are limited to the evidence recorded under validation. A successful Windows PE build does not establish Windows runtime behavior; a passing Windows CI run is still required.
