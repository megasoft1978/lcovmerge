#!/usr/bin/env python3
"""Render benchmark tables and site data from the canonical benchmark JSON."""

import argparse
import html
import json
import re
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = json.loads((ROOT / "data/benchmarks.json").read_text())
REAL_DATA = json.loads((ROOT / "data/real-projects.json").read_text())
VERSION_MATCH = re.search(
    r'^#define LCOVMERGE_VERSION "([^"]+)"$',
    (ROOT / "include/version.h").read_text(),
    re.MULTILINE,
)
if VERSION_MATCH is None:
    raise SystemExit("LCOVMERGE_VERSION is missing from include/version.h")
CURRENT_LCOVMERGE_LABEL = f"lcovmerge {VERSION_MATCH.group(1)}"
START = "<!-- BENCHMARKS:START -->"
END = "<!-- BENCHMARKS:END -->"
HERO_START = "<!-- HERO-PROOF:START -->"
HERO_END = "<!-- HERO-PROOF:END -->"
REAL_START = "<!-- REAL-PROJECTS:START -->"
REAL_END = "<!-- REAL-PROJECTS:END -->"
CHART_MARKERS = {
    ("time", "medium"): ("<!-- CHART-M:TIME:START -->", "<!-- CHART-M:TIME:END -->"),
    ("rss", "medium"): ("<!-- CHART-M:RSS:START -->", "<!-- CHART-M:RSS:END -->"),
}
CHART_ALT_MARKERS = {
    "time": ("<!-- CHART-M:TIME-ALT:START -->", "<!-- CHART-M:TIME-ALT:END -->"),
    "rss": ("<!-- CHART-M:RSS-ALT:START -->", "<!-- CHART-M:RSS-ALT:END -->"),
}
MEDIUM_RESULTS_START = "<!-- MEDIUM-RESULTS:START -->"
MEDIUM_RESULTS_END = "<!-- MEDIUM-RESULTS:END -->"
MEDIUM_CAPTION_START = "<!-- MEDIUM-CAPTION:START -->"
MEDIUM_CAPTION_END = "<!-- MEDIUM-CAPTION:END -->"
SITE_CHARTS_START = "<!-- SITE-BENCHMARK-CHARTS:START -->"
SITE_CHARTS_END = "<!-- SITE-BENCHMARK-CHARTS:END -->"
SITE_RESULTS_START = "<!-- SITE-BENCHMARK-RESULTS:START -->"
SITE_RESULTS_END = "<!-- SITE-BENCHMARK-RESULTS:END -->"
SITE_HOST_RESULTS_START = "<!-- SITE-HOST-RESULTS:START -->"
SITE_HOST_RESULTS_END = "<!-- SITE-HOST-RESULTS:END -->"
SITE_METHOD_START = "<!-- SITE-BENCHMARK-METHOD:START -->"
SITE_METHOD_END = "<!-- SITE-BENCHMARK-METHOD:END -->"


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


def display_tool_labels():
    labels = DATA["tool_labels"].copy()
    labels["lcovmerge"] = CURRENT_LCOVMERGE_LABEL
    return labels


def readme_table():
    wanted = {"M", "L", "XL-single", "PATH-HEAVY"}
    labels = display_tool_labels()
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
    labels = display_tool_labels()
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


def dataset(name):
    return next(item for item in DATA["datasets"] if item["name"] == name)


def seconds_display(value):
    return f'{value:.6f}'.rstrip("0").rstrip(".") + " s"


def bytes_display(value):
    return f"{value:,} B"


def hero_proof():
    return f'''<p class="eyebrow">LCOV tracefile merge · C11 · MIT</p>
        <h1>Merge existing LCOV shards with bounded record memory.</h1>
        <p class="hero-copy">Merge existing <code>.info</code> files into deterministic output. Keep your collector and report generator.</p>
        <div class="actions">
          <a class="button" href="https://github.com/megasoft1978/lcovmerge/releases/tag/v{VERSION_MATCH.group(1)}">Download v{VERSION_MATCH.group(1)}</a>
          <a class="button secondary" href="docs.html#github-actions">GitHub Action</a>
        </div>
        <p class="hero-note">The <code>--mem-limit</code> budget covers the record arena, not total RSS or temporary disk. lcovmerge is not a drop-in for <code>lcov -a</code>. Read <a href="https://github.com/megasoft1978/lcovmerge/blob/main/docs/LIMITATIONS.md">Limits &amp; semantics</a>.</p>'''


