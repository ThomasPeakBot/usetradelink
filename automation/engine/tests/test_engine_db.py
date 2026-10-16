"""Postgres-backed engine tests. Set TEST_DATABASE_URL to a disposable database."""
import json
import os
import unittest
import uuid
from datetime import date, timedelta
from unittest.mock import patch

import psycopg

from engine import competitors, db, proposals, research, run, signals, tracking
from engine.providers import semrush
from helpers import DB, REPO, good_article

SNAPSHOT = os.path.join(os.path.dirname(__file__), "fixtures", "semrush-snapshot-2026-10-16.json")
SEMRUSH_ON = {"ENGINE_SEMRUSH_ENABLED": "true", "SEMRUSH_API_KEY": "secret-test-key",
              "ENGINE_SEMRUSH_MAX_UNITS_PER_RUN": "400", "ENGINE_SEMRUSH_MAX_UNITS_PER_MONTH": "1000"}


class Resp:
    def __init__(self, text="", status=200, headers=None):
        self.text = text
        self.status_code = status
        self.headers = headers or {"content-type": "text/html"}
        self.content = text.encode()


class FakeSemrushSession:
    def __init__(self, bodies, balances=None):
        self.bodies = list(bodies)
        self.balances = list(balances or [])
        self.calls = []

    def get(self, url, params=None, timeout=None, **kw):
        if "countapiunits" in url:
            return Resp(str(self.balances.pop(0)) if self.balances else "", 200 if self.balances is not None else 500)
        self.calls.append(dict(params))
        return Resp(self.bodies.pop(0) if self.bodies else "ERROR 50 :: NOTHING FOUND")


