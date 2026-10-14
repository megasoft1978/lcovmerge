#!/usr/bin/env python3
"""Write a CycloneDX inventory of release binaries and locked Rust crates."""

from __future__ import annotations

import hashlib
import json
import sys
import tomllib
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
    dependencies = []
    binary_refs = []
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
        binary_refs.append(f"lcovmerge:{target}")

    lock = tomllib.loads(Path("Cargo.lock").read_text(encoding="utf-8"))
    crate_refs: dict[tuple[str, str], str] = {}
    lock_packages = [
        package for package in lock.get("package", [])
        if package.get("source", "").startswith("registry+")
    ]
    for package in lock_packages:
        name = package["name"]
        crate_version = package["version"]
        ref = f"pkg:cargo/{name}@{crate_version}"
        crate_refs[(name, crate_version)] = ref
        crate = {
            "type": "library",
            "bom-ref": ref,
            "name": name,
            "version": crate_version,
            "purl": ref,
        }
        checksum = package.get("checksum")
        if checksum:
            crate["hashes"] = [{"alg": "SHA-256", "content": checksum}]
        components.append(crate)

    def dependency_names(package: dict[str, object]) -> list[str]:
        refs = []
        for entry in package.get("dependencies", []):
            parts = str(entry).split()
            if len(parts) >= 2:
                ref = crate_refs.get((parts[0], parts[1]))
                if ref:
                    refs.append(ref)
            else:
                refs.extend(
                    ref for (name, _), ref in crate_refs.items() if name == parts[0]
                )
        return sorted(set(refs))

    root_package = next(
        package for package in lock.get("package", [])
        if package.get("name") == "lcovmerge" and package.get("version") == version
    )
    root_ref = f"lcovmerge:{version}"
    direct_refs = dependency_names(root_package)
    dependencies.append({"ref": root_ref, "dependsOn": direct_refs})
    for package in lock_packages:
        ref = crate_refs[(package["name"], package["version"])]
        dependencies.append({"ref": ref, "dependsOn": dependency_names(package)})
    dependencies.extend({"ref": ref, "dependsOn": [root_ref]} for ref in binary_refs)

    document = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "version": 1,
        "metadata": {
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "component": {
                "type": "application",
                "bom-ref": root_ref,
                "name": "lcovmerge",
                "version": version,
                "licenses": [{"license": {"id": "MIT"}}],
                "properties": [{"name": "lcovmerge:implementation", "value": "Rust"}],
            },
        },
        "components": components,
        "dependencies": dependencies,
    }
    Path(output).write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
