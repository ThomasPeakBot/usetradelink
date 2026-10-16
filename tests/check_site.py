#!/usr/bin/env python3
"""Static SEO / integrity checks for the TradeLink site. Stdlib only.

Run from the repo root:  python3 tests/check_site.py
Exits non-zero on any failure.
"""
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from urllib.parse import urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ORIGIN = 'https://usetradelink.com'
CHECKOUT = 'https://tlink.peakbot.com/checkout/price_1UDhh0BZsQfGLBPdlnScmYrH'
failures = []


def fail(page, msg):
    failures.append(f'{page}: {msg}')


class Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.titles, self.h1, self.links, self.imgs, self.scripts, self.ld = [], 0, [], [], [], []
        self.meta, self.canonical, self.ids = {}, None, set()
        self._in_title = self._in_ld = False
        self._buf = ''

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if 'id' in a:
            self.ids.add(a['id'])
        if tag == 'title':
            self._in_title, self._buf = True, ''
        elif tag == 'h1':
            self.h1 += 1
        elif tag == 'a' and 'href' in a:
            self.links.append(a['href'])
        elif tag == 'img':
            self.imgs.append(a)
        elif tag == 'meta' and 'name' in a:
            self.meta[a['name']] = a.get('content', '')
        elif tag == 'link' and a.get('rel') == 'canonical':
            self.canonical = a.get('href')
        elif tag == 'script':
            if a.get('type') == 'application/ld+json':
                self._in_ld, self._buf = True, ''
            if 'src' in a:
                self.scripts.append(a['src'])

    def handle_endtag(self, tag):
        if tag == 'title' and self._in_title:
            self.titles.append(self._buf.strip())
            self._in_title = False
        elif tag == 'script' and self._in_ld:
            self.ld.append(self._buf)
            self._in_ld = False

    def handle_data(self, data):
        if self._in_title or self._in_ld:
            self._buf += data


def local_path(url_path):
    p = url_path.split('#')[0].split('?')[0]
    if p.endswith('/'):
        p += 'index.html'
    return os.path.join(ROOT, p.lstrip('/'))


# ---- sitemap ----
tree = ET.parse(os.path.join(ROOT, 'sitemap.xml'))
ns = {'s': 'http://www.sitemaps.org/schemas/sitemap/0.9'}
locs = [e.text for e in tree.findall('.//s:loc', ns)]
if len(locs) != len(set(locs)):
    fail('sitemap.xml', 'duplicate <loc>')

robots = open(os.path.join(ROOT, 'robots.txt')).read()
if f'Sitemap: {ORIGIN}/sitemap.xml' not in robots or re.search(r'^Disallow:\s*/\s*$', robots, re.M):
    fail('robots.txt', 'missing sitemap line or blocks the whole site')

# Every indexable HTML page on disk must be in the sitemap.
on_disk = []
for dirpath, dirs, files in os.walk(ROOT):
    dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ('tests', 'research', 'node_modules', '_src')]
    for f in files:
        if f.endswith('.html'):
            rel = os.path.relpath(os.path.join(dirpath, f), ROOT).replace(os.sep, '/')
            on_disk.append(rel)

titles, descs = {}, {}
for rel in sorted(on_disk):
    html = open(os.path.join(ROOT, rel), encoding='utf-8').read()
    p = Page()
    p.feed(html)
    url = ORIGIN + '/' + (rel[:-len('index.html')] if rel.endswith('index.html') else rel)
    noindex = 'noindex' in p.meta.get('robots', '')

    if noindex:
        if url in locs:
            fail(rel, 'noindex page is listed in sitemap')
    else:
        if url not in locs:
            fail(rel, f'indexable page missing from sitemap ({url})')
        if p.canonical != url:
            fail(rel, f'canonical {p.canonical!r} != {url!r}')
        d = p.meta.get('description', '')
        if not 70 <= len(d) <= 165:
            fail(rel, f'meta description length {len(d)} (want 70-165)')
        descs.setdefault(d, []).append(rel)
        if p.h1 != 1:
            fail(rel, f'{p.h1} <h1> elements')
        if not p.ld:
            fail(rel, 'no JSON-LD')

    if len(p.titles) != 1:
        fail(rel, f'{len(p.titles)} <title> elements')
    elif not noindex:
        t = p.titles[0]
        if len(t) > 65:
            fail(rel, f'title is {len(t)} chars: {t}')
        titles.setdefault(t, []).append(rel)

    for block in p.ld:
        try:
            json.loads(block)
        except ValueError as e:
            fail(rel, f'invalid JSON-LD: {e}')

    # Analytics: shared loader exactly once, no stray/retired inline tags.
    if p.scripts.count('/tracking.js') != 1:
        fail(rel, f'/tracking.js included {p.scripts.count("/tracking.js")} times')
    for bad in ('UA-228903299', 'gtag/js?id=', 'ttq.load(', "fbq('init'", 'clarity.ms/tag'):
        if bad in html:
            fail(rel, f'inline tag outside tracking.js: {bad}')

    for img in p.imgs:
        if not img.get('alt'):
            fail(rel, f'img without alt: {img.get("src")}')
        if img.get('src', '').startswith('/') and not os.path.exists(local_path(img['src'])):
            fail(rel, f'missing image {img["src"]}')

    for href in p.links:
        u = urlparse(href)
        if u.scheme in ('mailto', 'tel'):
            continue
        if 'tlink.peakbot.com' in href or 'pay.peakbot.com' in href:
            if href != CHECKOUT:
                fail(rel, f'unexpected checkout URL {href}')
            continue
        if u.scheme or u.netloc:
            continue
        if href.startswith('#'):
            if href[1:] and href[1:] not in p.ids:
                fail(rel, f'broken in-page anchor {href}')
            continue
        target = local_path(href) if href.startswith('/') else os.path.join(os.path.dirname(os.path.join(ROOT, rel)), href)
        if not os.path.exists(target):
            fail(rel, f'broken internal link {href}')
        frag = u.fragment
        if frag and os.path.exists(target):
            tp = Page()
            tp.feed(open(target, encoding='utf-8').read())
            if frag not in tp.ids:
                fail(rel, f'link {href} points at missing #{frag}')

for t, pages in titles.items():
    if len(pages) > 1:
        fail(','.join(pages), f'duplicate title {t!r}')
for d, pages in descs.items():
    if len(pages) > 1:
        fail(','.join(pages), 'duplicate meta description')

for loc in locs:
    if not os.path.exists(local_path(urlparse(loc).path)):
        fail('sitemap.xml', f'{loc} has no file')

tracking = open(os.path.join(ROOT, 'tracking.js')).read()
if 'UA-' in re.sub(r'//.*|/\*[\s\S]*?\*/', '', tracking):
    fail('tracking.js', 'retired Universal Analytics ID still configured')
if tracking.count("ttq.load(CONFIG") != 1:
    fail('tracking.js', 'TikTok pixel should load exactly once')

if failures:
    print('FAIL')
    for f in failures:
        print(' -', f)
    sys.exit(1)
print(f'OK: {len(on_disk)} HTML files, {len(locs)} sitemap URLs')
