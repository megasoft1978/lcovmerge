#!/usr/bin/env python3
"""Check Markdown and published-site links, fragments, and external URLs."""

import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE_ROOT = ROOT / "docs" / "site"
LINK_RE = re.compile(r"!?\[[^\]]*\]\((<[^>]+>|[^\s)]+)(?:\s+[^)]*)?\)")
FENCE_RE = re.compile(r"^\s*(```|~~~)")
REPOSITORY_URL = ("github.com", "megasoft1978", "lcovmerge")
REPOSITORY_HOST = "github.com"


class HTMLLinks(HTMLParser):
    """Collect navigable/local resource references from HTML start tags."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.targets = []
        self.ids = set()

    def handle_starttag(self, _tag, attrs):
        attributes = dict(attrs)
        for name in ("href", "src"):
            target = attributes.get(name)
            if target:
                self.targets.append(target)
        element_id = attributes.get("id")
        if element_id:
            self.ids.add(element_id)
        if _tag.lower() == "a" and attributes.get("name"):
            self.ids.add(attributes["name"])

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)


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


def html_ids(path):
    parser = HTMLLinks()
    parser.feed(path.read_text(errors="replace"))
    return parser.ids


def github_markdown_path(url):
    """Map this repository's GitHub blob Markdown URLs back to local files."""
    parsed = urllib.parse.urlsplit(url)
    parts = [urllib.parse.unquote(part) for part in parsed.path.strip("/").split("/")]
    if (parsed.hostname or "").lower() != REPOSITORY_HOST:
        return None
    if len(parts) < 5 or tuple(parts[:2]) != REPOSITORY_URL[1:] or parts[2] != "blob":
        return None
    local_path = ROOT.joinpath(*parts[4:])
    return local_path if local_path.suffix.lower() == ".md" else None


def fetch(url):
    headers = {"User-Agent": "lcovmerge-doc-link-check/1.0", "Range": "bytes=0-0"}
    for attempt in range(3):
        retry = False
        for method in ("HEAD", "GET"):
            request = urllib.request.Request(url, headers=headers, method=method)
            try:
                with urllib.request.urlopen(request, timeout=12) as response:
                    return response.status, response.geturl()
            except urllib.error.HTTPError as error:
                if method == "HEAD" and error.code in (403, 405, 501):
                    continue
                if 500 <= error.code <= 599 and attempt < 2:
                    retry = True
                    break
                return error.code, error.geturl()
            except Exception:
                if method == "HEAD":
                    continue
                if attempt < 2:
                    retry = True
                    break
                return None, url
        if not retry:
            return None, url
        time.sleep(0.2 * (attempt + 1))
    return None, url


def local_path(source, parsed, *, html_source):
    path_text = urllib.parse.unquote(parsed.path)
    if not path_text:
        return source.resolve()
    if html_source and path_text in ("/lcovmerge", "/lcovmerge/"):
        return (SITE_ROOT / "index.html").resolve()
    if html_source and path_text.startswith("/lcovmerge/"):
        return (SITE_ROOT / path_text[len("/lcovmerge/"):]).resolve()
    if path_text.startswith("/"):
        return (ROOT / path_text.lstrip("/")).resolve()
    return (source.parent / path_text).resolve()


def external_request_url(url):
    parsed = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path,
                                   parsed.query, ""))


