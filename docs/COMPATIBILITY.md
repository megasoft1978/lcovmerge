# Compatibility with LCOV 2.6

lcovmerge merges already-exported LCOV `.info` files. It is not a drop-in replacement for `lcov -a`, and stable output from lcovmerge does not mean byte-for-byte or semantic identity with LCOV. This page describes selected merge-policy differences against upstream LCOV 2.6. LCOV options and configuration can change how it handles inconsistent input; the examples below call out strict behavior and diagnostic recovery where relevant.

## What has been verified

- **Small project-derived comparisons:** On one macOS 27 arm64 host, eight project-derived captures from 0.051 MB to 6.091 MB and an 11.225 MB composite across 15 shards passed normalized comparisons of `SF`, `DA`, `FN`, `FNDA`, and `BRDA` records and counts against LCOV 2.6. `genhtml` accepted the outputs. The validation report records capture workarounds and upstream test exclusions. These checks cover small inputs, not large production workloads; see [the validation report](validation/real-projects.md).
- **CI differential checks:** A dedicated Ubuntu CI job installs lcov and checks that both tools add line-hit counts for a two-shard fixture. The test runner also has an optional lcov comparison for a generated 200-source fixture, including line, function, branch, and MC/DC summary counts. These are focused regression checks, not a complete conformance suite.
- **Three public CI datasets (rerun 2026-10-11):** On macOS 27.0 arm64, 66 shards totaling 657,966,211 bytes were rerun with lcovmerge 1.0.2 and LCOV 2.6-0. Strict LCOV 2.6 `lcov -a` failed 3/3; lcovmerge 1.0.2 completed 3/3, with 12 joined-boundary recovery warnings on one set. The original lcovmerge 1.0.1 run found the joined-boundary bug. The affected set's earlier 7 `DA`, 18 `FN`, and 18 `FNDA` differences are gone against diagnostic LCOV. The rerun compared normalized `SF` keys and keyed `DA`, `FN`, `FNDA`, and `BRDA` values/counts against diagnostic LCOV outputs. It confirmed the aggregate residuals below; the earlier investigation root-caused each remaining difference class to the documented policies in the table. The rerun did not make strict LCOV succeed, did not compare byte-for-byte, did not cover summary, testcase, extension, or other unlisted records, and did not newly isolate the causes. This is compatibility evidence, not proof of equivalence or a performance result.

The public CI results below contain aggregate record counts only, with no project names, source paths, or source code. Difference counts are summed across the three dataset comparisons; they are not deduplicated across datasets.

| Public CI sets | Inputs | Strict `lcov -a` and lcovmerge 1.0.2 | Aggregate difference in diagnostic `lcov --ignore-errors` comparison |
| --- | ---: | --- | --- |
| **Across all three sets** | **66 shards · 657,966,211 bytes** | **Strict LCOV failed 3/3; lcovmerge 1.0.2 completed 3/3, with 12 joined-boundary recovery warnings on one set** | **86 lcovmerge-only `SF`; 713 LCOV-only `DA`; 2 lcovmerge-only `FN`; 0 `FNDA` differences; 9,877 `BRDA` key/value differences** |

The diagnostic LCOV flags suppressed errors so output could be inspected; they did not turn the strict failures into passes. The earlier investigation associated the residual classes with LCOV's branch-only `DA` synthesis, empty `SF` handling, `BRDA` block-ID remapping, and duplicate legacy `FN` handling, as described in the policy table below. The rerun confirmed the normalized aggregate counts; it did not repeat causal isolation with synthetic fixtures. See [benchmark context](BENCHMARKS.md) for the separation between compatibility findings and performance measurements.

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
