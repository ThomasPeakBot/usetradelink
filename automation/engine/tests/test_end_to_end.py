"""Whole publication pipeline with mocked GitHub and Slack and a real Postgres.

engine proposes a PR -> approvals discovers it and posts to Slack -> a signed
Slack "Approve" is accepted -> executor merges exactly that SHA -> engine
records the published change for measurement. Also checks that "Reject" and a
disarmed service publish nothing.
"""
import hashlib
import hmac
import importlib.util
import json
import os
import sys
import time
import unittest
from urllib.parse import urlencode
from unittest.mock import patch

import psycopg

from engine import db, tracking
from helpers import DB, REPO

SECRET = "e2e-signing-secret"
SHA = "c" * 40
ENV = {"DATABASE_URL": DB, "PUBLISHING_ENABLED": "true", "BRANCH_PROTECTION_VERIFIED": "true",
       "RAILWAY_GIT_BRANCH": "main", "GITHUB_TOKEN": "t", "SLACK_BOT_TOKEN": "b", "SLACK_SIGNING_SECRET": SECRET,
       "SLACK_APPROVER_IDS": "U1", "SLACK_TEAM_ID": "T1", "SLACK_CHANNEL_ID": "C1"}


def load_gateway():
    path = os.path.join(REPO, "automation", "approvals")
    if path not in sys.path:
        sys.path.insert(0, path)
    spec = importlib.util.spec_from_file_location("approvals_app", os.path.join(path, "app.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


PR = {"number": 91, "state": "open", "merged": False, "draft": False, "mergeable": True, "title": "SEO: guide",
      "body": "Automated SEO proposal", "labels": [{"name": "seo-agent-proposal"}], "base": {"ref": "main"},
      "head": {"sha": SHA, "repo": {"full_name": "ThomasPeakBot/usetradelink"}}}


class FakeGitHub:
    def __init__(self):
        self.merges = []

    def __call__(self, method, path, raw=False, **kw):
        if path.endswith("/pulls?state=open&per_page=100"):
            return [PR]
        if path.endswith("/pulls/91") and method == "GET":
            return PR
        if "/pulls/91/files" in path:
            return [{"filename": "guides/how-to-copy-trade-on-tradovate/index.html", "status": "added"},
                    {"filename": "sitemap.xml", "status": "modified"}]
        if "/check-runs" in path:
            return {"total_count": 1, "check_runs": [{"name": "seo-content-integrity", "status": "completed",
                                                      "conclusion": "success", "app": {"slug": "github-actions"}}]}
        if path.endswith(f"/commits/{SHA}/status"):
            return {"total_count": 0, "state": "pending"}
        if path.endswith("/pulls/91/merge") and method == "PUT":
            self.merges.append(kw["json"]["sha"])
            return {"merged": True, "sha": "d" * 40}
        raise AssertionError(f"unexpected GitHub call {method} {path}")


@unittest.skipUnless(DB, "TEST_DATABASE_URL not set")
@patch.dict(os.environ, ENV, clear=True)
class PublicationPipelineTests(unittest.TestCase):
    def setUp(self):
        with psycopg.connect(DB, autocommit=True) as conn:
            conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        self.gw = load_gateway()
        with psycopg.connect(DB, autocommit=True) as conn:
            conn.execute(self.gw.DDL)
        self.conn = db.connect(DB)
        self.addCleanup(self.conn.close)
        db.migrate(self.conn)
        self.github = FakeGitHub()
        self.slack = []
        for target, value in (("github", self.github), ("slack_request", self._slack),
                              ("branch_protection_ok", lambda *a: True),
                              ("verify_live_content", lambda *a, **k: False), ("respond", lambda *a: None),
                              ("notify_async", lambda *a: None), ("inform", lambda *a: None)):
            p = patch.object(self.gw, target, value)
            p.start()
            self.addCleanup(p.stop)

    def _slack(self, method, data, timeout=5):
        self.slack.append((method, data))
        return {"ok": True, "channel": "C1", "ts": "1700000000.1"}

    def click(self, action):
        pid = self.conn.execute("SELECT id FROM seo_approval_proposals").fetchone()["id"]
        payload = {"type": "block_actions", "user": {"id": "U1"}, "team": {"id": "T1"}, "channel": {"id": "C1"},
                   "response_url": "https://hooks.slack.com/x", "actions": [{"action_id": action, "value": str(pid)}]}
        body = urlencode({"payload": json.dumps(payload)}).encode()
        ts = str(int(time.time()))
        sig = "v0=" + hmac.new(SECRET.encode(), b"v0:" + ts.encode() + b":" + body, hashlib.sha256).hexdigest()
        from fastapi.testclient import TestClient
        with patch.object(self.gw, "publish_loop"), patch.object(self.gw, "proposal_discovery_loop"), \
                TestClient(self.gw.app) as client:
            r = client.post("/slack/interactions", content=body, headers={
                "X-Slack-Request-Timestamp": ts, "X-Slack-Signature": sig,
                "Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(r.status_code, 200)

    def status(self):
        return self.conn.execute("SELECT status FROM seo_approval_proposals").fetchone()["status"]

    def test_approve_merges_exact_sha_and_starts_tracking(self):
        self.gw.discover_once()
        self.assertEqual(self.slack[0][0], "chat.postMessage")
        self.assertIn(SHA[:12], json.dumps(self.slack[0][1]["blocks"]))
        self.click("seo_approve")
        self.assertEqual(self.status(), "approved_queued")
        self.assertTrue(self.gw.publish_once())
        self.assertEqual(self.github.merges, [SHA])
        self.assertEqual(self.status(), "merged_verification_pending")
        self.assertEqual(tracking.sync_changes(self.conn), 1)
        change = self.conn.execute("SELECT * FROM seo_changes").fetchone()
        self.assertEqual(change["pages"], ["https://usetradelink.com/guides/how-to-copy-trade-on-tradovate/"])
        self.assertEqual(change["merged_sha"], "d" * 40)
        kinds = [r["kind"] for r in self.conn.execute("SELECT kind FROM seo_approval_events ORDER BY id")]
        self.assertEqual(kinds, ["auto_discovered", "approved_queued", "execution_started",
                                 "merged_verification_pending", "published_paths"])

    def test_reject_publishes_nothing(self):
        self.gw.discover_once()
        self.click("seo_reject")
        self.assertEqual(self.status(), "rejected")
        self.assertFalse(self.gw.publish_once())
        self.assertEqual(self.github.merges, [])

    def test_disarmed_service_ignores_approval(self):
        self.gw.discover_once()
        with patch.dict(os.environ, {"PUBLISHING_ENABLED": "false"}):
            self.click("seo_approve")
        self.assertEqual(self.status(), "pending")
        self.assertEqual(self.github.merges, [])

    def test_development_branch_deploy_ignores_approval(self):
        self.gw.discover_once()
        with patch.dict(os.environ, {"RAILWAY_GIT_BRANCH": "seo/phase2-slack-approvals-20261016"}):
            self.click("seo_approve")
            self.assertFalse(self.gw.armed())
        self.assertEqual(self.status(), "pending")


if __name__ == "__main__":
    unittest.main()
