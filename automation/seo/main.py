"""TradeLink Phase 1 SEO collector. Reads public site and optional Search Console; never edits website."""
import argparse
import json
import os
import sys
import uuid
from datetime import date, timedelta, datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit
import xml.etree.ElementTree as ET

import psycopg
from semrush import enrich as enrich_semrush
import requests

SITE = "https://usetradelink.com"
SITEMAP = SITE + "/sitemap.xml"
GSC_PROPERTY = "sc-domain:usetradelink.com"
TIMEOUT = 12
MAX_PAGES = 100

SCHEMA = """
CREATE TABLE IF NOT EXISTS seo_runs (
  id uuid PRIMARY KEY, started_at timestamptz NOT NULL, finished_at timestamptz,
  page_count integer, issue_count integer, gsc_rows integer,
  gsc_status text NOT NULL, status text NOT NULL
);
CREATE TABLE IF NOT EXISTS seo_pages (
  run_id uuid NOT NULL REFERENCES seo_runs(id), url text NOT NULL,
  http_status integer, title text, meta_description text, canonical text,
  h1_count integer, internal_links integer, noindex boolean,
  issues jsonb NOT NULL, PRIMARY KEY(run_id, url)
);
CREATE TABLE IF NOT EXISTS seo_search_performance (
  data_date date NOT NULL, page text NOT NULL, query text NOT NULL,
  clicks double precision NOT NULL, impressions double precision NOT NULL,
  ctr double precision NOT NULL, position double precision NOT NULL,
  collected_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(data_date, page, query)
);
"""

class PageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.title = ""
        self.description = ""
        self.canonical = ""
        self.robots = ""
        self.h1_count = 0
        self.links = []
        self._title = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "title":
            self._title = True
        elif tag == "h1":
            self.h1_count += 1
        elif tag == "meta":
            name = (a.get("name") or "").lower()
            if name == "description":
                self.description = a.get("content") or ""
            elif name == "robots":
                self.robots = a.get("content") or ""
        elif tag == "link" and "canonical" in (a.get("rel") or "").lower().split():
            self.canonical = a.get("href") or ""
        elif tag == "a" and a.get("href"):
            self.links.append(a["href"])

    def handle_endtag(self, tag):
        if tag == "title":
            self._title = False

    def handle_data(self, data):
        if self._title:
            self.title += data

def normalized_url(value):
    u = urlsplit(value)
    path = u.path or "/"
    if path != "/" and not path.endswith("/") and "." not in path.rsplit("/", 1)[-1]:
        path += "/"
    return urlunsplit((u.scheme.lower(), (u.hostname or "").lower(), path, "", ""))

def extract_sitemap(xml_text):
    root = ET.fromstring(xml_text)
    ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    urls = []
    for el in root.findall("s:url/s:loc", ns):
        if el.text:
            url = normalized_url(el.text.strip())
            if urlsplit(url).scheme == "https" and urlsplit(url).hostname == "usetradelink.com":
                urls.append(url)
    return list(dict.fromkeys(urls))[:MAX_PAGES]

def inspect_page(session, url, sitemap_urls):
    issues = []
    result = {"url": url, "http_status": None, "title": "", "meta_description": "",
              "canonical": "", "h1_count": 0, "internal_links": 0, "noindex": False,
              "issues": issues}
    try:
        response = session.get(url, timeout=TIMEOUT, allow_redirects=True)
        result["http_status"] = response.status_code
        if response.status_code != 200:
            issues.append("http_non_200")
            return result
        if urlsplit(response.url).hostname != "usetradelink.com":
            issues.append("redirect_off_domain")
            return result
        if "text/html" not in response.headers.get("content-type", "").lower():
            issues.append("not_html")
            return result
        doc = PageParser()
        doc.feed(response.text[:2_000_000])
        result.update(title=doc.title.strip(), meta_description=doc.description.strip(),
                      canonical=doc.canonical.strip(), h1_count=doc.h1_count)
        result["noindex"] = ("noindex" in doc.robots.lower() or
                             "noindex" in response.headers.get("x-robots-tag", "").lower())
        links = set()
        for href in doc.links:
            dest = urlsplit(urljoin(url, href))
            if dest.scheme in ("http", "https") and dest.hostname == "usetradelink.com":
                links.add(normalized_url(urljoin(url, href)))
        result["internal_links"] = len(links)
        if not result["title"]:
            issues.append("missing_title")
        if not result["meta_description"]:
            issues.append("missing_description")
        if doc.h1_count != 1:
            issues.append("h1_count_not_one")
        if not doc.canonical:
            issues.append("missing_canonical")
        elif normalized_url(urljoin(url, doc.canonical)) != normalized_url(url):
            issues.append("canonical_mismatch")
        if result["noindex"]:
            issues.append("noindex")
        # A page's links to other sitemap pages should be checked without fetching external hosts.
        return result
    except requests.RequestException as exc:
        issues.append("fetch_error:" + exc.__class__.__name__)
        return result

