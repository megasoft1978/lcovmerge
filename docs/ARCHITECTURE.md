# Architecture

## Data flow

1. **Streaming parse.** Inputs are regular-file checked before selecting the direct path. Each stream uses a 64 KiB I/O block and a reusable line buffer. Lines are capped at 1 MiB and checked for NUL and valid UTF-8. Parsing validates recognized rows and rewrites/filters SF paths before records enter the merge.
2. **Sorted-input merge.** For up to 32 regular files whose rewritten record streams are already in canonical order, the parser feeds a tournament-tree merge directly. DA and BRDA rows use dedicated fast parsers; other records use the common parser. Queue rows refer to the current input buffer until consumed. The previous sort key is retained separately, and the attempt stages output in the destination directory. If a stream is out of order or the bounded direct-path budget is exceeded, staged output and buffered diagnostics are discarded before the external-sort path starts.
3. **Bounded external sort.** The fallback parses files into a compact array plus string arena. At the worker's arena threshold, qsort orders a chunk only if its rows arrived out of canonical order. Sorted rows serialize to temporary runs. The sizing rule reserves up to 24 MiB of the configured --mem-limit value from worker chunks for anticipated non-arena overhead; the minimum 8 MiB arena per active worker takes precedence for smaller settings.
4. **K-way merge and emit.** Tournament/heap merges combine sorted runs with bounded fan-in, writing intermediate runs as needed. Per-key counts are combined as rows stream through the merge. Function, branch, MC/DC, and line summaries are recomputed. File output is staged in the destination directory and renamed only after all reads, writes, closes, and summary emission succeed. Output to stdout (`-o -`) is streamed and cannot be rolled back if a later signal or write failure occurs.

## Memory model

The default `--mem-limit` value of 64 MiB is a record-arena budget for external-sort workers, not a total RSS
cap. The sizing rule can hold back up to 24 MiB from worker chunks for anticipated parser/writer buffers, merge
bookkeeping, thread stacks, and runtime state; the 8 MiB minimum per active worker takes precedence for small
settings. Actual process RSS also includes these buffers, merge readers, the C runtime, and allocator state.
Temporary disk use is separate and scales with parsed input and intermediate merge passes.

Rows are limited by the 1 MiB input-line cap. Inputs with many unique records use more sorted runs and temporary I/O, not an input-sized heap. Counts saturate at UINT64_MAX. The --mem-limit option accepts 8 MiB or greater and binary K/M/G suffixes.

## Platform layer

include/platform.h defines file handles, reads/writes, temp creation, rename/removal, CPU count, and worker-thread calls. src/platform_posix.c uses POSIX file descriptors and pthreads. src/platform_win32.c uses UTF-8-to-wide conversion and Win32 CreateFileW, ReadFile, WriteFile, MoveFileExW, DeleteFileW, and CreateThread; it does not use mmap. Windows arguments enter through wmain and are converted to UTF-8 before core parsing.

Sorted runs must be reopened across merge passes, so run paths remain named until their pass finishes. POSIX mkstemp creates mode-0600 files. On POSIX, every run is removed explicitly on success, handled failure, and caught interruption (SIGINT, SIGTERM, and SIGHUP); lcovmerge ignores SIGPIPE so EPIPE follows the ordinary I/O-failure cleanup path. Unlink-on-open is not used because later merge passes reopen runs by path. SIGKILL and machine failure cannot run cleanup. Windows uses synchronous ReadFile/WriteFile calls and blocking worker joins without a cancellation path, so interruption and cleanup guarantees are POSIX-only; a blocked operation can prevent interruption from completing.

## Determinism

The comparator orders first by rewritten SF path, then record class and typed keys, then textual fields, values, and origin tie-breakers. DA, BRDA, and MC/DC rows are sorted in separate record families by line and their remaining numeric/textual keys. Numeric keys compare numerically; textual branch identifiers and unknown rows compare by UTF-8 byte order. Nonzero chunk and merge path IDs only replace a string comparison when they are guaranteed to identify the same exact path; zero always falls back to bytewise path comparison. Active-reader path ranks are assigned from lexicographically sorted current SF paths, so the heap sees the same path order as the full comparator. Merge operators for counts are commutative and saturating. Checksum and MC/DC expression disagreements choose the lexicographically smallest spelling. TN rows are sorted and deduplicated; unknown records retain multiplicity. The resulting bytes do not depend on input order or the number of workers.

## Limits

The CLI supports at most 32 workers, at least 8 MiB per worker, and listfile nesting depth 8. One input line is limited to 1 MiB. Shell wildcard expansion is delegated to the caller's shell. The worker pool distributes whole input files, so a single very large input file cannot use multiple parser workers. See [LIMITATIONS.md](LIMITATIONS.md) for LCOV semantic differences and [MIGRATING-FROM-LCOV.md](MIGRATING-FROM-LCOV.md) for migration checks.
