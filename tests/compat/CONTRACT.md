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

BRF counts reachable branch coverpoints, including unknown (`-`) counts;
BRH counts positive hits. `U` coverpoints are excluded from those summaries but
participate in line-consistency checks. Missing-DA recovery uses the number of
positive canonical branch coverpoints, including `U`, rather than the sum of
their taken counts. A single testcase receives the generated DA record;
multiple testcases can retain it only in the source summary. Disabling
consistency checks leaves missing DA records absent. Perl accepts a positive
line with no positive branch hit; a positive branch with a zero line still fails.

Explicitly enabling a coverage family with no corresponding coverpoints raises
`empty`; default automatic function handling does not. Ignoring that category
retains valid DA data and emits zero function summaries for explicit function
enablement. The required cases verify these controls separately from branch
consistency. The old `branch-line-cross-testcase` fixture has balanced branch
and line data and fails because it explicitly enables absent function data;
its status is retained, without claiming that it proves a branch mismatch.

| Case | Behavior exercised | Oracle |
| --- | --- | --- |
| `tn-split` | Keep distinct test names | success |
| `duplicate-input` | Duplicate input aggregation | success |
| `true-empty-source` | Empty source file with a data record | success |
| `uncovered-line` | Uncovered line data | success |
| `branch-canonical` | Canonicalize block IDs when merging | success |
| `branch-duplicate-da-max-saturation` | Three duplicate DA counts saturate at signed 64-bit maximum in the branch collector | success |
| `branch-negative-strict` / `branch-negative-recovery` | Negative BRDA hit count is rejected; recovery needs `negative,inconsistent` and normalizes the count to zero | error / success |
| `branch-large-sparse-block` | Sparse branch block ID 70000 is canonicalized without materializing intervening IDs | success |
| `branch-duplicate-numeric-ids` | Numerically equivalent branch IDs (`0` and `00`) remain separate records in one block | success |
| `branch-line-cross-testcase` | Explicit function enablement without function coverpoints | error |
| `branch-line-cross-testcase-perl-exemption` | `.pl` source permits a line hit in one testcase and zero branch hit in another | success |
| `branch-missing-line-cross-testcase` | Missing branch line recovery in one input retains that testcase’s generated DA | success |
| `branch-missing-line-consistency-disabled` | Missing branch line with consistency checks disabled | success |
| `branch-same-input-source-rescue` | Branch hit agrees with another testcase’s line hit within one input | success |
| `branch-same-input-missing-summary` | Multiple testcases keep synthesized branch DA in the summary only | success |
| `branch-global-line-hit-zero-branches` | Valid individual inputs become inconsistent after source-wide merging | error |
| `function-explicit-empty` / `function-rc-empty` | Explicit function coverage without function data | error |
| `function-empty-recovery` | Empty-function recovery emits zero summaries while retaining DA | success |
| `branch-explicit-empty` / `mcdc-explicit-empty` | Explicit branch/MC/DC coverage without those records | error |
| `branch-zero-unknown` | BRDA records with zero and unknown (`-`) counts beside a DA record retain their count forms | success |
| `branch-global-da-cross-testcase` | Source-wide branch aggregation for individually valid testcase inputs with different line and branch hit states | success |
| `checksum-conflict-ignored-branch-collector` | Ignore conflicting DA checksums while branch coverage activates the collector | success |
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
| `negative-count-recovery-disabled-consistency` | Recover negative count with consistency checks disabled | success |
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
| `orphan-function-auto-mismatch-only` | Default-auto FNDA handling recovers the mismatch, then fails because function coverage has no coverpoints | error |
| `orphan-function-hit-recovery` | Recover orphan FNDA with mismatch and empty handling | success |
| `branch-no-da-strict` / `branch-no-da-empty-recovery` | BRDA-only input has no valid records; strict mode reports corrupt/empty input, while empty recovery succeeds with a zero-byte trace | error / success |
| `mcdc-no-da-strict` / `mcdc-no-da-empty-recovery` | MC/DC-only input has no valid records; strict mode reports corrupt/empty input, while empty recovery succeeds with a zero-byte trace | error / success |
| `removed-no-da-shared-source` | A no-DA testcase sharing an input and source with a DA testcase is removed; its BRDA and MC/DC records do not appear in output | success |
| `branch-fn-synthesized-single` | Branch-derived DA is emitted for a single testcase and the function summary is FNF=1/FNH=1 | success |
| `branch-fn-synthesized-multiple` | Branch-derived DA contributes to the source master summary but is omitted from a multi-testcase line output; function summary remains FNF=1/FNH=1 | success |
| `modern-fna-line-mismatch-zero` | Strict consistency rejects FNA zero hits when its line is hit | error |
| `modern-fna-line-mismatch-positive` | Strict consistency rejects positive FNA hits when no contained line is hit | error |
| `modern-fna-overlap-end-range` | A hit in overlapping explicit FNL ranges conflicts with zero-hit function counts in both ranges | error |
| `modern-fna-line-mismatch-zero-recovery` | Recover zero-hit FNA/positive-line mismatch with `--ignore-errors inconsistent` | success |
| `modern-fna-line-mismatch-positive-recovery` | Recover positive-hit FNA/no-hit-line mismatch with `--ignore-errors inconsistent` | success |
| `modern-fna-line-mismatch-zero-consistency-disabled` | Skip zero-hit FNA/positive-line consistency check with `check_data_consistency=0` | success |
| `modern-fna-line-mismatch-positive-consistency-disabled` | Skip positive-hit FNA/no-hit-line consistency check with `check_data_consistency=0` | success |
| `modern-fna-aliases-line-match` | Alias group with zero and positive FNA hits and a hit line | success |
| `lambda-modern-fna-line-mismatch` | Lambda-name exception skips the FNA/line mismatch | success |
| `derive-endline-source-wide` | Derive ends within each input before another input adds later lines | success |
| `modern-fna-source-wide-consistency` | Reject an invalid FNL/FNA input before another input adds a hit | error |
| `fn-zero-line-hit-same-input-testcases` | FN/FNDA consistency in one testcase despite a later line hit in another testcase of the same input | error |
| `fn-hit-line-zero-same-input-testcases` | A hit function agrees with another testcase’s hit line in the same input | success |
| `fn-zero-line-hit-separate-inputs` | Merged source-wide line hits conflict with zero-hit functions from another input | error |
| `fn-explicit-start-beyond-last-da` | Explicit function starts after the last line record | error |
| `fn-explicit-no-da-empty` | Explicit function data without any line records under inconsistent and empty recovery | success |
| `fn-lambda-mixed-alias-not-exempt` | Ordinary alias prevents the C lambda group from receiving the lambda line exception | error |
| `fn-cpp-lambda-first-line-exempt` | C++ lambda first-line hit is exempt from zero-function mismatch | success |
| `fn-cpp-lambda-later-line-error` | Later hit inside a zero-hit C++ lambda range remains inconsistent | error |
| `fn-mangled-nested-not-lambda` | C++-style `_ZZ` mangled function name is an ordinary function | error |

