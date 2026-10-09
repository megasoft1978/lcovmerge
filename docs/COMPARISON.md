# Comparison

lcovmerge is a focused merger for existing LCOV tracefiles. It is not a replacement for the capture,
conversion, or reporting features of the tools below.

| Tool | Primary purpose | Relevant behavior | When it may fit better |
| --- | --- | --- | --- |
| **lcov 2.x** | Collect and manipulate coverage, and support reports | `lcov --add-tracefile` accepts tracefile patterns and adds counts for matching test and filename combinations. Its suite includes branch coverage and MC/DC options, checksum/version checks, capture, filtering, summaries, and report tooling. | Use lcov when you need its full workflow, testcase-aware operations, MC/DC support, or compatibility with established lcov configuration. |
| **lcov-result-merger** | Merge LCOV files in Node.js pipelines | Its CLI accepts an input glob and output file and can normalize source paths relative to the merge working directory. | Use it where a small Node dependency and existing npm integration are preferred. |
| **grcov** | Collect, aggregate, and convert coverage data | Handles coverage data from multiple sources, including gcda/LLVM profiles and LCOV inputs, and can emit LCOV, HTML, Cobertura, and other formats. | Use it when the pipeline needs collection, source-format conversion, or multiple report formats rather than only joining LCOV files. |
| **Bazel CoverageOutputGenerator / LcovMerger** | Generate a combined report inside Bazel coverage workflows | It is integrated into Bazel's coverage action graph. The referenced issue reports high heap use for large LCOV inputs; the project benchmark did not measure Bazel's merger. | Use Bazel's built-in path when its coverage action and supported report format meet the need. Evaluate lcovmerge only as a separate post-processing step for LCOV files. |
| **lcovmerge** | Merge LCOV tracefiles with bounded record memory | Streams input, external-sorts records, and writes an LCOV tracefile. Supports path rewriting/filtering and selected line, function, and branch records; it does not implement MC/DC semantics. | Use it when existing `.info` files need a low-memory merge and no raw-data collection or report generation is required. |

## lcov 2.x behavior

The comparison is based on the [lcov v2.6 man page
source](https://github.com/linux-test-project/lcov/blob/v2.6/docs/man/lcov.rst) and the [tracefile format
documentation](https://github.com/linux-test-project/lcov/blob/v2.6/docs/man/geninfo.rst). In v2.6, `-a`
aggregates tracefiles by adding execution counts for matching testcase and filename combinations.
`--forget-test-names` can instead treat all input as one testcase, and the man page offers `--checksum`,
`--no-checksum`, `--branch-coverage`, and `--mcdc-coverage`. The lcov suite covers more than merging,
including capture and operations such as extract, remove, list, and summary.

lcovmerge is not claimed to be semantically interchangeable with all lcov operations. It does not implement
MC/DC semantics and should not be used where testcase-level reporting, lcov version checks, or lcov-specific
configuration behavior is required.

## Benchmark interpretation

The [benchmark report](BENCHMARKS.md) compares lcovmerge against `lcov -a`, `lcov-result-merger`, and grcov on
a defined set of tracefiles. It does not benchmark Bazel's `CoverageOutputGenerator`. grcov used smaller
no-checksum inputs for two datasets; its PATH-HEAVY output was invalid; one lcov-result-merger output was
partial on a small fixture. See the raw table and caveats before comparing values.

## Sources

- [lcov v2.6 `lcov` man page source](https://github.com/linux-test-project/lcov/blob/v2.6/docs/man/lcov.rst)
- [lcov v2.6 `geninfo` tracefile format
source](https://github.com/linux-test-project/lcov/blob/v2.6/docs/man/geninfo.rst)
- [lcov-result-merger package](https://github.com/mweibel/lcov-result-merger)
- [grcov project documentation](https://github.com/mozilla/grcov)
- [Bazel coverage guide](https://docs.bazel.build/versions/main/coverage.html)
- [Bazel issue #26383](https://github.com/bazelbuild/bazel/issues/26383)
- [Mozilla Bug 2070106](https://bugzilla.mozilla.org/show_bug.cgi?id=2070106)
