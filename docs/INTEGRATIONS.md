# Integrations

The shared pattern is: run tests, write one LCOV tracefile per shard, collect those files as CI artifacts, and
merge them in a final job. lcovmerge handles `.info` files only; it does not collect raw compiler or runtime
coverage data.

## GitHub Actions

Use the reusable action after downloading the shard artifacts. The action selects the release binary, checks
its checksum against `SHA256SUMS`, and writes the merged tracefile.

```yaml
name: coverage
on: [push, pull_request]

jobs:
  test:
    strategy:
      matrix:
        shard: [unit, integration]
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - run: mkdir -p coverage
      - run: ./ci/test-${{ matrix.shard }} --coverage-output "coverage/${{ matrix.shard }}.info"
      - uses: actions/upload-artifact@v7
        with:
          name: coverage-${{ matrix.shard }}
          path: coverage/${{ matrix.shard }}.info

  merge:
    needs: test
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: actions/download-artifact@v8
        with:
          pattern: coverage-*
          path: coverage-shards
          merge-multiple: true
      - uses: megasoft1978/lcovmerge@v1
        with:
          files: coverage-shards/*.info
          output: coverage/merged.info
          mem-limit: 256M
      - run: genhtml coverage/merged.info --output-directory coverage/html
```

Replace the test command and artifact names with those used by your project. The example expects each test
shard to write a distinct `.info` file. The Action accepts newline-separated paths or shell-style glob
patterns. For exact input matching, keep its `files` list aligned with the artifacts downloaded by your
workflow.

## GitLab CI

Publish one LCOV artifact per test job, then download the artifacts in a final merge job. This example fetches
the Linux x86-64 release archive and checks its SHA-256 before running it:

```yaml
test-unit:
  stage: test
  script:
    - mkdir -p coverage
    - ./ci/test-unit --coverage-output coverage/unit.info
  artifacts:
    paths:
      - coverage/unit.info

test-integration:
  stage: test
  script:
    - mkdir -p coverage
    - ./ci/test-integration --coverage-output coverage/integration.info
  artifacts:
    paths:
      - coverage/integration.info

merge-coverage:
  stage: report
  needs:
    - job: test-unit
      artifacts: true
    - job: test-integration
      artifacts: true
  script:
    - asset=lcovmerge-1.0.0-linux-x86_64.tar.gz
    - base=https://github.com/megasoft1978/lcovmerge/releases/download/v1.0.0
    - curl -fL "$base/$asset" -o "$asset"
    - curl -fL "$base/SHA256SUMS" -o SHA256SUMS
    - grep " $asset$" SHA256SUMS | sha256sum -c -
    - tar -xzf "$asset"
    - mkdir -p coverage
    - ./lcovmerge coverage/*.info -o coverage/merged.info
    - genhtml coverage/merged.info --output-directory coverage/html
  artifacts:
    paths:
      - coverage/merged.info
      - coverage/html/
```

