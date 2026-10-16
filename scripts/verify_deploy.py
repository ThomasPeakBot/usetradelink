"""Verify a deployed copy of the site (production or a Cloudflare preview) over HTTP.

    python scripts/verify_deploy.py https://usetradelink.com
    python scripts/verify_deploy.py https://<id>.usetradelink.pages.dev

Read-only GET requests. Checks every sitemap page, shared assets, tracking,
security headers and the 404 page, and fails if any repository file that is
not meant to be public (automation code, scripts, docs, workflows, tests) can
be downloaded. Uses only the standard library.
"""
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_site import is_public  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
UA = "TradeLinkDeployCheck/1.0 (+https://usetradelink.com/)"
ASSETS = {"/styles.css": "text/css", "/seo.css": "text/css", "/main.js": "javascript",
          "/tracking.js": "javascript", "/assets/logo.png": "image/png", "/sitemap.xml": "xml",
          "/robots.txt": "text/plain", "/llms.txt": "text/plain"}
HEADERS = ("x-content-type-options", "referrer-policy", "x-frame-options", "strict-transport-security")


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read(2_000_000)
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in e.headers.items()}, b""


def private_paths(limit=80):
    tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout.split()
    private = [t for t in tracked if not is_public(t) and not t.startswith(".git/")]
    # Most sensitive first, then everything else up to the limit.
    private.sort(key=lambda p: (not p.startswith(("automation/", ".github/", "docs/", "scripts/")), p))
    return private[:limit]


def verify(base, check_headers=True):
    base = base.rstrip("/")
    results = []

    def record(ok, what, detail=""):
        results.append((ok, what, detail))

    with open(os.path.join(ROOT, "sitemap.xml"), encoding="utf-8") as fh:
        ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        locs = [e.text.strip() for e in ET.fromstring(fh.read()).findall("s:url/s:loc", ns)]
    for loc in locs:
        path = re.sub(r"^https://usetradelink\.com", "", loc) or "/"
        status, headers, body = fetch(base + path)
        ok = status == 200 and f'href="{loc}"'.encode() in body
        record(ok, f"page {path}", f"HTTP {status}" + ("" if ok or status != 200 else ", canonical missing"))
        if path == "/":
            record(b"GTM-NP74LT9B" in body, "homepage loads Google Tag Manager")
            if check_headers:
                missing = [h for h in HEADERS if h not in headers]
                record(not missing, "security headers", "missing: " + ", ".join(missing) if missing else "")
    for path, kind in ASSETS.items():
        status, headers, body = fetch(base + path)
        record(status == 200 and kind in headers.get("content-type", ""), f"asset {path}",
               f"HTTP {status} {headers.get('content-type', '')}")
    status, _, body = fetch(base + "/tracking.js")
    record(b"GTM-NP74LT9B" in body and b"tlink.peakbot.com" in body, "tracking.js content")
    status, _, body = fetch(base + "/this-page-does-not-exist-tl/")
    record(status == 404, "unknown URL returns 404", f"HTTP {status}")
    for rel in private_paths():
        status, _, body = fetch(base + "/" + rel)
        # A 200 that is just the 404 page body would still be wrong; require a non-200 status.
        record(status != 200, f"private file not served: /{rel}", f"HTTP {status}")
    return results


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    base = argv[1]
    results = verify(base, check_headers="--no-headers" not in argv)
    failed = [r for r in results if not r[0]]
    lines = [f"### Deployment check: {base}", "", f"{len(results) - len(failed)}/{len(results)} checks passed", ""]
    lines += [f"- ❌ {what} {detail}".rstrip() for ok, what, detail in failed]
    if not failed:
        lines.append("- ✅ all checks passed")
    report = "\n".join(lines)
    print(report)
    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(report + "\n")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
