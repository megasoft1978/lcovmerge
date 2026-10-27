# Repository Guidelines

## Constraints

- Output is deterministic across input order and worker count.
- Keep parsing streamed and bounded: 1 MiB line cap, cancellation checks, small parser queues.
- Unsafe Rust only in small documented OS bindings.
- No performance claims without repeatable measurements. Pre-port benchmark results are C 1.1.0 history; do not relabel or overwrite.
- Keep fixtures synthetic and small. Never commit real coverage data.
- `make ptest` enables `test-partition`; never enable it in release artifacts.
- Retained logs, inputs and scratch go under ignored `.cache/<run>/` (`--tmpdir` under `.cache/`). Temp sort runs must be removed on success, handled errors and catchable signals.
- Do not commit the pre-existing uncommitted `docs/site/*` changes unless asked.

## Verify

- `make` (PGO build to `bin/lcovmerge`), `make test` for behavior changes, `make check-format` (`git diff --check`, `cargo fmt`, Clippy).
- Parser or merge changes: Rust unit/property tests plus Python CLI regressions. `make asan`, `make tsan` or `make fuzz` for memory-safety-sensitive parser work.

## Commits and Docs

- Conventional Commit subjects (`fix(parser): ...`, `docs: ...`). Update `CHANGELOG.md` for user-visible changes.

## Orchestration

- Routing: global (~/.claude-work/routing/ROUTING.md).
- Browser/UI test: n/a (site checks run inside `make test`).
- Push: commit and push when review is CLEAN and tests pass.
