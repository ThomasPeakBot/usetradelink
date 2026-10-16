"""Load stored evidence into per-keyword signals with provenance."""
from datetime import date

from .db import table_exists


def _f(v):
    return float(v) if v is not None else None


def keyword_metrics(conn):
    """Latest provider metrics per keyword. Engine (v3/MCP) data first, then the collector's v4 table."""
    out = {}
    if table_exists(conn, "seo_keyword_metrics"):
        for r in conn.execute(
                "SELECT DISTINCT ON (keyword) keyword,source,observed_on,search_volume,keyword_difficulty,"
                "cpc,cpc_unit,intents FROM seo_keyword_metrics ORDER BY keyword, observed_on DESC, source"):
            out[r["keyword"]] = r
    if table_exists(conn, "seo_semrush_keyword_metrics"):
        for r in conn.execute(
                "SELECT DISTINCT ON (keyword) keyword,'semrush-v4' AS source,collected_on AS observed_on,"
                "search_volume,keyword_difficulty,cpc_usd AS cpc,'USD' AS cpc_unit,intents "
                "FROM seo_semrush_keyword_metrics ORDER BY keyword, collected_on DESC"):
            current = out.get(r["keyword"])
            if not current or r["observed_on"] > current["observed_on"]:
                out[r["keyword"]] = r
    return out


def gsc(conn, days=28):
    """Search Console rows for the latest `days` of finalised data, per query."""
    if not table_exists(conn, "seo_search_performance"):
        return {}, None
    end = conn.execute("SELECT MAX(data_date) AS d FROM seo_search_performance").fetchone()["d"]
    if not end:
        return {}, None
    out = {}
    for r in conn.execute(
            "SELECT lower(trim(query)) AS q, page, SUM(impressions) AS imp, SUM(clicks) AS clk, "
            "SUM(position*impressions) AS wpos FROM seo_search_performance "
            "WHERE data_date > %s::date - %s::int GROUP BY 1,2", (end, days)):
        g = out.setdefault(r["q"], {"impressions": 0, "clicks": 0, "wpos": 0.0, "pages": {}})
        g["impressions"] += int(r["imp"])
        g["clicks"] += int(r["clk"])
        g["wpos"] += float(r["wpos"])
        g["pages"][r["page"]] = g["pages"].get(r["page"], 0) + int(r["imp"])
    for g in out.values():
        g["position"] = g.pop("wpos") / g["impressions"] if g["impressions"] else None
    return out, end


def competitor_rankings(conn):
    if not table_exists(conn, "seo_competitor_rankings"):
        return {}
    out = {}
    for r in conn.execute(
            "SELECT DISTINCT ON (domain, keyword) domain,keyword,position,url,traffic,search_volume,source,"
            "observed_on FROM seo_competitor_rankings ORDER BY domain, keyword, observed_on DESC"):
        out.setdefault(r["keyword"], []).append(r)
    return out


def recent_changes(conn):
    if not table_exists(conn, "seo_changes"):
        return {}
    out = {}
    for r in conn.execute("SELECT unnest(pages) AS page, MAX(published_at) AS at FROM seo_changes GROUP BY 1"):
        out[r["page"]] = r["at"].date()
    return out


def build_signals(conn, days=28):
    metrics = keyword_metrics(conn)
    queries, gsc_end = gsc(conn, days)
    ranks = competitor_rankings(conn)
    signals = {}
    for kw in set(metrics) | set(queries) | set(ranks):
        m, g, comp = metrics.get(kw), queries.get(kw), ranks.get(kw, [])
        evidence = []
        s = {"volume": None, "kd": None, "intents": [], "gsc": None, "competitors": [], "evidence": evidence}
        if m:
            kd = _f(m["keyword_difficulty"])
            # Semrush reports KD 0 when it lacks data; treat it as unknown rather than "easy".
            kd = kd if kd else None
            s.update(volume=m["search_volume"], kd=kd, intents=list(m["intents"] or []))
            evidence.append({"keyword": kw, "source": m["source"], "observed_on": m["observed_on"].isoformat(),
                             "metric": "volume/kd/cpc",
                             "value": {"volume": m["search_volume"], "kd": _f(m["keyword_difficulty"]),
                                       "cpc": _f(m["cpc"]), "cpc_unit": m["cpc_unit"]}})
        if g:
            s["gsc"] = g
            evidence.append({"keyword": kw, "source": "google-search-console", "observed_on": gsc_end.isoformat(),
                             "metric": f"last {days} days",
                             "value": {"impressions": g["impressions"], "clicks": g["clicks"],
                                       "position": round(g["position"], 1) if g["position"] else None,
                                       "pages": g["pages"]}})
        for c in comp:
            s["competitors"].append({"domain": c["domain"], "position": c["position"], "url": c["url"]})
            evidence.append({"keyword": kw, "source": c["source"], "observed_on": c["observed_on"].isoformat(),
                             "metric": "competitor_rank",
                             "value": {"domain": c["domain"], "position": c["position"], "url": c["url"],
                                       "traffic": _f(c["traffic"])}})
            if s["volume"] is None and c["search_volume"] is not None:
                s["volume"] = c["search_volume"]
        signals[kw] = s
    return signals


def latest_inventory(conn):
    if not table_exists(conn, "seo_page_inventory"):
        return []
    rows = conn.execute("SELECT DISTINCT ON (url) url,title,meta_description,h1,headings,word_count,"
                        "internal_links,date_modified FROM seo_page_inventory "
                        "ORDER BY url, inventoried_at DESC").fetchall()
    return [dict(r) for r in rows]


def today():
    return date.today()
