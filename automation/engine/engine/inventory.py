"""Inventory TradeLink's own pages: structure, text, links and modification date.

Reads either the live site (sitemap + HTTP) or a local checkout of the website,
which is what tests and offline research runs use.
"""
import hashlib
import json
import os
import re
import xml.etree.ElementTree as ET
from datetime import date
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

SITE = "https://usetradelink.com/"
HOST = "usetradelink.com"
SKIP_TEXT = {"script", "style", "noscript", "nav", "footer", "header", "svg"}


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.description = ""
        self.h1 = ""
        self.headings = []
        self.links = []
        self.text = []
        self.jsonld = []
        self._stack = []
        self._capture = None
        self._buf = []

    def handle_starttag(self, tag, attrs):
        a = {k: v or "" for k, v in attrs}
        self._stack.append(tag)
        if tag == "meta" and a.get("name", "").lower() == "description":
            self.description = a.get("content", "")
        elif tag == "a" and a.get("href"):
            self.links.append(a["href"])
        elif tag in ("title", "h1", "h2", "h3"):
            self._capture, self._buf = tag, []
        elif tag == "script" and a.get("type", "").lower() == "application/ld+json":
            self._capture, self._buf = "jsonld", []
        if tag in ("meta", "link", "img", "br", "hr", "input", "source"):
            self._stack.pop()

    def handle_endtag(self, tag):
        if self._capture == tag or (self._capture == "jsonld" and tag == "script"):
            text = "".join(self._buf)
            if self._capture == "jsonld":
                self.jsonld.append(text)
            else:
                text = re.sub(r"\s+", " ", text).strip()
                if self._capture == "title":
                    self.title = text
                elif self._capture == "h1" and not self.h1:
                    self.h1 = text
                elif self._capture in ("h2", "h3"):
                    self.headings.append(text)
            self._capture = None
        while self._stack and self._stack[-1] != tag:
            self._stack.pop()
        if self._stack:
            self._stack.pop()

    def handle_data(self, data):
        if self._capture:
            self._buf.append(data)
        if not any(t in SKIP_TEXT for t in self._stack):
            self.text.append(data)


def date_modified(jsonld_blocks):
    for raw in jsonld_blocks:
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        stack = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                value = node.get("dateModified")
                if isinstance(value, str) and re.match(r"\d{4}-\d{2}-\d{2}", value):
                    return date.fromisoformat(value[:10])
                stack.extend(node.values())
            elif isinstance(node, list):
                stack.extend(node)
    return None


def normalize_link(base, href):
    parts = urlsplit(urljoin(base, href))
    if parts.scheme not in ("http", "https") or parts.hostname != HOST:
        return None
    path = parts.path or "/"
    if not path.endswith("/") and "." not in path.rsplit("/", 1)[-1]:
        path += "/"
    return f"https://{HOST}{path}"


def parse_page(url, html):
    p = PageParser()
    p.feed(html)
    p.close()
    text = re.sub(r"\s+", " ", " ".join(p.text)).strip()
    links = sorted({l for l in (normalize_link(url, h) for h in p.links) if l and l != url})
    return {"url": url, "title": p.title, "meta_description": p.description.strip(), "h1": p.h1,
            "headings": p.headings, "word_count": len(text.split()), "internal_links": links,
            "date_modified": date_modified(p.jsonld), "text": text,
            "content_hash": hashlib.sha256(html.encode()).hexdigest()}


def sitemap_urls(xml_text):
    ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    return [(e.text or "").strip() for e in ET.fromstring(xml_text).findall("s:url/s:loc", ns)
            if (e.text or "").strip().startswith(SITE)]


def from_directory(root):
    with open(os.path.join(root, "sitemap.xml"), encoding="utf-8") as fh:
        urls = sitemap_urls(fh.read())
    pages = []
    for url in urls:
        rel = urlsplit(url).path.strip("/")
        path = os.path.join(root, rel, "index.html") if rel else os.path.join(root, "index.html")
        with open(path, encoding="utf-8") as fh:
            pages.append(parse_page(url, fh.read()))
    return pages


def from_site(session, limit=200, timeout=15):
    r = session.get(SITE + "sitemap.xml", timeout=timeout)
    r.raise_for_status()
    pages = []
    for url in sitemap_urls(r.text)[:limit]:
        resp = session.get(url, timeout=timeout)
        if resp.status_code == 200 and "text/html" in resp.headers.get("content-type", ""):
            pages.append(parse_page(url, resp.text))
    return pages


def save(conn, pages):
    with conn.transaction():
        for p in pages:
            conn.execute(
                "INSERT INTO seo_page_inventory(url,title,meta_description,h1,headings,word_count,"
                "internal_links,date_modified,content_hash) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (p["url"], p["title"], p["meta_description"], p["h1"], json.dumps(p["headings"]),
                 p["word_count"], json.dumps(p["internal_links"]), p["date_modified"], p["content_hash"]))
