"""Read-only internal dashboard for the TradeLink SEO engine.

Run from automation/engine:
    uvicorn engine.dashboard:app --host 0.0.0.0 --port 8080

Configuration (environment):
    DATABASE_URL          Postgres connection string (never displayed)
    DASHBOARD_USER        HTTP Basic username
    DASHBOARD_PASSWORD    HTTP Basic password, at least 16 characters
    SEMRUSH_MAX_UNITS_PER_MONTH / ENGINE_SEMRUSH_MAX_UNITS_PER_MONTH
                          optional; shown as numbers only

Every query runs inside a READ ONLY transaction. Every value that comes from
the database is HTML-escaped. The page has no JavaScript and no external assets.
"""
import base64
import binascii
import hmac
import json
import logging
import os
import threading
import time
import uuid
from collections import deque
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
from html import escape

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from psycopg import pq

from engine import db

log = logging.getLogger("engine.dashboard")

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

MIN_PASSWORD_LENGTH = 16
MAX_FAILURES = 10            # failed attempts allowed per client IP per window
FAILURE_WINDOW = 300         # seconds
MAX_TRACKED_CLIENTS = 10000  # bound memory used by the limiter
STATEMENT_TIMEOUT = "15s"
PR_URL = "https://github.com/ThomasPeakBot/usetradelink/pull/{}"

ACTIVE_STATUSES = ("open", "briefed", "drafted", "proposed")
IMPROVE_ACTIONS = ("improve_page", "rewrite_title_meta", "add_internal_links", "refresh_outdated",
                   "consolidate_cannibalization", "differentiate_pages")
PENDING_APPROVAL_STATUSES = ("pending", "approved_queued", "executing", "revision_requested")
CAP_ENV = ("SEMRUSH_MAX_UNITS_PER_MONTH", "ENGINE_SEMRUSH_MAX_UNITS_PER_MONTH")

