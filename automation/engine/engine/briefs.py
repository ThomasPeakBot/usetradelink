"""Turn an opportunity into a research brief a writer (human or model) must follow."""
import re

from .keywords import relevance, tokens
from .opportunities import coverage
from .quality import FILLER, MIN_WORDS, load_facts

ALLOWED_TOP_LEVEL = ("tradovate-", "futures-", "prop-firm-", "apex-", "best-futures-", "cloud-futures-")


def slugify(text):
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", text.lower())).strip("-")[:70]


def proposed_path(head, content_type, existing_urls, brands=()):
    slug = slugify(head)
    if content_type == "comparison":
        brand = next((b for b in brands if b in head.replace(" ", "").lower()), None)
        path = f"compare/tradelink-vs-{brand}/" if brand and "vs" not in slug else f"compare/{slug}/"
    elif slug.startswith(ALLOWED_TOP_LEVEL):
        path = f"{slug}/"
    else:
        path = f"guides/{slug}/"
    url = "https://usetradelink.com/" + path
    n = 2
    while url in existing_urls:
        url = "https://usetradelink.com/" + path.rstrip("/") + f"-{n}/"
        n += 1
    return url[len("https://usetradelink.com/"):] + "index.html", url


def related_questions(conn, keywords):
    want = set().union(*(tokens(k) for k in keywords)) if keywords else set()
    rows = conn.execute("SELECT DISTINCT r.keyword, m.search_volume FROM seo_keyword_relations r "
                        "LEFT JOIN seo_keyword_metrics m ON m.keyword=r.keyword "
                        "WHERE r.relation='question'").fetchall()
    scored = [(len(tokens(r["keyword"]) & want), r["search_volume"] or 0, r["keyword"]) for r in rows
              if relevance(r["keyword"]) >= 0.6]
    return [{"question": k, "volume": v} for o, v, k in sorted(scored, reverse=True) if o >= 2][:12]


def competitor_pages(conn, keywords):
    rows = conn.execute(
        "SELECT DISTINCT ON (r.url) r.domain, r.url, r.keyword, r.position, p.title, p.h1, p.headings, "
        "p.word_count, p.fetched_at FROM seo_competitor_rankings r LEFT JOIN LATERAL ("
        "  SELECT * FROM seo_competitor_pages cp WHERE cp.url=r.url ORDER BY fetched_at DESC LIMIT 1) p ON true "
        "WHERE r.keyword = ANY(%s) AND r.position <= 20 ORDER BY r.url, r.position", (list(keywords),)).fetchall()
    return [dict(r, fetched_at=r["fetched_at"].isoformat() if r["fetched_at"] else None) for r in rows]


def gsc_queries_for(conn, url, limit=15):
    if not url or not conn.execute("SELECT to_regclass('public.seo_search_performance') IS NOT NULL AS ok").fetchone()["ok"]:
        return []
    return [dict(r) for r in conn.execute(
        "SELECT query, SUM(impressions)::int AS impressions, SUM(clicks)::int AS clicks, "
        "ROUND((SUM(position*impressions)/NULLIF(SUM(impressions),0))::numeric,1) AS position "
        "FROM seo_search_performance WHERE page=%s AND data_date >= current_date - 35 "
        "GROUP BY query ORDER BY impressions DESC LIMIT %s", (url, limit))]


def link_plan(head, pages, target_url, n=3):
    ranked = sorted((p for p in pages if p["url"] != target_url),
                    key=lambda p: -coverage(head, p))
    return [{"url": p["url"], "title": p["title"]} for p in ranked[:n] if coverage(head, p) > 0]


