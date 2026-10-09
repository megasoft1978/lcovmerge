#!/usr/bin/env python3
"""Download a verified release binary, expand input globs, and run the merge."""

from __future__ import annotations

import glob
import hashlib
import os
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path


REPOSITORY = "megasoft1978/lcovmerge"


def release_coordinates() -> tuple[str, str, str]:
    version = os.environ.get("INPUT_VERSION", "1.0.0").strip().removeprefix("v")
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.+-"
    if not version or any(character not in allowed for character in version):
        raise SystemExit("Invalid version input")

    runner_os = os.environ.get("RUNNER_OS", "")
    runner_arch = os.environ.get("RUNNER_ARCH", "")
    if runner_os == "Linux":
        target_os = "linux"
        target_arch = {"X64": "x86_64", "ARM64": "aarch64"}.get(runner_arch)
    elif runner_os == "macOS":
        target_os = "macos"
        target_arch = {"X64": "x86_64", "ARM64": "arm64"}.get(runner_arch)
    elif runner_os == "Windows":
        target_os = "windows"
        target_arch = {"X64": "x86_64"}.get(runner_arch)
    else:
        target_os = ""
        target_arch = None
    if target_arch is None:
        raise SystemExit(f"Unsupported runner: {runner_os} {runner_arch}")

    extension = ".zip" if target_os == "windows" else ".tar.gz"
    asset = f"lcovmerge-{version}-{target_os}-{target_arch}{extension}"
    base_url = os.environ.get(
        "LCOVMERGE_BASE_URL",
        f"https://github.com/{REPOSITORY}/releases/download/v{version}",
    ).rstrip("/")
    return version, asset, base_url


def download(url: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "lcovmerge-github-action"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response, destination.open("wb") as output:
            shutil.copyfileobj(response, output)
    except Exception as exc:
        raise SystemExit(f"Could not download {url}: {exc}") from exc


def verify_archive(asset: str, archive: Path, sums_file: Path) -> None:
    expected: str | None = None
    for line in sums_file.read_text(encoding="ascii").splitlines():
        fields = line.split(maxsplit=1)
        if len(fields) != 2:
            continue
        listed_name = fields[1].lstrip("*")
        if listed_name == asset:
            expected = fields[0].lower()
            break
    if expected is None or len(expected) != 64 or any(character not in "0123456789abcdef" for character in expected):
        raise SystemExit(f"SHA256SUMS has no valid hash for {asset}")
    actual = hashlib.sha256(archive.read_bytes()).hexdigest()
    if actual != expected:
        raise SystemExit(f"SHA-256 verification failed for {asset}")


def extract_binary(asset: str, archive_path: Path, destination: Path) -> Path:
    executable = "lcovmerge.exe" if asset.endswith(".zip") else "lcovmerge"
    if asset.endswith(".zip"):
        with zipfile.ZipFile(archive_path) as archive:
            try:
                info = archive.getinfo(executable)
            except KeyError as exc:
                raise SystemExit(f"Release archive is missing {executable}") from exc
            destination.write_bytes(archive.read(info))
    else:
        with tarfile.open(archive_path, "r:gz") as archive:
            try:
                member = archive.getmember(executable)
            except KeyError as exc:
                raise SystemExit(f"Release archive is missing {executable}") from exc
            if not member.isfile():
                raise SystemExit(f"Release archive entry {executable} is not a regular file")
            stream = archive.extractfile(member)
            if stream is None:
                raise SystemExit(f"Could not read {executable} from release archive")
            destination.write_bytes(stream.read())
    if os.name != "nt":
        destination.chmod(0o755)
    return destination


def expand_inputs(workspace: Path) -> list[Path]:
    patterns = [line.strip() for line in os.environ.get("INPUT_FILES", "").splitlines() if line.strip()]
    matches: list[Path] = []
    seen: set[str] = set()
    for pattern in patterns:
        expression = str(Path(pattern) if Path(pattern).is_absolute() else workspace / pattern)
        found = sorted(glob.glob(expression, recursive=True))
        if not found and Path(expression).is_file():
            found = [expression]
        for item in found:
            path = Path(item)
            if not path.is_file():
                continue
            normalized = os.path.normcase(os.path.abspath(path))
            if normalized not in seen:
                seen.add(normalized)
                matches.append(path)
    if not matches:
        raise SystemExit("The files input did not match any files")
    return matches


def set_output(name: str, value: str) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return
    delimiter = "lcovmerge_" + os.urandom(12).hex()
    with open(output_path, "a", encoding="utf-8", newline="\n") as output:
        output.write(f"{name}<<{delimiter}\n{value}\n{delimiter}\n")


def main() -> int:
    version, asset, base_url = release_coordinates()
    temp_root = Path(tempfile.mkdtemp(prefix="lcovmerge-action-"))
    try:
        archive_path = temp_root / asset
        sums_path = temp_root / "SHA256SUMS"
        download(f"{base_url}/{asset}", archive_path)
        download(f"{base_url}/SHA256SUMS", sums_path)
        verify_archive(asset, archive_path, sums_path)
        binary_name = "lcovmerge.exe" if asset.endswith(".zip") else "lcovmerge"
        binary = extract_binary(asset, archive_path, temp_root / binary_name)

        workspace = Path(os.environ.get("GITHUB_WORKSPACE", os.getcwd())).resolve()
        inputs = expand_inputs(workspace)
        output_text = os.environ.get("INPUT_OUTPUT", "coverage-merged.info").strip()
        if not output_text or output_text == "-":
            raise SystemExit("The output input must be a file path")
        output_path = Path(output_text)
        if not output_path.is_absolute():
            output_path = workspace / output_path
        output_path.parent.mkdir(parents=True, exist_ok=True)

        command = [str(binary)]
        extra = shlex.split(os.environ.get("INPUT_EXTRA_ARGS", ""), posix=True)
        command.extend(extra)
        mem_limit = os.environ.get("INPUT_MEM_LIMIT", "").strip()
        if mem_limit:
            command.extend(["--mem-limit", mem_limit])
        command.extend(str(path) for path in inputs)
        command.extend(["--output", str(output_path)])
        if "--stats" not in extra:
            command.append("--stats")

        result = subprocess.run(command, cwd=workspace, capture_output=True, text=True, check=False)
        if result.stdout:
            print(result.stdout, end="")
        if result.stderr:
            print(result.stderr, end="", file=sys.stderr)
        if result.returncode != 0:
            raise SystemExit(result.returncode)

        summary = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part.strip())
        if not summary:
            summary = f"Merged {len(inputs)} input file(s) into {output_path} with lcovmerge {version}."
        set_output("summary", summary)
        step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if step_summary:
            with open(step_summary, "a", encoding="utf-8", newline="\n") as output:
                output.write("### lcovmerge\n\n" + summary + "\n")
        return 0
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