SECURITY_HEADERS = {
    "Content-Security-Policy": ("default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:; "
                                "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    "X-Robots-Tag": "noindex",
    "X-Frame-Options": "DENY",
}


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    for name, value in SECURITY_HEADERS.items():
        response.headers[name] = value
    return response


# --------------------------------------------------------------------------- auth

def credentials():
    """(user, password) when the dashboard is safely configured, otherwise None."""
    user = os.environ.get("DASHBOARD_USER", "")
    password = os.environ.get("DASHBOARD_PASSWORD", "")
    if not user or len(password) < MIN_PASSWORD_LENGTH:
        return None
    return user, password


_failures = {}
_failures_lock = threading.Lock()


def _recent(ip, now):
    q = _failures.get(ip)
    if q is None:
        return None
    while q and q[0] <= now - FAILURE_WINDOW:
        q.popleft()
    if not q:
        del _failures[ip]
        return None
    return q


def _locked_out(ip):
    with _failures_lock:
        q = _recent(ip, time.monotonic())
        return q is not None and len(q) >= MAX_FAILURES


def _record_failure(ip):
    now = time.monotonic()
    with _failures_lock:
        if ip not in _failures and len(_failures) >= MAX_TRACKED_CLIENTS:
            for key in list(_failures):
                _recent(key, now)
            if len(_failures) >= MAX_TRACKED_CLIENTS:
                _failures.pop(next(iter(_failures)))
        _failures.setdefault(ip, deque()).append(now)


def _clear_failures(ip):
    with _failures_lock:
        _failures.pop(ip, None)


def _parse_basic(header):
    scheme, _, encoded = (header or "").partition(" ")
    if scheme.lower() != "basic" or not encoded:
        return None
    try:
        decoded = base64.b64decode(encoded.strip(), validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None
    user, sep, password = decoded.partition(":")
    return (user, password) if sep else None


def require_auth(request: Request):
    expected = credentials()
    if expected is None:
        raise HTTPException(503, "dashboard not configured")
    ip = request.client.host if request.client else "unknown"
    if _locked_out(ip):
        raise HTTPException(429, "too many failed attempts", headers={"Retry-After": str(FAILURE_WINDOW)})
    given = _parse_basic(request.headers.get("authorization"))
    given_user, given_password = given or ("", "")
    user_ok = hmac.compare_digest(given_user.encode("utf-8"), expected[0].encode("utf-8"))
    password_ok = hmac.compare_digest(given_password.encode("utf-8"), expected[1].encode("utf-8"))
    if not (given and user_ok and password_ok):
        _record_failure(ip)
        raise HTTPException(401, "authentication required",
                            headers={"WWW-Authenticate": 'Basic realm="seo-dashboard", charset="UTF-8"'})
    _clear_failures(ip)


# --------------------------------------------------------------------------- read-only data access

def open_connection(url=None):
    """Connection whose transactions are READ ONLY by default (belt), see read_only_tx (braces)."""
    conn = db.connect(url, autocommit=True)
    conn.read_only = True
    conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
    return conn


@contextmanager
def read_only_tx(conn):
    with conn.transaction():
        conn.execute("SET TRANSACTION READ ONLY")
        conn.execute(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT}'")
        yield conn


def query(conn, sql, params=()):
    """Run one statement; outside a transaction it gets its own read-only one."""
    if conn.info.transaction_status == pq.TransactionStatus.IDLE:
        with read_only_tx(conn):
            return query(conn, sql, params)
    cur = conn.execute(sql, params)
    return [dict(r) for r in cur.fetchall()] if cur.description else []


class Table:
    def __init__(self, key, title, requires, sql, columns, params=(), post=None):
        self.key, self.title, self.requires, self.sql = key, title, requires, sql
        self.columns, self.params, self.post = columns, params, post


def run_table(conn, table):
    """{'status': 'ok'|'no_data'|'error', 'rows': [...]} - never raises for one bad section."""
    try:
        with conn.transaction():  # savepoint, so one failing section cannot abort the rest
            if not all(db.table_exists(conn, t) for t in table.requires):
                return {"status": "no_data", "rows": []}
            rows = query(conn, table.sql, table.params)
    except psycopg.Error:
        log.exception("dashboard section %s failed", table.key)
        return {"status": "error", "rows": []}
    if table.post:
        rows = [table.post(r) for r in rows]
    return {"status": "ok" if rows else "no_data", "rows": rows}


# --------------------------------------------------------------------------- formatting helpers

def _short(text, n=160):
    text = "" if text is None else str(text)
    return text if len(text) <= n else text[: n - 1] + "…"


def _steps_summary(steps):
    if not isinstance(steps, dict):
        return ""
    parts = []
    for k, v in steps.items():
        if isinstance(v, dict):
            v = v.get("status", v.get("count", len(v)))
        elif isinstance(v, list):
            v = len(v)
        parts.append(f"{k}={v}")
    return _short(", ".join(parts), 240)


def _post_engine_run(r):
    r["steps"] = _steps_summary(r.get("steps"))
    return r


def _post_opportunity(r):
    score = r.pop("score", None)
    if not isinstance(score, dict):
        score = {}
    metrics = score.get("metrics") if isinstance(score.get("metrics"), dict) else {}
    keep = {}
    for k in ("relevance", "demand", "winnability", "intent", "confidence"):
        if k in score and not isinstance(score[k], (dict, list)):
            keep[k] = score[k]
    for k in ("volume", "kd", "impressions", "clicks", "position"):
        if metrics.get(k) is not None:
            keep[k] = metrics[k]
    r["metrics"] = ", ".join(f"{k}={_fmt_scalar(v)}" for k, v in keep.items())
    r["rationale"] = _short(r.get("rationale"), 220)
    return r


def _post_event(r):
    r["detail"] = _short(r.get("detail"), 200)
    return r


def _post_proposal(r):
    r["short_sha"] = (r.pop("commit_sha", "") or "").strip()[:7]
    return r


def _fmt_scalar(v):
    if isinstance(v, float) or isinstance(v, Decimal):
        f = float(v)
        return f"{f:,.0f}" if abs(f) >= 1000 else f"{f:.2f}".rstrip("0").rstrip(".") or "0"
    return str(v)


def fmt(v):
    if v is None:
        return "—"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, datetime):
        if v.tzinfo:
            v = v.astimezone(timezone.utc)
        return v.strftime("%Y-%m-%d %H:%M") + (" UTC" if v.tzinfo else "")
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, (float, Decimal)):
        return _fmt_scalar(v)
    if isinstance(v, (list, tuple)):
        return ", ".join(fmt(x) for x in v)
    if isinstance(v, dict):
        return _short(json.dumps(v, default=str, sort_keys=True), 200)
    return str(v)


