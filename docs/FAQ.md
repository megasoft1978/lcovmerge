# FAQ

## Does lcovmerge collect coverage?

No. It merges LCOV tracefiles that another tool has already produced. Use gcov/lcov, grcov, Jest/Istanbul,
pytest-cov, or another collector to create `.info` files, then merge those files with lcovmerge.

## Does it replace lcov?

No. lcov includes capture, filtering, extraction, summaries, and report workflows. lcovmerge focuses on
merging existing LCOV tracefiles. Keep `genhtml` or another report tool after the merge. See the
[comparison](COMPARISON.md).

## Is the output identical to `lcov -a`?

No. lcovmerge writes canonicalized LCOV output and has documented differences in record ordering, summary
recalculation, checksum conflicts, function aliases, branches, MC/DC-shaped records, and unknown records.
The output is not guaranteed to be byte-identical to lcov's output. Keep `lcov -a` when you depend on lcov's
specific behavior, testcase semantics, configuration, or MC/DC handling. See
[limitations and intentional differences](LIMITATIONS.md).

## Is output deterministic?

With the same inputs, options, and build, lcovmerge emits byte-identical output regardless of input order or
the selected `-j` value. This makes it easier to compare and cache the output of repeatable CI runs.

## Is total memory capped by `--mem-limit`?

No. `--mem-limit` caps the record arena. Process overhead, parser and I/O buffers, merge bookkeeping, thread
stacks, and allocator state add to total RSS. External sorting also needs temporary disk space proportional
to input and intermediate runs; select a `--tmpdir` with enough free space.

## Can I merge CI shards?

Yes. Upload each shard's `.info` file as a CI artifact, then merge those artifacts in a final job. The
[integrations guide](INTEGRATIONS.md) includes GitHub Actions, GitLab, Bazel, and CMake examples. The
[reusable GitHub Action](../action/README.md) can download and verify the release binary for you.

## Can I send the result to genhtml, Codecov, Coveralls, or SonarQube?

lcovmerge writes an LCOV tracefile. Pass it to `genhtml`, Codecov, or a Coveralls integration that accepts
LCOV. SonarQube accepts LCOV for some analyzers, including JavaScript/TypeScript, Dart, and Rust. Its C/C++
analyzer uses gcov or llvm-cov reports, so an LCOV file is not a replacement for those inputs. See the
[uploader notes](INTEGRATIONS.md#coverage-report-consumers).

## Does `--jobs` parallelize one large input file?

No. `--jobs` controls concurrent external-sort run generation across input files. Already-sorted regular
files use a single-threaded stream merge, and one input file cannot be parsed by multiple workers. The merge
stage is single-threaded.

## How do I handle different checkout roots?

Pass one `--rebase OLD=NEW` mapping per old root. Use `--prefix-strip` when a common leading path should be
removed. Review the resulting `SF:` names before generating or publishing a report.

## When should I not use lcovmerge?

Keep your current tool when you need raw coverage collection, MC/DC semantic accounting, exact `lcov -a`
behavior, testcase-aware reporting, or a merge job that cannot spare temporary disk space. See
[all documented limitations](LIMITATIONS.md).

## What do the exit codes mean?

`0` means success, `1` means command-line usage error, `2` means input or LCOV format error, and `3` means an
I/O or allocation error. See [the CLI reference](USAGE.md#exit-status).

## How do I report a security issue?

Use [GitHub Security Advisories](https://github.com/megasoft1978/lcovmerge/security/advisories/new) for
suspected parser memory-safety bugs. See [SECURITY.md](../SECURITY.md).
