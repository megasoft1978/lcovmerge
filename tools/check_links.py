#!/usr/bin/env python3
"""Check Markdown links and report external URLs that cannot be reached."""

import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LINK_RE = re.compile(r"!?\[[^\]]*\]\((<[^>]+>|[^\s)]+)(?:\s+[^)]*)?\)")
FENCE_RE = re.compile(r"^\s*(```|~~~)")


def markdown_without_fences(text):
    output = []
    fence = None
    for line in text.splitlines():
        match = FENCE_RE.match(line)
        if match:
            token = match.group(1)[0]
            if fence is None:
                fence = token
            elif fence == token:
                fence = None
            continue
        if fence is None:
            output.append(line)
    return "\n".join(output)


def slug(heading):
    heading = re.sub(r"`([^`]*)`", r"\1", heading).lower()
    heading = re.sub(r"[^\w\- ]", "", heading, flags=re.UNICODE)
    return re.sub(r"\s+", "-", heading.strip())


def anchors(path):
    text = markdown_without_fences(path.read_text(errors="replace"))
    used = {}
    found = set()
    for line in text.splitlines():
        match = re.match(r"^#{1,6}\s+(.+?)\s*#*\s*$", line)
        if not match:
            continue
        base = slug(match.group(1))
        count = used.get(base, 0)
        used[base] = count + 1
        found.add(base if count == 0 else f"{base}-{count}")
    return found


def fetch(url):
    headers = {"User-Agent": "lcovmerge-doc-link-check/1.0", "Range": "bytes=0-0"}
    for method in ("HEAD", "GET"):
        request = urllib.request.Request(url, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=12) as response:
                return response.status, response.geturl()
        except urllib.error.HTTPError as error:
            if method == "HEAD" and error.code in (403, 405, 501):
                continue
            return error.code, url
        except Exception:
            if method == "HEAD":
                continue
            return None, url
    return None, url


def main():
    external = []
    local_errors = []
    for doc in sorted(ROOT.rglob("*.md")):
        if any(part in {".git", "node_modules"} for part in doc.parts):
            continue
        text = markdown_without_fences(doc.read_text(errors="replace"))
        for raw in LINK_RE.findall(text):
            target = raw[1:-1] if raw.startswith("<") and raw.endswith(">") else raw
            target = target.replace("&amp;", "&")
            if target.startswith(("https://", "http://")):
                external.append((doc.relative_to(ROOT), target))
                continue
            if target.startswith(("mailto:", "tel:", "data:")):
                continue
            parsed = urllib.parse.urlsplit(target)
            target_path = urllib.parse.unquote(parsed.path)
            if target_path:
                resolved = (doc.parent / target_path).resolve()
            else:
                resolved = doc.resolve()
            if not resolved.exists():
                local_errors.append(f"{doc.relative_to(ROOT)}: missing {target}")
                continue
            if parsed.fragment and resolved.suffix.lower() == ".md":
                if parsed.fragment not in anchors(resolved):
                    local_errors.append(
                        f"{doc.relative_to(ROOT)}: missing anchor {target}"
                    )

    if local_errors:
        print("Local link errors:")
        print("\n".join(local_errors))

    sources_by_url = {}
    for source, url in external:
        sources_by_url.setdefault(url, source)
    failures = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        pending = {pool.submit(fetch, url): (source, url)
                   for url, source in sources_by_url.items()}
        for task in as_completed(pending):
            source, url = pending[task]
            status, _ = task.result()
            if status is None or status >= 400:
                failures.append((source, url, status))

    print(f"Checked {len(sources_by_url)} unique external URLs and local Markdown links.")
    if failures:
        print("External URLs unreachable or rejected by the host:")
        for source, url, status in failures:
            print(f"- {source}: {url} ({status if status is not None else 'network error'})")
    else:
        print("All external URLs returned a successful response.")
    return 1 if local_errors else 0


if __name__ == "__main__":
    sys.exit(main())
