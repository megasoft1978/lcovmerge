#!/usr/bin/env python3
"""Render benchmark tables and site data from the canonical benchmark JSON."""

import argparse
import html
import json
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = json.loads((ROOT / "data/benchmarks.json").read_text())
REAL_DATA = json.loads((ROOT / "data/real-projects.json").read_text())
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


def dataset(name):
    return next(item for item in DATA["datasets"] if item["name"] == name)


def seconds_display(value):
    return f'{value:.6f}'.rstrip("0").rstrip(".") + " s"


def bytes_display(value):
    return f"{value:,} B"


def hero_proof():
    medium = dataset("M")
    lcovmerge = medium["results"]["lcovmerge"]
    lcov = medium["results"]["lcov"]
    shard_count = medium["shards"].split()[0]
    return f'''<p class="eyebrow">Dataset M · generated LCOV · {html.escape(medium["shards"])}</p>
        <h1>{html.escape(shard_count)} shards.<span>{seconds_display(lcovmerge["time_s"])}</span></h1>
        <p class="hero-copy">lcovmerge processed {medium["input_bytes"]:,} input bytes. On the same generated input, {html.escape(DATA["tool_labels"]["lcov"])} took {seconds_display(lcov["time_s"])}.</p>
        <div class="actions">
          <a class="button" href="#install">Install lcovmerge</a>
          <a class="button secondary" href="docs.html#github-actions">Use it in CI</a>
        </div>
        <p class="hero-note">M is a synthetic workload on one {html.escape(DATA["host"]["os"])} {html.escape(DATA["host"]["cpu"])} host. The measurements and caveats are listed below.</p>'''


def hero_card():
    medium = dataset("M")
    results = medium["results"]
    lcovmerge = results["lcovmerge"]
    lcov = results["lcov"]
    return f'''<p class="eyebrow">Measured on generated LCOV</p>
        <h2 id="hero-results-title">Same {medium["input_bytes"]:,}-byte input</h2>
        <p id="hero-benchmark-context">{html.escape(DATA["host"]["os"])}, {html.escape(DATA["host"]["cpu"])} · measured {html.escape(DATA["measured_on"])}</p>
        <div class="metric-grid">
          <div class="metric">
            <strong data-benchmark-value="datasets.0.results.0.seconds_display">{seconds_display(lcovmerge["time_s"])}</strong>
            <span>lcovmerge · median of {DATA["method"]["repeat_counts"]["M"]} runs</span>
            <span class="metric-detail">{bytes_display(lcovmerge["rss_bytes"])} peak RSS</span>
          </div>
          <div class="metric">
            <strong data-benchmark-value="datasets.0.results.1.seconds_display">{seconds_display(lcov["time_s"])}</strong>
            <span>{html.escape(DATA["tool_labels"]["lcov"])} · one run</span>
            <span class="metric-detail">{bytes_display(lcov["rss_bytes"])} peak RSS</span>
          </div>
        </div>
        <p class="bench-note">{html.escape(DATA["tool_labels"]["lcov"])} ran once; its RSS is from a prior canonical measurement. {html.escape(DATA["host"]["cache"].rstrip("."))}.</p>
        <a class="text-link" href="benchmarks.html">See the full results and caveats ↗</a>'''


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
    for key in ("lcovmerge", "lcov", "lcov-result-merger"):
        result = source["results"][key]
        value = result["time_s"] if is_time else result["rss_bytes"]
        values.append((key, result, value))
    present = [(key, result, value) for key, result, value in values if value is not None]
    maximum = max(value for _, _, value in present)
    width = 760
    left = 188
    right = 176
    top = 18
    row_height = 38
    bar_width = width - left - right
    height = top + len(values) * row_height + 22
    kind = "time" if is_time else "rss"
    title_id = f"chart-{dataset_name.lower()}-{kind}-title"
    desc_id = f"chart-{dataset_name.lower()}-{kind}-desc"
    title = f'{source["name"]} dataset {metric_label}'
    details = []
    for key, result, value in values:
        if value is None:
            details.append(f'{DATA["tool_labels"][key]}: {result["status"]}.')
        elif is_time:
            details.append(f'{DATA["tool_labels"][key]}: {seconds_display(value)}.')
        else:
            details.append(f'{DATA["tool_labels"][key]}: {bytes_display(result["rss_bytes"])}.')
    description = f'{source["name"]} is a generated input with {source["shards"]}. ' + " ".join(details)
    pieces = [
        f'<svg class="benchmark-chart" viewBox="0 0 {width} {height}" role="img" aria-labelledby="{title_id} {desc_id}">',
        f'<title id="{title_id}">{html.escape(title)}</title>',
        f'<desc id="{desc_id}">{html.escape(description)}</desc>',
    ]
    for index in range(5):
        fraction = index / 4
        x = left + fraction * bar_width
        tick = maximum * fraction
        label = f'{tick:.1f} s' if is_time and tick < 10 else (f'{tick:.0f} s' if is_time else f'{tick / (1024 * 1024):,.0f}')
        pieces.append(f'<line class="chart-gridline" x1="{x:.2f}" y1="{top}" x2="{x:.2f}" y2="{height - 17}" />')
        pieces.append(f'<text class="chart-muted" x="{x:.2f}" y="{height - 1}" text-anchor="middle">{html.escape(label)}</text>')
    for index, (key, result, value) in enumerate(values):
        y = top + index * row_height + 7
        tool = html.escape(DATA["tool_labels"][key])
        pieces.append(f'<text x="{left - 10}" y="{y + 13}" text-anchor="end">{tool}</text>')
        if value is None:
            label = html.escape(result["status"])
            pieces.append(f'<text class="chart-muted" x="{left + 8}" y="{y + 14}">{label}</text>')
            continue
        scaled_value = value if is_time else value / (1024 * 1024)
        maximum_scaled = maximum if is_time else maximum / (1024 * 1024)
        scaled_width = scaled_value / maximum_scaled * bar_width
        css_tool = "lcovmerge" if key == "lcovmerge" else ("lcov" if key == "lcov" else "lcov-result-merger")
        pieces.append(f'<rect class="bar {css_tool}" x="{left}" y="{y}" width="{scaled_width:.3f}" height="21" rx="3" />')
        pieces.append(f'<circle class="bar-marker {css_tool}" cx="{left + scaled_width:.3f}" cy="{y + 10.5}" r="3" />')
        label = seconds_display(value) if is_time else bytes_display(result["rss_bytes"])
        pieces.append(f'<text x="{width - 6}" y="{y + 15}" text-anchor="end">{html.escape(label)}</text>')
    pieces.append("</svg>")
    return "".join(pieces)


