class Lcovmerge < Formula
  desc "Streaming LCOV merger; macOS binaries use the system libSystem library"
  homepage "https://github.com/megasoft1978/lcovmerge"
  version "@VERSION@"
  license "MIT"

  on_arm do
    url "https://github.com/megasoft1978/lcovmerge/releases/download/v@VERSION@/lcovmerge-@VERSION@-macos-arm64.tar.gz"
    sha256 "@SHA256_MACOS_ARM64@"
  end

  on_intel do
    url "https://github.com/megasoft1978/lcovmerge/releases/download/v@VERSION@/lcovmerge-@VERSION@-macos-x86_64.tar.gz"
    sha256 "@SHA256_MACOS_X86_64@"
  end

  def install
    bin.install "lcovmerge"
  end

  test do
    assert_match version.to_s, shell_output("#{bin}/lcovmerge --version")
  end
end
