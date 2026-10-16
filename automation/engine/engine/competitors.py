"""Snapshot competitors' public pages: structure only, with URL and fetch time.

Respects robots.txt, identifies itself, fetches a small number of pages per run
and stores extracted title/headings/word count — never paraphrased claims.
"""
import hashlib
import json
import urllib.robotparser
from urllib.parse import urlsplit

import requests

from .inventory import PageParser

USER_AGENT = "TradeLinkSEOResearch/1.0 (+https://usetradelink.com/; competitor page snapshots)"


def allowed(session, url, cache):
    parts = urlsplit(url)
    base = f"{parts.scheme}://{parts.netloc}"
    if base not in cache:
        rp = urllib.robotparser.RobotFileParser()
        try:
            r = session.get(base + "/robots.txt", timeout=10, headers={"User-Agent": USER_AGENT})
            rp.parse(r.text.splitlines() if r.status_code == 200 else [])
        except requests.RequestException:
            rp.parse(["User-agent: *", "Disallow: /"])  # unknown robots policy: do not crawl
        cache[base] = rp
    return cache[base].can_fetch(USER_AGENT, url)


def snapshot(session, url):
    r = session.get(url, timeout=15, headers={"User-Agent": USER_AGENT})
    result = {"url": url, "domain": urlsplit(url).hostname, "http_status": r.status_code}
    if r.status_code == 200 and "text/html" in r.headers.get("content-type", ""):
        p = PageParser()
        p.feed(r.text[:3_000_000])
        text = " ".join(" ".join(p.text).split())
        result.update(title=p.title, meta_description=p.description.strip(), h1=p.h1,
                      headings=p.headings[:80], word_count=len(text.split()),
                      content_hash=hashlib.sha256(text.encode()).hexdigest())
    return result


def run(conn, config, session=None):
    session = session or requests.Session()
    cache = {}
    limit = config["limits"]["competitor_pages_per_run"]
    days = config["refresh_days"]["competitor_pages"]
    urls = [u for c in config["competitors"] for u in c.get("pages", [])]
    # Add the competitor URLs that actually rank for our keywords.
    urls += [r["url"] for r in conn.execute(
        "SELECT url, MAX(traffic) AS t FROM seo_competitor_rankings WHERE url IS NOT NULL "
        "GROUP BY url ORDER BY t DESC NULLS LAST LIMIT 20")]
    summary = {"fetched": 0, "blocked_by_robots": 0, "errors": 0, "changed": 0}
    for url in list(dict.fromkeys(urls)):
        if summary["fetched"] >= limit:
            break
        recent = conn.execute("SELECT content_hash FROM seo_competitor_pages WHERE url=%s AND "
                              "fetched_at >= now() - make_interval(days => %s) ORDER BY fetched_at DESC LIMIT 1",
                              (url, days)).fetchone()
        if recent:
            continue
        try:
            if not allowed(session, url, cache):
                summary["blocked_by_robots"] += 1
                continue
            snap = snapshot(session, url)
        except requests.RequestException:
            summary["errors"] += 1
            continue
        previous = conn.execute("SELECT content_hash FROM seo_competitor_pages WHERE url=%s "
                                "ORDER BY fetched_at DESC LIMIT 1", (url,)).fetchone()
        if previous and previous["content_hash"] != snap.get("content_hash"):
            summary["changed"] += 1
        with conn.transaction():
            conn.execute("INSERT INTO seo_competitor_pages(url,domain,http_status,title,meta_description,h1,"
                         "headings,word_count,content_hash) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                         (url, snap["domain"], snap["http_status"], snap.get("title"),
                          snap.get("meta_description"), snap.get("h1"), json.dumps(snap.get("headings", [])),
                          snap.get("word_count"), snap.get("content_hash")))
        summary["fetched"] += 1
    return summary
