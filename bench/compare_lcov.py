#!/usr/bin/env python3
"""Compare normalized LCOV records while holding at most one SF in memory."""

from __future__ import annotations

import argparse
import posixpath
import sys
from collections.abc import Iterator
from pathlib import Path

MetricKey = tuple[str, ...]
MetricValue = int | None
SourceRecord = tuple[str, dict[MetricKey, MetricValue]]


def normalize_sf(value: str) -> str:
    return posixpath.normpath(value.replace("\\", "/"))


def parse_metric(line: str) -> tuple[MetricKey, MetricValue] | None:
    tag, _, value = line.partition(":")
    if tag == "DA":
        fields = value.split(",")
        if len(fields) < 2:
            raise ValueError(f"malformed DA record: {line[:160]}")
        return ("DA", fields[0]), int(fields[1])
    if tag == "FN":
        fields = value.split(",")
        if len(fields) < 2:
            raise ValueError(f"malformed FN record: {line[:160]}")
        # lcov 2.6 may add an end line; it is not part of the semantic key.
        if len(fields) >= 3:
            return ("FN", fields[0], fields[-1]), None
        return ("FN", fields[0], fields[1]), None
    if tag == "FNDA":
        hits, separator, name = value.partition(",")
        if not separator:
            raise ValueError(f"malformed FNDA record: {line[:160]}")
        return ("FNDA", name), int(hits)
    if tag == "BRDA":
        fields = value.split(",")
        if len(fields) != 4:
            raise ValueError(f"malformed BRDA record: {line[:160]}")
        return ("BRDA", fields[0], fields[1], fields[2]), None if fields[3] == "-" else int(fields[3])
    return None


def add_metric(metrics: dict[MetricKey, MetricValue], key: MetricKey,
               value: MetricValue) -> None:
    if key not in metrics:
        metrics[key] = value
        return
    if key[0] == "FN":
        return
    previous = metrics[key]
    if previous is None:
        metrics[key] = value
    elif value is not None:
        metrics[key] = previous + value


def read_records(path: Path) -> Iterator[SourceRecord]:
    sf: str | None = None
    metrics: dict[MetricKey, MetricValue] = {}
    function_starts: dict[str, str] = {}
    function_annotations: dict[str, tuple[str, int]] = {}

    def finish() -> SourceRecord:
        for function_id, (name, hits) in function_annotations.items():
            start = function_starts.get(function_id)
            if start is not None:
                add_metric(metrics, ("FN", start, name), None)
                add_metric(metrics, ("FNDA", name), hits)
        return sf or "", metrics

    with path.open("r", encoding="utf-8", errors="replace") as source:
        for raw_line in source:
            line = raw_line.rstrip("\r\n")
            if line.startswith("SF:"):
                if sf is not None:
                    yield finish()
                sf = normalize_sf(line[3:])
                metrics = {}
                function_starts = {}
                function_annotations = {}
            elif line == "end_of_record":
                if sf is not None:
                    yield finish()
                    sf = None
                    metrics = {}
                    function_starts = {}
                    function_annotations = {}
            elif sf is not None:
                if line.startswith("FNL:"):
                    fields = line[4:].split(",")
                    if len(fields) < 3:
                        raise ValueError(f"malformed FNL record: {line[:160]}")
                    function_starts[fields[0]] = fields[1]
                elif line.startswith("FNA:"):
                    fields = line[4:].split(",")
                    if len(fields) < 3:
                        raise ValueError(f"malformed FNA record: {line[:160]}")
                    function_annotations[fields[0]] = (fields[-1], int(fields[1]))
                else:
                    parsed = parse_metric(line)
                    if parsed is not None:
                        add_metric(metrics, *parsed)
        if sf is not None:
            yield finish()


def sources(path: Path) -> Iterator[SourceRecord]:
    """Aggregate adjacent duplicate SF records and require sorted SF order."""
    current_sf: str | None = None
    current_metrics: dict[MetricKey, MetricValue] = {}
    for sf, metrics in read_records(path):
        if current_sf is None:
            current_sf = sf
            current_metrics = metrics
        elif sf == current_sf:
            for key, value in metrics.items():
                add_metric(current_metrics, key, value)
        else:
            if sf < current_sf:
                raise ValueError(
                    f"SF records in {path} are not sorted: {sf!r} follows {current_sf!r}"
                )
            yield current_sf, current_metrics
            current_sf = sf
            current_metrics = metrics
    if current_sf is not None:
        yield current_sf, current_metrics


def compare(left_path: Path, right_path: Path) -> tuple[bool, str]:
    left = iter(sources(left_path))
    right = iter(sources(right_path))
    left_item = next(left, None)
    right_item = next(right, None)
    source_count = 0
    row_count = 0
    differences: list[str] = []

    while left_item is not None or right_item is not None:
        if right_item is None or (left_item is not None and left_item[0] < right_item[0]):
            differences.append(f"missing from right: SF:{left_item[0]}")
            left_item = next(left, None)
            continue
        if left_item is None or right_item[0] < left_item[0]:
            differences.append(f"missing from left: SF:{right_item[0]}")
            right_item = next(right, None)
            continue

        source_count += 1
        left_sf, left_metrics = left_item
        _right_sf, right_metrics = right_item
        row_count += max(len(left_metrics), len(right_metrics))
        left_keys = set(left_metrics)
        right_keys = set(right_metrics)
        missing = sorted(left_keys - right_keys)
        extra = sorted(right_keys - left_keys)
        changed = sorted(key for key in left_keys & right_keys
                         if left_metrics[key] != right_metrics[key])
        if missing or extra or changed:
            differences.append(
                f"SF:{left_sf}: left-only={len(missing)} right-only={len(extra)} "
                f"value-differences={len(changed)}"
            )
            for label, keys in (("left-only", missing), ("right-only", extra),
                                ("value-difference", changed)):
                for key in keys[:2]:
                    differences.append(
                        f"  {label} {key!r}: left={left_metrics.get(key)!r} "
                        f"right={right_metrics.get(key)!r}"
                    )
            if len(differences) >= 12:
                break
        left_item = next(left, None)
        right_item = next(right, None)

    if differences:
        return False, (
            f"FAIL normalized LCOV comparison; compared {source_count} SF records and "
            f"{row_count} metric keys; " + " | ".join(differences)
        )
    return True, (
        f"PASS normalized SF/DA/FN/FNDA/BRDA keys and counts; "
        f"compared {source_count} SF records and {row_count} metric keys"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("left", type=Path, help="lcovmerge output")
    parser.add_argument("right", type=Path, help="lcov output")
    args = parser.parse_args()
    try:
        equal, summary = compare(args.left, args.right)
    except (OSError, ValueError) as error:
        print(f"ERROR comparing LCOV outputs: {error}", file=sys.stderr)
        return 2
    print(summary)
    return 0 if equal else 1


if __name__ == "__main__":
    raise SystemExit(main())
