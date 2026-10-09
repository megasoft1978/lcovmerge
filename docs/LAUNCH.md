# Launch drafts

These drafts are not posted. Before publishing, confirm that the source repository, release assets, package
manifests, benchmark page, checksums, and links are public and match the current release. The Bazel and Mozilla
messages describe lcovmerge as an optional downstream tool; neither says it changes those projects' code or
would have fixed a reported incident.

## Bazel issue #26383 comment

> I came across this issue while working on lcovmerge, a C11 command-line tool for merging LCOV tracefiles
> after coverage collection. It streams records, caps the record arena, and spills sorting work to temporary
> files. It may be useful as an optional post-processing step when separate LCOV files need to be combined.
> It does not replace or change Bazel's `CoverageOutputGenerator`. Its own benchmarks use generated inputs;
> the workload in this issue was not measured. Source and benchmark details: [repository](https://github.com/megasoft1978/lcovmerge)
> · [Bazel issue](https://github.com/bazelbuild/bazel/issues/26383).

## Mozilla Bug 2070106 comment

> I saw this bug was resolved by increasing the coverage worker's memory. I maintain lcovmerge, a tool for
> merging existing LCOV files after collection. It does not replace grcov or process raw profile data, and I
> am not suggesting it would have changed the outcome here. If a separate LCOV merge stage is useful in
> another pipeline, the source and benchmark methodology are here: [repository](https://github.com/megasoft1978/lcovmerge)
> · [benchmark report](https://github.com/megasoft1978/lcovmerge/blob/main/docs/BENCHMARKS.md).

## Show HN

**Title:** Show HN: lcovmerge – a bounded-memory LCOV merger for sharded CI

**Text:**

I built lcovmerge for the final merge step in coverage pipelines: test shards already produce `.info` files,
but combining them can turn into a memory-heavy or slow CI job. It's a small C11 CLI that streams LCOV
records, spills sorting work to temporary files, and emits canonical output. With the same inputs and options,
the output is byte-identical regardless of input order or job count.

It fits monorepos and Bazel, CMake, or Gradle C++ builds when their coverage steps already emit LCOV. It writes
an `.info` file for tools such as `genhtml`, Codecov, and Coveralls; SonarQube support depends on the language
analyzer. It is only a merger: no raw-data collection, HTML reporting, or MC/DC semantic guarantee. The
benchmarks use generated inputs on one host, with workload-specific run counts and uncontrolled cache state;
the report includes failed and timed-out cases and marks unavailable data.

Source, install options, and benchmark methodology: [lcovmerge on GitHub](https://github.com/megasoft1978/lcovmerge).

## Short post

lcovmerge is a C11 CLI for merging already-generated LCOV tracefiles in sharded CI. It uses bounded record
memory, temporary-file sorting, and canonical output that is independent of input order and job count. It
does not collect raw coverage or replace lcov's broader workflow. The benchmark page documents the generated
fixtures, methodology, failed runs, and limitations:
[lcovmerge on GitHub](https://github.com/megasoft1978/lcovmerge).

## Short blurb

Merge LCOV shards with bounded record memory. lcovmerge writes canonical `.info` output for your existing
report or upload step. It is a focused merger, not a coverage collector; benchmarks and limits are published
with the source: [lcovmerge on GitHub](https://github.com/megasoft1978/lcovmerge).
