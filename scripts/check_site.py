"""Static checks for the usetradelink.com site.

Run from the repository root: python scripts/check_site.py
Exits non-zero and lists every problem found. Uses only the standard library.
"""
import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from html import unescape
from html.parser import HTMLParser
from urllib.parse import unquote, urlsplit

SITE_HOST = "usetradelink.com"
TITLE_MAX = 70
DESCRIPTION_MAX = 160
SITEMAP_NS = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}


def squash(text):
    return re.sub(r"\s+", " ", unescape(text)).strip()


class Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.lang = None
        self.titles = []
        self.descriptions = []
        self.canonicals = []
        self.robots = ""
        self.viewport = False
        self.h1s = 0
        self.ids = set()
        self.links = []
        self.images = []
        self.jsonld = []
        self.faq = []
        self.crumbs = None
        self._capture = None
        self._buffer = []
        self._faq_depth = 0
        self._div_depth = 0
        self._faq_item = None
        self._in_crumbs = False
        self._noscript = 0

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if a.get("id"):
            self.ids.add(a["id"])
        if tag == "noscript":
            self._noscript += 1
        elif tag == "html":
            self.lang = a.get("lang")
        elif tag == "title":
            self._start("title")
        elif tag == "meta":
            name = a.get("name", "").lower()
            if name == "description":
                self.descriptions.append(a.get("content", ""))
            elif name == "robots":
                self.robots = a.get("content", "").lower()
            elif name == "viewport":
                self.viewport = True
        elif tag == "link" and "canonical" in a.get("rel", "").lower().split():
            self.canonicals.append(a.get("href", ""))
        elif tag == "h1":
            self.h1s += 1
        elif tag == "a" and "href" in a:
            self.links.append(a["href"])
            if self._in_crumbs:
                self.crumbs += 1
        elif tag == "img" and not self._noscript:
            # Tracking pixels inside <noscript> are not content images.
            self.images.append((a.get("src", ""), "alt" in a))
        elif tag == "script" and a.get("type", "").lower() == "application/ld+json":
            self._start("jsonld")
        elif tag == "nav" and "crumbs" in a.get("class", "").split():
            self._in_crumbs = True
            self.crumbs = 1  # the current page is the final, unlinked crumb
        if tag == "div":
            self._div_depth += 1
            if "faq-item" in a.get("class", "").split():
                self._faq_depth = self._div_depth
                self._faq_item = {"q": "", "a": []}
        if self._faq_item is not None and tag in ("h3", "h4") and not self._faq_item["q"]:
            self._start("faq_q")
        elif self._faq_item is not None and tag == "p" and self._capture is None:
            self._start("faq_a")

    def handle_endtag(self, tag):
        if self._capture == "title" and tag == "title":
            self.titles.append(self._finish())
        elif self._capture == "jsonld" and tag == "script":
            self.jsonld.append(self._finish(raw=True))
        elif self._capture == "faq_q" and tag in ("h3", "h4"):
            self._faq_item["q"] = self._finish()
        elif self._capture == "faq_a" and tag == "p":
            self._faq_item["a"].append(self._finish())
        if tag == "nav":
            self._in_crumbs = False
        if tag == "noscript" and self._noscript:
            self._noscript -= 1
        if tag == "div":
            if self._faq_item is not None and self._div_depth == self._faq_depth:
                self.faq.append((self._faq_item["q"], " ".join(self._faq_item["a"])))
                self._faq_item = None
            self._div_depth -= 1

    def handle_data(self, data):
        if self._capture:
            self._buffer.append(data)

    def _start(self, kind):
        self._capture = kind
        self._buffer = []

    def _finish(self, raw=False):
        text = "".join(self._buffer)
        self._capture = None
        return text if raw else squash(text)


def tracked_files(root):
    try:
        out = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True, text=True,
                             check=True).stdout
        return [line for line in out.splitlines() if line]
    except (OSError, subprocess.CalledProcessError):
        files = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            files += [os.path.relpath(os.path.join(dirpath, f), root).replace(os.sep, "/")
                      for f in filenames]
        return files


def url_for(rel_path):
    if rel_path.endswith("index.html"):
        return f"https://{SITE_HOST}/" + rel_path[:-len("index.html")]
    return f"https://{SITE_HOST}/" + rel_path


def target_file(root, path):
    """Map a site path to the file Cloudflare Pages would serve, or None."""
    path = unquote(path)
    candidates = [path.lstrip("/")]
    if path.endswith("/"):
        candidates = [path.lstrip("/") + "index.html"]
    elif "." not in path.rsplit("/", 1)[-1]:
        candidates += [path.lstrip("/") + "/index.html", path.lstrip("/") + ".html"]
    for c in candidates:
        if c and os.path.isfile(os.path.join(root, c)):
            return c
    return None


def jsonld_nodes(blocks):
    for raw in blocks:
        data = json.loads(raw)
        stack = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
            elif isinstance(node, dict):
                yield node
                stack.extend(v for k, v in node.items() if k == "@graph")


