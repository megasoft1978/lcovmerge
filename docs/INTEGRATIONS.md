# Integrations

lcovmerge is the merge stage after test jobs have produced LCOV tracefiles. It accepts existing `.info` files;
it does not collect compiler/runtime coverage, convert raw profiles, or generate HTML.

## GitHub Actions

Download the artifacts from your test jobs, then use the release-pinned action. It downloads the matching
release archive and checks it against that release's `SHA256SUMS` before execution:

```yaml
- name: Merge coverage
  uses: megasoft1978/lcovmerge@v1.0.0
  with:
    files: |
      coverage/unit/*.info
      coverage/integration/**/*.info
    output: coverage/merged.info
    mem-limit: 256M
```

The action accepts newline-separated paths or shell-style glob patterns. See the
[action reference](../action/README.md) and the full [GitHub Actions recipe](RECIPES.md#github-actions-matrix).

## GitLab, Jenkins, CMake, and Bazel

The [recipes](RECIPES.md) show how to pass one LCOV artifact per test shard into a merge job. The examples also
explain when a native collector already creates one combined report, as Bazel can do.

## Release archives and packages

Release archives are available from the [GitHub release page](https://github.com/megasoft1978/lcovmerge/releases).
The archive includes a binary and can be checked against `SHA256SUMS`. The Homebrew formula and Scoop manifest
templates are in [packaging/homebrew](../packaging/homebrew/lcovmerge.rb) and
[packaging/scoop](../packaging/scoop/lcovmerge.json); installation through those package managers is not
recorded as tested in this repository. Use the verified release archive if the package is unavailable.

## Docker

The release image is published at GitHub Container Registry. Run it from the project directory with the
workspace mounted so it can read inputs and write the merged file:

```sh
docker run --rm -v "$PWD:/work" -w /work ghcr.io/megasoft1978/lcovmerge:v1.0.0 \
  --tmpdir /tmp coverage/shard-*.info -o coverage/merged.info
```

The image uses lcovmerge as its entrypoint and includes no shell or report generator. `/tmp` is used for
external-sort runs. Generate HTML in another step with `genhtml`.

## Coverage report consumers

The merged output is an LCOV tracefile. Use it with `genhtml` or a consumer configured for LCOV. Codecov lists
LCOV among its [supported report formats](https://docs.codecov.com/docs/supported-report-formats), and the
Coveralls GitHub Action documents `lcov` as a supported
[format](https://github.com/coverallsapp/github-action). Some uploaders can accept multiple reports directly,
so merging is only required when you need one artifact or one path.

SonarQube accepts LCOV for some analyzers, including JavaScript/TypeScript, Dart, and Rust. Its C/C++ analyzer
uses gcov or llvm-cov reports. Check the current
[SonarQube coverage parameters](https://docs.sonarsource.com/sonarqube-server/analyzing-source-code/test-coverage/test-coverage-parameters)
for your language and analyzer.
