# Changelog

All notable changes to lcovmerge are documented here. This project follows [Keep a
Changelog](https://keepachangelog.com/en/1.1.0/) and [Semantic
Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-10-09

### Added

- Streaming LCOV tracefile merge with bounded record memory and temporary-file sorting.
- Direct streaming merge for already-sorted regular-file inputs, with external-sort fallback when ordering or input constraints require it.
- Standard input/output, input list files, configurable temporary directory, and memory limit.
- Source path rebasing, prefix stripping, and include/exclude filtering.
- Optional branch/function record handling, strict checksum checks, unknown-record warnings, statistics, and
quiet/verbose modes.
- Linux, macOS, and Windows release targets and command-line documentation.
