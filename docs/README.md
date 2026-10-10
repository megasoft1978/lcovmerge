# Documentation

lcovmerge combines existing LCOV .info files after collection. An .info file is plain text that records covered lines, functions, and branches. It does not collect raw coverage or generate reports.

## Choose a guide

- [Quick start and CLI options](USAGE.md) — command syntax, path rewriting, memory/temp settings, and exit codes.
- [CI and exporter recipes](RECIPES.md) — the canonical complete GitHub Actions matrix workflow plus build-system and exporter notes.
- [FAQ and choosing a merge path](FAQ.md) — fit, no-fit cases, and what strict lcov failures mean.
- [Limits and LCOV behavior](LIMITATIONS.md) — record semantics, memory/disk bounds, and platform verification.
- [Migrate from lcov -a](MIGRATING-FROM-LCOV.md) — option mapping, same-input compare command, and reversible one-week CI pilot.
- [Benchmark report](BENCHMARKS.md) — canonical measurements, method, run counts, and failures.

## For maintainers

- [Architecture](ARCHITECTURE.md)
- [Validation records](validation/SUMMARY.md)
- [Release checklist](RELEASING.md)
