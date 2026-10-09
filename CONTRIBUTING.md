# Contributing

Contributions are welcome. Keep changes focused, describe the LCOV input shape that motivates them, and
include a small before/after tracefile when behavior changes.

## Before opening a change

- Search the issue tracker for an existing report or discussion.
- For a behavior change, state what input rows are affected and what output should result.
- For a parser fix, include malformed and boundary cases where appropriate.
- Keep documentation and the canonical man page in sync with the CLI.
- Do not include real coverage data containing sensitive paths or code. Use a minimized synthetic tracefile.

## Development

The C implementation lives in the main repository. Follow its build and test instructions. Run the documented
checks for the code you changed and include the exact commands and results in the pull request. Performance
claims should include machine, OS, input size, cache state, repetition count, and whether output correctness
was checked.

## Pull requests

- Use a concise commit subject that describes the change.
- Explain the problem, the approach, and any compatibility impact.
- Link related issues.
- Update `CHANGELOG.md` for user-visible changes.
- Do not add contributor names to an AUTHORS file; the project intentionally has no AUTHORS file.

Please follow the [Code of Conduct](CODE_OF_CONDUCT.md). Report security vulnerabilities privately as
described in [SECURITY.md](SECURITY.md).
