"""Postgres-backed tests. Set TEST_DATABASE_URL to a disposable database."""
import os
import unittest
import uuid
from unittest.mock import patch

import psycopg

import semrush
from main import save_database

DB = os.getenv("TEST_DATABASE_URL", "")
ON = {"SEMRUSH_ENABLED": "true", "SEMRUSH_API_KEY": "test-key",
      "SEMRUSH_MAX_UNITS_PER_RUN": "60", "SEMRUSH_MAX_UNITS_PER_MONTH": "100",
      "SEMRUSH_SEED_KEYWORDS": "futures trade copier,tradovate trade copier,forex copier,"
                               "prop firm trade copier,cloud futures trade copier"}
OK = {"meta": {"success": True},
      "data": {"search_volume": 100, "keyword_difficulty": 30, "cpc": 250, "intents": ["commercial"]}}


class FakeResponse:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self.payload = payload if payload is not None else OK

    def raise_for_status(self):
        if self.status_code >= 400:
            err = semrush.requests.HTTPError(str(self.status_code))
            err.response = self
            raise err

    def json(self):
        return self.payload


class FakeBalance:
    def __init__(self, value):
        self.status_code = 200 if value is not None else 500
        self.text = str(value) if value is not None else ""


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        if "countapiunits" in url:
            balance = self.balances.pop(0) if getattr(self, "balances", None) else None
            return FakeBalance(balance)
        self.calls.append(params["keyword"])
        return self.responses.pop(0) if self.responses else FakeResponse()


