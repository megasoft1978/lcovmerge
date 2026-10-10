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
START = "<!-- BENCHMARKS:START -->"
END = "<!-- BENCHMARKS:END -->"
HERO_START = "<!-- HERO-PROOF:START -->"
HERO_END = "<!-- HERO-PROOF:END -->"
REAL_START = "<!-- REAL-PROJECTS:START -->"
REAL_END = "<!-- REAL-PROJECTS:END -->"
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
        return plain_copy(result["rss_display"])
    if result.get("rss_bytes") is not None:
        return f'{result["rss_bytes"] / (1024 * 1024):,.2f} MiB'
    if result["rss_mib"] is None:
        return "—"
    return f'{result["rss_mib"]:,.1f} MiB'


def plain_copy(value):
    """Use plain terms in rendered copy while preserving source values and statuses."""
    text = str(value)
    text = re.sub(r"\bRSS\b", "peak resident memory", text, flags=re.IGNORECASE)
    text = re.sub(r"\btracefiles\b", ".info files", text, flags=re.IGNORECASE)
    text = re.sub(r"\btracefile\b", ".info file", text, flags=re.IGNORECASE)
    text = text.replace("external-sort", "temporary-file sorting")
    text = text.replace("external sort", "temporary-file sorting")
    text = re.sub(r"prior canonical (measurement|measurements|benchmark|benchmarks)",
                  r"earlier recorded \1", text, flags=re.IGNORECASE)
    text = text.replace("full-run peak unavailable", "full-run peak resident memory unavailable")
    if text.startswith("Dataset M sorted regular shards use the direct single-threaded path;"):
        text = ("Dataset M's sorted regular inputs use one merge worker; --jobs results show run variation, "
                "not the effect of adding workers.")
    return text


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
    recorded_version = DATA.get("version")
    recorded_label = labels.get("lcovmerge", "")
    if not recorded_version or recorded_label != f"lcovmerge {recorded_version}":
        raise SystemExit("data/benchmarks.json version and lcovmerge tool label do not match")
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
        "| lcovmerge jobs argument | Time | Peak resident memory | Throughput |",
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
        textwrap.fill(plain_copy(item), width=110, initial_indent="- ",
                      subsequent_indent="  ", break_long_words=False,
                      break_on_hyphens=False)
        for item in DATA["caveats"]
    )


