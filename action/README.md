# lcovmerge GitHub Action

Use `megasoft1978/lcovmerge@v1` to merge LCOV tracefiles in a workflow. The action downloads the matching release archive and checks it against that release's `SHA256SUMS` before running it.

```yaml
- name: Merge coverage
  id: merge
  uses: megasoft1978/lcovmerge@v1
  with:
    files: |
      coverage/unit/*.info
      coverage/integration/**/*.info
    output: coverage/merged.info
    mem-limit: 256M
    extra-args: --branch-coverage on

- name: Show merge summary
  env:
    LCOVMERGE_SUMMARY: ${{ steps.merge.outputs.summary }}
  run: echo "$LCOVMERGE_SUMMARY"
```

`files` accepts newline-separated paths or shell-style glob patterns. Relative paths resolve from `GITHUB_WORKSPACE`; absolute paths are accepted, empty lines are ignored, matched directories are skipped, and matched files are deduplicated. `output` must be a file path, relative to the workspace or absolute; this action does not support `-` output. `mem-limit` is optional and accepts bytes or case-insensitive binary `K`, `M`, or `G` suffixes. The CLI default is `64M`, with a minimum of `8M` per job. `extra-args` is parsed with shell-style quoting and can pass options such as `--rebase`, `--prefix-strip`, `--include`, `--exclude`, and `--branch-coverage`. The action supplies file paths, output, and `--stats` unless `--stats` is already present in `extra-args`. `version` defaults to `1.0.0` and can select another release.

See the [CLI usage reference](../docs/USAGE.md) for exact prefix rules, branch defaults, filter order, checksum handling, and exit statuses.

The action selects binaries for GitHub-hosted Linux and macOS runners on x86_64/arm64, and Windows x86_64. Windows runtime verification is pending a passing Windows CI run; local checks cover the PE format and cross-build, and a Wine run is recorded separately. The merge summary comes from `lcovmerge --stats`.
