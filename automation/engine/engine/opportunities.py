"""Decide what, if anything, to do for each keyword cluster.

Everything here is a pure function of the evidence passed in, so a decision can
be reproduced and audited from the stored evidence. No input means no action:
the engine never invents demand, rankings or competitor coverage.
"""
import hashlib
import math
from datetime import date, timedelta

from .keywords import cluster, content_type_for, intent, normalize, relevance, tokens

HOME = "https://usetradelink.com/"
# Words every page on this site shares; a page must also carry a query's other words to own it.
GENERIC = {"trade", "copy", "futures", "account", "tradelink", "best", "how", "software", "tool"}
# Firms and platforms that narrow a page's intent (an "Apex Tradovate" guide vs a general Tradovate one).
QUALIFIERS = {"apex", "topstep", "tradeify", "lucid", "ninjatrader", "rithmic", "tradingview", "tradesyncer",
              "tradecopia"}
# Heuristic organic CTR by position, used only to flag unusually low CTR.
EXPECTED_CTR = {1: .28, 2: .15, 3: .11, 4: .08, 5: .06, 6: .05, 7: .04, 8: .03, 9: .03, 10: .025}
INTENT_WEIGHT = {"transactional": 1.0, "commercial": 1.0, "informational": 0.6, "navigational": 0.2}
ACTION_WEIGHT = {"rewrite_title_meta": 1.0, "improve_page": 0.9, "consolidate_cannibalization": 0.9,
                 "new_page": 0.8, "differentiate_pages": 0.6, "refresh_outdated": 0.7,
                 "add_internal_links": 0.6}
DEFAULTS = {
    "cooling_off_days": 28,       # do not touch a page again this soon after a change
    "min_new_page_volume": 20,    # provider monthly volume
    "min_gsc_impressions": 50,    # over the GSC window
    "min_ctr_impressions": 100,
    "min_relevance": 0.6,
    "outdated_days": 120,
    "min_inbound_links": 3,
    "owner_coverage": 0.6,
}


def fingerprint(action, head, target):
    return hashlib.sha256(f"{action}|{normalize(head)}|{target or ''}".encode()).hexdigest()[:24]


def demand_score(volume, impressions):
    demand = max(volume or 0, impressions or 0)
    return min(1.0, math.log10(1 + demand) / 3)


def winnability(position, kd):
    if position:
        if position <= 3:
            return 0.3
        if position <= 10:
            return 1.0
        if position <= 20:
            return 0.8
        if position <= 50:
            return 0.5
        return 0.3
    if kd is None:
        return 0.5
    return max(0.1, 1 - float(kd) / 100)


def page_tokens(page):
    return {"title": tokens(page.get("title", "")), "h1": tokens(page.get("h1", "")),
            "headings": set().union(*(tokens(h) for h in page.get("headings", []))) if page.get("headings") else set()}


def coverage(head, page):
    """Share of the head keyword's tokens present in the page's title, H1 and headings."""
    t = tokens(head)
    if not t:
        return 0.0
    pt = page_tokens(page)
    strong = pt["title"] | pt["h1"]
    score = sum(1.0 if w in strong else 0.5 if w in pt["headings"] else 0 for w in t)
    return score / len(t)


def answers(keyword, page):
    """True when one heading (or the title/H1) carries nearly all of the keyword's words."""
    t = tokens(keyword)
    if not t:
        return True
    for text in [page.get("title", ""), page.get("h1", "")] + list(page.get("headings") or []):
        if len(t & tokens(text)) / len(t) >= 0.8:
            return True
    return False


