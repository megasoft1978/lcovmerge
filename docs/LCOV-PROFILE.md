# LCOV 2.6 merge profile

The opt-in Rust profile targets the supported tracefile aggregation workflow of
LCOV 2.6. Native lcovmerge keeps its established CLI and merge policies. Select
the profile explicitly; no executable named `lcov` is installed or replaced.

```sh
lcovmerge --lcov-compat=2.6 -a 'coverage/shard-*.info' -o coverage/merged.info
lcovmerge --lcov-compat=2.6 --help
```

Quoted patterns expand inside the profile. Repeated inputs contribute repeatedly.
Omitting the output writes to stdout; repeated output options select the last
destination. Function coverage defaults on, branch coverage and MC/DC default
off. Every enabled family requires corresponding coverpoints in the merged
source master unless `empty` errors are ignored. Default function coverage thus
rejects pure DA-only inputs. With `--ignore-errors empty`, or when another source
has valid functions, DA-only source/testcase output includes `FNF:0` and `FNH:0`.
The function check is global across inputs, including retained master functions
from removed testcases. `--no-function-coverage` disables both the requirement
and function summaries.
Testcase names are retained unless `--forget-test-names` is selected.

Inputs are regular files or one `-` for stdin. Stdin is a lcovmerge extension:
LCOV 2.6 treats `-a -` as a filename. The profile stages stdin to disk before
aggregation, then merges it at its original input position; this also supports
mixed file/stdin inputs. File parsing errors can therefore follow completion of
stdin staging. Catchable interruption removes the staged input.

## Supported command surface

| Setting | Profile spelling |
| --- | --- |
| Add tracefiles | Repeated `-a`, `--add-tracefile` |
| Output | `-o`, `--output-file`; `.gz` selects compressed output |
| Source selection | Repeated `--include`, `--exclude` |
| Coverage families | `--function-coverage`, `--branch-coverage`, `--mcdc-coverage`, and their `--no-` forms |
| Checksums | `--checksum`, `--no-checksum` |
| Testcase identity | `--forget-test-names` |
| Configuration | Repeated `--config-file` and `--rc key=value` |
| Diagnostics | `--quiet`, `--verbose`, `--ignore-errors`, `--keep-going` |
| Resources | `--tempdir`, `--parallel`, and lcovmerge's `--mem-limit` extension |

Explicit configuration files take precedence over discovery. Otherwise the first
readable file in `$HOME/.lcovrc`, then `$LCOV_HOME/etc/lcovrc`, is selected.
Supported file settings apply before `--rc` overrides and command-line coverage
controls. Configuration inclusion detects cycles and has a depth limit of 32.

The supported merge settings are `function_coverage`, `branch_coverage`,
`mcdc_coverage`, `checksum`, `forget_testcase_names`, `include`, `exclude`,
`ignore_errors`, `parallel`, `lcov_tmp_dir`, `check_data_consistency`, and
`derive_function_end_line`. Other merge-relevant settings
are rejected when they cannot be honored. Capture, reset, extract/remove,
report generation, callbacks, and general Perl substitutions are outside this
merge profile. `--memory` and `memory_percentage` do not map to the record
arena budget and are rejected. Run the native mode or upstream LCOV when an
unsupported operation or setting is needed.

## Count policy

Testcase names use LCOV's bytewise normalization: trailing ASCII whitespace is
removed, non-word bytes become `_`, and comma suffixes are discarded except for
the special `,diff` marker. A name remains in effect for following source sections
until another TN record appears. Normalized names group together before counts merge;
`--forget-test-names` then uses the unnamed testcase.

Counts saturate at LCOV's signed 64-bit maximum, and fractional counts truncate.
Rust preserves exact decimal integers. In the retained `DA:1,9007199254740993`
fixture, LCOV 2.6 XS emits `9007199254740992`, while pure Perl and Rust retain
`9007199254740993`. The profile follows the pure-Perl result for this ambiguity.
The harness records this reference disagreement explicitly; it is not an XS
conformance pass or a guarantee of XS equivalence for arbitrary wide counts.

Function consistency checks run on each input's source-wide summary, including
all its testcases, and on the merged summary. Eligible missing ends are inferred
before inputs merge. With default inconsistency recovery, a zero-hit function
can receive count additions from its first contained hit line; disabling the
checks preserves input counts. These policies are verified against both pinned
backends in the required oracle matrix.

## Validation and operational differences

The reference is [LCOV v2.6](https://github.com/linux-test-project/lcov/releases/tag/v2.6),
commit `a17443e7dbb31b5b7bff6beac32e7b9b02ec3342`. The conformance harness
records actual source/package hashes, runtime, configuration, and confirmed XS
and pure-Perl backends. Version text alone is insufficient. Its comparator keeps
testcase, source, function alias, branch, MC/DC, checksum, version, and summary
identity; consumer checks use the pinned `genhtml`.

Compatibility failures exit with status 1; native mode retains its existing
0/1/2/3 status contract. Named outputs are staged and replaced only on success.
That protection is an intentional operational guarantee; partial stdout cannot
be rolled back. Resource limits and catchable interruption cleanup apply to the
Rust executor. Unsupported metadata or operations produce explicit errors.

Before switching a pipeline, run both tools on identical artifacts and compare
the records and reports used by that pipeline. Conformance results cover the
tested contract, not every LCOV operation or producer. Native performance
measurements do not establish this profile's speed. See the
[implementation and validation plan](LCOV-MERGE-COMPATIBILITY-PLAN.md).

The [isolated Linux comparison](BENCHMARKS.md#isolated-linux-characterization-2026-10-14)
reports six synthetic workloads against XS and pure Perl, including slower cases,
uncertainty intervals, resource metrics and exact build/input provenance.

Development validation is still in progress. No stable drop-in or performance
claim is made before the implementation's release gates pass.

DA/function, branch, and MC/DC inputs use external sort runs. Branch blocks use streamed, exact signature ranking
and bounded joins; sparse block IDs do not allocate sparse arrays. Stdin, including
mixed file/stdin input, is staged to disk with fixed-size buffers and then uses the
same eligible executors as a regular file. Source versions use bounded producer-order
reduction and propagate across retained testcases. Eligible checksum inputs use a
source-line sort and disk-backed source index; even one checksum group larger
than memory is streamed. Branch-only sections without DA use a disk-backed
input/source/testcase eligibility index, preserving source-summary checks while
omitting removed testcases. Removed-testcase functions and MC/DC use the same
eligibility index and separate spill streams for private source-summary data.
MC/DC line synthesis counts positive reachable outcomes and projects the
source-wide count into retained testcases without duplicating the private count.
Regular inputs, including malformed sections recovered with `--ignore-errors format`,
stay on the bounded executor. It retains earlier sections when recovering
missing terminators and ignores misplaced TN records. Trailing ASCII whitespace
is removed before record dispatch. Oversized records retain the 1 MiB cap; recovery
discards the entire physical line using bounded buffers. The remaining semantic
audit and release validation are still in progress. The experimental profile should be
compared with upstream LCOV on the intended input before adopting it.
