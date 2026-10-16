"""Optional, read-only Semrush v4 keyword enrichment for TradeLink.

Charges API units only when SEMRUSH_ENABLED=true, a v4 key is configured,
and the database budget reservation succeeds. No site changes are possible.
"""
import datetime as dt
import os
import re
from decimal import Decimal, InvalidOperation

import psycopg
import requests

URL = "https://api.semrush.com/apis/v4/keywords/v1/metrics"
UNITS_PER_REQUEST = 20
DEFAULT_SEEDS = (
    "futures trade copier",
    "tradovate trade copier",
    "best futures trade copier",
    "prop firm trade copier",
    "cloud futures trade copier",
    "tradovate multiple accounts",
)
SQL = """
CREATE TABLE IF NOT EXISTS seo_semrush_api_usage (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    requested_at timestamptz NOT NULL DEFAULT now(),
    keyword text NOT NULL,
    country char(2) NOT NULL DEFAULT 'US',
    units_reserved integer NOT NULL,
    success boolean,
    error_category text
);
CREATE TABLE IF NOT EXISTS seo_semrush_keyword_metrics (
    keyword text NOT NULL,
    country char(2) NOT NULL,
    collected_on date NOT NULL,
    search_volume integer,
    keyword_difficulty numeric,
    cpc_usd numeric,
    intents text[] NOT NULL DEFAULT '{}',
    competitive_density numeric,
    source text NOT NULL DEFAULT 'semrush-v4',
    PRIMARY KEY(keyword,country,collected_on)
);
"""
RELEVANT = re.compile(r"\b(futures?|tradovate|trade copier|prop firms?|copy trading)\b", re.I)
EXCLUDE = re.compile(r"\b(forex|mt4|mt5|crypto)\b", re.I)


def positive_limited(name, default, ceiling):
    try:
        return min(max(0, int(os.getenv(name, str(default)))), ceiling)
    except (ValueError, OverflowError):
        return 0  # malformed cap is fail-closed

def candidates(cur):
    # Search Console may be empty just after the property was verified.
    cur.execute("""
        SELECT lower(trim(query)) AS keyword, SUM(impressions) AS impressions
        FROM seo_search_performance
        WHERE data_date >= current_date - 45
        GROUP BY lower(trim(query))
        HAVING SUM(impressions) >= 5
        ORDER BY SUM(impressions) DESC LIMIT 50
    """)
    dynamic = [r[0] for r in cur.fetchall()]
    seeded = [s.strip().lower() for s in
              os.getenv("SEMRUSH_SEED_KEYWORDS", ",".join(DEFAULT_SEEDS)).split(",")]
    all_terms = []
    for term in dynamic + seeded:
        if (term and len(term) <= 255 and RELEVANT.search(term)
                and not EXCLUDE.search(term) and term not in all_terms):
            all_terms.append(term)
    return all_terms[:50]

def parse_metrics(payload):
    if not isinstance(payload, dict) or not payload.get("meta", {}).get("success", False):
        raise ValueError("semrush_bad_response")
    data = payload.get("data", {})
    if not isinstance(data, dict) or "search_volume" not in data:
        raise ValueError("semrush_missing_metrics")

    def num(value):
        if value is None or value == "":
            return None
        try:
            n = Decimal(str(value))
            return n if n.is_finite() else None
        except (InvalidOperation, ValueError):
            return None

    volume = num(data.get("search_volume"))
    kd = num(data.get("keyword_difficulty"))
    cents = num(data.get("cpc"))
    density = num(data.get("competitive_density"))
    intents = data.get("intents") or []
    if not isinstance(intents, list):
        intents = []
    return {"volume": int(volume) if volume is not None else None,
            "difficulty": kd, "cpc_usd": cents / 100 if cents is not None else None,
            "density": density, "intents": [str(v)[:30] for v in intents[:8]]}

