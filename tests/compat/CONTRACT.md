# LCOV 2.6 oracle characterization

The executable contract is LCOV release commit `a17443e7dbb31b5b7bff6beac32e7b9b02ec3342`, version `2.6-0`. `tools/lcov-oracle-identity.py --strict` checks the pinned package executable and `lcovutil.pm` SHA-256 values from the verified probe bundle, and inspects the installed XS and pure-Perl backends. Run it before trusting results. A changed package hash is reported as an altered/unverified oracle and strict harness runs stop.

`oracle.py --oracle-only` builds tiny tracefiles and an empty explicit `lcovrc` under `.cache/lcov-compat-oracle/`. With `--binary PATH`, it invokes that candidate with `--lcov-compat=2.6`, compares exact exit status, and on success compares normalized tracefiles. Normalization is limited to line endings and order of records within a section. It keeps section order, TN, SF, VER, all DA checksums, modern function leader/alias records, branch coordinates, MC/DC identity/expression, and summaries. It does not normalize diagnostics or conceal differing values. Each invocation writes input hashes, commands, status, outputs and hashes to its workdir manifest.

## Probed findings

- Different `TN` values for the same source remain two independent sections.
- Duplicate identical input sections aggregate counts; this fixture's execution count doubles.
- A source section containing only an uncovered line remains present, with `LF:1` and `LH:0`.
- Branch block IDs are eligible for canonicalization across inputs, but the small case is sensitive to LCOV's branch/line consistency policy; its valid fixture contains a line row for each branch line.
- Modern function encoding is `FNL:<index>,<start>[,<end>]` followed by one or more `FNA:<index>,<count>,<alias>` records. The parser also accepts legacy `FN`/`FNDA`; LCOV output can modernize function records.
- MC/DC encoding is `MCDC:<line>,[U]<groupSize>,<sense>,<taken>,<index>,<expression>`. Its identity includes all fields, especially expression and index. MC/DC defaults off in LCOV; enable it explicitly for the MC/DC fixture.
- With `--checksum`, deliberately incorrect source line checksums fail with a version/checksum diagnostic. Checksums are not disposable formatting.
- A malformed enabled `DA` record fails. A record family disabled by option may be discarded without parsing its payload, per LCOV's tracefile documentation.
- Quoted shell globs passed to `-a` are expanded by LCOV inside the process. The harness passes a quoted-pattern argument and uses valid matching shards.
- Strict inconsistency failure and `--ignore-errors inconsistent` recovery were verified in the separate `root-probes/verified-results.json` bundle; local empty-config behavior is recorded separately by the harness.
- Backend identity in the verified bundle is XS loaded `1` and forced pure Perl `0`; this harness records both in provenance. The pinned package hashes match that bundle on the characterization host.

## Coverage limits and implementation gaps

This is an initial characterization harness, not a complete compatibility claim. The current matrix does not yet cover CLI defaults/flag precedence, repeated output destinations, stdout and gzip, config discovery/`--rc` precedence, include/exclude filters, testcase forgetting, checksum conflict recovery, all malformed record categories, invalid globs, Unicode/byte paths, stdin/list files, parallel/tempdir/memory semantics, keep-going, or genhtml consumer acceptance. The harness does not yet run both backends for every case; recorded backend identity is not a substitute for paired backend results. MC/DC has a valid grammar fixture but broader recovery/duplicate semantics remain unprobed. These remain explicit gaps for the integrating coordinator; no unsupported option should be mapped to a native behavior by assumption.

Run comparator tests with `python3 -m unittest discover -s tests/compat -p 'test_*.py'` and characterization with `python3 tests/compat/oracle.py --oracle-only`.