def cell(v):
    return escape(fmt(v), quote=True)


def pr_cell(v):
    try:
        n = int(v)
    except (TypeError, ValueError):
        return cell(v)
    return f'<a href="{PR_URL.format(n)}" rel="noreferrer noopener">#{n}</a>'


def pct_cell(v):
    return "—" if v is None else escape(f"{float(v) * 100:.2f}%")


def pass_cell(v):
    return "—" if v is None else ("pass" if v else "<b>fail</b>")


# --------------------------------------------------------------------------- sections

SECTIONS = [
    ("engine_health", "Engine health", [
        Table("engine_runs", "Last 10 engine runs", ("engine_runs",), """
            SELECT status, started_at,
                   EXTRACT(EPOCH FROM (finished_at - started_at))::float AS duration_s, steps,
                   CASE WHEN jsonb_typeof(errors) = 'array' THEN jsonb_array_length(errors)
                        WHEN errors IS NULL OR errors = '{}'::jsonb THEN 0 ELSE 1 END AS error_count
              FROM engine_runs ORDER BY started_at DESC LIMIT 10""",
              [("status", "Status"), ("started_at", "Started"), ("duration_s", "Duration (s)"),
               ("steps", "Steps"), ("error_count", "Errors")], post=_post_engine_run),
        Table("collector_run", "Latest collector run (seo_runs)", ("seo_runs",), """
            SELECT status, gsc_status, started_at, finished_at, page_count, issue_count, gsc_rows
              FROM seo_runs ORDER BY started_at DESC LIMIT 1""",
              [("status", "Status"), ("gsc_status", "GSC"), ("started_at", "Started"),
               ("finished_at", "Finished"), ("page_count", "Pages"), ("issue_count", "Issues"),
               ("gsc_rows", "GSC rows")]),
        Table("engine_failures_7d", "Engine runs, last 7 days", ("engine_runs",), """
            SELECT count(*) AS runs,
                   count(*) FILTER (WHERE status IN ('failed', 'error') OR status LIKE 'error%%') AS failed,
                   count(*) FILTER (WHERE jsonb_typeof(errors) = 'array' AND jsonb_array_length(errors) > 0)
                     AS with_errors
              FROM engine_runs WHERE started_at >= now() - interval '7 days'""",
              [("runs", "Runs"), ("failed", "Failed"), ("with_errors", "With errors")]),
        Table("collector_failures_7d", "Collector runs, last 7 days", ("seo_runs",), """
            SELECT count(*) AS runs, count(*) FILTER (WHERE status <> 'complete') AS not_complete
              FROM seo_runs WHERE started_at >= now() - interval '7 days'""",
              [("runs", "Runs"), ("not_complete", "Partial / failed")]),
    ]),
    ("opportunities", "Keyword opportunities", [
        Table("opportunities", "Active opportunities (top 50 by priority)", ("seo_opportunities",), """
            SELECT priority, action, content_type, head_keyword, target_url, score, rationale, status, last_seen
              FROM seo_opportunities WHERE status = ANY(%s)
             ORDER BY priority DESC, last_seen DESC LIMIT 50""",
              [("priority", "Priority"), ("action", "Action"), ("content_type", "Type"),
               ("head_keyword", "Head keyword"), ("target_url", "Target URL"), ("metrics", "Score / metrics"),
               ("rationale", "Rationale"), ("status", "Status"), ("last_seen", "Last seen")],
              params=(list(ACTIVE_STATUSES),), post=_post_opportunity),
    ]),
    ("gsc", "Keyword performance (Search Console, last 28 days)", [
        Table("gsc_queries", "Top 50 queries by impressions", ("seo_search_performance",), """
            SELECT query, sum(impressions)::float AS impressions, sum(clicks)::float AS clicks,
                   (sum(clicks) / NULLIF(sum(impressions), 0))::float AS ctr,
                   (sum(position * impressions) / NULLIF(sum(impressions), 0))::float AS position
              FROM seo_search_performance WHERE data_date >= current_date - 28
             GROUP BY query ORDER BY sum(impressions) DESC, query LIMIT 50""",
              [("query", "Query"), ("impressions", "Impressions"), ("clicks", "Clicks"),
               ("ctr", ("CTR", pct_cell)), ("position", "Avg position (impr.-weighted)")]),
    ]),
    ("keyword_metrics", "Keyword research metrics", [
        Table("keyword_metrics", "Latest metrics per keyword (top 50 by volume)", ("seo_keyword_metrics",), """
            SELECT * FROM (
              SELECT DISTINCT ON (keyword) keyword, database, search_volume, keyword_difficulty, cpc, cpc_unit,
                     intents, source, observed_on
                FROM seo_keyword_metrics ORDER BY keyword, observed_on DESC, search_volume DESC NULLS LAST
            ) latest ORDER BY search_volume DESC NULLS LAST, keyword LIMIT 50""",
              [("keyword", "Keyword"), ("database", "DB"), ("search_volume", "Volume"),
               ("keyword_difficulty", "KD"), ("cpc", "CPC"), ("cpc_unit", "CPC unit"), ("intents", "Intents"),
               ("source", "Source"), ("observed_on", "Observed")]),
    ]),
    ("competitors", "Competitor findings", [
        Table("competitor_snapshots", "Latest ranking snapshot per domain", ("seo_competitor_rankings",), """
            WITH latest AS (SELECT domain, max(observed_on) AS d FROM seo_competitor_rankings GROUP BY domain)
            SELECT l.domain, l.d AS latest_observed_on, count(*) AS keywords,
                   sum(r.traffic) AS traffic
              FROM latest l JOIN seo_competitor_rankings r ON r.domain = l.domain AND r.observed_on = l.d
             GROUP BY l.domain, l.d ORDER BY l.domain""",
              [("domain", "Domain"), ("latest_observed_on", "Latest observed"), ("keywords", "Keywords"),
               ("traffic", "Est. traffic")]),
        Table("competitor_keywords", "Top keywords per domain by traffic (latest snapshot, 15 each)",
              ("seo_competitor_rankings",), """
            WITH latest AS (SELECT domain, max(observed_on) AS d FROM seo_competitor_rankings GROUP BY domain),
            ranked AS (
              SELECT r.domain, r.keyword, r.position, r.url, r.traffic, r.search_volume, r.source, r.observed_on,
                     row_number() OVER (PARTITION BY r.domain ORDER BY r.traffic DESC NULLS LAST, r.keyword) AS rn
                FROM seo_competitor_rankings r JOIN latest l ON l.domain = r.domain AND l.d = r.observed_on)
            SELECT domain, keyword, position, url, traffic, search_volume, source, observed_on
              FROM ranked WHERE rn <= 15 ORDER BY domain, rn""",
              [("domain", "Domain"), ("keyword", "Keyword"), ("position", "Pos"), ("url", "URL"),
               ("traffic", "Traffic"), ("search_volume", "Volume"), ("source", "Source"),
               ("observed_on", "Observed")]),
        Table("competitor_pages", "Latest competitor page snapshots", ("seo_competitor_pages",), """
            SELECT * FROM (
              SELECT DISTINCT ON (url) url, domain, title, h1, word_count, fetched_at, http_status
                FROM seo_competitor_pages ORDER BY url, fetched_at DESC
            ) latest ORDER BY fetched_at DESC, url LIMIT 50""",
              [("url", "URL"), ("title", "Title"), ("h1", "H1"), ("word_count", "Words"),
               ("fetched_at", "Fetched"), ("http_status", "HTTP")]),
    ]),
    ("pages_to_improve", "Pages needing improvement", [
        Table("pages_to_improve", "Active page-level opportunities grouped by URL", ("seo_opportunities",), """
            SELECT target_url, count(*) AS opportunities, max(priority) AS top_priority,
                   array_agg(DISTINCT action ORDER BY action) AS actions,
                   (array_agg(head_keyword ORDER BY priority DESC))[1:5] AS head_keywords,
                   max(last_seen) AS last_seen
              FROM seo_opportunities WHERE action = ANY(%s) AND status = ANY(%s)
             GROUP BY target_url ORDER BY max(priority) DESC LIMIT 50""",
              [("target_url", "Page"), ("opportunities", "Count"), ("top_priority", "Top priority"),
               ("actions", "Actions"), ("head_keywords", "Head keywords"), ("last_seen", "Last seen")],
              params=(list(IMPROVE_ACTIONS), list(ACTIVE_STATUSES))),
    ]),
    ("content", "Proposed content", [
        Table("briefs", "Content briefs (latest 50)", ("seo_content_briefs", "seo_opportunities"), """
            SELECT b.created_at, o.head_keyword, o.action, o.target_url, o.status AS opportunity_status
              FROM seo_content_briefs b LEFT JOIN seo_opportunities o ON o.id = b.opportunity_id
             ORDER BY b.created_at DESC LIMIT 50""",
              [("created_at", "Created"), ("head_keyword", "Head keyword"), ("action", "Action"),
               ("target_url", "Target URL"), ("opportunity_status", "Opportunity status")]),
        Table("drafts", "Content drafts (latest 50)", ("seo_content_drafts", "seo_content_briefs",
                                                       "seo_opportunities"), """
            SELECT d.created_at, d.path, d.status, o.head_keyword,
                   CASE WHEN jsonb_typeof(d.quality -> 'passed') = 'boolean'
                        THEN (d.quality ->> 'passed')::boolean END AS quality_passed,
                   d.pr_number
              FROM seo_content_drafts d
              LEFT JOIN seo_content_briefs b ON b.id = d.brief_id
              LEFT JOIN seo_opportunities o ON o.id = b.opportunity_id
             ORDER BY d.created_at DESC LIMIT 50""",
              [("created_at", "Created"), ("path", "Path"), ("head_keyword", "Head keyword"),
               ("status", "Status"), ("quality_passed", ("Quality", pass_cell)), ("pr_number", ("PR", pr_cell))]),
    ]),
    ("approvals", "Pending Slack approvals", [
        Table("pending_proposals", "Open proposals", ("seo_approval_proposals",), """
            SELECT pr_number, commit_sha, title, status, approved_by, updated_at
              FROM seo_approval_proposals WHERE status = ANY(%s) ORDER BY updated_at DESC LIMIT 50""",
              [("pr_number", ("PR", pr_cell)), ("short_sha", "SHA"), ("title", "Title"), ("status", "Status"),
               ("approved_by", "Approved by"), ("updated_at", "Updated")],
              params=(list(PENDING_APPROVAL_STATUSES),), post=_post_proposal),
        Table("approval_events", "Last 20 approval events", ("seo_approval_events", "seo_approval_proposals"), """
            SELECT e.created_at, p.pr_number, e.kind, e.actor, e.detail
              FROM seo_approval_events e LEFT JOIN seo_approval_proposals p ON p.id = e.proposal_id
             ORDER BY e.created_at DESC, e.id DESC LIMIT 20""",
              [("created_at", "When"), ("pr_number", ("PR", pr_cell)), ("kind", "Kind"), ("actor", "Actor"),
               ("detail", "Detail")], post=_post_event),
    ]),
    ("changes", "Published changes and outcomes", [
        Table("changes", "Published changes (latest 50)", ("seo_changes", "seo_change_evaluations"), """
            SELECT c.pages, c.change_type, c.published_at, c.pr_number, e.window_days, e.verdict,
                   e.before ->> 'clicks' AS clicks_before, e.after ->> 'clicks' AS clicks_after,
                   e.before ->> 'impressions' AS impressions_before, e.after ->> 'impressions' AS impressions_after
              FROM seo_changes c LEFT JOIN seo_change_evaluations e ON e.change_id = c.id
             ORDER BY c.published_at DESC, e.window_days LIMIT 50""",
              [("pages", "Pages"), ("change_type", "Type"), ("published_at", "Published"),
               ("pr_number", ("PR", pr_cell)), ("window_days", "Window (d)"), ("verdict", "Verdict"),
               ("clicks_before", "Clicks before"), ("clicks_after", "Clicks after"),
               ("impressions_before", "Impr. before"), ("impressions_after", "Impr. after")]),
    ]),
    ("api_usage", "Semrush / API consumption (current month, UTC)", [
        Table("engine_api_usage", "Engine provider usage by report", ("engine_api_usage",), """
            SELECT provider, report, count(*) AS calls, sum(units_reserved) AS units_reserved,
                   sum(units_reported) AS units_reported,
                   count(*) FILTER (WHERE success IS FALSE) AS failed_calls
              FROM engine_api_usage
             WHERE requested_at >= date_trunc('month', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'
             GROUP BY provider, report ORDER BY sum(units_reserved) DESC""",
              [("provider", "Provider"), ("report", "Report"), ("calls", "Calls"),
               ("units_reserved", "Units reserved"), ("units_reported", "Units reported"),
               ("failed_calls", "Failed calls")]),
        Table("collector_semrush_usage", "Collector Semrush usage (seo_semrush_api_usage)",
              ("seo_semrush_api_usage",), """
            SELECT count(*) AS calls, coalesce(sum(units_reserved), 0) AS units_reserved,
                   count(*) FILTER (WHERE success IS FALSE) AS failed_calls
              FROM seo_semrush_api_usage
             WHERE requested_at >= date_trunc('month', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'""",
              [("calls", "Calls"), ("units_reserved", "Units reserved"), ("failed_calls", "Failed calls")]),
    ]),
]


