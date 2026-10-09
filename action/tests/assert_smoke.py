"""Assert that the action output matches the direct CLI output and exposes summary."""

from __future__ import annotations

import os
from pathlib import Path


workspace = Path.cwd()
actual = workspace / "coverage" / "action-merged.info"
expected = workspace / "coverage" / "expected.info"
if not actual.is_file():
    raise SystemExit("Action output file was not created")
if not expected.is_file():
    raise SystemExit("CLI oracle output file was not created")
if actual.read_bytes() != expected.read_bytes():
    raise SystemExit("Action output differs from the direct CLI result")
summary = os.environ.get("ACTION_SUMMARY", "").strip()
if not summary:
    raise SystemExit("The action summary output is empty")
print("Action output exists and is byte-identical to the CLI result.")
print("Action summary output is present.")
