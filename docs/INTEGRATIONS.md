# Integrations

The common pattern is: each test worker produces an LCOV file, CI stores those files as artifacts, and one
aggregation job merges them before reporting or upload. lcovmerge handles `.info` files only; it does not
collect raw compiler or runtime coverage data.

## Bazel

Bazel can produce a combined LCOV report with `bazel coverage --combined_report=lcov` when supported by the
repository's Bazel version and rules. The documented output is commonly
`bazel-out/_coverage/_coverage_report.dat`.

```sh
bazel coverage --combined_report=lcov //...
mkdir -p coverage
lcovmerge bazel-out/_coverage/_coverage_report.dat extra-worker.info -o coverage/combined.info
genhtml coverage/combined.info --output-directory coverage/html
```

If Bazel already creates the one report you need, no additional merge is necessary. lcovmerge can be evaluated
as an optional final file-combine step for independently produced LCOV files. It does not replace Bazel
instrumentation, `CoverageOutputGenerator`, or the Bazel action pipeline. See the [Bazel coverage
guide](https://bazel.build/configure/coverage) and [issue
26383](https://github.com/bazelbuild/bazel/issues/26383); the issue's workload was not included in our
benchmark.

## GitHub Actions sharded tests

Have each matrix leg write its own `coverage/shard.info` and upload it as an artifact. Download all artifacts
in a final job and merge them. This illustrates the artifact pattern documented by [GitHub
Actions](https://docs.github.com/en/actions/tutorials/store-and-share-data).

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
      - run: |
          mkdir -p coverage
          ./ci/test-${{ matrix.shard }} --coverage-output coverage/shard.info
      - uses: actions/upload-artifact@v7
        with:
          name: coverage-${{ matrix.shard }}
          path: coverage/shard.info

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
      - name: Install lcovmerge
        run: |
          asset=lcovmerge-1.0.0-linux-x86_64.tar.gz
          base=https://github.com/megasoft1978/lcovmerge/releases/download/v1.0.0
          curl -fL "$base/$asset" -o "$asset"
          curl -fL "$base/SHA256SUMS" -o SHA256SUMS
          grep " $asset$" SHA256SUMS | sha256sum -c -
          tar -xzf "$asset"
          sudo install -m 755 lcovmerge /usr/local/bin/lcovmerge
      - run: |
          mkdir -p coverage
          lcovmerge coverage-shards/*.info -o coverage/merged.info
      - uses: actions/upload-artifact@v7
        with:
          name: merged-coverage
          path: coverage/merged.info
```

Adjust action versions and artifact naming to the workflow policy in your repository. The example assumes the
release archive and checksum file have been published. If shards ran in different checkout roots, pass
repeated `--rebase OLD=NEW` options in the merge step.

## GitLab CI

Publish one LCOV artifact per test job, then download the artifacts in a final job. GitLab's built-in
`coverage_report` visualization currently consumes Cobertura or JaCoCo reports, not LCOV; keep the `.info`
artifact for lcov-compatible uploaders or convert it separately. The [GitLab CI
reference](https://docs.gitlab.com/ci/yaml/#artifactsreportscoverage_report) describes the accepted report
formats.

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
    - mkdir -p coverage
    - lcovmerge coverage/*.info -o coverage/merged.info
    - genhtml coverage/merged.info --output-directory coverage/html
  artifacts:
    paths:
      - coverage/merged.info
      - coverage/html/
```

Install lcovmerge in the merge job from a pinned release archive and verify its SHA-256 as shown in the GitHub
Actions example. If you need GitLab's line annotations, convert the data to Cobertura with a compatible
reporting tool and upload that report separately.

## Jenkins

For a single Pipeline run, `stash`/`unstash` can transfer a small set of files between stages. Jenkins
documents stashes as compressed TAR transfers and recommends another artifact mechanism for large transfers.
For large LCOV files, prefer the configured artifact manager or shared workspace/storage instead of routing
multi-gigabyte reports through the controller.

```groovy
pipeline {
  agent none
  stages {
    stage('Test shards') {
      parallel {
        stage('Unit') {
          agent any
          steps {
            sh './ci/test-unit --coverage-output coverage/unit.info'
            stash name: 'unit-coverage', includes: 'coverage/unit.info'
          }
        }
        stage('Integration') {
          agent any
          steps {
            sh './ci/test-integration --coverage-output coverage/integration.info'
            stash name: 'integration-coverage', includes: 'coverage/integration.info'
          }
        }
      }
    }
    stage('Merge coverage') {
      agent any
      steps {
        unstash 'unit-coverage'
        unstash 'integration-coverage'
        sh 'lcovmerge coverage/*.info -o coverage/merged.info'
        archiveArtifacts artifacts: 'coverage/merged.info'
      }
    }
  }
}
```

See Jenkins' [Pipeline artifact steps](https://www.jenkins.io/doc/pipeline/steps/workflow-basic-steps/) and
[archiveArtifacts](https://www.jenkins.io/doc/pipeline/steps/core/).

## CMake and gcov

Compile with GCC or compatible gcov instrumentation, run the test suite, and capture an LCOV tracefile. `lcov`
can capture directly; `gcovr` can also emit LCOV with `--lcov`.

```sh
cmake -S . -B build -DCMAKE_BUILD_TYPE=Debug \
  -DCMAKE_C_FLAGS=--coverage -DCMAKE_CXX_FLAGS=--coverage
  cmake --build build
  ctest --test-dir build --output-on-failure

mkdir -p coverage
lcov --capture --directory build --output-file coverage/gcov.info
lcovmerge coverage/gcov.info -o coverage/merged.info
```

For a gcovr-based collector, one possible flow is:

```sh
mkdir -p coverage
gcovr --root . --lcov coverage/gcovr.info
lcovmerge coverage/gcovr.info -o coverage/merged.info
```

See [lcov](https://github.com/linux-test-project/lcov) and the [gcovr LCOV output
guide](https://gcovr.com/en/stable/output/lcov.html). Capture options depend on compiler and build-tree
layout.

## Jest and Istanbul

Configure Jest to emit LCOV for each shard. Give each shard a distinct output path, upload the files, and
merge them in the aggregation job.

```sh
npx jest --coverage --coverageReporters=lcov --coverageDirectory=coverage/unit
```

If the test runner writes each shard to a separate directory, merge the resulting `lcov.info` files:

```sh
lcovmerge coverage/shard-*/lcov.info -o coverage/merged.info
```

Jest documents the `lcov` reporter in its [`coverageReporters`
configuration](https://jestjs.io/docs/configuration#coveragereporters-arraystring). The exact Istanbul setup
may differ when a project invokes `nyc` directly.

## pytest-cov

pytest-cov can write LCOV directly. In a sharded pipeline, choose one output file per worker rather than
having workers overwrite a shared file.

```sh
mkdir -p coverage
pytest --cov=my_package --cov-report=lcov:coverage/shard-1.info
```

Then merge after CI has downloaded all shard artifacts:

```sh
lcovmerge coverage/shard-*.info -o coverage/merged.info
```

See the pytest-cov [reporting guide](https://pytest-cov.readthedocs.io/en/stable/reporting.html) for LCOV
report output options.

## Mozilla-style worker pipelines

If worker jobs already produce LCOV files, transfer those `.info` artifacts to the final aggregation worker
and use:

```sh
lcovmerge artifacts/coverage/*.info -o coverage/merged.info
```

This replaces only the final merge step for already-generated LCOV. It does not consume raw `.gcda`,
`.profraw`, or other profile data, and it does not replace grcov's collection or conversion features.
Mozilla's [Bug 2070106](https://bugzilla.mozilla.org/show_bug.cgi?id=2070106) describes an aggregation task
killed for memory use and resolved by increasing the worker size. The local grcov benchmark in this project is
not a reproduction of Mozilla's workload.
