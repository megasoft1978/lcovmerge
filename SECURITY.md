# Security Policy

## Scope

The security scope is parser memory safety in lcovmerge: malformed or adversarial LCOV input that can cause
out-of-bounds access, use-after-free, integer overflow leading to unsafe memory access, or another
memory-safety failure in the parser or merge path.

This policy does not promise security review of downstream report tools, CI systems, build instrumentation, or
arbitrary temporary-directory contents.

## Reporting a vulnerability

Please report suspected vulnerabilities privately through [GitHub Security
Advisories](https://github.com/megasoft1978/lcovmerge/security/advisories/new). Include a minimized input,
affected version or commit, platform, and a reproduction command when possible. Do not open a public issue for
an unpatched parser memory-safety problem.

The repository is planned for public creation. If private advisories are not enabled when you need to report
an issue, use GitHub's private vulnerability reporting mechanism on the repository page or contact the
maintainers through the private channel listed there. Do not attach sensitive material to a public issue.

The project will acknowledge receipt and coordinate disclosure after a fix is available. No response-time
guarantee is made.