def bazel_evidence():
    evidence = DATA["external_evidence"]["bazel_issue_26383"]
    return (
        "Large LCOV .info files can make a merge job the most memory hungry part of a "
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
    return f'''<p class="eyebrow">LCOV .info merger · C11 · MIT</p>
        <h1 id="hero-title">Merge existing LCOV shards after your test jobs.</h1>
        <p class="hero-copy">Turn existing .info files into one LCOV file. Keep your collector and report step.</p>
        <div class="hero-actions">
          <a class="button" href="https://github.com/megasoft1978/lcovmerge/releases/tag/v{VERSION_MATCH.group(1)}">Download v{VERSION_MATCH.group(1)}</a>
          <a class="action-link" href="docs.html#github-actions">Use in GitHub Actions</a>
        </div>
        <div class="quick-merge card" aria-labelledby="quick-merge-title">
          <p class="eyebrow">Quick merge</p>
          <h2 id="quick-merge-title">Merge, then keep your report step.</h2>
          <pre class="code-block" data-copy-label="Copy merge and genhtml commands"><code>lcovmerge coverage/shard-*.info -o coverage/merged.info
genhtml coverage/merged.info --output-directory coverage/html</code></pre>
        </div>
        <p class="hero-note">The memory setting limits memory reserved for coverage records while sorting. Total process memory can be higher, and sorting uses temporary disk. <a href="https://github.com/megasoft1978/lcovmerge/blob/main/docs/LIMITATIONS.md">Read the limits</a>.</p>'''


def hero_card():
    medium = dataset("M")
    results = medium["results"]
    lcovmerge = results["lcovmerge"]
    merge_runs = DATA["method"]["repeat_counts"]["M"]
    labels = display_tool_labels()
    return f'''<p class="eyebrow">Measured on generated LCOV</p>
        <h2 id="hero-results-title">Dataset M · {medium["input_bytes"]:,} bytes · {html.escape(medium["shards"])}</h2>
        <p><strong>{html.escape(labels["lcovmerge"])}</strong>: {seconds_display(lcovmerge["time_s"])} median of {merge_runs} runs; {bytes_display(lcovmerge["rss_bytes"])} peak resident memory.</p>
        <p>One {html.escape(DATA["host"]["os"])} host. {html.escape(DATA["host"]["cache"])}.</p>
        <a class="text-link" href="benchmarks.html#method-title">Full data and method ↗</a>'''


def readme_proof():
    medium = dataset("M")
    lcovmerge = medium["results"]["lcovmerge"]
    lcov = medium["results"]["lcov"]
    runs = DATA["method"]["repeat_counts"]["M"]
    labels = display_tool_labels()
    return "\n".join([
        f'> **Dataset M · generated, {medium["shards"]} · {medium["input_bytes"]:,} input bytes**<br>',
        f'> {labels["lcovmerge"]}: **{seconds_display(lcovmerge["time_s"])}**, **{bytes_display(lcovmerge["rss_bytes"])} peak resident memory** (median of {runs} runs).<br>',
        f'> {DATA["tool_labels"]["lcov"]}: **{seconds_display(lcov["time_s"])}**, **{bytes_display(lcov["rss_bytes"])} peak resident memory** (one run; peak resident memory from an earlier recorded measurement).<br>',
        f'> One {DATA["host"]["os"]} {DATA["host"]["cpu"]} host; {DATA["host"]["cache"]}.',
    ])


def chart(dataset_name="M"):
    """Render the compact elapsed-time chart; exact values live in the table."""
    source = dataset(dataset_name)
    labels = display_tool_labels()
    keys = ("lcovmerge", "lcov", "lcov-result-merger")
    values = [(key, source["results"][key], source["results"][key]["time_s"])
              for key in keys]
    numeric = [value for _, _, value in values if value is not None]
    maximum = max(numeric, default=1)
    if maximum <= 0:
        maximum = 1
    input_size = (f'{source["input_bytes"]:,} bytes'
                  if source.get("input_bytes") is not None else "Input size not reported")
    pieces = [
        '<figcaption><h3 id="chart-m-title">'
        f'Dataset {html.escape(source["name"])} · elapsed time in seconds</h3>'
        f'<p>Generated input: {html.escape(input_size)} · {html.escape(source["shards"])}. '
        'Bars use a zero-based scale; the table gives exact values and statuses.</p></figcaption>',
        f'<ul class="bar-chart" aria-label="{html.escape(source["name"])} elapsed-time comparison; '
        'exact values and statuses are in the table below">'
    ]
    for key, _result, value in values:
        label = labels.get(key, key)
        bar_size = max(0, value / maximum * 100) if value is not None else 0
        pieces.append(
            f'<li class="bar-chart-item {key}">'
            f'<div class="bar-chart-label"><span class="bar-chart-tool">{html.escape(label)}</span></div>'
            f'<div class="bar-track" aria-hidden="true"><span class="bar-fill" style="width:{bar_size:.3f}%"></span></div>'
            '</li>'
        )
    pieces.append("</ul>")
    ticks = []
    for index in range(3):
        tick = maximum * index / 2
        tick_text = f'{tick:.1f}' if tick < 10 else f'{tick:,.0f}'
        ticks.append(f'<span>{tick_text} s</span>')
    pieces.append(
        f'<div class="bar-axis" role="img" aria-label="Zero-based scale from 0 to {maximum:,.3f} seconds">'
        + "".join(ticks) + "</div>"
    )
    return "".join(pieces)


def run_count_for(dataset_name, key, result):
    if result.get("status") == "NOT MEASURED":
        return "Not measured"
    if key == "lcovmerge":
        count = DATA["method"]["repeat_counts"].get(dataset_name)
        return f'{count} measured' if count else "Not reported"
    if key == "lcov-result-merger":
        return "Earlier recorded measurement; run count not reported"
    if key == "lcov":
        count = DATA["method"].get("comparison_runs_per_tool")
        time_count = f'Time: {count} run(s)' if count else "Time: run count not reported"
        if result.get("rss_bytes") is not None:
            return f'{time_count}; peak resident memory from an earlier measurement; run count not reported'
        return f'{time_count}; peak resident memory unavailable for this failed run'
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
                input_size = f'{input_bytes:,} bytes' if input_bytes is not None else "Input not reported"
                shard_text = source.get("shards", "Shard count not reported")
                cells.append(f'<th scope="rowgroup" rowspan="{len(keys)}">{html.escape(source["name"])}</th>')
                cells.append(f'<td rowspan="{len(keys)}">{html.escape(input_size)}; {html.escape(shard_text)}</td>')
            elapsed = (seconds_display(result["time_s"])
                       if result.get("time_s") is not None else result.get("status", "Not reported"))
            rss = bytes_display(result["rss_bytes"]) if result.get("rss_bytes") is not None else plain_copy(result.get("rss_display", "—"))
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
    host = DATA["host"]
    method = DATA["method"]
    summary = (
        f'<p>Primary host: {html.escape(host["os"])} · {html.escape(host["cpu"])} · '
        f'{host["cores"]} cores · {host["memory_gib"]} GiB RAM; measured {html.escape(DATA["measured_on"])}. '
        f'lcovmerge run counts: {html.escape(repeats)}. lcov elapsed time was measured '
        f'{method["comparison_runs_per_tool"]} time(s) per dataset; successful lcov peak resident memory values and '
        'lcov-result-merger results are earlier recorded measurements with run counts not reported. '
        f'Cache conditions: {html.escape(host["cache"])} '
        '<a href="data/benchmarks.json">Open the published benchmark record</a>.</p>'
    )
    caveat_items = "".join(f'<li>{html.escape(plain_copy(item))}</li>' for item in DATA["caveats"])
    return summary + f'<ul>{caveat_items}</ul>'


def site_host_results():
    entries = DATA.get("host_results", [])
    if not isinstance(entries, list) or not entries:
        return ""
    blocks = []
    for entry_index, entry in enumerate(entries, start=1):
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
                rss = f'{rss_bytes:,} B' if isinstance(rss_bytes, int) else plain_copy(result.get("rss_display", "—"))
                throughput_value = result.get("throughput_mb_s")
                throughput = f'{throughput_value:,.1f} MB/s' if isinstance(throughput_value, (int, float)) else "—"
                run_count = result.get("run_count")
                if run_count is None:
                    run_count = repeats.get(dataset_name)
                if result.get("status") in {"NOT MEASURED", "SKIPPED"}:
                    run_display = "Not measured"
                else:
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
        date = str(entry.get("measurement_date", "date not reported"))
        row_count = len(rows)
        blocks.append(
            f'<details class="host-result-block">'
            f'<summary><strong>{html.escape(str(label))}</strong>'
            f'<span>{row_count} rows · measured {html.escape(date)} · separate measurement</span></summary>'
            f'<p>Host: {html.escape(host_details)}. This result set stays separate from the primary comparison.</p>'
            f'<div class="table-wrap" role="region" tabindex="0" aria-label="Additional host results {entry_index}">'
            '<table class="benchmark-table">'
            '<caption>Dataset results for this host; each row includes its own tool, run count, and status.</caption>'
            '<thead><tr><th scope="col">Dataset</th><th scope="col">Input</th><th scope="col">Tool and build</th>'
            '<th scope="col">Elapsed</th><th scope="col">Peak resident memory</th><th scope="col">Throughput</th>'
            '<th scope="col">Runs</th><th scope="col">Status</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div></details>'
        )
    if not blocks:
        return ""
    return (
        '<section class="section host-results" id="additional-host-results" aria-labelledby="additional-host-results-title">'
        '<div class="shell"><div class="section-heading">'
        '<p class="eyebrow">Separate measurements</p>'
        '<h2 id="additional-host-results-title">Additional host results</h2>'
        '<p>Each group contains its recorded tools and builds. Results are not combined into a cross-host ranking.</p>'
        '</div><div class="host-result-list">'
        + "\n".join(blocks)
        + '</div></div></section>'
    )


def real_project_records():
    return [item for item in REAL_DATA["projects"] if item["project"] != "REAL composite"]


def real_project_rows():
    rows = []
    for item in REAL_DATA["projects"]:
        project = item["project"]
        lcovmerge = item["lcovmerge"]
        lcov = item["lcov"]
        rows.append(
            f'| {project} | {item["input_mb"]:.3f} MB · {item["shards"]} shards | '
            f'{lcovmerge["wall_seconds"]:.3f} s · {lcovmerge["peak_rss_mib"]:.2f} MiB | '
            f'{lcov["wall_seconds"]:.3f} s · {lcov["peak_rss_mib"]:.2f} MiB | '
            f'{item["lcov_comparison"]} / {item["genhtml"]} |'
        )
    return "\n".join(rows)


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
        f'passed for each project. The separate {sort_inputs}-input temporary-file sorting checks and bytewise checks across input order and worker settings passed.',
        "See [the validation record](docs/validation/real-projects.md) for toolchain, capture warnings, and method details.",
        "",
        "| Project | Input | lcovmerge time / peak resident memory | LCOV 2.6 time / peak resident memory | Comparison / genhtml |",
        "| --- | ---: | ---: | ---: | --- |",
        real_project_rows(),
    ])