def configured_caps():
    """Monthly unit caps from the environment, as integers only (never raw env text)."""
    caps = {}
    for name in CAP_ENV:
        raw = os.environ.get(name, "").strip()
        if raw.isdigit():
            caps[name] = int(raw)
    return caps


def collect(conn):
    data = {}
    with read_only_tx(conn):
        for key, title, tables in SECTIONS:
            data[key] = {"title": title,
                         "tables": {t.key: dict(title=t.title, **run_table(conn, t)) for t in tables}}
    data["api_usage"]["caps"] = configured_caps()
    return data


def load_summary():
    try:
        conn = open_connection()
    except (KeyError, psycopg.Error):
        log.exception("dashboard could not connect to the database")
        return None
    try:
        with conn:
            return collect(conn)
    except psycopg.Error:
        log.exception("dashboard query transaction failed")
        return None


# --------------------------------------------------------------------------- rendering

CSS = """
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
 margin:0;padding:16px 24px;background:#fafafa;color:#1a1a1a;font-size:14px}
h1{font-size:20px;margin:0 0 4px} h2{font-size:16px;margin:28px 0 8px;border-bottom:1px solid #ccc;padding-bottom:4px}
h3{font-size:13px;margin:14px 0 6px;color:#444} .meta{color:#666;font-size:12px}
nav a{margin-right:10px;font-size:12px;color:#0b57d0}
.wrap{overflow-x:auto} table{border-collapse:collapse;font-size:12px;background:#fff;min-width:40%}
th,td{border:1px solid #ddd;padding:3px 6px;text-align:left;vertical-align:top;max-width:420px;overflow-wrap:anywhere}
th{background:#f0f0f0;font-weight:600} tr:nth-child(even) td{background:#fcfcfc}
.empty{color:#777;font-style:italic;font-size:12px} .err{color:#a00;font-size:12px} a{color:#0b57d0}
"""


