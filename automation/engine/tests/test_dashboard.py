import base64
import os
import unittest
import uuid
from datetime import date, datetime, timedelta, timezone
from unittest import mock

from fastapi.testclient import TestClient

from engine import dashboard, db

TEST_DB = os.environ.get("TEST_DATABASE_URL")
USER = "ops"
PASSWORD = "correct-horse-battery-staple"
ENV = {"DASHBOARD_USER": USER, "DASHBOARD_PASSWORD": PASSWORD}


def basic(user, password):
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()}


GOOD = basic(USER, PASSWORD)
XSS = "<script>alert(1)</script>"

# Minimal versions of tables owned by other services (collector, approvals).
FOREIGN_DDL = """
CREATE TABLE seo_runs (id uuid PRIMARY KEY, started_at timestamptz NOT NULL, finished_at timestamptz,
  page_count integer, issue_count integer, gsc_rows integer, gsc_status text NOT NULL, status text NOT NULL);
CREATE TABLE seo_search_performance (data_date date NOT NULL, page text NOT NULL, query text NOT NULL,
  clicks double precision NOT NULL, impressions double precision NOT NULL, ctr double precision NOT NULL,
  position double precision NOT NULL, PRIMARY KEY(data_date, page, query));
CREATE TABLE seo_semrush_api_usage (id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  requested_at timestamptz NOT NULL DEFAULT now(), keyword text NOT NULL, units_reserved integer NOT NULL,
  success boolean);
CREATE TABLE seo_approval_proposals (id uuid PRIMARY KEY, repo text NOT NULL, pr_number integer NOT NULL,
  commit_sha char(40) NOT NULL, title text NOT NULL, summary text NOT NULL, status text NOT NULL DEFAULT 'pending',
  approved_by text, updated_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE seo_approval_events (id bigserial PRIMARY KEY, proposal_id uuid NOT NULL
  REFERENCES seo_approval_proposals(id), kind text NOT NULL, actor text NOT NULL, detail text NOT NULL DEFAULT '',
  created_at timestamptz NOT NULL DEFAULT now());
"""


