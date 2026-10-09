# Repository settings checklist

This checklist is for an owner with admin access to `megasoft1978/lcovmerge`. The commands below change GitHub settings; review each command and run it yourself only when ready. They have not been run as part of this repository change.

First confirm the target and your authentication:

```sh
gh auth status
gh api repos/megasoft1978/lcovmerge --jq '{default_branch: .default_branch, homepage: .homepage, discussions: .has_discussions}'
```

## Protect `main`

Create or update branch protection for `main`. Require pull requests, at least one approval when another reviewer is available, dismissal of stale approvals, up-to-date branches, administrator enforcement, and resolved review conversations. Block force pushes and branch deletion. If the repository has only one reviewer, set `required_approving_review_count` to `0` until an independent reviewer is available; checks and pull requests remain required.

The status check contexts below are the job names in `.github/workflows/ci.yml`, `.github/workflows/codeql.yml`, and `.github/workflows/action-smoke.yml`; matrix jobs include their expanded target or runner. Do not require the release or Pages jobs: they do not run for every pull request. GitHub Actions status checks use the job name as the check name. After a successful run, confirm the names GitHub reports:

```sh
gh api repos/megasoft1978/lcovmerge/commits/main/check-runs --paginate --jq '.check_runs[].name' | sort -u
```

Then apply the protection payload, adjusting the approval count if there is no independent reviewer:

```sh
gh api --method PUT repos/megasoft1978/lcovmerge/branches/main/protection --input - <<'JSON'
{
  "required_status_checks": {
    "strict": true,
    "contexts": [
      "Build and test (linux-x86_64)",
      "Build and test (linux-aarch64)",
      "Build and test (macos-arm64)",
      "Build and test (macos-x86_64)",
      "Build and test (windows-x86_64)",
      "ASan and UBSan",
      "Five-minute fuzz smoke",
      "Differential check against lcov",
      "Reproducible build",
      "ShellCheck",
      "Analyze C and C++",
      "Smoke test (ubuntu-24.04)",
      "Smoke test (macos-latest)",
      "Smoke test (windows-latest)"
    ]
  },
  "enforce_admins": true,
  "required_pull_request_reviews": {
    "dismiss_stale_reviews": true,
    "require_code_owner_reviews": false,
    "required_approving_review_count": 1,
    "require_last_push_approval": false
  },
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "required_conversation_resolution": true
}
JSON
```

This is a full branch-protection update, so it replaces the current protection configuration for `main`. Review existing settings first and preserve any repository-specific restrictions you need. The REST API requires repository Administration write permission. See [GitHub's branch-protection API](https://docs.github.com/en/rest/branches/branch-protection), [required-check troubleshooting](https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/troubleshooting-required-status-checks), and [protected-branch guidance](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches).

Read back the configured contexts and review settings:

```sh
gh api repos/megasoft1978/lcovmerge/branches/main/protection --jq '{checks: .required_status_checks.contexts, reviews: .required_pull_request_reviews, admins: .enforce_admins.enabled, conversations: .required_conversation_resolution.enabled}'
```

## Dependabot security updates

The repository already has a weekly GitHub Actions entry in `.github/dependabot.yml`; security updates are a separate repository setting. Enable vulnerability alerts first if GitHub reports that they are disabled, then enable automated security fixes:

```sh
gh api --method PUT repos/megasoft1978/lcovmerge/vulnerability-alerts
gh api --method PUT repos/megasoft1978/lcovmerge/automated-security-fixes
```

Confirm the security-updates setting:

```sh
gh api repos/megasoft1978/lcovmerge/automated-security-fixes --jq .enabled
```

See [GitHub's repository API](https://docs.github.com/en/rest/repos/repos#enable-dependabot-security-updates).

## Secret scanning push protection

The owner reports that secret scanning push protection is already enabled. Verify both settings before changing anything:

```sh
gh api repos/megasoft1978/lcovmerge --jq '{secret_scanning: .security_and_analysis.secret_scanning.status, push_protection: .security_and_analysis.secret_scanning_push_protection.status}'
```

Both values should be `enabled`. If either is disabled and your plan supports it, enable secret scanning before push protection in **Settings → Code security and analysis**. The current setting is also visible from the repository Settings page; public repositories may not expose every security field to every token.

The equivalent `gh` commands, for a repair only, are:

```sh
gh repo edit megasoft1978/lcovmerge --enable-secret-scanning
gh repo edit megasoft1978/lcovmerge --enable-secret-scanning-push-protection
```

## Discussions and homepage

Enable Discussions and point the repository homepage at the published project site:

```sh
gh repo edit megasoft1978/lcovmerge --enable-discussions
gh repo edit megasoft1978/lcovmerge --homepage https://megasoft1978.github.io/lcovmerge/
```

The URL is the canonical URL used by `docs/site/index.html` and `docs/site/sitemap.xml`. Check the **Discussions** tab after enabling it and create categories that fit project support, such as Q&A and Ideas.

These flags are documented in [`gh repo edit`](https://cli.github.com/manual/gh_repo_edit). Use a current GitHub CLI release if an older local `gh` does not recognize them.

## Social preview

Upload the existing `docs/site/og-image.png` (currently 1200 × 630 pixels):

1. Open `megasoft1978/lcovmerge` on GitHub and select **Settings → General**.
2. Find **Social preview** and choose **Edit**.
3. Upload `docs/site/og-image.png` from this checkout and save the change.
4. Reopen the preview to confirm the image and crop look correct.

The website already references this image as its Open Graph preview. The repository social preview is a separate setting and must be uploaded through the GitHub UI.

## CODEOWNERS suggestion

Add `.github/CODEOWNERS` with the active maintainers who can review the files. For a single owner, a minimal starting point is:

```text
* @megasoft1978
```

Replace that account or add other maintainers before enabling **Require review from Code Owners**. A pull request author cannot approve their own change, so a solo-owner rule cannot supply an independent approval. GitHub reads CODEOWNERS from the repository root, `.github/`, or `docs/`; `.github/CODEOWNERS` is the suggested location.

## `TAP_TOKEN` for package update pull requests

The release workflow reads the optional `TAP_TOKEN` repository secret when opening manifest-update pull requests in `megasoft1978/homebrew-tap`.

1. Open **Settings → Developer settings → Personal access tokens → Fine-grained tokens** and create a token.
2. Set the resource owner to `megasoft1978` and select **Only select repositories → homebrew-tap**.
3. Grant repository permissions **Contents: Read and write** and **Pull requests: Read and write**. Keep the token's expiration short enough for the owner to rotate it.
4. In `megasoft1978/lcovmerge`, open **Settings → Secrets and variables → Actions → Repository secrets** and create `TAP_TOKEN` with the token value.

The release workflow skips package-update PR creation when the secret is absent. It does not need a broad personal access token or write access to the lcovmerge repository.
