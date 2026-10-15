#!/usr/bin/env python3
"""Check that generated site benchmark sections match the canonical renderer."""

import html
import importlib.util
import json
import re
import sys
from html.parser import HTMLParser
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


class VisibleText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []
        self.ignored_tags = []
        self.parts = []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        classes = dict(attrs).get("class", "").split()
        if tag in {"head", "script", "style", "pre"} or "skip-link" in classes:
            self.ignored_tags.append(tag)

    def handle_endtag(self, tag):
        if tag in self.ignored_tags:
            index = len(self.ignored_tags) - 1 - self.ignored_tags[::-1].index(tag)
            del self.ignored_tags[index]
        if tag in self.tags:
            self.tags = self.tags[:len(self.tags) - 1 - self.tags[::-1].index(tag)]

    def handle_data(self, data):
        if not self.ignored_tags:
            self.parts.append(data)


def word_count(text):
    return len(re.findall(r"\b[\w]+(?:[’'.-][\w]+)*\b", text, flags=re.UNICODE))


def check_featured_rendering(root, renderer):
    display_cases = (
        (renderer.seconds_display(0), "0 s"),
        (renderer.seconds_display(0.259979), "260 ms"),
        (renderer.seconds_display(80.966641), "1m 21s"),
        (renderer.seconds_display(893.540574), "14m 53.5s"),
        (renderer.input_size_display(20_274_194_280), "20.3 GB"),
        (renderer.bytes_display(2_853_126_144, binary=True), "2.66 GiB"),
    )
    for actual, expected in display_cases:
        if actual != expected:
            raise AssertionError(f"human-readable benchmark display mismatch: {actual!r} != {expected!r}")

    readme_path = root / "README.md"
    readme = readme_path.read_text(encoding="utf-8")
    expected_readme = renderer.featured_readme()
    if section_text(readme, renderer.README_BENCHMARK_START,
                    renderer.README_BENCHMARK_END) != expected_readme.strip():
        raise AssertionError("stale generated README benchmark section")
    if "Experimental profile-guided build" in readme:
        raise AssertionError("README must fold profile-guided results into the main benchmark")

    editorial = re.sub(
        re.escape(renderer.README_BENCHMARK_START) + r".*?" +
        re.escape(renderer.README_BENCHMARK_END), " ", readme, flags=re.DOTALL,
    )
    editorial = re.sub(r"```.*?```", " ", editorial, flags=re.DOTALL)
    editorial = re.sub(r"<!--.*?-->", " ", editorial, flags=re.DOTALL)
    editorial = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", editorial)
    if word_count(editorial) > 150:
        raise AssertionError(f"README has {word_count(editorial)} editorial words outside code and generated sections")

    blocks = re.findall(r"```sh\s*(.*?)\s*```", readme, flags=re.DOTALL)
    if len(blocks) != 2 or any(len([line for line in block.splitlines() if line.strip()]) != 3 for block in blocks):
        raise AssertionError("README install and usage examples must each have three lines")
    if "set -eu" not in blocks[0] or "git clone https://github.com/megasoft1978/lcovmerge.git" not in blocks[0] or "cd lcovmerge && make" not in blocks[0]:
        raise AssertionError("README must build the current Rust source and stop on command errors")

    home_path = root / "docs" / "site" / "index.html"
    home = home_path.read_text(encoding="utf-8")
    expected_home = renderer.featured_site()
    if section_text(home, renderer.HOME_BENCHMARK_START,
                    renderer.HOME_BENCHMARK_END) != expected_home.strip():
        raise AssertionError("stale generated landing-page benchmark section")
    if expected_home.count('class="home-stat"') != 2:
        raise AssertionError("featured benchmark should render two side-by-side statistics")
    if "sorted · 1 job" not in expected_home or "sorted · 4 jobs" not in expected_home:
        raise AssertionError("featured Rust medians are missing their workload captions")
    if re.search(r"`(?:\.info|\.gcda|\.profraw|lcov -a)`", home):
        raise AssertionError("homepage prose contains literal Markdown backticks")
    for code_markup in (
        "<code>.info</code>", "<code>.gcda</code>", "<code>.profraw</code>",
        "<code>lcov -a</code>",
    ):
        if code_markup not in home:
            raise AssertionError(f"homepage prose did not render {code_markup} as code")
    if len(re.findall(r"<section\b", home)) > 4:
        raise AssertionError("landing page has more than four sections")
    if "104.6 MB" not in home or "64" not in home:
        raise AssertionError("homepage benchmark omitted current Rust measurements")
    if 'role="tablist"' in home:
        raise AssertionError("landing page should not add tabs")
    visible = VisibleText()
    visible.feed(home)
    visible_words = word_count(" ".join(visible.parts))
    if visible_words > 250:
        raise AssertionError(f"landing page has {visible_words} visible words outside code")

    rust = json.loads((root / "data" / "rust-port-validation.json").read_text(encoding="utf-8"))
    if section_text(readme, renderer.README_BENCHMARK_START, renderer.README_BENCHMARK_END) != renderer.featured_readme():
        raise AssertionError("README Rust benchmark is not generated from raw validation data")
    for name, workload in rust["workloads"].items():
        hashes = {v["output_sha256"] if "output_sha256" in v else workload["output_sha256"] for v in workload["variants"].values()}
        if len(hashes) != 1:
            raise AssertionError(f"{name} Rust validation outputs differ")
    if "207 ms" not in expected_home or "145 ms" not in expected_home or "65 ms" not in expected_home or "64 ms" not in expected_home:
        raise AssertionError("featured homepage values differ from the Rust 19-run medians")

    synthetic = {
        "host": "runner-a",
        "datasets": [
            {"name": "M", "input_bytes": 10, "results": {
                "lcovmerge": {"status": "OK", "time_s": 2},
                "lcov": {"status": "OK", "time_s": 5},
            }},
            {"name": "XXL", "input_bytes": 100, "results": {
                "lcovmerge": {"status": "OK", "time_s": 10, "host": "runner-a"},
                "lcov": {"status": "OK", "time_s": 90, "host": "runner-b"},
            }},
        ],
    }
    if renderer.featured_dataset(synthetic)["name"] != "M":
        raise AssertionError("featured benchmark compared results from different hosts")
    synthetic["datasets"][1]["results"]["lcov"]["host"] = "runner-a"
    if renderer.featured_dataset(synthetic)["name"] != "XXL":
        raise AssertionError("new largest completed pair did not become the featured benchmark")
    synthetic["datasets"][1]["results"]["lcov"]["status"] = "TIMEOUT"
    synthetic["datasets"][1]["results"]["lcov"]["exit_code"] = 124
    if renderer.featured_dataset(synthetic)["name"] != "M":
        raise AssertionError("timed-out benchmark incorrectly became the featured pair")
    if renderer.comparison_ratios(synthetic["datasets"][1], "lcov") is not None:
        raise AssertionError("timed-out comparator incorrectly received a speed or memory ratio")
    print(f"featured_benchmark_rendering=PASS rust_date={rust['date']} home_words={visible_words}")


