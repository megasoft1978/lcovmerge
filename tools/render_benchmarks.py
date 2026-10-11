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
VERSION_MATCH = re.search(
    r'^#define LCOVMERGE_VERSION "([^"]+)"$',
    (ROOT / "include/version.h").read_text(),
    re.MULTILINE,
)
if VERSION_MATCH is None:
    raise SystemExit("LCOVMERGE_VERSION is missing from include/version.h")
START = "<!-- BENCHMARKS:START -->"
END = "<!-- BENCHMARKS:END -->"
SITE_CHARTS_START = "<!-- SITE-BENCHMARK-CHARTS:START -->"
SITE_CHARTS_END = "<!-- SITE-BENCHMARK-CHARTS:END -->"
SITE_RESULTS_START = "<!-- SITE-BENCHMARK-RESULTS:START -->"
SITE_RESULTS_END = "<!-- SITE-BENCHMARK-RESULTS:END -->"
SITE_MOBILE_RESULTS_START = "<!-- SITE-BENCHMARK-MOBILE-RESULTS:START -->"
SITE_MOBILE_RESULTS_END = "<!-- SITE-BENCHMARK-MOBILE-RESULTS:END -->"
SITE_HOST_RESULTS_START = "<!-- SITE-HOST-RESULTS:START -->"
SITE_HOST_RESULTS_END = "<!-- SITE-HOST-RESULTS:END -->"
SITE_METHOD_START = "<!-- SITE-BENCHMARK-METHOD:START -->"
SITE_METHOD_END = "<!-- SITE-BENCHMARK-METHOD:END -->"
SITE_SCOPE_START = "<!-- SITE-BENCHMARK-SCOPE:START -->"
SITE_SCOPE_END = "<!-- SITE-BENCHMARK-SCOPE:END -->"
SITE_OPTIMIZATION_START = "<!-- SITE-BENCHMARK-OPTIMIZATION:START -->"
SITE_OPTIMIZATION_END = "<!-- SITE-BENCHMARK-OPTIMIZATION:END -->"
README_BENCHMARK_START = "<!-- README-BENCHMARK:START -->"
README_BENCHMARK_END = "<!-- README-BENCHMARK:END -->"
README_OPTIMIZATION_START = "<!-- README-OPTIMIZATION:START -->"
README_OPTIMIZATION_END = "<!-- README-OPTIMIZATION:END -->"
HOME_BENCHMARK_START = "<!-- HOME-BENCHMARK:START -->"
HOME_BENCHMARK_END = "<!-- HOME-BENCHMARK:END -->"
WINDOWS_VERIFICATION_SENTENCE = (
    "Windows is verified in GitHub-hosted windows-latest CI: both the MSYS2 UCRT64 GCC build "
    "and shipped Zig binary pass the full suite and smoke tests, and separate generated-input "
    "benchmark runs are recorded; physical desktops, other Windows versions and real-user reports "
    "remain unverified, Windows CI runs no LCOV comparator, and large-dataset timings are "
    "disk-bound CI numbers."
)


def metric(result):
    seconds = "—" if result["time_s"] is None else seconds_display(result["time_s"])
    rss = memory(result)
    rate = "n/a" if result["throughput_mb_s"] is None else f'{result["throughput_mb_s"]:,.1f} MB/s'
    return f'{seconds} / {rss} / {rate} / {result["status"]}'


def memory(result):
    if result.get("rss_display"):
        return plain_copy(result["rss_display"])
    if result.get("rss_bytes") is not None:
        return bytes_display(result["rss_bytes"], binary=True)
    if result.get("rss_mib") is None:
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
    text = re.sub(
        r'(?<![/\w])([0-9][0-9,]*(?:\.[0-9]+)?) s\b',
        lambda match: seconds_display(float(match.group(1).replace(",", ""))),
        text,
    )
    if text.startswith("Dataset M sorted regular shards use the direct single-threaded path;"):
        text = ("Dataset M's sorted regular inputs use one merge worker; --jobs results show run variation, "
                "not the effect of adding workers.")
    return text