def aggregate(cluster_keywords, signals):
    """Combine evidence for a cluster. Volume = head-term style max, GSC = sums."""
    volume = None
    kd_values = []
    impressions = clicks = 0
    weighted_pos = 0.0
    pages = {}
    competitors = []
    intents = []
    evidence = []
    for kw in cluster_keywords:
        s = signals.get(kw, {})
        if s.get("volume") is not None:
            volume = max(volume or 0, s["volume"])
        if s.get("kd") is not None:
            kd_values.append(float(s["kd"]))
        intents += list(s.get("intents") or [])
        g = s.get("gsc") or {}
        if g.get("impressions"):
            impressions += g["impressions"]
            clicks += g.get("clicks", 0)
            weighted_pos += (g.get("position") or 0) * g["impressions"]
            for url, imp in (g.get("pages") or {}).items():
                pages[url] = pages.get(url, 0) + imp
        competitors += s.get("competitors") or []
        evidence += s.get("evidence") or []
    return {"volume": volume, "kd": max(kd_values) if kd_values else None, "impressions": impressions,
            "clicks": clicks, "position": (weighted_pos / impressions) if impressions else None,
            "pages": pages, "competitors": competitors, "intents": intents, "evidence": evidence}


def owner_for(head, agg, pages, min_coverage):
    if agg["pages"]:
        return max(agg["pages"], key=agg["pages"].get), "gsc"
    def score(p):
        # A page whose URL slug names the topic is the intended owner; the homepage is the fallback.
        slug = tokens(p["url"].replace("https://usetradelink.com", "").replace("/", " ").replace("-", " "))
        return coverage(head, p) + (0.5 if tokens(head) and tokens(head) <= slug else 0) - \
            (0.2 if p["url"] == HOME else 0)
    specific = tokens(head) - GENERIC
    candidates = [p for p in pages if specific <= set().union(*page_tokens(p).values())]
    best = max(candidates, key=score, default=None)
    if best and coverage(head, best) >= min_coverage:
        return best["url"], "on_page_match"
    return None, None


def inbound_counts(pages):
    counts = {p["url"]: 0 for p in pages}
    for p in pages:
        for link in set(p.get("internal_links") or []):
            if link in counts and link != p["url"]:
                counts[link] += 1
    return counts


