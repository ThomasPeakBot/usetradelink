import hashlib
import hmac
import json
import os
import time
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

from fastapi.testclient import TestClient

import app as gateway
from app import (app, allowed_path, branch_protection_ok, normalize_html, signed_request,
                 validate_publishable, verify_checks, armed)

SECRET = "test-signing-secret"
ARMED_ENV = {"PUBLISHING_ENABLED": "true", "BRANCH_PROTECTION_VERIFIED": "true",
             "GITHUB_TOKEN": "t", "SLACK_BOT_TOKEN": "b", "SLACK_SIGNING_SECRET": SECRET,
             "SLACK_APPROVER_IDS": "U1", "SLACK_TEAM_ID": "T1", "SLACK_CHANNEL_ID": "C1",
             "DATABASE_URL": "postgresql://unused"}
PID = "6f1c1d0e-8a7b-4c55-9a43-2f3a1e5b7c90"


def good_pr(sha="b" * 40, **extra):
    pr = {"number": 25, "draft": False, "mergeable": True, "head": {"sha": sha},
          "labels": [{"name": "seo-agent-proposal"}]}
    pr.update(extra)
    return pr


def actions_check(name="seo-content-integrity", conclusion="success", slug="github-actions",
                  status="completed"):
    return {"name": name, "conclusion": conclusion, "status": status, "app": {"slug": slug}}


def classic_protection(**overrides):
    p = {"required_status_checks": {"contexts": ["seo-content-integrity"]},
         "required_pull_request_reviews": {"required_approving_review_count": 0,
                                           "bypass_pull_request_allowances":
                                               {"users": [], "teams": [], "apps": []}},
         "enforce_admins": {"enabled": True}, "allow_force_pushes": {"enabled": False},
         "allow_deletions": {"enabled": False}}
    p.update(overrides)
    return p


def signed(body, ts=None, secret=SECRET):
    ts = str(int(time.time()) if ts is None else ts)
    sig = "v0=" + hmac.new(secret.encode(), b"v0:" + ts.encode() + b":" + body,
                           hashlib.sha256).hexdigest()
    return {"X-Slack-Request-Timestamp": ts, "X-Slack-Signature": sig,
            "Content-Type": "application/x-www-form-urlencoded"}


def action_payload(action_id="seo_approve", user="U1", team="T1", channel="C1", value=PID):
    return {"type": "block_actions", "user": {"id": user}, "team": {"id": team},
            "channel": {"id": channel}, "trigger_id": "trig",
            "response_url": "https://hooks.slack.com/actions/T1/1/x",
            "actions": [{"action_id": action_id, "value": value}]}


def form(payload):
    return urlencode({"payload": json.dumps(payload)}).encode()


class SignatureTests(unittest.TestCase):
    def test_slack_signature_and_replay_window(self):
        body = b"payload=%7B%22type%22%3A%22block_actions%22%7D"
        now = int(time.time())
        signature = signed(body, now, "secret")["X-Slack-Signature"]
        self.assertTrue(signed_request(body, str(now), signature, "secret", now))
        self.assertFalse(signed_request(body + b"x", str(now), signature, "secret", now))
        self.assertFalse(signed_request(body, str(now), signature, "secret", now + 400))
        self.assertFalse(signed_request(body, str(now), signature, "", now))
        self.assertFalse(signed_request(body, "not-a-number", signature, "secret", now))

    def test_paths_are_content_only(self):
        for path in ("resources/index.html", "blog/new-guide/index.html",
                     "compare/tradelink-vs-competitor/index.html",
                     "futures-trade-copier/index.html", "sitemap.xml", "llms.txt"):
            self.assertTrue(allowed_path(path), path)
        for path in ("index.html", "main.js", "tracking.js", "assets/logo.png",
                     ".github/workflows/deploy.yml", "automation/approvals/app.py",
                     "resources/../../main.js", "seo.css", "robots.txt", "checkout/index.html",
                     "resources/evil.js", "CNAME", "404.html"):
            self.assertFalse(allowed_path(path), path)


