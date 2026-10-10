# FAQ

## What does lcovmerge do?

It merges already-exported LCOV `.info` files into one file. It does not collect raw coverage or generate reports.

## When should I use lcovmerge?

Use it when separate test jobs have produced LCOV files and a later step needs one file. If `genhtml` or your uploader accepts the files directly, you may not need a separate merge. See [CI and exporter recipes](RECIPES.md).

## Does it replace `lcov -a`?

No. lcovmerge is not a drop-in replacement. Check your own output before switching; records, checksums, testcase/configuration data, branches, function aliases, MC/DC coverage records, unknown rows, summaries, and ordering can differ. See [migration checks](MIGRATING-FROM-LCOV.md) and [record policies](LIMITATIONS.md).

## Does it accept raw coverage data?

No. Export raw profiles or execution data to LCOV first, then merge the resulting `.info` files. lcovmerge does not capture coverage or render reports.

## What does `--mem-limit` limit?

It limits memory reserved for coverage records while sorting. Total process memory can be higher, and sorting needs temporary disk space. `--tmpdir` selects an existing directory for temporary files. See [operational limits](LIMITATIONS.md#input-and-operational-bounds).

## Is output deterministic?

With the same inputs, options, and build, lcovmerge writes stable bytes regardless of input order or worker setting. This does not mean its bytes or record behavior match `lcov -a`.

## What if input paths differ or collide?

`--rebase` and `--prefix-strip` rewrite leading `SF:` path prefixes in that order; matching uses path boundaries. Check the rewritten `SF:` paths before reporting. Two unrelated files with the same rewritten path are merged, so keep package-qualified paths in monorepos. See [path options](USAGE.md#--rebase-oldnew) and the [JavaScript recipe](RECIPES.md#javascript-with-c8-or-nyc).

## Does `--jobs` parallelize one large input file?

No. Workers process whole input files; one large file is not split among them. The merge stage itself uses one worker.

## Is Windows runtime behavior verified?

Windows runtime verification is pending a passing Windows CI run. A cross-build or release archive alone does not establish runtime behavior; see [platform status](LIMITATIONS.md#verification-status).

## What do the exit codes mean?

`0` means success, `1` a command-line error, `2` an input or format error, and `3` an I/O, allocation, temporary-file, or interruption error. See the [CLI reference](USAGE.md#exit-status).

## How do I report a security issue?

Use [GitHub Security Advisories](https://github.com/megasoft1978/lcovmerge/security/advisories/new) for suspected parser memory-safety bugs. See [SECURITY.md](../SECURITY.md).
