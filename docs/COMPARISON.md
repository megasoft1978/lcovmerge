# How lcovmerge compares

lcovmerge is for one stage of a coverage pipeline: combining LCOV tracefiles that another tool has already
created. It is a fit when the merge itself is using too much memory or time and the pipeline can accept
lcovmerge's documented LCOV semantics.

| Tool | Best fit | What to keep in mind |
| --- | --- | --- |
| **lcovmerge** | Merge existing `.info` files in a memory-constrained CI job. | Streams records, spills sorting work to disk, and writes canonical LCOV output. It does not collect raw data, generate reports, or implement MC/DC semantics. |
| **lcov** | Capture, filter, summarize, and report coverage across established lcov workflows. | `lcov -a` also merges tracefiles. Keep it when you rely on its testcase, checksum, configuration, or MC/DC behavior. |
| **lcov-result-merger** | Merge LCOV files in a Node.js workflow. | A small Node dependency can be convenient for existing JavaScript pipelines. Compare its behavior and resource use on your own tracefiles before changing a critical merge job. |
| **grcov** | Collect coverage from supported compiler outputs, convert formats, and generate reports. | It covers a broader collection and conversion workflow than a file-only merger. Keep it when that broader role is needed. |
| **Bazel CoverageOutputGenerator / LcovMerger** | Produce and combine coverage reports inside Bazel's coverage action graph. | It is integrated with Bazel. A separate lcovmerge step applies only when LCOV files are available and an additional file merge is useful; it does not replace Bazel's generator or fix its internals. |

## Who may benefit

The merge job can become a bottleneck in monorepos and CI pipelines that combine many test-shard reports.
Bazel, CMake, and Gradle C++ teams can use lcovmerge when their test or coverage jobs already produce LCOV
tracefiles. If Bazel already creates the single report the pipeline needs, there may be no additional merge
step to replace.

The [Bazel issue #26383](https://github.com/bazelbuild/bazel/issues/26383) describes high heap use in
`CoverageOutputGenerator` for a reported large-input workload. That report is useful context; the issue's
workload was not measured in this project's benchmark.

## Compatibility with `lcov -a`

Both tools combine LCOV tracefiles, but they are not guaranteed to produce byte-identical output or behave the
same for every record family and configuration. lcovmerge canonicalizes record order, recomputes summary rows,
and documents specific rules for checksums, function aliases, branches, MC/DC-shaped records, and unknown
records. See [limitations and intentional differences](LIMITATIONS.md) before switching a workflow that
depends on exact lcov behavior.

Use lcov when you need its broader capture and tracefile operations, testcase-aware reporting, lcov-specific
configuration, or MC/DC semantics. Use lcovmerge when an existing `.info` merge is the task and its output
semantics fit the downstream report or uploader.

## Benchmark interpretation

The project benchmark data records generated inputs and the tool versions, host, run counts, output status,
and measurement caveats. A failed or timed-out run is not a successful speed comparison. `REAL` is not measured
when no project-derived coverage capture is available. Read the [full benchmark report](BENCHMARKS.md) and
inspect the canonical [`data/benchmarks.json`](../data/benchmarks.json) before applying a result to your own
workload.

The Bazel issue and the Mozilla worker report are external incidents, not reproductions or benchmark results
for lcovmerge. See [Mozilla Bug 2070106](https://bugzilla.mozilla.org/show_bug.cgi?id=2070106) for that
project's report and resolution.

## Sources

- [lcov v2.6 `lcov` manual](https://github.com/linux-test-project/lcov/blob/v2.6/docs/man/lcov.rst)
- [lcov v2.6 tracefile format](https://github.com/linux-test-project/lcov/blob/v2.6/docs/man/geninfo.rst)
- [lcov-result-merger package](https://github.com/mweibel/lcov-result-merger)
- [grcov documentation](https://github.com/mozilla/grcov)
- [Bazel coverage guide](https://bazel.build/configure/coverage)
- [Bazel issue #26383](https://github.com/bazelbuild/bazel/issues/26383)
- [Mozilla Bug 2070106](https://bugzilla.mozilla.org/show_bug.cgi?id=2070106)
