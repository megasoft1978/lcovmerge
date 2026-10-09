# Launch drafts

These drafts are not posted. Publish them only after the source repository, release assets, checksum file, and
benchmark page are public and the links resolve. The Bazel and Mozilla messages offer lcovmerge as an optional
downstream tool; neither claims that it changes those projects' implementations.

## Bazel issue #26383 comment

> I came across this issue while working on a small C11 tool called lcovmerge. It merges already-generated
> LCOV tracefiles using bounded record memory and temporary-file sorting, so it may be worth evaluating as an
> optional post-processing step when LCOV files are available. It does not replace Bazel's
> `CoverageOutputGenerator` or fix its internal memory use. The benchmark page documents the synthetic inputs,
> one-host methodology, and limitations; the Bazel merger itself was not benchmarked. If useful, the source
> and release are here: [repository](https://github.com/megasoft1978/lcovmerge) ·
> [benchmarks](https://github.com/megasoft1978/lcovmerge/blob/main/docs/BENCHMARKS.md).

## Mozilla Bug 2070106 comment

> I saw that this bug was resolved by increasing the coverage worker's memory. I maintain lcovmerge, a small
> tool for merging LCOV files after collection has completed. It does not replace grcov or process raw profile
> data, and I am not suggesting it would have fixed this worker issue. If it is useful to compare a file-only
> aggregation step on another pipeline, the benchmark page lists the synthetic fixtures and caveats:
> [repository](https://github.com/megasoft1978/lcovmerge) ·
> [benchmarks](https://github.com/megasoft1978/lcovmerge/blob/main/docs/BENCHMARKS.md).

## Show HN

**Title:** Show HN: lcovmerge – a bounded-memory LCOV tracefile merger in C

**Text:**

I built lcovmerge for pipelines where combining LCOV files consumes too much time or memory. It is a small C11
CLI that streams tracefiles, spills sorting work to temporary files, and writes a normal `.info` file for
existing tools such as `genhtml`.

It only merges existing LCOV data; it does not collect coverage, create HTML reports, or implement MC/DC
semantics. The performance comparison uses generated inputs measured on macOS; run counts are recorded per
workload and the operating-system cache was not controlled. Linux performance was not measured, and Windows
runtime verification is pending a passing Windows CI run. No project-derived REAL capture was available. The
benchmark page includes errored cases and marks unavailable inputs.

Source and usage: [repository](https://github.com/megasoft1978/lcovmerge)

## Short r/programming-style post

I wrote lcovmerge, a C11 command-line merger for large LCOV tracefiles. It uses bounded record memory and
temporary-file sorting, then emits an ordinary `.info` file that existing report tools can read. It is not a
coverage collector or HTML reporter, and it does not implement MC/DC semantics. The benchmark page documents
macOS measurements on generated inputs, with workload-specific run counts and uncontrolled cache state, and
includes errored or unavailable cases:
[benchmark page](https://github.com/megasoft1978/lcovmerge/blob/main/docs/BENCHMARKS.md)

## Tweet-length blurb

lcovmerge is a small C11 CLI for merging LCOV tracefiles with bounded record memory. It writes standard
`.info` output; it does not collect coverage or implement MC/DC. Benchmarks are macOS measurements on generated
inputs with workload-specific run counts. Cache state was uncontrolled and REAL was unavailable:
[https://github.com/megasoft1978/lcovmerge](https://github.com/megasoft1978/lcovmerge)
