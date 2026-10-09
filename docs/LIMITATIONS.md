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
- External sorting consumes temporary disk space proportional to input plus intermediate runs. A named run file is necessary because the merge performs bounded-fan-in passes and reopens runs. --tmpdir selects their directory. Files are created privately on POSIX and removed during handled exit/error paths.
- A signal during a long read is checked at line boundaries; cleanup starts after the current read call returns. Sudden process termination such as SIGKILL or power loss can leave named run files behind.
- Parallel parsing is across input files. -j gives no parser scaling when the workload contains only one input file. The merge stage itself is single-threaded.
- The CLI does not expand glob patterns itself. The caller's shell should expand them; use @listfile for explicit lists. Listfile paths are interpreted relative to the process working directory.
- A directory path containing unsupported native Windows path length forms may fail in the Win32 temp helper, which uses GetTempFileNameW and its MAX_PATH buffer.

## Performance and packaging limits

- The Apple Silicon benchmark run did not meet the 250 MB/s target. S measured 222.1 MB/s; M measured 167.3 MB/s with the default jobs setting and 171.2 MB/s at -j1; XL-single measured 234.0 MB/s; PATH-HEAVY measured 66.2 MB/s. These are single-run measurements from the reproducible harness, not a statistical distribution. The default M peak RSS was 25,395,200 bytes (24.2 MiB), within the 32 MiB target. M at -j8 used 38,993,920 bytes (37.2 MiB), above that RSS target.
- Parallel parsing showed little and inconsistent M-workload scaling: -j2 measured 165.2 MB/s, -j4 162.9 MB/s, and -j8 175.8 MB/s compared with -j1 at 171.2 MB/s. The option remains available; deterministic output was verified at -j1, -j2, -j8, and shuffled input order.
- The macOS binaries depend on the OS-provided /usr/lib/libSystem.B.dylib. They have no third-party runtime dependencies, but are not fully static because the macOS system library is dynamically linked. The Linux binaries are statically linked musl executables.
- Wine was unavailable in the validation environment. The Windows x86_64 binary passed PE checks, and the PowerShell test runner passed against the native macOS binary; Windows executable runtime behavior remains unverified here.

Benchmark and dependency evidence: [benchmark results](validation/benchmark.txt), [macOS dependencies](validation/macos-dependencies.txt), and [cross-build checks](validation/cross-build.txt).

## Verification status

Platform-specific and performance claims are limited to the evidence recorded under validation. A successful Windows PE build does not establish Windows runtime behavior; the PowerShell suite still needs execution on a Windows runner or Wine.
