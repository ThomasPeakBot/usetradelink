"""Integration tests against a real, disposable Postgres.

Set TEST_DATABASE_URL (CI starts a Postgres service). Every test drops and
recreates the approval tables, so never point this at a shared database.
"""
import os
import unittest
import uuid
from unittest.mock import patch

import psycopg

import app as gateway

DB = os.getenv("TEST_DATABASE_URL", "")
ENV = {"DATABASE_URL": DB, "PUBLISHING_ENABLED": "true", "BRANCH_PROTECTION_VERIFIED": "true",
       "GITHUB_TOKEN": "t", "SLACK_BOT_TOKEN": "b", "SLACK_SIGNING_SECRET": "s",
       "SLACK_APPROVER_IDS": "U1", "SLACK_TEAM_ID": "T1", "SLACK_CHANNEL_ID": "C1"}
SHA1, SHA2 = "1" * 40, "2" * 40


@unittest.skipUnless(DB, "TEST_DATABASE_URL not set")
@patch.dict(os.environ, ENV, clear=True)
class ApprovalDatabaseFlows(unittest.TestCase):
    def setUp(self):
        with psycopg.connect(DB, autocommit=True) as conn:
            conn.execute("DROP TABLE IF EXISTS seo_approval_jobs, seo_approval_events, "
                         "seo_approval_proposals, seo_slack_deliveries, "
                         "seo_search_performance, seo_semrush_keyword_metrics CASCADE")
            conn.execute(gateway.DDL)
        patcher = patch("app.inform")
        self.inform = patcher.start()
        self.addCleanup(patcher.stop)

    def rows(self, sql, *args):
        with gateway.connection() as conn:
            return conn.execute(sql, args).fetchall()

    def proposal(self, sha=SHA1):
        return gateway.register_proposal(25, sha, "Guide update", "Adds a sourced guide", "test")

    def test_new_sha_supersedes_pending_proposal(self):
        old = self.proposal(SHA1)
        new = self.proposal(SHA2)
        status = {r["commit_sha"]: r["status"] for r in self.rows(
            "SELECT commit_sha,status FROM seo_approval_proposals")}
        self.assertEqual(status, {SHA1: "superseded", SHA2: "pending"})
        self.assertIsNone(gateway.update_status(old["id"], ["pending"], "approved_queued", "U1"))
        self.assertIsNotNone(gateway.update_status(new["id"], ["pending"], "approved_queued", "U1"))

    def test_duplicate_registration_is_idempotent(self):
        a, b = self.proposal(), self.proposal()
        self.assertEqual(a["id"], b["id"])
        self.assertEqual(len(self.rows("SELECT 1 FROM seo_approval_events")), 1)

    def test_approval_is_single_use_and_rejection_cancels_queue(self):
        p = self.proposal()
        self.assertIsNotNone(gateway.update_status(p["id"], ["pending"], "approved_queued", "U1"))
        self.assertIsNone(gateway.update_status(p["id"], ["pending"], "approved_queued", "U1"))
        self.assertEqual(len(self.rows("SELECT 1 FROM seo_approval_jobs")), 1)
        gateway.update_status(p["id"], ["pending", "approved_queued"], "rejected", "U1")
        self.assertEqual(self.rows("SELECT 1 FROM seo_approval_jobs"), [])
        with patch("app.github") as gh:
            self.assertFalse(gateway.publish_once())
            gh.assert_not_called()

    def test_revision_request_blocks_approval(self):
        p = self.proposal()
        gateway.update_status(p["id"], ["pending"], "revision_requested", "U1", "Fix intro")
        self.assertIsNone(gateway.update_status(p["id"], ["pending"], "approved_queued", "U1"))

    def test_replayed_slack_delivery_detected(self):
        self.assertTrue(gateway.claim_delivery("v0=abc"))
        self.assertFalse(gateway.claim_delivery("v0=abc"))
        self.assertTrue(gateway.claim_delivery("v0=abd"))

    def queue(self):
        p = self.proposal()
        gateway.update_status(p["id"], ["pending"], "approved_queued", "U1")
        return p

    def final(self, p):
        return self.rows("SELECT p.status, j.state, j.result FROM seo_approval_proposals p "
                         "JOIN seo_approval_jobs j ON j.proposal_id=p.id WHERE p.id=%s", p["id"])[0]

    def test_edited_pr_after_approval_is_not_merged(self):
        p = self.queue()
        with patch("app.fresh_pr", return_value={"number": 25, "head": {"sha": SHA2}}), \
                patch("app.github") as gh:
            self.assertTrue(gateway.publish_once())
            gh.assert_not_called()
        row = self.final(p)
        self.assertEqual((row["status"], row["state"]), ("needs_attention", "done"))
        self.assertIn("SHA changed", row["result"])

    def test_disarmed_after_approval_is_not_merged(self):
        p = self.queue()
        with patch.dict(os.environ, {"PUBLISHING_ENABLED": "false"}), patch("app.github") as gh:
            self.assertTrue(gateway.publish_once())
            gh.assert_not_called()
        self.assertEqual(self.final(p)["status"], "needs_attention")

    def test_failed_ci_is_not_merged(self):
        p = self.queue()
        with patch("app.fresh_pr", return_value={"number": 25}), \
                patch("app.validate_publishable", side_effect=ValueError("A GitHub check failed")), \
                patch("app.github") as gh:
            gateway.publish_once()
            gh.assert_not_called()
        self.assertIn("check failed", self.final(p)["result"])

    def test_merge_without_live_proof_is_pending_verification(self):
        p = self.queue()
        with patch("app.fresh_pr", return_value={"number": 25}), \
                patch("app.validate_publishable", return_value=["resources/index.html"]), \
                patch("app.github", return_value={"merged": True, "sha": "f" * 40}) as gh, \
                patch("app.verify_live_content", return_value=False):
            gateway.publish_once()
        method, path = gh.call_args[0][:2]
        self.assertEqual((method, path), ("PUT", "/repos/ThomasPeakBot/usetradelink/pulls/25/merge"))
        self.assertEqual(gh.call_args[1]["json"]["sha"], SHA1)
        self.assertEqual(self.final(p)["status"], "merged_verification_pending")

    def test_stale_running_job_needs_a_human(self):
        p = self.queue()
        with gateway.connection() as conn:
            conn.execute("UPDATE seo_approval_jobs SET state='running', "
                         "started_at=now()-interval '1 hour'")
        self.assertEqual(gateway.reap_stale_jobs(), [p["id"]])
        self.assertEqual(self.final(p)["status"], "needs_attention")
        with patch("app.github") as gh:
            self.assertFalse(gateway.publish_once())
            gh.assert_not_called()

    def test_discovery_retries_failed_slack_post(self):
        pr = {"number": 25, "title": "Guide", "body": "Adds a guide",
              "labels": [{"name": "seo-agent-proposal"}], "base": {"ref": "main"},
              "head": {"sha": SHA1, "repo": {"full_name": gateway.REPO}}}
        with patch("app.github", return_value=[pr]), \
                patch("app.slack_request", side_effect=RuntimeError("slack_network_error")):
            with self.assertRaises(RuntimeError):
                gateway.discover_once()
        with patch("app.github", return_value=[pr]), \
                patch("app.slack_request", return_value={"channel": "C1", "ts": "1.2"}) as slack:
            gateway.discover_once()
            gateway.discover_once()
        self.assertEqual(slack.call_count, 1)
        self.assertEqual(self.rows("SELECT slack_ts FROM seo_approval_proposals")[0]["slack_ts"], "1.2")

    def test_unlabelled_or_fork_prs_are_not_discovered(self):
        pr = {"number": 26, "title": "x", "labels": [], "base": {"ref": "main"},
              "head": {"sha": SHA1, "repo": {"full_name": gateway.REPO}}}
        fork = dict(pr, labels=[{"name": "seo-agent-proposal"}],
                    head={"sha": SHA2, "repo": {"full_name": "someone/fork"}})
        with patch("app.github", return_value=[pr, fork]), patch("app.slack_request") as slack:
            gateway.discover_once()
            slack.assert_not_called()
        self.assertEqual(self.rows("SELECT 1 FROM seo_approval_proposals"), [])

    def test_opportunities_without_collector_tables(self):
        with patch.dict(os.environ, {"PROPOSAL_API_TOKEN": "x"}):
            result = gateway.opportunities("Bearer x")
        self.assertEqual(result["candidates"], [])


if __name__ == "__main__":
    unittest.main()
