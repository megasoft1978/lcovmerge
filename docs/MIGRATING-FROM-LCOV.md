# Migrating from `lcov -a`

lcovmerge merges already-exported LCOV `.info` files; it does not collect coverage or replace `genhtml`. Native mode has its own merge policies; the experimental `--lcov-compat=2.6` profile implements a version-pinned subset of LCOV 2.6's merge command. Neither mode replaces LCOV collection, reset, extraction, reporting, or callback workflows. See the [profile contract and validation status](LCOV-PROFILE.md), and compare your records and reports before switching.

## Native-mode option mapping

| lcov | lcovmerge | Notes |
| --- | --- | --- |
| -a FILE / `--add-tracefile` FILE | Pass each `.info` path as a positional input | The shell expands globs before lcovmerge runs; it does not expand wildcards itself. |
| -o FILE / `--output-file` FILE | -o FILE / `--output` FILE | The destination is replaced only after a successful file merge. |
| `--parallel` N | `--jobs` N | Workers process whole files; a single large file is not split. The external-sort merge stage uses one worker; an explicit `-j N` can thread the sorted-input streaming merge (see [USAGE](USAGE.md#-j-n---jobs-n)). |
| `--memory` N | No direct equivalent. `--mem-limit` SIZE | Limits memory reserved for coverage records while sorting. It does not cap total process memory or temporary disk. The minimum is 8 MiB per job. |
| `--tempdir` DIR | `--tmpdir` DIR | Selects an existing writable directory for temporary sorted files. Success and handled failures clean them up; interruption details differ by platform. |
| `--include` GLOB / `--exclude` GLOB | `--include` GLOB / `--exclude` GLOB | Check path matching on your own files. lcovmerge rewrites paths before applying filters. |
| `--substitute` REGEXP | No direct equivalent. Use `--rebase` OLD=NEW or `--prefix-strip` PREFIX for leading path prefixes. | Prefix matching uses path boundaries; it is not regular-expression substitution or automatic path detection. |

The options may serve similar purposes, but their processing and edge cases are not interchangeable. See [all CLI options](USAGE.md) and [record differences](LIMITATIONS.md).

For the opt-in profile, keep LCOV's `-a`/`--add-tracefile`, `-o`/`--output-file`,
`--parallel`, and `--tempdir` spellings after `--lcov-compat=2.6`. The profile
expands quoted input globs and retains testcase names. Function coverage defaults
on and requires function coverpoints across the merged inputs; use
`--no-function-coverage` for line-only data. Read the [profile contract](LCOV-PROFILE.md)
for supported configuration and record policies.

## Differences to review

Before changing the final merge job, check whether your inputs or downstream reports depend on:

- Testcase names or lcov configuration that changes merge behavior.
- Line checksums. lcovmerge preserves supplied checksums; conflicting non-empty checksums warn and resolve to the lexicographically smallest value unless `--strict-checksum` is set.
- Branch rows and markers. Counts are combined by lcovmerge's documented key rules; unreachable branches are excluded from its branch summary.
- Function aliases and orphan `FNDA` rows.
- MC/DC coverage records. lcovmerge retains and combines these rows, but does not promise lcov-equivalent MC/DC accounting.
- Unknown colon-delimited extension rows. lcovmerge preserves them without interpreting their application-specific meaning.
- Summary rows and ordering. lcovmerge discards input summaries, recalculates summaries from records, and writes records in a stable order.

See [LIMITATIONS.md](LIMITATIONS.md#lcov-record-differences) for the record-by-record policies.

## Reversible one-week pilot

For one week, run lcovmerge in a non-blocking CI job beside your current merge, using the same downloaded shard artifacts. Keep `lcov -a` as the job that supplies your report and any coverage gate. Save lcovmerge's output separately, run it through the same downstream report or upload checks, and compare normalized records and report summaries with the same source paths and filters.

Keep the current merge command as the exit path: if a difference is unexplained or unacceptable, disable the candidate job and leave the existing report job unchanged. Switch only after your team accepts the candidate output.

## Same-input compare command

From a source checkout, build the CLI with make first. This command creates two LCOV shards, merges those same files with both tools, and keeps the outputs separate:

```sh
set -eu
work=$(mktemp -d "${TMPDIR:-/tmp}/lcovmerge-compare.XXXXXX")
trap 'python3 -c "import shutil,sys; shutil.rmtree(sys.argv[1])" "$work"' EXIT
python3 tools/gen-lcov.py --out "$work/shards" --shards 2 --files 4 --lines 80 --seed 1 --benchmark-compatible --lcov-valid
lcov -a "$work/shards/shard-0000.info" -a "$work/shards/shard-0001.info" -o "$work/lcov.info"
./bin/lcovmerge "$work"/shards/shard-*.info -o "$work/lcovmerge-native.info" --strict-checksum
./bin/lcovmerge --lcov-compat=2.6 -a "$work"/shards/shard-*.info -o "$work/lcovmerge-profile.info"
```

The first command exercises native mode; the second exercises the experimental profile on the same input files. To exercise quoted-glob expansion, use `-a "$work/shards/shard-*.info"` in the profile command. Compare each output with the LCOV output and with the downstream reports your pipeline uses.

This command prepares separate outputs; it does not prove semantic equivalence. Compare normalized SF path sets and keyed `DA`, `FN`, `FNDA`, and `BRDA` rows and counts. A raw cmp or diff is not sufficient because row order and summary rows can differ. Also check checksums, `TN` rows, branches, function aliases, MC/DC records, rewritten paths, and extension records used by your consumers. Run both outputs through the same downstream report commands and filters.

For a real pilot, replace the generated inputs above with the paths to the same downloaded shards in your non-blocking job. Keep the lcov output as the current report path until the review is complete.

## lcovmerge-only smoke check

This source-checkout example tests fixture generation and the lcovmerge CLI only. It does not invoke lcov and does not establish compatibility with `lcov -a`.

```sh
set -eu
work=$(mktemp -d "${TMPDIR:-/tmp}/lcovmerge-smoke.XXXXXX")
trap 'python3 -c "import shutil,sys; shutil.rmtree(sys.argv[1])" "$work"' EXIT
python3 tools/gen-lcov.py --out "$work/shards" --shards 2 --files 4 --lines 80 --seed 1 --benchmark-compatible --lcov-valid
./bin/lcovmerge "$work"/shards/shard-*.info -o "$work/merged.info" --stats
```

Run make first to build bin/lcovmerge. The release archives do not include tools/gen-lcov.py.

## Path checks

When workers use different checkout roots, use `--rebase` OLD=NEW or `--prefix-strip` PREFIX to rewrite leading `SF:` paths. Rewriting runs in this order: rebasing, prefix stripping, then include/exclude filtering. Prefix matches require a path boundary; lcovmerge does not discover the right source root for you. Inspect the rewritten `SF:` paths before comparing or generating reports. If different source files become the same path, lcovmerge combines their records.

## Temporary files and memory

`--mem-limit` limits memory reserved for coverage records while sorting; total process memory can be higher. Temporary sorted files use disk space outside that setting, and `--tmpdir` must name an existing writable directory with enough space. POSIX cleanup runs on success, handled errors, and caught SIGINT, SIGTERM, and SIGHUP. A forced kill or machine failure can leave named files behind. Windows does not promise the same interruption cleanup. See [operational limits](LIMITATIONS.md#input-and-operational-bounds).