def check(root="."):
    errors = []
    files = tracked_files(root)
    html_files = sorted(f for f in files if f.endswith(".html"))
    pages = {}
    for rel in html_files:
        with open(os.path.join(root, rel), encoding="utf-8") as fh:
            doc = Page()
            doc.feed(fh.read())
            doc.close()
        pages[rel] = doc

    tree = ET.parse(os.path.join(root, "sitemap.xml"))
    sitemap = [(x.text or "").strip() for x in tree.findall("s:url/s:loc", SITEMAP_NS)]
    if len(sitemap) != len(set(sitemap)):
        errors.append("sitemap.xml: duplicate URLs")

    for rel, doc in pages.items():
        err = lambda msg, rel=rel: errors.append(f"{rel}: {msg}")
        indexable = "noindex" not in doc.robots
        if not doc.lang:
            err("missing <html lang>")
        if not doc.viewport:
            err("missing viewport meta")
        if len(doc.titles) != 1 or not doc.titles[0]:
            err("must have exactly one non-empty <title>")
        elif len(doc.titles[0]) > TITLE_MAX:
            err(f"title is {len(doc.titles[0])} characters (max {TITLE_MAX})")
        if indexable:
            if len(doc.descriptions) != 1 or not doc.descriptions[0].strip():
                err("must have exactly one meta description")
            elif len(squash(doc.descriptions[0])) > DESCRIPTION_MAX:
                err(f"meta description is {len(squash(doc.descriptions[0]))} characters "
                    f"(max {DESCRIPTION_MAX})")
            if re.search(r"\bpre-?sale\b", " ".join(doc.descriptions + doc.titles), re.I):
                err("title/description still describes a presale")
            if doc.canonicals != [url_for(rel)]:
                err(f"canonical must be exactly {url_for(rel)}")
            if doc.h1s != 1:
                err(f"has {doc.h1s} <h1> elements")
            if url_for(rel) not in sitemap:
                err("indexable page missing from sitemap.xml")
        elif url_for(rel) in sitemap:
            err("noindex page is listed in sitemap.xml")

        for src, has_alt in doc.images:
            if not has_alt:
                err(f"image {src} has no alt attribute")
            parts = urlsplit(src)
            if not parts.scheme and target_file(root, os.path.join(os.path.dirname("/" + rel), parts.path)
                                                if not parts.path.startswith("/") else parts.path) is None:
                err(f"image {src} does not exist")

        for href in doc.links:
            parts = urlsplit(href)
            if parts.scheme in ("mailto", "tel"):
                continue
            if parts.scheme and parts.hostname != SITE_HOST:
                continue
            path = parts.path or ("/" + rel if not parts.scheme else "/")
            if not path.startswith("/"):
                path = os.path.normpath(os.path.join(os.path.dirname("/" + rel), path))
            target = rel if (not parts.path and not parts.scheme) else target_file(root, path)
            if target is None:
                err(f"broken internal link {href}")
            elif parts.fragment and target in pages and parts.fragment not in pages[target].ids:
                err(f"link {href} points to a missing #{parts.fragment}")

        try:
            nodes = list(jsonld_nodes(doc.jsonld))
        except ValueError:
            err("invalid JSON-LD")
            nodes = []
        visible_faq = {q: a for q, a in doc.faq}
        for node in nodes:
            kind = node.get("@type")
            if kind == "FAQPage":
                for item in node.get("mainEntity", []):
                    q = squash(item.get("name", ""))
                    answer = squash((item.get("acceptedAnswer") or {}).get("text", ""))
                    if q not in visible_faq:
                        err(f"FAQ schema question not visible on page: {q!r}")
                    elif squash(re.sub(r"<[^>]+>", "", answer)) != visible_faq[q]:
                        err(f"FAQ schema answer differs from visible text: {q!r}")
            elif kind == "BreadcrumbList" and doc.crumbs is not None:
                count = len(node.get("itemListElement", []))
                if count != doc.crumbs:
                    err(f"BreadcrumbList has {count} items but the visible breadcrumb has {doc.crumbs}")

    for url in sitemap:
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.hostname != SITE_HOST:
            errors.append(f"sitemap.xml: off-site URL {url}")
        elif target_file(root, parts.path) is None:
            errors.append(f"sitemap.xml: no page for {url}")

    with open(os.path.join(root, "llms.txt"), encoding="utf-8") as fh:
        for url in re.findall(r"\((https://usetradelink\.com[^)\s]*)\)", fh.read()):
            if target_file(root, urlsplit(url).path) is None:
                errors.append(f"llms.txt: broken link {url}")

    with open(os.path.join(root, "robots.txt"), encoding="utf-8") as fh:
        if f"Sitemap: https://{SITE_HOST}/sitemap.xml" not in fh.read():
            errors.append("robots.txt: missing Sitemap line")
    return errors


def main(argv):
    errors = check(argv[1] if len(argv) > 1 else ".")
    for e in errors:
        print("ERROR", e)
    print(f"site check: {len(errors)} problem(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
