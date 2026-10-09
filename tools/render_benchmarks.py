#!/usr/bin/env python3
"""Render benchmark tables and site data from the canonical benchmark JSON."""

import json
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = json.loads((ROOT / "data/benchmarks.json").read_text())
START = "<!-- BENCHMARKS:START -->"
END = "<!-- BENCHMARKS:END -->"


def metric(result):
    seconds = "—" if result["time_s"] is None else f'{result["time_s"]:,.3f} s'
    rss = memory(result)
    rate = "n/a" if result["throughput_mb_s"] is None else f'{result["throughput_mb_s"]:,.1f} MB/s'
    return f'{seconds} / {rss} / {rate} / {result["status"]}'


def memory(result):
    if result.get("rss_display"):
        return result["rss_display"]
    if result.get("rss_bytes") is not None:
        return f'{result["rss_bytes"] / (1024 * 1024):,.2f} MiB'
    if result["rss_mib"] is None:
        return "—"
    return f'{result["rss_mib"]:,.1f} MiB'


def compact_metric(result):
    seconds = "—" if result["time_s"] is None else f'{result["time_s"]:,.3f} s'
    if result["throughput_mb_s"] is None:
        detail = "n/a"
    else:
        detail = f'{result["throughput_mb_s"]:,.1f} MB/s'
    return f'{seconds} / {memory(result)} / {detail} / {result["status"]}'


def context():
    host = DATA["host"]
    method = DATA["method"]
    repeats = ", ".join(f'{name}: {count}' for name, count in method["repeat_counts"].items())
    timeout_minutes = method["timeout_seconds"] / 60
    result = (
        f'Host: {host["os"]}, {host["cpu"]}, {host["cores"]} cores, '
        f'{host["memory_gib"]} GiB RAM; {host["cache"]}. '
        f'Measured: {DATA["measured_on"]}. lcovmerge runs by dataset: {repeats}. '
        f'Each comparison tool ran {DATA["method"]["comparison_runs_per_tool"]} time(s) per dataset. '
        f'Per-command timeout: {timeout_minutes:g} minutes.'
    )
    return textwrap.fill(result, width=110, break_long_words=False,
                         break_on_hyphens=False)


def readme_table():
    wanted = {"M", "L", "XL-single", "PATH-HEAVY"}
    labels = DATA["tool_labels"]
    rows = [
        f'| Dataset (input bytes) | {labels["lcovmerge"]} | {labels["lcov"]} | '
        f'{labels["lcov-result-merger"]} |',
        "| --- | ---: | ---: | ---: |",
    ]
    for ds in DATA["datasets"]:
        if ds["name"] not in wanted:
            continue
        r = ds["results"]
        size = f'{ds["input_bytes"]:,}' if ds.get("input_bytes") is not None else "unavailable"
        rows.append(f'| {ds["name"]} ({size}) '
                    f'| {compact_metric(r["lcovmerge"])} '
                    f'| {compact_metric(r["lcov"])} '
                    f'| {compact_metric(r["lcov-result-merger"])} |')
    return "\n".join(rows)


def full_table():
    labels = DATA["tool_labels"]
    rows = [
        f'| Dataset (input bytes) | {labels["lcovmerge"]} | {labels["lcov"]} | '
        f'{labels["lcov-result-merger"]} |',
        "| --- | ---: | ---: | ---: |",
    ]
    for ds in DATA["datasets"]:
        r = ds["results"]
        size = f'{ds["input_bytes"]:,} bytes' if ds.get("input_bytes") is not None else "unavailable"
        rows.append(
            f'| {ds["name"]} ({size}) '
            f'| {metric(r["lcovmerge"])} '
            f'| {metric(r["lcov"])} '
            f'| {metric(r["lcov-result-merger"])} |'
        )
    return "\n".join(rows)


def scaling_table():
    scaling = DATA["scaling"]["M"]
    rows = [
        "| lcovmerge jobs argument | Time | Peak RSS | Throughput |",
        "| ---: | ---: | ---: | ---: |",
    ]
    for jobs in ("default", "1", "2", "4", "8"):
        result = scaling[jobs]
        label = "default" if jobs == "default" else f'-j{jobs}'
        rows.append(
            f'| {label} | {result["time_s"]:,.3f} s | {memory(result)} | '
            f'{result["throughput_mb_s"]:,.1f} MB/s |'
        )
    return "\n".join(rows)


def caveats():
    return "\n".join(
        textwrap.fill(item, width=110, initial_indent="- ",
                      subsequent_indent="  ", break_long_words=False,
                      break_on_hyphens=False)
        for item in DATA["caveats"]
    )


