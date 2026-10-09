#!/usr/bin/env python3
"""Small in-memory LCOV reference merger used by tests and differential checks."""

from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path


def number(text: str) -> int:
    if not text or not text.isascii() or not text.isdecimal():
        raise ValueError("expected unsigned decimal")
    value = int(text)
    if value > (1 << 64) - 1:
        raise ValueError("integer overflow")
    return value


def sat_add(left: int, right: int) -> int:
    return min((1 << 64) - 1, left + right)


def fresh() -> dict:
    return {
        "section": False,
        "tn": set(),
        "groups": set(),
        "new_fns": {},
        "old_fns": set(),
        "old_fnda": defaultdict(int),
        "da": {},
        "br": {},
        "mcdc": {},
        "ext": [],
    }


def merge(paths: list[str]) -> dict:
    files: dict[str, dict] = {}
    for filename in paths:
        current = None
        pending_tn = None
        fnl = None
        with open(filename, "r", encoding="utf-8", errors="strict", newline="") as stream:
            for raw in stream:
                line = raw.rstrip("\n")
                if line.endswith("\r"):
                    line = line[:-1]
                if not line or line.startswith("#"):
                    continue
                if line == "end_of_record":
                    if current is None:
                        raise ValueError("format")
                    current["section"] = True
                    current = None
                    fnl = None
                    continue
                if line.startswith("TN:"):
                    if current is not None or pending_tn is not None:
                        raise ValueError("format")
                    pending_tn = line[3:]
                    continue
                if line.startswith(("SF:", "KF:")):
                    if current is not None or len(line) == 3:
                        raise ValueError("format")
                    name = line[3:]
                    current = files.setdefault(name, fresh())
                    current["section"] = True
                    if pending_tn is not None:
                        current["tn"].add(pending_tn)
                    pending_tn = None
                    fnl = None
                    continue
                if current is None:
                    raise ValueError("format")
                if line.startswith(("FNF:", "FNH:", "LF:", "LH:", "BRF:", "BRH:", "MCF:", "MCH:")):
                    number(line.split(":", 1)[1])
                    continue
                if line.startswith("FNL:"):
                    parts = line[4:].split(",")
                    if len(parts) not in (2, 3):
                        raise ValueError("format")
                    index, start = number(parts[0]), number(parts[1])
                    end = number(parts[2]) if len(parts) == 3 else 0
                    fnl = (index, start, end)
                    current["groups"].add((1, start, end, index, ""))
                    continue
                if line.startswith("FNA:"):
                    parts = line[4:].split(",", 2)
                    if len(parts) != 3 or fnl is None:
                        raise ValueError("format")
                    index, count, name = number(parts[0]), number(parts[1]), parts[2]
                    if index != fnl[0] or not name:
                        raise ValueError("format")
                    _, start, end = fnl
                    group = (1, start, end, index, "")
                    current["groups"].add(group)
                    key = (start, end, index, name)
                    current["new_fns"][key] = sat_add(current["new_fns"].get(key, 0), count)
                    continue
                if line.startswith("FN:"):
                    parts = line[3:].split(",", 2)
                    if len(parts) < 2:
                        raise ValueError("format")
                    start = number(parts[0])
                    if len(parts) == 3:
                        end, name = number(parts[1]), parts[2]
                    else:
                        end, name = 0, parts[1]
                    if not name:
                        raise ValueError("format")
                    group = (0, 0, 0, 0, name)
                    current["groups"].add(group)
                    current["old_fns"].add((start, end, name))
                    fnl = None
                    continue
                if line.startswith("FNDA:"):
                    count_field, separator, name = line[5:].partition(",")
                    if not separator or not name:
                        raise ValueError("format")
                    count = number(count_field)
                    current["old_fnda"][name] = sat_add(current["old_fnda"][name], count)
                    fnl = None
                    continue
                if line.startswith("DA:"):
                    parts = line[3:].split(",", 2)
                    if len(parts) < 2:
                        raise ValueError("format")
                    line_no, count = number(parts[0]), number(parts[1])
                    checksum = parts[2] if len(parts) == 3 else ""
                    old = current["da"].get(line_no)
                    if old is None:
                        current["da"][line_no] = [count, checksum]
                    else:
                        old[0] = sat_add(old[0], count)
                        if checksum and (not old[1] or checksum < old[1]):
                            old[1] = checksum
                    fnl = None
                    continue
                if line.startswith("BRDA:"):
                    body = line[5:]
                    branch_fields, separator, count_field = body.rpartition(",")
                    fields = branch_fields.split(",", 2)
                    if not separator or len(fields) != 3:
                        raise ValueError("format")
                    line_no, block = number(fields[0]), fields[1]
                    markers = ""
                    while block and block[0] in "efU":
                        markers += block[0]
                        block = block[1:]
                    block_no = number(block)
                    branch = fields[2]
                    try:
                        branch_no = number(branch)
                        branch_text = ""
                        numeric = True
                    except ValueError:
                        branch_no = 0
                        branch_text = branch
                        numeric = False
                    dash = count_field == "-"
                    count = 0 if dash else number(count_field)
                    key = (line_no, block_no, branch_no, branch_text, markers)
                    old = current["br"].get(key)
                    if old is None:
                        current["br"][key] = [count, dash, numeric]
                    else:
                        if old[1] and not dash:
                            old[0], old[1] = count, False
                        elif not old[1] and not dash:
                            old[0] = sat_add(old[0], count)
                        old[2] = old[2] or numeric
                    fnl = None
                    continue
                if line.startswith("MCDC:"):
                    parts = line[5:].split(",", 5)
                    if len(parts) != 6:
                        raise ValueError("format")
                    line_no = number(parts[0])
                    unreachable = parts[1].startswith("U")
                    group = number(parts[1][1:] if unreachable else parts[1])
                    sense = parts[2]
                    count = number(parts[3])
                    index = number(parts[4])
                    expression = parts[5]
                    if sense not in ("t", "f") or not expression:
                        raise ValueError("format")
                    flags = (1 if sense == "t" else 0) | (2 if unreachable else 0)
                    key = (line_no, group, index, flags)
                    old = current["mcdc"].get(key)
                    if old is None:
                        current["mcdc"][key] = [count, expression]
                    else:
                        old[0] = sat_add(old[0], count)
                        old[1] = min(old[1], expression)
                    fnl = None
                    continue
                if line.startswith("VER:"):
                    current["ext"].append(line)
                elif ":" in line:
                    current["ext"].append(line)
                else:
                    raise ValueError("format")
                fnl = None
    return files


