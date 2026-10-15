# Changelog

All notable changes to lcovmerge are documented here. This project follows [Keep a
Changelog](https://keepachangelog.com/en/1.1.0/) and [Semantic
Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.2.0] - 2026-10-15

### Changed

- Port the CLI and library to native Rust while retaining established native command-line and tracefile policies, bounded external sorting, and opt-in threaded sorted-input merging. Use locked Cargo builds for all five release targets.
- Add experimental `--lcov-compat=2.6` for the documented LCOV merge surface: configuration, quoted `-a` globs, testcase normalization, function aliases and inferred ends, branch/MC/DC records, checksums, source versions, and diagnostic recovery. Collection, report generation, callbacks, and unsupported settings remain outside this profile; see [the contract](docs/LCOV-PROFILE.md).
- Use bounded sort runs, exact disk-backed indexes and streamed joins for compatibility metadata, including groups larger than memory. Stage sole or mixed stdin at its original producer position. Retain record limits, staged output replacement, and temporary cleanup on success, errors, and catchable POSIX interruption.
- Build portable PGO binaries by default with `make`, caching synthetic training feedback by source and toolchain identity. Retain reproducible-build and binary-size checks. Plain `cargo build --release` remains unprofiled.
- Speed up native partition planning by skipping ordinary complete lines before marker classification; the measured macOS ARM candidate passed all eight predeclared timing comparisons against C 1.1.0 and the prior Rust build. See the [native benchmark report](docs/BENCHMARKS.md#rust-port-comparison).
- Add Rust unit/property tests, structured mutations, CLI fuzzing, pinned XS/pure-Perl conformance, bounded-memory workloads, and Rust AddressSanitizer/ThreadSanitizer CI. Verify downloaded release binaries on all five platforms, with checksums, signatures and attestations.
- Add isolated native and LCOV-profile comparisons with fixed paired sampling, output equivalence, uncertainty intervals, resource metrics and exact binary/input/profile provenance. Preserve every historical measurement and report slower or inconclusive cases; see [benchmarks](docs/BENCHMARKS.md).
- Update the README, website, CLI/man references, architecture and packaging guides for Rust. Default the versioned Action and installers to 1.2.0. Promote the exact measured macOS ARM binary after verifying its source identity and all eight passing comparisons.

### Fixed

- Match profile collector policies for globally required function coverpoints, DA-only zero function summaries, aliases from removed testcases, ghost master functions, inferred function ends, consistency recovery, and the lambda exception. Honor `--no-function-coverage`.
- Preserve negative branch-count recovery as zero, branch expressions and exception kinds, sparse block IDs, source-wide checksum selection, source-version propagation, and separate MC/DC reachability flags and synthesized line counts.
- Recover malformed profile sections without buffering an entire source: retain earlier sections, ignore misplaced records, trim padding, accept KF aliases and suffixed end markers, and drain oversized physical lines within the 1 MiB cap. Reject repeated stdin before reading. Classify recovery errors independently of filenames and function names.
- Interrupt blocked POSIX stdin/stdout operations on caught termination signals and remove staged output and temporary runs. Keep Windows blocking-I/O limitations explicit.
- Make partition interruption tests synchronize with live worker stages, correct Windows fixture/path-identity checks, configure supported Rust CodeQL extraction, and preserve shell build failures. Derive Docker validation filenames from the Cargo version.

## [1.1.0] - 2026-10-12

### Fixed

- Size merge aggregation storage for the maximum source path, text, and extra fields; normalize numeric branch identifiers before merging; and omit testcase names when merged coverage combines conflicting or unnamed testcases.
- Grow input line buffers as needed so the sorted-input path can handle more than 32 small files within its memory budget.
- Reject named non-regular tracefile inputs before they can block worker cancellation, cap external-merge fan-in to available file descriptors, and test the packaged Linux release binary before publication.
- Extend sorted streaming to budget-fit shard counts above 32; a local 128-shard trial was 22.0% faster with lower peak memory and identical output.

### Added

- Add a coverage-guided Clang fuzz target, per-sample benchmark and binary provenance, and list-file input handling in the GitHub Action.
- Exclude an existing Action output file when it matches an input glob.
- Add an XXL local benchmark workload targeting about 20 GB of LCOV input, with disk-space checks, peak-RSS measurements, and a streaming semantic comparison against lcov.

### Documentation

- Clarify lcovmerge's niche for costly LCOV shard merges and add an opt-in, privacy-conscious path for reporting real-workload measurements.
- Display benchmark input sizes, elapsed times, and memory in readable units across the README and site; retain exact measurements in the published JSON record.
- Use the profile-guided host build in the main benchmark presentation and record the lcov-result-merger 6.0.0 XXL measurement with updated toolchain provenance.
- Simplify the README and landing page around a generated, largest-valid-pair benchmark summary and a short install/run path.
- Add an LCOV 2.6 compatibility guide with policy comparisons, scoped validation evidence, aggregate public-CI findings, and local comparison instructions.
- Update public-CI compatibility evidence with the 2026-10-11 rerun on lcovmerge 1.0.2 and its aggregate normalized-difference scope.
- Update Windows platform notes to current CI scope: Windows is verified in GitHub-hosted windows-latest CI: both the MSYS2 UCRT64 GCC build and shipped Zig binary pass the full suite and smoke tests, and separate generated-input benchmark runs are recorded; physical desktops, other Windows versions and real-user reports remain unverified, Windows CI runs no LCOV comparator, and large-dataset timings are disk-bound CI numbers.

## [1.0.2] - 2026-10-10

### Fixed

- Recover a tracefile line where `end_of_record` is joined directly to `SF:`/`KF:` (seen in a public CI artifact): the following rows now go to the new source instead of the previous one, with a warning.

### Changed

- Make temporary-run errors name `--tmpdir` and state that it must exist and be writable; output temporary-file errors include the OS reason and parent-directory hint.
- Include unmatched file patterns in GitHub Action no-match errors.
- Document Windows host-verification limits and add JavaScript monorepo and temporary-directory
  examples to the site usage guide.

### Added

- Add a local S/M benchmark reproduction bundle with exact commands, resource measurements, and normalized LCOV comparison.
- Add the opt-in `tools/gen-lcov.py --lcov-valid` mode, which emits function starts on in-range `DA` lines while preserving the existing default and benchmark-compatible output.

## [1.0.1] - 2026-10-10

### Added

- Add Linux and Windows benchmark workflows and publish the recorded hosted-runner results.
- Add a Scoop installation smoke test to CI.
- Refresh the README and site with a category-first introduction, and add an FAQ, migration guide, and CI recipes.
- Record soak validation with `SOAK=1`, five million fuzz executions, and AddressSanitizer stress.

### Changed

- Build the Windows release binary with `-O2` instead of `-Oz`; on windows-latest, the Zig `-O2` build took 0.619 s (S) and 8.31 s (M) versus 0.743 s and 9.51 s for the `-Oz` build in an earlier run, and matched an MSYS2 gcc `-O2` build within 3% in the same run (different runner instances; I/O-bound L is unchanged by optimization level). The measured executable is 148,992 bytes, and the per-executable size budget is raised from 150,000 to 160,000 bytes.
- Pin GitHub Action examples to a released tag.

### Fixed

- Correct the man page's `--tmpdir` example.

No CLI behavior changes in this release.

## [1.0.0] - 2026-10-09

### Added

- Streaming LCOV tracefile merge with bounded record memory and temporary-file sorting.
- Direct streaming merge for already-sorted regular-file inputs, with external-sort fallback when ordering or input constraints require it.
- Standard input/output, input list files, configurable temporary directory, and memory limit.
- Source path rebasing, prefix stripping, and include/exclude filtering.
- Optional branch/function record handling, strict checksum checks, unknown-record warnings, statistics, and
quiet/verbose modes.
- Linux, macOS, and Windows release targets and command-line documentation.
- Interruption handling: on POSIX, handle SIGINT, SIGTERM, and SIGHUP cooperatively and remove temporary sort runs on
  caught interruption. On POSIX, lcovmerge ignores SIGPIPE so a closed pipe is reported as EPIPE
  and follows normal failure cleanup. Windows has no equivalent interruption guarantee, and
  stdout output cannot be rolled back after a later signal or write failure.
- Windows hardening: extended-length paths, collision-checked temporary files, console Ctrl handler, and broken-pipe stdin treated as end of input.
- Validation on real C/C++ projects (see `docs/validation/real-projects.md`) and expanded boundary, randomized differential, and fuzz-seed tests.
