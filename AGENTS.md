# Repository Guidelines

## Project Structure

`src/` contains the Rust library and CLI. `Cargo.toml`, `Cargo.lock`, and `build.rs` define the crate, reproducible release profile, dependency lock, and build metadata. `tests/` holds the Python CLI regression runner, Rust unit/property tests, shell and PowerShell entry points, and synthetic fuzz corpus. `tools/` provides trace generators, an oracle, and documentation helpers. Build, packaging, and CI scripts live in `scripts/`; benchmark inputs and runners are in `bench/`. User and developer documentation is in `docs/`, with the CLI reference in `man/`. The GitHub Action is under `action/`.

## Build, Test, and Development Commands

- `make` trains/caches portable PGO and builds the optimized executable at `bin/lcovmerge` (or `.exe` on Windows) using ignored `.cache/optimized/` build and profile storage.
- `make test` runs Rust tests, Python golden/malformed/differential/determinism checks, partition-feature CLI cases, documentation consistency, and site rendering checks.
- `make ptest` builds the test-only binary with the `test-partition` feature; this feature must not be enabled in release artifacts.
- `make asan` and `make tsan` run Rust unit/property and full CLI/partition tests with compiler sanitizers and require a Rust toolchain that supports `-Zsanitizer` (nightly, or the repository's explicit bootstrap setup). ThreadSanitizer also installs `rust-src` and rebuilds the standard library.
- `make fuzz FUZZ_SECONDS=N` runs deterministic, structured and byte-level mutations through the CLI, checks exit status and timeouts, and keeps generated inputs in temporary storage.
- `make dist` builds all release targets. Install Rust with rustup, `cargo-zigbuild`, and Zig; rustup target components are added by the build script. `cargo-zigbuild` uses Zig for Linux/macOS cross linking; Windows is built natively and supplied through `WINDOWS_BINARY` on Unix. Building macOS binaries may require the Apple SDK on the host; see `docs/RELEASING.md`.
- `make check-format` runs `git diff --check`, `cargo fmt`, and Clippy with warnings denied.

## Coding Style and Naming

Source builds require stable Rust, Python 3.11+, and the `llvm-tools` component (installed automatically by Make). Use Rust edition 2021, four-space indentation, idiomatic `snake_case`, and `cargo fmt`. Keep parser and merger logic safe. Restrict unsafe Rust to small documented OS bindings for signal handlers, polling, and descriptor limits; the crate denies unsafe operations inside unsafe functions unless explicitly scoped. Keep the parser streaming and bounded: preserve the 1 MiB line cap, cancellation checks, path interning per section, and small parser queues. Keep platform-specific behavior behind narrow modules and the public module/API boundaries in `src/lib.rs`. Release builds use `opt-level=3`, thin LTO, and one codegen unit; do not claim a performance change without repeatable measurements.

## Testing Guidelines

Add Rust unit/property tests and Python CLI regressions for parser or merge changes, including boundaries, malformed inputs, determinism, and cancellation where relevant. Run `make test` for behavior changes and report the exact commands and results. Use `make asan`, `make tsan`, or `make fuzz` for memory-safety-sensitive parser work. Keep fixtures synthetic and small; never commit real coverage data that could expose source paths or code. Do not relabel or overwrite pre-port benchmark results: they are historical C 1.1.0 measurements.

## Agent Workflow and Workspace Boundaries

- The user has explicitly authorized Codex Luna for the Rust port integration task; that task-specific instruction overrides the earlier Anthropic-only model rule. Follow later explicit user model instructions when present.
- Keep retained prompts, terminal logs, session markers, scratch files, generated inputs, and benchmark results under ignored repo-local `.cache/<run>/` directories. Never leave them beside the repository in its parent folder or on the Desktop.
- Prefer scripts that clean temporary benchmark data automatically. If retaining outputs for later comparison, place them under `.cache/` and preserve the source inputs and reports together.
- The CLI's system temporary directory is an intentional default. For project runs that need retained temporary runs, pass `--tmpdir` under `.cache/`; remove those runs after the operation.
- Temporary sort runs must be removed on success, handled errors, and catchable termination signals. A forced kill or machine crash cannot guarantee cleanup.

## Commit and Pull Request Guidelines

Recent commits use concise Conventional Commit subjects such as `fix(parser): ...`, `docs: ...`, and `test: ...`. Pull requests should describe the problem, approach, and compatibility impact; link related issues; include validation results; update `CHANGELOG.md` for user-visible changes; and keep CLI documentation and the man page consistent. Include a small before/after synthetic tracefile when merge behavior changes.
