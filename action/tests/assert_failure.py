"""Assert that a deliberately invalid Action invocation failed without output."""

from __future__ import annotations

import os
from pathlib import Path


outcome = os.environ.get("ACTION_OUTCOME", "")
output = Path(os.environ["ACTION_OUTPUT_PATH"])
if outcome != "failure":
    raise SystemExit(f"Expected the Action step to fail; got outcome {outcome!r}")
if output.exists():
    raise SystemExit(f"Failed Action invocation left an output file: {output}")
print("Invalid Action invocation failed and left no output file.")
