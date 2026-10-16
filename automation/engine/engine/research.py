"""Collect keyword and competitor evidence from providers and public pages.

All numbers come from Semrush responses or Search Console rows; competitor
facts are limited to what their public pages and provider rankings show.
"""
import json
from datetime import date
from pathlib import Path

from .providers.semrush import (BudgetExceeded, Semrush, SemrushError, store_keyword_rows,
                                store_rankings, store_relations)

CONFIG = Path(__file__).resolve().parent.parent / "research_config.json"


def load_config(path=CONFIG):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def ensure_competitors(conn, config):
    with conn.transaction():
        for c in config["competitors"]:
            conn.execute("INSERT INTO seo_competitors(domain,name) VALUES(%s,%s) "
                         "ON CONFLICT(domain) DO UPDATE SET name=EXCLUDED.name", (c["domain"], c["name"]))


def stale_keywords(conn, keywords, days):
    fresh = {r["keyword"] for r in conn.execute(
        "SELECT DISTINCT keyword FROM seo_keyword_metrics WHERE source LIKE 'semrush%%' "
        "AND observed_on >= current_date - %s::int", (days,))}
    return [k for k in dict.fromkeys(k.lower() for k in keywords) if k not in fresh]


def recently(conn, sql, args, days):
    return conn.execute(sql + " AND observed_on >= current_date - %s::int LIMIT 1", (*args, days)).fetchone() is not None


def gsc_queries(conn, min_impressions=5, limit=100):
    if not conn.execute("SELECT to_regclass('public.seo_search_performance') IS NOT NULL AS ok").fetchone()["ok"]:
        return []
    return [r["q"] for r in conn.execute(
        "SELECT lower(trim(query)) AS q FROM seo_search_performance WHERE data_date >= current_date - 90 "
        "GROUP BY 1 HAVING SUM(impressions) >= %s ORDER BY SUM(impressions) DESC LIMIT %s",
        (min_impressions, limit))]


def run_semrush(conn, config, client=None):
    """Spend at most the configured budget; skip anything refreshed recently."""
    client = client or Semrush(conn)
    summary = {"enabled": client.enabled, "calls": 0, "skipped": [], "errors": []}
    if not client.enabled:
        summary["skipped"].append("disabled_or_no_budget")
        return summary
    days = config["refresh_days"]
    limits = config["limits"]
    from .keywords import relevance
    brands = [c["brand"] for c in config["competitors"]]
    candidates = [k for k in config["seeds"] + gsc_queries(conn) if relevance(k, brands) > 0]
    plan = []
    todo = stale_keywords(conn, candidates, days["keyword_metrics"])
    for i in range(0, len(todo), 100):
        plan.append(("phrase_these", ";".join(todo[i:i + 100]), {"phrase": ";".join(todo[i:i + 100])},
                     len(todo[i:i + 100]), "keywords", None))
    for seed in config["related_seeds"]:
        if not recently(conn, "SELECT 1 FROM seo_keyword_relations WHERE seed=%s AND relation='related'",
                        (seed,), days["relations"]):
            plan.append(("phrase_related", seed, {"phrase": seed}, limits["related"], "related", seed))
    for seed in config["question_seeds"]:
        if not recently(conn, "SELECT 1 FROM seo_keyword_relations WHERE seed=%s AND relation='question'",
                        (seed,), days["relations"]):
            plan.append(("phrase_questions", seed, {"phrase": seed}, limits["questions"], "question", seed))
    for c in config["competitors"]:
        if not recently(conn, "SELECT 1 FROM seo_competitor_rankings WHERE domain=%s", (c["domain"],),
                        days["competitor_rankings"]):
            plan.append(("domain_organic", c["domain"], {"domain": c["domain"], "display_sort": "tr_desc"},
                         limits["competitor_keywords"], "rankings", c["domain"]))
    for report, target, params, lines, kind, key in plan:
        try:
            rows = client.call(report, target, params, lines)
        except BudgetExceeded as exc:
            summary["skipped"].append(f"{report}:{target}:{exc}")
            continue
        except SemrushError as exc:
            summary["errors"].append(f"{report}:{exc.category}")
            if exc.stop:
                break
            continue
        summary["calls"] += 1
        if kind == "keywords":
            store_keyword_rows(conn, rows, "semrush-v3")
        elif kind in ("related", "question"):
            store_keyword_rows(conn, rows, "semrush-v3")
            store_relations(conn, key, rows, kind, "semrush-v3")
        else:
            store_rankings(conn, key, rows, "semrush-v3")
    return summary


def import_snapshot(conn, path):
    """Load a saved Semrush response snapshot (for example from the Semrush MCP) as evidence."""
    with open(path, encoding="utf-8") as fh:
        snap = json.load(fh)
    observed = date.fromisoformat(snap["collected_at"][:10])
    source = "semrush-mcp"
    counts = {}
    for call in snap["calls"]:
        if call.get("status") != "ok":
            continue
        rows = [{_canon(k): v for k, v in row.items()} for row in call["rows"]]
        report, params = call["report"], call.get("params", {})
        if report in ("phrase_these", "phrase_related", "phrase_questions"):
            store_keyword_rows(conn, rows, source, observed)
            if report != "phrase_these":
                store_relations(conn, params["phrase"], rows,
                                "related" if report == "phrase_related" else "question", source, observed)
        elif report in ("resource_organic", "domain_organic"):
            store_rankings(conn, params.get("target") or params.get("domain"), rows, source, observed)
        counts[report] = counts.get(report, 0) + len(rows)
    return counts


def _canon(header):
    from .providers.semrush import HEADER_FIELDS
    return HEADER_FIELDS.get(header.strip().lower(), header.strip().lower())