@unittest.skipUnless(DB, "TEST_DATABASE_URL not set")
class CollectorDatabaseTests(unittest.TestCase):
    def setUp(self):
        with psycopg.connect(DB, autocommit=True) as conn:
            conn.execute("DROP TABLE IF EXISTS seo_pages, seo_runs, seo_search_performance, "
                         "seo_semrush_api_usage, seo_semrush_keyword_metrics, seo_spend_controls, "
                         "engine_api_usage CASCADE")
        save_database(DB, uuid.uuid4(), [], [], "not_configured")

    def scalar(self, sql):
        with psycopg.connect(DB) as conn:
            return conn.execute(sql).fetchone()[0]

    def test_audit_and_gsc_rows_saved_and_upserted(self):
        page = {"url": "https://usetradelink.com/", "http_status": 200, "title": "t",
                "meta_description": "d", "canonical": "c", "h1_count": 1, "internal_links": 3,
                "noindex": False, "issues": ["x"]}
        row = {"keys": ["2026-10-01", "https://usetradelink.com/", "trade copier"],
               "clicks": 1, "impressions": 10, "ctr": .1, "position": 8}
        save_database(DB, uuid.uuid4(), [page], [row, {"keys": ["bad"]}], "error:HTTPError")
        save_database(DB, uuid.uuid4(), [page], [dict(row, clicks=4)], "ok")
        self.assertEqual(self.scalar("SELECT count(*) FROM seo_search_performance"), 1)
        self.assertEqual(self.scalar("SELECT clicks FROM seo_search_performance"), 4)
        self.assertEqual(self.scalar("SELECT count(*) FROM seo_runs WHERE status='partial'"), 1)

    @patch.dict(os.environ, ON, clear=True)
    def test_run_budget_caps_requests_and_skips_irrelevant_terms(self):
        session = FakeSession([])
        result = semrush.enrich(DB, session)
        self.assertEqual(result["requests"], 3)
        self.assertEqual(result["status"], "run_budget_reached")
        self.assertNotIn("forex copier", session.calls)
        self.assertEqual(self.scalar("SELECT sum(units_reserved) FROM seo_semrush_api_usage"), 60)
        self.assertEqual(float(self.scalar("SELECT max(cpc_usd) FROM seo_semrush_keyword_metrics")), 2.5)

    @patch.dict(os.environ, ON, clear=True)
    def test_refresh_interval_avoids_paying_twice(self):
        semrush.enrich(DB, FakeSession([]))
        second = FakeSession([])
        self.assertEqual(semrush.enrich(DB, second)["status"], "ok")
        # Only the one relevant seed not fetched in the first (budget-capped) run is requested.
        self.assertEqual(second.calls, ["cloud futures trade copier"])

    @patch.dict(os.environ, dict(ON, SEMRUSH_MAX_UNITS_PER_MONTH="50"), clear=True)
    def test_monthly_cap_blocks_further_spend(self):
        self.assertEqual(semrush.enrich(DB, FakeSession([]))["status"], "monthly_budget_reached")
        second = FakeSession([])
        self.assertEqual(semrush.enrich(DB, second)["status"], "monthly_budget_reached")
        self.assertEqual(second.calls, [])
        self.assertEqual(self.scalar("SELECT sum(units_reserved) FROM seo_semrush_api_usage"), 40)

    @patch.dict(os.environ, ON, clear=True)
    def test_quota_or_auth_errors_stop_and_still_count_units(self):
        for code in (401, 429):
            with self.subTest(code=code):
                self.setUp()
                session = FakeSession([FakeResponse(code)])
                result = semrush.enrich(DB, session)
                self.assertEqual((result["status"], result["requests"]), (f"http_{code}", 1))
                self.assertEqual(self.scalar(
                    "SELECT count(*) FROM seo_semrush_api_usage WHERE success = false"), 1)

    @patch.dict(os.environ, {k: v for k, v in ON.items() if not k.startswith("SEMRUSH_MAX")}, clear=True)
    def test_unset_caps_mean_no_spending(self):
        session = FakeSession([])
        self.assertEqual(semrush.enrich(DB, session)["status"], "budget_disabled")
        self.assertEqual(session.calls, [])

    @patch.dict(os.environ, dict(ON, SEMRUSH_KILL_SWITCH="true"), clear=True)
    def test_kill_switch(self):
        self.assertEqual(semrush.enrich(DB, FakeSession([]))["status"], "kill_switch")

    @patch.dict(os.environ, ON, clear=True)
    def test_operator_pause_shared_with_engine(self):
        with psycopg.connect(DB) as conn:
            conn.execute("CREATE TABLE seo_spend_controls(provider text PRIMARY KEY, paused boolean, "
                         "reason text, updated_at timestamptz)")
            conn.execute("INSERT INTO seo_spend_controls VALUES('semrush', true, 'review', now())")
        session = FakeSession([])
        self.assertEqual(semrush.enrich(DB, session)["status"], "paused")
        self.assertEqual(session.calls, [])

    @patch.dict(os.environ, ON, clear=True)
    def test_engine_usage_counts_toward_the_monthly_cap(self):
        with psycopg.connect(DB) as conn:
            conn.execute("CREATE TABLE engine_api_usage(provider text, requested_at timestamptz DEFAULT now(), "
                         "units_reserved int, units_reported int)")
            conn.execute("INSERT INTO engine_api_usage(provider,units_reserved,units_reported) "
                         "VALUES('semrush', 40, 70)")
        session = FakeSession([])
        result = semrush.enrich(DB, session)
        # 100/month cap - 70 (engine, reported) leaves room for one 20-unit request.
        self.assertEqual((result["status"], result["requests"]), ("monthly_budget_reached", 1))

    @patch.dict(os.environ, ON, clear=True)
    def test_cost_above_estimate_pauses(self):
        with psycopg.connect(DB) as conn:
            conn.execute("CREATE TABLE seo_spend_controls(provider text PRIMARY KEY, paused boolean, "
                         "reason text, updated_at timestamptz)")
        session = FakeSession([])
        session.balances = [1000, 940]  # 60 units for a request estimated at 20
        result = semrush.enrich(DB, session)
        self.assertEqual((result["status"], result["requests"]), ("cost_exceeded_estimate", 1))
        with psycopg.connect(DB) as conn:
            self.assertTrue(conn.execute("SELECT paused FROM seo_spend_controls").fetchone()[0])
            self.assertEqual(conn.execute("SELECT units_reported FROM seo_semrush_api_usage").fetchone()[0], 60)

    @patch.dict(os.environ, ON, clear=True)
    def test_bad_payload_is_recorded_not_stored(self):
        result = semrush.enrich(DB, FakeSession([FakeResponse(payload={"meta": {"success": False}})]))
        self.assertEqual(result["failed"], 1)
        self.assertEqual(self.scalar(
            "SELECT error_category FROM seo_semrush_api_usage ORDER BY id LIMIT 1"), "response_error")

    @patch.dict(os.environ, ON, clear=True)
    def test_concurrent_run_is_refused(self):
        with psycopg.connect(DB) as holder:
            holder.execute("SELECT pg_advisory_lock(915202610)")
            session = FakeSession([])
            self.assertEqual(semrush.enrich(DB, session)["status"], "another_run_active")
            self.assertEqual(session.calls, [])

    @patch.dict(os.environ, ON, clear=True)
    def test_database_error_releases_lock(self):
        with patch("semrush.candidates", side_effect=psycopg.errors.UndefinedTable("x")):
            with self.assertRaises(psycopg.Error):
                semrush.enrich(DB, FakeSession([]))
        with psycopg.connect(DB) as conn:
            self.assertTrue(conn.execute("SELECT pg_try_advisory_lock(915202610)").fetchone()[0])


if __name__ == "__main__":
    unittest.main()
