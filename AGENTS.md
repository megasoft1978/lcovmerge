# Repository Guidelines

## Project Structure & Module Organization

`src/` contains the C11 merger and platform-specific POSIX and Windows layers; public platform and version headers are in `include/`. `tests/` holds the Python test runner, shell and PowerShell entry points, and C fuzz harnesses and corpus. `tools/` provides trace generators, an oracle, and documentation helpers. Build, packaging, and CI scripts live in `scripts/`; benchmark inputs and runners are in `bench/`. User and developer documentation is in `docs/`, with the CLI reference in `man/`. The GitHub Action is under `action/`.

## Build, Test, and Development Commands

- `make` builds `bin/lcovmerge` (or `bin/lcovmerge.exe` on Windows).
- `make test` builds and runs golden, malformed-input, differential, and determinism checks, then checks documentation consistency. The optional comparison with `lcov` runs when it is installed.
- `make asan` builds with AddressSanitizer and UndefinedBehaviorSanitizer and runs the core suite.
- `make fuzz` builds the Clang sanitizer fuzz harness and runs its corpus; set `FUZZ_RUNS` to adjust the run count.
- `make check-format` checks whitespace and runs `clang-format` when available.

## Coding Style & Naming Conventions

Follow `.editorconfig`: UTF-8, LF endings, final newline, no trailing whitespace, and four spaces for source files; Makefile recipes use tabs. C builds with strict warnings as errors. Keep platform APIs in the matching `src/platform_*.c` layer and shared behavior in `src/lcovmerge.c`. Name test cases descriptively in `tests/run_tests.py` and keep generated fixtures small and synthetic.

## Testing Guidelines

Add regression cases for parser or merge changes, including boundary and malformed inputs where relevant. Run `make test` for behavior changes and report the exact commands and results. Use `make asan` or `make fuzz` for memory-safety-sensitive parser work. Do not include real coverage data that could expose source paths or code.

## Agent Workflow & Workspace Boundaries

- Use Codex GPT-6-Luna as the primary implementation agent when selecting a Codex model; keep yowork complementary.
- Keep retained prompts, terminal logs, session markers, scratch files, generated inputs, and benchmark results under ignored repo-local `.cache/<run>/` directories. Never leave them beside the repository in its parent folder or on the Desktop.
- Prefer scripts that clean temporary benchmark data automatically. If retaining their outputs for later comparison, place them under `.cache/` and preserve the source inputs and reports together.
- The CLI's system temporary directory is an intentional default. For project runs that need retained temporary runs, pass `--tmpdir` under `.cache/`; remove those runs after the operation.
- Temporary sort runs must be removed on success, handled errors, and catchable termination signals. A forced kill or machine crash cannot guarantee cleanup.

## Commit & Pull Request Guidelines

Recent commits use concise Conventional Commit subjects such as `fix(docker): ...`, `docs: ...`, and `test: ...`. Pull requests should describe the problem, approach, and compatibility impact; link related issues; include validation results; update `CHANGELOG.md` for user-visible changes; and keep CLI documentation and the man page consistent. Include a small before/after tracefile when merge behavior changes.