def render_table(table, result):
    if result["status"] == "error":
        return '<p class="err">Section unavailable (query failed; see service logs).</p>'
    if not result["rows"]:
        return '<p class="empty">No data yet</p>'
    head, fmts = [], []
    for key, spec in table.columns:
        label, formatter = (spec if isinstance(spec, tuple) else (spec, cell))
        head.append(f"<th>{escape(label)}</th>")
        fmts.append((key, formatter))
    body = ["<tr>" + "".join(f"<td>{f(row.get(k))}</td>" for k, f in fmts) + "</tr>" for row in result["rows"]]
    return f'<div class="wrap"><table><thead><tr>{"".join(head)}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'


def render_page(data):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    out = ['<!doctype html><html lang="en"><head><meta charset="utf-8">',
           '<meta name="viewport" content="width=device-width,initial-scale=1">',
           '<meta name="robots" content="noindex,nofollow"><meta name="referrer" content="no-referrer">',
           f"<title>SEO engine dashboard</title><style>{CSS}</style></head><body>",
           "<h1>TradeLink SEO engine</h1>",
           f'<p class="meta">Read-only snapshot generated {escape(now)}. Times in UTC.</p>',
           "<nav>" + "".join(f'<a href="#{k}">{escape(t)}</a>' for k, t, _ in SECTIONS) + "</nav>"]
    if data is None:
        out.append('<p class="err">Database unavailable. No data yet.</p></body></html>')
        return "".join(out)
    for key, title, tables in SECTIONS:
        out.append(f'<section id="{escape(key)}"><h2>{escape(title)}</h2>')
        if key == "api_usage":
            caps = data[key].get("caps") or {}
            if caps:
                out.append('<p class="meta">Configured monthly caps: ' + ", ".join(
                    f"{escape(n)} = {int(v):,}" for n, v in caps.items()) + "</p>")
            else:
                out.append('<p class="meta">No monthly caps configured in this service.</p>')
        for t in tables:
            out.append(f"<h3>{escape(t.title)}</h3>")
            out.append(render_table(t, data[key]["tables"][t.key]))
        out.append("</section>")
    out.append("</body></html>")
    return "".join(out)


def jsonable(v):
    if isinstance(v, dict):
        return {str(k): jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [jsonable(x) for x in v]
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, (bytes, memoryview)):
        return None
    return v


# --------------------------------------------------------------------------- routes

@app.get("/health")
def health():
    return {"ok": True, "configured": credentials() is not None}


@app.get("/", response_class=HTMLResponse, dependencies=[Depends(require_auth)])
def index():
    data = load_summary()
    return HTMLResponse(render_page(data), status_code=200 if data is not None else 503)


@app.get("/api/summary", dependencies=[Depends(require_auth)])
def api_summary():
    data = load_summary()
    if data is None:
        return JSONResponse({"error": "database unavailable"}, status_code=503)
    payload = {"generated_at": datetime.now(timezone.utc).isoformat(), "sections": jsonable(data)}
    return JSONResponse(payload)


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    return PlainTextResponse(str(exc.detail), status_code=exc.status_code, headers=getattr(exc, "headers", None))