def read_gsc(session, service_account_json):
    from google.oauth2 import service_account
    from google.auth.transport.requests import Request
    credentials = service_account.Credentials.from_service_account_info(
        json.loads(service_account_json),
        scopes=["https://www.googleapis.com/auth/webmasters.readonly"])
    credentials.refresh(Request())
    end = date.today() - timedelta(days=3)
    start = end - timedelta(days=27)
    url = ("https://searchconsole.googleapis.com/webmasters/v3/sites/"
           "sc-domain%3Ausetradelink.com/searchAnalytics/query")
    rows = []
    for offset in range(0, 25000, 2500):
        body = {"startDate": start.isoformat(), "endDate": end.isoformat(),
                "dimensions": ["date", "page", "query"], "rowLimit": 2500,
                "startRow": offset, "dataState": "final"}
        r = session.post(url, headers={"Authorization": "Bearer " + credentials.token},
                         json=body, timeout=30)
        r.raise_for_status()
        chunk = r.json().get("rows", [])
        rows.extend(chunk)
        if len(chunk) < 2500:
            break
    return rows

def save_database(database_url, run_id, pages, gsc_rows, gsc_status):
    issue_count = sum(len(p["issues"]) for p in pages)
    with psycopg.connect(database_url, connect_timeout=10) as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA)
            cur.execute(
                "INSERT INTO seo_runs(id,started_at,finished_at,page_count,issue_count,gsc_rows,gsc_status,status) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
                (run_id, datetime.now(timezone.utc), datetime.now(timezone.utc),
                 len(pages), issue_count, len(gsc_rows), gsc_status, "complete"))
            for p in pages:
                cur.execute(
                    "INSERT INTO seo_pages(run_id,url,http_status,title,meta_description,canonical,h1_count,"
                    "internal_links,noindex,issues) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)",
                    (run_id, p["url"], p["http_status"], p["title"], p["meta_description"],
                     p["canonical"], p["h1_count"], p["internal_links"], p["noindex"],
                     json.dumps(p["issues"])))
            for row in gsc_rows:
                keys = row.get("keys", [])
                if len(keys) != 3:
                    continue
                cur.execute(
                    "INSERT INTO seo_search_performance(data_date,page,query,clicks,impressions,ctr,position) "
                    "VALUES(%s,%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT(data_date,page,query) DO UPDATE SET "
                    "clicks=EXCLUDED.clicks,impressions=EXCLUDED.impressions,"
                    "ctr=EXCLUDED.ctr,position=EXCLUDED.position,collected_at=now()",
                    (keys[0], keys[1], keys[2], row.get("clicks", 0),
                     row.get("impressions", 0), row.get("ctr", 0), row.get("position", 0)))
        conn.commit()

def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="No DB writes and no GSC API calls")
    args = parser.parse_args(argv)
    run_id = uuid.uuid4()
    session = requests.Session()
    session.headers.update({"User-Agent": "TradeLinkSEOAudit/1.0 (site-owner; read-only)"})
    resp = session.get(SITEMAP, timeout=TIMEOUT)
    resp.raise_for_status()
    urls = extract_sitemap(resp.text)
    if not urls:
        raise RuntimeError("Sitemap returned zero same-domain URLs; no changes made")
    pages = [inspect_page(session, url, urls) for url in urls]
    gsc_rows = []
    gsc_status = "dry_run" if args.dry_run else "not_configured"
    if not args.dry_run and os.getenv("GSC_SERVICE_ACCOUNT_JSON"):
        try:
            gsc_rows = read_gsc(session, os.environ["GSC_SERVICE_ACCOUNT_JSON"])
            gsc_status = "ok"
        except Exception as exc:
            gsc_status = "error:" + exc.__class__.__name__
    if not args.dry_run and os.getenv("DATABASE_URL"):
        save_database(os.environ["DATABASE_URL"], run_id, pages, gsc_rows, gsc_status)
        storage = "postgres"
    else:
        storage = "none"
    semrush_result = {"status": "dry_run" if args.dry_run else "disabled",
                     "requests": 0, "reserved_units": 0}
    if not args.dry_run and storage == "postgres":
        try:
            semrush_result = enrich_semrush(os.environ["DATABASE_URL"], session=session)
        except Exception as exc:
            # Never expose API key, environment values or full HTTP traces.
            semrush_result = {"status": "error:" + exc.__class__.__name__,
                             "requests": 0, "reserved_units": 0}
    report = {"run_id": str(run_id), "pages": len(pages),
              "reachable": sum(p["http_status"] == 200 for p in pages),
              "issues": sum(len(p["issues"]) for p in pages),
              "gsc_rows": len(gsc_rows), "gsc_status": gsc_status,
              "semrush": semrush_result, "storage": storage, "publishing_enabled": False,
              "page_issues": [{"url": p["url"], "issues": p["issues"]}
                              for p in pages if p["issues"]]}
    print(json.dumps(report, sort_keys=True))
    if not args.dry_run and (storage != "postgres" or gsc_status.startswith("error:")):
        return 1
    return 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        # Never print environment values, API tokens, or credentials.
        print(json.dumps({"status": "error", "error_type": exc.__class__.__name__,
                          "publishing_enabled": False}))
        sys.exit(1)
