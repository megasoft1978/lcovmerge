# LCOV 2.6 compatibility oracle

The required oracle is the LCOV source release at commit
`a17443e7dbb31b5b7bff6beac32e7b9b02ec3342` (`LCOV version 2.6-0`). The pinned
source tree is read-only. `tools/lcov-oracle-identity.py --strict` checks its
commit and every tracked file against the commit object, then records the
selected launcher and implementation executable, library, package deviations, Perl runtime and
environment, the LCOV Perl library and XS loader hashes, the XS binary hash,
and real XS/pure-Perl import probes.
The probe imports `lcovutil` normally so its XS loader executes. Strict mode
requires the XS probe to report `1` and the forced `LCOV_PURE_PERL=1` probe to
report `0`; unavailable backends stop the harness.

Use `--source-root PATH` to select the pinned checkout. `--lcov PATH` selects
the actual oracle executable used by every command; otherwise the runner uses
`lcov` on `PATH`, then `source-root/bin/lcov`. `--genhtml PATH` selects the
matching HTML consumer. Source-tree runs use `bin/lcov`, `bin/genhtml`, and
`lib/` from that checkout. If the checkout does not have a built XS extension,
strict identity fails instead of describing a pure-Perl-only run as complete.

Run oracle characterization and differential checks with:

```sh
python3 tests/compat/oracle.py --oracle-only
python3 tests/compat/oracle.py --binary /absolute/path/to/lcovmerge
```

The default work directory is `.cache/lcov-compat-oracle/`. Every required
fixture runs once with XS and once with forced pure Perl. LCOV's profile record
confirms the backend used by each individual fixture. The manifest captures
commands, timeout and status, full stdout/stderr and hashes, config contents,
source and expanded input hashes, trace output and hashes, plus genhtml output
and coverage statistics. Outputs are removed before each command. Success
cases require matching normalized trace semantics and successful genhtml
consumption with matching coverage statistics. Failure cases assert the
expected oracle status for both backends; candidate runs must match status,
stdout, and whether the output destination was created.

## Required case matrix

All rows below are required in both backends. `success` and `error` are
expected oracle exit classes, checked before candidate results are counted.

| Case | Behavior exercised | Oracle |
| --- | --- | --- |
| `tn-split` | Keep distinct test names | success |
| `duplicate-input` | Duplicate input aggregation | success |
| `true-empty-source` | Empty source file with a data record | success |
| `uncovered-line` | Uncovered line data | success |
| `branch-canonical` | Canonicalize block IDs when merging | success |
| `branch-duplicate-signature` | Duplicate branch signatures | success |
| `branch-strict-inconsistent` | Strict line/branch consistency | error |
| `branch-recovery-inconsistent` | Recover with `--ignore-errors inconsistent` | success |
| `branch-absent-counts` | Absent branch counts | success |
| `function-alias-group-endline` | Legacy and modern names, aliases, end lines | success |
| `mcdc-enabled` | Enabled MC/DC records | success |
| `mcdc-strict-malformed` | Strict malformed MC/DC record | error |
| `mcdc-recovery-malformed` | Format and empty recovery | success |
| `mcdc-disabled-malformed` | Malformed MC/DC while disabled | success |
| `checksum-conflict` | Conflicting checksums | error |
| `checksum-source-error` | Checksum verification with missing source | error |
| `version-error` | Trace version mismatch | error |
| `defaults` | LCOV coverage defaults | success |
| `toggle-precedence` | Repeated coverage option ordering | success |
| `forget-names` | Forget testcase names | success |
| `include-source` | Include path filtering | success |
| `exclude-source` | Exclude path filtering | success |
| `rc-enable-branch` | `--rc` branch setting | success |
| `explicit-config-precedence` | Explicit config versus command-line RC/flag | success |
| `glob-flat` | Flat wildcard expansion | success |
| `glob-bracket` | Bracket wildcard expansion | success |
| `glob-nested` | Nested wildcard expansion | success |
| `stdout-output` | Trace output directed to stdout | success |
| `gzip-output` | Gzip trace output | success |
| `truncated-gzip-input` | Truncated gzip input | error |
| `malformed-da` | Malformed line record | error |
| `malformed-branch` | Malformed branch record | error |
| `malformed-function` | Malformed function record | error |

## Comparison rules and limits

Normalization changes line endings and sorts unordered records within a
section. It preserves section order, TN, SF, VER, all coverage values and
checksums, summaries, function records and alias order, branch signatures, and
all MC/DC identity fields. The unit suite includes deliberately altered
identity, line, branch, function alias, summary, checksum, and MC/DC records;
each must compare unequal.

This matrix is an initial required set, not a complete LCOV implementation
claim. Optional or unimplemented operations remain separate from required
rows and are never counted as skipped passes. Gaps include stdin/list-file
inputs, Unicode and byte paths, parallel/tempdir/memory behavior, keep-going,
output collisions, and broader checksum version/format combinations. Add
required rows with an explicit expected oracle status before relying on them
for conformance.

Run comparator tests with:

```sh
python3 -m unittest discover -s tests/compat -p 'test_*.py'
```