def bazel_evidence():
    evidence = DATA["external_evidence"]["bazel_issue_26383"]
    return (
        "Large tracefiles can make a merge job the most memory hungry part of a "
        "coverage pipeline. The Bazel CoverageOutputGenerator issue reports "
        "that combining "
        f'{evidence["input_description"]} required a Java heap above '
        f'{evidence["reported_heap_requirement_gb"]} GB. It also shows a '
        f'{evidence["single_file_size_mb"]} MB single-file case failing with a '
        f'heap cap of {evidence["single_file_heap_cap_gb"]} GB. This is one '
        "reported workload, not a universal result for Bazel."
    )


def site_benchmarks():
    """Map canonical measurements to the schema consumed by docs/site/site.js."""
    ids = {"M": "medium", "L": "large", "XL-single": "single_file"}
    order = {"M": 0, "L": 1, "XL-single": 2, "S": 3, "PATH-HEAVY": 4, "REAL": 5}
    labels = DATA["tool_labels"]
    result_keys = (("lcovmerge", "lcovmerge"), ("lcov", "lcov"),
                   ("lcov-result-merger", "lcov-result-merger"))
    datasets = []
    for source in sorted(DATA["datasets"], key=lambda item: order.get(item["name"], 99)):
        rows = []
        for key, label_key in result_keys:
            item = source["results"][key]
            seconds = item["time_s"]
            rss = item.get("rss_bytes")
            rows.append({
                "tool": labels[label_key],
                "seconds": seconds,
                "seconds_display": f'{seconds:,.3f} s' if seconds is not None else item["status"],
                "peak_rss_mib": rss / (1024 * 1024) if rss is not None else None,
                "rss_display": (f'{rss / (1024 * 1024):,.2f} MiB' if rss is not None
                                else item.get("rss_display", item["status"])),
                "throughput_mbs": item.get("throughput_mb_s"),
                "throughput_display": (f'{item["throughput_mb_s"]:,.1f} MB/s'
                                       if item.get("throughput_mb_s") is not None else "—"),
                "status": item["status"],
            })
        site_dataset = {
            "id": ids.get(source["name"], source["name"].lower().replace("-", "_")),
            "name": source["name"],
            "input_size": (f'{source["input_bytes"]:,} bytes'
                           if source.get("input_bytes") is not None else "unavailable"),
            "shards": source.get("shards", "unknown shard count"),
            "results": rows,
        }
        datasets.append(site_dataset)
    metadata = {
        "version": DATA["version"],
        "machine": DATA["host"]["cpu"],
        "measured_on": DATA["measured_on"],
        "runs": 1,
        "repeat_counts": DATA["method"]["repeat_counts"],
        "comparison_runs_per_tool": DATA["method"]["comparison_runs_per_tool"],
        "cache": DATA["host"]["cache"],
        "source": "data/benchmarks.json",
        "data_kind": "synthetic",
        "environment": DATA["environment"],
    }
    return {**metadata, "datasets": datasets}


def replace_section(path, content):
    text = path.read_text()
    if START not in text or END not in text:
        raise SystemExit(f"benchmark markers missing in {path.relative_to(ROOT)}")
    before, rest = text.split(START, 1)
    _, after = rest.split(END, 1)
    note = "<!-- Generated by tools/render_benchmarks.py from data/benchmarks.json. -->\n"
    path.write_text(before + START + "\n" + note + content + "\n" + END + after)


def replace_named_section(path, start, end, content):
    text = path.read_text()
    before, rest = text.split(start, 1)
    _, after = rest.split(end, 1)
    path.write_text(before + start + "\n" + content + "\n" + end + after)


replace_section(ROOT / "README.md", readme_table())
replace_section(ROOT / "docs/BENCHMARKS.md", full_table())
replace_named_section(ROOT / "README.md", "<!-- BENCH-CONTEXT:START -->", "<!-- BENCH-CONTEXT:END -->", context())
replace_named_section(ROOT / "docs/BENCHMARKS.md", "<!-- BENCH-METHOD:START -->", "<!-- BENCH-METHOD:END -->", context())
replace_named_section(ROOT / "docs/BENCHMARKS.md", "<!-- BENCH-CAVEATS:START -->", "<!-- BENCH-CAVEATS:END -->", caveats())
replace_named_section(ROOT / "README.md", "<!-- BAZEL-EVIDENCE:START -->", "<!-- BAZEL-EVIDENCE:END -->", textwrap.fill(bazel_evidence(), width=110, break_long_words=False, break_on_hyphens=False))
replace_named_section(ROOT / "docs/BENCHMARKS.md", "<!-- SCALING:START -->", "<!-- SCALING:END -->", scaling_table())

site_data = site_benchmarks()
(ROOT / "docs/site/data/benchmarks.json").write_text(
    json.dumps(site_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
