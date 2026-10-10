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
- On Windows, long drive and UNC paths use the extended-length (`\\?\`) prefix and temporary run files are created with collision-checked `CREATE_NEW`. This path is exercised only by the Windows CI smoke test, not by local runs.

## Performance and packaging limits

- Performance figures depend on the recorded host, dataset, tool version, and measurement conditions. Consult the canonical [`data/benchmarks.json`](../data/benchmarks.json) and [benchmark report](BENCHMARKS.md) for the current results, run counts, and caveats. Failed or timed-out runs are not successful speed comparisons.
- Already-sorted regular files use the single-threaded stream path. `-j` applies to the external-sort fallback, so a timing difference on a workload that stays on the stream path does not show worker scaling. The benchmark data records each run's host and input type.
- Linux release binaries are statically linked against musl. macOS binaries dynamically link Apple's `libSystem.B.dylib`. Windows uses Windows system APIs; use the Windows CI smoke result as runtime evidence in addition to cross-build results.
- The generated benchmark set's `REAL` case is not measured when no project-derived capture is available for that run. Separate small real-project compatibility results are documented in [the validation report](validation/real-projects.md). Measurement tools and RSS availability also differ by host; check the recorded method before comparing results.

Benchmark and dependency evidence: [benchmark results](validation/benchmark.txt), [macOS dependencies](validation/macos-dependencies.txt), and [cross-build checks](validation/cross-build.txt).

## Verification status

Platform-specific and performance claims are limited to the evidence recorded under validation. A Windows PE cross-build alone does not establish runtime behavior; consult the Windows CI smoke result for runtime evidence.
