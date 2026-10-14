#!/bin/sh
set -eu
root=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
cd "$root"
mkdir -p .cache
work=$(mktemp -d "$root/.cache/reproducible.XXXXXX")
trap 'rm -rf "$work"' EXIT HUP INT TERM
export ZERO_AR_DATE=1
export SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-0}"
export LCOVMERGE_GIT_COMMIT="${LCOVMERGE_GIT_COMMIT:-$(git rev-parse --short=12 HEAD 2>/dev/null || printf unknown)}"
export CARGO_ENCODED_RUSTFLAGS="$(python3 - "$root" "$work" <<'PYFLAGS'
import os, sys
flags = os.environ.get("CARGO_ENCODED_RUSTFLAGS")
flags = flags.split("\x1f") if flags else os.environ.get("RUSTFLAGS", "").split()
flags.extend([f"--remap-path-prefix={sys.argv[1]}=.", f"--remap-path-prefix={sys.argv[2]}=build"])
print("\x1f".join(flags), end="")
PYFLAGS
)"
case "$(rustc -vV | sed -n 's/^host: //p')" in *windows-msvc) CARGO_ENCODED_RUSTFLAGS="$CARGO_ENCODED_RUSTFLAGS$(printf '\037')-C$(printf '\037')target-feature=+crt-static"; export CARGO_ENCODED_RUSTFLAGS ;; esac
for build in first second; do
    if [ "$build" = second ]; then
        python3 - "$work/pgo/use" "$work/pgo/first-use" <<'PYMOVE'
from pathlib import Path
import sys
Path(sys.argv[1]).rename(sys.argv[2])
PYMOVE
    fi
    python3 scripts/build-optimized.py --workdir "$work/pgo" --output "$work/lcovmerge-$build"
done
cmp "$work/lcovmerge-first" "$work/lcovmerge-second"
printf 'Reproducibility check passed: two clean Rust PGO release builds using frozen feedback.\n'
