"""Record published changes and measure them against Search Console, without overreacting.

A change is evaluated once at least `window + delay` days have passed, comparing
the 28 days before publication with 28 days starting a week after it (Google's
reporting lag and re-crawl time). Small samples are labelled, not judged.
"""
import json
from datetime import timedelta

from .db import table_exists

SITE = "https://usetradelink.com/"


def path_to_url(path):
    if path in ("sitemap.xml", "llms.txt"):
        return None
    return SITE + (path[:-len("index.html")] if path.endswith("index.html") else path)


def sync_changes(conn):
    if not (table_exists(conn, "seo_approval_events") and table_exists(conn, "seo_approval_proposals")):
        return 0
    rows = conn.execute(
        "SELECT p.id, p.pr_number, e.detail, e.created_at, j.result FROM seo_approval_events e "
        "JOIN seo_approval_proposals p ON p.id=e.proposal_id "
        "LEFT JOIN seo_approval_jobs j ON j.proposal_id=p.id "
        "WHERE e.kind='published_paths' AND NOT EXISTS (SELECT 1 FROM seo_changes c WHERE c.proposal_id=p.id)").fetchall()
    added = 0
    with conn.transaction():
        for r in rows:
            pages = [u for u in (path_to_url(p) for p in json.loads(r["detail"] or "[]")) if u]
            merged = (r["result"] or "").split("github_merged:")[-1][:40] if "github_merged:" in (r["result"] or "") else None
            if pages:
                conn.execute("INSERT INTO seo_changes(proposal_id,pr_number,merged_sha,pages,published_at) "
                             "VALUES(%s,%s,%s,%s,%s) ON CONFLICT(proposal_id) DO NOTHING",
                             (r["id"], r["pr_number"], merged, pages, r["created_at"]))
                added += 1
    return added


def window_metrics(conn, pages, start, end):
    r = conn.execute("SELECT COALESCE(SUM(clicks),0) AS clicks, COALESCE(SUM(impressions),0) AS impressions, "
                     "SUM(position*impressions)/NULLIF(SUM(impressions),0) AS position "
                     "FROM seo_search_performance WHERE page = ANY(%s) AND data_date BETWEEN %s AND %s",
                     (pages, start, end)).fetchone()
    return {"clicks": float(r["clicks"]), "impressions": float(r["impressions"]),
            "position": round(float(r["position"]), 1) if r["position"] is not None else None,
            "start": start.isoformat(), "end": end.isoformat()}


def verdict(before, after, min_impressions=100):
    if before["impressions"] < min_impressions and after["impressions"] < min_impressions:
        return "insufficient_data"
    def change(a, b):
        return (b - a) / a if a else (1.0 if b else 0.0)
    clicks, imps = change(before["clicks"], after["clicks"]), change(before["impressions"], after["impressions"])
    if clicks >= 0.2 and imps >= 0.1:
        return "improved"
    if clicks <= -0.2 or imps <= -0.3:
        return "declined"
    return "no_significant_change"


def evaluate(conn, today, window=28, delay=7):
    if not (table_exists(conn, "seo_changes") and table_exists(conn, "seo_search_performance")):
        return 0
    latest = conn.execute("SELECT MAX(data_date) AS d FROM seo_search_performance").fetchone()["d"]
    if not latest:
        return 0
    done = 0
    for c in conn.execute("SELECT * FROM seo_changes c WHERE NOT EXISTS (SELECT 1 FROM seo_change_evaluations e "
                          "WHERE e.change_id=c.id AND e.window_days=%s)", (window,)).fetchall():
        published = c["published_at"].date()
        after_start = published + timedelta(days=delay)
        after_end = after_start + timedelta(days=window - 1)
        if after_end > latest or after_end > today:
            continue
        before = window_metrics(conn, c["pages"], published - timedelta(days=window), published - timedelta(days=1))
        after = window_metrics(conn, c["pages"], after_start, after_end)
        result = verdict(before, after)
        # Conversions need GA4/checkout data, which is not connected yet.
        after["conversions"] = "not_connected"
        with conn.transaction():
            conn.execute("INSERT INTO seo_change_evaluations(change_id,window_days,before,after,verdict) "
                         "VALUES(%s,%s,%s,%s,%s)", (c["id"], window, json.dumps(before), json.dumps(after), result))
        done += 1
    return done
