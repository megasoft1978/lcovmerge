#!/usr/bin/env python3
"""Render release URLs and checksums into the Homebrew and Scoop manifests."""
from __future__ import annotations

import json
from pathlib import Path
import re
import sys


def hashes(sums_text: str, version: str) -> dict[str, str]:
    assets = {
        "macos-arm64": f"lcovmerge-{version}-macos-arm64.tar.gz",
        "macos-x86_64": f"lcovmerge-{version}-macos-x86_64.tar.gz",
        "windows-x86_64": f"lcovmerge-{version}-windows-x86_64.zip",
    }
    found = {}
    for line in sums_text.splitlines():
        match = re.fullmatch(r"([0-9a-fA-F]{64})  (.+)", line)
        if match and match.group(2) in assets.values():
            found[match.group(2)] = match.group(1).lower()
    missing = [asset for asset in assets.values() if asset not in found]
    if missing:
        raise ValueError("missing valid SHA-256 entries for: " + ", ".join(missing))
    return {platform: found[asset] for platform, asset in assets.items()}


def _replace_one(text: str, pattern: str, replacement: str, label: str) -> str:
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.MULTILINE)
    if count != 1:
        raise ValueError(f"expected one {label} field, found {count}")
    return updated


def render(formula_text: str, scoop_text: str, sums_text: str, version: str) -> tuple[str, str]:
    checksum = hashes(sums_text, version)
    formula = _replace_one(formula_text, r'^(\s*version\s+")[^"]*("\s*)$',
                           rf'\g<1>{version}\g<2>', "Homebrew version")
    formula = _replace_one(
        formula,
        r'^(\s*url\s+")[^"]*lcovmerge-[^/" ]+-macos-arm64\.tar\.gz("\s*)$',
        rf'\g<1>https://github.com/megasoft1978/lcovmerge/releases/download/v{version}/lcovmerge-{version}-macos-arm64.tar.gz\g<2>',
        "Homebrew ARM URL",
    )
    formula = _replace_one(
        formula,
        r'^(\s*url\s+")[^"]*lcovmerge-[^/" ]+-macos-x86_64\.tar\.gz("\s*)$',
        rf'\g<1>https://github.com/megasoft1978/lcovmerge/releases/download/v{version}/lcovmerge-{version}-macos-x86_64.tar.gz\g<2>',
        "Homebrew Intel URL",
    )
    sha_pattern = r'^(\s*sha256\s+")[0-9a-fA-F]{64}("\s*)$'
    checksum_values = iter((checksum["macos-arm64"], checksum["macos-x86_64"]))
    formula, checksum_count = re.subn(
        sha_pattern, lambda match: f'{match.group(1)}{next(checksum_values)}{match.group(2)}',
        formula, flags=re.MULTILINE,
    )
    if checksum_count != 2:
        raise ValueError(f"expected two Homebrew checksum fields, found {checksum_count}")

    manifest = json.loads(scoop_text)
    manifest["version"] = version
    windows = manifest["architecture"]["64bit"]
    windows["url"] = (
        f"https://github.com/megasoft1978/lcovmerge/releases/download/v{version}/"
        f"lcovmerge-{version}-windows-x86_64.zip"
    )
    windows["hash"] = checksum["windows-x86_64"]
    return formula, json.dumps(manifest, indent=2) + "\n"


def update_files(sums_path: Path, version: str, formula_path: Path, scoop_path: Path) -> None:
    formula, scoop = render(formula_path.read_text(), scoop_path.read_text(),
                            sums_path.read_text(), version)
    formula_path.write_text(formula)
    scoop_path.write_text(scoop)


def main(argv: list[str]) -> int:
    if len(argv) != 5:
        print("Usage: update-packaging.py SHA256SUMS VERSION HOMEBREW_FORMULA SCOOP_MANIFEST",
              file=sys.stderr)
        return 2
    sums_path, formula_path, scoop_path = Path(argv[1]), Path(argv[3]), Path(argv[4])
    version = argv[2]
    try:
        update_files(sums_path, version, formula_path, scoop_path)
    except (OSError, ValueError, json.JSONDecodeError, KeyError, TypeError) as exc:
        print(f"packaging update failed: {exc}", file=sys.stderr)
        return 1
    print(f"Updated Homebrew and Scoop manifests for version {version}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
