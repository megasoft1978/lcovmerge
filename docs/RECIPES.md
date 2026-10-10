# CI and exporter recipes

lcovmerge runs after a collector or exporter has written LCOV tracefiles. Each merge command expects one or
more valid `.info` files in the named directory. The CI samples use Python `unittest` as a concrete test
runner; change the test filename patterns to match your suite. They assume `genhtml` is available if you render
HTML in the final job.

## GitHub Actions matrix

This workflow runs two test patterns in separate jobs, uploads one LCOV file per job, then merges the
downloaded artifacts. For another test runner, replace the test and export commands with commands that write
one distinct `.info` file per matrix value.

```yaml
name: coverage

on: [push, pull_request]

jobs:
  test-shard:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        include:
          - shard: unit
            pattern: "test_unit*.py"
          - shard: integration
            pattern: "test_integration*.py"
    env:
      COVERAGE_FILE: .coverage-${{ matrix.shard }}
    steps:
      - uses: actions/checkout@v7
      - uses: actions/setup-python@v7
        with:
          python-version: "3.12"
      - run: python -m pip install coverage
      - run: |
          mkdir -p coverage/shards
          python -m coverage run -m unittest discover -s tests -p "${{ matrix.pattern }}"
          python -m coverage lcov -o "coverage/shards/${{ matrix.shard }}.info"
      - uses: actions/upload-artifact@v7
        with:
          name: coverage-${{ matrix.shard }}
          path: coverage/shards/${{ matrix.shard }}.info

  merge:
    needs: test-shard
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: actions/download-artifact@v8
        with:
          pattern: coverage-*
          path: coverage/shards
          merge-multiple: true
      - name: Merge LCOV
        uses: megasoft1978/lcovmerge@v1.0.1
        with:
          files: coverage/shards/*.info
          output: coverage/merged.info
          mem-limit: 256M
      - run: sudo apt-get update && sudo apt-get install -y lcov
      - run: genhtml coverage/merged.info --output-directory coverage/html
```

