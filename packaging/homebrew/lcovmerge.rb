class Lcovmerge < Formula
  desc "Streaming LCOV merger; macOS binaries use the system libSystem library"
  homepage "https://github.com/megasoft1978/lcovmerge"
  version "1.0.2"
  license "MIT"

  on_arm do
    url "https://github.com/megasoft1978/lcovmerge/releases/download/v1.0.2/lcovmerge-1.0.2-macos-arm64.tar.gz"
    sha256 "a61f47349a3856be0106c9bde37dcbe9e488ecb9aae729abb0ac77c2002ed02e"
  end

  on_intel do
    url "https://github.com/megasoft1978/lcovmerge/releases/download/v1.0.2/lcovmerge-1.0.2-macos-x86_64.tar.gz"
    sha256 "083065541c20867921b57a3c76cf31e90e52752bcce706771d1f7b7ec6a7d935"
  end

  def install
    bin.install "lcovmerge"
  end

  test do
    assert_match version.to_s, shell_output("#{bin}/lcovmerge --version")
  end
end
