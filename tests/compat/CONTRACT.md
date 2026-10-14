# LCOV 2.6 compatibility oracle

The required oracle is the LCOV source release at commit
`a17443e7dbb31b5b7bff6beac32e7b9b02ec3342` (source version `2.6`; the installed package reports `2.6-0`). The pinned
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
quiet stdout, and whether the output destination was created. Commands use
`--quiet` so diagnostic wording is not mistaken for tracefile output.

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
| `signed-max-count` | Signed 64-bit maximum in line, function, and branch counts | success |
| `count-overflow-saturation` | Aggregate counts saturate at signed 64-bit maximum | success |
| `negative-count-strict` | Negative count rejected | error |
| `negative-count-recovery` | Negative count recovery with `--ignore-errors negative` | success |
| `fraction-count` | Fractional count truncates toward zero | success |
| `checksum-valid` | Existing checksum matches source | success |
| `checksum-mismatch` | Existing checksum mismatch | error |
| `checksum-mismatch-recovery` | Recover checksum mismatch with `--ignore-errors version` | success |
| `checksum-default-ignored` | Checksum is ignored by default | success |
| `checksum-no-flag-precedence` | `--no-checksum` overrides earlier `--checksum` | success |
| `function-conflict-strict` | Same function name at different locations across testcases | success |
| `mcdc-expression-strict` | Cross-testcase MC/DC expression conflict | error |
| `mcdc-expression-recovery` | Recover cross-testcase MC/DC expression conflict | success |
| `mcdc-layout-summary` | MC/DC layout, unreachable records, and summary counts | success |
| `mcdc-duplicate-permuted` | Duplicate MC/DC expressions merged from inputs in permuted order | success |
| `source-version-split` | Conflicting source versions across testcases | error |
| `source-version-recovery` | Recover conflicting source versions | success |
| `malformed-branch-disabled` | Malformed branch payload is ignored when branch coverage is disabled | success |
| `malformed-function-disabled` | Malformed function payload is ignored when function coverage is disabled | success |
| `consistency-enabled-success` | `check_data_consistency=1` on consistent data | success |
| `derive-endline-disabled` | Disable function end-line inference | success |
| `checksum-cross-testcase` | Select and propagate source checksum across testcases | success |
| `branch-only-recovery-summary` | Branch recovery counts nonzero coverpoints, not taken totals | success |
| `derive-endline-cross-testcase` | Derive function end line using that testcase's line data | success |
| `lambda-cpp-no-inference` | Do not infer lambda end line in C++ source | success |
| `function-aliases-cross-testcase` | Source-wide function aliases retain testcase-local identity | success |
| `function-name-commas` | Legacy function names preserve embedded commas | success |
| `modern-reused-id-input-order` | Reused sparse modern IDs retain per-input alias ordering | success |
| `modern-alias-same-start-different-end` | Conflicting function end lines at one start | error |
| `modern-alias-same-start-different-end-recovery` | Recover conflicting function end lines | success |
| `modern-missing-end-cross-input` | Merge function definitions with and without end lines across inputs | success |
| `orphan-function-hit-strict` | Reject FNDA without a matching FN definition | error |
| `orphan-function-hit-recovery` | Recover orphan FNDA with mismatch and empty handling | success |

The seven function cases above are required in both pinned backends. The
end-line conflict is an observed agreement: XS and pure Perl both exit 1 in
strict mode and succeed with `--ignore-errors inconsistent`. Orphan hits need
both `mismatch` and `empty` recovery categories; `mismatch` alone reports the
orphan but then fails because no function coverpoints remain.

## Bounded runtime gates

Run the large synthetic gates separately from the tiny differential matrix:

```sh
python3 tests/compat_bounded.py --binary /absolute/path/to/lcovmerge \
  --workdir .cache/compat-bounded
```

The runner makes three single-source tracefiles with more than 24 million
records each, at least three times the 8 million record budget, then invokes
the candidate with `--mem-limit 8M`. The shapes cover DA plus modern function
aliases, distinct legacy functions, and sparse modern function IDs. It checks
the emitted tracefile in a streaming pass: one section, exact FNL/FNA/FN/FNDA
and DA counts, every DA hit value and line range, and exact LF/LH totals. The
default per-command timeout is 240 seconds and can be changed with
`--timeout`. The generated inputs, commands, stdout/stderr logs, outputs, and
`report.json` stay in `--workdir`; failures are recorded rather than skipped.
These runs do not invoke LCOV or genhtml on the large files.

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

The `integer-exact-above-f64` fixture records a reference disagreement: XS
rounds `9007199254740993` to `9007199254740992`; pure Perl preserves it. The
Rust profile preserves exact digits and selects the pure-Perl policy for this
case. Its XS row must match the retained rounded result with every other field
equal, and is reported as an expected reference difference rather than an XS
conformance pass. Other candidate differences continue to fail.
