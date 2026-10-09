# Release checklist

Use this checklist for a lcovmerge release. Complete a step only after its output is reviewed.

## Before tagging

- [ ] Confirm the release version in the binary, README, man page, changelog, and artifact names.
- [ ] Review all CLI behavior against `man/lcovmerge.1` and `docs/USAGE.md`.
- [ ] Confirm the documented limits, especially MC/DC, supported job count, path/line sizes, checksum
behavior, and memory accounting.
- [ ] Refresh benchmark results only from a recorded run; update `data/benchmarks.json` and regenerate both
tables with `python3 tools/render_benchmarks.py`.
- [ ] Review benchmark caveats and ensure no values outside the JSON were copied into documentation without a
`<!-- BENCH -->` marker.
- [ ] Run parser tests, sanitizer/fuzz checks, and the documented regression suite in the code repository.
- [ ] Verify runtime CI on Linux x86-64, Linux aarch64, macOS arm64, macOS x86-64, and Windows x86-64.
- [ ] Verify Linux binaries are static musl builds and macOS/Windows artifacts match their target
architecture.
- [ ] Confirm the release binary size meets the project's stated limit and that archives contain only intended
files.
- [ ] Check the release assets' names and regenerate `SHA256SUMS` from the final archives.
- [ ] Review license notices and dependency inventory.

## Publish

- [ ] Merge the release-ready change and create the version tag.
- [ ] Publish `.tar.gz` archives for Linux/macOS and a `.zip` archive for Windows using the documented names.
- [ ] Publish `SHA256SUMS` beside the archives.
- [ ] Confirm the GitHub release page, links, and download checksums from a clean environment.
- [ ] Confirm private GitHub Security Advisories are enabled and the SECURITY.md reporting path works.
- [ ] Update release notes with only verified changes and tested platforms.

## After publishing

- [ ] Download and verify each archive from the release page.
- [ ] Run `lcovmerge --version` and a representative merge on each supported runtime platform.
- [ ] Confirm README download commands and man-page installation instructions.
- [ ] Update Homebrew, Scoop, Docker, install-script, or GitHub Action instructions only if those distribution
channels actually exist.
- [ ] Record any known issues and open follow-up tasks for unsupported features.