def chart_text_alternative(metric_name):
    source = dataset("M")
    phrases = []
    for key in ("lcovmerge", "lcov", "lcov-result-merger"):
        result = source["results"][key]
        value = result["time_s"] if metric_name == "time" else result["rss_bytes"]
        if value is None:
            display = result["status"]
        elif metric_name == "time":
            display = seconds_display(value)
        else:
            display = bytes_display(result["rss_bytes"])
        phrases.append(f'{DATA["tool_labels"][key]} {display}')
    label = "elapsed time" if metric_name == "time" else "peak RSS"
    return f'{source["name"]} {label}: ' + "; ".join(phrases) + "."


def medium_results_rows():
    source = dataset("M")
    rows = []
    for key in ("lcovmerge", "lcov", "lcov-result-merger"):
        result = source["results"][key]
        elapsed = seconds_display(result["time_s"]) if result["time_s"] is not None else result["status"]
        rss = bytes_display(result["rss_bytes"]) if result["rss_bytes"] is not None else result.get("rss_display", "—")
        throughput = (f'{result["throughput_mb_s"]:,.1f} MB/s'
                      if result["throughput_mb_s"] is not None else "—")
        primary = " class=\"tool-primary\"" if key == "lcovmerge" else ""
        rows.append(
            f'<tr><th scope="row"{primary}>{html.escape(DATA["tool_labels"][key])}</th>'
            f'<td class="table-numeric">{html.escape(elapsed)}</td>'
            f'<td class="table-numeric">{html.escape(rss)}</td>'
            f'<td class="table-numeric">{html.escape(throughput)}</td>'
            f'<td>{html.escape(result["status"])}</td></tr>'
        )
    return "\n".join(rows)


def medium_caption():
    source = dataset("M")
    return f'{source["name"]}: {source["input_bytes"]:,} generated input bytes across {html.escape(source["shards"])}; RSS is peak resident memory.'


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
            <caption>Aggregated measurements on {html.escape(REAL_DATA["host"]["os"])} {html.escape(REAL_DATA["host"]["architecture"])}; elapsed time / peak RSS.</caption>
            <thead><tr><th scope="col">Project</th><th scope="col">Input</th><th scope="col">lcovmerge</th><th scope="col">LCOV 2.6</th><th scope="col">Semantic comparison / genhtml</th></tr></thead>
            <tbody>{real_project_rows()}</tbody>
          </table>
        </div>
        <p class="small-note">All individual project checks passed normalized record comparisons and genhtml. The separate {html.escape(sort_inputs)}-input external-sort, reverse-order and job-determinism checks passed. Lua used portable test mode; capture warnings and method details are documented in <a href="https://github.com/megasoft1978/lcovmerge/blob/main/docs/validation/real-projects.md">the real-project validation record ↗</a>. Measurements cover one {html.escape(REAL_DATA["host"]["os"])} {html.escape(REAL_DATA["host"]["architecture"])} host.</p>'''


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
    if start not in text or end not in text:
        raise SystemExit(f"generated section markers missing in {path.relative_to(ROOT)}")
    before, rest = text.split(start, 1)
    _, after = rest.split(end, 1)
    path.write_text(before + start + "\n" + content + "\n" + end + after)


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
