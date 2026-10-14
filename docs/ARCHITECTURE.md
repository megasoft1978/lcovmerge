# Architecture

lcovmerge is implemented in native Rust. `Cargo.toml` defines the library and CLI, the release optimization profile, and the test-only `test-partition` feature. There is no C foreign-function interface to the former merger; small audited OS bindings handle signals, polling, and descriptor limits.

## Data flow

1. **Parse inputs as streams.** The parser reads through buffered I/O, checks cancellation, rejects NUL and invalid UTF-8, and caps each input line at 1 MiB. It validates recognized records and rewrites or filters `SF:` paths before records enter the merge. A section's path is shared through an interned `Arc<str>`; the parser does not buffer a whole section.
2. **Use the sorted-input path when possible.** Already ordered regular-file inputs can merge directly with bounded reader and queue state. If the input order or memory budget prevents that path, lcovmerge falls back to external sorting. With an explicit `-j N`, the testable partition path can divide sorted input by source-path range and join staged output in order; anomalies trigger the serial path so diagnostics and output retain the same rules.
3. **Sort through temporary files when needed.** The fallback holds records up to the configured record-memory budget, sorts each batch, and spills sorted runs to the selected temporary directory. Bounded-fan-in merge passes combine those runs without holding all input records in memory.
4. **Aggregate and write output.** Counts are combined as records stream through the merge, and summary rows are recalculated. File output is staged beside the destination and renamed only after successful completion. Output to stdout (`-o -`) streams directly and cannot be rolled back after a later failure.

## Rust modules

`src/lib.rs` exposes the reusable modules and API; `src/main.rs` is the executable entry point. The native modules are organized around shared options and errors (`common`), record representation and ordering (`model`), path rewriting/filtering (`path`), streaming input (`parser`), summary and output formatting (`emit`), merge orchestration and temporary runs (`engine`), source-path partition planning (`partition`), and command-line parsing (`cli`). Keep the parser and merge state bounded and preserve the public behavior described in [the CLI reference](../man/lcovmerge.1) and [usage guide](USAGE.md).

The opt-in `--lcov-compat=2.6` request is dispatched separately through the compatibility modules (`src/compat`). It resolves profile configuration and options without changing native defaults or the native option contract. Its supported command surface and unsupported capture, reset, extract/remove, genhtml, and Perl callback features are listed in the [LCOV profile contract](LCOV-PROFILE.md). Development validation remains in progress. The profile's resource behavior has not passed the external spill gate; native memory and parallel guarantees must not be treated as profile evidence.

## Memory and cancellation

The default `--mem-limit` bounds record storage, not total process memory. Parser and writer buffers, merge bookkeeping, thread stacks, and runtime state add overhead. The 1 MiB line cap and minimum worker budget are defined in `common`; temporary disk use is separate and grows with parsed input and merge passes. Tests should cover behavior at these boundaries. On POSIX, `SIGINT`, `SIGTERM`, and `SIGHUP` are caught and set the shared cancellation state; the engine removes temporary sort runs on success, handled errors, and caught interruption. `SIGPIPE` is ignored so `EPIPE` follows normal I/O-failure cleanup. Windows uses blocking `ReadFile`/`WriteFile` operations and blocking worker joins, which can delay cancellation; these signal-cleanup guarantees are POSIX-only. A forced kill or machine failure cannot run cleanup. Output to stdout cannot be rolled back after bytes have been written.

## Determinism and compatibility

The record ordering and key comparison rules live in the model layer. Keep output bytes, diagnostics, exit statuses, path handling, and overflow behavior deterministic across input ordering and worker count. The CLI regression suite compares golden output, malformed input handling, differential cases, determinism, and forced partitions. The Rust unit/property tests exercise the parser and merge primitives directly.

Native mode targets behavioral compatibility with C lcovmerge 1.1.0 and retains the documented differences from `lcov -a`; see [compatibility notes](COMPATIBILITY.md). The opt-in profile is a separate implementation with its own validation status. Existing benchmark data and reports labeled for C lcovmerge 1.1.0 remain historical C measurements, not Rust native-mode or profile performance evidence.

## Release builds

Cargo release builds use optimization level 3, thin LTO, and one codegen unit. `make` and native release CI use `scripts/build-optimized.py` to train portable PGO on synthetic seed 42 fixtures. Training profiles are cached by source, compiler, target, build flags, and commit; frozen profile hashes force Cargo to rebuild when feedback changes. `cargo build --release` remains an ordinary unprofiled build. Cross builds use `scripts/build-all.sh` with Zig for Linux musl linking and the native Apple SDK for both Apple targets on macOS. Cross artifacts are unprofiled; native release runners train each target. See [the release guide](RELEASING.md).

Sorted merges use a tournament tree, source-path ranks, and shared paths among active inputs. Complete input lines are borrowed from the reader buffer; split and long lines use bounded storage. Reader buffers are 64 KiB for up to 32 inputs and 16 KiB for larger fan-in, with their full capacity included in merge budgets.

ThreadSanitizer builds also instrument the standard library with `-Z build-std` and require the `rust-src` component, following the [Rust sanitizer guidance](https://doc.rust-lang.org/stable/unstable-book/compiler-flags/sanitizer.html#threadsanitizer). `make tsan` runs the unit/property and CLI/forced-partition suites.
