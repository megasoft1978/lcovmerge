# Share real-workload evidence safely

The published XXL result uses generated input. Project-derived validation is useful for merge behavior, but it
does not show how often lcovmerge saves time or memory in actual CI. Optional, sanitized reports from real
workflows would help identify the workloads where this tool earns its place.

## Measure private LCOV inputs locally

Build lcovmerge, then point the benchmark runner at a directory containing `.info` files. The runner reads
those files in place and writes temporary merge outputs under its own temporary directory. Keep its report
inside the ignored repository-local `.cache/` directory:

```sh
make
mkdir -p .cache
TMPDIR="$PWD/.cache" python3 -I bench/run.py --workload REAL \
  --real-dir /absolute/path/to/private-captures --hyperfine-runs 3 \
  --report .cache/private-real-report.txt
```

The runner uses LCOV when it is installed and uses `lcov-result-merger` when available. To measure lcovmerge
alone, add `--skip-lcov --skip-node`; that run does not provide a comparator result. Use the same host, inputs,
options, and cache conditions when comparing with the command your CI currently runs. The built-in LCOV
comparator may use different options from your CI command; record and compare your actual command separately
when its flags or workflow differ.

## Review before sharing

Keep the generated text and JSON reports private. They can contain absolute file paths, host details, commands,
and environment metadata. Do not attach `.info` coverage files, raw logs, or unreviewed benchmark output to a
public issue. Instead, manually share only the fields you are comfortable making public:

- OS and CPU family/model (a broad description is enough); do not include hostnames or account names.
- Total input size, number of shards, and approximate output size.
- Tool names and versions, elapsed time, and peak resident memory for each completed run.
- Run count, whether measurements were warm or cold, and any timeout or failure status.
- Whether both outputs were accepted by the same downstream report/upload step, plus a short description of
  any meaningful normalized difference. Do not include source paths, project names, source code, or coverage
  values tied to identifiable files.
- Whether the result came from a work project, an open source project, or a generated fixture.

If you cannot safely share a field, leave it out. A rough anonymous result is more useful than exposing private
coverage data. Submit it through the repository's [real-workload report issue form](https://github.com/megasoft1978/lcovmerge/issues/new?template=real_workload.yml).

## How reports will be used

Reports are optional. The maintainer may summarize sanitized aggregate values in the benchmark documentation,
with the host class, workload type, tool versions, measurement date, and method. No private input files are
needed or requested. A report does not imply endorsement, compatibility, or that lcovmerge should replace the
submitter's existing merge step.
