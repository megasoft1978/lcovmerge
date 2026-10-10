# Architecture

## Data flow

1. **Read LCOV files.** Inputs are checked before choosing the direct path. Each stream uses a 64 KiB I/O block and a reusable line buffer. Lines are limited to 1 MiB and checked for NUL bytes and valid UTF-8. Recognized rows are validated, and `SF:` paths are rewritten or filtered before records enter the merge.
2. **Merge already-sorted inputs.** For up to 32 regular files whose rewritten rows are already in stable order, the parser feeds a tournament-tree merge directly. DA and BRDA rows use dedicated parsers; other rows use the common parser. Queue rows refer to the current input buffer until consumed. If a stream is out of order or the direct path exceeds its storage budget, staged output and buffered diagnostics are discarded before the fallback begins.
3. **Sort through temporary files.** The fallback keeps coverage records in memory up to the configured limit. When a batch is full, it orders the records and writes a temporary sorted file (called a sort run in the implementation). Later merge passes reopen these files by name. The sizing rule reserves up to 24 MiB of `--mem-limit` from worker storage for anticipated non-record overhead; the minimum 8 MiB per active worker takes precedence for smaller settings.
4. **Merge and write output.** Tournament or heap merges combine sorted files in bounded-fan-in passes, writing intermediate files as needed. Counts are combined while rows stream through the merge. Function, branch, line, and MC/DC coverage records feed recalculated summary rows. File output is staged in the destination directory and renamed only after all reads, writes, closes, and summary emission succeed. Output to stdout (`-o -`) streams directly and cannot be rolled back after a later failure.

## Memory model

The default `--mem-limit` value of 64 MiB limits memory reserved for coverage records while sorting; it is not a limit on total process memory. The implementation can hold back up to 24 MiB from worker storage for parser/writer buffers, merge bookkeeping, thread stacks, and runtime state. The 8 MiB minimum per active worker takes precedence for smaller settings. Actual resident memory also includes merge readers and allocator state. Temporary disk use is separate and grows with parsed input and intermediate merge passes.

Rows are limited by the 1 MiB input-line cap. Inputs with many unique records create more temporary sorted files and I/O, not an input-sized heap. Counts saturate at `UINT64_MAX`. `--mem-limit` accepts 8 MiB or greater and binary K/M/G suffixes.

## Platform layer

`include/platform.h` defines file handles, reads and writes, temporary-file creation, rename/removal, CPU count, and worker-thread calls. `src/platform_posix.c` uses POSIX file descriptors and pthreads. `src/platform_win32.c` uses UTF-8-to-wide conversion and Win32 `CreateFileW`, `ReadFile`, `WriteFile`, `MoveFileExW`, `DeleteFileW`, and `CreateThread`; it does not use `mmap`. Windows arguments enter through `wmain` and are converted to UTF-8 before core parsing.

Temporary sorted files must be reopened across merge passes, so their names remain until each pass finishes. POSIX `mkstemp` creates mode-0600 files. On POSIX, files are removed explicitly on success, handled failure, and caught interruption (SIGINT, SIGTERM, and SIGHUP); lcovmerge ignores SIGPIPE so EPIPE follows the ordinary I/O-failure cleanup path. A forced kill or machine failure cannot run cleanup. Windows uses synchronous reads/writes and blocking worker joins without cancellation, so a blocked operation can prevent interruption from completing; the signal-cleanup guarantee is POSIX-only.

## Determinism

The comparator orders records by rewritten `SF:` path, then record class and typed keys, then text, values, and origin tie-breakers. DA, BRDA, and MC/DC rows are sorted in separate families by line and remaining numeric/textual keys. Numeric keys compare numerically; textual branch identifiers and unknown rows compare by UTF-8 byte order. Nonzero path IDs replace a string comparison only when they identify the same exact path; zero always falls back to bytewise path comparison. Active-reader path ranks are assigned from lexicographically sorted current `SF:` paths, so the merge sees the same path order as the full comparator. Count operations are commutative and saturating. Checksum and MC/DC expression disagreements choose the lexicographically smallest spelling. TN rows are sorted and deduplicated; unknown records retain multiplicity. With the same inputs, options, and build, the resulting bytes do not depend on input order or worker count.

## Limits

The CLI supports at most 32 workers, at least 8 MiB per worker, and `@listfile` nesting depth 8. One input line is limited to 1 MiB. Shell wildcard expansion is delegated to the caller's shell. Workers process whole input files, so a single very large file cannot use multiple parser workers. See [LIMITATIONS.md](LIMITATIONS.md) for record differences and [MIGRATING-FROM-LCOV.md](MIGRATING-FROM-LCOV.md) for migration checks.
