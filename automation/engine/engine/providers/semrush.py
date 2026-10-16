"""Budgeted Semrush Analytics (v3) client for keyword and competitor research.

Every call reserves its worst-case unit cost in engine_api_usage *before* the
request, refuses to run past the per-run or monthly cap (which also counts the
collector's v4 usage), and records the provider-reported units when the
balance endpoint is available. Nothing runs unless ENGINE_SEMRUSH_ENABLED=true
and both caps are set explicitly; defaults are zero.
"""
import csv
import io
import os
from datetime import date

import requests

API = "https://api.semrush.com/"
BALANCE = "https://www.semrush.com/users/countapiunits.html"
# Worst-case units per returned line. Derived from usage Semrush reported for
# these reports on 2026-10-16 (phrase_these 120/12 rows, phrase_related 1850/20,
# phrase_questions 1650/15, organic 1650/25 and 200/20); revisit if pricing changes.
UNITS_PER_LINE = {"phrase_these": 10, "phrase_related": 95, "phrase_questions": 110,
                  "domain_organic": 70}
HARD_RUN_CEILING = 3000
HARD_MONTH_CEILING = 10000
HARD_CALL_CEILING = 500
COLUMNS = {
    "phrase_these": "Ph,Nq,Cp,Co,Nr,In,Kd",
    "phrase_related": "Ph,Nq,Cp,Co,Nr,Rr,In,Kd",
    "phrase_questions": "Ph,Nq,Cp,Co,Nr,In,Kd",
    "domain_organic": "Ph,Po,Nq,Cp,Ur,Tr,Kd,In",
}
HEADER_FIELDS = {
    "keyword": "keyword", "search volume": "volume", "cpc": "cpc", "competition": "competition",
    "keyword difficulty index": "kd", "keyword difficulty": "kd", "intent": "intents",
    "intents": "intents", "position": "position", "url": "url", "traffic": "traffic",
    "traffic (%)": "traffic_share", "related relevance": "relevance",
    "number of results": "results", "trends": "trends",
}
STOP_ERRORS = ("API UNITS BALANCE IS ZERO", "LIMIT EXCEEDED", "WRONG KEY", "QUERY TYPE NOT FOUND",
               "ACCESS DENIED", "API KEY")


class SemrushError(Exception):
    """Provider refused the request; `stop` means do not try further calls this run."""
    def __init__(self, category, stop):
        super().__init__(category)
        self.category = category
        self.stop = stop


class BudgetExceeded(Exception):
    pass


def capped(name, ceiling):
    try:
        return min(max(0, int(os.getenv(name, "0"))), ceiling)
    except ValueError:
        return 0


def parse_csv(text):
    """Semicolon CSV with a header row -> list of dicts keyed by canonical field names."""
    reader = csv.reader(io.StringIO(text.strip()), delimiter=";")
    rows = list(reader)
    if not rows:
        return []
    keys = [HEADER_FIELDS.get(h.strip().lower(), h.strip().lower()) for h in rows[0]]
    return [dict(zip(keys, r)) for r in rows[1:] if any(cell.strip() for cell in r)]


def to_number(value, kind=float):
    try:
        return kind(float(value)) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def intents(value):
    return [v.strip() for v in str(value or "").split(",") if v.strip()]


