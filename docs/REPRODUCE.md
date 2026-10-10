# Reproducing the benchmark

`bench/reproduce.sh` rebuilds lcovmerge, generates the deterministic S or M fixture used by `bench/run.py`, and measures each available merger once. It records the host, tool versions, exact invocations, wall time, peak resident set size, and status in a local report. When `lcov` is installed, it also compares the merged outputs after normalizing source paths and keyed `DA`, `FN`, `FNDA`, and `BRDA` rows and counts.

## Run it

From the repository root, run S:

```sh
TMPDIR="$PWD/.cache" bench/reproduce.sh S .cache/reproduce-S
```

Run M with the same command and replace `S` and `reproduce-S` with `M` and `reproduce-M`. If the output directory is omitted, the script creates one with `mktemp` under `TMPDIR` (or the system temporary directory). Create an ignored repository-local `.cache` directory first when you want default results retained inside the checkout.

The script requires POSIX `sh`, `make`, Python 3, a C compiler, and `/usr/bin/time`. It uses `/usr/bin/time -l` on macOS and `/usr/bin/time -v` on Linux. `lcov` and `lcov-result-merger` are optional; the latter is used from `PATH` or through an already available local `npx --no-install` package. No optional comparator is installed by the script.

The fixture parameters and generator switches match `bench/run.py` exactly:

| Size | Shards | Files | Lines | Seed |
| --- | ---: | ---: | ---: | ---: |
| S | 8 | 56 | 23,000 | 4242 |
| M | 32 | 220 | 16,000 | 4242 |

The command uses `tools/gen-lcov.py --checksums --benchmark-compatible`. Generated input shards are removed on normal exit and handled signals. The result directory retains `report.txt`, captured command output and timing logs, and each successful merged output for inspection. The report lists commands with their actual expanded input paths and records versions and measurements; the temporary shard paths in those command lines are removed after the run.

## Read the results

Each table row is one run, not a median. Wall time comes from the platform time utility. Peak RSS is the process maximum reported by that utility and is converted to MiB for the table; the original time output is retained alongside stdout and stderr. If host policy prevents the time utility from reporting RSS, the table marks it `n/a` rather than estimating it. Unavailable optional tools are shown as `UNAVAILABLE`. A failed tool or semantic comparison makes the script exit non-zero.

The normalized comparison ignores row ordering, summary rows, testcase names, DA checksums, and FN end-line annotations; function rows are keyed by start line and name because lcov may derive end lines that are absent from an input. It compares the normalized `SF:` path set and keyed `DA`, `FN`, `FNDA`, and `BRDA` records and counts. This checks the generated fixture's main coverage records; it does not establish equivalence for checksums, `TN`, MC/DC, unknown extension rows, every lcov option, or your report consumers. Review [record differences](LIMITATIONS.md) and [the migration guide](MIGRATING-FROM-LCOV.md) before changing a real workflow.

## Limits and private data

These fixtures are synthetic. They give a skeptic a repeatable command and input definition, but they do not represent every project's path lengths, coverage density, storage, or report settings. The benchmark can be disk-bound: sorting and reading large tracefiles exercise the filesystem as well as CPU and memory. Cache state, operating system, filesystem, background load, and hardware affect wall time, so compare tools on the same host and inputs. A timeout is a censored run, not evidence that the timed-out tool is slower by the timeout duration or that a partial run is a speedup.

lcovmerge's `--mem-limit` controls memory reserved for coverage records during sorting; it is not a cap on process RSS. RSS includes other process memory. The reproduction command uses the default CLI settings and reports the observed process peak.

To measure private `.info` files, run the existing local-only benchmark runner and keep its reports in an ignored directory. For example:

```sh
make
TMPDIR="$PWD/.cache" python3 -I bench/run.py --workload REAL \
  --real-dir /absolute/path/to/private-captures --skip-lcov \
  --report .cache/private-run.txt --json-report .cache/private-run.json
```

This reads the files in place and removes its temporary work data. Keep the input files and reports on your machine; if you need to share a result, share only the aggregate numbers you choose, after checking that they contain no paths or other private details. Do not commit coverage data or publish a report containing identifying metadata.