Change the two test patterns to names that exist in your checkout. Give each upload a distinct artifact name;
the download step merges their contents into `coverage/shards/`. See GitHub's [matrix guide](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/run-job-variations)
and the [upload](https://github.com/actions/upload-artifact) and
[download](https://github.com/actions/download-artifact) action documentation.

## GitLab CI matrix

Each matrix job uploads a uniquely named tracefile. The merge job downloads artifacts from all instances of
`test`:

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
    - apt-get update
    - apt-get install -y --no-install-recommends curl lcov
    - asset=lcovmerge-1.0.1-linux-x86_64.tar.gz
    - base=https://github.com/megasoft1978/lcovmerge/releases/download/v1.0.1
    - curl -fL "$base/$asset" -o "$asset"
    - curl -fL "$base/SHA256SUMS" -o SHA256SUMS
    - grep " $asset$" SHA256SUMS | sha256sum -c -
    - tar -xzf "$asset"
  script:
    - mkdir -p coverage
    - ./lcovmerge coverage/shards/*.info -o coverage/merged.info
    - genhtml coverage/merged.info --output-directory coverage/html
  artifacts:
    paths:
      - coverage/merged.info
      - coverage/html/
```

The patterns must match files in your checkout. The merge job installs lcov for `genhtml`. See GitLab's
[job artifact guide](https://docs.gitlab.com/ci/jobs/job_artifacts/) and
[parallel:matrix reference](https://docs.gitlab.com/ci/yaml/#parallelmatrix).

## Jenkins Pipeline

The agent needs Python with Coverage.py, curl, tar, and sha256sum. Install `genhtml` if the final stage should
render HTML. The two test stages stash distinct LCOV files; the merge stage unstashes both:

```groovy
pipeline {
  agent { label 'linux-python' }
  stages {
    stage('Test shards') {
      parallel {
        stage('Unit') {
          steps {
            sh '''
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
          asset=lcovmerge-1.0.1-linux-x86_64.tar.gz
          base=https://github.com/megasoft1978/lcovmerge/releases/download/v1.0.1
          curl -fL "$base/$asset" -o "$asset"
          curl -fL "$base/SHA256SUMS" -o SHA256SUMS
          grep " $asset$" SHA256SUMS | sha256sum -c -
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

Jenkins `stash` is intended for small files in one pipeline. Use an artifact manager or shared storage for
large reports. See the [stash/unstash steps](https://www.jenkins.io/doc/pipeline/steps/workflow-basic-steps/)
and [archiveArtifacts](https://www.jenkins.io/doc/pipeline/steps/core/).

## CMake with gcov

Compile each isolated test shard with GCC coverage instrumentation, run its tests, then capture the shard's
`.gcda` data to LCOV with lcov. Set `SHARD` to a different value in each CI job so output names stay distinct.
GCC documents `--coverage` in its
[instrumentation guide](https://gcc.gnu.org/onlinedocs/gcc/Instrumentation-Options.html).

```sh
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
lcovmerge coverage/shards/shard-*.info -o coverage/merged.info
genhtml coverage/merged.info --output-directory coverage/html
```

Use isolated build directories so each shard captures only its own counters. See the
[LCOV project documentation](https://github.com/linux-test-project/lcov).

## Bazel

Bazel's `coverage` command creates a combined LCOV report. Use that report directly when one Bazel invocation
already covers the tests you need:

```sh
bazel coverage --combined_report=lcov //...
report="$(bazel info output_path)/_coverage/_coverage_report.dat"
genhtml "$report" --output-directory coverage/html
```

If independent Bazel jobs have each exported a separate LCOV file and you need one artifact, merge those files
after downloading them:

```sh
lcovmerge coverage/bazel-shards/bazel-shard-*.info -o coverage/merged.info
```

Bazel does not need lcovmerge for the combined report it already creates. See the
[Bazel coverage guide](https://bazel.build/docs/coverage).

## Export to LCOV, then merge

Use the exporter that matches your raw data. Run the commands below only after the collector has produced the
required raw profile or execution data. lcovmerge reads LCOV tracefiles, not these raw formats.

### Rust with cargo-llvm-cov

`cargo llvm-cov` can run the Rust coverage workflow and write LCOV:

```sh
mkdir -p coverage/shards
cargo llvm-cov --lcov --output-path coverage/shards/rust.info
```

See the [cargo-llvm-cov LCOV instructions](https://github.com/taiki-e/cargo-llvm-cov#lcov-output).

### Rust with grcov

This source-based coverage example assumes `rustup`, `cargo`, and `grcov` are installed:

```sh
mkdir -p coverage/shards
rustup component add llvm-tools
export RUSTFLAGS="-Cinstrument-coverage"
cargo build
LLVM_PROFILE_FILE="coverage/rust-%p-%m.profraw" cargo test
grcov . --binary-path ./target/debug/ -s . -t lcov --branch --ignore-not-existing --ignore "/*" -o coverage/shards/rust.info
```

See the [grcov Rust example](https://github.com/mozilla/grcov#example-how-to-generate-source-based-coverage-for-a-rust-project).

### Clang with llvm-profdata and llvm-cov

These commands assume `./build/test-suite` was compiled for source-based coverage and each test job stored its
raw profile in `coverage/profiles/`. LLVM documents raw-profile merging and LCOV export in the
[llvm-profdata guide](https://llvm.org/docs/CommandGuide/llvm-profdata.html) and
[llvm-cov guide](https://www.llvm.org/docs/CommandGuide/llvm-cov.html):

```sh
mkdir -p coverage/profiles coverage/shards
LLVM_PROFILE_FILE="coverage/profiles/shard-%p-%m.profraw" ./build/test-suite
llvm-profdata merge -sparse coverage/profiles/*.profraw -o coverage/merged.profdata
llvm-cov export -format=lcov -instr-profile=coverage/merged.profdata ./build/test-suite > coverage/shards/clang.info
```

### JavaScript with c8 or nyc

Install c8 or nyc in the project first. These commands assume the package has an `npm test` script:

```sh
mkdir -p coverage/c8 coverage/nyc coverage/shards
npx c8 --reporter=lcov --reports-dir=coverage/c8 npm test
mv coverage/c8/lcov.info coverage/shards/c8.info
npx nyc --reporter=lcov --report-dir=coverage/nyc npm test
mv coverage/nyc/lcov.info coverage/shards/nyc.info
```

Use a separate output directory for each test shard. If all raw nyc JSON data is available, see nyc's
[`nyc merge` documentation](https://github.com/istanbuljs/nyc#what-about-nyc-merge) before exporting LCOV.

### Python with Coverage.py

Run each shard with its own data file, then export that file to LCOV. If you still have the raw data from all
shards, use `coverage combine` before `coverage lcov` instead of exporting each shard:

```sh
mkdir -p coverage/shards
COVERAGE_FILE=.coverage-unit coverage run -m pytest
COVERAGE_FILE=.coverage-unit coverage lcov -o coverage/shards/python.info
```

Coverage.py documents the [LCOV reporting command](https://coverage.readthedocs.io/en/latest/commands/cmd_lcov.html)
and [combine command](https://coverage.readthedocs.io/en/latest/commands/cmd_combine.html).

### Go with gcov2lcov

Run Go's coverage command, then convert its profile to LCOV:

```sh
mkdir -p coverage/shards
go test -coverprofile=coverage/go.out ./...
gcov2lcov -infile=coverage/go.out -outfile=coverage/shards/go.info
```

Install gcov2lcov following its [upstream README](https://github.com/jandelgado/gcov2lcov#installation).

### Java with JaCoCo

If JaCoCo has produced execution data and the JaCoCo CLI, classes, and LCOV converter are available, the lcov
project documents these conversion commands:

```sh
mkdir -p coverage/shards
java -jar jacococli.jar report coverage/jacoco.exec --classfiles build/classes --xml coverage/jacoco.xml
xml2lcov --output coverage/shards/java.info --source-directory src coverage/jacoco.xml
```

The lcov project also documents the `jacoco2lcov` wrapper:

```sh
jacoco2lcov --output coverage/shards/java.info --source-directory src --classpath build/classes coverage/jacoco.exec
```

See the lcov project's [JaCoCo instructions](https://github.com/linux-test-project/lcov#java-code-using-jacoco)
and [tool list](https://github.com/linux-test-project/lcov#included-files).

## Common after-export merge

After exported artifacts have been downloaded into one job:

```sh
lcovmerge coverage/shards/*.info -o coverage/merged.info
genhtml coverage/merged.info --output-directory coverage/html
```

This produces one LCOV tracefile and then uses the report tool. If your consumer accepts the shard files
directly, skip the merge step.
