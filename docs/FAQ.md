# FAQ

## What does lcovmerge do?

It merges existing LCOV `.info` tracefiles into one canonical tracefile. It does not collect raw coverage or
render a report. See [where it fits](RECIPES.md).

## Should I merge the files before running genhtml?

Only if you need a single tracefile artifact or a downstream consumer expects one path. `genhtml` can consume
multiple tracefiles; check whether your current report command already handles the shards you have.

## Is lcovmerge a drop-in replacement for lcov -a?

No. It has a narrower job and intentional differences in record ordering, summaries, checksums, function
aliases, branches, MC/DC-shaped rows, and unknown records. Read the [migration guide](MIGRATING-FROM-LCOV.md)
and [limitations](LIMITATIONS.md). Keep `lcov -a` when you depend on its behavior.

## Is the output identical to lcov -a?

No. lcovmerge canonicalizes records and recomputes summary rows. It does not promise byte-identical output or
identical semantics for every tracefile. The migration guide explains how to compare your own records and
reports before switching.

## Does it collect raw coverage?

No. It accepts already-exported LCOV tracefiles. Use the collector or native raw-profile merge for `.gcda`,
`.profraw`, nyc JSON, Coverage.py data, Go profiles, or JaCoCo execution data, then use lcovmerge only if
multiple LCOV files need to become one. See the [export recipes](RECIPES.md#export-to-lcov-then-merge).

## Is total memory capped by --mem-limit?

No. `--mem-limit` is a record-arena budget for external-sort workers. Parser and I/O buffers, merge bookkeeping,
threads, allocator state, and the C runtime contribute to process RSS. External sorting also uses temporary
disk space for runs and intermediate passes. Choose a suitable temporary directory and leave disk headroom.

## Is output deterministic?

With the same inputs, options, and build, lcovmerge emits byte-identical output regardless of input order or
the selected `-j` value. This is a lcovmerge determinism guarantee; it does not mean output bytes match lcov.

## Can I merge CI shards?

Yes. Have each test job export a separate `.info` artifact, download those files into one final job, then run
lcovmerge. The [recipes](RECIPES.md) include GitHub Actions, GitLab, Jenkins, CMake, and Bazel examples. The
[reusable GitHub Action](../action/README.md) downloads a release binary and verifies its checksum.

## Can I send the result to genhtml, Codecov, Coveralls, or SonarQube?

lcovmerge writes an LCOV tracefile. Pass it to `genhtml` or to a consumer configured to accept LCOV. Some
uploaders also accept separate files, so a merged file is only needed when your pipeline requires one. For
SonarQube, supported formats depend on the language analyzer; its C/C++ analyzer expects gcov or llvm-cov
reports rather than LCOV. See [consumer notes](INTEGRATIONS.md#coverage-report-consumers).

## What if shard source paths differ?

Use `--rebase OLD=NEW` for a leading build-root mapping or `--prefix-strip PREFIX` to remove a common leading
path. Review the rewritten `SF:` paths before publishing a report. See [path options](USAGE.md#--rebase-oldnew).

## Does --jobs parallelize one large input file?

No. `--jobs` controls external-sort run generation across input files. The direct stream path is single-threaded,
and a single input file is not split between workers.

## Is Windows runtime behavior verified?

The repository has a Windows implementation and release target, but runtime verification remains pending a
passing Windows CI run. Cross-build evidence is not the same as runtime evidence; see
[action/README.md](../action/README.md).

## What do the exit codes mean?

`0` means success, `1` means a command-line usage error, `2` means an input or tracefile format error, and
`3` means an I/O, temporary-file, allocation, or interruption error. See the [CLI reference](USAGE.md#exit-status).

## How do I report a security issue?

Use [GitHub Security Advisories](https://github.com/megasoft1978/lcovmerge/security/advisories/new) for
suspected parser memory-safety bugs. See [SECURITY.md](../SECURITY.md).