def site_benchmarks():
    """Map canonical measurements to the downloadable site data file."""
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
                                else plain_copy(item.get("rss_display", item["status"]))),
                "throughput_mbs": item.get("throughput_mb_s"),
                "throughput_display": (f'{item["throughput_mb_s"]:,.1f} MB/s'
                                       if item.get("throughput_mb_s") is not None else "—"),
                "status": item["status"],
            })
        site_dataset = {
            "id": ids.get(source["name"], source["name"].lower().replace("-", "_")),
            "name": source["name"],
            "description": plain_copy(source.get("description", "")),
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
    if text.count(START) != 1 or text.count(END) != 1:
        raise SystemExit(f"benchmark markers must each occur once in {path.relative_to(ROOT)}")
    before, rest = text.split(START, 1)
    if rest.find(END) < 0:
        raise SystemExit(f"benchmark markers are out of order in {path.relative_to(ROOT)}")
    _, after = rest.split(END, 1)
    note = "<!-- Generated by tools/render_benchmarks.py from data/benchmarks.json. -->\n"
    path.write_text(before + START + "\n" + note + content + "\n" + END + after)


def replace_named_section(path, start, end, content):
    text = path.read_text()
    if text.count(start) != 1 or text.count(end) != 1:
        raise SystemExit(f"generated section markers must each occur once in {path.relative_to(ROOT)}")
    before, rest = text.split(start, 1)
    if rest.find(end) < 0:
        raise SystemExit(f"generated section markers are out of order in {path.relative_to(ROOT)}")
    _, after = rest.split(end, 1)
    path.write_text(before + start + "\n" + content + "\n" + end + after)


def replace_site_host_results(path, content):
    replace_named_section(path, SITE_HOST_RESULTS_START, SITE_HOST_RESULTS_END, content)


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

    benchmark_page = ROOT / "docs/site/benchmarks.html"
    replace_named_section(benchmark_page, SITE_CHARTS_START, SITE_CHARTS_END, chart())
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
    (ROOT / "docs/site/data/real-projects.json").unlink(missing_ok=True)


if __name__ == "__main__":
    main()
