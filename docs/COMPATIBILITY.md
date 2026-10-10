# Compatibility with LCOV 2.6

lcovmerge merges already-exported LCOV `.info` files. It is not a drop-in replacement for `lcov -a`, and stable output from lcovmerge does not mean byte-for-byte or semantic identity with LCOV. This page describes selected merge-policy differences against upstream LCOV 2.6. LCOV options and configuration can change how it handles inconsistent input; the examples below call out strict behavior and diagnostic recovery where relevant.

## What has been verified

- **Small project-derived comparisons:** On one macOS 27 arm64 host, eight project-derived captures from 0.051 MB to 6.091 MB and an 11.225 MB composite across 15 shards passed normalized comparisons of `SF`, `DA`, `FN`, `FNDA`, and `BRDA` records and counts against LCOV 2.6. `genhtml` accepted the outputs. The validation report records capture workarounds and upstream test exclusions. These checks cover small inputs, not large production workloads; see [the validation report](validation/real-projects.md).
- **CI differential checks:** A dedicated Ubuntu CI job installs lcov and checks that both tools add line-hit counts for a two-shard fixture. The test runner also has an optional lcov comparison for a generated 200-source fixture, including line, function, branch, and MC/DC summary counts. These are focused regression checks, not a complete conformance suite.
- **Three public CI datasets:** In a 2026-10-10 investigation on macOS 27.0 arm64 with lcovmerge 1.0.1 and LCOV 2.6-0, 66 shards totaling 657,966,211 bytes were compared. Strict `lcov -a` failed on all three datasets. lcovmerge completed all three. To compare normalized records, LCOV had to be rerun with `--ignore-errors`; those diagnostic outputs differed from lcovmerge on all three datasets. This is a compatibility investigation, not a successful equivalence check or performance result. One joined-record boundary issue found in that run was fixed in lcovmerge 1.0.2; the dataset comparison has not been rerun against 1.0.2.

The public CI results below contain aggregate record counts only. They do not include source paths or source code. Difference counts are summed across the three dataset comparisons; they are not deduplicated across datasets.

| Public CI dataset | Inputs | Strict `lcov -a` result | Difference in diagnostic `lcov --ignore-errors` comparison |
| --- | ---: | --- | --- |
| Vector | 43 shards · 643,761,091 bytes | Failed on an FN-format error; the input also contained 12 joined record boundaries | 7 `DA`, 18 `FN`, and 18 `FNDA` key differences; total line-hit count matched |
| Alioth | 5 shards · 5,842,683 bytes | Failed on branch data without corresponding line data | 8 `DA` key differences; LCOV's total line-hit count was 20 higher |
| BotFramework-WebChat | 18 shards · 8,362,437 bytes | Failed on conflicting duplicate function metadata | 86 additional `SF` entries in lcovmerge; 9,877 `BRDA`, 705 `DA`, and 2 `FN` key differences |
| **Across the three comparisons** | **66 shards · 657,966,211 bytes** | **Failed 3/3; lcovmerge 1.0.1 completed 3/3** | **86 additional `SF`; 9,877 `BRDA`, 720 `DA`, 20 `FN`, and 18 `FNDA` key differences** |

The diagnostic LCOV flags suppressed errors so output could be inspected; they did not turn the strict failures into passes. The observed differences are not all explained by the policy table below. See [benchmark context](BENCHMARKS.md) for the separation between compatibility findings and performance measurements.

## Policy differences