def reset_schema(url):
    with db.connect(url, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")


class AuthTests(unittest.TestCase):
    def setUp(self):
        dashboard._failures.clear()
        self.client = TestClient(dashboard.app)
        # Auth is checked before any database access; make sure nothing could reach a DB anyway.
        patcher = mock.patch.object(dashboard, "load_summary", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_health_without_auth(self):
        with mock.patch.dict(os.environ, ENV):
            r = self.client.get("/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"ok": True, "configured": True})
        with mock.patch.dict(os.environ, {"DASHBOARD_USER": "", "DASHBOARD_PASSWORD": ""}):
            self.assertEqual(self.client.get("/health").json(), {"ok": True, "configured": False})

    def test_unauthenticated_401(self):
        with mock.patch.dict(os.environ, ENV):
            for path in ("/", "/api/summary"):
                r = self.client.get(path)
                self.assertEqual(r.status_code, 401)
                self.assertIn("Basic", r.headers["www-authenticate"])

    def test_wrong_password_401(self):
        with mock.patch.dict(os.environ, ENV):
            self.assertEqual(self.client.get("/", headers=basic(USER, PASSWORD + "x")).status_code, 401)
            self.assertEqual(self.client.get("/", headers=basic("other", PASSWORD)).status_code, 401)
            self.assertEqual(self.client.get("/", headers={"Authorization": "Basic !!!"}).status_code, 401)

    def test_unconfigured_503(self):
        cases = [{"DASHBOARD_USER": "", "DASHBOARD_PASSWORD": ""},
                 {"DASHBOARD_USER": USER, "DASHBOARD_PASSWORD": "short-pass"},  # < 16 chars
                 {"DASHBOARD_USER": "", "DASHBOARD_PASSWORD": PASSWORD}]
        for env in cases:
            with mock.patch.dict(os.environ, env):
                for path in ("/", "/api/summary"):
                    r = self.client.get(path, headers=GOOD)
                    self.assertEqual(r.status_code, 503, (env, path))
                    self.assertIn("dashboard not configured", r.text)

    def test_rate_limit_429(self):
        with mock.patch.dict(os.environ, ENV):
            for _ in range(dashboard.MAX_FAILURES):
                self.assertEqual(self.client.get("/", headers=basic(USER, "nope")).status_code, 401)
            r = self.client.get("/", headers=basic(USER, "nope"))
            self.assertEqual(r.status_code, 429)
            # Locked out even with the right password until the window passes.
            self.assertEqual(self.client.get("/", headers=GOOD).status_code, 429)

    def test_correct_credentials_reach_route(self):
        with mock.patch.dict(os.environ, ENV):
            r = self.client.get("/", headers=GOOD)
        self.assertEqual(r.status_code, 503)  # load_summary patched to "database unavailable"
        self.assertIn("Database unavailable", r.text)

    def test_security_headers(self):
        with mock.patch.dict(os.environ, ENV):
            responses = [self.client.get("/health"), self.client.get("/"), self.client.get("/", headers=GOOD)]
        for r in responses:
            self.assertEqual(r.headers["content-security-policy"], dashboard.SECURITY_HEADERS["Content-Security-Policy"])
            self.assertIn("default-src 'none'", r.headers["content-security-policy"])
            self.assertIn("frame-ancestors 'none'", r.headers["content-security-policy"])
            self.assertEqual(r.headers["x-content-type-options"], "nosniff")
            self.assertEqual(r.headers["referrer-policy"], "no-referrer")
            self.assertEqual(r.headers["cache-control"], "no-store")
            self.assertEqual(r.headers["x-robots-tag"], "noindex")

    def test_no_docs_routes(self):
        with mock.patch.dict(os.environ, ENV):
            for path in ("/docs", "/redoc", "/openapi.json"):
                self.assertEqual(self.client.get(path, headers=GOOD).status_code, 404)

    def test_caps_are_numbers_only(self):
        with mock.patch.dict(os.environ, {"SEMRUSH_MAX_UNITS_PER_MONTH": "50000",
                                          "ENGINE_SEMRUSH_MAX_UNITS_PER_MONTH": "<b>1</b>"}):
            self.assertEqual(dashboard.configured_caps(), {"SEMRUSH_MAX_UNITS_PER_MONTH": 50000})


@unittest.skipUnless(TEST_DB, "TEST_DATABASE_URL not set")
class DatabaseTests(unittest.TestCase):
    def setUp(self):
        dashboard._failures.clear()
        reset_schema(TEST_DB)
        env = dict(ENV, DATABASE_URL=TEST_DB, SEMRUSH_MAX_UNITS_PER_MONTH="12000")
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(dashboard.app)

    def tearDown(self):
        reset_schema(TEST_DB)

    def test_empty_database_renders(self):
        r = self.client.get("/", headers=GOOD)
        self.assertEqual(r.status_code, 200)
        self.assertIn("No data yet", r.text)
        self.assertNotIn("Section unavailable", r.text)
        self.assertNotIn(TEST_DB, r.text)
        j = self.client.get("/api/summary", headers=GOOD)
        self.assertEqual(j.status_code, 200)
        statuses = {t["status"] for s in j.json()["sections"].values() for t in s["tables"].values()}
        self.assertEqual(statuses, {"no_data"})

    def populate(self):
        now = datetime.now(timezone.utc)
        with db.connect(TEST_DB) as conn:
            db.migrate(conn)
            with conn.transaction():
                conn.execute(FOREIGN_DDL)
                run = uuid.uuid4()
                conn.execute("INSERT INTO engine_runs(id, started_at, finished_at, status, steps, errors) "
                             "VALUES (%s, %s, %s, 'failed', %s, %s)",
                             (run, now - timedelta(minutes=5), now, '{"collect": {"status": "ok"}, "rank": 12}',
                              '["boom"]'))
                conn.execute("INSERT INTO seo_runs VALUES (%s, %s, %s, 30, 4, 100, 'ok', 'complete')",
                             (uuid.uuid4(), now - timedelta(hours=1), now))
                opp, opp2 = uuid.uuid4(), uuid.uuid4()
                conn.execute(
                    "INSERT INTO seo_opportunities(id, fingerprint, run_id, action, content_type, target_url, "
                    "head_keyword, priority, score, rationale, status) VALUES "
                    "(%s, 'fp1', %s, 'new_page', 'guide', NULL, %s, 88.5, "
                    "'{\"relevance\": 1.0, \"demand\": 0.5, \"metrics\": {\"volume\": 1900}}', 'because', 'open'),"
                    "(%s, 'fp2', %s, 'improve_page', NULL, '/futures-copier', 'futures trade copier', 42, '{}', "
                    "'weak title', 'open')",
                    (opp, run, XSS, opp2, run))
                brief = uuid.uuid4()
                conn.execute("INSERT INTO seo_content_briefs(id, opportunity_id, brief, markdown) "
                             "VALUES (%s, %s, '{}', '# b')", (brief, opp))
                conn.execute("INSERT INTO seo_content_drafts(id, brief_id, path, html, quality, status, pr_number) "
                             "VALUES (%s, %s, 'guides/copier.html', '<p>x</p>', '{\"passed\": false}', 'draft', 77)",
                             (uuid.uuid4(), brief))
                conn.execute("INSERT INTO seo_search_performance VALUES "
                             "(%s, '/a', 'tradovate copier', 3, 100, 0.03, 4.0),"
                             "(%s, '/b', 'tradovate copier', 1, 300, 0.0033, 8.0)",
                             (date.today() - timedelta(days=2), date.today() - timedelta(days=3)))
                conn.execute("INSERT INTO seo_keyword_metrics(keyword, source, observed_on, search_volume, "
                             "keyword_difficulty, cpc, cpc_unit, intents) VALUES "
                             "('prop firm copier', 'semrush', %s, 1900, 35, 2.5, 'USD', '{commercial}')",
                             (date.today(),))
                conn.execute("INSERT INTO seo_competitor_rankings(domain, keyword, position, url, traffic, "
                             "search_volume, source, observed_on) VALUES "
                             "('rival.example', 'copy trading futures', 3, 'https://rival.example/x', 120, 900, "
                             "'semrush', %s)", (date.today(),))
                conn.execute("INSERT INTO seo_competitor_pages(url, domain, http_status, title, h1, word_count) "
                             "VALUES ('https://rival.example/x', 'rival.example', 200, 'Rival Copier', 'Copy', 1500)")
                prop = uuid.uuid4()
                conn.execute("INSERT INTO seo_approval_proposals(id, repo, pr_number, commit_sha, title, summary, "
                             "status) VALUES (%s, 'ThomasPeakBot/usetradelink', 123, %s, 'Add copier guide', 's', "
                             "'pending')", (prop, "abcdef1" + "0" * 33))
                conn.execute("INSERT INTO seo_approval_events(proposal_id, kind, actor, detail) "
                             "VALUES (%s, 'proposed', 'system', 'posted to slack')", (prop,))
                cid = conn.execute("INSERT INTO seo_changes(pr_number, pages, published_at) "
                                   "VALUES (99, '{/futures-copier}', %s) RETURNING id",
                                   (now - timedelta(days=40),)).fetchone()["id"]
                conn.execute("INSERT INTO seo_change_evaluations(change_id, window_days, before, after, verdict) "
                             "VALUES (%s, 28, '{\"clicks\": 10, \"impressions\": 500}', "
                             "'{\"clicks\": 25, \"impressions\": 900}', 'improved')", (cid,))
                conn.execute("INSERT INTO engine_api_usage(provider, report, target, units_reserved, units_reported) "
                             "VALUES ('semrush', 'phrase_this', 'x', 10, 7), ('semrush', 'phrase_this', 'y', 10, 9)")
                conn.execute("INSERT INTO seo_semrush_api_usage(keyword, units_reserved, success) "
                             "VALUES ('a', 30, true)")

    def test_populated_sections_and_escaping(self):
        self.populate()
        r = self.client.get("/", headers=GOOD)
        self.assertEqual(r.status_code, 200)
        html = r.text
        self.assertNotIn("Section unavailable", html)
        self.assertNotIn("<script", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)
        for expected in ("collect=ok", "rank=12", "complete", "relevance=1", "volume=1900", "tradovate copier",
                         "prop firm copier", "rival.example", "Rival Copier", "/futures-copier",
                         "guides/copier.html", "fail", 'href="https://github.com/ThomasPeakBot/usetradelink/pull/123"',
                         "abcdef1", "posted to slack", "improved", "phrase_this", "12,000"):
            self.assertIn(expected, html)
        self.assertNotIn(TEST_DB, html)

        j = self.client.get("/api/summary", headers=GOOD)
        self.assertEqual(j.status_code, 200)
        s = j.json()["sections"]
        opp = s["opportunities"]["tables"]["opportunities"]["rows"]
        self.assertEqual(opp[0]["head_keyword"], XSS)
        self.assertIsInstance(opp[0]["priority"], float)
        self.assertIsInstance(opp[0]["last_seen"], str)
        gsc = s["gsc"]["tables"]["gsc_queries"]["rows"][0]
        self.assertEqual((gsc["impressions"], gsc["clicks"]), (400.0, 4.0))
        self.assertAlmostEqual(gsc["position"], 7.0)
        self.assertEqual(s["engine_health"]["tables"]["engine_failures_7d"]["rows"][0]["failed"], 1)
        usage = s["api_usage"]["tables"]["engine_api_usage"]["rows"][0]
        self.assertEqual((usage["units_reserved"], usage["units_reported"]), (20, 16))
        self.assertEqual(s["api_usage"]["tables"]["collector_semrush_usage"]["rows"][0]["units_reserved"], 30)
        self.assertEqual(s["api_usage"]["caps"], {"SEMRUSH_MAX_UNITS_PER_MONTH": 12000})
        self.assertEqual(s["approvals"]["tables"]["pending_proposals"]["rows"][0]["short_sha"], "abcdef1")
        pages = s["pages_to_improve"]["tables"]["pages_to_improve"]["rows"]
        self.assertEqual([p["target_url"] for p in pages], ["/futures-copier"])
        self.assertFalse(s["content"]["tables"]["drafts"]["rows"][0]["quality_passed"])
        self.assertEqual(s["changes"]["tables"]["changes"]["rows"][0]["clicks_after"], "25")

    def test_query_helper_is_read_only(self):
        with db.connect(TEST_DB, autocommit=True) as setup:
            setup.execute("CREATE TABLE scratch (x int)")
        import psycopg
        # Through the dashboard's connection and helper.
        with dashboard.open_connection(TEST_DB) as conn:
            with self.assertRaises(psycopg.errors.ReadOnlySqlTransaction):
                dashboard.query(conn, "INSERT INTO scratch VALUES (1)")
        # SET TRANSACTION READ ONLY alone is enough, even on a plain read-write connection.
        with db.connect(TEST_DB, autocommit=True) as conn:
            with self.assertRaises(psycopg.errors.ReadOnlySqlTransaction):
                with dashboard.read_only_tx(conn):
                    dashboard.query(conn, "DROP TABLE scratch")
        with db.connect(TEST_DB) as conn:
            self.assertEqual(conn.execute("SELECT count(*) AS n FROM scratch").fetchone()["n"], 0)

    def test_failing_section_does_not_break_page(self):
        with db.connect(TEST_DB, autocommit=True) as conn:
            # A collector table with an unexpected shape: only that section degrades.
            conn.execute("CREATE TABLE seo_runs (id int)")
        with self.assertLogs("engine.dashboard", "ERROR"):
            r = self.client.get("/", headers=GOOD)
        self.assertEqual(r.status_code, 200)
        self.assertIn("Section unavailable", r.text)
        self.assertIn("No data yet", r.text)


if __name__ == "__main__":
    unittest.main()