class PublishGateTests(unittest.TestCase):
    def setUp(self):
        gateway._protection_cache.update(at=0.0, ok=False)

    @patch.dict(os.environ, {"PUBLISHING_ENABLED": "false"}, clear=True)
    def test_disarmed_by_default(self):
        self.assertFalse(armed())
        with self.assertRaises(ValueError):
            validate_publishable(good_pr("a" * 40), "a" * 40)

    @patch.dict(os.environ, dict(ARMED_ENV, SLACK_CHANNEL_ID=""), clear=True)
    def test_missing_channel_disarms(self):
        self.assertFalse(armed())

    @patch("app.branch_protection_ok", return_value=True)
    @patch("app.armed", return_value=True)
    @patch("app.github")
    def test_successful_exact_commit_content_pr(self, mock_gh, _armed, _prot):
        sha = "b" * 40
        mock_gh.side_effect = [
            [{"filename": "blog/real-guide/index.html", "status": "added"},
             {"filename": "sitemap.xml", "status": "modified"}],
            {"total_count": 1, "check_runs": [actions_check()]},
            {"total_count": 0, "state": "pending"},
        ]
        self.assertEqual(len(validate_publishable(good_pr(sha), sha)), 2)

    @patch("app.branch_protection_ok", return_value=True)
    @patch("app.armed", return_value=True)
    @patch("app.github")
    def test_blocks_unapproved_file(self, mock_gh, _armed, _prot):
        sha = "c" * 40
        mock_gh.return_value = [{"filename": "tracking.js", "status": "modified"}]
        with self.assertRaisesRegex(ValueError, "high-risk"):
            validate_publishable(good_pr(sha), sha)

    @patch("app.branch_protection_ok", return_value=True)
    @patch("app.armed", return_value=True)
    @patch("app.github")
    def test_blocks_deletion_and_rename(self, mock_gh, _armed, _prot):
        sha = "c" * 40
        for status in ("removed", "renamed"):
            mock_gh.return_value = [{"filename": "resources/index.html", "status": status}]
            with self.assertRaisesRegex(ValueError, "high-risk"):
                validate_publishable(good_pr(sha), sha)

    @patch("app.armed", return_value=True)
    def test_blocks_sha_changed(self, _armed):
        with self.assertRaisesRegex(ValueError, "SHA changed"):
            validate_publishable(good_pr("a" * 40), "b" * 40)

    @patch("app.armed", return_value=True)
    def test_blocks_draft_unlabelled_and_unknown_mergeability(self, _armed):
        sha = "a" * 40
        with self.assertRaisesRegex(ValueError, "Draft"):
            validate_publishable(good_pr(sha, draft=True), sha)
        with self.assertRaisesRegex(ValueError, "label"):
            validate_publishable(good_pr(sha, labels=[]), sha)
        with self.assertRaisesRegex(ValueError, "mergeable"):
            validate_publishable(good_pr(sha, mergeable=None), sha)

    @patch("app.branch_protection_ok", return_value=False)
    @patch("app.armed", return_value=True)
    @patch("app.github")
    def test_env_flag_alone_does_not_prove_protection(self, mock_gh, _armed, _prot):
        sha = "a" * 40
        with self.assertRaisesRegex(ValueError, "protection"):
            validate_publishable(good_pr(sha), sha)
        mock_gh.assert_not_called()

    @patch("app.github")
    def test_check_from_other_app_is_not_trusted(self, mock_gh):
        mock_gh.return_value = {"total_count": 1,
                                "check_runs": [actions_check(slug="some-other-app")]}
        with self.assertRaisesRegex(ValueError, "has not passed"):
            verify_checks("a" * 40)

    @patch("app.github")
    def test_failed_running_or_unverifiable_checks_block(self, mock_gh):
        cases = [
            {"total_count": 2, "check_runs": [actions_check(), actions_check("lint", "failure")]},
            {"total_count": 2, "check_runs": [actions_check(),
                                              actions_check("lint", None, status="in_progress")]},
            {"total_count": 1, "check_runs": [actions_check(conclusion="failure")]},
            {"total_count": 150, "check_runs": [actions_check()]},
        ]
        for case in cases:
            mock_gh.side_effect = [case]
            with self.assertRaises(ValueError):
                verify_checks("a" * 40)
        mock_gh.side_effect = [{"total_count": 1, "check_runs": [actions_check()]},
                               {"total_count": 1, "state": "failure"}]
        with self.assertRaisesRegex(ValueError, "commit status"):
            verify_checks("a" * 40)

    @patch("app.time.sleep")
    @patch("app.pr_details")
    def test_fresh_pr_waits_for_mergeability(self, mock_pr, _sleep):
        mock_pr.side_effect = [good_pr(mergeable=None), good_pr(mergeable=True)]
        self.assertTrue(gateway.fresh_pr(25)["mergeable"])
        self.assertEqual(mock_pr.call_count, 2)