The function cases above are required in both pinned backends. LCOV derives
eligible missing function ends and checks each input's source-wide summary
before merging inputs. That summary includes all testcases within the input:
a hit function in testcase `a` can agree with a hit line in testcase `b`, while
a zero-hit function becomes inconsistent if either testcase has a contained
hit line. An invalid input cannot be rescued by another input. LCOV also checks
the merged source-wide summary, so independently valid zero-hit input followed
by hit line data can produce a strict failure after aggregation.

The two-input end-line fixture emits `FNL:0,1,1` for testcase `a`; testcase `b`'s
`DA:5,1` in the other input does not extend the end inferred while reading the
first input. Explicit starts beyond the last `DA` are inconsistent. The
no-`DA` recovery fixture yields an empty output, whose exact bytes and successful
status are checked; genhtml cannot consume that empty worklist and is skipped
only for this named case. For explicit ends, both endpoints are included;
overlapping function ranges are checked independently.

Strict function/line mismatches exit 1. LCOV's default inconsistency recovery
can modify function counts: a zero-hit function with a hit line adds that
line's count to every alias. Line counts are retained. The reverse-direction
fixture retains its positive alias count in the serialized trace. With
`check_data_consistency=0`, counts remain unchanged. The harness supplies
`inconsistent` to genhtml only for these recovery or disabled-check cases and
still requires successful consumption and matching coverage statistics.
A C/C++ `{lambda(...)}` or Java `.lambda$` function is exempt for a declaration
line hit when all its aliases are lambdas; later-line hits still fail. Mixed
lambda/ordinary groups and `_ZZ` mangled names receive ordinary checks.
Alias groups are
checked as a whole: zero and positive FNA aliases together agree with a hit
line. The end-line conflict is an
observed agreement: XS and pure Perl both exit 1 in strict mode and succeed
with `--ignore-errors inconsistent`. The initial six added cases were run
against pinned source with verified XS and `LCOV_PURE_PERL=1`; both successful
outputs passed pinned genhtml. Their manifest is
`.cache/fna-sourcewide-characterization/manifest.json` in the oracle worktree.
Orphan hits under default-auto function handling need both `mismatch` and
`empty` recovery categories: mismatch-only recovery emits the unknown-function
warning, then errors that function coverage has no coverpoints. Explicit
`--function-coverage` with mismatch and empty recovery succeeds and emits the
valid DA record with zero function summaries.

