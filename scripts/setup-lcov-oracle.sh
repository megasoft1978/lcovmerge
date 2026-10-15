#!/bin/sh
# Build the exact reference revision in ignored project-local storage.
set -eu
root=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
oracle=${1:-"$root/.cache/lcov-oracle/source"}
revision=a17443e7dbb31b5b7bff6beac32e7b9b02ec3342
mkdir -p "$(dirname -- "$oracle")"
if [ ! -d "$oracle/.git" ]; then
    git clone --quiet --filter=blob:none --no-checkout https://github.com/linux-test-project/lcov.git "$oracle"
fi
if [ "$(git -C "$oracle" rev-parse HEAD)" != "$revision" ]; then
    git -C "$oracle" checkout --quiet "$revision"
fi
test "$(git -C "$oracle" rev-parse HEAD)" = "$revision"
git -C "$oracle" diff --exit-code "$revision" -- bin lib
mkdir -p "$oracle/.cache/tmp"
TMPDIR="$oracle/.cache/tmp" make -C "$oracle" LCOV_NO_DOC=1 xs_lib
printf '%s\n' "$oracle"