def main():
    external = []
    local_errors = []
    local_reference_count = 0
    anchor_cache = {}
    html_id_cache = {}

    def check_markdown_fragment(source, target, target_path, fragment):
        if not fragment or target_path.suffix.lower() != ".md":
            return
        if not target_path.exists():
            local_errors.append(f"{source.relative_to(ROOT)}: missing Markdown target {target}")
            return
        if target_path not in anchor_cache:
            anchor_cache[target_path] = anchors(target_path)
        found = anchor_cache[target_path]
        if urllib.parse.unquote(fragment) not in found:
            local_errors.append(f"{source.relative_to(ROOT)}: missing anchor {target}")

    def check_github_markdown_fragment(source, target, parsed):
        if not parsed.fragment or not parsed.path.lower().endswith(".md"):
            return
        target_path = github_markdown_path(target)
        if target_path is None:
            return
        check_markdown_fragment(source, target, target_path, parsed.fragment)

    def check_reference(source, target, *, html_source):
        nonlocal local_reference_count
        target = target.replace("&amp;", "&").strip()
        if not target:
            return
        parsed = urllib.parse.urlsplit(target)
        if parsed.scheme.lower() in ("http", "https"):
            external.append((source.relative_to(ROOT), external_request_url(target)))
            check_github_markdown_fragment(source, target, parsed)
            return
        if target.startswith("//"):
            external_url = "https:" + target
            external.append((source.relative_to(ROOT), external_request_url(external_url)))
            check_github_markdown_fragment(source, external_url, urllib.parse.urlsplit(external_url))
            return
        if parsed.scheme.lower() in ("mailto", "tel", "data", "javascript", "ftp"):
            return
        local_reference_count += 1
        resolved = local_path(source, parsed, html_source=html_source)
        if resolved.is_dir():
            if html_source:
                index = resolved / "index.html"
                resolved = index if index.exists() else resolved
        if not resolved.exists():
            local_errors.append(f"{source.relative_to(ROOT)}: missing {target}")
            return
        if not parsed.fragment:
            return
        fragment = urllib.parse.unquote(parsed.fragment)
        if resolved.suffix.lower() == ".md":
            check_markdown_fragment(source, target, resolved, fragment)
        elif resolved.suffix.lower() == ".html":
            if resolved not in html_id_cache:
                html_id_cache[resolved] = html_ids(resolved)
            found = html_id_cache[resolved]
            if fragment not in found:
                local_errors.append(f"{source.relative_to(ROOT)}: missing anchor {target}")

    markdown_files = []
    for doc in sorted(ROOT.rglob("*.md")):
        if any(part in {".git", "node_modules", ".luna-tmp", ".cache"} for part in doc.relative_to(ROOT).parts):
            continue
        markdown_files.append(doc)
        text = markdown_without_fences(doc.read_text(errors="replace"))
        for raw in LINK_RE.findall(text):
            target = raw[1:-1] if raw.startswith("<") and raw.endswith(">") else raw
            check_reference(doc, target, html_source=False)

    html_files = sorted(SITE_ROOT.glob("*.html"))
    for page in html_files:
        parser = HTMLLinks()
        parser.feed(page.read_text(errors="replace"))
        html_id_cache[page.resolve()] = parser.ids
        for target in parser.targets:
            check_reference(page, target, html_source=True)

    if local_errors:
        print("Broken local links and fragments:")
        print("\n".join(f"- {error}" for error in sorted(set(local_errors))))

    sources_by_url = {}
    for source, url in external:
        sources_by_url.setdefault(url, source)
    broken_external = []
    transient_external = []
    unverified_external = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        pending = {pool.submit(fetch, url): (source, url)
                   for url, source in sources_by_url.items()}
        for task in as_completed(pending):
            source, url = pending[task]
            status, final_url = task.result()
            if status is None:
                unverified_external.append((source, url, final_url))
            elif 500 <= status <= 599:
                transient_external.append((source, url, status))
            elif status >= 400:
                broken_external.append((source, url, status))

    print(f"Checked {len(sources_by_url)} unique external URLs, "
          f"{local_reference_count} local references in {len(markdown_files)} Markdown files "
          f"and {len(html_files)} site HTML pages.")
    if broken_external:
        print("Broken external URLs (HTTP 4xx):")
        for source, url, status in sorted(broken_external):
            print(f"- {source}: {url} ({status})")
    if transient_external:
        print("Transient external responses (HTTP 5xx; retry later):")
        for source, url, status in sorted(transient_external):
            print(f"- {source}: {url} ({status})")
    if unverified_external:
        print("External URLs not verified because of network errors:")
        for source, url, detail in sorted(unverified_external):
            print(f"- {source}: {url} ({detail})")
    if not broken_external and not transient_external and not unverified_external:
        print("All external URLs returned a successful response.")
    return 1 if local_errors or broken_external else 0


if __name__ == "__main__":
    sys.exit(main())
