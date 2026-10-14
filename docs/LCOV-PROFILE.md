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
off. Testcase names are retained unless `--forget-test-names` is selected.

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

Counts saturate at LCOV's signed 64-bit maximum, and fractional counts truncate.
Rust preserves exact decimal integers. In the retained `DA:1,9007199254740993`
fixture, LCOV 2.6 XS emits `9007199254740992`, while pure Perl and Rust retain
`9007199254740993`. The profile follows the pure-Perl result for this ambiguity.
The harness records this reference disagreement explicitly; it is not an XS
conformance pass or a guarantee of XS equivalence for arbitrary wide counts.

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

Development validation is still in progress. No stable drop-in or performance
claim is made before the implementation's release gates pass.

DA/function inputs and MC/DC sections containing DA records use external sort
runs. A sole stdin input is staged to disk with fixed-size buffers and then uses the
same eligible executors as a regular file. Branch-bearing inputs, checksums,
version metadata, mixed stdin/file inputs, and MC/DC-only sections still use the
collector; large working sets can exceed its record budget. Completing those bounded paths and strict function/line consistency
checks remains part of the release work. The experimental profile should be
compared with upstream LCOV on the intended input before adopting it.
