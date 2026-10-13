# Is lcovmerge right for this pipeline?

lcovmerge combines existing LCOV `.info` files. It belongs after coverage has been exported and before a local report or upload step. It does not collect coverage, merge native profiles, or generate reports.

## Decide in a minute

1. **Do you have multiple exported LCOV files?** If not, keep using the collector or exporter that produces your current report.
2. **Can the next service accept each file?** Codecov and Coveralls document combining parallel uploads. If that fits your workflow, a local merge may add no value.
3. **Are the original profiles available?** Prefer the collector's native merge when it preserves the information your reports need. For example, merge Clang `.profraw` files with `llvm-profdata` before exporting LCOV.
4. **Is the local merge a measured cost?** If it is quick and uses acceptable memory, keep the simpler step you already have.
5. **Does a downstream tool require one LCOV file, and is the current merge costly?** lcovmerge is a candidate. Run it alongside your current command and compare before switching.

```text
coverage collector / exporter
              │
              ▼
       LCOV .info shards
              │
              ├── consumer accepts shards ──► upload each shard
              │
              ├── native profile merge fits ─► merge profiles, then export
              │
              └── one LCOV file required and local merge is costly
                         │
                         ▼
                    lcovmerge
                         │
                         ▼
             existing report or uploader
```

## Fit by workflow

| Workflow | Recommended path | Where lcovmerge fits |
| --- | --- | --- |
| C/C++ with gcov and LCOV | Capture each shard with `lcov`; use `genhtml` or the existing uploader afterward. | Candidate for a costly final merge of downloaded `.info` files. For small inputs, `lcov -a` may be simpler. |
| Clang source-based coverage | Merge raw `.profraw` profiles with `llvm-profdata`, then use `llvm-cov`. | Only when independent LCOV exports are already the handoff and a single file is needed. |
| Rust with `cargo-llvm-cov` or grcov | Use the tool's native profile handling and export LCOV. | Candidate after export when independent jobs leave separate `.info` files. |
| Python with Coverage.py | Use `coverage combine` on raw data when the shard data is available; then export LCOV. | Candidate when only exported LCOV shards remain and one file is required. |
| JavaScript with nyc | Prefer `nyc merge` while raw coverage JSON is available. | Candidate after LCOV export when a local consumer requires one file. |
| Go, Java, or other producers | Use the producer or converter to create LCOV first. | Candidate only at the LCOV handoff; behavior depends on the converter's record conventions. |
| Bazel | Prefer Bazel's combined LCOV report for a single `bazel coverage` run. | Candidate when separate CI jobs provide independent LCOV artifacts that need a final local merge. |
| Codecov or Coveralls | Upload shards and configure parallel-build behavior as documented by the service. | Usually unnecessary if service-side aggregation meets the reporting need. |

These are workflow choices, not claims that lcovmerge has been tested against every version or configuration. See [CI and exporter recipes](RECIPES.md) for commands and the [exporter integration checks](validation/exporter-integrations.md) for the exact versions and synthetic fixtures verified so far.

## Safe evaluation

Before switching, use the same downloaded inputs with the current merger and lcovmerge. Record input bytes, shard count, host, versions, run count, warm/cold conditions, elapsed time, peak resident memory, and each command's status. Then compare normalized records that your consumer uses and run both outputs through the same report or upload check.

Keep the current merge as the reporting path while evaluating the candidate. The output is deterministic for a fixed build and options, but it is not byte-identical or fully policy-equivalent to `lcov -a`. Review [documented record differences](LIMITATIONS.md#lcov-record-differences) and the [reversible migration pilot](MIGRATING-FROM-LCOV.md#reversible-one-week-pilot).

`--mem-limit` is a budget for coverage-record storage, not a cap on process RSS or temporary disk. Check the [operational limits](LIMITATIONS.md#input-and-operational-bounds), especially when using `--jobs` or external sorting.

## Evidence status

The generated 20.3 GB comparison and the 11.2 MB project-derived composite were measured on one host. The composite joins 15 LCOV shards from eight pinned projects; lcovmerge completed in 0.085 s / 15.94 MiB, compared with lcov 2.6 at 2.043 s / 140.27 MiB, and the downstream `genhtml` report passed. These small one-host results establish compatibility and a measured example, not typical production savings. See [benchmarks and methods](BENCHMARKS.md), [real-project checks](validation/real-projects.md), the [exporter integration checks](validation/exporter-integrations.md), and the privacy-conscious [real-workload measurement guide](REAL-WORKLOADS.md).

## Sources

- [LCOV tracefile workflow and format](https://github.com/linux-test-project/lcov/blob/master/docs/man/geninfo.rst)
- [LLVM source-based coverage workflow](https://clang.llvm.org/docs/SourceBasedCodeCoverage.html)
- [Codecov report merging](https://docs.codecov.com/docs/merging-reports)
- [Coveralls parallel builds](https://docs.coveralls.io/parallel-builds)
- [Bazel coverage](https://bazel.build/docs/coverage)
