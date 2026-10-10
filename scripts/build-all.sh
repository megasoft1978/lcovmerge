#!/bin/sh
set -eu
root=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
cd "$root"
zig_bin=${ZIG_CC:-zig}
command -v "$zig_bin" >/dev/null 2>&1 || { echo "zig cc is required (install with brew install zig)" >&2; exit 1; }
version=$(sed -n 's/^#define LCOVMERGE_VERSION "\([^"]*\)"/\1/p' include/version.h)
commit=$(git rev-parse --short=12 HEAD 2>/dev/null || printf unknown)
epoch=${SOURCE_DATE_EPOCH:-$(git log -1 --format=%ct HEAD 2>/dev/null || printf 0)}
export SOURCE_DATE_EPOCH="$epoch"
dist="$root/dist"
if [ -n "${DIST_DIR:-}" ]; then
    case "$DIST_DIR" in
        /*) dist=$DIST_DIR ;;
        *) dist="$root/$DIST_DIR" ;;
    esac
fi
work=$(mktemp -d "${TMPDIR:-/tmp}/lcovmerge-dist.XXXXXX")
cleanup() { python3 -c 'import shutil,sys; shutil.rmtree(sys.argv[1], ignore_errors=True)' "$work"; }
trap cleanup EXIT HUP INT TERM

build_set() {
    python3 tools/package-dist.py --clean "$dist"
    # Keep the flags as positional arguments so paths containing spaces remain one argument.
    set --
    for flag in -Iinclude -O2 -std=c11 -Wall -Wextra -Wpedantic -Wconversion -Wshadow -Wstrict-prototypes -Werror -fstack-protector-strong -D_FORTIFY_SOURCE=2 -ffunction-sections -fdata-sections "-ffile-prefix-map=$root=." "-DLCOVMERGE_GIT_COMMIT=\"$commit\""; do
        set -- "$@" "$flag"
    done

    "$zig_bin" cc -target x86_64-linux-musl "$@" -static src/lcovmerge.c src/platform_posix.c src/platform_entry.c -pthread -Wl,--gc-sections -Wl,-s -o "$dist/lcovmerge-$version-linux-x86_64"
    "$zig_bin" cc -target aarch64-linux-musl "$@" -static src/lcovmerge.c src/platform_posix.c src/platform_entry.c -pthread -Wl,--gc-sections -Wl,-s -o "$dist/lcovmerge-$version-linux-aarch64"
    "$zig_bin" cc -target aarch64-macos "$@" src/lcovmerge.c src/platform_posix.c src/platform_entry.c -pthread -Wl,-dead_strip -Wl,-x -o "$dist/lcovmerge-$version-macos-arm64"
    "$zig_bin" cc -target x86_64-macos "$@" src/lcovmerge.c src/platform_posix.c src/platform_entry.c -pthread -Wl,-dead_strip -Wl,-x -o "$dist/lcovmerge-$version-macos-x86_64"
    if [ -n "${MINGW_CC:-}" ]; then
        command -v "$MINGW_CC" >/dev/null 2>&1 || {
            printf 'MINGW_CC not found: %s\n' "$MINGW_CC" >&2
            exit 1
        }
        "$MINGW_CC" "$@" -static src/lcovmerge.c src/platform_win32.c -municode \
            -Wl,--gc-sections -Wl,-s -o "$dist/lcovmerge-$version-windows-x86_64.exe"
    else
        "$zig_bin" cc -target x86_64-windows-gnu "$@" -static src/lcovmerge.c src/platform_win32.c -municode -Wl,--gc-sections -Wl,-s -o "$dist/lcovmerge-$version-windows-x86_64.exe"
    fi
    python3 tools/package-dist.py --archive "$dist" --version "$version" --epoch "$epoch"
}

mkdir -p "$dist"
build_set
cp "$dist/SHA256SUMS" "$work/SHA256SUMS.first"
build_set
scripts/check-binary-size.sh \
    "$dist/lcovmerge-$version-linux-x86_64" \
    "$dist/lcovmerge-$version-linux-aarch64" \
    "$dist/lcovmerge-$version-macos-arm64" \
    "$dist/lcovmerge-$version-macos-x86_64" \
    "$dist/lcovmerge-$version-windows-x86_64.exe"
cmp "$work/SHA256SUMS.first" "$dist/SHA256SUMS"
(cd "$dist" && if command -v sha256sum >/dev/null 2>&1; then sha256sum -c SHA256SUMS; else shasum -a 256 -c SHA256SUMS; fi)
if command -v file >/dev/null 2>&1; then file "$dist"/lcovmerge-"$version"-*; fi
python3 - "$dist/lcovmerge-$version-windows-x86_64.exe" <<'PY'
import pathlib
import struct
import sys
image = pathlib.Path(sys.argv[1]).read_bytes()
assert image[:2] == b"MZ", "missing DOS/PE header"
pe_offset = struct.unpack_from("<I", image, 0x3c)[0]
assert image[pe_offset:pe_offset + 4] == b"PE\0\0", "missing PE signature"
machine = struct.unpack_from("<H", image, pe_offset + 4)[0]
assert machine == 0x8664, f"unexpected PE machine 0x{machine:04x}"
print("windows_pe=x86_64 PASS")
PY
printf 'reproducible=PASS source_date_epoch=%s git=%s version=%s\n' "$epoch" "$commit" "$version"