def hero_card():
    medium = dataset("M")
    results = medium["results"]
    lcovmerge = results["lcovmerge"]
    lcov = results["lcov"]
    merge_runs = DATA["method"]["repeat_counts"]["M"]
    comparator_runs = DATA["method"]["comparison_runs_per_tool"]
    return f'''<p class="eyebrow">Measured on generated LCOV</p>
        <h2 id="hero-results-title">Dataset M · {html.escape(medium["shards"])}</h2>
        <p id="hero-benchmark-context">Generated input: {medium["input_bytes"]:,} bytes · {html.escape(DATA["host"]["os"])}, {html.escape(DATA["host"]["cpu"])} · measured {html.escape(DATA["measured_on"])}</p>
        <div class="metric-grid">
          <div class="metric">
            <strong data-benchmark-value="datasets.0.results.0.seconds_display">{seconds_display(lcovmerge["time_s"])}</strong>
            <span>{html.escape(CURRENT_LCOVMERGE_LABEL)} · median of {merge_runs} measured runs</span>
            <span class="metric-detail">{bytes_display(lcovmerge["rss_bytes"])} peak RSS</span>
          </div>
          <div class="metric">
            <strong data-benchmark-value="datasets.0.results.1.seconds_display">{seconds_display(lcov["time_s"])}</strong>
            <span>{html.escape(DATA["tool_labels"]["lcov"])} · {comparator_runs} measured run(s)</span>
            <span class="metric-detail">Status: {html.escape(lcov["status"])}</span>
          </div>
        </div>
        <p class="bench-note">One generated workload on one recorded host. {html.escape(DATA["host"]["cache"].rstrip("."))}.</p>
        <a class="text-link" href="benchmarks.html#method-title">Method and caveats ↗</a>'''


def readme_proof():
    medium = dataset("M")
    lcovmerge = medium["results"]["lcovmerge"]
    lcov = medium["results"]["lcov"]
    runs = DATA["method"]["repeat_counts"]["M"]
    return "\n".join([
        f'> **Dataset M · generated, {medium["shards"]} · {medium["input_bytes"]:,} input bytes**<br>',
        f'> lcovmerge: **{seconds_display(lcovmerge["time_s"])}**, **{bytes_display(lcovmerge["rss_bytes"])} peak RSS** (median of {runs} runs).<br>',
        f'> {DATA["tool_labels"]["lcov"]}: **{seconds_display(lcov["time_s"])}**, **{bytes_display(lcov["rss_bytes"])} peak RSS** (one run; prior canonical RSS measurement).<br>',
        f'> One {DATA["host"]["os"]} {DATA["host"]["cpu"]} host; {DATA["host"]["cache"]}.',
    ])


