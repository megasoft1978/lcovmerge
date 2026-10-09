"""Run the locally built CLI on the same synthetic inputs as the Action."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


workspace = Path.cwd()
binary = workspace / "bin" / ("lcovmerge.exe" if sys.platform == "win32" else "lcovmerge")
command = [
    str(binary),
    "--branch-coverage",
    "on",
    "--mem-limit",
    "8M",
    "coverage/shards/unit.info",
    "coverage/shards/integration.info",
    "--output",
    "coverage/expected.info",
    "--stats",
]
result = subprocess.run(command, cwd=workspace, text=True, check=False)
if result.returncode != 0:
    raise SystemExit(result.returncode)