Use the archive matching the runner architecture. GitLab's built-in `coverage_report` visualization currently
accepts Cobertura or JaCoCo reports, not LCOV; keep the `.info` artifact for an LCOV-aware uploader or convert
it separately. See the [GitLab CI reference](https://docs.gitlab.com/ci/yaml/#artifactsreportscoverage_report).

## Bazel

Bazel can produce a combined LCOV report with `bazel coverage --combined_report=lcov` when supported by the
repository's Bazel version and rules. If that report already covers the pipeline, no additional merge is
needed. For separately produced LCOV shard files, lcovmerge can run as an optional post-processing step:

```sh
bazel coverage --combined_report=lcov //...
mkdir -p coverage
lcovmerge bazel-out/_coverage/_coverage_report.dat extra-worker.info -o coverage/combined.info
genhtml coverage/combined.info --output-directory coverage/html
```

The output path and command options depend on the Bazel version and rules. lcovmerge does not replace Bazel
instrumentation, `CoverageOutputGenerator`, or the coverage action pipeline. [Issue #26383](https://github.com/bazelbuild/bazel/issues/26383)
describes memory pressure in Bazel's generator; the reported workload was not included in this project's
benchmarks.

## CMake and gcov

Compile with GCC or a compatible gcov toolchain, run tests, and capture an LCOV file. Each independent test
shard should write to its own output path before the artifact merge job.

```sh
cmake -S . -B build -DCMAKE_BUILD_TYPE=Debug \
  -DCMAKE_C_FLAGS=--coverage -DCMAKE_CXX_FLAGS=--coverage
cmake --build build
ctest --test-dir build --output-on-failure

mkdir -p coverage
lcov --capture --directory build --output-file coverage/shard-1.info
# Repeat in each CI shard, using a distinct output path
lcovmerge coverage/shard-*.info -o coverage/merged.info
genhtml coverage/merged.info --output-directory coverage/html
```

For a gcovr-based collector, one possible flow is:

```sh
mkdir -p coverage
gcovr --root . --lcov coverage/gcovr.info
lcovmerge coverage/gcovr.info -o coverage/merged.info
```

Capture options depend on your compiler and build-tree layout. See [lcov](https://github.com/linux-test-project/lcov)
and the [gcovr LCOV output guide](https://gcovr.com/en/stable/output/lcov.html).

## Homebrew and Scoop

The repository contains a [Homebrew formula template](../packaging/homebrew/lcovmerge.rb) and a
[Scoop manifest template](../packaging/scoop/lcovmerge.json). On a release, the workflow can create a package
update pull request when `TAP_TOKEN` is configured. Install from the tap only after the matching manifest is
published in the [Homebrew/Scoop tap](https://github.com/megasoft1978/homebrew-tap):

```sh
brew install megasoft1978/homebrew-tap/lcovmerge
scoop bucket add lcovmerge https://github.com/megasoft1978/homebrew-tap
scoop install lcovmerge
```

Until the manifest for your version is present, install the matching release archive and verify it against
`SHA256SUMS`.

## Docker

Release tags publish a multi-architecture image to GitHub Container Registry. The image contains the
lcovmerge executable; mount the workspace to read and write tracefiles:

```sh
docker run --rm -v "$PWD:/work" -w /work ghcr.io/megasoft1978/lcovmerge:v1.0.0 \
  --tmpdir /tmp coverage/shard-*.info -o coverage/merged.info
```

`/tmp` is available in the image for external-sort runs. The image is a command container with lcovmerge as
its entrypoint; it does not include a shell or report generator.

## Coverage report consumers

The merged output remains an LCOV tracefile. Use it with `genhtml`, a Codecov uploader that accepts LCOV, or a
Coveralls integration configured for LCOV. Codecov lists LCOV among its supported
[coverage report formats](https://docs.codecov.com/docs/supported-report-formats); the Coveralls GitHub Action
documents `lcov` as a supported [`format`](https://github.com/coverallsapp/github-action).

SonarQube accepts LCOV for analyzers including JavaScript/TypeScript, Dart, and Rust. For C/C++ analysis,
SonarQube's CFamily analyzer imports gcov or llvm-cov reports instead. lcovmerge does not convert LCOV back
to those compiler-specific formats. Check the current [SonarQube coverage parameters](https://docs.sonarsource.com/sonarqube-server/analyzing-source-code/test-coverage/test-coverage-parameters)
for your language and analyzer.

## Jenkins

For parallel stages, transfer each shard's LCOV artifact to a merge stage. Use `stash`/`unstash` for smaller
files; for large reports, prefer the configured artifact manager or shared storage rather than routing
multi-gigabyte files through the controller.

```sh
find coverage -type f -name '*.info' -print | sort > coverage-files.txt
lcovmerge @coverage-files.txt -o coverage/merged.info
```

Keep the list file outside a directory that your collection step scans repeatedly. See Jenkins'
[Pipeline artifact steps](https://www.jenkins.io/doc/pipeline/steps/workflow-basic-steps/) and
[archiveArtifacts](https://www.jenkins.io/doc/pipeline/steps/core/).

## Jest and pytest-cov

Configure each test shard to write a separate LCOV file, then merge after CI downloads all shard outputs.

```sh
# Jest/Istanbul
npx jest --coverage --coverageReporters=lcov --coverageDirectory=coverage/unit

# pytest-cov
pytest --cov=my_package --cov-report=lcov:coverage/python.info

# Merge the files produced by either collector
lcovmerge coverage/**/*.info -o coverage/merged.info
```

See the Jest [`coverageReporters`](https://jestjs.io/docs/configuration#coveragereporters-arraystring)
configuration and the pytest-cov [reporting guide](https://pytest-cov.readthedocs.io/en/stable/reporting.html).
The shell's glob behavior varies; use an `@listfile` when you need an explicit recursive file list.