def chart(dataset_name, metric_name):
    source = dataset(dataset_name)
    is_time = metric_name == "time"
    metric_label = "elapsed time" if is_time else "peak resident memory"
    values = []
    labels = display_tool_labels()
    for key, result in source["results"].items():
        value = result["time_s"] if is_time else result["rss_bytes"]
        values.append((key, result, value))
    numeric = [value for _, _, value in values if value is not None]
    maximum = max(numeric, default=1)
    if maximum <= 0:
        maximum = 1
    scale_max = maximum if is_time else maximum / (1024 * 1024)
    unit = "seconds" if is_time else "MiB"
    merge_runs = DATA["method"]["repeat_counts"].get(source["name"])
    comparator_runs = DATA["method"]["comparison_runs_per_tool"]
    merge_run_text = f'{merge_runs} measured run(s)' if merge_runs is not None else "run count not reported"
    input_size = (f'{source["input_bytes"]:,} bytes'
                  if source.get("input_bytes") is not None else "input size not reported")
    run_context = (f'lcovmerge: {merge_run_text}; lcov: {comparator_runs} run(s); '
                   'lcov RSS: prior canonical where available; run count not reported; '
                   'lcov-result-merger: prior canonical measurement, run count not reported')
    pieces = [
        f'<ul class="bar-chart" aria-label="{html.escape(source["name"])} dataset {html.escape(metric_label)} results">'
    ]
    for key, result, value in values:
        label = labels.get(key, key)
        if key == "lcovmerge":
            run_text = f'{merge_runs} run(s)' if merge_runs else "run count not reported"
        elif key == "lcov" and not is_time:
            run_text = ("prior canonical RSS; run count not reported" if value is not None
                        else "current failed run; RSS unavailable")
        elif key == "lcov":
            run_text = f'{comparator_runs} time run(s)'
        elif key == "lcov-result-merger":
            run_text = "prior canonical measurement; run count not reported"
        if value is None:
            display = result.get("rss_display") if not is_time else None
            display = display or result.get("status", "Not reported")
            bar_size = 0
        else:
            display = seconds_display(value) if is_time else bytes_display(result.get("rss_bytes"))
            bar_size = max(0, value / maximum * 100)
        status = result.get("status", "Not reported")
        status_text = f' · {status} · {run_text}' if status else f' · {run_text}'
        css_tool = key if key in {"lcovmerge", "lcov", "lcov-result-merger"} else "other"
        pieces.append(
            f'<li class="bar-chart-item {css_tool}">'
            f'<div class="bar-chart-label"><span class="bar-chart-tool">{html.escape(label)}</span>'
            f'<span class="bar-chart-value">{html.escape(display + status_text)}</span></div>'
            f'<div class="bar-track" aria-hidden="true"><span class="bar-fill" style="width:{bar_size:.3f}%"></span></div>'
            '</li>'
        )
    pieces.append("</ul>")
    ticks = []
    for index in range(3):
        tick = scale_max * index / 2
        if is_time:
            tick_text = f'{tick:.1f}' if tick < 10 else f'{tick:,.0f}'
            ticks.append(f'<span>{tick_text} s</span>')
        else:
            tick_text = f'{tick:,.1f}' if tick < 10 else f'{tick:,.0f}'
            ticks.append(f'<span>{tick_text} MiB</span>')
    pieces.append(
        f'<div class="bar-axis" role="img" aria-label="Zero-based scale from 0 to {scale_max:,.3f} {unit}">'
        + "".join(ticks) + "</div>"
    )
    host = f'{DATA["host"]["os"]}, {DATA["host"]["cpu"]}'
    pieces.append(
        f'<p class="chart-context">Dataset {html.escape(source["name"])} · {html.escape(input_size)} · '
        f'{html.escape(source["shards"])} · host {html.escape(host)} · measured {html.escape(DATA["measured_on"])}. '
        f'{html.escape(run_context)}. <a href="benchmarks.html#method-title">Method</a>.</p>'
    )
    return "".join(pieces)


def chart_text_alternative(metric_name):
    source = dataset("M")
    phrases = []
    labels = display_tool_labels()
    for key in ("lcovmerge", "lcov", "lcov-result-merger"):
        result = source["results"][key]
        value = result["time_s"] if metric_name == "time" else result["rss_bytes"]
        if value is None:
            display = result.get("rss_display") if metric_name != "time" else None
            display = display or result["status"]
        elif metric_name == "time":
            display = seconds_display(value)
        else:
            display = bytes_display(result["rss_bytes"])
        if key == "lcovmerge":
            run_text = f'{DATA["method"]["repeat_counts"].get(source["name"], "unknown")} runs'
        elif key == "lcov" and metric_name != "time":
            run_text = ("prior canonical RSS; run count not reported" if value is not None
                        else "current failed run; RSS unavailable")
        elif key == "lcov":
            run_text = f'{DATA["method"]["comparison_runs_per_tool"]} time run(s)'
        elif key == "lcov-result-merger":
            run_text = "prior canonical measurement; run count not reported"
        phrases.append(f'{labels[key]} {display} · {result["status"]} · {run_text}')
    label = "elapsed time" if metric_name == "time" else "peak RSS"
    return (f'{source["name"]} {label}, generated input on {DATA["host"]["os"]} '
            f'{DATA["host"]["cpu"]}, measured {DATA["measured_on"]}: ' + "; ".join(phrases) + ".")


