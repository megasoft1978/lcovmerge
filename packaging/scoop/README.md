# Scoop package

`lcovmerge.json` is the Scoop bucket manifest template. `scripts/update-packaging.sh` fills its version and
Windows ZIP hash from the published release `SHA256SUMS` file. The script also renders the Homebrew formula, so
review both generated files before publishing a package update.

For example, after publishing v1.0.0:

~~~sh
mkdir -p .luna-tmp/scoop-v1.0.0/checksums
curl -fsSL \
  https://github.com/megasoft1978/lcovmerge/releases/download/v1.0.0/SHA256SUMS \
  -o .luna-tmp/scoop-v1.0.0/checksums/SHA256SUMS
TMPDIR=./.luna-tmp scripts/update-packaging.sh \
  .luna-tmp/scoop-v1.0.0/checksums/SHA256SUMS 1.0.0
rm -rf .luna-tmp/scoop-v1.0.0
~~~

The updater writes the rendered manifest to `packaging/scoop/lcovmerge.json`. The release automation can copy
it into the `bucket/lcovmerge.json` path in `megasoft1978/homebrew-tap` and open a package update pull request
when `TAP_TOKEN` is configured.

The `Scoop smoke` GitHub Actions workflow runs on pull requests that change this directory or
`scripts/update-packaging.sh`. It defaults to the published v1.0.0 release; a manual run accepts another
published version. On Windows, it verifies the bootstrap script SHA-256 before running the pinned official
Scoop installer, pins Scoop core to v0.6.0, compares the downloaded lcovmerge ZIP with the release checksum,
checks `checkver` and forced autoupdate behavior, installs from a temporary local bucket, runs the version and
two-file merge smoke checks, then uninstalls the package. Scoop can execute upstream installer code, so the
workflow trusts ScoopInstaller/Install commit `1e2f334083d609986d8c8bc9e31ae8e87c39fab4` only after verifying
its SHA-256 (`94f983b190438311e006b957db7c8422709e0ba62a6c2ac04e278164108f2512`); Scoop core is pinned to
commit `e6aa3b366bdee8ed138c1e0f7b85192ebdd35d0f`. The lcovmerge archive is verified separately against the
release's published `SHA256SUMS`.
