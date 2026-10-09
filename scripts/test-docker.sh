#!/bin/sh
set -eu
root=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
mkdir -p "$root/docs/validation"

for platform in linux/amd64 linux/arm64; do
    tag=$(printf '%s' "$platform" | tr '/' '-')
    log="$root/docs/validation/docker-$tag.txt"
    case "$platform" in
        linux/amd64) static_binary=dist/lcovmerge-1.0.0-linux-x86_64 ;;
        linux/arm64) static_binary=dist/lcovmerge-1.0.0-linux-aarch64 ;;
    esac
    set +e
    docker run --rm --platform "$platform" \
        -v "$root:/workspace" -w /workspace debian:bookworm-slim \
        sh -ec "apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq build-essential python3 make >/dev/null && cc --version | head -1 && make BIN=/tmp/lcovmerge-native test && cp '$static_binary' /tmp/lcovmerge-static && python3 tests/run_tests.py --binary /tmp/lcovmerge-static" \
        >"$log" 2>&1
    status=$?
    set -e
    cat "$log"
    if [ "$status" -ne 0 ]; then
        printf 'docker validation failed: %s (exit %s)\n' "$platform" "$status" >&2
        exit "$status"
    fi
done