def medium_results_rows():
    source = dataset("M")
    rows = []
    labels = display_tool_labels()
    for key in ("lcovmerge", "lcov", "lcov-result-merger"):
        result = source["results"][key]
        elapsed = seconds_display(result["time_s"]) if result["time_s"] is not None else result["status"]
        rss = bytes_display(result["rss_bytes"]) if result["rss_bytes"] is not None else result.get("rss_display", "—")
        throughput = (f'{result["throughput_mb_s"]:,.1f} MB/s'
                      if result["throughput_mb_s"] is not None else "—")
        if key == "lcovmerge":
            runs = DATA["method"]["repeat_counts"].get("M")
            run_display = f'{runs} measured' if runs else "Not reported"
        elif key == "lcov-result-merger":
            run_display = "Prior canonical; count unknown"
        else:
            run_display = (f'Time: {DATA["method"]["comparison_runs_per_tool"]} run; '
                           'RSS: prior canonical, count unknown')
        primary = " class=\"tool-primary\"" if key == "lcovmerge" else ""
        rows.append(
            f'<tr><th scope="row"{primary}>{html.escape(labels[key])}</th>'
            f'<td class="table-numeric">{html.escape(elapsed)}</td>'
            f'<td class="table-numeric">{html.escape(rss)}</td>'
            f'<td class="table-numeric">{html.escape(throughput)}</td>'
            f'<td>{html.escape(run_display)}</td>'
            f'<td>{html.escape(result["status"])}</td></tr>'
        )
    return "\n".join(rows)


def medium_caption():
    source = dataset("M")
    repeats = DATA["method"]["repeat_counts"]["M"]
    comparators = DATA["method"]["comparison_runs_per_tool"]
    return (f'{source["name"]}: {source["input_bytes"]:,} generated input bytes across '
            f'{html.escape(source["shards"])}; host {html.escape(DATA["host"]["os"])} '
            f'{html.escape(DATA["host"]["cpu"])}; measured {html.escape(DATA["measured_on"])}. '
            f'lcovmerge: {repeats} measured runs; lcov elapsed time: {comparators} run(s); '
            'successful lcov RSS values are prior canonical, with run counts not reported. '
            'lcov-result-merger values are prior canonical, with run counts not reported. '
            f'RSS is peak resident memory. See <a href="benchmarks.html#method-title">method and caveats</a>.')


def run_count_for(dataset_name, key, result):
    if result.get("status") == "NOT MEASURED":
        return "Not measured"
    if key == "lcovmerge":
        count = DATA["method"]["repeat_counts"].get(dataset_name)
        return f'{count} measured' if count else "Not reported"
    if key == "lcov-result-merger":
        return "Prior canonical; count unknown"
    if key == "lcov":
        count = DATA["method"].get("comparison_runs_per_tool")
        time_count = f'Time: {count} run(s)' if count else "Time: run count not reported"
        if result.get("rss_bytes") is not None:
            return f'{time_count}; RSS: prior canonical, count unknown'
        return f'{time_count}; RSS unavailable for current failed run'
    count = DATA["method"].get("comparison_runs_per_tool")
    return f'{count} measured' if count else "Not reported"


def site_results_rows():
    rows = []
    labels = display_tool_labels()
    for source in DATA["datasets"]:
        keys = ("lcovmerge", "lcov", "lcov-result-merger")
        for index, key in enumerate(keys):
            result = source["results"][key]
            cells = []
            if index == 0:
                input_bytes = source.get("input_bytes")
                input_size = f'{input_bytes:,} bytes' if input_bytes is not None else "Not supplied"
                shard_text = source.get("shards", "Shard count not reported")
                cells.append(f'<th scope="rowgroup" rowspan="{len(keys)}">{html.escape(source["name"])}</th>')
                cells.append(f'<td rowspan="{len(keys)}">{html.escape(input_size)}; {html.escape(shard_text)}</td>')
            elapsed = (seconds_display(result["time_s"])
                       if result.get("time_s") is not None else result.get("status", "Not reported"))
            rss = bytes_display(result["rss_bytes"]) if result.get("rss_bytes") is not None else result.get("rss_display", "—")
            throughput = (f'{result["throughput_mb_s"]:,.1f} MB/s'
                          if result.get("throughput_mb_s") is not None else "—")
            primary = ' class="tool-primary"' if key == "lcovmerge" else ""
            cells.extend([
                f'<th scope="row"{primary}>{html.escape(labels[key])}</th>',
                f'<td class="table-numeric">{html.escape(elapsed)}</td>',
                f'<td class="table-numeric">{html.escape(rss)}</td>',
                f'<td class="table-numeric">{html.escape(throughput)}</td>',
                f'<td>{html.escape(run_count_for(source["name"], key, result))}</td>',
                f'<td>{html.escape(result.get("status", "Not reported"))}</td>',
            ])
            rows.append("<tr>" + "".join(cells) + "</tr>")
    return "\n".join(rows)


