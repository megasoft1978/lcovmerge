# FAQ

## What does lcovmerge do?

It merges already-exported LCOV `.info` files into one file. An `.info` file is plain text that records covered source lines, functions, and branches. lcovmerge does not collect raw coverage or generate reports.

## When should I use lcovmerge?

### Should I use this?

Use lcovmerge when separate test jobs produce LCOV files and a later local report or upload step requires one file. Skip it when your consumer accepts all shards, a native LLVM/Go/Python/JavaScript merge fits your source data, hosted Codecov or Coveralls combining is enough, or `lcov -a` is already fast on your files. Raw profiles must first be exported to LCOV. See the [matrix recipe](RECIPES.md#github-actions) for the artifact handoff.

## What if strict lcov failed on my inputs?

A strict LCOV failure does not show that another merger's output is correct or compatible. In a separate investigation, strict LCOV 2.6 failed on all three tested public CI datasets. lcovmerge merged each set, but normalized records differed from LCOV's diagnostic `--ignore-errors` output on every set; the investigation did not establish that every difference follows a documented policy. One real lcovmerge bug found during that work was fixed in v1.0.2. Keep your current merge as the report path while you compare the same inputs and downstream reports. See the [migration pilot](MIGRATING-FROM-LCOV.md#reversible-one-week-pilot) and [record policies](LIMITATIONS.md).

## Does it replace `lcov -a`?

No. lcovmerge is not a drop-in replacement. Records, checksums, testcase/configuration data, branches, function aliases, MC/DC coverage records, unknown rows, summaries, and ordering can differ. Review your own output before switching; see [migration checks](MIGRATING-FROM-LCOV.md).

## Does it accept raw coverage data?

No. Export raw profiles or execution data to LCOV first, then merge the resulting `.info` files. lcovmerge does not capture coverage or render reports.

## What does `--mem-limit` limit?

It limits memory reserved for coverage records while sorting. Total process memory can be higher, and sorting needs temporary disk space. `--tmpdir` selects an existing directory for temporary files. See [operational limits](LIMITATIONS.md#input-and-operational-bounds).

## Is output deterministic?

With the same inputs, options, and build, lcovmerge writes stable bytes regardless of input order or worker setting. This does not mean its bytes or record behavior match `lcov -a`.

## What if input paths differ or collide?

`--rebase` and `--prefix-strip` rewrite leading SF: path prefixes in that order; matching uses path boundaries. Check the rewritten SF: paths before reporting. Two unrelated files with the same rewritten path are merged, so keep package-qualified paths in monorepos. See [path options](USAGE.md#--rebase-oldnew) and the [JavaScript recipe](RECIPES.md#javascript-with-c8-or-nyc).

## Does `--jobs` parallelize one large input file?

No. Workers process whole input files; one large file is not split among them. The merge stage itself uses one worker.

## Is Windows runtime behavior verified?

Windows runtime verification is pending a passing Windows CI run. A cross-build or release archive alone does not establish runtime behavior; see [platform status](LIMITATIONS.md#verification-status).

## What do the exit codes mean?

0 means success, 1 a command-line error, 2 an input or format error, and 3 an I/O, allocation, temporary-file, or interruption error. See the [CLI reference](USAGE.md#exit-status).

## How do I report a security issue?

Use [GitHub Security Advisories](https://github.com/megasoft1978/lcovmerge/security/advisories/new) for suspected parser memory-safety bugs. See [SECURITY.md](../SECURITY.md).