class BranchProtectionTests(unittest.TestCase):
    def setUp(self):
        gateway._protection_cache.update(at=0.0, ok=False)

    def check(self, responses):
        gateway._protection_cache.update(at=0.0, ok=False)

        def fake(method, path, **_kw):
            for suffix, value in responses.items():
                if path.endswith(suffix):
                    return value
            raise RuntimeError("github_http_404")
        with patch("app.github", side_effect=fake):
            return branch_protection_ok(now=1e9)

    def test_classic_protection_accepted(self):
        self.assertTrue(self.check({"/branches/main/protection": classic_protection()}))

    def test_classic_protection_weaknesses_rejected(self):
        weak = [
            classic_protection(required_status_checks={"contexts": ["other"]}),
            classic_protection(required_pull_request_reviews=None),
            classic_protection(enforce_admins={"enabled": False}),
            classic_protection(allow_force_pushes={"enabled": True}),
            classic_protection(required_pull_request_reviews={
                "bypass_pull_request_allowances": {"apps": [{"slug": "bot"}]}}),
        ]
        for p in weak:
            self.assertFalse(self.check({"/branches/main/protection": p}))

    def test_rulesets_require_no_bypass_for_executor(self):
        rules = [{"type": "pull_request", "ruleset_id": 7},
                 {"type": "non_fast_forward", "ruleset_id": 7},
                 {"type": "deletion", "ruleset_id": 7},
                 {"type": "required_status_checks", "ruleset_id": 7,
                  "parameters": {"required_status_checks": [{"context": "seo-content-integrity"}]}}]
        ok = {"enforcement": "active", "current_user_can_bypass": "never"}
        self.assertTrue(self.check({"/rules/branches/main": rules, "/rulesets/7": ok}))
        self.assertFalse(self.check({"/rules/branches/main": rules,
                                     "/rulesets/7": dict(ok, current_user_can_bypass="always")}))
        self.assertFalse(self.check({"/rules/branches/main": rules,
                                     "/rulesets/7": dict(ok, enforcement="evaluate")}))
        self.assertFalse(self.check({"/rules/branches/main": rules[:2], "/rulesets/7": ok}))

    def test_no_access_fails_closed(self):
        self.assertFalse(self.check({}))

    def test_result_is_cached_briefly(self):
        with patch("app.classic_protection_ok", return_value=True) as classic:
            self.assertTrue(branch_protection_ok(now=1000.0))
            self.assertTrue(branch_protection_ok(now=1100.0))
            self.assertEqual(classic.call_count, 1)


