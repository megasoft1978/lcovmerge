# Sorted many-shard streaming trial

This local experiment compares the previous 32-input direct-merge limit with the expanded budgeted direct merge. It is an architecture trial on one synthetic workload, not a cross-platform performance claim or a replacement for the canonical tool benchmark.

## Method

- Host: macOS 27.0 arm64, 18 logical CPUs, Apple Clang 21.0.3.
- Baseline: clean source archive at commit `71624d359b4e`, built with the repository Makefile.
- Candidate: working tree with dynamically growing 4 KiB line buffers, 16 KiB read blocks, and the budgeted direct path for more than 32 regular inputs.
- Fixture: 128 sorted shards, 12,663,168 bytes total, each containing 10,000 ascending `DA` records for the same source path (1,280,000 input rows total).
- Measurement: Hyperfine 2.0.0, one warmup and five measured runs per binary; peak resident memory is the maximum measured sample. Both commands used `--jobs 1` and wrote to files.
- Combined ordered fixture manifest SHA-256: `ddd6218fa7bac8e783962dc9525b89a1c9c0a60ad4365cbb98984e97acc591a9`.

## Results

| Build | Median time | Peak resident memory | Output SHA-256 |
| --- | ---: | ---: | --- |
| Baseline, external sort | 179.462 ms | 7.72 MiB | `bf9695f35de2a8609a6b5f2d1099cce332f92aa42dc38e30afaacdda803a2ace` |
| Candidate, direct merge | 139.936 ms | 6.14 MiB | `bf9695f35de2a8609a6b5f2d1099cce332f92aa42dc38e30afaacdda803a2ace` |

The candidate was 22.0% faster and used 1.83 MiB less peak resident memory in this run. The identical output hashes confirm byte-for-byte output equality for this fixture. Raw Hyperfine JSON, generated inputs, binaries, and per-run values are retained under the ignored `.cache/review-20261011/direct-128-large/` directory.

The 32-input threshold was removed, but the direct path still falls back when its reader state exceeds its budget or when it cannot open every input. The 16 KiB I/O block was retained after this trial; a separate half-row/half-arena fallback experiment improved a DA-heavy case by 9.7% but regressed a function-name workload by 5.5% and increased peak RSS, so that change was rejected.