def build(signals, pages, competitor_brands=(), recent_changes=None, today=None, config=None):
    """Return a list of opportunity dicts, highest priority first, plus no-action decisions.

    signals: {keyword: {"volume", "kd", "intents", "gsc": {"impressions", "clicks", "position",
              "pages": {url: impressions}}, "competitors": [{"domain", "position", "url"}],
              "evidence": [{"source", "metric", "value", "observed_on"}]}}
    pages: [{"url", "title", "h1", "headings", "internal_links", "date_modified"}]
    recent_changes: {url: date of last published change}
    """
    cfg = dict(DEFAULTS, **(config or {}))
    today = today or date.today()
    recent_changes = recent_changes or {}
    cooling = today - timedelta(days=cfg["cooling_off_days"])
    brands = tuple(b.lower() for b in competitor_brands)
    page_by_url = {p["url"]: p for p in pages}
    inbound = inbound_counts(pages)

    rel = {k: relevance(k, brands) for k in signals}
    weight = {k: max(signals[k].get("volume") or 0, (signals[k].get("gsc") or {}).get("impressions") or 0)
              for k in signals}
    relevant = [k for k in signals if rel[k] > 0]
    results = []

    def emit(action, head, kws, target, agg, why, content_type=None, extra_conf=1.0):
        intent_label = intent(head, agg["intents"])
        confidence = (1.0 if agg["impressions"] else 0.7 if agg["volume"] is not None else 0.4) * extra_conf
        parts = {
            "relevance": round(rel.get(head, 0), 2),
            "demand": round(demand_score(agg["volume"], agg["impressions"]), 3),
            "winnability": round(winnability(agg["position"], agg["kd"]), 2),
            "intent": intent_label, "intent_weight": INTENT_WEIGHT[intent_label],
            "confidence": round(confidence, 2), "action_weight": ACTION_WEIGHT.get(action, 0),
        }
        priority = 100 * parts["relevance"] * parts["demand"] * parts["winnability"] * \
            parts["intent_weight"] * parts["confidence"] * parts["action_weight"]
        changed = recent_changes.get(target) if target else None
        if changed and changed >= cooling and action != "no_action":
            why = (f"Would be {action}, but {target} changed on {changed.isoformat()}; waiting until "
                   f"{(changed + timedelta(days=cfg['cooling_off_days'])).isoformat()} to measure that change.")
            action, priority = "no_action", 0.0
        results.append({
            "fingerprint": fingerprint(action, head, target), "action": action,
            "content_type": content_type, "target_url": target, "head_keyword": head,
            "keywords": sorted(kws), "priority": round(priority, 1), "score": parts,
            "metrics": {k: agg[k] for k in ("volume", "kd", "impressions", "clicks", "position")},
            "evidence": agg["evidence"][:40], "rationale": why,
        })

    # Cluster within relevance tiers so a generic head ("copy trading") never borrows the
    # relevance of a specific variant ("copy trading futures").
    tiers = {}
    for k in relevant:
        tiers.setdefault(rel[k], []).append(k)
    clusters = [c for tier in sorted(tiers, reverse=True) for c in cluster(tiers[tier], weight)]
    for c in clusters:
        head, kws = c["head"], c["keywords"]
        agg = aggregate(kws, signals)
        r = rel[head]
        if agg["volume"] is None and not agg["impressions"]:
            emit("no_action", head, kws, None, agg, "No measured demand from Search Console or Semrush.")
            continue
        owner, how = owner_for(head, agg, pages, cfg["owner_coverage"])

        # Several of our pages split the same queries.
        if len(agg["pages"]) >= 2 and agg["impressions"] >= cfg["min_gsc_impressions"]:
            shares = {u: i / agg["impressions"] for u, i in agg["pages"].items()}
            split = [u for u, s in shares.items() if s >= 0.2]
            if len(split) >= 2:
                emit("consolidate_cannibalization", head, kws, owner, agg,
                     f"{len(split)} pages each get at least 20% of {agg['impressions']} impressions for this "
                     f"cluster: {', '.join(sorted(split))}. Pick one primary page and differentiate or merge the others.")
                continue

        if owner:
            pos, imp = agg["position"], agg["impressions"]
            if pos and pos <= 10 and imp >= cfg["min_ctr_impressions"]:
                ctr = agg["clicks"] / imp
                expected = EXPECTED_CTR.get(max(1, round(pos)), 0.025)
                if ctr < 0.6 * expected:
                    emit("rewrite_title_meta", head, kws, owner, agg,
                         f"Average position {pos:.1f} but CTR {ctr:.1%} vs about {expected:.0%} typical; "
                         f"title/description may not match the query.")
                    continue
            if pos and 4 <= pos <= 20 and imp >= cfg["min_gsc_impressions"]:
                emit("improve_page", head, kws, owner, agg,
                     f"{owner} ranks at average position {pos:.1f} with {imp} impressions; page-two/low "
                     f"page-one queries usually move with better coverage of the intent.")
                continue
            if (how == "on_page_match" and not imp and r >= cfg["min_relevance"]
                    and (agg["volume"] or 0) >= cfg["min_new_page_volume"] and owner in page_by_url
                    and not answers(head, page_by_url[owner])):
                emit("improve_page", head, kws, owner, agg,
                     f"{owner} is the closest page but no heading answers '{head}' "
                     f"({agg['volume']} searches/month). Add a section that answers it directly.",
                     extra_conf=0.8)
                continue
            if how == "on_page_match" and agg["volume"] is not None and not imp:
                comp = sorted({c["domain"] for c in agg["competitors"] if (c.get("position") or 99) <= 10})
                if comp and r >= cfg["min_relevance"] and agg["volume"] >= cfg["min_new_page_volume"]:
                    emit("improve_page", head, kws, owner, agg,
                         f"{owner} targets this topic but has no Search Console impressions for it yet, while "
                         f"{', '.join(comp)} rank in the top 10. Strengthen the existing page before adding another.",
                         extra_conf=0.8)
                    continue
            p = page_by_url.get(owner)
            if p and imp >= cfg["min_gsc_impressions"] and inbound.get(owner, 0) < cfg["min_inbound_links"]:
                emit("add_internal_links", head, kws, owner, agg,
                     f"{owner} earns {imp} impressions but only {inbound.get(owner, 0)} internal pages link to it.")
                continue
            emit("no_action", head, kws, owner, agg, f"{owner} already covers this cluster; no signal of underperformance.")
            continue

        # No page of ours covers the cluster.
        ctype = content_type_for(head, brands)
        volume = agg["volume"] or 0
        if r < cfg["min_relevance"]:
            emit("no_action", head, kws, None, agg,
                 "Mostly navigational or loosely related to TradeLink; not worth a dedicated page.")
        elif volume >= cfg["min_new_page_volume"] or agg["impressions"] >= cfg["min_gsc_impressions"]:
            comp = sorted({c["domain"] for c in agg["competitors"] if (c.get("position") or 99) <= 10})
            why = f"No TradeLink page targets this cluster. Demand: {volume or 'n/a'} monthly searches"
            if agg["impressions"]:
                why += f", {agg['impressions']} Search Console impressions"
            why += "."
            if comp:
                why += f" Ranking competitors: {', '.join(comp)}."
            emit("new_page", head, kws, None, agg, why, content_type=ctype, extra_conf=1.0 if comp else 0.85)
        else:
            emit("no_action", head, kws, None, agg,
                 f"Demand below threshold ({volume} searches/month, {agg['impressions']} impressions).")

    results += differentiate_overlaps(pages, signals, rel, weight, cfg)
    results += refresh_outdated(pages, today, cfg)
    results.sort(key=lambda o: (-o["priority"], o["head_keyword"]))
    return results


