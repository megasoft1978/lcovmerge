#!/usr/bin/env python3
"""Create reproducible per-platform tar.gz and zip packages and checksums."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import pathlib
import shutil
import tarfile
import zipfile


def clean(directory: pathlib.Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for path in directory.iterdir():
        if path.is_file() or path.is_symlink():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)


def packages(directory: pathlib.Path, version: str, epoch: int) -> list[pathlib.Path]:
    binaries = sorted(path for path in directory.glob(f"lcovmerge-{version}-*")
                      if path.is_file() and path.suffix not in (".gz", ".zip"))
    outputs: list[pathlib.Path] = []
    stamp = max(epoch, 315532800)  # ZIP timestamps start at 1980-01-01.
    for binary in binaries:
        for suffix in (".tar.gz", ".zip"):
            package = pathlib.Path(f"{binary}{suffix}")
            if suffix == ".tar.gz":
                with package.open("wb") as raw:
                    with gzip.GzipFile(filename="", mode="wb", fileobj=raw,
                                       compresslevel=9, mtime=epoch) as compressed:
                        with tarfile.open(fileobj=compressed, mode="w", format=tarfile.USTAR_FORMAT) as archive:
                            info = tarfile.TarInfo(binary.name)
                            info.size = binary.stat().st_size
                            info.mode = 0o755
                            info.uid = info.gid = 0
                            info.uname = info.gname = ""
                            info.mtime = epoch
                            with binary.open("rb") as source:
                                archive.addfile(info, source)
            else:
                info = zipfile.ZipInfo(binary.name)
                from datetime import datetime, timezone
                dt = datetime.fromtimestamp(stamp, timezone.utc)
                info.date_time = (dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second - dt.second % 2)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = (0o100755 << 16)
                with zipfile.ZipFile(package, "w", compression=zipfile.ZIP_DEFLATED,
                                     compresslevel=9, strict_timestamps=False) as archive:
                    archive.writestr(info, binary.read_bytes())
            outputs.append(package)

    checksummed = sorted([*binaries, *outputs], key=lambda item: item.name)
    manifest = directory / "SHA256SUMS"
    with manifest.open("w", encoding="ascii", newline="\n") as stream:
        for path in checksummed:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            stream.write(f"{digest}  {path.name}\n")
    return outputs


def main() -> int:
    parser = argparse.ArgumentParser()
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--clean", type=pathlib.Path)
    action.add_argument("--archive", type=pathlib.Path)
    parser.add_argument("--version")
    parser.add_argument("--epoch", type=int, default=0)
    args = parser.parse_args()
    if args.clean:
        clean(args.clean)
        return 0
    if not args.version:
        parser.error("--archive requires --version")
    packages(args.archive, args.version, args.epoch)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
