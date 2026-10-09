#!/usr/bin/env python3
"""Write a small CycloneDX inventory of the five release binaries."""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


TARGETS = (
    ("linux-x86_64", "lcovmerge"),
    ("linux-aarch64", "lcovmerge"),
    ("macos-arm64", "lcovmerge"),
    ("macos-x86_64", "lcovmerge"),
    ("windows-x86_64", "lcovmerge.exe"),
)


def main() -> int:
    if len(sys.argv) != 4:
        raise SystemExit("Usage: write-sbom.py VERSION BUILD_DIR OUTPUT")
    version, build_dir, output = sys.argv[1:]
    components = []
    for target, binary_name in TARGETS:
        binary_root = Path(build_dir)
        versioned_name = f"lcovmerge-{version}-{target}"
        versioned_name += ".exe" if binary_name.endswith(".exe") else ""
        versioned_binary = binary_root / versioned_name
        binary = versioned_binary if versioned_binary.is_file() else binary_root / target / binary_name
        if not binary.is_file():
            raise SystemExit(f"Release binary not found: {binary}")
        digest = hashlib.sha256(binary.read_bytes()).hexdigest()
        components.append(
            {
                "type": "file",
                "bom-ref": f"lcovmerge:{target}",
                "name": binary_name,
                "version": version,
                "hashes": [{"alg": "SHA-256", "content": digest}],
                "properties": [{"name": "lcovmerge:target", "value": target}],
            }
        )

    document = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "version": 1,
        "metadata": {
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "component": {
                "type": "application",
                "bom-ref": f"lcovmerge:{version}",
                "name": "lcovmerge",
                "version": version,
                "licenses": [{"license": {"id": "MIT"}}],
            },
        },
        "components": components,
    }
    Path(output).write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
