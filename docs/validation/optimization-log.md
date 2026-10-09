# Performance optimization log

## Objective and method

The acceptance target is at least 300 MB/s on dataset M with default settings, with default peak RSS at or below
32 MiB. Dataset M is generated outside the repository with `bench/run.py` and `tools/gen-lcov.py`; it contains
1,118,686,233 input bytes across 32 shards. Throughput uses decimal MB/s. Measurements are single runs on the
recorded Apple M5 Max host and cache state was not controlled.

The [v1.0 baseline report](benchmark-v1-baseline.txt) recorded 5.987 s, 186.8 MB/s and 22,855,680 B peak RSS. The historical prototype
comparison supplied for this review was 343.9 MB/s, 3.253 s and 5.2 MiB; that separate read-only implementation
was not rebuilt or remeasured in this run.

## Profile and hot paths

The final 3-second macOS `sample` profile is summarized in [profile-hot-paths.txt](profile-hot-paths.txt). It
sampled `direct_tree_winner`, `direct_input_next`, `parse_direct_da`, and the main processing loop. The top-of-stack
summary also includes string comparison, `memcpy`/`memmove`, and output writer functions. A source diff against the
prototype highlighted the sorted-input stream path and per-input path-comparison caching. The v1.0 external-sort
route staged and sorted records even when all regular input shards were already in canonical order. That work
added both CPU and arena/temporary-record memory.

## Iterations

| Iteration | Default M time | Throughput | Peak RSS | Change measured |
| --- | ---: | ---: | ---: | --- |
| v1.0 regression baseline | 5.987 s | 186.8 MB/s | 22,855,680 B | External-sort processing of generated sorted shards. |
| Initial sorted-stream pass | 5.811 s | 192.5 MB/s | 5,406,720 B | Stream already-sorted regular files without creating sorted runs; raw result is [benchmark-stream-stage.txt](benchmark-stream-stage.txt). |
| Intermediate optimized pass | 4.749 s | 235.6 MB/s | 24,723,456 B | Additional path-key and row-processing changes; raw result is [benchmark-optimization-stage.txt](benchmark-optimization-stage.txt). |
| Final sorted-stream pass | 3.316 s | 337.4 MB/s | 5,373,952 B | Bounded two-row lookahead, a tournament tree for live shard heads, lower-copy row ownership, and direct DA/BRDA parsing with canonical-parser fallback for malformed rows. |

The final run exceeds the 300 MB/s requirement by 12.5% and uses 5.12 MiB peak RSS. Compared with the v1.0
baseline, throughput increased by 80.6% and peak RSS fell by 76.5%. The final implementation is within 1.9% of
the historical prototype throughput while preserving the v1.0 parser, aggregation and error semantics.

The direct path is limited to sorted regular-file inputs and bounded fan-in. It abandons staged output and
diagnostics and falls back to the external-sort implementation if it detects an out-of-order row or cannot use
the streaming path. DA and BRDA direct parsers validate row shape and defer malformed input to the canonical
parser. Golden, oracle, lcov-differential, determinism and fuzz checks are run on the final code separately.

## Job-setting measurements

The requested `-j1`, `-j2`, `-j4` and `-j8` runs are recorded in [benchmark.txt](benchmark.txt) and the canonical
[`data/benchmarks.json`](../../data/benchmarks.json). Sorted regular inputs use the single-threaded direct path,
so these values show run-to-run timing variation rather than external-sort scaling. A separate out-of-order
fallback case is checked byte-for-byte against direct output.

## Workload limits observed

The final M target is met, but the result is not a general throughput promise. Dataset L and PATH-HEAVY emphasize
different merge costs; their exact timings and memory are in the final benchmark report. The sorted-stream path
also does not change the memory cap's documented scope: parser buffers, I/O, thread stacks, allocator overhead
and temporary-file storage are not included in `--mem-limit`.
