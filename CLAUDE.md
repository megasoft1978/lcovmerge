# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

`lcovmerge`: C11 CLI that merges LCOV `.info` tracefiles with bounded memory (streaming parse, external sort spilled to temp files, k-way merge). Output must be byte-deterministic regardless of input order or worker count.

## Commands

- `make` → `bin/lcovmerge` (strict warnings, `-Werror`; `-Wconversion -Wshadow` included)
- `make test` → `tests/run_tests.py --binary bin/lcovmerge` (golden, malformed, differential, determinism) + `tests/doc-consistency.sh`
- `make asan` → ASan/UBSan build, runs core suite (`--no-lcov`)
- `make fuzz` → needs `clang`; `FUZZ_RUNS=N` to adjust
- `make check-format` → `git diff --check` + `clang-format` if installed
- `make dist` → cross builds via `scripts/build-all.sh`
- No single-test selector: `run_tests.py` only takes `--binary` and `--no-lcov`. Cases are functions in that file.
- Optional `lcov` comparison runs only if `lcov` is installed.

## Architecture

- `src/lcovmerge.c` (~2.7k lines): all core logic (parse, sort, merge, emit, CLI). Shared behavior goes here.
- `src/platform_posix.c` / `src/platform_win32.c` + `include/platform.h`: file I/O, temp files, rename/remove, CPU count, threads. Platform APIs stay in these layers. `platform_entry.c` is the POSIX `main` shim; Windows uses `wmain` + UTF-8 conversion.
- Pipeline (details in `docs/ARCHITECTURE.md`):
  1. Streaming parse with 1 MiB line cap, NUL/UTF-8 validation, SF path rewrite/filter.
  2. Direct path: ≤32 regular files already in canonical order → tournament-tree merge, output staged in destination dir. On out-of-order input or budget overflow, staged output is discarded and the fallback runs.
  3. Fallback: bounded external sort (chunks → sorted temp runs; `--mem-limit` shared across workers, ≥8 MiB each, 24 MiB reserved overhead).
  4. K-way merge; summaries (function/branch/MC/DC/line) recomputed; output renamed atomically only after all I/O succeeds.
- Determinism rules live in the comparator (SF path → record class → typed keys → text → values). Count merge is commutative and saturating (UINT64_MAX). Changes to ordering/merge semantics need regression cases.
- Temp run files are named (not unlink-on-open) because later merge passes reopen them by path; they must be removed explicitly on success and handled failure.
- Limits: 32 workers, listfile nesting 8, whole-file worker distribution (one huge file can't be parallel-parsed).

## Other components

- `tools/`: trace generators (`gen-lcov.py`, `gen-path-heavy.py`), `oracle.py`, docs/site helpers (`check_links.py`, `render_benchmarks.py`).
- `scripts/`: CI, packaging, release, differential-vs-lcov, SBOM. `packaging/`: Docker, Homebrew, Scoop, install scripts.
- `action/` + root `action.yml`: GitHub Action (`action/scripts/entrypoint.py`).
- `bench/`, `data/benchmarks.json`, `docs/site/`: benchmarks and published site (`data/benchmarks.json` is mirrored in `docs/site/data/`).
- `docs/validation/`: recorded release validation outputs.

## Conventions

- `.editorconfig`: UTF-8, LF, final newline, 4-space indent (tabs in Makefile).
- Commits: Conventional Commits (`fix(docker): …`, `docs: …`, `test: …`).
- User-visible changes: update `CHANGELOG.md`; keep CLI docs (`docs/USAGE.md`) and `man/lcovmerge.1` consistent — `tests/doc-consistency.sh` enforces part of this.
- Tests: fixtures synthetic and small; never commit real coverage data (leaks paths/code). Use `make asan`/`make fuzz` for parser memory-safety work.
- Markdown lint config: `.markdownlint-cli2.jsonc`.

## Agent Workflow & Workspace Boundaries

- Read `AGENTS.md` before making changes; it is the shared policy for project structure, commands, coding, tests, and safe output locations.
- Codex GPT-6-Luna is the primary implementation agent. Use yowork for complementary work and keep Luna leading the implementation.
- Keep retained prompts, terminal logs, session markers, scratch files, generated inputs, and benchmark results under ignored repo-local `.cache/<run>/` directories. Never leave them beside the repository in its parent folder or on the Desktop.
- Keep temporary benchmark data self-cleaning unless the user needs it for comparison; retain requested data under `.cache/` with its input and report.