def render(files: dict) -> str:
    output: list[str] = []
    for path in sorted(files):
        record = files[path]
        output.extend(f"TN:{name}\n" for name in sorted(record["tn"]))
        output.append(f"SF:{path}\n")
        new_groups = sorted(group for group in record["groups"] if group[0] == 1)
        aliases = record["new_fns"]
        for _, start, end, index, _name in new_groups:
            if end:
                output.append(f"FNL:{index},{start},{end}\n")
            else:
                output.append(f"FNL:{index},{start}\n")
            for (fn_start, fn_end, fn_index, name), count in sorted(aliases.items()):
                if (fn_start, fn_end, fn_index) == (start, end, index):
                    output.append(f"FNA:{index},{count},{name}\n")
        for start, end, name in sorted(record["old_fns"]):
            output.append(f"FN:{start},{end},{name}\n" if end else f"FN:{start},{name}\n")
        for name, count in sorted(record["old_fnda"].items()):
            output.append(f"FNDA:{count},{name}\n")

        branch_data = record["br"]
        for (line_no, block, branch, branch_text, markers), (count, dash, numeric) in sorted(branch_data.items()):
            block_text = "".join(ch for ch in "efU" if ch in markers) + str(block)
            branch_value = str(branch) if numeric else branch_text
            count_value = "-" if dash else str(count)
            output.append(f"BRDA:{line_no},{block_text},{branch_value},{count_value}\n")
        for (line_no, group, index, flags), (count, expression) in sorted(record["mcdc"].items()):
            unreachable = "U" if flags & 2 else ""
            sense = "t" if flags & 1 else "f"
            output.append(f"MCDC:{line_no},{unreachable}{group},{sense},{count},{index},{expression}\n")
        for line_no, (count, checksum) in sorted(record["da"].items()):
            suffix = f",{checksum}" if checksum else ""
            output.append(f"DA:{line_no},{count}{suffix}\n")
        output.extend(f"{row}\n" for row in sorted(record["ext"]))

        group_hits = set()
        for (start, end, index, name), count in aliases.items():
            if count:
                group_hits.add((1, start, end, index, ""))
        for name, count in record["old_fnda"].items():
            if count:
                group_hits.add((0, 0, 0, 0, name))
        line_hits = sum(count > 0 for count, _checksum in record["da"].values())
        branch_rows = [(key, value) for key, value in branch_data.items() if "U" not in key[4]]
        branch_hits = sum(value[0] > 0 and not value[1] for _key, value in branch_rows)
        mcdc_rows = [(key, value) for key, value in record["mcdc"].items() if not key[3] & 2]
        mcdc_hits = sum(value[0] > 0 for _key, value in mcdc_rows)
        output.extend((
            f"FNF:{len(record['groups'])}\nFNH:{len(group_hits)}\n",
            f"BRF:{len(branch_rows)}\nBRH:{branch_hits}\n",
            f"MCF:{len(mcdc_rows)}\nMCH:{mcdc_hits}\n",
            f"LF:{len(record['da'])}\nLH:{line_hits}\nend_of_record\n",
        ))
    return "".join(output)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("-o", "--output", required=True)
    parser.add_argument("inputs", nargs="+")
    args = parser.parse_args()
    try:
        result = render(merge(args.inputs))
    except (OSError, UnicodeError, ValueError):
        return 2
    Path(args.output).write_text(result, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
