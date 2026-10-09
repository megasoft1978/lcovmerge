# Architecture

## Data flow

1. **Streaming parse.** Each worker reads one input at a time through a 64 KiB I/O block and a reusable line buffer. Lines are capped at 1 MiB and checked for NUL and valid UTF-8. Parsing validates each recognized row and rewrites/filters SF paths before storing coverpoints.
2. **Bounded run generation.** Parsed rows are copied into a compact array plus string arena. Each worker chunk interns up to 4,096 distinct SF paths in a fixed-size open-addressed table, so qsort can compare records from the same path by ID. Once the table fills, new paths use ID zero and compare by their full strings. Repeated SF paths are stored once per chunk, and empty row fields use a shared empty string. At the worker's arena threshold, qsort orders a chunk only if its rows arrived out of canonical order, then serializes it to a uniquely created temporary run. Worker budgets share the configured --mem-limit after reserving up to 24 MiB for non-arena process memory; each active worker still receives at least 8 MiB. Row arrays, string arenas, and path tables are charged to those worker budgets. The default 64 MiB cap therefore assigns up to 40 MiB total to worker chunks for up to four jobs.
3. **K-way merge.** A min-heap merges at most 16 sorted runs at once. If more remain, intermediate sorted runs are written in another pass until the final merge has a bounded fan-in. Per-key counts are combined as rows stream through the heap.
4. **Emit.** The final merge emits one SF section at a time. Function, branch, MC/DC, and line summaries are recomputed. File output is staged in the destination directory and renamed only after all reads, writes, closes, and summary emission succeed.

## Memory model

The default 64 MiB setting bounds the per-worker row arrays, arenas, and fixed 4,096-entry path tables. Up to 24 MiB is held back from the worker chunk budget for parser/writer buffers, merge bookkeeping, thread stacks, and runtime state, while preserving at least 8 MiB per active worker. The program also uses a bounded merge fan-in, path/argument lists, and small bookkeeping structures. Merge readers allocate a small buffer for ordinary records and grow it only when a stored record requires it. RSS includes these buffers and the C runtime; temporary disk use scales with parsed input and intermediate merge passes.

Rows are limited by the 1 MiB input-line cap. Inputs with many unique records use more sorted runs and temporary I/O, not an input-sized heap. Counts saturate at UINT64_MAX. The --mem-limit option accepts 8 MiB or greater and binary K/M/G suffixes.

## Platform layer

include/platform.h defines file handles, reads/writes, temp creation, rename/removal, CPU count, and worker-thread calls. src/platform_posix.c uses POSIX file descriptors and pthreads. src/platform_win32.c uses UTF-8-to-wide conversion and Win32 CreateFileW, ReadFile, WriteFile, MoveFileExW, DeleteFileW, and CreateThread; it does not use mmap. Windows arguments enter through wmain and are converted to UTF-8 before core parsing.

Sorted runs must be reopened across merge passes, so run paths remain named until their pass finishes. POSIX mkstemp creates mode-0600 files. The implementation removes every run explicitly on success and handled failure; unlink-on-open is not used because later merge passes reopen those files by path.

## Determinism

The comparator orders first by rewritten SF path, then record class and typed keys, then textual fields, values, and origin tie-breakers. DA, BRDA, and MC/DC rows are sorted in separate record families by line and their remaining numeric/textual keys. Numeric keys compare numerically; textual branch identifiers and unknown rows compare by UTF-8 byte order. Nonzero chunk and merge path IDs only replace a string comparison when they are guaranteed to identify the same exact path; zero always falls back to bytewise path comparison. Merge operators for counts are commutative and saturating. Checksum and MC/DC expression disagreements choose the lexicographically smallest spelling. TN rows are sorted and deduplicated; unknown records retain multiplicity. The resulting bytes do not depend on input order or the number of workers.

## Limits

The CLI supports at most 32 workers, at least 8 MiB per worker, and listfile nesting depth 8. One input line is limited to 1 MiB. Shell wildcard expansion is delegated to the caller's shell. The worker pool distributes whole input files, so a single very large input file cannot use multiple parser workers. See LIMITATIONS.md for LCOV semantic differences and unsupported behavior.
