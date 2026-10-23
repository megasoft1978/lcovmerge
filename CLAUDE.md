# CLAUDE.md

Project rules, commands and constraints: see [AGENTS.md](AGENTS.md).

## Orchestration
- Verify: `make test`; `make check-format`
- Browser/UI test: n/a (site rendering checks run inside `make test`)
- Push policy: commit+push when review CLEAN and tests pass
- Project overrides: retained logs and scratch under `.cache/<run>/`; do not commit the pre-existing uncommitted `docs/site/*` changes unless asked
