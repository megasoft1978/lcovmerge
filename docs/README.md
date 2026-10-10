# Documentation

lcovmerge combines existing LCOV .info files after collection. It does not collect raw coverage or generate reports.

## Choose a guide

- [Quick start and CLI options](USAGE.md) — command syntax, path rewriting, memory/temp settings, and exit codes.
- [CI and exporter recipes](RECIPES.md) — artifact jobs, build systems, and exporters that write LCOV.
- [FAQ and choosing a merge path](FAQ.md) — when a separate merge is useful and which input format to use.
- [Limits and LCOV behavior](LIMITATIONS.md) — record semantics, memory/disk bounds, and platform verification.
- [Migrate from lcov -a](MIGRATING-FROM-LCOV.md) — option mapping and a compare-before-switch checklist.
- [Benchmark report](BENCHMARKS.md) — canonical measurements, method, run counts, and failures.

## For maintainers

- [Architecture](ARCHITECTURE.md)
- [Validation records](validation/SUMMARY.md)
- [Release checklist](RELEASING.md)
