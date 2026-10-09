# lcovmerge

lcovmerge is a C11 streaming merger for LCOV tracefiles. It uses bounded record arenas, sorted temporary runs, and a k-way merge so memory use stays bounded as the input grows.

    make
    bin/lcovmerge shard-a.info shard-b.info -o merged.info

Inputs may include - for standard input, @listfile entries, and shell-expanded globs. Output - writes to standard output. File output is written to a temporary file beside the destination and renamed after a successful merge.

Use lcovmerge --help or the [man page](man/lcovmerge.1) for the full option list. The default record arena limit is 64 MiB. Temporary run files are created under the system temporary directory, or under --tmpdir.

## Build and test

make uses the system C11 compiler. The POSIX build needs pthreads; it has no third-party runtime dependency. make test runs 54 golden cases, malformed-input checks, 220 seeded generated differential cases plus malformed oracle cases, I/O/options checks, determinism checks, and an lcov -a summary comparison when lcov is installed. The same Python runner is available through tests/run.sh and tests/run.ps1.

    make test
    make asan
    make fuzz                 # ASan/UBSan mutation runner, default 2 million executions
    make test-docker          # linux/amd64 and linux/arm64 Debian containers
    make dist                 # five Zig cross-target builds and reproducible archives

make bench regenerates S, M, XL-single, and PATH-HEAVY data with the C and Python generators from the read-only benchmark checkout at /Users/megasoft78/Desktop/Freelance/lcov-merge-bench (override with LCOVMERGE_BENCH_REPO). Datasets are created in a temporary directory outside this repository and removed after measurement.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/LIMITATIONS.md](docs/LIMITATIONS.md), and [docs/validation/SUMMARY.md](docs/validation/SUMMARY.md) for implementation details, semantic differences, and recorded evidence.