The BRDA-only and MC/DC-only inputs reference the existing synthetic source but
contain no DA records. Both backends reject each strict input as corrupt because
it has no valid records. Empty recovery succeeds but produces exactly zero
trace bytes; genhtml is skipped only for these named cases after that byte
condition is asserted. The same empty-trace exception applies to
`fn-explicit-no-da-empty`.

In `removed-no-da-shared-source`, LCOV retains only testcase `keep` and its
`DA:1,1`; the removed testcase contributes neither branch nor MC/DC records.
The function synthesis fixtures include `DA:1,1` so the testcase is valid, but
line 2 has BRDA and no DA. For a single testcase LCOV emits synthesized
`DA:2,1`; with multiple testcases the source summary contains the branch
coverpoint while serialized testcase output omits `DA:2,1`. Both outputs report
`FNF:1` and `FNH:1` for the testcase containing the function. These runs use
`--ignore-errors inconsistent` because LCOV also reports the original
branch-without-line inconsistency.

## Bounded runtime gates

Run the large synthetic gates separately from the tiny differential matrix:

```sh
python3 tests/compat_bounded.py --binary /absolute/path/to/lcovmerge \
  --workdir .cache/compat-bounded
```

The 8 MiB setting is a byte budget. Each generated input is at least 24 MiB
(about 28 MiB by default), not 24 million records. The regular-file workloads
separately stress DA line volume, many distinct sparse modern function IDs,
legacy functions, one very large alias group, a large MC/DC group, and one huge
branch block with sparse ID 70000 and mixed exception/unreachable records.
A checksum workload puts over a million producers into one source-line group
and verifies its selected checksum, accumulated count, and source version.
An eighth case reuses the DA fixture through stdin and verifies every output
record again. Use `--case checksum-group` to run only that workload.
The validator streams the output against
independently generated expectations, checking every DA value and line,
function declaration, alias, hit, ID, and summary. LCOV 2.6 normalizes legacy
function input to FNL/FNA records; the validator accepts TN before SF and
checks FNF/FNH summaries when emitted. It also checks temporary directory
cleanup after success, samples peak temporary disk usage, and records peak
process RSS using per-process `wait4` high-water measurements on supported platforms.
Other platforms report RSS as unsupported instead of estimating it. The
default per-command timeout is 240 seconds and can be changed with
`--timeout`. Generated inputs, commands, logs, outputs, and `report.json` stay
in the requested ignored `--workdir`; failures are recorded rather than
skipped. The validator runs malformed-output self-checks for altered hits,
IDs, summaries, and truncation. These runs do not invoke LCOV or genhtml on
the large files; production runtime must be measured separately.

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

Fractional FNA counts truncate with an explicit FNL end line in both reference
backends. The suite checks both external function joins and the branch-enabled
collector, plus negative FNA recovery with consistency checking disabled.