def site_benchmark_method():
    repeats = ", ".join(f'{name}: {count}' for name, count in DATA["method"]["repeat_counts"].items())
    return (
        f'Primary comparison host: {html.escape(DATA["host"]["os"])} · '
        f'{html.escape(DATA["host"]["cpu"])} · measured {html.escape(DATA["measured_on"])}. '
        f'lcovmerge measured run counts by dataset: {html.escape(repeats)}; '
        f'lcov elapsed time ran {DATA["method"]["comparison_runs_per_tool"]} time(s) on each measured dataset; REAL was not measured. '
        f'Successful lcov RSS values and lcov-result-merger values are prior canonical measurements; their run counts are not reported. '
        f'Cache and machine-load limits: {html.escape(DATA["host"]["cache"])}. '
        f'These are primary-host generated-input results. <a href="data/benchmarks.json">Open the canonical data</a>.'
    )


def site_benchmark_charts():
    mapping = {"M": "medium", "L": "large", "XL-single": "single_file", "PATH-HEAVY": "path_heavy"}
    groups = []
    for name in ("M", "L", "XL-single", "PATH-HEAVY"):
        source = dataset(name)
        input_bytes = source.get("input_bytes")
        input_size = f'{input_bytes:,} bytes' if input_bytes is not None else "Input size not reported"
        shard_text = source.get("shards", "Shard count not reported")
        description = source.get("description", "Generated LCOV input.")
        charts = []
        for metric_name, heading, dataset_id in (
            ("time", "Elapsed time · seconds", mapping[name]),
            ("rss", "Peak resident memory · MiB axis; exact bytes shown", mapping[name]),
        ):
            charts.append(
                f'<article class="chart-card"><h4>{heading}</h4>'
                f'<div data-chart="{metric_name}" data-dataset="{dataset_id}">{chart(name, metric_name)}</div>'
                '</article>'
            )
        groups.append(
            f'<section class="benchmark-dataset" aria-labelledby="dataset-{mapping[name]}-title">'
            f'<div class="dataset-heading"><h3 id="dataset-{mapping[name]}-title">Dataset {html.escape(name)}</h3>'
            f'<span>{html.escape(input_size)} · {html.escape(shard_text)}</span></div>'
            f'<p class="dataset-description">{html.escape(description)}</p>'
            f'<div class="dataset-charts">{"".join(charts)}</div></section>'
        )
    return "\n".join(groups)


