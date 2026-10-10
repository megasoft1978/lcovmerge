# Migrating from lcov -a

lcovmerge handles the merge stage for LCOV files that already exist. It does not collect coverage or replace
the lcov/genhtml suite. Keep `lcov -a` if you need its specific testcase, checksum, configuration, or MC/DC
behavior. The output is canonicalized and is not guaranteed to match lcov byte for byte.

## Common option mapping

| lcov | lcovmerge | Notes |
| --- | --- | --- |
| `-a FILE` / `--add-tracefile FILE` | Pass each `.info` path as a positional input | The shell expands globs before lcovmerge runs; it does not expand wildcards itself. |
| `-o FILE` / `--output-file FILE` | `-o FILE` / `--output FILE` | Writes the output tracefile. |
| `--parallel N` | `--jobs N` | lcovmerge uses these workers for external-sort input processing. Already-sorted regular files can use the single-threaded stream path; one large file is not split across jobs. |
| `--memory N` | No direct equivalent. `--mem-limit SIZE` is a record-arena budget. | The setting is not a total RSS cap and does not cap temporary disk. The minimum is 8 MiB per job. |
| `--tempdir DIR` | `--tmpdir DIR` | Both select a temporary directory; lcovmerge's external-sort runs are removed after success and handled failures. |
| `--include GLOB` / `--exclude GLOB` | `--include GLOB` / `--exclude GLOB` | Check path matching on your own tracefiles. lcovmerge applies rewriting before filters. |
| `--substitute REGEXP` | No direct equivalent. Use `--rebase OLD=NEW` or `--prefix-strip PREFIX` for leading path prefixes. | These are path-boundary prefix operations, not regular-expression substitutions. |

The options have similar goals in some cases, but their processing and edge-case behavior are not interchangeable.
See [all CLI options](USAGE.md) and [intentional differences](LIMITATIONS.md).

## Differences to review

Before changing the final merge job, check whether your inputs or downstream reports use:

- Testcase names or lcov configuration that changes merge behavior.
- Line checksums. lcovmerge preserves supplied checksums; conflicting non-empty checksums warn and resolve to
  the lexicographically smallest value unless `--strict-checksum` is set.
- MC/DC reporting. lcovmerge retains and merges MC/DC-shaped rows, but does not promise lcov-equivalent
  MC/DC accounting.
- Function aliases, orphan FNDA rows, branch markers, and unknown extension rows.
- Exact record ordering or summary values. lcovmerge sorts canonical record classes and recomputes summaries.

See [LIMITATIONS.md](LIMITATIONS.md) for the record-by-record policies.

## Verify your data before switching

Run both mergers against the same shard set and keep their outputs separate:

```sh
lcov -a coverage/shard-a.info -a coverage/shard-b.info -o coverage/lcov-merged.info
lcovmerge coverage/shard-a.info coverage/shard-b.info -o coverage/lcovmerge-merged.info --strict-checksum
```

Compare normalized `SF` path sets and keyed `DA`, `FN`, `FNDA`, and `BRDA` records and counts. Raw `cmp` or
`diff` is not a semantic-equivalence check because presentation order and summary rows can differ. Also inspect
checksums, `TN` rows, MC/DC rows, rewritten paths, and any extension records that your report consumers use.
Generate reports from both outputs with the same source tree and filters, then review the differences that
matter to your project. lcovmerge cannot establish equivalence for your data automatically.

### Synthetic smoke check from a source checkout

After `make` has built `bin/lcovmerge`, this deterministic fixture command checks the CLI path. It is a smoke
check, not evidence that your project has equivalent semantics:

```sh
work=$(mktemp -d "${TMPDIR:-/tmp}/lcovmerge.XXXXXX")
trap 'rm -rf "$work"' EXIT
python3 tools/gen-lcov.py --out "$work/shards" --shards 2 --files 4 --lines 40 --seed 1 --benchmark-compatible
./bin/lcovmerge "$work"/shards/shard-*.info -o "$work/merged.info" --stats
```

Output:

```text
lcovmerge: stats LF=32 LH=5 FNF=40 FNH=12 BRF=24 BRH=2
```

The fixture comes from `tools/gen-lcov.py` in the repository. Use a checkout to run this smoke check; installed
release archives do not include the generator.

## Rollout

1. Preserve a copy of the current lcov output and the input shards.
2. Compare normalized records and report summaries using the same source paths and filters.
3. Check any checksum, testcase, MC/DC, and unknown-record requirements in the migration list above.
4. Run the candidate output through the downstream report and upload steps in a non-blocking CI job.
5. Switch the merge job only after the comparison matches the requirements of your project. Keep `lcov -a`
   where its behavior is required.
