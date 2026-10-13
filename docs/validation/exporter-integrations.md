# Exporter integration checks

These checks exercise the handoff from separate exporter-generated LCOV files through lcovmerge into `genhtml`. They use tiny synthetic programs and two complementary test shards per language. They confirm basic interoperability for these exact tool versions and fixtures; they do not establish production performance, broad format compatibility, or adoption by real projects.

## Results

| Producer | Exporter | Merged report | Result |
| --- | --- | --- | --- |
| Python 3.12.12 | Coverage.py 7.16.2 | `genhtml` 2.6-0 | Passed; 1 source file, 5/6 lines covered |
| Go 1.25.7 | `gcov2lcov` 1.1.1 | `genhtml` 2.6-0 | Passed; 1 source file, 4/4 lines covered |
| Node.js 24.14.0 | c8 12.0.0 | `genhtml` 2.6-0 | Passed; 1 source file, 5/5 lines covered |

All three produced separate LCOV shards, merged them with lcovmerge 1.1.0, and generated a report from the merged file. Inputs and generated reports are disposable and kept under `.cache/integration-validation/`; no real project coverage data is included.

## Reproduce the handoff

The fixtures each define a small function with two branches and split tests so each shard covers a different path. Run the commands from a fixture project after creating its source and tests:

### Python / Coverage.py

```sh
python -m pip install coverage
mkdir -p coverage/shards
COVERAGE_FILE=.coverage-negative python -m coverage run --source=src -m unittest discover -s tests -p 'test_negative.py'
COVERAGE_FILE=.coverage-negative python -m coverage lcov -o coverage/shards/negative.info
COVERAGE_FILE=.coverage-positive python -m coverage run --source=src -m unittest discover -s tests -p 'test_positive.py'
COVERAGE_FILE=.coverage-positive python -m coverage lcov -o coverage/shards/positive.info
lcovmerge coverage/shards/negative.info coverage/shards/positive.info -o coverage/merged.info
genhtml coverage/merged.info --output-directory coverage/html
```

### Go / gcov2lcov

```sh
mkdir -p coverage
go test -coverprofile=coverage/negative.out -run '^TestNegative$' .
gcov2lcov -infile=coverage/negative.out -outfile=coverage/negative.info
go test -coverprofile=coverage/positive.out -run '^TestPositive$' .
gcov2lcov -infile=coverage/positive.out -outfile=coverage/positive.info
lcovmerge coverage/negative.info coverage/positive.info -o coverage/merged.info
genhtml coverage/merged.info --output-directory coverage/html
```

Run `genhtml` from a directory where the source paths written by the converter resolve. The tested `gcov2lcov` output used paths relative to the repository root.

### JavaScript / c8

```sh
npm install --save-dev c8
mkdir -p coverage
npx c8 --reporter=lcov --reports-dir=coverage/negative node --test negative.test.mjs
npx c8 --reporter=lcov --reports-dir=coverage/positive node --test positive.test.mjs
lcovmerge coverage/negative/lcov.info coverage/positive/lcov.info -o coverage/merged.info
genhtml coverage/merged.info --output-directory coverage/html
```

For evaluating a production pipeline, use the same downloaded shard files with both the current merge and lcovmerge, then compare the records consumed by the project and generate the same downstream report. See the [decision guide](../DECISION.md), [reversible migration pilot](../MIGRATING-FROM-LCOV.md#reversible-one-week-pilot), and [real-workload measurement guide](../REAL-WORKLOADS.md).

## GitHub Action check

The published v1.1.0 macOS arm64 archive was downloaded and checksum-verified by running the Action entrypoint locally against the same two synthetic shards used by the Action smoke workflow. The merged output matched the locally built CLI byte-for-byte; the entrypoint wrote both the GitHub output summary and step summary. The 12 Action unit tests passed, and `actionlint` passed on the smoke, main CI, and Pages workflows.

This checks the Unix/macOS entrypoint and release asset locally. It does not reproduce GitHub's artifact transport or hosted Linux/Windows runners; the repository's [Action smoke workflow](../../.github/workflows/action-smoke.yml) covers Ubuntu, macOS, and Windows when CI runs.

## Upstream exporter references

- [Coverage.py LCOV command](https://coverage.readthedocs.io/en/7.11.3/commands/cmd_lcov.html)
- [gcov2lcov](https://github.com/jandelgado/gcov2lcov)
- [c8](https://github.com/bcoe/c8)