def site_host_results():
    entries = DATA.get("host_results", [])
    if not isinstance(entries, list) or not entries:
        return ""
    blocks = []
    for entry_index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        host = entry.get("host") if isinstance(entry.get("host"), dict) else {}
        label = entry.get("label") or host.get("runner_label") or host.get("os") or "Host not reported"
        details = [host.get(key) for key in ("os", "kernel", "cpu_model") if host.get(key)]
        host_details = " · ".join(str(value) for value in details) or "Host details not reported"
        method = entry.get("method") if isinstance(entry.get("method"), dict) else {}
        repeats = method.get("repeat_counts") if isinstance(method.get("repeat_counts"), dict) else {}
        rows = []
        for dataset_record in entry.get("datasets", []):
            if not isinstance(dataset_record, dict):
                continue
            dataset_name = dataset_record.get("name", "Dataset not reported")
            input_bytes = dataset_record.get("input_bytes")
            input_size = f'{input_bytes:,} bytes' if isinstance(input_bytes, int) else "Input not reported"
            shards = dataset_record.get("shards", "Shard count not reported")
            results = dataset_record.get("results") if isinstance(dataset_record.get("results"), dict) else {}
            for key, result in results.items():
                if not isinstance(result, dict):
                    continue
                tool = entry.get("tool_labels", {}).get(key, key)
                elapsed_value = result.get("time_s")
                elapsed = f'{elapsed_value:,.3f} s' if isinstance(elapsed_value, (int, float)) else result.get("status", "—")
                rss_bytes = result.get("rss_bytes")
                rss = f'{rss_bytes:,} B' if isinstance(rss_bytes, int) else result.get("rss_display", "—")
                throughput_value = result.get("throughput_mb_s")
                throughput = f'{throughput_value:,.1f} MB/s' if isinstance(throughput_value, (int, float)) else "—"
                run_count = result.get("run_count")
                if run_count is None:
                    run_count = repeats.get(dataset_name)
                run_display = f'{run_count} measured' if isinstance(run_count, int) else "Not reported"
                rows.append(
                    '<tr>'
                    f'<th scope="row">{html.escape(str(dataset_name))}</th>'
                    f'<td>{html.escape(input_size)}; {html.escape(str(shards))}</td>'
                    f'<td>{html.escape(str(tool))}</td>'
                    f'<td class="table-numeric">{html.escape(str(elapsed))}</td>'
                    f'<td class="table-numeric">{html.escape(str(rss))}</td>'
                    f'<td class="table-numeric">{html.escape(str(throughput))}</td>'
                    f'<td>{html.escape(run_display)}</td>'
                    f'<td>{html.escape(str(result.get("status", "Not reported")))}</td>'
                    '</tr>'
                )
            if not results:
                rows.append(
                    '<tr>'
                    f'<th scope="row">{html.escape(str(dataset_name))}</th>'
                    f'<td>{html.escape(input_size)}; {html.escape(str(shards))}</td>'
                    '<td>—</td><td>—</td><td>—</td><td>—</td><td>Not reported</td>'
                    f'<td>{html.escape(str(dataset_record.get("status", "Not measured")))}</td></tr>'
                )
        blocks.append(
            f'<div class="host-result-block"><h3>{html.escape(str(label))}</h3>'
            f'<p>Host: {html.escape(host_details)} · measured {html.escape(str(entry.get("measurement_date", "date not reported")))}. '
            'This host is reported separately from the primary comparison.</p>'
            '<div class="table-wrap"><table class="benchmark-table">'
            '<caption>Dataset results for this additional host; measured runs and statuses are shown in each row.</caption>'
            '<thead><tr><th scope="col">Dataset</th><th scope="col">Input</th><th scope="col">Tool</th>'
            '<th scope="col">Elapsed</th><th scope="col">Peak RSS</th><th scope="col">Throughput</th>'
            '<th scope="col">Runs</th><th scope="col">Status</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div></div>'
        )
    return (
        '<h2 id="additional-host-results-title">Additional host results</h2>'
        '<p>Each additional host is grouped by its recorded label and remains separate from the primary-host comparison above.</p>'
        + "\n".join(blocks)
    )


def real_project_records():
    return [item for item in REAL_DATA["projects"] if item["project"] != "REAL composite"]


def real_project_rows(markdown=False):
    rows = []
    for item in REAL_DATA["projects"]:
        project = item["project"]
        if markdown:
            lcovmerge = item["lcovmerge"]
            lcov = item["lcov"]
            rows.append(
                f'| {project} | {item["input_mb"]:.3f} MB · {item["shards"]} shards | '
                f'{lcovmerge["wall_seconds"]:.3f} s · {lcovmerge["peak_rss_mib"]:.2f} MiB | '
                f'{lcov["wall_seconds"]:.3f} s · {lcov["peak_rss_mib"]:.2f} MiB | '
                f'{item["lcov_comparison"]} / {item["genhtml"]} |'
            )
        else:
            lcovmerge = item["lcovmerge"]
            lcov = item["lcov"]
            rows.append(
                "<tr>"
                f'<th scope="row">{html.escape(project)}</th>'
                f'<td class="table-numeric">{item["input_mb"]:.3f} MB · {item["shards"]} shards</td>'
                f'<td class="table-numeric">{lcovmerge["wall_seconds"]:.3f} s / {lcovmerge["peak_rss_mib"]:.2f} MiB</td>'
                f'<td class="table-numeric">{lcov["wall_seconds"]:.3f} s / {lcov["peak_rss_mib"]:.2f} MiB</td>'
                f'<td>{html.escape(item["lcov_comparison"])} / {html.escape(item["genhtml"])}</td>'
                "</tr>"
            )
    return "\n".join(rows)


