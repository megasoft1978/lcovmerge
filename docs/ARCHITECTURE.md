# Architecture

## Data flow

1. **Streaming parse.** Inputs are regular-file checked before selecting the direct path. Each stream uses a 64 KiB I/O block and a reusable line buffer. Lines are capped at 1 MiB and checked for NUL and valid UTF-8. Parsing validates recognized rows and rewrites/filters SF paths before records enter the merge.
2. **Sorted-input merge.** For up to 32 regular files whose rewritten record streams are already in canonical order, the parser feeds a tournament-tree merge directly. DA and BRDA rows use dedicated fast parsers; other records use the common parser. Queue rows refer to the current input buffer until consumed. The previous sort key is retained separately, and the attempt stages output in the destination directory. If a stream is out of order or the bounded direct-path budget is exceeded, staged output and buffered diagnostics are discarded before the external-sort path starts.
3. **Bounded external sort.** The fallback parses files into a compact array plus string arena. At the worker's arena threshold, qsort orders a chunk only if its rows arrived out of canonical order. Sorted rows serialize to temporary runs. Worker budgets share the configured --mem-limit after reserving up to 24 MiB for non-arena process memory; each active worker still receives at least 8 MiB.
4. **K-way merge and emit.** Tournament/heap merges combine sorted runs with bounded fan-in, writing intermediate runs as needed. Per-key counts are combined as rows stream through the merge. Function, branch, MC/DC, and line summaries are recomputed. File output is staged in the destination directory and renamed only after all reads, writes, closes, and summary emission succeed.

## Memory model

The default 64 MiB setting bounds the per-worker row arrays and arenas. Up to 24 MiB is held back from the worker chunk budget for parser/writer buffers, merge bookkeeping, thread stacks, and runtime state, while preserving at least 8 MiB per active worker. The program also uses a bounded merge fan-in, path/argument lists, and small bookkeeping structures. Merge readers allocate a small buffer for ordinary records and grow it only when a stored record requires it. RSS includes these buffers and the C runtime; temporary disk use scales with parsed input and intermediate merge passes.

Rows are limited by the 1 MiB input-line cap. Inputs with many unique records use more sorted runs and temporary I/O, not an input-sized heap. Counts saturate at UINT64_MAX. The --mem-limit option accepts 8 MiB or greater and binary K/M/G suffixes.

## Platform layer

include/platform.h defines file handles, reads/writes, temp creation, rename/removal, CPU count, and worker-thread calls. src/platform_posix.c uses POSIX file descriptors and pthreads. src/platform_win32.c uses UTF-8-to-wide conversion and Win32 CreateFileW, ReadFile, WriteFile, MoveFileExW, DeleteFileW, and CreateThread; it does not use mmap. Windows arguments enter through wmain and are converted to UTF-8 before core parsing.

Sorted runs must be reopened across merge passes, so run paths remain named until their pass finishes. POSIX mkstemp creates mode-0600 files. The implementation removes every run explicitly on success and handled failure; unlink-on-open is not used because later merge passes reopen those files by path.

## Determinism

The comparator orders first by rewritten SF path, then record class and typed keys, then textual fields, values, and origin tie-breakers. DA, BRDA, and MC/DC rows are sorted in separate record families by line and their remaining numeric/textual keys. Numeric keys compare numerically; textual branch identifiers and unknown rows compare by UTF-8 byte order. Nonzero chunk and merge path IDs only replace a string comparison when they are guaranteed to identify the same exact path; zero always falls back to bytewise path comparison. Active-reader path ranks are assigned from lexicographically sorted current SF paths, so the heap sees the same path order as the full comparator. Merge operators for counts are commutative and saturating. Checksum and MC/DC expression disagreements choose the lexicographically smallest spelling. TN rows are sorted and deduplicated; unknown records retain multiplicity. The resulting bytes do not depend on input order or the number of workers.

## Limits

The CLI supports at most 32 workers, at least 8 MiB per worker, and listfile nesting depth 8. One input line is limited to 1 MiB. Shell wildcard expansion is delegated to the caller's shell. The worker pool distributes whole input files, so a single very large input file cannot use multiple parser workers. See LIMITATIONS.md for LCOV semantic differences and unsupported behavior.