def enrich(database_url, session=None):
    """Return a summary; preserve audit data even if provider calls fail."""
    if os.getenv("SEMRUSH_ENABLED", "false").lower() != "true":
        return {"status": "disabled", "requests": 0, "reserved_units": 0}
    api_key = os.getenv("SEMRUSH_API_KEY", "").strip()
    if not api_key:
        return {"status": "missing_key", "requests": 0, "reserved_units": 0}
    run_cap = positive_limited("SEMRUSH_MAX_UNITS_PER_RUN", 120, 400)
    month_cap = positive_limited("SEMRUSH_MAX_UNITS_PER_MONTH", 600, 2000)
    if run_cap < UNITS_PER_REQUEST or month_cap < UNITS_PER_REQUEST:
        return {"status": "budget_disabled", "requests": 0, "reserved_units": 0}

    session = session or requests.Session()
    count = 0
    failed = 0
    reserved = 0
    status = "ok"
    with psycopg.connect(database_url, connect_timeout=10) as conn:
        # A single global database lock prevents concurrent cron runs spending twice.
        cur = conn.cursor()
        cur.execute("SELECT pg_try_advisory_lock(915202610)")
        if not cur.fetchone()[0]:
            return {"status": "another_run_active", "requests": 0, "reserved_units": 0}
        try:
            cur.execute(SQL)
            conn.commit()
            available = candidates(cur)
            country = "US"
            for phrase in available:
                if reserved + UNITS_PER_REQUEST > run_cap:
                    status = "run_budget_reached"
                    break
                cur.execute("""
                    SELECT COALESCE(SUM(units_reserved),0)
                    FROM seo_semrush_api_usage
                    WHERE requested_at >= date_trunc('month', now())
                      AND requested_at < date_trunc('month', now()) + interval '1 month'
                """)
                used_month = cur.fetchone()[0]
                if used_month + UNITS_PER_REQUEST > month_cap:
                    status = "monthly_budget_reached"
                    break
                # Do not continuously spend units on unchanged keyword metrics.
                cur.execute("""
                    SELECT 1 FROM seo_semrush_keyword_metrics
                    WHERE keyword=%s AND country=%s AND collected_on >= current_date - 27 LIMIT 1
                """, (phrase, country))
                if cur.fetchone():
                    continue
                # Reserve unit costs even for failed attempts (Semrush can bill before errors).
                cur.execute("""
                    INSERT INTO seo_semrush_api_usage(keyword,country,units_reserved)
                    VALUES(%s,%s,%s) RETURNING id
                """, (phrase, country, UNITS_PER_REQUEST))
                usage_id = cur.fetchone()[0]
                conn.commit()
                reserved += UNITS_PER_REQUEST
                count += 1
                error_category = None
                success = False
                try:
                    response = session.get(URL, params={"keyword": phrase, "country": country,
                                                         "format": "json"},
                                           headers={"Authorization": "Apikey " + api_key},
                                           timeout=12)
                    response.raise_for_status()
                    value = parse_metrics(response.json())
                    cur.execute("""
                        INSERT INTO seo_semrush_keyword_metrics
                            (keyword,country,collected_on,search_volume,keyword_difficulty,
                             cpc_usd,intents,competitive_density)
                        VALUES(%s,%s,current_date,%s,%s,%s,%s,%s)
                        ON CONFLICT(keyword,country,collected_on) DO UPDATE SET
                            search_volume=EXCLUDED.search_volume,
                            keyword_difficulty=EXCLUDED.keyword_difficulty,
                            cpc_usd=EXCLUDED.cpc_usd,
                            intents=EXCLUDED.intents,
                            competitive_density=EXCLUDED.competitive_density
                    """, (phrase, country, value["volume"], value["difficulty"],
                          value["cpc_usd"], value["intents"], value["density"]))
                    success = True
                except requests.HTTPError as exc:
                    code = getattr(exc.response, "status_code", None)
                    error_category = "http_" + str(code)
                except (requests.RequestException, ValueError, TypeError):
                    error_category = "response_error"
                if not success:
                    failed += 1
                cur.execute("""
                    UPDATE seo_semrush_api_usage
                    SET success=%s,error_category=%s WHERE id=%s
                """, (success, error_category, usage_id))
                conn.commit()
                # Stop on authentication/quota errors; no retry and no token output.
                if error_category in ("http_401", "http_403", "http_402", "http_429"):
                    status = error_category
                    break
        finally:
            # A failed statement aborts the transaction; roll back first so the
            # unlock runs and the original error is not masked.
            conn.rollback()
            cur.execute("SELECT pg_advisory_unlock(915202610)")
            conn.commit()
    return {"status": status, "requests": count, "failed": failed,
            "reserved_units": reserved, "country": "US",
            "publishing_enabled": False}
