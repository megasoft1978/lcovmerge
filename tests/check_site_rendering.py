#!/usr/bin/env python3
"""Check that generated site benchmark sections match the canonical renderer."""

import html
import importlib.util
import json
import re
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


def check_public_compatibility_copy(text, source):
    plain_text = html.unescape(re.sub(r"<[^>]*>", " ", text)).replace("`", "")
    normalized = " ".join(plain_text.split()).casefold()
    normalized = re.sub(r"\s+([,;:.])", r"\1", normalized)
    required = (
        "2026-10-11",
        "strict lcov 2.6 lcov -a",
        "failed 3/3",
        "lcovmerge 1.0.2 completed 3/3",
        "12 joined-boundary recovery warnings",
        "original lcovmerge 1.0.1 run found the joined-boundary bug",
        "7 da",
        "18 fn",
        "18 fnda",
        "86 lcovmerge-only sf",
        "713 lcov-only da",
        "2 lcovmerge-only fn",
        "0 fnda differences",
        "9,877 brda key/value differences",
        "normalized sf keys",
        "keyed da, fn, fnda, and brda values/counts",
        "earlier investigation root-caused",
        "branch-only da synthesis",
        "empty sf",
        "block-id remapping",
        "duplicate legacy fn",
        "did not make strict lcov succeed",
        "did not compare byte-for-byte",
        "did not cover summary, testcase, extension",
        "did not newly isolate",
        "not proof of equivalence or a performance result",
    )
    missing = [fragment for fragment in required if fragment not in normalized]
    if missing:
        raise AssertionError(f"{source} compatibility summary is missing: {', '.join(missing)}")


def check_stale_windows_verification_copy(root):
    stale_patterns = (
        re.compile(r"windows runtime verification is pending", re.IGNORECASE),
        re.compile(r"windows runtime verification pending", re.IGNORECASE),
        re.compile(r"runtime verification on a windows host is pending", re.IGNORECASE),
        re.compile(r"windows runtime verification remains pending", re.IGNORECASE),
        re.compile(r"native windows runtime remains pending", re.IGNORECASE),
        re.compile(r"native windows ci is still required", re.IGNORECASE),
        re.compile(r"windows runtime documentation therefore remains conditional", re.IGNORECASE),
        re.compile(r"do not establish runtime verification of the published windows release", re.IGNORECASE),
        re.compile(r"has not been runtime-verified on a windows host", re.IGNORECASE),
        re.compile(r"hosted runtime verification as complete only after", re.IGNORECASE),
        re.compile(r"clarify pending windows host verification", re.IGNORECASE),
    )
    roots = (
        root / "README.md",
        root / "CHANGELOG.md",
        root / "action",
        root / "docs",
        root / "man",
        root / "packaging",
    )
    paths = []
    for base in roots:
        if base.is_file():
            paths.append(base)
        elif base.is_dir():
            paths.extend(
                path for path in base.rglob("*")
                if path.is_file() and path.suffix.lower() in {".md", ".html", ".txt", ".1"}
            )

    matches = []
    for path in paths:
        text = path.read_text(encoding="utf-8")
        for pattern in stale_patterns:
            for match in pattern.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                matches.append(f"{path.relative_to(root)}:{line}")
    if matches:
        raise AssertionError("stale Windows verification copy: " + ", ".join(matches))
    print(f"windows_verification_copy=PASS files={len(paths)}")


def main():
    root = Path(sys.argv[1]).resolve()
    check_stale_windows_verification_copy(root)
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

    compatibility_section = page.split(
        '<section class="public-compatibility-note" id="public-compatibility"', 1
    )
    if len(compatibility_section) != 2:
        raise AssertionError("missing site public compatibility notes")
    check_public_compatibility_copy(
        compatibility_section[1].split("</section>", 1)[0], "site"
    )
    check_public_compatibility_copy(
        (root / "docs" / "COMPATIBILITY.md").read_text(encoding="utf-8"),
        "docs/COMPATIBILITY.md",
    )
    benchmark_doc = (root / "docs" / "BENCHMARKS.md").read_text(encoding="utf-8")
    benchmark_section = benchmark_doc.split("## Public CI compatibility investigation", 1)
    if len(benchmark_section) != 2:
        raise AssertionError("missing docs/BENCHMARKS.md public compatibility investigation")
    check_public_compatibility_copy(
        benchmark_section[1].split("\n## ", 1)[0], "docs/BENCHMARKS.md"
    )

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
