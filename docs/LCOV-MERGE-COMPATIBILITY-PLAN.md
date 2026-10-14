# LCOV merge compatibility plan

Build an opt-in Rust replacement for the supported `lcov -a` merge workflow, pinned to LCOV 2.6. Preserve lcovmerge's existing behavior and speed for callers that use the native mode. Release compatibility claims only for the command, configuration, record, and failure cases covered by conformance tests.

This plan covers tracefile aggregation. Coverage collection, counter reset, extraction/removal operations, HTML generation, converters, and arbitrary Perl callbacks remain outside this project's merge contract. Supporting LCOV's entire tool suite would require a separate plan.

## Target and command contract

The reference release is [LCOV v2.6](https://github.com/linux-test-project/lcov/releases/tag/v2.6), commit `a17443e7dbb31b5b7bff6beac32e7b9b02ec3342`. The installed reference identifies itself as `2.6-0`. Record the release commit, package modifications, executable/library hashes, runtime, configuration, and backend together; the version string alone does not define an oracle.

Use the proposed selector `--lcov-compat=2.6`:

```sh
lcovmerge --lcov-compat=2.6 -a 'coverage/shard-*.info' -o coverage/merged.info
lcovmerge --lcov-compat=2.6 -a first.info -a second.info > merged.info
```

LCOV already has a different `--compat` option. Keep that name available for its upstream meaning rather than repurposing it for our profile selector. Native invocation and the existing Rust `Options` construction must retain their current meaning.

The mode must resolve its defaults before parsing mode-dependent arguments, irrespective of selector position. It must identify itself as lcovmerge with an LCOV merge profile; its help/version output must not impersonate the upstream executable. No automatic installation over the `lcov` executable is planned.

| Surface | Initial supported profile and acceptance requirement |
| --- | --- |
| Repeated `-a` / `--add-tracefile` | Required. Expand quoted patterns inside the program; characterize matching, duplicate inputs, unmatched patterns, and processing order against the oracle. |
| `-o` / `--output-file` | Required. Omission selects stdout. Probe repeated destinations, `-`, gzip suffixes, and failure behavior before fixing the exact contract. |
| Coverage toggles | Required for supported record families. LCOV defaults are functions enabled, branches and MC/DC disabled. Native defaults stay independent. Test positive/negative combinations and precedence rather than assuming last-option-wins. |
| Quiet, verbose, help, version | Required. Define stdout/stderr channels, precedence, successful help/version exits, and profile identification. Exact diagnostic prose is not a conformance requirement. |
| Include/exclude patterns | Required common merge controls. Verify wildcard rules, ordering, source resolution, and case sensitivity per platform. Do not reuse native filtering until these rules agree. |
| Testcase and checksum controls | Required: preserve testcase identity, support characterized forgetting of test names and checksum controls, and specify conflicts and source-file requirements. |
| Configuration and `--rc` | Implement an explicit merge-setting support matrix and tested precedence/discovery. Honor supported settings; reject merge-relevant settings that cannot be honored. Known capture-only settings may be ignored only where upstream merging also ignores them. |
| `--parallel`, `--tempdir` | Support after validating accepted values, automatic worker selection, and resource semantics. Worker count is subject to documented operating limits, while results must meet the profile contract. |
| `--memory` | Characterize upstream units and scheduling semantics first. It must not become a misleading alias for the native record-storage budget. Keep `--mem-limit` documented as a lcovmerge resource extension. |
| Gzip, stdin, list files, Unicode and byte paths | Characterize before declaring support. Gzip is a useful common-workflow target; keep decode buffers, checksum validation, and cancellation bounded. Do not silently interpret native `@file` syntax as an upstream feature. |
| `--ignore-errors`, `--keep-going` | Add only tested categories and partial-success behavior. Unsupported categories must produce a useful error. Strict and recovery profiles need separate fixtures. |
| Other operations, source callbacks, advanced substitutions | Reject explicitly until individually implemented. Generic Perl regex/callback compatibility is not implied by matching ordinary merge commands. |

An initial experimental build may expose a smaller subset. Its support table and errors must identify that subset; broad drop-in language waits for the release gates below.

## Verified starting points

Small synthetic probes of the installed LCOV 2.6 reference established the following, with explicit scratch configuration and source files. These are fixture-specific observations, not complete policy definitions.

| Probe | Observed reference behavior |
| --- | --- |
| Same source under testcases `a` and `b` | Successful merge retains two `TN`/`SF` sections with their separate counts. |
| A valid source followed by an empty source section | Successful merge omits the empty source. |
| Equal two-branch layouts with input block IDs `7` and `91` | Successful merge produces one block numbered `0`; corresponding taken counts add. |
| A branch on line 2 without a line-coverage row, alongside valid line 1 | Strict merging fails. Recovery with `--ignore-errors inconsistent` succeeds; this fixture's one executed branch produces `DA:2,1`, despite a taken count of 3. |
| Duplicate function `f` declared at lines 1 and 2 | Strict merging fails. Inconsistent-data recovery succeeds, retaining line 1 and emitting modern `FNL`/`FNA` records in this fixture. |
| Runtime backend inspection | `lcovutil::XS_LOADED` is `1` locally; forcing `LCOV_PURE_PERL=1` makes it `0`. |

Fixtures enabling a coverage family without any coverpoints can fail with an `empty` error. The oracle harness must create valid fixtures for each enabled family, preserve strict failures as failures, and avoid unrelated recovery flags. Earlier invalid exploratory probes are not acceptance evidence.

The local planning probes and their exact commands, input/config/source hashes, backend checks, output and exit status are retained under `.cache/lcov-compat-plan/`. `verify-probes.py` writes `root-probes/verified-results.json`; earlier exploratory JSON files must not substitute for that verified report.

Exit statuses and successful output bytes agreed across seven paired fixtures using the confirmed XS and pure-Perl backends. This verifies those cases; the broader conformance matrix still has to be implemented.

## Architecture and bounded storage

Keep native execution behind its existing entry point. Add a separate profile/request wrapper and compatibility entry point rather than adding mandatory fields to the public `Options` struct. Existing library callers using struct literals should continue to compile. Resolve profile configuration into an immutable request before workers start.

Implement compatibility semantics in dedicated modules such as `src/compat/mod.rs`, `config.rs`, `record.rs`, `merge.rs`, and `run.rs`. Native `Record` layout, comparison, and aggregation should stay on their current path; adding testcase or branch metadata to every native record would increase its memory and CPU cost. Share low-level I/O and cancellation utilities where their contracts already match.

The compatibility representation must preserve source and testcase identities, producer/input/section order, function declarations and aliases, branch expressions/layout, checksums, MC/DC metadata, and any supported version/comment records until their policies are applied. Parsing must not destroy information required by later normalization. Numeric limits, encoding rules, line bounds, and malformed-record treatment need an explicit contract.

Initially use a compatibility executor with external sorting and bounded memory. It may use multiple passes. Budget all reader, string, identity, signature, and aggregation storage; a single enormous source or branch block must not force an unlimited map. Spill oversized groups and branch-signature data to private runs. Hashes may index signatures, but equality must verify the complete signature, including collisions and duplicate signatures.

Instrument run count, merge passes, spill bytes written/read, and effective merge fan-in. Choose buffers and fan-in within both the memory budget and file-descriptor limit; retain measurements for oversized single-source fixtures. This is a project application of the external-memory model discussed below, not a hardware throughput prediction.

Give compatibility runs their own magic/version and decoder validation. Native temporary-run handling can remain unchanged. New readers must reject incompatible, truncated, overlength, or malformed metadata and clean up all stages. Preserve input order explicitly wherever LCOV recovery or normalization depends on it; do not replace ordered decisions with arbitrary hash iteration.

Enable sorted-input and threaded compatibility paths only after equivalence is proven. Source-level metadata may connect different testcases, so a partition must contain every section needed to normalize that source. Start with complete-source boundaries; finer partitions require evidence that they preserve function, branch, and diagnostic behavior. Define each policy's associativity/order assumptions before allowing worker regrouping.

Update `scripts/build-optimized.py` to fingerprint nested Rust modules recursively. Its current top-level `src/*.rs` inventory would miss changes inside a new compatibility directory. Profile training must exercise native and compatibility paths with separate training seeds, freeze profile hashes, and validate training outputs. Maintain the 1 MiB release-binary budget or resolve any necessary budget change explicitly before release.

## Semantic conformance matrix

Each row requires successful cases, boundaries, conflicting inputs, and strict/recovery cases where upstream supports recovery.

| Area | Required characterization and tests |
| --- | --- |
| Source and testcase grouping | Named/unnamed testcases, repeated sections, duplicate inputs, empty sources, source versions, relative/absolute paths, filters and source lookup. |
| Lines and checksums | Zero/nonzero counts, supplied/missing checksums, conflict selection, checksum generation/verification, source-line bounds, disabled coverage, and numeric overflow. |
| Functions | Legacy and modern formats, aliases, duplicate names/locations, differing end lines, orphan hits, inconsistent declarations and summary definitions. |
| Branches | Literal versus canonical block identities, ordered signatures, repeated/ambiguous expressions, differing layouts, exception/unreachable markers, absent counts, and branch-only line recovery. |
| MC/DC | Group/expression identity, condition outcomes, reachability, counter combination, incompatible layouts, enabled/disabled behavior, and summaries. |
| Extra records and syntax | Known versions/comments/extensions, unknown rows, joined boundaries, UTF-8/byte inputs, NULs, CRLF, incomplete final lines, and oversized records. |
| Diagnostics and failures | Usage, input, checksum/layout, missing-source, gzip, and I/O failures; warning categories, error suppression, keep-going, exit codes and partial results. |
| Operational behavior | Existing-destination preservation, stdout/broken pipes, same-input/output paths, disk exhaustion, bounded RAM/spills, deterministic worker joins, and signal cleanup. |

Successful comparison must cover every supported field, not only `SF`/`DA`/`FN`/`FNDA`/`BRDA`. Compare testcase-sensitive normalized semantics and summaries. Canonical branch comparison must preserve branch identity; it must not erase IDs indiscriminately to conceal a wrong merge. Test both outputs through the pinned `genhtml` and compare coverage results and gates.

Track byte equality separately. Native mode requires byte equality to the frozen native baseline. Compatibility mode requires equivalent supported records and consumer results, with a stated ordering/format contract. Compare exact exit behavior for advertised failure cases, while diagnostic wording may differ. If preserving atomic output differs from upstream failure behavior, document that operational difference and narrow the drop-in claim.

Use input permutation and associativity tests only for cases proven order-independent. For conflicting checksums, layouts, and recovery, compare the same input order and preserve its provenance. If XS and pure Perl disagree, resolve the reference policy and supported case set explicitly before publishing a version-wide compatibility claim.

Maintain a per-policy ledger of identity, associativity, commutativity, overflow behavior, and order dependence. For valid, conflict-free count fixtures within numeric limits, duplicating an input should double additive counters; merging coverage is not generally idempotent. Define metadata and summaries separately. Test worker counts 1, 2, and 4, spill thresholds, and eligible sorted/unsorted paths against the same oracle. Metamorphic transforms must record their preconditions, retain failing seeds, and minimize failures without invalidating those preconditions. Include deliberately broken merger variants to check that the selected relations detect plausible faults.

## Work packages and dependencies

| Package | Deliverable | Depends on | Completion gate |
| --- | --- | --- | --- |
| 1. Oracle and contract | Immutable LCOV source/install identity; confirmed XS/Perl variants; clean configs; valid synthetic sources; command/record/error support matrix. | None | Corrected probes and reviewed defaults, ordering, limits, and failure rules for the initial profile. |
| 2. Conformance harness | `tests/compat/` fixtures, independent normalized comparator, backend/config manifest, failure/output checks and upstream-reference links. | 1 | Known semantic mismatches fail visibly; fixture/source validation catches invalid oracle inputs. |
| 3. CLI and request API | Profile selector, aliases, stdout/globs, configuration precedence, resource parsing and unsupported-option errors. | 1 | Native CLI/API regressions pass; profile selection is independent of argument position. |
| 4. Compatibility executor | Rich records, bounded groups/spills, versioned codec, testcase/line/function semantics and cleanup. | 1, 2, request API from 3 | Profile fixtures pass; oversized single-source tests stay budgeted; native output remains identical. |
| 5. Branch and MC/DC policies | Signature alignment, line recovery, condition/group semantics, strict/recovery rules, checksums and remaining advertised settings. | 2, 4 | Every supported conformance row passes against the pinned oracle; contradictory backend cases are resolved. |
| 6. Performance and parallel paths | Profile training, sorted/parallel eligibility, repeated benchmarks, RSS/temp-disk reports and collision/order tests. | 4, 5 | Correctness survives each optimization and the agreed default/compatibility performance gates pass. |
| 7. CI and rollout | Cross-platform fixtures, sanitizers/fuzzing, documentation, Action examples, release metadata and migration guide. | 2 through 6 | Release gates below pass on the exact candidate commit. |

Packages are reviewable pull requests; branch policy and MC/DC work can be split into additional changes. Do not release a partially implemented record family behind an apparently complete compatibility flag.

## Parallel Luna execution

Use at most three worker processes plus the coordinating process. During implementation, give workers isolated worktrees under ignored `.cache/` directories and explicit file ownership. Agree on request and record interfaces before parallel edits. The coordinator owns shared interfaces, `engine.rs` integration, and final verification.

| Work | Luna effort | Parallel boundary |
| --- | --- | --- |
| Scoped CLI/source inventories, mechanical docs and routine command runs | Low | Separate read-only inventory or documentation task. |
| CLI/config implementation, oracle harness, ordinary fixtures, CI/build changes | Medium | Different worktree and owned files; shared interface fixed first. |
| Branch alignment, MC/DC, bounded spilling, ordering/overflow design | High | Narrow semantic module with oracle fixtures; increase scope only after its gate passes. |
| Independent plan/code review and performance interpretation | Medium; High for a specific hard semantic dispute | Review completed artifacts; do not edit concurrently with their owner. |

A practical first implementation wave is oracle/comparator work, CLI/config work, and compatibility-record design in parallel. The next wave can split function, branch, and MC/DC modules once their shared representation is stable. Integrate sequentially. Run performance measurements on an idle host, not beside compiler, fuzz, or benchmark jobs.

Keep prompts, logs, profiles, generated coverage, and reports under `.cache/`. Give each worker only its contract, named paths, and relevant failure evidence. Request compact reports; print failing excerpts rather than full logs. Run focused tests while modules change and the complete required suites at integration gates.

## Performance gates

Freeze the current Rust PGO binary and its source/profile/input hashes before implementation. Preserve the historical implementation comparison separately. Measure sorted and unsorted data, one and several jobs, many small shards, huge single sources, testcase-heavy data, branch/function/MC/DC-heavy inputs, and gzip if supported.

Start with one warm-up and 19 seeded interleaved measured runs for release comparisons, with identical inputs and equivalent outputs. Record elapsed/CPU time, peak RSS, peak temporary disk, spill I/O/passes, machine load, flags, backend, profile, and failures. Use held-out seeds and fixtures; do not measure only profile-training data. This run count is an initial sampling design, not a universal sufficiency rule.

The default mode's release target remains at least its frozen baseline speed on the agreed workloads. A result within 3% is a proposed noise-investigation band, not an automatic regression allowance: repeat close results on an idle host and retain samples. Investigate memory/disk increases and require a documented explanation rather than silently accepting them.

As a project evaluation decision, predeclare the paired timing statistic, uncertainty method, and acceptance boundary before comparing candidates. Report raw samples and uncertainty for each workload; a bound spanning the acceptance boundary is inconclusive and needs more evidence. Failure to find a significant difference does not establish equal speed. Repeat close comparisons across idle-host sessions and document sensitivity to supported toolchain/profile variants, with binary hashes. Run-order randomization controls temporal drift; it does not sample code layouts. Identical reproducible rebuilds do not provide independent layouts either. Scope speed claims to the measured binaries, workloads, and environments.

Characterize compatibility mode against LCOV 2.6 XS and pure Perl separately. Correctness gates precede speed claims. Set its numerical speed/RSS targets after package 1 establishes representative workloads and both reference backends; no existing measurement establishes the new mode's speed. Report each workload honestly, including any losing case.

## Research evidence and transfer limits

On 2026-10-14, the science-paper-search review covered external-memory sorting, test-oracle methods, deterministic parallelism, and performance evaluation. Semantic/keyword title-and-abstract searches were followed by a metamorphic-testing similarity search and citation walk. Results were deduplicated by DOI and reconciled with published versions. This focused search does not establish exhaustive coverage. Original-paper sections covering models, methods, results, and limitations were reviewed; the table does not claim every paper was read end to end.

| Primary paper | Finding relevant to this plan | Transfer limit |
| --- | --- | --- |
| Aggarwal and Vitter, [The Input/Output Complexity of Sorting and Related Problems](https://doi.org/10.1145/48529.48535), 1988; [original paper hosted by Washington University](https://www.cse.wustl.edu/~angelee/archive/cse341/fall14/papers/AggarwalVi88.pdf). | Establishes external-sorting I/O bounds and merge-sort optimality up to constant factors in its single-block-transfer model. Supports measuring passes and block traffic under bounded memory. | Fixed-size records and a magnetic-disk model do not predict Rust parser cost, variable-size metadata, compression, or NVMe performance. |
| Liu, Kuo, Towey and Chen, [How Effectively Does Metamorphic Testing Alleviate the Oracle Problem?](https://doi.org/10.1109/TSE.2013.46), published 2014, online 2013; [repository manuscript](https://vuir.vu.edu.au/33046/1/TSEmt.pdf). | Experiments with five Java algorithmic programs and mutants support selecting diverse, validated relations. Use independent transformations alongside pinned-reference comparisons. | Study scope and subjective relation selection limit generalization. Its fault-detection results do not establish LCOV conformance; invalid relations can give false failures. |
| Kuper and Newton, [LVars: Lattice-based Data Structures for Deterministic Parallelism](https://doi.org/10.1145/2502323.2502326), 2013; [author paper](https://users.soe.ucsc.edu/~lkuper/papers/lvars-fhpc13.pdf). | Its determinism proof depends on monotonic lattice updates and restricted threshold reads. It motivates documenting the actual algebra and observation rules before parallel regrouping. | The proof concerns its own language model. Ordinary count addition and LCOV recovery are not automatically lattice joins; this does not prove our Rust executor deterministic. |
| Curtsinger and Berger, [STABILIZER: Statistically Sound Performance Evaluation](https://doi.org/10.1145/2451116.2451141), 2013; [author paper](https://people.cs.umass.edu/~emery/pubs/stabilizer-asplos13.pdf). | Demonstrates layout-dependent measurement bias using runtime layout randomization and LLVM/SPEC CPU2006 experiments. Repeated timings of one executable do not explore the space of code layouts. | Its compiler/runtime and experimental hardware differ from this project. It supplies no project-specific speed threshold or sufficient sample count. |

These papers inform the added instrumentation, relation preconditions, policy ledger, and benchmark qualifications. Those are project recommendations requiring implementation and measurement. They establish neither the new mode's compatibility nor performance parity with the previous implementation.

Search requests/responses, screening decisions, identifiers, reviewed sections, paper hashes, and download failures are retained in ignored `.cache/lcov-science-review/`, with `evidence.json` as the index. The repository manuscript is marked as a presentation/accepted version; its journal year is 2014. The two STABILIZER ACM DOIs for conference and SIGARCH versions refer to the same study, not independent evidence. Its [source repository](https://github.com/ccurtsinger/stabilizer) was checked independently: Apache-2.0 license, LLVM 3.1 requirements, and stated lack of active maintenance. No research implementation or dependency is proposed for adoption.

## Release and rollback gates

Before a stable profile release:

1. The supported CLI/configuration/record/error matrix has no unexplained oracle differences. There are no skipped advertised features or silently ignored merge options.
2. Native `make test` and the new profile suite pass; legacy native outputs remain byte-identical to the frozen reference.
3. `make asan`, `make tsan`, deterministic mutations and CLI fuzzing pass, including corrupted compatibility runs and deterministic interruption tests.
4. Linux x86-64/aarch64, macOS arm64/x86-64 and Windows builds/runtime checks pass. Pinned backend oracles run on supported environments; platform-specific skips are explicit.
5. Build caching, profile training, reproducibility, binary-size limits, cancellation, output protection, memory accounting and spill cleanup pass.
6. Native performance gates pass and profile-versus-oracle results are published with exact provenance.
7. CLI help, man page, usage/compatibility/migration guides, README, website, Action input/examples, changelog and release notes agree on the same profile and supported subset. Published earlier measurements remain historical.

Ship opt-in first. Existing native invocations provide the behavioral rollback by removing the profile selector. Users trial it beside their LCOV merge on identical artifacts and the same report consumer. A later default change or `lcov`-named wrapper needs its own compatibility evidence and release decision.

## Effort and decisions

The initial planning estimate is roughly 11–21 engineering days across characterization, implementation, validation and rollout, with parallel overlap possible. Branch-signature collisions, MC/DC, source-dependent filtering, configuration discovery, and bounded spilling are the largest uncertainties; re-estimate after package 1. This is an effort range rather than a calendar promise or an estimate for replacing the entire LCOV suite.

The first implementation decision is the exact initial support matrix. Resolve gzip/input forms, configuration discovery/allowlist, error recovery categories, source-dependent options, extreme counts/encodings, backend disagreements, and resource-option mappings through oracle probes. Then freeze those decisions before parallel semantic implementation.
