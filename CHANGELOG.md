# Changelog

All notable changes to lcovmerge are documented here. This project follows [Keep a
Changelog](https://keepachangelog.com/en/1.1.0/) and [Semantic
Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- Recover a tracefile line where `end_of_record` is joined directly to `SF:`/`KF:` (seen in a public CI artifact): the following rows now go to the new source instead of the previous one, with a warning.

### Changed

- Make temporary-run errors name `--tmpdir` and state that it must exist and be writable; output temporary-file errors include the OS reason and parent-directory hint.
- Include unmatched file patterns in GitHub Action no-match errors.
- Clarify pending Windows host verification and add JavaScript monorepo and temporary-directory
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
