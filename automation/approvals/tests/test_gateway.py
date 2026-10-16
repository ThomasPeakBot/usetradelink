import hashlib
import hmac
import json
import os
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from app import app, allowed_path, signed_request, validate_publishable, armed

class SignatureTests(unittest.TestCase):
    def test_slack_signature_and_replay(self):
        body = b"payload=%7B%22type%22%3A%22block_actions%22%7D"
        now = int(time.time())
        signature = "v0=" + hmac.new(b"secret", b"v0:" + str(now).encode() + b":" + body,
                                     hashlib.sha256).hexdigest()
        self.assertTrue(signed_request(body, str(now), signature, "secret", now))
        self.assertFalse(signed_request(body + b"x", str(now), signature, "secret", now))
        self.assertFalse(signed_request(body, str(now), signature, "secret", now + 400))
        self.assertFalse(signed_request(body, str(now), signature, "", now))

    def test_paths_are_content_only(self):
        for path in ("resources/index.html", "blog/new-guide/index.html",
                     "compare/tradelink-vs-competitor/index.html",
                     "futures-trade-copier/index.html", "sitemap.xml", "llms.txt"):
            self.assertTrue(allowed_path(path), path)
        for path in ("index.html", "main.js", "tracking.js", "assets/logo.png",
                     ".github/workflows/deploy.yml", "automation/approvals/app.py",
                     "resources/../../main.js", "seo.css", "robots.txt", "checkout/index.html"):
            self.assertFalse(allowed_path(path), path)

    @patch.dict(os.environ, {"PUBLISHING_ENABLED": "false"}, clear=True)
    def test_disarmed_by_default(self):
        self.assertFalse(armed())
        pr = {"number": 10, "draft": False, "mergeable": True,
              "head": {"sha": "a" * 40}}
        with self.assertRaises(ValueError):
            validate_publishable(pr, "a" * 40)

    @patch("app.armed", return_value=True)
    @patch("app.github")
    def test_successful_exact_commit_content_pr(self, mock_gh, mock_armed):
        sha = "b" * 40
        pr = {"number": 25, "draft": False, "mergeable": True, "head": {"sha": sha}}
        mock_gh.side_effect = [
            [{"filename": "blog/real-guide/index.html", "status": "added"},
             {"filename": "sitemap.xml", "status": "modified"}],
            {"check_runs": [{"name": "seo-content-integrity", "conclusion": "success"}]},
        ]
        self.assertEqual(len(validate_publishable(pr, sha)), 2)

    @patch("app.armed", return_value=True)
    @patch("app.github")
    def test_blocks_unapproved_file(self, mock_gh, mock_armed):
        sha = "c" * 40
        mock_gh.return_value = [{"filename": "tracking.js", "status": "modified"}]
        with self.assertRaisesRegex(ValueError, "high-risk"):
            validate_publishable({"number": 3, "draft": False, "mergeable": True,
                                  "head": {"sha": sha}}, sha)

    @patch("app.armed", return_value=True)
    def test_blocks_sha_changed(self, mock_armed):
        with self.assertRaisesRegex(ValueError, "SHA changed"):
            validate_publishable({"number": 1, "draft": False, "mergeable": True,
                                  "head": {"sha": "a" * 40}}, "b" * 40)

    def test_unsigned_webhook_rejected(self):
        with TestClient(app) as client:
            r = client.post("/slack/interactions", data={"payload": "{}"})
        self.assertEqual(r.status_code, 401)

if __name__ == "__main__":
    unittest.main()