class Semrush:
    def __init__(self, conn, session=None, key=None):
        self.conn = conn
        self.session = session or requests.Session()
        self.key = key if key is not None else os.getenv("SEMRUSH_API_KEY", "").strip()
        self.run_cap = capped("ENGINE_SEMRUSH_MAX_UNITS_PER_RUN", HARD_RUN_CEILING)
        self.month_cap = capped("ENGINE_SEMRUSH_MAX_UNITS_PER_MONTH", HARD_MONTH_CEILING)
        # Largest single report the engine may buy; expensive reports beyond this are refused.
        self.call_cap = capped("ENGINE_SEMRUSH_MAX_UNITS_PER_CALL", HARD_CALL_CEILING)
        self.reserved = 0
        self.stopped = None

    @property
    def enabled(self):
        return (os.getenv("ENGINE_SEMRUSH_ENABLED", "false").lower() == "true" and bool(self.key)
                and os.getenv("SEMRUSH_KILL_SWITCH", "").lower() != "true"
                and self.run_cap > 0 and self.month_cap > 0 and self.call_cap > 0 and not self.paused())

    def paused(self):
        if not self.conn.execute("SELECT to_regclass('public.seo_spend_controls') IS NOT NULL AS ok").fetchone()["ok"]:
            return False
        row = self.conn.execute("SELECT paused FROM seo_spend_controls WHERE provider='semrush'").fetchone()
        return bool(row and row["paused"])

    def pause(self, reason):
        with self.conn.transaction():
            self.conn.execute("INSERT INTO seo_spend_controls(provider,paused,reason,updated_at) "
                              "VALUES('semrush',true,%s,now()) ON CONFLICT(provider) DO UPDATE SET "
                              "paused=true,reason=EXCLUDED.reason,updated_at=now()", (reason[:300],))

    def month_used(self):
        used = self.conn.execute(
            "SELECT COALESCE(SUM(GREATEST(units_reserved, COALESCE(units_reported, 0))),0) AS u "
            "FROM engine_api_usage WHERE provider='semrush' AND requested_at >= date_trunc('month', now())"
        ).fetchone()["u"]
        if self.conn.execute("SELECT to_regclass('public.seo_semrush_api_usage') IS NOT NULL AS ok").fetchone()["ok"]:
            has_reported = self.conn.execute(
                "SELECT 1 FROM information_schema.columns WHERE table_name='seo_semrush_api_usage' "
                "AND column_name='units_reported'").fetchone()
            expr = "GREATEST(units_reserved, COALESCE(units_reported, 0))" if has_reported else "units_reserved"
            used += self.conn.execute(
                f"SELECT COALESCE(SUM({expr}),0) AS u FROM seo_semrush_api_usage "
                "WHERE requested_at >= date_trunc('month', now())").fetchone()["u"]
        return int(used)

    def affordable_lines(self, report, wanted):
        per = UNITS_PER_LINE[report]
        left = min(self.run_cap - self.reserved, self.month_cap - self.month_used(), self.call_cap)
        return max(0, min(wanted, left // per))

    def balance(self):
        """Remaining API units, from Semrush's free balance endpoint; None if unavailable."""
        try:
            r = self.session.get(BALANCE, params={"key": self.key}, timeout=10)
            return int(r.text.strip()) if r.status_code == 200 and r.text.strip().isdigit() else None
        except requests.RequestException:
            return None

    def call(self, report, target, params, lines):
        if not self.enabled:
            raise BudgetExceeded("semrush_disabled")
        if self.stopped:
            raise SemrushError(self.stopped, True)
        lines = self.affordable_lines(report, lines)
        if lines <= 0:
            raise BudgetExceeded("semrush_budget_reached")
        reserve = lines * UNITS_PER_LINE[report]
        with self.conn.transaction():
            usage_id = self.conn.execute(
                "INSERT INTO engine_api_usage(provider,report,target,units_reserved) "
                "VALUES('semrush',%s,%s,%s) RETURNING id", (report, target[:200], reserve)).fetchone()["id"]
        self.reserved += reserve
        before = self.balance()
        rows, error, success = [], None, False
        try:
            query = dict(params, type=report, key=self.key, database=os.getenv("SEMRUSH_DATABASE", "us"),
                         export_columns=COLUMNS[report])
            if report != "phrase_these":
                query["display_limit"] = lines
            # The key is a query parameter; never log the URL or exception text.
            r = self.session.get(API, params=query, timeout=30)
            body = r.text.strip()
            if body.startswith("ERROR"):
                message = body.split("::", 1)[-1].strip().upper()
                if "NOTHING FOUND" in message:
                    success = True
                else:
                    error = "semrush_error"
                    if any(s in message for s in STOP_ERRORS):
                        self.stopped = "semrush_stopped"
            elif r.status_code != 200:
                error = f"http_{r.status_code}"
                if r.status_code in (401, 402, 403, 429):
                    self.stopped = error
            else:
                rows = parse_csv(body)
                success = True
        except requests.RequestException:
            error = "network_error"
        after = self.balance() if before is not None else None
        reported = before - after if before is not None and after is not None and before >= after else None
        with self.conn.transaction():
            self.conn.execute("UPDATE engine_api_usage SET success=%s,error_category=%s,units_reported=%s,"
                              "rows_returned=%s WHERE id=%s",
                              (success, error, reported, len(rows), usage_id))
        if reported is not None and reported > reserve:
            # Real cost above the worst-case estimate: stop and pause all Semrush spending.
            self.pause(f"engine {report} cost {reported} units, estimate {reserve}")
            self.stopped = "cost_exceeded_estimate"
            raise SemrushError("cost_exceeded_estimate", True)
        if not success:
            raise SemrushError(error, bool(self.stopped))
        return rows


def store_keyword_rows(conn, rows, source, observed_on=None, cpc_unit="USD"):
    observed_on = observed_on or date.today()
    with conn.transaction():
        for r in rows:
            kw = (r.get("keyword") or "").strip().lower()
            if not kw:
                continue
            conn.execute(
                "INSERT INTO seo_keyword_metrics(keyword,source,observed_on,search_volume,keyword_difficulty,"
                "cpc,cpc_unit,competition,intents,evidence) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT(keyword,database,source,observed_on) DO UPDATE SET "
                "search_volume=EXCLUDED.search_volume,keyword_difficulty=EXCLUDED.keyword_difficulty,"
                "cpc=EXCLUDED.cpc,competition=EXCLUDED.competition,intents=EXCLUDED.intents,"
                "evidence=EXCLUDED.evidence",
                (kw, source, observed_on, to_number(r.get("volume"), int), to_number(r.get("kd")),
                 to_number(r.get("cpc")), cpc_unit, to_number(r.get("competition")),
                 intents(r.get("intents")), _json(r)))


def store_relations(conn, seed, rows, relation, source, observed_on=None):
    observed_on = observed_on or date.today()
    with conn.transaction():
        for r in rows:
            kw = (r.get("keyword") or "").strip().lower()
            if kw:
                conn.execute("INSERT INTO seo_keyword_relations(seed,keyword,relation,source,observed_on) "
                             "VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                             (seed.lower(), kw, relation, source, observed_on))


def store_rankings(conn, domain, rows, source, observed_on=None):
    observed_on = observed_on or date.today()
    with conn.transaction():
        for r in rows:
            kw = (r.get("keyword") or "").strip().lower()
            if not kw:
                continue
            conn.execute(
                "INSERT INTO seo_competitor_rankings(domain,keyword,position,url,traffic,traffic_share,"
                "search_volume,source,observed_on) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT(domain,keyword,source,observed_on) DO UPDATE SET position=EXCLUDED.position,"
                "url=EXCLUDED.url,traffic=EXCLUDED.traffic,traffic_share=EXCLUDED.traffic_share,"
                "search_volume=EXCLUDED.search_volume",
                (domain, kw, to_number(r.get("position"), int), r.get("url"), to_number(r.get("traffic")),
                 to_number(r.get("traffic_share")), to_number(r.get("volume"), int), source, observed_on))


def _json(row):
    import json
    return json.dumps({k: v for k, v in row.items() if k not in ("trends",)})