def build(conn, opportunity, pages, brands=(), facts=None):
    facts = facts or load_facts()
    ctype = opportunity.get("content_type") or ("update" if opportunity.get("target_url") else "article")
    existing = {p["url"] for p in pages}
    target = opportunity.get("target_url")
    path = None
    if opportunity["action"] == "new_page":
        path, target = proposed_path(opportunity["head_keyword"], ctype, existing, brands)
    elif target:
        rel = target[len("https://usetradelink.com/"):]
        path = (rel + "index.html") if rel.endswith("/") or rel == "" else rel
    keywords = opportunity["keywords"]
    brief = {
        "opportunity_id": str(opportunity.get("id", "")),
        "action": opportunity["action"], "content_type": ctype,
        "target_url": target, "path": path,
        "head_keyword": opportunity["head_keyword"], "keywords": keywords,
        "why": opportunity["rationale"], "priority": float(opportunity["priority"]),
        "evidence": opportunity.get("evidence", []),
        "questions_people_search": related_questions(conn, keywords),
        "current_queries": gsc_queries_for(conn, opportunity.get("target_url")),
        "competitor_pages": competitor_pages(conn, keywords),
        "internal_links": link_plan(opportunity["head_keyword"], pages, target),
        "allowed_facts": [{"id": f["id"], "statement": f["statement"], "source": f["source"]}
                          for f in facts["facts"] if f["status"] == "published"],
        "blocked_facts": [{"id": f["id"], "statement": f["statement"], "status": f["status"],
                           "note": f.get("note", "")} for f in facts["facts"] if f["status"] != "published"],
        "forbidden_claims": facts["forbidden_claims"],
        "requirements": [
            (f"At least {MIN_WORDS.get(ctype, 600)} words of substance for a {ctype}; no padding." if MIN_WORDS.get(ctype)
             else "Edit the existing page: keep what is accurate, add only what the evidence calls for, and "
                  "remove anything outdated or unsourced."),
            "Answer the searcher's actual question in the first paragraph.",
            "Only state TradeLink facts from allowed_facts; never use blocked_facts.",
            "Every competitor, prop-firm or third-party figure needs a link to its primary source in the same "
            "paragraph and a visible 'Last verified <date>' line.",
            "Use competitor pages to see what searchers expect, not as text to rephrase.",
            "Add a FAQ section only for questions listed under questions_people_search or current_queries.",
            "Describe limitations honestly (e.g. Rithmic not live).",
            "No invented tests, quotes, user stories, results or author credentials.",
            "Avoid stock phrasing: " + ", ".join(p.strip("\\b") for p in FILLER[:6]) + ".",
        ],
    }
    return brief, render_markdown(brief)


def render_markdown(b):
    lines = [f"# Brief: {b['head_keyword']}", "",
             f"- Action: **{b['action']}** ({b['content_type']}) · priority {b['priority']}",
             f"- Target: {b['target_url'] or 'new page'} (`{b['path']}`)",
             f"- Why: {b['why']}", "", "## Keywords and evidence"]
    for e in b["evidence"][:20]:
        lines.append(f"- '{e.get('keyword', '')}' — {e.get('source')} ({e.get('observed_on')}): "
                     f"{e.get('metric')} = {e.get('value')}")
    if b["questions_people_search"]:
        lines += ["", "## Questions people search (provider data)"]
        lines += [f"- {q['question']} ({q['volume']}/mo)" for q in b["questions_people_search"]]
    if b["current_queries"]:
        lines += ["", "## Current Search Console queries for this page"]
        lines += [f"- {q['query']}: {q['impressions']} impressions, {q['clicks']} clicks, pos {q['position']}"
                  for q in b["current_queries"]]
    if b["competitor_pages"]:
        lines += ["", "## Pages that rank now (structure only)"]
        for c in b["competitor_pages"]:
            heads = "; ".join((c.get("headings") or [])[:8])
            lines.append(f"- #{c['position']} {c['url']} — {c.get('title') or '(not fetched)'}; "
                         f"{c.get('word_count') or '?'} words; headings: {heads or 'n/a'}")
    lines += ["", "## Internal links", *[f"- {l['title']}: {l['url']}" for l in b["internal_links"]],
              "", "## Allowed TradeLink facts", *[f"- {f['statement']} ({f['source']})" for f in b["allowed_facts"]],
              "", "## Do not use", *[f"- {f['statement']} — {f['status']}: {f['note']}" for f in b["blocked_facts"]],
              *[f"- {c}" for c in b["forbidden_claims"]],
              "", "## Requirements", *[f"- {r}" for r in b["requirements"]]]
    return "\n".join(lines) + "\n"
