# Limitations and intentional differences

lcovmerge merges already-exported LCOV `.info` files. It does not collect raw coverage or generate reports. It is not a drop-in replacement for `lcov -a`; compare its behavior against your inputs and consumers before switching. See the [migration guide](MIGRATING-FROM-LCOV.md) for flag mapping and a data-check procedure.

## LCOV record differences

- Output records are written in a stable order: source paths, testcase names, function groups and aliases, line records, branch records, MC/DC coverage records, then extension rows. Numeric record families are sorted by their LCOV keys. Input summary rows are discarded and recomputed. Output includes MCF/MCH summary rows even when there are no MC/DC records.
- Counts use unsigned 64-bit values and saturate at `UINT64_MAX`. LCOV versions may use different numeric bounds internally.
- For conflicting non-empty line checksums, lcovmerge warns and emits the lexicographically smallest checksum. `--strict-checksum` turns the warning into input error status 2. This policy makes output independent of input order.
- Orphan FNDA rows are preserved, and their nonzero values contribute to FNH. LCOV 2.6 may reject or drop orphan hits during summary calculation; this merger keeps the supplied row.
- FNL is counted as one function group. Every FNA alias is retained, and its FNDA count is merged by index and name. LCOV 2.6's summary command may count aliases as separate functions in some files even though FNF describes function locations.
- MC/DC records are keyed by line, group size, sense, expression index, and reachability flag. Their taken counts are added with saturation. If the same key has different expression text, lcovmerge warns and keeps the lexicographically smallest expression; lcov 2.6 reports inconsistent expression text. A reachable row and a `U`-unreachable row remain separate keys; LCOV may instead report a reachability mismatch.
- For branch rows, numeric counts are added; `-` means no count and yields to a numeric count when present. Reachability, exception, and fall-through markers remain part of the key. Unreachable branches are excluded from BRF/BRH.
- Branch rows are keyed by their literal line, block, and branch identifiers. LCOV 2.6 re-derives contiguous block IDs while merging, using branch-type signatures and order to match blocks. That can combine rows whose input block IDs differ; lcovmerge keeps those IDs distinct. Mapping blocks this way is ambiguous when inputs came from different source or instrumentation versions.
- A branch-only line with no matching DA row remains branch-only in lcovmerge; it does not invent line coverage. LCOV 2.6 treats this as inconsistent input, but `--ignore-errors inconsistent` repairs it by adding DA equal to the number of nonzero branch coverpoints on that line. Such diagnostic output has line coverage that was absent from the input.
- Empty source sections are retained, with zeroed summaries, when an input contains an SF row but no coverage records. LCOV 2.6 drops empty sections during addition, so its SF set can be smaller without losing line, function, or branch rows.
- For legacy FN rows, lcovmerge preserves the start-line/name keys and adds FNDA by function name. LCOV 2.6 reports duplicate function names at different start lines as inconsistent and collapses them to one function entry at the smallest start line. When captures refer to different source versions, the trace data alone cannot establish whether those rows describe one moved function or distinct functions.
- If `end_of_record` is immediately joined to an `SF:` or `KF:` row with no newline, lcovmerge splits the boundary, continues under the new source, and warns. This narrowly recognized repair prevents following rows from being assigned to the previous source.
- Unknown colon-delimited rows inside source sections are preserved with duplicates and sorted lexicographically; `--warn-unknown` reports each one. They are not interpreted as coverage records, so their application-specific merge meaning is unknown.

The record definitions and LCOV 2.6 merge behavior were checked against the [geninfo tracefile format](https://github.com/linux-test-project/lcov/blob/v2.6/docs/man/geninfo.rst) and [merge implementation](https://github.com/linux-test-project/lcov/blob/v2.6/lib/lcovutil.pm).

## Input and operational bounds

- An input line can be at most 1 MiB. `@listfile` nesting is limited to 8. The program supports at most 32 workers, with at least 8 MiB of coverage-record storage per worker.
- `--mem-limit` limits memory reserved for coverage records while sorting. It is not a hard cap on total process memory. Parser and I/O buffers, merge bookkeeping, thread stacks, allocator metadata, and runtime/library state use additional memory. Temporary sorted files and intermediate merge passes also need disk space; this setting does not cap that use.
- `--tmpdir` selects the directory for temporary sorted files; the directory must already exist and be writable. Disk use can grow with the input and intermediate merge passes. Files are removed after success and handled errors, and on POSIX after caught SIGINT, SIGTERM, and SIGHUP. A forced kill or machine failure can leave named files behind. Windows does not promise the same interruption cleanup.
- On POSIX, lcovmerge ignores SIGPIPE so a write to a closed pipe reports EPIPE and follows the usual I/O-failure cleanup path. This returns status 3.
- Output sent to stdout with `-o -` cannot be rolled back. A later caught signal or write failure can leave partial output in the stream, even though temporary files are cleaned up.
- Windows uses synchronous `ReadFile`/`WriteFile` calls and blocking worker-thread joins without a cancellation path. A blocked operation can delay interruption and cleanup; the signal-cleanup guarantee is POSIX only.
- Parsing can use multiple workers across input files. One large input file is not split among workers, and the merge stage itself uses one worker.
- The program does not expand glob patterns. The caller's shell expands them; use `@listfile` for explicit lists. List-file paths are relative to the process working directory.
- Windows long drive and UNC paths use the extended-length `\\?\` prefix, and temporary files use collision-checked `CREATE_NEW`. These paths were exercised by the Windows CI smoke test, not by local runs.

## Performance and packaging limits

- Performance depends on the recorded host, dataset, measured build, and method. Consult [`data/benchmarks.json`](../data/benchmarks.json) and the [benchmark report](BENCHMARKS.md) for run counts, statuses, and caveats. Failed or timed-out runs are not successful speed comparisons.
- Already-sorted regular files use a one-worker streaming path. `-j` applies when lcovmerge writes temporary sorted files; a timing difference on the streaming path does not show worker scaling. The benchmark record identifies host and input type.
- Linux release binaries are statically linked against musl. macOS binaries dynamically link Apple's `libSystem.B.dylib`. Windows uses Windows system APIs. Windows runtime verification is pending a passing Windows CI run; a Windows cross-build or release archive alone is not runtime validation.
- The generated benchmark set's `REAL` case is not measured when no project-derived capture is available for that run. Separate small project-derived compatibility checks are documented in the [validation report](validation/real-projects.md). Measurement tools and resident-memory availability differ by host; check the recorded method before comparing results.

Benchmark and dependency evidence: [benchmark results](validation/benchmark.txt), [macOS dependencies](validation/macos-dependencies.txt), and [cross-build checks](validation/cross-build.txt).

## Verification status

Platform and performance claims are limited to evidence recorded under `docs/validation/`. A Windows PE cross-build does not establish runtime behavior. See the [release and package notes](RECIPES.md#release-archives-and-provenance) for the local audit status of Windows, Homebrew, and Scoop installation paths.