def real_projects_html():
    projects = real_project_records()
    sizes = [project["input_mb"] for project in projects]
    composite = next(item for item in REAL_DATA["projects"] if item["project"] == "REAL composite")
    shard_counts = sorted({project["shards"] for project in projects})
    capture_count = shard_counts[0] if len(shard_counts) == 1 else "multiple"
    sort_inputs = REAL_DATA["external_sort_method"].split()[0]
    return f'''<div class="section-heading">
          <p class="eyebrow">Project-derived captures · small inputs</p>
          <h2 id="real-projects-title">Tested on real projects.</h2>
          <p>{len(projects)} small project-derived captures, with {capture_count} independently captured shards per project, were compared with LCOV 2.6. Each input was {min(sizes):.3f}–{max(sizes):.3f} MB; the composite was {composite["input_mb"]:.3f} MB across {composite["shards"]} shards. These are useful compatibility checks, not large production workloads.</p>
        </div>
        <div class="table-wrap">
          <table class="benchmark-table real-project-table">
            <caption>Measurements from {html.escape(REAL_DATA["measurement_date"])} on one {html.escape(REAL_DATA["host"]["os"])} {html.escape(REAL_DATA["host"]["architecture"])} host; elapsed time / peak RSS. Timed-run counts are not reported. See the <a href="https://github.com/megasoft1978/lcovmerge/blob/main/docs/validation/real-projects.md">method and exclusions</a>.</caption>
            <thead><tr><th scope="col">Project</th><th scope="col">Input</th><th scope="col">lcovmerge</th><th scope="col">LCOV 2.6</th><th scope="col">Semantic comparison / genhtml</th></tr></thead>
            <tbody>{real_project_rows()}</tbody>
          </table>
        </div>
        <p class="small-note">All listed project checks passed normalized record comparisons and genhtml. The separate {html.escape(sort_inputs)}-input external-sort, reverse-order and job-determinism checks passed. Lua used portable test mode; capture warnings, exclusions, and method details are documented in <a href="https://github.com/megasoft1978/lcovmerge/blob/main/docs/validation/real-projects.md">the real-project validation record ↗</a>. These are small compatibility checks on one host; they are not large production benchmarks.</p>'''


def readme_real_projects():
    projects = real_project_records()
    sizes = [project["input_mb"] for project in projects]
    composite = next(item for item in REAL_DATA["projects"] if item["project"] == "REAL composite")
    shard_counts = sorted({project["shards"] for project in projects})
    capture_count = shard_counts[0] if len(shard_counts) == 1 else "multiple"
    sort_inputs = REAL_DATA["external_sort_method"].split()[0]
    return "\n".join([
        f'{len(projects)} small project-derived LCOV captures were checked on one {REAL_DATA["host"]["os"]} {REAL_DATA["host"]["architecture"]} host. Each project used {capture_count} shards,',
        f'with inputs from {min(sizes):.3f} MB to {max(sizes):.3f} MB; the composite was {composite["input_mb"]:.3f} MB across {composite["shards"]} shards. These are',
        "small compatibility checks, not large production workloads. Normalized record comparisons and genhtml",
        f'passed for each project. The separate {sort_inputs}-input external-sort and order/job determinism checks passed.',
        "Lua used portable test mode. See [the validation record](docs/validation/real-projects.md) for toolchain,",
        "capture warnings, and method details.",
        "",
        "| Project | Input | lcovmerge time / peak RSS | LCOV 2.6 time / peak RSS | Comparison / genhtml |",
        "| --- | ---: | ---: | ---: | --- |",
        real_project_rows(markdown=True),
    ])


def site_benchmarks():
    """Map canonical measurements to the schema consumed by docs/site/site.js."""
    ids = {"M": "medium", "L": "large", "XL-single": "single_file"}
    order = {"M": 0, "L": 1, "XL-single": 2, "S": 3, "PATH-HEAVY": 4, "REAL": 5}
    labels = display_tool_labels()
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
                "seconds_display": seconds_display(seconds) if seconds is not None else item["status"],
                "peak_rss_mib": rss / (1024 * 1024) if rss is not None else None,
                "rss_display": (bytes_display(rss) if rss is not None
                                else item.get("rss_display", item["status"])),
                "throughput_mbs": item.get("throughput_mb_s"),
                "throughput_display": (f'{item["throughput_mb_s"]:,.1f} MB/s'
                                       if item.get("throughput_mb_s") is not None else "—"),
                "status": item["status"],
            })
        site_dataset = {
            "id": ids.get(source["name"], source["name"].lower().replace("-", "_")),
            "name": source["name"],
            "description": source.get("description", ""),
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
        "repeat_counts": DATA["method"]["repeat_counts"],
        "comparison_runs_per_tool": DATA["method"]["comparison_runs_per_tool"],
        "cache": DATA["host"]["cache"],
        "source": "data/benchmarks.json",
        "data_kind": DATA.get("data_kind", "generated"),
        "environment": DATA["environment"],
    }
    site_data = {**metadata, "datasets": datasets}
    if isinstance(DATA.get("host_results"), list):
        site_data["host_results"] = DATA["host_results"]
    return site_data


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
    if start not in text or end not in text:
        raise SystemExit(f"generated section markers missing in {path.relative_to(ROOT)}")
    before, rest = text.split(start, 1)
    _, after = rest.split(end, 1)
    path.write_text(before + start + "\n" + content + "\n" + end + after)


