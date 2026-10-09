#!/bin/sh
set -eu

first_build=$(mktemp "${TMPDIR:-/tmp}/lcovmerge-first.XXXXXX")
trap 'rm -f "$first_build"' EXIT HUP INT TERM

export SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-0}"
make
binary=$(scripts/find-lcovmerge.sh)
cp "$binary" "$first_build"
rm -f "$binary"

make
second_binary=$(scripts/find-lcovmerge.sh)
if ! cmp -s "$first_build" "$second_binary"; then
  printf 'Reproducibility check failed: the two builds differ.\n' >&2
  exit 1
fi
printf 'Reproducibility check passed for %s.\n' "$second_binary"
