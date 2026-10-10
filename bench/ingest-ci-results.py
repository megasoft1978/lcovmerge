#!/usr/bin/env python3
"""Merge a Linux or Windows CI benchmark artifact into canonical benchmark data."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import tempfile
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_DATA = ROOT / "data/benchmarks.json"
DEFAULT_SITE_DATA = ROOT / "docs/site/data/benchmarks.json"


def load_json(path: pathlib.Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"cannot read JSON from {path}: {error}") from error
    if not isinstance(value, dict):
        raise SystemExit(f"expected a JSON object in {path}")
    return value


def validate_artifact(artifact: dict[str, Any]) -> None:
    if artifact.get("schema_version") != 1:
        raise SystemExit("unsupported benchmark artifact schema_version")
    platform_name = artifact.get("platform")
    if platform_name not in (None, "windows"):
        raise SystemExit(f"unsupported benchmark artifact platform: {platform_name}")
    required_host = ("runner_label", "os", "kernel", "cpu_model", "logical_cores",
                     "memory_bytes", "compiler", "lcov")
    host = artifact.get("host")
    if not isinstance(host, dict) or any(host.get(key) in (None, "", "unavailable")
                                         for key in required_host):
        raise SystemExit("artifact is missing required host information")
    if not isinstance(artifact.get("source"), dict):
        raise SystemExit("artifact is missing source metadata")
    if not isinstance(artifact.get("method"), dict):
        raise SystemExit("artifact is missing measurement method metadata")
    datasets = artifact.get("datasets")
    if not isinstance(datasets, list) or not datasets:
        raise SystemExit("artifact has no dataset results")
    for dataset in datasets:
        if not isinstance(dataset, dict) or not isinstance(dataset.get("name"), str):
            raise SystemExit("artifact contains an invalid dataset record")
        if dataset.get("status") == "SKIPPED":
            continue
        if not isinstance(dataset.get("input_bytes"), int):
            raise SystemExit(f"dataset {dataset['name']} has no input byte count")
        if not isinstance(dataset.get("results"), dict):
            raise SystemExit(f"dataset {dataset['name']} has no tool results")
        if platform_name == "windows":
            results = dataset["results"]
            for tool in ("lcovmerge", "lcovmerge_ucrt64_gcc"):
                if not isinstance(results.get(tool), dict):
                    raise SystemExit(f"Windows dataset {dataset['name']} has no {tool} result")
                result = results[tool]
                for key in ("time_s", "min_time_s", "throughput_mb_s", "status", "exit_code"):
                    if key not in result:
                        raise SystemExit(f"Windows {tool} result for {dataset['name']} is missing {key}")


def entry_id(artifact: dict[str, Any]) -> str:
    source = artifact["source"]
    run_id = source.get("run_id")
    revision = source.get("revision", "unknown")
    repository = source.get("repository", "unknown")
    if run_id:
        attempt = source.get("run_attempt") or "1"
        return f"github:{repository}:{run_id}:{attempt}:{revision}"
    date = artifact.get("measurement_date", "unknown")
    runner = artifact["host"].get("runner_label", "unknown")
    return f"local:{repository}:{date}:{runner}:{revision}"


def host_entry(artifact: dict[str, Any]) -> dict[str, Any]:
    host = artifact["host"]
    source = artifact["source"]
    provider = "GitHub-hosted" if source.get("run_id") else "local"
    label = f"{host['runner_label']} {provider} benchmark host"
    entry = {
        "entry_schema_version": 1,
        "id": entry_id(artifact),
        "label": label,
        "measurement_date": artifact["measurement_date"],
        "host": host,
        "source": source,
        "method": artifact["method"],
        "tool_labels": artifact.get("tool_labels", {}),
        "datasets": artifact["datasets"],
    }
    if artifact.get("platform") == "windows":
        entry["platform"] = "windows"
        entry["label"] = (f"{label} (Zig release build and MSYS2 UCRT64 GCC -O2)")
    return entry


def merge_entry(document: dict[str, Any], entry: dict[str, Any]) -> None:
    entries = document.setdefault("host_results", [])
    if not isinstance(entries, list):
        raise SystemExit("canonical benchmark host_results field must be an array")
    for index, current in enumerate(entries):
        if isinstance(current, dict) and current.get("id") == entry["id"]:
            entries[index] = entry
            return
    entries.append(entry)


def write_json(path: pathlib.Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp",
                                             dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
            json.dump(value, output, indent=2, ensure_ascii=False)
            output.write("\n")
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=pathlib.Path,
                        help="benchmark.json from a Linux or Windows benchmark workflow artifact")
    parser.add_argument("--data", type=pathlib.Path, default=DEFAULT_DATA,
                        help="canonical benchmark JSON path")
    parser.add_argument("--site-data", type=pathlib.Path, default=DEFAULT_SITE_DATA,
                        help="generated site benchmark JSON mirror")
    parser.add_argument("--no-site-mirror", action="store_true",
                        help="update only --data")
    args = parser.parse_args()

    artifact = load_json(args.artifact)
    validate_artifact(artifact)
    entry = host_entry(artifact)
    canonical = load_json(args.data)
    if "datasets" not in canonical or "host" not in canonical:
        raise SystemExit(f"{args.data} does not look like canonical benchmark data")
    merge_entry(canonical, entry)

    mirror = None
    if not args.no_site_mirror and args.data.resolve() == DEFAULT_DATA.resolve():
        mirror = load_json(args.site_data)
        site_entries = mirror.setdefault("host_results", [])
        if not isinstance(site_entries, list):
            raise SystemExit("site benchmark host_results field must be an array")
        merge_entry(mirror, entry)

    write_json(args.data, canonical)
    if mirror is not None:
        write_json(args.site_data, mirror)
    print(f"Merged {entry['label']} measurements into {args.data} (id={entry['id']})")
    if mirror is not None:
        print(f"Updated site benchmark data in {args.site_data}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