def replace_site_host_results(path, content):
    replace_named_section(path, SITE_HOST_RESULTS_START, SITE_HOST_RESULTS_END, content)
    text = path.read_text()
    section_start = text.find('<section id="additional-host-results"')
    if section_start < 0:
        raise SystemExit(f"additional host results section missing in {path.relative_to(ROOT)}")
    section_end = text.find(">", section_start)
    if section_end < 0:
        raise SystemExit(f"malformed additional host results section in {path.relative_to(ROOT)}")
    opening = text[section_start:section_end + 1]
    if content.strip():
        opening = opening.replace(" hidden", "")
    elif " hidden" not in opening:
        opening = opening[:-1] + " hidden>"
    text = text[:section_start] + opening + text[section_end + 1:]
    path.write_text(text)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--readme-site-only",
        action="store_true",
        help="update README and site data without changing docs/BENCHMARKS.md",
    )
    args = parser.parse_args()

    replace_section(ROOT / "README.md", readme_table())
    replace_named_section(ROOT / "README.md", "<!-- BENCH-CONTEXT:START -->", "<!-- BENCH-CONTEXT:END -->", context())
    replace_named_section(ROOT / "README.md", "<!-- BAZEL-EVIDENCE:START -->", "<!-- BAZEL-EVIDENCE:END -->", textwrap.fill(bazel_evidence(), width=110, break_long_words=False, break_on_hyphens=False))
    replace_named_section(ROOT / "README.md", HERO_START, HERO_END, readme_proof())
    replace_named_section(ROOT / "README.md", REAL_START, REAL_END, readme_real_projects())

    index = ROOT / "docs/site/index.html"
    replace_named_section(index, HERO_START, HERO_END, hero_proof())
    replace_named_section(index, "<!-- HERO-CARD:START -->", "<!-- HERO-CARD:END -->", hero_card())
    for (metric_name, dataset_name), (chart_start, chart_end) in CHART_MARKERS.items():
        replace_named_section(index, chart_start, chart_end, chart("M", metric_name))
        alt_start, alt_end = CHART_ALT_MARKERS[metric_name]
        replace_named_section(index, alt_start, alt_end, html.escape(chart_text_alternative(metric_name)))
    replace_named_section(index, MEDIUM_RESULTS_START, MEDIUM_RESULTS_END, medium_results_rows())
    replace_named_section(index, MEDIUM_CAPTION_START, MEDIUM_CAPTION_END, medium_caption())
    replace_named_section(index, REAL_START, REAL_END, real_projects_html())

    benchmark_page = ROOT / "docs/site/benchmarks.html"
    replace_named_section(benchmark_page, SITE_CHARTS_START, SITE_CHARTS_END, site_benchmark_charts())
    replace_named_section(benchmark_page, SITE_RESULTS_START, SITE_RESULTS_END, site_results_rows())
    replace_named_section(benchmark_page, SITE_METHOD_START, SITE_METHOD_END, site_benchmark_method())
    replace_site_host_results(benchmark_page, site_host_results())

    if not args.readme_site_only:
        replace_section(ROOT / "docs/BENCHMARKS.md", full_table())
        replace_named_section(ROOT / "docs/BENCHMARKS.md", "<!-- BENCH-METHOD:START -->", "<!-- BENCH-METHOD:END -->", context())
        replace_named_section(ROOT / "docs/BENCHMARKS.md", "<!-- BENCH-CAVEATS:START -->", "<!-- BENCH-CAVEATS:END -->", caveats())
        replace_named_section(ROOT / "docs/BENCHMARKS.md", "<!-- SCALING:START -->", "<!-- SCALING:END -->", scaling_table())

    site_data = site_benchmarks()
    (ROOT / "docs/site/data/benchmarks.json").write_text(
        json.dumps(site_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (ROOT / "docs/site/data/real-projects.json").write_text(
        json.dumps(REAL_DATA, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
