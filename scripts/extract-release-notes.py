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

    end = next(
        (index for index in range(start + 1, len(lines)) if re.match(r"^##\s", lines[index])),
        len(lines),
    )
    changelog = "\n".join(lines[start + 1 : end]).strip()
    if not changelog:
        raise SystemExit(f"Changelog section for version {version} is empty")

    highlights = [
        line[2:].strip()
        for line in changelog.splitlines()
        if line.startswith("- ") or line.startswith("* ")
    ][:3]
    if not highlights:
        highlights = ["See the changelog section below."]
    rendered_highlights = "\n".join(f"- {highlight}" for highlight in highlights)

    template = """# lcovmerge v@VERSION@

- lcovmerge v@VERSION@ is available for Linux, macOS, and Windows.
- Merge LCOV tracefiles with bounded record memory and deterministic output.
- Release downloads include SHA-256 checksums, an SBOM, and GitHub build provenance.

## Highlights

@HIGHLIGHTS@

## Install

### Linux x86-64 release archive

```sh
set -eu
version=@VERSION@
base=https://github.com/megasoft1978/lcovmerge/releases/download/v$version
asset=lcovmerge-$version-linux-x86_64.tar.gz
curl -fL "$base/$asset" -o "$asset"
curl -fL "$base/SHA256SUMS" -o SHA256SUMS
checksum_line=$(awk -v asset="$asset" '$2 == asset { line = $0; count++ } END { if (count != 1) exit 1; print line }' SHA256SUMS) || exit 1
printf '%s\\n' "$checksum_line" | sha256sum -c -
tar -xzf "$asset"
sudo install -m 0755 lcovmerge /usr/local/bin/lcovmerge
```

### Homebrew

After the formula update for this version is merged into the tap:

```sh
brew install megasoft1978/tap/lcovmerge
```

### Docker

```sh
docker run --rm -v "$PWD:/work" -w /work ghcr.io/megasoft1978/lcovmerge:v@VERSION@ \\
  --tmpdir /tmp coverage/shard-*.info -o coverage/merged.info
```

### GitHub Action

```yaml
- uses: megasoft1978/lcovmerge@v@VERSION@
  with:
    files: coverage/shards/*.info
    output: coverage/merged.info
    mem-limit: 64M
    version: @VERSION@
```

## Checksum and provenance verification

Use a GitHub CLI version with `attestation verify` support and install Cosign before running these commands. See the [GitHub CLI attestation reference](https://cli.github.com/manual/gh_attestation_verify) and [Sigstore verification guide](https://docs.sigstore.dev/cosign/verifying/verify/).

Verify the downloaded archive against the release checksum manifest:

```sh
set -eu
version=@VERSION@
asset=lcovmerge-$version-linux-x86_64.tar.gz
checksum_line=$(awk -v asset="$asset" '$2 == asset { line = $0; count++ } END { if (count != 1) exit 1; print line }' SHA256SUMS) || exit 1
printf '%s\\n' "$checksum_line" | sha256sum -c -
```

Verify the archive's GitHub build provenance:

```sh
version=@VERSION@
asset=lcovmerge-$version-linux-x86_64.tar.gz
gh attestation verify "$asset" \\
  --repo megasoft1978/lcovmerge \\
  --signer-workflow megasoft1978/lcovmerge/.github/workflows/release.yml \\
  --source-ref "refs/tags/v@VERSION@"
```

Verify the keyless Cosign signature on `SHA256SUMS`:

```sh
cosign verify-blob SHA256SUMS --bundle SHA256SUMS.sigstore.json \\
  --certificate-identity "https://github.com/megasoft1978/lcovmerge/.github/workflows/release.yml@refs/tags/v@VERSION@" \\
  --certificate-oidc-issuer "https://token.actions.githubusercontent.com"
```

## Changelog

@CHANGELOG@
"""
    notes = (
        template.replace("@VERSION@", version)
        .replace("@HIGHLIGHTS@", rendered_highlights)
        .replace("@CHANGELOG@", changelog)
    )
    Path(output_name).write_text(notes, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