@unittest.skipUnless(DB, "TEST_DATABASE_URL not set")
class EngineDatabaseTests(unittest.TestCase):
    def setUp(self):
        with psycopg.connect(DB, autocommit=True) as conn:
            conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        self.conn = db.connect(DB)
        self.addCleanup(self.conn.close)
        db.migrate(self.conn)

    def count(self, sql, *args):
        return self.conn.execute(sql, args).fetchone()["n"]

    def test_migrations_are_idempotent(self):
        self.assertEqual(db.migrate(self.conn), [])
        self.assertEqual(self.count("SELECT count(*) AS n FROM engine_schema_migrations"), 1)

    def test_real_snapshot_import_builds_signals(self):
        counts = research.import_snapshot(self.conn, SNAPSHOT)
        self.assertEqual(counts["phrase_these"], 12)
        sig = signals.build_signals(self.conn)
        self.assertEqual(sig["futures trade copier"]["volume"], 140)
        self.assertEqual(sig["how to copy trade on tradovate"]["kd"], 18.0)
        self.assertTrue(any(c["domain"] == "tradecopia.com" for c in sig["futures trade copier"]["competitors"]))
        self.assertEqual(sig["futures trade copier"]["evidence"][0]["source"], "semrush-mcp")

    def test_semrush_disabled_spends_nothing(self):
        session = FakeSemrushSession([])
        with patch.dict(os.environ, {"SEMRUSH_API_KEY": "k"}, clear=True):
            summary = research.run_semrush(self.conn, research.load_config(), semrush.Semrush(self.conn, session))
        self.assertEqual(summary["skipped"], ["disabled_or_no_budget"])
        self.assertEqual(session.calls, [])

    @patch.dict(os.environ, SEMRUSH_ON, clear=True)
    def test_budget_reserved_before_calls_and_capped(self):
        body = "Keyword;Search Volume;CPC;Keyword Difficulty Index\r\nfutures trade copier;140;3.42;6\r\n"
        session = FakeSemrushSession([body] * 10, balances=[5000, 4880])
        summary = research.run_semrush(self.conn, research.load_config(), semrush.Semrush(self.conn, session))
        used = self.count("SELECT COALESCE(SUM(units_reserved),0) AS n FROM engine_api_usage")
        self.assertLessEqual(used, 400)
        self.assertTrue(any("budget" in s for s in summary["skipped"]))
        first = self.conn.execute("SELECT * FROM engine_api_usage ORDER BY id LIMIT 1").fetchone()
        self.assertEqual(first["units_reported"], 120)
        # The key is sent to Semrush but never stored or logged.
        self.assertNotIn("secret-test-key", json.dumps(
            [dict(r) for r in self.conn.execute("SELECT * FROM engine_api_usage")], default=str))
        # display_limit is reduced to what the remaining budget can pay for.
        related = [c for c in session.calls if c["type"] == "phrase_related"]
        if related:
            self.assertLessEqual(int(related[0]["display_limit"]), 400 // semrush.UNITS_PER_LINE["phrase_related"])

    @patch.dict(os.environ, dict(SEMRUSH_ON, ENGINE_SEMRUSH_MAX_UNITS_PER_MONTH="150"), clear=True)
    def test_month_cap_counts_collector_usage(self):
        self.conn.execute("CREATE TABLE seo_semrush_api_usage(requested_at timestamptz DEFAULT now(), units_reserved int)")
        self.conn.execute("INSERT INTO seo_semrush_api_usage(units_reserved) VALUES (100)")
        self.conn.commit()
        client = semrush.Semrush(self.conn, FakeSemrushSession([]))
        self.assertEqual(client.affordable_lines("phrase_these", 12), 5)
        self.assertEqual(client.affordable_lines("phrase_related", 20), 0)

    @patch.dict(os.environ, SEMRUSH_ON, clear=True)
    def test_quota_error_stops_the_run(self):
        session = FakeSemrushSession(["ERROR 132 :: API UNITS BALANCE IS ZERO"] + ["x"] * 5)
        summary = research.run_semrush(self.conn, research.load_config(), semrush.Semrush(self.conn, session))
        self.assertEqual(len(session.calls), 1)
        self.assertEqual(summary["errors"], ["phrase_these:semrush_error"])

    @patch.dict(os.environ, SEMRUSH_ON, clear=True)
    def test_refresh_interval_prevents_rebuying(self):
        research.import_snapshot(self.conn, SNAPSHOT)
        self.conn.execute("UPDATE seo_keyword_metrics SET observed_on=current_date, source='semrush-v3'")
        self.conn.execute("UPDATE seo_competitor_rankings SET observed_on=current_date")
        self.conn.execute("UPDATE seo_keyword_relations SET observed_on=current_date")
        self.conn.commit()
        stale = research.stale_keywords(self.conn, ["futures trade copier", "brand new copier term"], 28)
        self.assertEqual(stale, ["brand new copier term"])

    def test_competitor_crawler_respects_robots(self):
        class S:
            def get(self, url, **kw):
                if url.endswith("/robots.txt"):
                    return Resp("User-agent: *\nDisallow: /pricing" if "tradesyncer" in url else "", 200)
                return Resp(f"<html><head><title>{url}</title></head><body><h1>Copier</h1><h2>Plans</h2>"
                            "<p>one two three</p></body></html>")
        config = research.load_config()
        research.ensure_competitors(self.conn, config)
        summary = competitors.run(self.conn, config, S())
        self.assertEqual(summary["blocked_by_robots"], 1)
        self.assertGreaterEqual(summary["fetched"], 3)
        row = self.conn.execute("SELECT * FROM seo_competitor_pages WHERE url='https://tradesyncer.com/'").fetchone()
        self.assertEqual(row["h1"], "Copier")
        self.assertEqual(competitors.run(self.conn, config, S())["fetched"], 0)  # refreshed within 7 days

    def test_full_cycle_on_real_snapshot_and_site(self):
        research.import_snapshot(self.conn, SNAPSHOT)
        r, status = run.cycle(self.conn, site_dir=REPO, today=date(2026, 10, 16))
        self.assertEqual(status, "complete", r.errors)
        self.assertEqual(r.steps["inventory"], 12)
        open_before = self.count("SELECT count(*) AS n FROM seo_opportunities WHERE status IN ('open','briefed')")
        self.assertGreater(open_before, 0)
        self.assertGreater(self.count("SELECT count(*) AS n FROM seo_content_briefs"), 0)
        brief = self.conn.execute("SELECT brief FROM seo_content_briefs LIMIT 1").fetchone()["brief"]
        self.assertTrue(brief["allowed_facts"])
        self.assertIn("ninjatrader", [f["id"] for f in brief["blocked_facts"]])
        # A second run updates the same opportunities instead of duplicating them.
        run.cycle(self.conn, site_dir=REPO, today=date(2026, 10, 16))
        self.assertEqual(self.count("SELECT count(*) AS n FROM seo_opportunities WHERE status IN ('open','briefed')"),
                         open_before)
        self.assertIn("| Priority |", run.report(self.conn))
        self.assertEqual(self.conn.execute("SELECT status FROM engine_runs ORDER BY started_at DESC LIMIT 1")
                         .fetchone()["status"], "complete")

    def test_failing_step_is_recorded_and_run_marked_failed(self):
        with patch("engine.research.run_semrush", side_effect=RuntimeError("boom secret-key")):
            r, status = run.cycle(self.conn, site_dir=REPO)
        self.assertEqual(status, "failed")
        self.assertEqual(r.errors[0]["step"], "semrush")
        self.assertNotIn("secret-key", json.dumps(r.errors))

    def _approval_tables(self):
        self.conn.execute("CREATE TABLE seo_approval_proposals(id uuid PRIMARY KEY, pr_number int, status text)")
        self.conn.execute("CREATE TABLE seo_approval_events(id bigserial, proposal_id uuid, kind text, actor text, "
                          "detail text, created_at timestamptz)")
        self.conn.execute("CREATE TABLE seo_approval_jobs(proposal_id uuid, result text)")
        self.conn.execute("CREATE TABLE seo_search_performance(data_date date, page text, query text, clicks float, "
                          "impressions float, ctr float, position float)")

    def test_published_change_is_tracked_and_evaluated(self):
        self._approval_tables()
        pid = uuid.uuid4()
        published = date(2026, 6, 1)
        self.conn.execute("INSERT INTO seo_approval_proposals VALUES(%s,42,'published_verified')", (pid,))
        self.conn.execute("INSERT INTO seo_approval_jobs VALUES(%s,'github_merged:" + "a" * 40 + "')", (pid,))
        self.conn.execute("INSERT INTO seo_approval_events(proposal_id,kind,actor,detail,created_at) VALUES"
                          "(%s,'published_paths','system',%s,%s)", (pid, json.dumps(["resources/index.html", "sitemap.xml"]),
                                                                     published))
        url = "https://usetradelink.com/resources/"
        for d in range(1, 29):
            self.conn.execute("INSERT INTO seo_search_performance VALUES(%s,%s,'q',1,10,0.1,12)",
                              (published - timedelta(days=d), url))
            self.conn.execute("INSERT INTO seo_search_performance VALUES(%s,%s,'q',3,20,0.15,8)",
                              (published + timedelta(days=6 + d), url))
        self.conn.commit()
        self.assertEqual(tracking.sync_changes(self.conn), 1)
        self.assertEqual(tracking.sync_changes(self.conn), 0)
        change = self.conn.execute("SELECT * FROM seo_changes").fetchone()
        self.assertEqual((change["pages"], change["merged_sha"]), ([url], "a" * 40))
        self.assertEqual(tracking.evaluate(self.conn, date(2026, 10, 1)), 1)
        ev = self.conn.execute("SELECT * FROM seo_change_evaluations").fetchone()
        self.assertEqual(ev["verdict"], "improved")
        self.assertEqual(ev["after"]["conversions"], "not_connected")
        # The changed page is in cooling-off for new recommendations.
        self.assertEqual(signals.recent_changes(self.conn), {url: published})

    def test_small_samples_are_not_judged(self):
        self.assertEqual(tracking.verdict({"clicks": 1, "impressions": 20}, {"clicks": 5, "impressions": 40}),
                         "insufficient_data")
        self.assertEqual(tracking.verdict({"clicks": 50, "impressions": 1000}, {"clicks": 30, "impressions": 900}),
                         "declined")
        self.assertEqual(tracking.verdict({"clicks": 50, "impressions": 1000}, {"clicks": 52, "impressions": 1010}),
                         "no_significant_change")

    def test_draft_quality_gate_and_proposal_with_mocked_services(self):
        research.import_snapshot(self.conn, SNAPSHOT)
        run.cycle(self.conn, site_dir=REPO, today=date(2026, 10, 16))
        opp = self.conn.execute("SELECT id FROM seo_opportunities LIMIT 1").fetchone()["id"]
        self.conn.execute("UPDATE seo_opportunities SET action='new_page', status='briefed' WHERE id=%s", (opp,))
        brief = {"target_url": "https://usetradelink.com/guides/how-to-copy-trade-on-tradovate/",
                 "path": "guides/how-to-copy-trade-on-tradovate/index.html", "content_type": "article",
                 "head_keyword": "copy trades on tradovate", "why": "test", "internal_links": []}
        self.conn.execute("DELETE FROM seo_content_briefs")
        self.conn.execute("INSERT INTO seo_content_briefs(id,opportunity_id,brief,markdown) VALUES(%s,%s,%s,'')",
                          (uuid.uuid4(), opp, json.dumps(brief)))
        self.conn.commit()
        body = good_article().split("<h1>How to copy trades on Tradovate</h1>")[1].split("</body>")[0]
        draft = {"title": "How to Copy Trades on Tradovate Step by Step",
                 "meta_description": "A practical walkthrough of copying futures trades across Tradovate accounts, with the checks that prevent drift.",
                 "h1": "How to copy trades on Tradovate", "intro_html": "<p>Start with one leader account.</p>",
                 "sections": [{"heading": "Checks before the open", "html": body}], "faq": [],
                 "sources": [], "unverifiable_points": []}
        calls = []

        class FakeGitHub:
            def __call__(self, method, path, **kw):
                calls.append((method, path))
                if path.endswith("pulls?state=open&per_page=100"):
                    return []
                if "/git/ref/heads/main" in path:
                    return {"object": {"sha": "b" * 40}}
                if "/contents/" in path and method == "GET":
                    raise proposals.ProposalError("github_http_404")
                if path.endswith("/pulls"):
                    return {"number": 77}
                return {}
        env = {"CONTENT_LLM_ENABLED": "true", "ENGINE_PROPOSALS_ENABLED": "true", "ENGINE_GITHUB_TOKEN": "t"}
        with patch.dict(os.environ, env), \
                patch("engine.drafting.draft_with_claude", return_value=(draft, {"input_tokens": 1, "output_tokens": 2})), \
                patch("engine.proposals.GitHub", return_value=FakeGitHub()):
            pages = run.inventory.from_directory(REPO)
            summary = run.draft_and_propose(self.conn, pages, [], research.load_config())
        stored = self.conn.execute("SELECT * FROM seo_content_drafts").fetchone()
        self.assertEqual(stored["status"], "proposed", stored["quality"])
        self.assertEqual(stored["pr_number"], 77)
        self.assertEqual(summary["proposed"], 1)
        self.assertIn(("PUT", "/repos/ThomasPeakBot/usetradelink/contents/guides/how-to-copy-trade-on-tradovate/index.html"), calls)
        self.assertIn(("POST", "/repos/ThomasPeakBot/usetradelink/issues/77/labels"), calls)
        self.assertEqual(self.count("SELECT count(*) AS n FROM engine_api_usage WHERE provider='anthropic'"), 1)


class ProposalGuardTests(unittest.TestCase):
    def test_paths_outside_policy_are_refused(self):
        for path in ("index.html", "tracking.js", ".github/workflows/x.yml", "automation/engine/run.py"):
            with self.assertRaises(proposals.ProposalError):
                proposals.open_proposal({path: "x"}, "t", "b", "h", gh=lambda *a, **k: [])

    def test_open_proposal_limit(self):
        gh = lambda method, path, **kw: [{"labels": [{"name": "seo-agent-proposal"}]}] * 3
        with self.assertRaisesRegex(proposals.ProposalError, "too_many_open"):
            proposals.open_proposal({"guides/x/index.html": "x"}, "t", "b", "h", gh=gh, max_open=3)

    def test_disabled_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(proposals.enabled())

    def test_sitemap_insert_is_idempotent(self):
        xml = "<urlset>\n</urlset>"
        once = proposals.add_to_sitemap(xml, "https://usetradelink.com/guides/x/", "2026-10-16")
        self.assertEqual(proposals.add_to_sitemap(once, "https://usetradelink.com/guides/x/", "2026-10-16"), once)


if __name__ == "__main__":
    unittest.main()
