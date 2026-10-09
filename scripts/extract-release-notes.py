#!/usr/bin/env python3
"""Extract one version section from a Markdown changelog."""

from __future__ import annotations

import re
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 4:
        raise SystemExit("Usage: extract-release-notes.py VERSION CHANGELOG OUTPUT")
    version, changelog_name, output_name = sys.argv[1:]
    changelog = Path(changelog_name)
    if not changelog.is_file():
        raise SystemExit(f"Changelog not found: {changelog}")

    headings = {
        f"## [{version}]",
        f"## {version}",
        f"## [v{version}]",
        f"## v{version}",
    }
    lines = changelog.read_text(encoding="utf-8").splitlines()
    start = next(
        (
            index
            for index, line in enumerate(lines)
            if any(line == heading or line.startswith(heading + " ") for heading in headings)
        ),
        None,
    )
    if start is None:
        raise SystemExit(f"No changelog section found for version {version}")

    end = next((index for index in range(start + 1, len(lines)) if re.match(r"^##\s", lines[index])), len(lines))
    notes = "\n".join(lines[start + 1 : end]).strip()
    if not notes:
        raise SystemExit(f"Changelog section for version {version} is empty")
    Path(output_name).write_text(notes + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
