# lcovmerge GitHub Action

For the upcoming Rust-based 1.2.0 release, use `megasoft1978/lcovmerge@v1.2.0` to merge LCOV tracefiles in a workflow. Once published, the action will download the matching release archive and check it against that release's `SHA256SUMS` before running it. Until the v1.2.0 assets are published, pin `@v1.1.0` explicitly.

```yaml
- name: Merge coverage
  id: merge
  uses: megasoft1978/lcovmerge@v1.2.0
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

`files` accepts newline-separated paths or shell-style glob patterns. Relative paths resolve from `GITHUB_WORKSPACE`; absolute paths are accepted, empty lines are ignored, matched directories are skipped, and matched files are deduplicated. The output file is excluded if a broad glob matches it, so rerunning the action does not merge its previous output into itself. Paths containing newlines are unsupported. Inputs are passed with a temporary list file to avoid operating-system command-line length limits. `output` must be a file path, relative to the workspace or absolute; this action does not support `-` output. `mem-limit` is optional and accepts bytes or case-insensitive binary `K`, `M`, or `G` suffixes. The CLI default is `64M`, with a minimum of `8M` per job. `extra-args` is parsed with shell-style quoting and can pass options such as `--rebase`, `--prefix-strip`, `--include`, `--exclude`, and `--branch-coverage`. The action supplies file paths, output, and `--stats` unless `--stats` is already present in `extra-args`. `version` defaults to `1.2.0` and can select another release.

See the [CLI usage reference](../docs/USAGE.md) for exact prefix rules, branch defaults, filter order, checksum handling, and exit statuses.

## LCOV merge profile

The v1.2.0 Action is intended to use the Rust CLI and retains the native CLI's
deduplicated input-list and summary contract. The experimental
`--lcov-compat=2.6` profile is not supported through this Action: it accepts
repeated tracefile arguments, while this Action supplies a native `@listfile`.
To opt in to the profile's documented merge surface, build the Rust checkout
and invoke its CLI directly in a workflow step:

```yaml
- name: Build and merge with the LCOV profile
  shell: bash
  run: |
    make
    ./bin/lcovmerge --lcov-compat=2.6 -a 'coverage/*.info' -o coverage/merged.info
```

The profile supports only the operations and settings listed in its
[command and validation contract](../docs/LCOV-PROFILE.md); capture, report
generation, and unsupported merge settings remain outside that surface. Its
validation is still in progress, with no stable drop-in or performance claim.
Compare results with LCOV on your pipeline's inputs before changing reports or
coverage gates.

The action selects release assets for Linux and macOS x86_64/arm64 runners and Windows x86_64. These are supported asset targets; this README makes no claim that every operating-system version or environment has been independently verified. Historical evidence for the published C v1.1.0 release is recorded in the repository's release and benchmark documentation. The merge summary comes from `lcovmerge --stats`.

The `Action smoke` workflow runs on pull requests and pushes to `main` on Ubuntu, macOS, and Windows. Until v1.2.0 assets are published, it explicitly uses the published C v1.1.0 assets, compares the result byte-for-byte with the locally built CLI, checks the `summary` output, and asserts failure for empty file input and malformed LCOV. These checks cover action integration for v1.1.0.
