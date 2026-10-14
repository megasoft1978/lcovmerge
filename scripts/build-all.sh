#!/bin/sh
set -eu
root=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
cd "$root"
version=$(sed -n 's/^version = "\([^"]*\)"/\1/p' Cargo.toml | head -n 1)
[ -n "$version" ] || { echo 'Cannot determine Cargo package version.' >&2; exit 1; }
host=$(rustc -vV | sed -n 's/^host: //p')
targets=${BUILD_TARGETS:-'x86_64-unknown-linux-musl aarch64-unknown-linux-musl aarch64-apple-darwin x86_64-apple-darwin x86_64-pc-windows-msvc'}
# Windows release binaries are built natively in CI. Cross builds can supply that artifact.
for target in $targets; do
    case "$target" in
        *windows*)
            case "$host" in *windows*) ;; *)
                [ -n "${WINDOWS_BINARY:-}" ] && [ -f "$WINDOWS_BINARY" ] || {
                    echo 'Supply WINDOWS_BINARY from native Windows CI, or select Linux/macOS with BUILD_TARGETS.' >&2
                    exit 1
                }
            ;; esac
        ;;
    esac
done
export ZERO_AR_DATE=1
export SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-$(git log -1 --format=%ct HEAD 2>/dev/null || printf 0)}"
export LCOVMERGE_GIT_COMMIT="${LCOVMERGE_GIT_COMMIT:-$(git rev-parse --short=12 HEAD 2>/dev/null || printf unknown)}"
dist=${DIST_DIR:-dist}
case "$dist" in /*) ;; *) dist="$root/$dist" ;; esac
mkdir -p .cache
work=$(mktemp -d "$root/.cache/dist-build.XXXXXX")
trap 'rm -rf "$work"' EXIT HUP INT TERM
export CARGO_ENCODED_RUSTFLAGS="$(python3 - "$root" "$work" <<'PYFLAGS'
import os, sys
flags = os.environ.get("CARGO_ENCODED_RUSTFLAGS")
flags = flags.split("\x1f") if flags else os.environ.get("RUSTFLAGS", "").split()
flags.extend([f"--remap-path-prefix={sys.argv[1]}=.", f"--remap-path-prefix={sys.argv[2]}=build"])
print("\x1f".join(flags), end="")
PYFLAGS
)"
python3 tools/package-dist.py --clean "$dist"
for target in $targets; do
    executable=lcovmerge
    prebuilt=
    case "$target" in
        x86_64-unknown-linux-musl) suffix=linux-x86_64 ;;
        aarch64-unknown-linux-musl) suffix=linux-aarch64 ;;
        aarch64-apple-darwin) suffix=macos-arm64 ;;
        x86_64-apple-darwin) suffix=macos-x86_64 ;;
        x86_64-pc-windows-msvc|x86_64-pc-windows-gnu)
            suffix=windows-x86_64.exe; executable=lcovmerge.exe
            case "$host" in *windows*) ;; *) prebuilt=$WINDOWS_BINARY ;; esac
        ;;
        *) printf 'Unsupported release target: %s\n' "$target" >&2; exit 1 ;;
    esac
    if [ -n "$prebuilt" ]; then
        cp "$prebuilt" "$dist/lcovmerge-$version-$suffix"
    else
        rustup target add "$target"
        for build in first second; do
            rm -rf "$work/target"
            native=0
            [ "$target" != "$host" ] || native=1
            case "$host:$target" in *apple-darwin:*apple-darwin) native=1 ;; esac
            if [ "$native" -eq 1 ]; then
                cargo build --locked --release --target "$target" --target-dir "$work/target"
            else
                cargo zigbuild --locked --release --target "$target" --target-dir "$work/target"
            fi
            cp "$work/target/$target/release/$executable" "$work/binary-$build"
        done
        cmp "$work/binary-first" "$work/binary-second"
        cp "$work/binary-first" "$dist/lcovmerge-$version-$suffix"
    fi
    scripts/check-binary-size.sh "$dist/lcovmerge-$version-$suffix"
done
# The archive tool accepts the complete five-target set; a selected-target build retains binaries.
count=$(printf '%s\n' "$targets" | wc -w | tr -d '[:space:]')
if [ "$count" -eq 5 ]; then
    python3 tools/package-dist.py --archive "$dist" --version "$version" --epoch "$SOURCE_DATE_EPOCH"
    (cd "$dist" && if command -v sha256sum >/dev/null 2>&1; then sha256sum -c SHA256SUMS; else shasum -a 256 -c SHA256SUMS; fi)
fi
printf 'Built and independently compared Rust release targets: %s\n' "$targets"
