"""Unit tests for the GitHub Action's download and input-handling logic."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import os
import re
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch


ENTRYPOINT_PATH = Path(__file__).parents[1] / "scripts" / "entrypoint.py"
SPEC = importlib.util.spec_from_file_location("lcovmerge_action_entrypoint", ENTRYPOINT_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Could not load {ENTRYPOINT_PATH}")
entrypoint = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(entrypoint)


class ReleaseCoordinatesTests(unittest.TestCase):
    def coordinates(self, runner_os: str, runner_arch: str, version: str = "v1.0.1"):
        with patch.dict(
            os.environ,
            {"INPUT_VERSION": version, "RUNNER_OS": runner_os, "RUNNER_ARCH": runner_arch},
            clear=True,
        ):
            return entrypoint.release_coordinates()

    def test_release_asset_names_match_all_supported_runner_targets(self) -> None:
        expected = {
            ("Linux", "X64"): "lcovmerge-1.0.1-linux-x86_64.tar.gz",
            ("Linux", "ARM64"): "lcovmerge-1.0.1-linux-aarch64.tar.gz",
            ("macOS", "X64"): "lcovmerge-1.0.1-macos-x86_64.tar.gz",
            ("macOS", "ARM64"): "lcovmerge-1.0.1-macos-arm64.tar.gz",
            ("Windows", "X64"): "lcovmerge-1.0.1-windows-x86_64.zip",
        }
        for (runner_os, runner_arch), asset in expected.items():
            with self.subTest(runner_os=runner_os, runner_arch=runner_arch):
                version, actual_asset, base_url = self.coordinates(runner_os, runner_arch)
                self.assertEqual(version, "1.0.1")
                self.assertEqual(actual_asset, asset)
                self.assertEqual(
                    base_url,
                    "https://github.com/megasoft1978/lcovmerge/releases/download/v1.0.1",
                )

    def test_defaults_to_v1_0_1_when_version_input_is_unset(self) -> None:
        with patch.dict(os.environ, {"RUNNER_OS": "Linux", "RUNNER_ARCH": "X64"}, clear=True):
            version, asset, _ = entrypoint.release_coordinates()
        self.assertEqual(version, "1.0.1")
        self.assertEqual(asset, "lcovmerge-1.0.1-linux-x86_64.tar.gz")

    def test_rejects_invalid_version_and_unsupported_runner(self) -> None:
        with self.assertRaises(SystemExit):
            self.coordinates("Linux", "X64", "../1.0.1")
        with self.assertRaises(SystemExit):
            self.coordinates("Windows", "ARM64")


class InputExpansionTests(unittest.TestCase):
    def test_expands_globs_skips_directories_and_deduplicates_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            shards = workspace / "coverage" / "shards"
            shards.mkdir(parents=True)
            (shards / "unit.info").write_text("unit", encoding="utf-8")
            (shards / "integration.info").write_text("integration", encoding="utf-8")
            (shards / "directory.info").mkdir()
            with patch.dict(
                os.environ,
                {
                    "INPUT_FILES": "coverage/shards/unit.info\n"
                    "coverage/shards/*.info\ncoverage/shards"
                },
                clear=True,
            ):
                matches = entrypoint.expand_inputs(workspace)

        self.assertEqual([path.name for path in matches], ["unit.info", "integration.info"])

    def test_rejects_empty_or_unmatched_files_input(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            for files in ("", "missing/*.info"):
                with self.subTest(files=files), patch.dict(
                    os.environ, {"INPUT_FILES": files}, clear=True
                ):
                    with self.assertRaisesRegex(SystemExit, "did not match any files"):
                        entrypoint.expand_inputs(workspace)


class ArchiveVerificationTests(unittest.TestCase):
    def test_verifies_sha256_checksum_and_rejects_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            asset = "lcovmerge-1.0.1-linux-x86_64.tar.gz"
            archive = root / asset
            sums = root / "SHA256SUMS"
            contents = b"synthetic archive fixture"
            archive.write_bytes(contents)
            digest = hashlib.sha256(contents).hexdigest()
            sums.write_text(f"{digest}  *{asset}\n", encoding="ascii")

            entrypoint.verify_archive(asset, archive, sums)

            archive.write_bytes(b"tampered fixture")
            with self.assertRaisesRegex(SystemExit, "SHA-256 verification failed"):
                entrypoint.verify_archive(asset, archive, sums)

    def test_rejects_missing_checksum_entry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "archive.tar.gz"
            sums = root / "SHA256SUMS"
            archive.write_bytes(b"synthetic")
            sums.write_text("0" * 64 + "  other.tar.gz\n", encoding="ascii")
            with self.assertRaisesRegex(SystemExit, "SHA256SUMS has no valid hash"):
                entrypoint.verify_archive("wanted.tar.gz", archive, sums)

    def test_extracts_only_the_expected_binary_from_tar_and_zip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tar_path = root / "binary.tar.gz"
            tar_binary = b"synthetic unix executable"
            with tarfile.open(tar_path, "w:gz") as archive:
                info = tarfile.TarInfo("lcovmerge")
                info.mode = 0o755
                info.size = len(tar_binary)
                archive.addfile(info, io.BytesIO(tar_binary))
            extracted_tar = entrypoint.extract_binary(
                "lcovmerge-1.0.1-linux-x86_64.tar.gz", tar_path, root / "lcovmerge"
            )
            self.assertEqual(extracted_tar.read_bytes(), tar_binary)

            zip_path = root / "binary.zip"
            zip_binary = b"synthetic windows executable"
            with zipfile.ZipFile(zip_path, "w") as archive:
                archive.writestr("lcovmerge.exe", zip_binary)
            extracted_zip = entrypoint.extract_binary(
                "lcovmerge-1.0.1-windows-x86_64.zip",
                zip_path,
                root / "lcovmerge.exe",
            )
            self.assertEqual(extracted_zip.read_bytes(), zip_binary)

    def test_rejects_archives_missing_expected_binary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive_path = root / "binary.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("other.exe", b"not the expected executable")
            with self.assertRaisesRegex(SystemExit, "Release archive is missing lcovmerge.exe"):
                entrypoint.extract_binary(
                    "lcovmerge-1.0.1-windows-x86_64.zip",
                    archive_path,
                    root / "lcovmerge.exe",
                )


class OutputTests(unittest.TestCase):
    def test_writes_named_github_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output_path = Path(temporary) / "GITHUB_OUTPUT"
            with patch.dict(os.environ, {"GITHUB_OUTPUT": str(output_path)}, clear=True):
                entrypoint.set_output("summary", "two synthetic input files merged")
            output = output_path.read_text(encoding="utf-8")

        lines = output.splitlines()
        self.assertTrue(lines[0].startswith("summary<<"))
        delimiter = lines[0].removeprefix("summary<<")
        self.assertRegex(delimiter, re.compile(r"^lcovmerge_[0-9a-f]{24}$"))
        self.assertEqual(lines[1], "two synthetic input files merged")
        self.assertEqual(lines[2], delimiter)


if __name__ == "__main__":
    unittest.main()
