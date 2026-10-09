#!/bin/sh
set -eu

tag=${GITHUB_REF_NAME:-}
if [ -z "$tag" ]; then
  printf 'GITHUB_REF_NAME is required to create a release.\n' >&2
  exit 1
fi
if [ ! -f dist/release/RELEASE_NOTES.md ] || [ ! -f dist/release/SHA256SUMS ]; then
  printf 'Release notes or SHA256SUMS are missing.\n' >&2
  exit 1
fi

set -- dist/release/lcovmerge-*.tar.gz dist/release/lcovmerge-*.zip dist/release/SHA256SUMS dist/release/sbom.cdx.json
for asset in "$@"; do
  if [ ! -f "$asset" ]; then
    printf 'Release asset is missing: %s\n' "$asset" >&2
    exit 1
  fi
done
if [ -f dist/release/SHA256SUMS.sigstore.json ]; then
  set -- "$@" dist/release/SHA256SUMS.sigstore.json
fi

if gh release view "$tag" >/dev/null 2>&1; then
  gh release upload "$tag" "$@" --clobber
  gh release edit "$tag" --title "$tag" --notes-file dist/release/RELEASE_NOTES.md
else
  gh release create "$tag" "$@" --verify-tag --title "$tag" --notes-file dist/release/RELEASE_NOTES.md
fi
