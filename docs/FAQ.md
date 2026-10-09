# FAQ

## Does lcovmerge collect coverage?

No. It combines LCOV tracefiles that another tool has already created. Use gcov/lcov, grcov, Jest/Istanbul,
pytest-cov, or another collector to produce `.info` files, then merge them with lcovmerge.

## Does it replace lcov?

No. lcov includes capture, tracefile operations, summaries, filtering, and related report tooling. lcovmerge
focuses on bounded-memory merging of existing LCOV files. Use `genhtml` or another report tool after merging.
The [comparison](COMPARISON.md) explains the scope difference.

## Does it support branch coverage?

It can preserve and merge LCOV `BRDA` rows when branch coverage is enabled. The CLI option is
`--branch-coverage on|off`. It does not implement MC/DC semantics.

## What happens to function data?

Function rows are merged by the tracefile merger. Use `--no-function-data` when you want line and optional
branch data without function records. LCOV 2.x enhanced function records have fixture-specific compatibility
limits; see [benchmarks](BENCHMARKS.md).

## Can I merge several CI shards?

Yes. Upload each shard's `.info` file as a workflow artifact, download all of them in one aggregation job, and
run `lcovmerge artifacts/**/*.info -o merged.info`. See [integrations](INTEGRATIONS.md).

## How do I handle different checkout roots?

Use one `--rebase OLD=NEW` mapping per old root. If the paths share a prefix that should be removed, use
`--prefix-strip`. Review the resulting `SF:` names before publishing the merged file.

## Is memory strictly bounded by `--mem-limit`?

The limit applies to the record arena. Process overhead, buffers, and temporary sort files add to total
resource use. Ensure `--tmpdir` has enough free disk space for the spill files.

## Does `--jobs` run multiple workers?

`--jobs` controls concurrent external-sort run generation and accepts 1 through 32. For regular files whose
rewritten records are already in canonical order, lcovmerge uses the single-threaded stream merge and ignores
the job count. Out-of-order inputs and unsupported stream types use the external-sort workers.

## What do exit codes mean?

`0` means success, `1` means command-line usage error, `2` means input or LCOV format error, and `3` means an
I/O error. See [USAGE](USAGE.md#exit-status).

## How do I report a security issue?

Use [GitHub Security Advisories](https://github.com/megasoft1978/lcovmerge/security/advisories/new) for
suspected parser memory-safety bugs. See [SECURITY.md](../SECURITY.md).
