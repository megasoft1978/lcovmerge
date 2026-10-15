# Project direction

## Goal

Make lcovmerge a dependable, easy-to-evaluate option for the final merge of exported LCOV `.info` shards when a local consumer needs one file and the existing merge is a measured cost. Keep the project focused on that handoff. Collection, raw profile merging, report generation, and coverage-service aggregation belong to the tools that already provide them.

## What is established

- The native Rust CLI and library preserve established merge behavior. Rust unit/property tests, CLI regressions, pinned LCOV profile oracles, and separate Rust benchmark reports record the current validation scope. Historical measurements retain their original provenance.
- Release artifacts, checksums, attestations, a GitHub Action, and container packaging are implemented.
- Generated large-input benchmarks and project-derived compatibility checks are documented with their methods and limits.
- Synthetic exporter handoffs have been exercised for Coverage.py, Go via `gcov2lcov`, and c8.
- The README, decision guide, website, and limitations explain when the tool fits and when an existing native or service-side merge is the better choice.

These are repository validation results. They do not establish adoption, typical production savings, compatibility with every exporter configuration, or install success for every package route.

## Next phase: validate adoption

1. **Get independent workloads.** Invite a small set of maintainers with separate LCOV artifacts and a local merge step to run the privacy-conscious measurement procedure in [REAL-WORKLOADS.md](REAL-WORKLOADS.md). Seek varied shard counts, input sizes, CI systems, and exporters.
2. **Check the outcome, not just speed.** On identical inputs, compare the current merger and lcovmerge, verify normalized records, and pass both through the same report or upload consumer. Capture path rewriting needs, failures, disk use, elapsed time, and peak RSS.
3. **Publish anonymized evidence with consent.** Report only reviewed aggregate measurements. Never ask for private `.info` files, source paths, source code, or coverage values tied to identifiable files.
4. **Audit the live adoption paths.** From a clean environment, verify release archives and checksums, Action examples, Pages routes, and package/container install instructions. Record any route that cannot be independently checked.
5. **Choose the next engineering investment from evidence.** Prioritize a repeatable production bottleneck or compatibility issue users report. If no repeated need appears, keep the feature set stable and focus on maintenance and clear documentation.

## Website and discovery work

- Keep the homepage explicit about the problem, the fit test, the Action path, installation, and the benchmark caveats.
- Maintain useful task pages for merging LCOV shards in CI, choosing between `lcov -a`, native profile merging, service aggregation, and a local merger, and validating a reversible migration.
- Keep page titles, descriptions, sitemap entries, canonical URLs, and links aligned with the pages actually published under `docs/site/`.
- Use Search Console data if the owner has access; review queries and landing-page impressions before deciding which guide to expand.
- Avoid generic SEO pages or claims of typical savings until independent workload evidence supports them.

## Owner follow-up

The repository can prepare the docs, website, and measurement process. The owner will need to recruit external workload contributors, review any shared measurements, configure Search Console if desired, verify live hosting/package accounts, and decide whether to publish or promote the project.
