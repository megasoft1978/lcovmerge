# Repository notes for Claude Code

`lcovmerge` is a native Rust CLI and library for bounded-memory merging of LCOV `.info` tracefiles. Output must remain deterministic across input order and worker count, and the Rust port targets the established C 1.1.0 CLI behavior.

## Commands

- `make` builds portable PGO `bin/lcovmerge`, caching profiles and target outputs under `.cache/optimized/`. Python 3.11+ and Rust `llvm-tools` are required.
- `make test` runs Rust tests, the Python CLI regression suite, doc consistency, and site checks.
- `make ptest` builds with the test-only `test-partition` feature; release binaries must not enable it.
- `make asan`, `make tsan`, and `make fuzz` run sanitizer or parser mutation checks; see `AGENTS.md` for toolchain requirements.
- `make check-format` runs `git diff --check`, `cargo fmt`, and Clippy.
- `make dist` cross-builds the five release targets with rustup, cargo-zigbuild, and Zig; macOS cross-linking needs an Apple SDK.

## Architecture

See `docs/ARCHITECTURE.md`. The core public modules are shared options/errors (`common`), record ordering (`model`), streaming parser (`parser`), path rewriting (`path`), output/summaries (`emit`), merge orchestration (`engine`), and CLI parsing (`cli`). Keep parsing streamed and bounded, preserve cancellation and temporary-run cleanup, and avoid unsafe Rust unless a reviewed platform need requires it.

## Conventions

- Use Rust 2021 and `cargo fmt`; keep synthetic fixtures small and private coverage data out of the repository.
- Add Rust unit/property tests and CLI regression cases for parser, merge, boundary, malformed-input, and determinism changes.
- Update `CHANGELOG.md` for user-visible changes and keep the CLI reference, usage docs, and man page aligned.
- Existing benchmark results and validation reports describe the C implementation; do not present them as Rust performance results.
- Keep retained logs, generated inputs, and scratch work under ignored `.cache/<run>/` directories.

## Agent model override for the port

This repository historically requested Anthropic models only. For the Rust port integration task, the user explicitly authorized Codex Luna, which overrides that restriction for this task. Continue to follow any later explicit user model instruction.
