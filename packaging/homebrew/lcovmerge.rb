class Lcovmerge < Formula
  desc "Streaming LCOV merger; macOS binaries use the system libSystem library"
  homepage "https://github.com/megasoft1978/lcovmerge"
  version "1.1.0"
  license "MIT"

  on_arm do
    url "https://github.com/megasoft1978/lcovmerge/releases/download/v1.1.0/lcovmerge-1.1.0-macos-arm64.tar.gz"
    sha256 "e0eacc814e4c2b5de97b6191306937726c289f98e3752e917e488a6477ab7b28"
  end

  on_intel do
    url "https://github.com/megasoft1978/lcovmerge/releases/download/v1.1.0/lcovmerge-1.1.0-macos-x86_64.tar.gz"
    sha256 "5a45573df9ee6b9f51a3ffe5b64b261d6efeae48710416e79416c18e08d4d646"
  end

  def install
    bin.install "lcovmerge"
  end

  test do
    assert_match version.to_s, shell_output("#{bin}/lcovmerge --version")
  end
end
