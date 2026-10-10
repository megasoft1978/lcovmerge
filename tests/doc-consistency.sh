#!/bin/sh
set -eu

# pwd -W yields a native path under MSYS2 so the Windows Python can open files; plain pwd elsewhere.
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && { pwd -W 2>/dev/null || pwd; })
binary=${1:-$root/bin/lcovmerge}
python3 -I "$root/tests/check_site_rendering.py" "$root"
exec python3 - "$root" "$binary" <<'PY'
import re
import subprocess
import sys
from pathlib import Path

root = Path(sys.argv[1])
binary = sys.argv[2]
help_text = subprocess.run([binary, "--help"], check=True, capture_output=True, text=True).stdout
option_pattern = re.compile(r"--[a-z][a-z0-9-]*|(?<![A-Za-z0-9])-([a-z])")

def options(text: str) -> set[str]:
    found = set()
    for match in option_pattern.finditer(text):
        token = match.group(0)
        found.add(token)
    return found

help_options = options("\n".join(
    line.strip().split("  ", 1)[0]
    for line in help_text.splitlines()[1:]
    if line.lstrip().startswith("-")
))

man_lines = (root / "man/lcovmerge.1").read_text(encoding="utf-8").splitlines()
man_start = man_lines.index(".SH OPTIONS")
man_end = next(i for i in range(man_start + 1, len(man_lines)) if man_lines[i].startswith(".SH "))
man_definitions = "\n".join(line for line in man_lines[man_start:man_end] if line.startswith((".B ", ".BI ")))
man_options = options(man_definitions)

usage_text = (root / "docs/USAGE.md").read_text(encoding="utf-8")
usage_start = usage_text.index("## Options")
usage_end = usage_text.index("## Exit status")
usage_options = options(usage_text[usage_start:usage_end])

for name, found in (("man page", man_options), ("USAGE.md", usage_options)):
    missing = sorted(help_options - found)
    extra = sorted(found - help_options)
    if missing or extra:
        details = []
        if missing:
            details.append("missing: " + ", ".join(missing))
        if extra:
            details.append("extra: " + ", ".join(extra))
        raise SystemExit(f"{name} option list differs from --help ({'; '.join(details)})")

exit_statuses = {
    "0": "Merge completed.",
    "1": "Invalid or incomplete command-line options.",
    "2": "Input or tracefile format error, including strict checksum disagreement.",
    "3": "Input/output or temporary-file I/O failure, allocation failure, or interruption.",
}
for code, meaning in exit_statuses.items():
    tick = chr(96)
    row = f"| {tick}{code}{tick} | {meaning} |"
    if row not in usage_text:
        raise SystemExit(f"USAGE.md exit-status table is missing or changed status {code}")

print(f"doc_option_lists=PASS options={len(help_options)}")
print(f"doc_exit_statuses=PASS statuses={len(exit_statuses)}")
PY
