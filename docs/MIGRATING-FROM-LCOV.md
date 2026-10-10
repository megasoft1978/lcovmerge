# Migrating from lcov -a

lcovmerge merges already-exported LCOV `.info` files; it does not collect coverage or replace `genhtml`. It is not a drop-in replacement for `lcov -a`. Keep `lcov -a` when your workflow depends on its behavior, and compare your own records and reports before switching.

## Common option mapping

| lcov | lcovmerge | Notes |
| --- | --- | --- |
| `-a FILE` / `--add-tracefile FILE` | Pass each `.info` path as a positional input | The shell expands globs before lcovmerge runs; it does not expand wildcards itself. |
| `-o FILE` / `--output-file FILE` | `-o FILE` / `--output FILE` | The destination is replaced only after a successful file merge. |
| `--parallel N` | `--jobs N` | Workers process whole files; a single large file is not split. The merge stage uses one worker. |
| `--memory N` | No direct equivalent. `--mem-limit SIZE` | Limits memory reserved for coverage records while sorting. It does not cap total process memory or temporary disk. The minimum is 8 MiB per job. |
| `--tempdir DIR` | `--tmpdir DIR` | Selects an existing writable directory for temporary sorted files. Success and handled failures clean them up; interruption details differ by platform. |
| `--include GLOB` / `--exclude GLOB` | `--include GLOB` / `--exclude GLOB` | Check path matching on your own files. lcovmerge rewrites paths before applying filters. |
| `--substitute REGEXP` | No direct equivalent. Use `--rebase OLD=NEW` or `--prefix-strip PREFIX` for leading path prefixes. | Prefix matching uses path boundaries; it is not regular-expression substitution or automatic path detection. |

The options may serve similar purposes, but their processing and edge cases are not interchangeable. See [all CLI options](USAGE.md) and [record differences](LIMITATIONS.md).

## Differences to review

Before changing the final merge job, check whether your inputs or downstream reports depend on:

- Testcase names or lcov configuration that changes merge behavior.
- Line checksums. lcovmerge preserves supplied checksums; conflicting non-empty checksums warn and resolve to the lexicographically smallest value unless `--strict-checksum` is set.
- Branch rows and markers. Counts are combined by lcovmerge's documented key rules; unreachable branches are excluded from its branch summary.
- Function aliases and orphan FNDA rows.
- MC/DC coverage records. lcovmerge retains and combines these rows, but does not promise lcov-equivalent MC/DC accounting.
- Unknown colon-delimited extension rows. lcovmerge preserves them without interpreting their application-specific meaning.
- Summary rows and ordering. lcovmerge discards input summaries, recalculates summaries from records, and writes records in a stable order.

See [LIMITATIONS.md](LIMITATIONS.md#lcov-record-differences) for the record-by-record policies.

## Same-input comparison

Use the same LCOV files with both programs and keep the outputs separate. This example generates an LCOV fixture with 80 executable lines per source so function rows have matching line-coverage rows in the generated data. The comparison uses lcov 2.6 syntax; adjust paths for your checkout.

```sh
set -eu
work=$(mktemp -d "${TMPDIR:-/tmp}/lcovmerge-compare.XXXXXX")
trap 'rm -rf "$work"' EXIT
python3 tools/gen-lcov.py --out "$work/shards" --shards 2 --files 4 --lines 80 --seed 1 --benchmark-compatible --lcov-valid
lcov -a "$work/shards/shard-0000.info" -a "$work/shards/shard-0001.info" -o "$work/lcov.info"
./bin/lcovmerge "$work"/shards/shard-*.info -o "$work/lcovmerge.info" --strict-checksum
```

Compare normalized `SF:` path sets and keyed `DA`, `FN`, `FNDA`, and `BRDA` rows and counts. A raw `cmp` or `diff` is not a semantic-equivalence check because ordering and summary rows can differ. Also inspect checksums, `TN` rows, branches, function aliases, MC/DC records, rewritten paths, and any extension records used by your report consumers. Generate both reports with the same source tree and filters, then review differences that matter to your project. lcovmerge cannot establish equivalence for your data automatically.

## lcovmerge-only smoke check

This source-checkout example tests fixture generation and the lcovmerge CLI only. It does not invoke lcov and does not establish compatibility with `lcov -a`.

```sh
set -eu
work=$(mktemp -d "${TMPDIR:-/tmp}/lcovmerge-smoke.XXXXXX")
trap 'rm -rf "$work"' EXIT
python3 tools/gen-lcov.py --out "$work/shards" --shards 2 --files 4 --lines 80 --seed 1 --benchmark-compatible --lcov-valid
./bin/lcovmerge "$work"/shards/shard-*.info -o "$work/merged.info" --stats
```

Run `make` first to build `bin/lcovmerge`. The release archives do not include `tools/gen-lcov.py`.

## Path checks

When workers use different checkout roots, use `--rebase OLD=NEW` or `--prefix-strip PREFIX` to rewrite leading `SF:` paths. Rewriting runs in this order: rebasing, prefix stripping, then include/exclude filtering. Prefix matches require a path boundary; lcovmerge does not discover the right source root for you. Inspect the rewritten `SF:` paths before comparing or generating reports. If different source files become the same path, lcovmerge combines their records.

## Temporary files and memory

`--mem-limit` limits memory reserved for coverage records while sorting; total process memory can be higher. Temporary sorted files use disk space outside that setting, and `--tmpdir` must name an existing writable directory with enough space. POSIX cleanup runs on success, handled errors, and caught SIGINT, SIGTERM, and SIGHUP. A forced kill or machine failure can leave named files behind. Windows does not promise the same interruption cleanup. See [operational limits](LIMITATIONS.md#input-and-operational-bounds).

## Rollout

1. Keep a copy of the current lcov output and input files.
2. Compare normalized records and report summaries using the same source paths and filters.
3. Check checksum, testcase/configuration, branch, function-alias, MC/DC, unknown-record, summary, and ordering requirements.
4. Run the candidate output through downstream report and upload steps in a non-blocking CI job.
5. Switch only after the comparison meets your project's requirements. Keep `lcov -a` where its behavior is required.