@patch.dict(os.environ, ARMED_ENV, clear=True)
class InteractionTests(unittest.TestCase):
    def setUp(self):
        for name, value in (("claim_delivery", True), ("update_status", None),
                            ("respond", None), ("notify_async", None), ("slack_request", {})):
            patcher = patch(f"app.{name}", return_value=value)
            setattr(self, name, patcher.start())
            self.addCleanup(patcher.stop)

    def post(self, payload, headers=None, body=None):
        body = form(payload) if body is None else body
        # The startup hook must not touch a database or start background workers.
        with patch("app.connection"), patch("app.publish_loop"), \
                patch("app.proposal_discovery_loop"), TestClient(app) as client:
            return client.post("/slack/interactions", content=body,
                               headers=signed(body) if headers is None else headers)

    def test_unsigned_and_stale_requests_rejected(self):
        body = form(action_payload())
        self.assertEqual(self.post(None, headers={}, body=body).status_code, 401)
        self.assertEqual(self.post(None, headers=signed(body, int(time.time()) - 600),
                                   body=body).status_code, 401)
        self.assertEqual(self.post(None, headers=signed(body, secret="wrong"),
                                   body=body).status_code, 401)
        self.update_status.assert_not_called()

    def test_malformed_payload(self):
        self.assertEqual(self.post(None, body=b"payload=not-json").status_code, 400)
        self.assertEqual(self.post(None, body=b"nothing=here").status_code, 400)
        self.assertEqual(self.post(None, body=b"payload=%5B%5D").status_code, 400)

    def test_other_subsystem_actions_are_ignored_quietly(self):
        r = self.post(action_payload("emailcreator_send", user="U999"))
        self.assertEqual((r.status_code, r.json()), (200, {}))
        r = self.post({"type": "shortcut", "callback_id": "emailcreator", "user": {"id": "U9"}})
        self.assertEqual((r.status_code, r.json()), (200, {}))
        self.respond.assert_not_called()
        self.claim_delivery.assert_not_called()

    def test_outsider_wrong_team_and_wrong_channel_cannot_act(self):
        for payload in (action_payload(user="U999"), action_payload(team="T999"),
                        action_payload(channel="C999")):
            self.assertEqual(self.post(payload).status_code, 200)
        self.update_status.assert_not_called()
        self.assertEqual(self.respond.call_count, 3)

    def test_replayed_delivery_is_ignored(self):
        self.claim_delivery.return_value = False
        self.post(action_payload("seo_reject"))
        self.update_status.assert_not_called()

    def test_storage_failure_fails_closed(self):
        self.claim_delivery.side_effect = RuntimeError("db down")
        self.assertEqual(self.post(action_payload()).status_code, 503)
        self.update_status.assert_not_called()

    def test_approve_while_disarmed_changes_nothing(self):
        with patch.dict(os.environ, {"PUBLISHING_ENABLED": "false"}):
            self.post(action_payload())
        self.update_status.assert_not_called()
        self.assertIn("not armed", self.respond.call_args[0][1])

    def test_approve_and_reject_use_exact_state_transitions(self):
        self.post(action_payload("seo_approve"))
        self.update_status.assert_called_with(
            gateway.uuid.UUID(PID), ["pending"], "approved_queued", "U1")
        self.post(action_payload("seo_reject"))
        self.update_status.assert_called_with(
            gateway.uuid.UUID(PID), ["pending", "approved_queued"], "rejected", "U1")

    def test_invalid_proposal_id(self):
        self.post(action_payload(value="not-a-uuid"))
        self.update_status.assert_not_called()

    def test_revision_modal_and_submission(self):
        self.post(action_payload("seo_revise"))
        self.assertEqual(self.slack_request.call_args[0][0], "views.open")
        view = {"type": "view_submission", "user": {"id": "U1"}, "team": {"id": "T1"},
                "view": {"callback_id": "seo_revision_modal", "private_metadata": PID,
                         "state": {"values": {"revision_block": {"notes": {"value": "Fix the intro"}}}}}}
        self.assertEqual(self.post(view).json(), {})
        self.update_status.assert_called_with(gateway.uuid.UUID(PID), ["pending"],
                                              "revision_requested", "U1", "Fix the intro")
        view["view"]["state"]["values"]["revision_block"]["notes"]["value"] = "  "
        self.assertEqual(self.post(view).json()["response_action"], "errors")
        outsider = dict(view, user={"id": "U999"})
        self.assertEqual(self.post(outsider).json()["response_action"], "errors")

    def test_modal_failure_is_reported_not_raised(self):
        self.slack_request.side_effect = RuntimeError("slack_network_error")
        self.assertEqual(self.post(action_payload("seo_revise")).status_code, 200)
        self.assertIn("Could not open", self.respond.call_args[0][1])


class RespondTests(unittest.TestCase):
    @patch("app.background")
    def test_response_url_must_be_slack(self, bg):
        gateway.respond({"response_url": "https://attacker.example/x"}, "hi")
        gateway.respond({"response_url": "http://hooks.slack.com/x"}, "hi")
        gateway.respond({}, "hi")
        bg.assert_not_called()
        gateway.respond({"response_url": "https://hooks.slack.com/actions/x"}, "hi")
        bg.assert_called_once()


class LiveVerificationTests(unittest.TestCase):
    def test_cdn_injections_are_normalised(self):
        source = b"<html>\n  <head><title>A</title></head>\n<body><!-- c --><h1>A</h1></body></html>"
        served = (b'<html><head><title>A</title></head><body><h1>A</h1>'
                  b'<script defer src="https://static.cloudflareinsights.com/beacon.min.js" '
                  b'data-cf-beacon="{}"></script></body></html>')
        self.assertEqual(normalize_html(source), normalize_html(served))
        self.assertNotEqual(normalize_html(source), normalize_html(served.replace(b">A<", b">B<")))

    @patch("app.time.sleep")
    @patch("app.requests.get")
    @patch("app.github", return_value=b"<h1>New</h1>")
    def test_stale_or_partial_deploy_not_reported_live(self, _gh, mock_get, _sleep):
        class Resp:
            status_code = 200
            content = b"<h1>Old</h1>"
        mock_get.return_value = Resp()
        self.assertFalse(gateway.verify_live_content(["resources/index.html"], "d" * 40, attempts=2))
        Resp.content = b"<h1>New</h1>\n"
        self.assertTrue(gateway.verify_live_content(["resources/index.html"], "d" * 40, attempts=1))
        self.assertIn("?tl_verify=dddddddddddd", mock_get.call_args[0][0])

    @patch("app.github", side_effect=RuntimeError("github_http_404"))
    def test_missing_source_is_not_verified(self, _gh):
        self.assertFalse(gateway.verify_live_content(["resources/index.html"], "d" * 40))


if __name__ == "__main__":
    unittest.main()
