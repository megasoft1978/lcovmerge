# lcovmerge GitHub Action

Use `megasoft1978/lcovmerge@v1.2.0` to merge LCOV tracefiles with the Rust CLI. The Action downloads the matching release archive and verifies it against that release's `SHA256SUMS` before running it.

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

The v1.2.0 Action uses the Rust CLI and retains the native CLI's
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
generation, and unsupported merge settings remain outside that surface. The profile is experimental; its tests and measurements cover the documented merge contract.
Compare results with LCOV on your pipeline's inputs before changing reports or
coverage gates.

The action selects release assets for Linux and macOS x86_64/arm64 runners and Windows x86_64. Runtime CI exercises these targets; see [platform limits](../docs/LIMITATIONS.md#verification-status) for the verification scope. The merge summary comes from `lcovmerge --stats`.

The `Action smoke` workflow runs on pull requests and pushes to `main` on Ubuntu, macOS, and Windows. Prepublication CI explicitly selects the existing v1.1.0 assets, compares the result byte-for-byte with the locally built CLI, checks the `summary` output, and asserts failure for empty file input and malformed LCOV. After publication, dispatch it with `release_version=1.2.0` to verify the Rust release through the Action on all three operating systems.
