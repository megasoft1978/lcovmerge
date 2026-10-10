# Usage

```text
lcovmerge [options] a.info b.info ... -o out.info
```

lcovmerge reads existing LCOV `.info` files; it does not collect raw coverage or generate reports. `-o` or
`--output` is required. Use `-` as one input to read standard input or as the output path to write standard
output. Input paths beginning with `@` name a list file: one path per line, blank lines and lines beginning
with `#` are ignored, nested list files are limited to eight levels, and paths are relative to the process
working directory. Use `@@name` for a literal input path beginning with `@`. The shell expands globs;
lcovmerge does not.

## Install

The [release page](https://github.com/megasoft1978/lcovmerge/releases) provides Linux x86-64 and aarch64,
macOS arm64 and x86-64, and Windows x86-64 archives. Verify the selected archive against `SHA256SUMS` before
extracting it. The [README quick start](../README.md#try-it-linux-x86-64) has a complete checksum-verified
Linux command sequence. Windows runtime verification is pending a passing Windows CI run; a published archive
or cross-build does not establish target-runtime verification. See [platform limits](LIMITATIONS.md).

## Options

### `-o FILE`, `--output FILE`

Write the merged tracefile to `FILE`. The output file is replaced only after a successful merge. Use `-` for
standard output. File output is staged before it replaces the destination. Bytes already written when using
`-o -` cannot be rolled back; a later signal or write failure may leave partial stdout output.

### `--mem-limit SIZE`

Set the limit for memory reserved for coverage records while sorting. The default is `64M`; the minimum is
`8M` per job. A suffix of `K`, `M`, or `G` is case-insensitive and denotes a binary multiple of 1024. A value
without a suffix is bytes. This setting does not cap total process memory or temporary disk use: parser
buffers, merge bookkeeping, thread stacks, and runtime state need additional memory, and sorting writes
temporary files.

### `--tmpdir DIR`

Store temporary sorted files under an existing `DIR`; it must be writable and have enough free disk space.
The default is the operating system's temporary directory. Files are removed on success and handled failures.
On POSIX, caught SIGINT, SIGTERM, and SIGHUP also trigger cleanup. A forced kill or machine failure can leave
named files behind. Windows does not promise the same interruption cleanup because blocked I/O and worker
joins have no cancellation path.

### `-j N`, `--jobs N`

Set the number of input-file workers from 1 through 32. The default is the lesser of four and the detected
processor count, further reduced when needed to fit the memory setting. An explicit job count must fit the
minimum `8M` per job. Workers process whole files; a single large file is not split across workers. When all
inputs are regular files and their rewritten records are already in stable order, lcovmerge streams the merge
with one worker and ignores this setting. An out-of-order row, unsupported stream type, or memory limit for
the streaming path causes lcovmerge to retry using temporary sorted files and the requested worker count.

### `--prefix-strip PREFIX`

Remove a matching leading prefix from each `SF:` path. A match must end at a path boundary. Any separators
after the prefix are removed.

### `--rebase OLD=NEW`

Rewrite a matching leading `SF:` path prefix. Repeat the option to map multiple build roots. Prefix matching
uses a path boundary, and the replacement preserves the input's directory separator style where possible.

### `--include GLOB`

Keep paths matching at least one include pattern. Repeat the option for additional patterns.

### `--exclude GLOB`

Drop paths matching an exclude pattern. Exclusions take precedence over includes. `*`, `?`, and bracket ranges
are supported.

Path rewriting runs in this order: `--rebase`, `--prefix-strip`, then include and exclude filtering. Prefix
matching requires a path boundary; it is not automatic path detection. If rewritten paths become identical,
their records are merged. Review the emitted `SF:` paths before generating a report.

### `--branch-coverage on|off`

Keep or drop `BRDA:` rows. The default is `on`; this option does not create branch data that was not captured.

### `--no-function-data`

Drop `FN`, `FNDA`, `FNL`, and `FNA` records.

### `--strict-checksum`

Return exit status 2 if two non-empty checksums for the same source line disagree. By default, lcovmerge warns
and emits the lexicographically smallest non-empty checksum, making the result independent of input order.

### `--warn-unknown`

Print a diagnostic for each preserved unknown colon-delimited record inside a source section. Unknown records
are not interpreted as coverage data.

### `-q`

Suppress routine progress output. Errors remain visible.

### `-v`

Print parsed byte and record totals, the selected worker count, and merged summary totals to standard error.

### `--stats`

Print merged `LF`, `LH`, `FNF`, `FNH`, `BRF`, and `BRH` totals to standard error.

### `--version`

Print the version and build's Git commit identifier.

### `-h`, `--help`

Print the built-in usage summary.

## Exit status

| Code | Meaning |
| ---: | --- |
| `0` | Merge completed. |
| `1` | Invalid or incomplete command-line options. |
| `2` | Input or tracefile format error, including strict checksum disagreement. |
| `3` | Input/output or temporary-file I/O failure, allocation failure, or interruption. |

## Interruption and cleanup

On POSIX, SIGINT, SIGTERM, and SIGHUP request cooperative interruption. A caught interruption returns status
3 and removes temporary sorted files and any unpublished staged output. Temporary sorted files are also removed
after success and handled failures. SIGKILL and machine failure cannot run cleanup.

On POSIX, lcovmerge ignores SIGPIPE, so writing to a closed pipe reports EPIPE as an I/O failure and
follows normal cleanup. Bytes already written to stdout remain visible if a later signal or write
failure occurs.

Windows uses synchronous ReadFile/WriteFile calls and blocking worker-thread joins without a cancellation path.
A blocked operation can prevent interruption from completing, so the interruption and cleanup guarantee is
POSIX-only.

## Input records

The parser recognizes `TN`, `SF`/`KF`, legacy `FN`/`FNDA`, LCOV 2.x `FNL`/`FNA`, `DA` with optional
checksums, `BRDA`, and `MCDC`. Existing summary rows are ignored and regenerated from the merged coverpoints.
Unknown colon-delimited rows inside a source section are preserved in sorted order; exact duplicate unknown
rows are preserved too.

## Related guides

- [Migrate from `lcov -a`](MIGRATING-FROM-LCOV.md), including flag mapping and a verification checklist.
- [CI and format-export recipes](RECIPES.md).
- [Frequently asked questions](FAQ.md).

## Local temporary-directory example

The selected directory must exist and have free disk space. This example creates it before the merge and
removes it when the shell exits:

```sh
set -eu
tmpdir=$(mktemp -d)
trap 'rm -rf "$tmpdir"' EXIT
lcovmerge --mem-limit 8M --jobs 1 --tmpdir "$tmpdir" @coverage-inputs.txt -o merged.info -v
```
