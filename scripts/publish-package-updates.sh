#!/bin/sh
set -eu

if [ "$#" -ne 1 ]; then
  printf 'Usage: %s VERSION\n' "$0" >&2
  exit 2
fi
version=${1#v}
if [ -z "${GH_TOKEN:-}" ]; then
  printf 'GH_TOKEN is required to update the tap repository.\n' >&2
  exit 1
fi

repo_root=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
temp_root=$(mktemp -d "${TMPDIR:-/tmp}/lcovmerge-tap.XXXXXX")
trap 'rm -rf "$temp_root"' EXIT HUP INT TERM
tap_dir="$temp_root/homebrew-tap"

gh auth setup-git
gh repo clone megasoft1978/homebrew-tap "$tap_dir"
mkdir -p "$tap_dir/Formula" "$tap_dir/bucket"
cp "$repo_root/packaging/homebrew/lcovmerge.rb" "$tap_dir/Formula/lcovmerge.rb"
cp "$repo_root/packaging/scoop/lcovmerge.json" "$tap_dir/bucket/lcovmerge.json"

git -C "$tap_dir" add Formula/lcovmerge.rb bucket/lcovmerge.json
if git -C "$tap_dir" diff --cached --quiet; then
  printf 'Tap manifests already match release %s.\n' "$version"
  exit 0
fi

branch="lcovmerge-release-$version"
actor=${GITHUB_ACTOR:-github-actions[bot]}
actor_id=${GITHUB_ACTOR_ID:-41898282}
noreply_domain=users.noreply.github.com
git -C "$tap_dir" config user.name "$actor"
git -C "$tap_dir" config user.email "${actor_id}+${actor}@${noreply_domain}"
git -C "$tap_dir" checkout -b "$branch"
git -C "$tap_dir" commit -m "chore: update lcovmerge manifests for v$version"
git -C "$tap_dir" push --set-upstream origin "$branch"

body_file="$temp_root/pull-request.md"
cat > "$body_file" <<EOF
Updates the Homebrew formula and Scoop manifest to lcovmerge v$version.

The checksums come from the release SHA256SUMS file.
EOF
gh pr create \
  --repo megasoft1978/homebrew-tap \
  --head "$branch" \
  --base main \
  --title "Update lcovmerge manifests for v$version" \
  --body-file "$body_file"
