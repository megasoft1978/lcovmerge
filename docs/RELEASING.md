# Release checklist

Use this checklist for a lcovmerge release. Complete a step only after its output is reviewed.

The current CLI and library are Rust. v1.1.0 release files, the matching source history, and all benchmark reports currently in `data/benchmarks.json` are from the former C implementation. Do not reuse those figures as Rust performance evidence.

## Rust cross-build requirements

`scripts/build-all.sh` builds these Rust targets with the optimized Cargo profile and keeps the established archive names:

| Release asset | Rust target |
| --- | --- |
| Linux x86-64 | `x86_64-unknown-linux-musl` |
| Linux aarch64 | `aarch64-unknown-linux-musl` |
| macOS arm64 | `aarch64-apple-darwin` |
| macOS x86-64 | `x86_64-apple-darwin` |
| Windows x86-64 | `x86_64-pc-windows-msvc` |

Install stable Rust through rustup. For Linux/macOS cross builds, install `cargo-zigbuild` with `cargo install cargo-zigbuild --locked` and install Zig. macOS cross-linking needs an Apple SDK; use a macOS host or configure an SDK on Linux. Release CI builds each target on a matching native runner, with static CRT linking for the Windows MSVC executable. On Unix, supply `WINDOWS_BINARY` from that Windows build when assembling all five local release targets. Set `BUILD_TARGETS` to select only Linux/macOS targets.

The Homebrew formula installs a macOS binary from the published release archive. The checked-in formula still points to v1.1.0, whose archive predates the Rust port; regenerate its URLs and checksums after publishing the first Rust release. Native optimized builds use `python3 scripts/build-optimized.py --output bin/lcovmerge` (also invoked by Make), with Python 3.11+ and Rust `llvm-tools`. Synthetic PGO inputs, feedback, build logs, and binary hashes remain in `.cache/optimized/`; unchanged sources reuse the profile. Plain `cargo build --release` builds without PGO. Cross builds do not train PGO because target binaries must run on a matching host. Ordinary development and tests do not need Zig or cross-target components.

## Before tagging

- [ ] Confirm the release version in the binary, README, man page, changelog, and artifact names.
- [ ] Review all CLI behavior against `man/lcovmerge.1` and `docs/USAGE.md`.
- [ ] Confirm the documented limits, especially MC/DC, supported job count, path/line sizes, checksum
behavior, and memory accounting.
- [ ] Refresh benchmark results only from a recorded run. Preserve historical `data/benchmarks.json`;
  native Rust and LCOV-profile measurements use separate JSON reports with their exact build provenance.
  Keep documentation tables consistent with their source reports; `python3 tools/render_benchmarks.py`
  regenerates the historical tables without changing the underlying measurements.
- [ ] Review benchmark caveats and ensure no values outside the JSON were copied into documentation without a
`<!-- BENCH -->` marker.
- [ ] Run parser tests, sanitizer/fuzz checks, and the documented regression suite in the code repository.
- [ ] Verify Rust runtime CI on Linux x86-64, Linux aarch64, macOS arm64, macOS x86-64, and Windows x86-64;
  confirm the release-target size budget job and the Windows release-binary job pass on the final commit.
- [ ] Run the Action smoke workflow on Linux, macOS, and Windows; confirm it verifies the currently
  published download and checks byte-for-byte output against the CLI. Rerun against the new version after publication.
- [ ] Run `actionlint` and confirm the action references remain SHA-pinned and match `docs/validation/action-pins.txt`.
- [ ] Verify Linux binaries are static musl builds and macOS/Windows artifacts match their target
architecture.
- [ ] Confirm the release binary size meets the 2 MiB ceiling enforced by `scripts/check-binary-size.sh` and that archives contain only intended
files.
- [ ] Check the release assets' names and regenerate `SHA256SUMS` from the final archives.
- [ ] Generate `RELEASE_NOTES.md` with `scripts/package-release.sh VERSION`; inspect the three-line summary, highlights, install examples, checksum and provenance commands, and changelog section.
- [ ] Verify the checksum manifest and Cosign signature locally before creating the GitHub release.
- [ ] Review license notices and dependency inventory.

## Publish