| Case | What LCOV 2.6 does | What lcovmerge does | Which to prefer when |
| --- | --- | --- | --- |
| Branch-only line without `DA` | Strict `lcov -a` reports inconsistent data and stops by default. With `--ignore-errors inconsistent`, it can add a `DA` count based on nonzero branch coverpoints. | Keeps the branch rows and does not invent a line-coverage row. | Prefer LCOV's recovery only when you want its repaired line counts. Prefer lcovmerge when line coverage should reflect only supplied `DA` records. Otherwise fix the capture at its source. |
| Empty `SF` section | Drops a source section that has no coverage records while adding tracefiles. | Retains the source section with zeroed summaries. | Prefer LCOV when matching its source-file set matters. Prefer lcovmerge when an explicitly listed but empty source must remain present. |
| Branch block IDs | Re-derives contiguous block IDs and matches blocks by branch signatures and order; rows with different input IDs may be combined. | Uses the literal line, block, and branch identifiers as keys, so different block IDs stay separate. | Prefer LCOV when its signature-based alignment matches your producer's intent. Prefer lcovmerge when literal IDs must remain distinct, especially across different source or instrumentation versions where inferred matches may be ambiguous. |
| Duplicate legacy `FN` names at different start lines | Reports inconsistent function data and collapses the name to one entry at the smallest start line. | Retains distinct start-line/name `FN` keys; `FNDA` counts are merged by function name. | Prefer LCOV when its one-entry normalization is the expected report policy. Prefer lcovmerge when both locations must remain visible, and verify the name-based `FNDA` totals for your inputs. |
| Joined `end_of_record` boundary | Treats `end_of_record` joined directly to the next `SF:` or `KF:` row as malformed input. | Since 1.0.2, recognizes this boundary, starts the next source section, and emits a warning. | Prefer strict LCOV validation when malformed input should fail. Use lcovmerge's recovery only when accepting this specific missing-newline repair is appropriate; fixing the producer is preferable. |
| Conflicting line checksums | Reports a checksum mismatch and stops by default. With `--ignore-errors mismatch`, the later merged checksum replaces the earlier one. | Warns and keeps the lexicographically smallest non-empty checksum, independent of input order. `--strict-checksum` exits with status 2 on conflict. | Prefer either tool's strict mode when checksum disagreement must stop the merge. Prefer lcovmerge's default only when deterministic conflict resolution is intended and the selected checksum is reviewed against the source version. |
| MC/DC rows | Merges compatible groups by line, group size, and expression index. Incompatible expression layouts are reported as inconsistent; differing reachability markers are also reported. | Adds counts by line, group size, sense, expression index, and reachability marker. On expression-text conflicts it warns and keeps the lexicographically smaller text; reachable and `U` rows remain separate. | Prefer LCOV when its MC/DC compatibility checks and report accounting are required. Use lcovmerge only when its explicit key and conflict rules suit your inputs; it does not promise LCOV-equivalent MC/DC counts. |

These descriptions follow the [LCOV 2.6 tracefile documentation](https://github.com/linux-test-project/lcov/blob/v2.6/docs/man/geninfo.rst) and [LCOV 2.6 merge implementation](https://github.com/linux-test-project/lcov/blob/v2.6/lib/lcovutil.pm). Other settings and record families can also affect reports.

## Run your own comparison

For a repeatable generated-fixture comparison, build lcovmerge and run the S workload:

```sh
make
mkdir -p .cache
TMPDIR="$PWD/.cache" bench/reproduce.sh S .cache/reproduce-S
```

Run M by replacing `S` with `M` and changing the output directory. The script compares normalized `SF`, `DA`, `FN`, `FNDA`, and `BRDA` records when lcov is installed; it does not test every record family or every LCOV option. See the [`bench/reproduce.sh` source](../bench/reproduce.sh) and [reproduction instructions](REPRODUCE.md) for requirements and report details.

For your own `.info` inputs, run both mergers on the same files and keep their outputs separate. The [same-input comparison and migration checklist](MIGRATING-FROM-LCOV.md#same-input-compare-command) shows the LCOV 2.6 command form. Compare normalized records and the reports from your actual consumer, including checksum, testcase, alias, MC/DC, and extension-record behavior that matters to your workflow. A passing normalized comparison is evidence for the fields it checks, not proof of full compatibility.