def context():
    host = DATA["host"]
    method = DATA["method"]
    repeats = ", ".join(f'{name}: {count}' for name, count in method["repeat_counts"].items())
    timeout_minutes = method["timeout_seconds"] / 60
    result = (
        f'Host: {host["os"]}, {host["cpu"]}, {host["cores"]} cores, '
        f'{host["memory_gib"]} GiB RAM; {host["cache"]}. '
        f'Measured: {DATA["measured_on"]}. lcovmerge runs by dataset: {repeats}. '
        f'Comparison runs per tool: {DATA["method"]["comparison_runs_per_tool"]}; '
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


def full_table():
    labels = display_tool_labels()
    rows = [
        f'| Dataset (input size) | {labels["lcovmerge"]} | {labels["lcov"]} | '
        f'{labels["lcov-result-merger"]} |',
        "| --- | ---: | ---: | ---: |",
    ]
    for ds in DATA["datasets"]:
        r = ds["results"]
        size = input_size_display(ds["input_bytes"]) if ds.get("input_bytes") is not None else "unavailable"
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
            f'| {label} | {seconds_display(result["time_s"])} | {memory(result)} | '
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


def dataset(name):
    return next(item for item in DATA["datasets"] if item["name"] == name)


def completed(result):
    return (
        isinstance(result, dict)
        and result.get("status") == "OK"
        and isinstance(result.get("time_s"), (int, float))
        and result["time_s"] > 0
        and result.get("exit_code", 0) == 0
    )


def host_identity(source, key, data):
    result = source.get("results", {}).get(key, {})
    host = result.get("host") or source.get("host") or data.get("host")
    if host is None:
        return None
    if isinstance(host, dict):
        return json.dumps(host, sort_keys=True, separators=(",", ":"))
    return str(host)


def featured_dataset(data=None):
    """Choose the largest primary dataset with successful same-host lcov results."""
    data = DATA if data is None else data
    candidates = []
    for source in data.get("datasets", []):
        results = source.get("results", {})
        merge = results.get("lcovmerge", {})
        lcov = results.get("lcov", {})
        input_bytes = source.get("input_bytes")
        if not isinstance(input_bytes, int) or input_bytes <= 0:
            continue
        if not completed(merge) or not completed(lcov):
            continue
        merge_host = host_identity(source, "lcovmerge", data)
        lcov_host = host_identity(source, "lcov", data)
        if merge_host is None or merge_host != lcov_host:
            continue
        candidates.append(source)
    if not candidates:
        raise SystemExit("no completed same-host lcovmerge/lcov benchmark pair is available")
    return max(candidates, key=lambda source: source["input_bytes"])


def featured_host(source):
    host = source.get("host") or DATA.get("host", {})
    if isinstance(host, str):
        return host
    details = [host.get("os"), host.get("cpu")]
    return " · ".join(str(value) for value in details if value)


def is_earlier_memory(key, result):
    source = result.get("rss_source") or result.get("memory_source")
    if source and re.search(r"earlier|previous|prior", str(source), re.IGNORECASE):
        return True
    if key == "lcov" and "retain the previous canonical" in DATA.get("method", {}).get("rss_source", ""):
        return result.get("rss_bytes") is not None
    if key == "lcov-result-merger" and result.get("run_count") is None:
        environment = DATA.get("environment", {}).get("lcov-result-merger", "")
        return result.get("rss_bytes") is not None and "prior results retained" in environment
    return False


def is_earlier_timing(key, result):
    source = result.get("time_source") or result.get("measurement_source")
    if source and re.search(r"earlier|previous|prior", str(source), re.IGNORECASE):
        return True
    if key == "lcov-result-merger" and result.get("run_count") is None:
        environment = DATA.get("environment", {}).get("lcov-result-merger", "")
        return "prior results retained" in environment
    return False


def measurement_note(source, key, result):
    if not result or result.get("status") in {"NOT MEASURED", None}:
        return "Not measured"
    count = result.get("run_count")
    if count is None and key == "lcovmerge":
        count = DATA.get("method", {}).get("repeat_counts", {}).get(source["name"])
    if count is None and key in {"lcov", "lcov-result-merger"} and not is_earlier_timing(key, result):
        count = DATA.get("method", {}).get("comparison_runs_per_tool")
    if (result.get("run_count") == 0 or
            result.get("status", "").lower() in {"not measured", "skipped"}):
        return "Not measured"
    if count is None:
        if is_earlier_timing(key, result):
            return "Earlier recorded measurement; run count not reported"
        return "Run count not reported"
    suffix = f"{count} measured run" + ("s" if count != 1 else "")
    if key == "lcovmerge" and count > 1:
        suffix += "; median time"
    if key == "lcov" and is_earlier_memory(key, result):
        suffix += "; peak memory from an earlier measurement"
    return suffix


def featured_memory(key, result):
    value = (bytes_display(result["rss_bytes"], binary=True)
             if result.get("rss_bytes") is not None
             else memory(result))
    if is_earlier_memory(key, result) and value != "—":
        value += " · earlier measurement"
    return value


def result_elapsed(result):
    value = result.get("time_s")
    status = result.get("status", "Not reported")
    if status == "OK" and isinstance(value, (int, float)):
        return seconds_display(value)
    if isinstance(value, (int, float)):
        return f'{status} · stopped after {seconds_display(value)}'
    return status


def comparison_ratios(source, key):
    """Return only successful, same-host time and resident-memory ratios."""
    merge = source.get("results", {}).get("lcovmerge", {})
    other = source.get("results", {}).get(key, {})
    if not completed(merge) or not completed(other):
        return None
    if host_identity(source, "lcovmerge", DATA) != host_identity(source, key, DATA):
        return None
    time_ratio = other["time_s"] / merge["time_s"]
    memory_ratio = None
    if merge.get("rss_bytes") and other.get("rss_bytes"):
        memory_ratio = other["rss_bytes"] / merge["rss_bytes"]
    return time_ratio, memory_ratio


def semantic_comparison(source):
    """Find a semantic comparison result without mistaking ratio metadata for one."""
    for field in ("semantic_comparison", "comparison"):
        value = source.get(field)
        if not isinstance(value, dict):
            continue
        for key in ("lcovmerge_vs_lcov", "semantic_comparison"):
            nested = value.get(key)
            if isinstance(nested, dict) and nested.get("status") is not None:
                return nested
        if value.get("status") is not None:
            return value
    return None


def semantic_difference_count(comparison):
    """Read an explicit difference count or count the comparator's reported deltas."""
    for key in ("difference_count", "differences_count", "diff_count", "num_differences"):
        count = comparison.get(key)
        if isinstance(count, int) and count >= 0:
            return count
    differences = comparison.get("differences")
    if isinstance(differences, int) and differences >= 0:
        return differences
    if isinstance(differences, list):
        return len(differences)

    details = comparison.get("details")
    if not isinstance(details, str):
        return None
    total_match = re.search(r"\b(\d[\d,]*)\s+differences\b", details, re.IGNORECASE)
    if total_match:
        return int(total_match.group(1).replace(",", ""))

    # compare_lcov.py stops after 12 reported detail fragments. Avoid presenting
    # the partial details as a total if that cap may have been reached.
    if len(details.split(" | ")) >= 12:
        return None

    # bench/compare_lcov.py reports per-SF key deltas and whole missing SF records.
    # For an untruncated result, these values give its exact difference count.
    delta_rows = re.findall(
        r"left-only=(\d+)\s+right-only=(\d+)\s+value-differences=(\d+)",
        details,
    )
    missing_sources = re.findall(r"missing from (?:left|right): SF:", details)
    if delta_rows or missing_sources:
        return sum(sum(int(value) for value in row) for row in delta_rows) + len(missing_sources)
    return None


def semantic_comparison_copy(source):
    comparison = semantic_comparison(source)
    if comparison is None:
        return "outputs not compared"
    status = str(comparison.get("status", "")).upper()
    if status == "PASS":
        return "normalized outputs matched"
    if status == "FAIL":
        count = semantic_difference_count(comparison)
        if count is not None and count > 0:
            return f"{count:,} differences"
        return "normalized outputs differ; difference count not reported"
    if status == "TIMEOUT":
        return "semantic comparison timed out"
    return "outputs not compared"


def ratio_copy(source, key):
    ratios = comparison_ratios(source, key)
    if ratios is None:
        return None
    time_ratio, memory_ratio = ratios
    label = display_tool_labels().get(key, key)
    if time_ratio >= 1:
        time_text = f'{time_ratio:,.1f}× faster than {label}'
    else:
        time_text = f'{1 / time_ratio:,.1f}× slower than {label}'
    memory_text = None
    if memory_ratio is not None:
        if memory_ratio >= 1:
            memory_text = f'{memory_ratio:,.1f}× less recorded peak resident memory than {label}'
        else:
            memory_text = f'{1 / memory_ratio:,.1f}× more recorded peak resident memory than {label}'
    parts = [time_text]
    if memory_text:
        parts.append(memory_text)
    caveat = ""
    result = source.get("results", {}).get(key, {})
    if is_earlier_timing(key, result):
        caveat = " (its values are from an earlier recorded measurement; run count not reported)"
    elif is_earlier_memory(key, result):
        caveat = " (its peak memory is from an earlier measurement)"
    return "; ".join(parts) + caveat + "."


def featured_metadata(source):
    labels = display_tool_labels()
    method = DATA.get("method", {})
    merge = source["results"]["lcovmerge"]
    lcov = source["results"]["lcov"]
    merger = source["results"].get("lcov-result-merger", {})
    merge_runs = merge.get("run_count") or method.get("repeat_counts", {}).get(source["name"])
    lcov_runs = lcov.get("run_count") or method.get("comparison_runs_per_tool")
    merger_runs = merger.get("run_count")
    merge_note = f'{merge_runs}-run median' if merge_runs else "run count unreported"
    lcov_note = f'{lcov_runs} timed run' if lcov_runs else "run count unreported"
    if is_earlier_memory("lcov", lcov):
        lcov_note += "; peak memory from earlier measurement"
    if merger.get("status") in {"NOT MEASURED", None}:
        merger_note = "not measured"
    elif merger_runs:
        merger_note = f"{merger_runs} runs"
    elif is_earlier_timing("lcov-result-merger", merger):
        merger_note = "earlier measurement; run count unreported"
    else:
        merger_note = f'{method.get("comparison_runs_per_tool", "run count unreported")} measured run'
    return (
        f'Generated Dataset {source["name"]} · {input_size_display(source["input_bytes"])} input · '
        f'{source.get("shards", "shard count not reported")} · {featured_host(source)} · '
        f'{DATA.get("measured_on", "date not reported")}. '
        f'Builds: {labels["lcovmerge"]} / {labels["lcov"]} / {labels["lcov-result-merger"]}. '
        f'Runs: lcovmerge {merge_note}; lcov {lcov_note}; lcov-result-merger {merger_note}.'
    )


def featured_readme():
    source = featured_dataset()
    labels = display_tool_labels()
    keys = ("lcovmerge", "lcov", "lcov-result-merger")
    rows = [
        f'**{featured_metadata(source)}**',
        "",
        "| Tool | Time | Peak resident memory | Runs / status |",
        "| --- | ---: | ---: | --- |",
    ]
    for key in keys:
        result = source.get("results", {}).get(key, {})
        rows.append(
            f'| {labels[key]} | {result_elapsed(result)} | {featured_memory(key, result)} | '
            f'{measurement_note(source, key, result)} · {result.get("status", "Not reported")} |'
        )
    for key in ("lcov", "lcov-result-merger"):
        copy = ratio_copy(source, key)
        if copy:
            rows.extend(["", f"**{copy}**"])
    rows.extend([
        "",
        "Input sizes and durations are rounded for readability; exact measurements are in the "
        "[published benchmark record](data/benchmarks.json).",
    ])
    return "\n".join(rows)


def optimization_readme():
    trials = DATA.get("optimization_trials", [])
    if not trials:
        return ""
    trial = trials[0]
    by_name = {result["dataset"]: result for result in trial["results"]}
    s_result = by_name["S"]
    m_result = by_name["M"]
    s_gain = (1 - s_result["optimized"]["time_median_s"] /
              s_result["baseline"]["time_median_s"]) * 100
    m_gain = (1 - m_result["optimized"]["time_median_s"] /
              m_result["baseline"]["time_median_s"]) * 100
    native_gain = (1 - m_result["optimized"]["time_median_s"] /
                   m_result["native_o2"]["time_median_s"]) * 100
    rows = [
        "### Experimental profile-guided build",
        "",
        f'A separate {trial["host"]["cpu"]} experiment trained an Apple Clang profile on Dataset M and reused it on S and M. '
        f'Against the standard portable `-O2` build, the tuned build was {s_gain:.1f}% faster on S and '
        f'{m_gain:.1f}% faster on M, with peak resident memory increasing by '
        f'{bytes_display(s_result["optimized"]["rss_median_bytes"] - s_result["baseline"]["rss_median_bytes"], binary=True).replace(".00 ", " ")} '
        'on each dataset. The M-trained profile also beat the same native `-O2` build by '
        f'{native_gain:.1f}% on M. Outputs matched byte for byte.',
        "",
        "| Dataset | Standard `-O2` | Profile-guided build | Time change | Peak memory change |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for result in trial["results"]:
        base = result["baseline"]
        optimized = result["optimized"]
        speedup = (1 - optimized["time_median_s"] / base["time_median_s"]) * 100
        memory_delta = optimized["rss_median_bytes"] - base["rss_median_bytes"]
        memory_delta_display = bytes_display(memory_delta, binary=True).replace(".00 ", " ")
        rows.append(
            f'| {result["dataset"]} ({input_size_display(result["input_bytes"])}) '
            f'| {seconds_display(base["time_median_s"])} / {bytes_display(base["rss_median_bytes"], binary=True)} '
            f'| {seconds_display(optimized["time_median_s"])} / {bytes_display(optimized["rss_median_bytes"], binary=True)} '
            f'| {speedup:.1f}% faster | +{memory_delta_display} |'
        )
    rows.extend([
        "",
        "This is a compiler-tuning experiment on one host, separate from the standard tool comparison above; it is not a release-build or cross-platform claim. "
        "The [benchmark report](docs/BENCHMARKS.md#experimental-profile-guided-build) records compiler flags, run counts, and hashes.",
    ])
    return "\n".join(rows)


def site_optimization_trials():
    trial = (DATA.get("optimization_trials") or [None])[0]
    if not trial:
        return ""
    host = trial["host"]
    rows = []
    for result in trial["results"]:
        baseline = result["baseline"]
        optimized = result["optimized"]
        speedup = (1 - optimized["time_median_s"] / baseline["time_median_s"]) * 100
        memory_delta = optimized["rss_median_bytes"] - baseline["rss_median_bytes"]
        memory_delta_display = bytes_display(memory_delta, binary=True).replace(".00 ", " ")
        extra = ""
        if result.get("native_o2"):
            native = result["native_o2"]
            native_gain = (1 - optimized["time_median_s"] / native["time_median_s"]) * 100
            extra = (
                f'<p class="small-note">Native <code>-O2</code> without PGO: '
                f'{html.escape(seconds_display(native["time_median_s"]))}, '
                f'{html.escape(bytes_display(native["rss_median_bytes"], binary=True))}; PGO was '
                f'{native_gain:.1f}% faster.</p>'
            )
        rows.append(
            f'<tr><th scope="row">Dataset {html.escape(result["dataset"])}</th>'
            f'<td>{html.escape(input_size_display(result["input_bytes"]))}</td>'
            f'<td>{html.escape(seconds_display(baseline["time_median_s"]))}</td>'
            f'<td>{html.escape(seconds_display(optimized["time_median_s"]))}</td>'
            f'<td>{speedup:.1f}% faster{extra}</td>'
            f'<td>{html.escape(bytes_display(baseline["rss_median_bytes"], binary=True))} → '
            f'{html.escape(bytes_display(optimized["rss_median_bytes"], binary=True))} '
            f'(+{html.escape(memory_delta_display)})</td></tr>'
        )
    return (
        '<section class="optimization-note" id="profile-guided-build" aria-labelledby="optimization-title">'
        '<h2 id="optimization-title">Experimental profile-guided build</h2>'
        f'<p>Apple Clang profile guided optimization on {html.escape(host["cpu"])} · '
        f'{html.escape(host["os"])} · measured {html.escape(trial["measured_on"])}. '
        'The profile was trained once on generated Dataset M, then measured on S and M with one warmup '
        'and three runs per build. This lcovmerge-only experiment is separate from the comparator results above.</p>'
        '<div class="table-wrap desktop-benchmark-table" role="region" tabindex="0" '
        'aria-label="Profile-guided build results, scroll horizontally if needed">'
        '<table class="benchmark-table"><caption>Median elapsed time and peak resident memory</caption>'
        '<thead><tr><th scope="col">Dataset</th><th scope="col">Input</th>'
        '<th scope="col">Portable <code>-O2</code></th><th scope="col">Profile guided</th>'
        '<th scope="col">Time</th><th scope="col">Peak memory</th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table></div>'
        f'<p class="small-note">Compiler: {html.escape(trial["compiler"])}. '
        f'Optimized flags: <code>{html.escape(" ".join(trial["build_flags"]))}</code>. '
        'Output SHA-256 matched for each pair. The profile guided binary is experimental, host specific, '
        'and not used in the standard comparison table; these results do not establish a general speedup. '
        '<a href="https://github.com/megasoft1978/lcovmerge/blob/main/docs/BENCHMARKS.md#experimental-profile-guided-build">'
        'Read the detailed method and exact output hashes.</a></p>'
        '</section>'
    )


def featured_site():
    source = featured_dataset()
    labels = display_tool_labels()
    results = source.get("results", {})
    keys = ("lcovmerge", "lcov", "lcov-result-merger")
    completed_times = [
        results.get(key, {}).get("time_s")
        for key in keys
        if completed(results.get(key, {}))
    ]
    maximum = max(completed_times)
    lcov_ratios = comparison_ratios(source, "lcov")
    lcov_mem_ratio = lcov_ratios[1] if lcov_ratios else None
    if lcov_ratios:
        time_ratio = lcov_ratios[0]
        headline_html = (
            '<h2 id="home-title" class="home-stats">'
            f'<span class="home-stat"><strong>{time_ratio:,.1f}×</strong>'
            f'<small>faster than {html.escape(labels["lcov"])}</small></span>'
        )
        if lcov_mem_ratio is not None:
            headline_html += (
                f'<span class="home-stat"><strong>{lcov_mem_ratio:,.1f}×</strong>'
                '<small>less peak memory</small></span>'
            )
        headline_html += '</h2>'
    else:
        headline_html = '<h2 id="home-title">Benchmark ratio unavailable.</h2>'

    chart_items = ['<p class="bar-caption">Time · peak resident memory</p>']
    for key in keys:
        result = results.get(key, {})
        status = result.get("status", "Not reported")
        if completed(result):
            time_text = seconds_display(result["time_s"])
            width = result["time_s"] / maximum * 100
            bar = f'<span class="bar-fill" style="width:{width:.3f}%"></span>'
        else:
            time_text = result_elapsed(result)
            bar = ""
        if result.get("status") == "OK":
            status_note = ""
            if is_earlier_timing(key, result):
                status_note = "Earlier measurement; runs unreported"
        else:
            status_note = result.get("status", "Not reported")
            if status_note in {"TIMEOUT", "ERROR", "ERROR_1"}:
                status_note += " · excluded from scale and ratios"
        value = result.get("rss_bytes")
        memory_value = (
            bytes_display(value, binary=True)
            if value is not None else memory(result)
        )
        if key == "lcov" and is_earlier_memory(key, result) and memory_value != "—":
            memory_value += " (earlier measurement)"
        chart_items.append(
            f'<li class="home-bar {key}">'
            f'<div class="home-bar-label"><span>{html.escape(labels[key])}</span>'
            f'<strong>{html.escape(time_text)} · {html.escape(memory_value)}</strong></div>'
            f'<div class="bar-track" aria-hidden="true">{bar}</div>'
            f'<span class="home-bar-status">{html.escape(status_note)}</span>'
            '</li>'
        )
    mid = maximum / 2
    axis = (
        '<div class="bar-axis" role="img" '
        f'aria-label="Elapsed-time scale from 0 to {seconds_display(maximum)}">'
        f'<span>{seconds_display(0)}</span><span>{seconds_display(mid)}</span>'
        f'<span>{seconds_display(maximum)}</span></div>'
    )
    return "\n".join([
        headline_html,
        f'<p class="featured-meta">{input_size_display(source["input_bytes"])} input · '
        f'{html.escape(str(source.get("shards", "shard count not reported")))}</p>',
        f'<ul class="home-bars" aria-label="Elapsed time on a zero-based scale; peak resident memory values are shown">{"".join(chart_items)}</ul>',
        axis,
        f'<p class="generated-note">Generated; <span class="generated-prefix">{html.escape(semantic_comparison_copy(source))}</span>. '
        f'Release v{html.escape(VERSION_MATCH.group(1))}.</p>',
        '<a class="text-link" href="benchmarks.html">All results &amp; methodology ↗</a>',
    ])


def seconds_display(value):
    """Render durations at a useful scale while preserving exact source values in JSON."""
    value = float(value)
    if value == 0:
        return "0 s"
    if value < 1:
        milliseconds = value * 1000
        if round(milliseconds) >= 1000:
            return "1.00 s"
        return f'{milliseconds:,.0f} ms' if milliseconds >= 100 else f'{milliseconds:,.1f} ms'
    rounded = round(value, 2) if value < 60 else round(value, 1)
    if rounded < 60:
        return f'{rounded:,.2f} s'
    minutes, seconds = divmod(rounded, 60)
    seconds_text = f'{seconds:.0f}' if seconds.is_integer() else f'{seconds:.1f}'
    if minutes < 60:
        suffix = f' {seconds_text}s' if seconds else ""
        return f'{int(minutes):,}m{suffix}'
    hours, minutes = divmod(int(minutes), 60)
    suffix = f' {seconds_text}s' if seconds else ""
    minute_part = f' {int(minutes)}m' if minutes else ""
    return f'{int(hours):,}h{minute_part}{suffix}'


def bytes_display(value, binary=False):
    """Format byte counts with compact SI input sizes or IEC memory sizes."""
    base = 1024 if binary else 1000
    units = ("B", "KiB", "MiB", "GiB", "TiB") if binary else ("B", "kB", "MB", "GB", "TB")
    number = float(value)
    unit = 0
    while abs(number) >= base and unit < len(units) - 1:
        number /= base
        unit += 1
    if unit == 0:
        return f'{int(number):,} B'
    precision = (2 if number < 100 else 1) if binary else (0 if number >= 100 else (1 if number >= 10 else 2))
    return f'{number:,.{precision}f} {units[unit]}'


def input_size_display(value):
    return bytes_display(value)


def chart(dataset_name="M"):
    """Render the elapsed-time chart with rounded display values."""
    source = dataset(dataset_name)
    labels = display_tool_labels()
    keys = ("lcovmerge", "lcov", "lcov-result-merger")
    values = [(key, source["results"][key], source["results"][key]["time_s"])
              for key in keys]
    numeric = [value for _, _, value in values if value is not None]
    maximum = max(numeric, default=1)
    if maximum <= 0:
        maximum = 1
    input_size = (input_size_display(source["input_bytes"])
                  if source.get("input_bytes") is not None else "Input size not reported")
    pieces = [
        '<figcaption><h3 id="chart-m-title">'
        f'Dataset {html.escape(source["name"])} · elapsed time</h3>'
        f'<p>Generated input: {html.escape(input_size)} · {html.escape(source["shards"])}. '
        'Bars use a zero-based scale; the table shows rounded durations and statuses. '
        'The published record retains exact measurements.</p></figcaption>',
        f'<ul class="bar-chart" aria-label="{html.escape(source["name"])} elapsed-time comparison; '
        'rounded durations and statuses are in the table below">'
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
        ticks.append(f'<span>{seconds_display(tick)}</span>')
    pieces.append(
        f'<div class="bar-axis" role="img" aria-label="Elapsed-time scale from 0 to {seconds_display(maximum)}">'
        + "".join(ticks) + "</div>"
    )
    return "".join(pieces)


def run_count_for(dataset_name, key, result):
    if result.get("run_count") == 0 or result.get("status", "").lower() in {"not measured", "skipped"}:
        return "Not measured"
    if result.get("status") == "NOT MEASURED":
        return "Not measured"
    if key == "lcovmerge":
        if result.get("run_count") is not None:
            return f'{result["run_count"]} measured'
        count = DATA["method"]["repeat_counts"].get(dataset_name)
        return f'{count} measured' if count else "Not reported"
    if key == "lcov-result-merger":
        if result.get("run_count") is not None:
            return f'{result["run_count"]} measured'
        return "Earlier recorded measurement; run count not reported"
    if key == "lcov":
        count = DATA["method"].get("comparison_runs_per_tool")
        time_count = f'Time: {count} run(s)' if count else "Time: run count not reported"
        if result.get("run_count") is not None:
            time_count = f'Time: {result["run_count"]} run(s)'
        if result.get("rss_bytes") is not None:
            if "earlier" not in str(result.get("rss_source", "")).lower():
                return time_count
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
                input_size = input_size_display(input_bytes) if input_bytes is not None else "Input not reported"
                shard_text = source.get("shards", "Shard count not reported")
                cells.append(f'<th scope="rowgroup" rowspan="{len(keys)}">{html.escape(source["name"])}</th>')
                cells.append(f'<td rowspan="{len(keys)}">{html.escape(input_size)}; {html.escape(shard_text)}</td>')
            elapsed = (seconds_display(result["time_s"])
                       if result.get("time_s") is not None else result.get("status", "Not reported"))
            rss = (
                bytes_display(result["rss_bytes"], binary=True)
                if result.get("rss_bytes") is not None
                else plain_copy(result.get("rss_display", "—"))
            )
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


def mobile_result(tool, elapsed, status, run_note, peak_memory, memory_source,
                  throughput):
    return (
        '<section class="mobile-tool-result">'
        f'<h4>{html.escape(str(tool))}</h4>'
        '<p class="mobile-result-meta">'
        f'<span class="elapsed">Elapsed: {html.escape(str(elapsed))}</span>'
        f'<span class="status">Status: {html.escape(str(status))}</span>'
        '</p>'
        f'<p class="mobile-run-note">Run and memory record: {html.escape(str(run_note))}</p>'
        '<details><summary>Peak memory and throughput</summary>'
        '<dl class="mobile-result-details">'
        f'<dt>Peak resident memory</dt><dd>{html.escape(str(peak_memory))}</dd>'
        f'<dt>Memory record</dt><dd>{html.escape(str(memory_source))}</dd>'
        f'<dt>Throughput</dt><dd>{html.escape(str(throughput))}</dd>'
        '</dl></details></section>'
    )


def primary_host_text():
    host = DATA["host"]
    return (f'{host["os"]} · {host["cpu"]} · {host["cores"]} cores · '
            f'{host["memory_gib"]} GiB RAM')


def shard_text(value):
    return f'{value} shards' if isinstance(value, int) else str(value)


def primary_rss_source(key, result):
    if result.get("status", "").lower() in {"not measured", "skipped"}:
        return "Not measured"
    if result.get("rss_bytes") is None:
        return plain_copy(result.get("rss_display", "Peak resident memory not reported"))
    if key == "lcov" and "earlier" in str(result.get("rss_source", "")).lower():
        return "Earlier recorded peak resident memory; current elapsed-time run did not measure memory"
    if key == "lcov" and result.get("rss_source"):
        return plain_copy(result["rss_source"])
    if key == "lcov-result-merger" and result.get("run_count") is None:
        return "Earlier recorded measurement; run count not reported"
    hyperfine = DATA["environment"].get("hyperfine", "Hyperfine")
    return f'Hyperfine {hyperfine} memory_peak_resident; maximum over measured runs'


def site_results_mobile():
    cards = []
    labels = display_tool_labels()
    host_text = primary_host_text()
    for source in DATA["datasets"]:
        input_bytes = source.get("input_bytes")
        input_size = input_size_display(input_bytes) if input_bytes is not None else "Input not reported"
        shards = source.get("shards", "Shard count not reported")
        description = plain_copy(source.get("description", ""))
        kind = "Generated" if source["name"] != "REAL" else "Project-derived input not supplied"
        results = []
        for key in ("lcovmerge", "lcov", "lcov-result-merger"):
            result = source["results"][key]
            elapsed = (seconds_display(result["time_s"])
                       if result.get("time_s") is not None else result.get("status", "Not reported"))
            rss = (bytes_display(result["rss_bytes"], binary=True)
                   if result.get("rss_bytes") is not None
                   else plain_copy(result.get("rss_display", "—")))
            throughput = (f'{result["throughput_mb_s"]:,.1f} MB/s'
                          if result.get("throughput_mb_s") is not None else "—")
            run_note = run_count_for(source["name"], key, result)
            results.append(mobile_result(
                labels[key], elapsed, result.get("status", "Not reported"), run_note,
                rss, primary_rss_source(key, result), throughput,
            ))
        cards.append(
            '<article class="benchmark-dataset-card">'
            f'<h3>Dataset {html.escape(source["name"])} · {html.escape(description)}</h3>'
            f'<p>Input: {html.escape(input_size)} · {html.escape(str(shards))} · {kind}</p>'
            f'<p class="benchmark-card-host">Host: {html.escape(host_text)}</p>'
            f'<div class="mobile-result-list">{"".join(results)}</div>'
            '</article>'
        )
    return "\n".join(cards)


def site_benchmark_scope():
    labels = display_tool_labels()
    generated = ", ".join(
        item["name"] for item in DATA["datasets"] if item["name"] != "REAL"
    )
    windows_note = ""
    if any(item.get("platform") == "windows" for item in DATA.get("host_results", [])):
        windows_note = f" {WINDOWS_VERIFICATION_SENTENCE}"
    return (
        '<p>Generated inputs: '
        f'{html.escape(generated)}. Measured lcovmerge build: '
        f'<strong>{html.escape(labels["lcovmerge"])}</strong>. Comparators: '
        f'{html.escape(labels["lcov"])} and '
        f'{html.escape(labels["lcov-result-merger"])}. '
        f'The release is v{html.escape(VERSION_MATCH.group(1))}. Generated measurements do not establish a universal speedup '
        'or output compatibility. The REAL dataset was not measured because no project-derived '
        f'capture was supplied.{html.escape(windows_note)}</p>'
    )


def site_benchmark_method():
    repeats = ", ".join(f'{name}: {count}' for name, count in DATA["method"]["repeat_counts"].items())
    host = DATA["host"]
    method = DATA["method"]
    summary = (
        f'<p>Primary host: {html.escape(host["os"])} · {html.escape(host["cpu"])} · '
        f'{host["cores"]} cores · {host["memory_gib"]} GiB RAM; measured {html.escape(DATA["measured_on"])}. '
        f'lcovmerge run counts: {html.escape(repeats)}. Each row lists its measured tools, run counts, '
        'and memory source. '
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
        mobile_cards = []
        for dataset_record in entry.get("datasets", []):
            if not isinstance(dataset_record, dict):
                continue
            dataset_name = dataset_record.get("name", "Dataset not reported")
            input_bytes = dataset_record.get("input_bytes")
            input_size = input_size_display(input_bytes) if isinstance(input_bytes, int) else "Input not reported"
            shards = shard_text(dataset_record.get("shards", "Shard count not reported"))
            results = dataset_record.get("results") if isinstance(dataset_record.get("results"), dict) else {}
            mobile_results = []
            for key, result in results.items():
                if not isinstance(result, dict):
                    continue
                tool = entry.get("tool_labels", {}).get(key, key)
                elapsed_value = result.get("time_s")
                elapsed = (
                    seconds_display(elapsed_value)
                    if isinstance(elapsed_value, (int, float))
                    else result.get("status", "—")
                )
                rss_bytes = result.get("rss_bytes")
                rss = (
                    bytes_display(rss_bytes, binary=True)
                    if isinstance(rss_bytes, int)
                    else plain_copy(result.get("rss_display", "—"))
                )
                throughput_value = result.get("throughput_mb_s")
                throughput = f'{throughput_value:,.1f} MB/s' if isinstance(throughput_value, (int, float)) else "—"
                run_count = result.get("run_count")
                if run_count is None:
                    run_count = repeats.get(dataset_name)
                if result.get("status") in {"NOT MEASURED", "SKIPPED"}:
                    run_display = "Not measured"
                else:
                    run_display = f'{run_count} measured' if isinstance(run_count, int) else "Not reported"
                rss_source = method.get("rss_tool", "Peak memory source not reported")
                rss_summary = method.get("rss_summary", "")
                if result.get("rss_bytes") is None:
                    memory_source = plain_copy(result.get("rss_display", "Peak resident memory not reported"))
                else:
                    memory_source = " ".join(part for part in (rss_source, rss_summary) if part)
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
                mobile_results.append(mobile_result(
                    tool, elapsed, result.get("status", "Not reported"), run_display,
                    rss, memory_source, throughput,
                ))
            if not results:
                rows.append(
                    '<tr>'
                    f'<th scope="row">{html.escape(str(dataset_name))}</th>'
                    f'<td>{html.escape(input_size)}; {html.escape(str(shards))}</td>'
                    '<td>—</td><td>—</td><td>—</td><td>—</td><td>Not reported</td>'
                    f'<td>{html.escape(str(dataset_record.get("status", "Not measured")))}</td></tr>'
                )
                mobile_results.append(mobile_result(
                    "Tool not reported", "—", dataset_record.get("status", "Not measured"),
                    "Not reported", "—", "Not reported", "—",
                ))
            description = plain_copy(dataset_record.get("description", "Generated LCOV input."))
            kind = "Generated" if description else "Input type not reported"
            mobile_cards.append(
                '<article class="benchmark-dataset-card">'
                f'<h3>Dataset {html.escape(str(dataset_name))} · {html.escape(description)}</h3>'
                f'<p>Input: {html.escape(input_size)} · {html.escape(str(shards))} · {kind}</p>'
                f'<p class="benchmark-card-host">Host: {html.escape(host_details)}</p>'
                f'<div class="mobile-result-list">{"".join(mobile_results)}</div>'
                '</article>'
            )
        date = str(entry.get("measurement_date", "date not reported"))
        row_count = len(rows)
        blocks.append(
            f'<details class="host-result-block">'
            f'<summary><strong>{html.escape(str(label))}</strong>'
            f'<span>{row_count} rows · measured {html.escape(date)} · separate measurement</span></summary>'
            f'<p>Host: {html.escape(host_details)}. This result set stays separate from the primary comparison.</p>'
            f'<div class="table-wrap desktop-benchmark-table" role="region" tabindex="0" aria-label="Additional host results {entry_index}">'
            '<table class="benchmark-table">'
            '<caption>Dataset results for this host; each row includes its own tool, run count, and status.</caption>'
            '<thead><tr><th scope="col">Dataset</th><th scope="col">Input</th><th scope="col">Tool and build</th>'
            '<th scope="col">Elapsed</th><th scope="col">Peak resident memory</th><th scope="col">Throughput</th>'
            '<th scope="col">Runs</th><th scope="col">Status</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>'
            f'<div class="benchmark-mobile-list" aria-label="Additional host result cards {entry_index}">'
            f'{"".join(mobile_cards)}</div></details>'
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
                "rss_display": (bytes_display(rss, binary=True) if rss is not None
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
            "input_size": (input_size_display(source["input_bytes"])
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
    site_data = {
        **metadata,
        "datasets": datasets,
        "optimization_trials": DATA.get("optimization_trials", []),
    }
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


def render_site_benchmarks(path):
    replace_named_section(path, SITE_CHARTS_START, SITE_CHARTS_END, chart())
    replace_named_section(path, SITE_RESULTS_START, SITE_RESULTS_END, site_results_rows())
    replace_named_section(path, SITE_MOBILE_RESULTS_START, SITE_MOBILE_RESULTS_END,
                          site_results_mobile())
    replace_named_section(path, SITE_METHOD_START, SITE_METHOD_END, site_benchmark_method())
    replace_named_section(path, SITE_SCOPE_START, SITE_SCOPE_END, site_benchmark_scope())
    replace_named_section(path, SITE_OPTIMIZATION_START, SITE_OPTIMIZATION_END,
                          site_optimization_trials())
    replace_site_host_results(path, site_host_results())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--readme-site-only",
        action="store_true",
        help="update README and site data without changing docs/BENCHMARKS.md",
    )
    parser.add_argument(
        "--site-only",
        action="store_true",
        help="update generated sections in docs/site/benchmarks.html only",
    )
    args = parser.parse_args()

    benchmark_page = ROOT / "docs/site/benchmarks.html"
    if args.site_only:
        render_site_benchmarks(benchmark_page)
        return

    replace_named_section(
        ROOT / "README.md", README_BENCHMARK_START, README_BENCHMARK_END, featured_readme()
    )
    replace_named_section(
        ROOT / "README.md", README_OPTIMIZATION_START, README_OPTIMIZATION_END,
        optimization_readme(),
    )

    index = ROOT / "docs/site/index.html"
    replace_named_section(index, HOME_BENCHMARK_START, HOME_BENCHMARK_END, featured_site())

    render_site_benchmarks(benchmark_page)

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
