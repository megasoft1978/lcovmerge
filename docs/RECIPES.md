# CI and exporter recipes

lcovmerge reads existing LCOV `.info` files. Run it after test jobs have exported coverage; it does not collect raw coverage or generate reports. Export each shard to a distinct file.

## GitHub Actions

This is the canonical complete matrix workflow, mirrored in [README](../README.md#github-actions-matrix). Replace the example test patterns and test command with your project's. Each matrix job must upload one uniquely named artifact containing the `.info` file at the path shown.

```yaml
name: coverage

on: [push, pull_request]

permissions:
  contents: read

jobs:
  test-shard:
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        include:
          - shard: unit
            pattern: "test_unit*.py"
          - shard: integration
            pattern: "test_integration*.py"
    env:
      COVERAGE_FILE: .coverage-${{ matrix.shard }}
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
      - uses: actions/setup-python@v7
        with:
          python-version: "3.12"
      - run: python -m pip install coverage
      - name: Run tests and export LCOV
        run: |
          mkdir -p coverage/shards
          python -m coverage run -m unittest discover -s tests -p "${{ matrix.pattern }}"
          python -m coverage lcov -o "coverage/shards/${{ matrix.shard }}.info"
      - uses: actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9 # v7.0.2
        with:
          name: coverage-${{ matrix.shard }}
          path: coverage/shards/${{ matrix.shard }}.info
          if-no-files-found: error

  merge:
    needs: test-shard
    runs-on: ubuntu-latest
    steps:
      - uses: actions/download-artifact@9000827ccba6bdab643e8b6fd33ac0654aef8333 # v8.0.2
        with:
          pattern: coverage-*
          path: coverage/shards
          merge-multiple: true
      - name: Merge LCOV shards
        uses: megasoft1978/lcovmerge@v1.0.2 # For immutable pinning, replace the tag with the reviewed release commit SHA.
        with:
          files: coverage/shards/*.info
          output: coverage/merged.info
          mem-limit: 256M
      - uses: actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9 # v7.0.2
        with:
          name: merged-coverage
          path: coverage/merged.info
          if-no-files-found: error
```

`download-artifact` flattens the shard files into `coverage/shards/`. The 256M setting is an example record-memory budget, not an RSS cap; sorting also needs temporary disk. The action verifies its release archive against SHA256SUMS. For immutable pinning, replace `@v1.0.2` with a reviewed full commit SHA and keep `# v1.0.2` as its label. Feed the merged file to your existing report step.

See GitHub's [matrix guide](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/run-job-variations), [upload action](https://github.com/actions/upload-artifact), and [download action](https://github.com/actions/download-artifact).

## GitLab CI

This example uses Python Coverage.py. The artifact merge and release-download shell block starts with `set -eu` so a failed download or checksum check stops before extraction.

```yaml
stages: [test, report]

test:
  image: python:3.12
  stage: test
  parallel:
    matrix:
      - SHARD: [unit, integration]
  script:
    - python -m pip install coverage
    - mkdir -p coverage/shards
    - COVERAGE_FILE=".coverage-$SHARD" python -m coverage run -m unittest discover -s tests -p "test_$SHARD*.py"
    - COVERAGE_FILE=".coverage-$SHARD" python -m coverage lcov -o "coverage/shards/$SHARD.info"
  artifacts:
    paths:
      - coverage/shards/$SHARD.info

merge-coverage:
  image: python:3.12
  stage: report
  needs:
    - job: test
      artifacts: true
  before_script:
    - |
      set -eu
      apt-get update
      apt-get install -y --no-install-recommends curl lcov
      asset=lcovmerge-1.0.2-linux-x86_64.tar.gz
      base=https://github.com/megasoft1978/lcovmerge/releases/download/v1.0.2
      curl -fL "$base/$asset" -o "$asset"
      curl -fL "$base/SHA256SUMS" -o SHA256SUMS
      awk -v name="$asset" '$2 == name { count++; print } END { if (count != 1) exit 1 }' SHA256SUMS > "$asset.sha256"
      sha256sum -c "$asset.sha256"
      tar -xzf "$asset"
  script:
    - |
      set -eu
      mkdir -p coverage
      ./lcovmerge coverage/shards/*.info -o coverage/merged.info
      genhtml coverage/merged.info --output-directory coverage/html
  artifacts:
    paths:
      - coverage/merged.info
      - coverage/html/
```

Change the test patterns to match files in your checkout. Hosted GitLab CI execution was not tested in the documentation audit. See GitLab's [artifact guide](https://docs.gitlab.com/ci/jobs/job_artifacts/) and [parallel matrix reference](https://docs.gitlab.com/ci/yaml/#parallelmatrix).

## Jenkins

The agent needs Python with Coverage.py, `curl`, `tar`, and `sha256sum`; install `genhtml` if the final stage should render HTML. `set -eu` makes the archive verification and extraction block stop on failure.

```groovy
pipeline {
  agent { label 'linux-python' }
  stages {
    stage('Test shards') {
      parallel {
        stage('Unit') {
          steps {
            sh '''
              set -eu
              python3 -m pip install coverage
              mkdir -p coverage/shards
              COVERAGE_FILE=.coverage-unit python3 -m coverage run -m unittest discover -s tests -p 'test_unit*.py'
              COVERAGE_FILE=.coverage-unit python3 -m coverage lcov -o coverage/shards/unit.info
            '''
            stash name: 'lcov-unit', includes: 'coverage/shards/unit.info'
          }
        }
        stage('Integration') {
          steps {
            sh '''
              set -eu
              python3 -m pip install coverage
              mkdir -p coverage/shards
              COVERAGE_FILE=.coverage-integration python3 -m coverage run -m unittest discover -s tests -p 'test_integration*.py'
              COVERAGE_FILE=.coverage-integration python3 -m coverage lcov -o coverage/shards/integration.info
            '''
            stash name: 'lcov-integration', includes: 'coverage/shards/integration.info'
          }
        }
      }
    }
    stage('Merge') {
      steps {
        unstash 'lcov-unit'
        unstash 'lcov-integration'
        sh '''
          set -eu
          asset=lcovmerge-1.0.2-linux-x86_64.tar.gz
          base=https://github.com/megasoft1978/lcovmerge/releases/download/v1.0.2
          curl -fL "$base/$asset" -o "$asset"
          curl -fL "$base/SHA256SUMS" -o SHA256SUMS
          awk -v name="$asset" '$2 == name { count++; print } END { if (count != 1) exit 1 }' SHA256SUMS > "$asset.sha256"
          sha256sum -c "$asset.sha256"
          tar -xzf "$asset"
          ./lcovmerge coverage/shards/unit.info coverage/shards/integration.info -o coverage/merged.info
          genhtml coverage/merged.info --output-directory coverage/html
        '''
        archiveArtifacts artifacts: 'coverage/merged.info,coverage/html/**', fingerprint: true
      }
    }
  }
}
```

Jenkins `stash` is intended for small files in one pipeline. Hosted Jenkins syntax and runtime were not tested in the documentation audit. See the [stash/unstash steps](https://www.jenkins.io/doc/pipeline/steps/workflow-basic-steps/) and [archiveArtifacts](https://www.jenkins.io/doc/pipeline/steps/core/).

## CMake with gcov

Compile each isolated test shard with GCC coverage instrumentation, run its tests, then capture the `.gcda` data to LCOV with `lcov`. Give each CI job a different shard name. GCC documents `--coverage` in its [instrumentation guide](https://gcc.gnu.org/onlinedocs/gcc/Instrumentation-Options.html).

```sh
set -eu
cmake -S . -B build -DCMAKE_BUILD_TYPE=Debug \
  -DCMAKE_C_FLAGS=--coverage -DCMAKE_CXX_FLAGS=--coverage
cmake --build build
ctest --test-dir build --output-on-failure
mkdir -p coverage/shards
SHARD=1
lcov --capture --directory build --output-file "coverage/shards/shard-$SHARD.info"
```

After each shard uploads its `.info` file, merge them in the report job:

```sh
set -eu
lcovmerge coverage/shards/shard-*.info -o coverage/merged.info
genhtml coverage/merged.info --output-directory coverage/html
```

Use separate build directories so each shard captures only its own counters. See the [LCOV project documentation](https://github.com/linux-test-project/lcov).

## Bazel

A single `bazel coverage` invocation can already create a combined LCOV report. Use that report directly when it covers the tests you need:

```sh
set -eu
bazel coverage --combined_report=lcov //...
report="$(bazel info output_path)/_coverage/_coverage_report.dat"
genhtml "$report" --output-directory coverage/html
```

If independent Bazel jobs each exported a separate LCOV file and you need one artifact, merge after downloading them:

```sh
set -eu
lcovmerge coverage/bazel-shards/bazel-shard-*.info -o coverage/merged.info
```

The Bazel commands were not run in the documentation audit because Bazel was unavailable. See the [Bazel coverage guide](https://bazel.build/docs/coverage).

## Export to LCOV, then merge

Use the exporter that matches your raw data. lcovmerge reads the LCOV files these commands create; it does not read raw profiles or execution data.

### Rust with cargo-llvm-cov

```sh
set -eu
mkdir -p coverage/shards
cargo llvm-cov --lcov --output-path coverage/shards/rust.info
```

This command was not verified in the documentation audit because `cargo-llvm-cov` was unavailable. See the [cargo-llvm-cov LCOV instructions](https://github.com/taiki-e/cargo-llvm-cov#lcov-output).

### Rust with grcov

This source-based coverage example requires `rustup`, `cargo`, `grcov`, and the compatible Rust LLVM tools component:

```sh
set -eu
mkdir -p coverage/shards
rustup component add llvm-tools
export RUSTFLAGS="-Cinstrument-coverage"
cargo build
LLVM_PROFILE_FILE="coverage/rust-%p-%m.profraw" cargo test
grcov . --binary-path ./target/debug/ -s . -t lcov --branch --ignore-not-existing --ignore "/*" -o coverage/shards/rust.info
```

The source-based example was not verified in the documentation audit because the required compatible `llvm-tools` component was unavailable. See the [grcov Rust example](https://github.com/mozilla/grcov#example-how-to-generate-source-based-coverage-for-a-rust-project).

### Clang with llvm-profdata and llvm-cov

These commands assume `./build/test-suite` was compiled for source-based coverage and test jobs saved raw profiles under `coverage/profiles/`. LLVM documents raw-profile merging and LCOV export in the [llvm-profdata guide](https://llvm.org/docs/CommandGuide/llvm-profdata.html) and [llvm-cov guide](https://llvm.org/docs/CommandGuide/llvm-cov.html):

```sh
set -eu
mkdir -p coverage/profiles coverage/shards
LLVM_PROFILE_FILE="coverage/profiles/shard-%p-%m.profraw" ./build/test-suite
llvm-profdata merge -sparse coverage/profiles/*.profraw -o coverage/merged.profdata
llvm-cov export -format=lcov -instr-profile=coverage/merged.profdata ./build/test-suite > coverage/shards/clang.info
```

### JavaScript with c8 or nyc

Run one exporter from the monorepo root. Use either c8 or nyc for a package, not both. Keep package-specific output directories and the coverage working directory at the monorepo root so `SF:` records retain package-qualified paths.

For c8:

```sh
set -eu
mkdir -p coverage/packages/a
npx c8 --cwd "$PWD" --reporter=lcov --reports-dir coverage/packages/a npm --prefix packages/a test
```

Or use nyc instead:

```sh
set -eu
mkdir -p coverage/packages/a
npx nyc --cwd "$PWD" --reporter=lcov --report-dir coverage/packages/a npm --prefix packages/a test
```

Repeat the chosen command for each package with a separate report directory, then merge the exported files:

```sh
set -eu
lcovmerge coverage/packages/*/lcov.info -o coverage/merged.info
```

If two packages emit the same relative `SF:` path, such as `SF:src/math.js`, lcovmerge treats those records as the same source and combines them. It cannot infer that identical paths refer to different files. Inspect the output `SF:` paths before reporting. If nyc's raw JSON data is still available, consider its [`nyc merge` path](https://github.com/istanbuljs/nyc#what-about-nyc-merge) before exporting LCOV.

### Python with Coverage.py

Run each shard with its own data file, then export it to LCOV. If you still have raw data from all shards, use `coverage combine` before exporting:

```sh
set -eu
mkdir -p coverage/shards
COVERAGE_FILE=.coverage-unit coverage run -m pytest
COVERAGE_FILE=.coverage-unit coverage lcov -o coverage/shards/python.info
```

See Coverage.py's [LCOV reporting command](https://coverage.readthedocs.io/en/latest/commands/cmd_lcov.html) and [combine command](https://coverage.readthedocs.io/en/latest/commands/cmd_combine.html).

### Go with gcov2lcov

Run Go's coverage command, then convert its profile to LCOV:

```sh
set -eu
mkdir -p coverage/shards
go test -coverprofile=coverage/go.out ./...
gcov2lcov -infile=coverage/go.out -outfile=coverage/shards/go.info
```

Install gcov2lcov following its [upstream README](https://github.com/jandelgado/gcov2lcov#installation).

### Java with JaCoCo

If JaCoCo produced execution data and the JaCoCo CLI, classes, and LCOV converter are available, convert the execution data to XML and then to LCOV:

```sh
set -eu
mkdir -p coverage/shards
java -jar jacococli.jar report coverage/jacoco.exec --classfiles build/classes --xml coverage/jacoco.xml
xml2lcov --output coverage/shards/java.info --source-directory src coverage/jacoco.xml
```

The lcov project's `jacoco2lcov` wrapper also needs the JaCoCo CLI jar location. Include `--jar`; the documented wrapper invocation without it fails when the jar is not otherwise configured:

```sh
set -eu
jacoco2lcov --jar jacococli.jar --output coverage/shards/java.info --source-directory src --classpath build/classes coverage/jacoco.exec
```

The audit verified the XML plus `xml2lcov` path and the wrapper with `--jar`. See the lcov project's [JaCoCo instructions](https://github.com/linux-test-project/lcov#java-code-using-jacoco) and [tool list](https://github.com/linux-test-project/lcov#included-files).

## Build from source

From a checkout of the repository, the standard build command creates `bin/lcovmerge` with the project's strict compiler warnings:

```sh
make
```

Run `make test` when validating a local change.

## Release archives and provenance

Release archives and `SHA256SUMS` are published on the [release page](https://github.com/megasoft1978/lcovmerge/releases). For Linux and macOS installation commands, see the [Usage guide](USAGE.md#install). The [Homebrew tap](https://github.com/megasoft1978/homebrew-tap) also has a formula; Homebrew installation could not be verified in the local audit because `brew` stopped before formula lookup while trying to create a protected lock. The Scoop package was not installed on Windows in that audit. Windows runtime verification is pending a passing Windows CI run; see [limitations](LIMITATIONS.md#verification-status).

To verify build provenance manually, use a GitHub CLI version with `gh attestation verify` support. Cosign needs a writable Sigstore trust-root cache or a supplied trust-root file. See the [GitHub CLI attestation guide](https://cli.github.com/manual/gh_attestation_verify) and [Cosign verification guide](https://docs.sigstore.dev/cosign/verifying/verify/).

## Docker

The container writes into the mounted working directory. The temporary directory must exist and have available disk space:

```sh
set -eu
mkdir -p coverage

docker run --rm -v "$PWD:/work" -w /work ghcr.io/megasoft1978/lcovmerge:v1.0.2 \
  --tmpdir /tmp coverage/shard-*.info -o coverage/merged.info
```

The v1.0.2 arm64 container command passed in the local audit; other container platforms were not exercised there. See the [container registry](https://github.com/megasoft1978/lcovmerge/pkgs/container/lcovmerge).

## Report and upload consumers

lcovmerge produces one LCOV `.info` file. Pass that file to `genhtml` or to an uploader configured for LCOV. Some report and upload tools accept multiple input files directly; if your current command already handles the shards, skip the separate merge. A consumer that expects another format needs its matching exporter or converter first.

## Common after-export merge

After exported files have been downloaded into one job, merge them and pass the result to the existing report command:

```sh
set -eu
lcovmerge coverage/shards/*.info -o coverage/merged.info
genhtml coverage/merged.info --output-directory coverage/html
```
