#!/usr/bin/env python3
"""Check that generated site benchmark sections match the canonical renderer."""

import importlib.util
import json
import sys
from pathlib import Path


def load_renderer(root):
    script = root / "tools" / "render_benchmarks.py"
    spec = importlib.util.spec_from_file_location("render_benchmarks", script)
    if spec is None or spec.loader is None:
        raise SystemExit("cannot load tools/render_benchmarks.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def section_text(document, start, end):
    if document.count(start) != 1 or document.count(end) != 1:
        raise AssertionError(f"expected one pair of markers: {start}")
    _, content = document.split(start, 1)
    content, _ = content.split(end, 1)
    return content.strip()


def main():
    root = Path(sys.argv[1]).resolve()
    renderer = load_renderer(root)
    page_path = root / "docs" / "site" / "benchmarks.html"
    page = page_path.read_text(encoding="utf-8")
    generated_sections = (
        (renderer.SITE_CHARTS_START, renderer.SITE_CHARTS_END, renderer.chart()),
        (renderer.SITE_RESULTS_START, renderer.SITE_RESULTS_END, renderer.site_results_rows()),
        (renderer.SITE_MOBILE_RESULTS_START, renderer.SITE_MOBILE_RESULTS_END,
         renderer.site_results_mobile()),
        (renderer.SITE_METHOD_START, renderer.SITE_METHOD_END,
         renderer.site_benchmark_method()),
        (renderer.SITE_SCOPE_START, renderer.SITE_SCOPE_END,
         renderer.site_benchmark_scope()),
        (renderer.SITE_HOST_RESULTS_START, renderer.SITE_HOST_RESULTS_END,
         renderer.site_host_results()),
    )
    for start, end, expected in generated_sections:
        actual = section_text(page, start, end)
        if actual != expected.strip():
            raise AssertionError(f"stale generated site section: {start}")

    benchmark_data = json.loads((root / "data" / "benchmarks.json").read_text(encoding="utf-8"))
    host_data = benchmark_data.get("host_results", [])
    expected_mobile_results = sum(len(dataset["results"]) for dataset in benchmark_data["datasets"])
    expected_mobile_results += sum(
        len(dataset.get("results", {}))
        for host in host_data
        for dataset in host.get("datasets", [])
    )
    if page.count('class="mobile-tool-result"') != expected_mobile_results:
        raise AssertionError("mobile renderer omitted one or more tool rows")

    for dataset in benchmark_data["datasets"]:
        for result in dataset["results"].values():
            status = result["status"]
            if f"Status: {status}" not in page:
                raise AssertionError(f"mobile renderer omitted status {status}")
    for key, label in renderer.display_tool_labels().items():
        if key != "grcov" and label not in page:
            raise AssertionError(f"mobile renderer omitted comparator/build label {label}")
    if renderer.primary_host_text() not in page:
        raise AssertionError("mobile renderer omitted the exact primary host label")
    for host in host_data:
        if host["label"] not in page:
            raise AssertionError(f"mobile renderer omitted host label {host['label']}")
        host_details = host.get("host", {})
        for key in ("os", "kernel", "cpu_model"):
            if host_details.get(key) and host_details[key] not in page:
                raise AssertionError(f"mobile renderer omitted host detail {host_details[key]}")
        for tool in host["tool_labels"].values():
            if tool not in page:
                raise AssertionError(f"mobile renderer omitted exact build label {tool}")
        for dataset in host.get("datasets", []):
            for result in dataset.get("results", {}).values():
                status = result["status"]
                if f"Status: {status}" not in page:
                    raise AssertionError(f"mobile renderer omitted host status {status}")

    for page_name in ("docs.html", "benchmarks.html"):
        page_text = (root / "docs" / "site" / page_name).read_text(encoding="utf-8")
        if '<link rel="stylesheet" href="pages.css">' not in page_text:
            raise AssertionError(f"{page_name} does not load pages.css")
    print(f"site_benchmark_rendering=PASS rows={expected_mobile_results}")


if __name__ == "__main__":
    main()
