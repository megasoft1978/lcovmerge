class Lcovmerge < Formula
  desc "Streaming LCOV merger; macOS binaries use the system libSystem library"
  homepage "https://github.com/megasoft1978/lcovmerge"
  version "1.2.0"
  license "MIT"

  on_arm do
    url "https://github.com/megasoft1978/lcovmerge/releases/download/v1.2.0/lcovmerge-1.2.0-macos-arm64.tar.gz"
    sha256 "54eb1cb67f7761b82e52423404a01a092cd31fdc4f28a922e90ebabe66d3efe4"
  end

  on_intel do
    url "https://github.com/megasoft1978/lcovmerge/releases/download/v1.2.0/lcovmerge-1.2.0-macos-x86_64.tar.gz"
    sha256 "e6f9537364576114f8fe0948fbbec3f9d3ddbfc0da753d186fde861e83cac818"
  end

  def install
    bin.install "lcovmerge"
  end

  test do
    assert_match version.to_s, shell_output("#{bin}/lcovmerge --version")
  end
end