def differentiate_overlaps(pages, signals, rel, weight, cfg):
    """Two of our pages strongly target the same head term: keyword cannibalisation risk."""
    out = []
    seen = set()
    by_url = {p["url"]: p for p in pages}
    for kw in sorted((k for k in signals if rel.get(k, 0) >= cfg["min_relevance"]),
                     key=lambda k: -weight.get(k, 0)):
        if len(tokens(kw)) < 3:
            continue  # two-word heads like "copy trading" are the site's topic, not one page's intent
        want = tokens(kw)
        # A page whose title/H1 names a firm or platform the query doesn't (an Apex guide for a
        # general Tradovate query) serves a narrower intent; it isn't competing for this query.
        strong = sorted(p["url"] for p in pages if coverage(kw, p) >= 0.99 and p["url"] != HOME
                        and not ((page_tokens(p)["title"] | page_tokens(p)["h1"]) & QUALIFIERS) - want)
        # Point at the page with a section that answers the query, then the most focused title;
        # the others get differentiated.
        strong.sort(key=lambda u: (not any(want <= tokens(h) for h in by_url[u].get("headings") or []),
                                   len(page_tokens(by_url[u])["title"] - want), u))
        key = tuple(sorted(strong))
        # Matching most of the site means a shared topic word, not two pages competing.
        if 2 <= len(strong) <= max(2, len(pages) // 3) and key not in seen:
            seen.add(key)
            agg = aggregate([kw], signals)
            out.append({
                "fingerprint": fingerprint("differentiate_pages", kw, "|".join(key)),
                "action": "differentiate_pages", "content_type": None, "target_url": strong[0],
                "head_keyword": kw, "keywords": [kw],
                "priority": round(100 * demand_score(agg["volume"], agg["impressions"]) * 0.5 * 0.6, 1),
                "score": {"pages": strong}, "metrics": {k: agg[k] for k in ("volume", "kd", "impressions", "clicks", "position")},
                "evidence": agg["evidence"][:10],
                "rationale": f"{len(strong)} pages put every word of '{kw}' in their title/H1: {', '.join(strong)}. "
                             "Give each a distinct intent or merge them.",
            })
    return out


def refresh_outdated(pages, today, cfg):
    out = []
    for p in pages:
        modified = p.get("date_modified")
        url = p["url"]
        if not modified or not any(s in url for s in ("/compare/", "best-", "prop-firm", "apex-")):
            continue
        age = (today - modified).days
        if age >= cfg["outdated_days"]:
            out.append({
                "fingerprint": fingerprint("refresh_outdated", url, url), "action": "refresh_outdated",
                "content_type": None, "target_url": url, "head_keyword": p.get("title") or url,
                "keywords": [], "priority": round(min(40.0, age / 10), 1), "score": {"age_days": age},
                "metrics": {}, "evidence": [{"source": "page", "metric": "dateModified", "value": modified.isoformat()}],
                "rationale": f"Third-party pricing/rules on this page were last updated {age} days ago; re-verify every competitor or prop-firm claim.",
            })
    return out
