"""CI gate for pages the approval executor is allowed to publish.

The executor only merges allowlisted content paths, but an HTML file can still
carry scripts, iframes, forms or a changed checkout link. This guard limits
content pages to the markup the existing guides already use.

Usage: python content_guard.py [repo_root]
"""
import json
import os
import sys
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from urllib.parse import urlsplit

from policy import GITHUB_ALLOWED_CONTENT

SITE_HOST = "usetradelink.com"
CHECKOUT_URL = "https://tlink.peakbot.com/checkout/price_1UDhh0BZsQfGLBPdlnScmYrH"
CHECKOUT_HOSTS = {"tlink.peakbot.com", "pay.peakbot.com"}
ALLOWED_SCRIPT_SRC = {"/tracking.js"}
ALLOWED_STYLESHEETS = {"/seo.css"}
GTM_NOSCRIPT = "https://www.googletagmanager.com/ns.html?id=GTM-NP74LT9B"
FORBIDDEN_TAGS = {"form", "object", "embed", "base", "applet", "frame", "frameset", "svg", "math"}
URL_ATTRS = {"href", "src", "action", "formaction", "poster", "srcset", "data"}


class ContentPage(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.errors = []
        self.titles = self.h1s = 0
        self.canonicals = []
        self.robots = ""
        self.noscript = 0
        self._script = None
        self._script_data = []

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        for name, value in a.items():
            if name.startswith("on"):
                self.errors.append(f"inline event handler {name} on <{tag}>")
            if name in URL_ATTRS and value.strip().lower().startswith(("javascript:", "data:", "vbscript:")):
                self.errors.append(f"unsafe URL in {name} on <{tag}>")
        if tag in FORBIDDEN_TAGS:
            self.errors.append(f"forbidden <{tag}>")
        elif tag == "noscript":
            self.noscript += 1
        elif tag == "title":
            self.titles += 1
        elif tag == "h1":
            self.h1s += 1
        elif tag == "meta":
            if a.get("http-equiv"):
                self.errors.append("meta http-equiv is not allowed")
            if a.get("name", "").lower() == "robots":
                self.robots = a.get("content", "").lower()
        elif tag == "link":
            rel = a.get("rel", "").lower().split()
            if "canonical" in rel:
                self.canonicals.append(a.get("href", ""))
            if "stylesheet" in rel and a.get("href") not in ALLOWED_STYLESHEETS:
                self.errors.append(f"stylesheet {a.get('href')!r} not allowed")
            if {"import", "preload", "modulepreload", "prefetch"} & set(rel):
                self.errors.append(f"link rel={a.get('rel')!r} not allowed")
        elif tag == "script":
            if a.get("type", "").lower() == "application/ld+json" and "src" not in a:
                self._script = "jsonld"
            elif a.get("src") in ALLOWED_SCRIPT_SRC:
                self._script = "src"
            else:
                self.errors.append(f"script {a.get('src') or 'inline'!r} not allowed")
                self._script = "bad"
            self._script_data = []
        elif tag == "iframe":
            if not (self.noscript and a.get("src") == GTM_NOSCRIPT):
                self.errors.append(f"iframe {a.get('src')!r} not allowed")
        elif tag == "a":
            host = (urlsplit(a.get("href", "")).hostname or "").lower()
            if host in CHECKOUT_HOSTS and a.get("href") != CHECKOUT_URL:
                self.errors.append(f"checkout link changed: {a.get('href')!r}")

    def handle_endtag(self, tag):
        if tag == "noscript" and self.noscript:
            self.noscript -= 1
        elif tag == "script" and self._script:
            data = "".join(self._script_data).strip()
            if self._script == "jsonld":
                try:
                    json.loads(data)
                except ValueError:
                    self.errors.append("invalid JSON-LD")
            elif self._script == "src" and data:
                self.errors.append("external script tag must be empty")
            self._script = None

    def handle_data(self, data):
        if self._script:
            self._script_data.append(data)


def expected_canonical(rel_path):
    page = rel_path[:-len("index.html")]
    return f"https://{SITE_HOST}/{page}"


def check_page(rel_path, html):
    doc = ContentPage()
    doc.feed(html)
    doc.close()
    errors = list(doc.errors)
    if doc.titles != 1:
        errors.append("must have exactly one <title>")
    if doc.h1s != 1:
        errors.append("must have exactly one <h1>")
    if doc.canonicals != [expected_canonical(rel_path)]:
        errors.append(f"canonical must be {expected_canonical(rel_path)}")
    if "noindex" in doc.robots:
        errors.append("content page is noindex")
    return errors


def sitemap_urls(root):
    tree = ET.parse(os.path.join(root, "sitemap.xml"))
    ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    return [(x.text or "").strip() for x in tree.findall("s:url/s:loc", ns)]


def check_repo(root):
    errors = []
    urls = sitemap_urls(root)
    if not urls or len(urls) != len(set(urls)):
        errors.append("sitemap.xml: missing or duplicate URLs")
    for url in urls:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.netloc != SITE_HOST or not parsed.path.endswith("/"):
            errors.append(f"sitemap.xml: bad URL {url}")
            continue
        page = parsed.path.strip("/")
        if not os.path.isfile(os.path.join(root, page, "index.html") if page
                              else os.path.join(root, "index.html")):
            errors.append(f"sitemap.xml: no file for {url}")
    listed = set(urls)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d != "automation"]
        for name in filenames:
            rel = os.path.relpath(os.path.join(dirpath, name), root).replace(os.sep, "/")
            if not GITHUB_ALLOWED_CONTENT.fullmatch(rel):
                continue
            with open(os.path.join(root, rel), encoding="utf-8") as fh:
                errors += [f"{rel}: {e}" for e in check_page(rel, fh.read())]
            if expected_canonical(rel) not in listed:
                errors.append(f"{rel}: missing from sitemap.xml")
    with open(os.path.join(root, "llms.txt"), encoding="utf-8") as fh:
        if "<" in fh.read():
            errors.append("llms.txt: markup is not allowed")
    return errors


def main(argv):
    root = argv[1] if len(argv) > 1 else "."
    errors = check_repo(root)
    for e in errors:
        print("ERROR", e)
    print(f"content guard: {len(errors)} problem(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