def check_benchmark_data(root, renderer):
    data = json.loads((root / "data" / "benchmarks.json").read_text(encoding="utf-8"))
    mirror = json.loads(
        (root / "docs" / "site" / "data" / "benchmarks.json").read_text(encoding="utf-8")
    )
    if mirror != renderer.site_benchmarks():
        raise AssertionError("site benchmark JSON is stale against the canonical renderer and data")
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", str(data.get("version", ""))):
        raise AssertionError("historical benchmark record has no valid measured version")
    names = [dataset.get("name") for dataset in data.get("datasets", [])]
    if len(names) != len(set(names)) or not names:
        raise AssertionError("canonical benchmark dataset names are empty or duplicated")

    def check_result(result, label):
        status = str(result.get("status", ""))
        run_count = result.get("run_count")
        if run_count is not None and (not isinstance(run_count, int) or run_count < 0):
            raise AssertionError(f"{label} has an invalid run_count")
        elapsed = result.get("time_s")
        if elapsed is not None and (not isinstance(elapsed, (int, float)) or elapsed <= 0):
            raise AssertionError(f"{label} has a nonpositive or invalid time")
        if status.casefold() in {"not measured", "not measured (this run)", "unavailable", "skipped"}:
            if run_count not in (None, 0) or elapsed is not None:
                raise AssertionError(f"{label} claims measurements despite status {status!r}")
        if status == "OK":
            if result.get("exit_code") not in (None, 0) or elapsed is None or run_count == 0:
                raise AssertionError(f"{label} has OK status without a successful measurement")
            rss = result.get("rss_bytes")
            if rss is not None and (not isinstance(rss, int) or rss <= 0):
                raise AssertionError(f"{label} has an invalid peak RSS")

    for dataset in data.get("datasets", []):
        input_bytes = dataset.get("input_bytes")
        if input_bytes is not None and input_bytes <= 0:
            raise AssertionError(f"dataset {dataset.get('name')} has no positive input size")
        if input_bytes is None and dataset.get("name") != "REAL":
            raise AssertionError(f"dataset {dataset.get('name')} is missing its input size")
        for tool, result in dataset.get("results", {}).items():
            check_result(result, f"{dataset.get('name')} / {tool}")
            throughput = result.get("throughput_mb_s")
            elapsed = result.get("time_s")
            if throughput is not None and elapsed is not None and input_bytes is not None:
                calculated = input_bytes / elapsed / 1_000_000
                if abs(throughput - calculated) > 0.15:
                    raise AssertionError(
                        f"{dataset.get('name')} / {tool} throughput does not match bytes and time"
                    )
    for host in data.get("host_results", []):
        for dataset in host.get("datasets", []):
            for tool, result in dataset.get("results", {}).items():
                check_result(result, f"{host.get('label')} / {dataset.get('name')} / {tool}")
    default_build = data.get("default_build", {})
    if default_build.get("optimization") != "profile-guided":
        raise AssertionError("benchmark default build must record profile-guided optimization")
    if not default_build.get("training_dataset") or not default_build.get("compiler"):
        raise AssertionError("benchmark default build is missing training or compiler provenance")
    labels = data.get("tool_labels", {})
    if "(PGO)" not in labels.get("lcovmerge", ""):
        raise AssertionError("main lcovmerge benchmark label must identify the profile-guided build")
    if labels.get("lcov-result-merger") != "lcov-result-merger 6.0.0":
        raise AssertionError("benchmark must identify lcov-result-merger 6.0.0")
    xxl = next((item for item in data["datasets"] if item.get("name") == "XXL"), None)
    if xxl is None:
        raise AssertionError("benchmark is missing the XXL result")
    xxl_merger = xxl.get("results", {}).get("lcov-result-merger", {})
    if xxl_merger.get("status") not in {"OK", "TIMEOUT"}:
        raise AssertionError("XXL lcov-result-merger attempt must be reported as measured or timed out")
    if xxl_merger.get("status") == "TIMEOUT":
        if xxl_merger.get("cutoff_seconds") != 3600 or xxl_merger.get("run_count") != 0:
            raise AssertionError("XXL merger timeout must record its 60-minute cutoff and no completed run")


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
    readme = (root / "README.md").read_text(encoding="utf-8")
    architecture = (root / "docs" / "ARCHITECTURE.md").read_text(encoding="utf-8")
    if ("This Rust CLI and library" not in readme or
            "Version 1.2.0 is the first Rust release" not in readme):
        raise AssertionError("README must identify the Rust implementation and release")
    if "There is no C foreign-function interface" not in architecture:
        raise AssertionError("architecture page must describe the native Rust implementation")
    for page_name, required in (
        ("index.html", "native Rust CLI"),
        ("docs.html", "Version 1.2.0 is the first Rust release"),
        ("benchmarks.html", "archived tables below describe a previous release"),
    ):
        page_text = (root / "docs" / "site" / page_name).read_text(encoding="utf-8")
        if required not in page_text:
            raise AssertionError(f"{page_name} is missing the Rust port note: {required}")
    check_stale_windows_verification_copy(root)
    renderer = load_renderer(root)
    check_benchmark_data(root, renderer)
    check_featured_rendering(root, renderer)
    page_path = root / "docs" / "site" / "benchmarks.html"
    page = page_path.read_text(encoding="utf-8")
    if "Experimental profile-guided build" in page:
        raise AssertionError("site must fold profile-guided results into the main benchmark")
    if "20,274,194,280 bytes" in page or "657,966,211 bytes" in page:
        raise AssertionError("site benchmark page displays raw byte counts")
    if "154.66037 s" in page or "893.540574 s" in page:
        raise AssertionError("site benchmark page displays over-precise durations")
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