- [ ] Merge the release-ready change and create the version tag.
- [ ] Publish `.tar.gz` archives for Linux/macOS and a `.zip` archive for Windows using the documented names.
- [ ] Publish `SHA256SUMS` beside the archives.
- [ ] Confirm GitHub build attestations verify for each uploaded archive, `SHA256SUMS`, and the SBOM.
- [ ] Confirm the GitHub release page, links, and download checksums from a clean environment.
- [ ] Confirm private GitHub Security Advisories are enabled and the SECURITY.md reporting path works.
- [ ] Update release notes with only verified changes and tested platforms.

## After publishing

- [ ] Download and verify each archive from the release page.
- [ ] Run the Action smoke workflow against the published version on Linux, macOS, and Windows.
- [ ] Verify one archive with `gh attestation verify` and verify `SHA256SUMS` with the published `SHA256SUMS.sigstore.json` bundle.
- [ ] Run `lcovmerge --version` and a representative merge on each supported runtime platform.
- [ ] Confirm README download commands and man-page installation instructions.
- [ ] Regenerate the Homebrew formula and Scoop manifest from the published release checksums, then run the
  Scoop smoke workflow for that release. See [Scoop packaging](../packaging/scoop/README.md).
- [ ] Record any known issues and open follow-up tasks for unsupported features.

## Published archive validation

After publication, dispatch the five-platform validation workflow with the
published version, without its leading `v`:

```sh
gh workflow run published-release.yml --ref main -f version=1.2.0
```

It checks out the release tag, downloads and verifies each platform's archive,
runs the CLI and compatibility operational suites against that executable, and
verifies archive and checksum-manifest attestations. The Linux x86-64 job also
verifies the published checksum signature and SBOM attestation. Review all five
jobs and their retained reports before completing the release.

## Scoop release validation

`packaging/scoop/lcovmerge.json` is the manifest template. Download the release's `SHA256SUMS` into a
release-specific directory under `.cache/`, then run `scripts/update-packaging.sh CHECKSUMS VERSION` from a
clean checkout. The script renders both package manifests in place. Review the generated Windows URL and hash
before the package update is published.

The release workflow can open a package update pull request to `megasoft1978/homebrew-tap` when `TAP_TOKEN` is
configured; that repository contains both the Homebrew formula and Scoop bucket manifest. After the manifest is
updated, run the `Scoop smoke` workflow from GitHub Actions and enter the published version without its leading
`v`. Pull requests touching `packaging/scoop/**` or `scripts/update-packaging.sh` also run the smoke workflow
against v1.1.0 by default.

The smoke workflow downloads the official Scoop installer at commit
`1e2f334083d609986d8c8bc9e31ae8e87c39fab4` and verifies SHA-256
`94f983b190438311e006b957db7c8422709e0ba62a6c2ac04e278164108f2512` before running it. It checks out Scoop
core at the pinned v0.6.0 commit `e6aa3b366bdee8ed138c1e0f7b85192ebdd35d0f`, verifies the release ZIP against
the published `SHA256SUMS`, exercises Scoop's `checkver` and forced autoupdate on the generated manifest, and
installs the manifest from a temporary local bucket. The CI trust boundary is the pinned
[ScoopInstaller/Install](https://github.com/ScoopInstaller/Install) bootstrap plus the pinned
[ScoopInstaller/Scoop](https://github.com/ScoopInstaller/Scoop) core; the lcovmerge ZIP is independently
verified against the release checksum file.

For the exact release-note install and provenance commands, see the generated `RELEASE_NOTES.md`. Repository-level branch protection, Dependabot, Discussions, homepage, social-preview, CODEOWNERS, and `TAP_TOKEN` settings are documented in [REPO-SETTINGS.md](REPO-SETTINGS.md).

For local Linux/macOS cross builds, set `BUILD_TARGETS` to the desired Rust target triples. The default five-target build requires `WINDOWS_BINARY` from native Windows CI when run on Unix. Cross builds compare two clean compilations at the same canonical build path before packaging; `scripts/reproducible-build.sh` compares two clean host PGO compilations using the same frozen training feedback. `cargo-zigbuild` is used only for Linux/macOS cross linking.
