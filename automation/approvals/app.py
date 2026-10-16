"""Approval-first TradeLink SEO Slack gateway.

No publishing occurs unless the service is explicitly armed, GitHub confirms
at runtime that `main` is protected, and a verified Slack approver approves an
immutable GitHub PR SHA.
"""
import hashlib
import hmac
import json
import logging
import os
import re
import threading
import time
import uuid
from urllib.parse import parse_qs, urlsplit

import psycopg
import requests
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from psycopg.rows import dict_row

from policy import ALLOWED_ROOTS, GITHUB_ALLOWED_CONTENT, allowed_path  # noqa: F401

log = logging.getLogger("seo-approvals")
logging.basicConfig(level=logging.INFO)
app = FastAPI(title="TradeLink SEO approval gateway", docs_url=None, redoc_url=None)
REPO = "ThomasPeakBot/usetradelink"
API_BASE = "https://api.github.com"
SITE = "https://usetradelink.com/"
SHA_PATTERN = re.compile(r"^[a-f0-9]{40}$")
GITHUB_CHECK = "seo-content-integrity"
# Only a check run created by GitHub Actions counts; any installed app could
# otherwise post a check run that merely carries the required name.
GITHUB_CHECK_APP = "github-actions"
PROPOSAL_LABEL = "seo-agent-proposal"
SLACK_ACTIONS = {"seo_approve", "seo_reject", "seo_revise"}
SLACK_VIEWS = {"seo_revision_modal"}
OK_CONCLUSIONS = {"success", "neutral", "skipped"}
STALE_JOB_MINUTES = 15
PROTECTION_CACHE_SECONDS = 300
MERGEABLE_ATTEMPTS = 4
MERGEABLE_DELAY = 3
DDL = """
CREATE TABLE IF NOT EXISTS seo_approval_proposals (
  id uuid PRIMARY KEY, repo text NOT NULL, pr_number integer NOT NULL,
  commit_sha char(40) NOT NULL, title text NOT NULL, summary text NOT NULL,
  status text NOT NULL DEFAULT 'pending', slack_channel text,
  slack_ts text, approved_by text, created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(repo, pr_number, commit_sha)
);
CREATE TABLE IF NOT EXISTS seo_approval_events (
  id bigserial PRIMARY KEY, proposal_id uuid NOT NULL
  REFERENCES seo_approval_proposals(id), kind text NOT NULL, actor text NOT NULL,
  detail text NOT NULL DEFAULT '', created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS seo_approval_jobs (
  proposal_id uuid PRIMARY KEY REFERENCES seo_approval_proposals(id),
  state text NOT NULL DEFAULT 'queued', created_at timestamptz NOT NULL DEFAULT now(),
  started_at timestamptz, finished_at timestamptz, result text
);
CREATE TABLE IF NOT EXISTS seo_slack_deliveries (
  digest char(64) PRIMARY KEY, received_at timestamptz NOT NULL DEFAULT now()
);
"""

def configured(name):
    return bool(os.getenv(name, "").strip())

def deployed_from_allowed_branch():
    """Railway sets RAILWAY_GIT_BRANCH. A service built from a development branch never publishes."""
    allowed = {b.strip() for b in os.getenv("PUBLISH_ALLOWED_BRANCHES", "main").split(",") if b.strip()}
    return os.getenv("RAILWAY_GIT_BRANCH", "") in allowed

def armed():
    """Configuration gate only. GitHub-side protection is verified separately at execution."""
    return (os.getenv("PUBLISHING_ENABLED", "").lower() == "true"
            and deployed_from_allowed_branch()
            and os.getenv("BRANCH_PROTECTION_VERIFIED", "").lower() == "true"
            and all(configured(x) for x in
                    ("GITHUB_TOKEN", "SLACK_BOT_TOKEN", "SLACK_SIGNING_SECRET",
                     "SLACK_APPROVER_IDS", "SLACK_TEAM_ID", "SLACK_CHANNEL_ID",
                     "DATABASE_URL")))

def connection():
    return psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=8, row_factory=dict_row)

def record(cur, proposal_id, kind, actor, detail=""):
    cur.execute(
        "INSERT INTO seo_approval_events(proposal_id,kind,actor,detail) VALUES(%s,%s,%s,%s)",
        (proposal_id, kind, actor, detail[:2000]))

def signed_request(body, timestamp, signature, secret, now=None):
    """Verifies the original bytes, not reconstructed form data."""
    if not secret or not timestamp or not signature:
        return False
    try:
        t = int(timestamp)
    except (ValueError, TypeError):
        return False
    if abs((now if now is not None else time.time()) - t) > 300:
        return False
    digest = hmac.new(secret.encode(), b"v0:" + timestamp.encode() + b":" + body,
                      hashlib.sha256).hexdigest()
    return hmac.compare_digest("v0=" + digest, signature)

def claim_delivery(signature):
    """Record a signed delivery once. A replay inside the 5-minute window is refused."""
    digest = hashlib.sha256(signature.encode()).hexdigest()
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM seo_slack_deliveries WHERE received_at < now() - interval '1 day'")
            cur.execute("INSERT INTO seo_slack_deliveries(digest) VALUES(%s) "
                        "ON CONFLICT(digest) DO NOTHING", (digest,))
            return cur.rowcount == 1

def github(method, path, raw=False, **kwargs):
    token = os.getenv("GITHUB_TOKEN", "")
    headers = {"Accept": "application/vnd.github.raw" if raw else "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = "Bearer " + token
    response = requests.request(method, API_BASE + path, headers=headers, timeout=15, **kwargs)
    if response.status_code >= 400:
        # Avoid leaking GitHub auth or response bodies into logs.
        raise RuntimeError("github_http_" + str(response.status_code))
    return response.content if raw else response.json()

_protection_cache = {"at": 0.0, "ok": False}

def classic_protection_ok():
    """Classic branch protection; reading it needs Administration: read on the token."""
    try:
        p = github("GET", f"/repos/{REPO}/branches/main/protection")
    except RuntimeError:
        return False
    checks = p.get("required_status_checks") or {}
    contexts = set(checks.get("contexts") or [])
    contexts |= {c.get("context") for c in checks.get("checks") or []}
    reviews = p.get("required_pull_request_reviews")
    bypass = (reviews or {}).get("bypass_pull_request_allowances") or {}
    return bool(GITHUB_CHECK in contexts
                and reviews is not None
                and not any(bypass.get(k) for k in ("users", "teams", "apps"))
                and (p.get("enforce_admins") or {}).get("enabled") is True
                and (p.get("allow_force_pushes") or {}).get("enabled") is False
                and (p.get("allow_deletions") or {}).get("enabled") is False)

def ruleset_protection_ok():
    """Repository rulesets; the executor's own token must not be able to bypass them."""
    try:
        rules = github("GET", f"/repos/{REPO}/rules/branches/main")
    except RuntimeError:
        return False
    if not isinstance(rules, list):
        return False
    types = {r.get("type") for r in rules}
    contexts = {c.get("context")
                for r in rules if r.get("type") == "required_status_checks"
                for c in (r.get("parameters") or {}).get("required_status_checks") or []}
    if not ({"pull_request", "non_fast_forward", "deletion"} <= types and GITHUB_CHECK in contexts):
        return False
    for ruleset_id in {r.get("ruleset_id") for r in rules if r.get("ruleset_id")}:
        try:
            ruleset = github("GET", f"/repos/{REPO}/rulesets/{ruleset_id}")
        except RuntimeError:
            return False
        if ruleset.get("enforcement") != "active" or ruleset.get("current_user_can_bypass") != "never":
            return False
    return True

def branch_protection_ok(now=None):
    """Runtime proof from GitHub that main cannot be changed without the required check."""
    now = time.time() if now is None else now
    if now - _protection_cache["at"] < PROTECTION_CACHE_SECONDS:
        return _protection_cache["ok"]
    try:
        ok = classic_protection_ok() or ruleset_protection_ok()
    except Exception:
        log.warning("Branch protection lookup failed; treating main as unprotected")
        ok = False
    _protection_cache.update(at=now, ok=ok)
    return ok

def pr_details(number):
    pr = github("GET", f"/repos/{REPO}/pulls/{number}")
    if (pr.get("state") != "open" or pr.get("merged") or
            pr.get("base", {}).get("ref") != "main" or
            pr.get("head", {}).get("repo", {}).get("full_name") != REPO):
        raise ValueError("PR must be an open same-repo change targeting main")
    sha = pr.get("head", {}).get("sha", "")
    if not SHA_PATTERN.fullmatch(sha):
        raise ValueError("PR head SHA is invalid")
    return pr

def fresh_pr(number, attempts=MERGEABLE_ATTEMPTS, delay=MERGEABLE_DELAY):
    """GitHub computes `mergeable` lazily and returns null on the first read."""
    pr = pr_details(number)
    for _ in range(attempts - 1):
        if pr.get("mergeable") is not None:
            break
        time.sleep(delay)
        pr = pr_details(number)
    return pr

def verify_checks(sha):
    checks = github("GET", f"/repos/{REPO}/commits/{sha}/check-runs?per_page=100")
    runs = checks.get("check_runs", [])
    if checks.get("total_count", len(runs)) > len(runs):
        raise ValueError("Too many check runs to verify")
    required = [c for c in runs if c.get("name") == GITHUB_CHECK
                and (c.get("app") or {}).get("slug") == GITHUB_CHECK_APP]
    if not required or any(c.get("conclusion") != "success" for c in required):
        raise ValueError("Required seo-content-integrity check has not passed")
    for c in runs:
        if c.get("status") != "completed":
            raise ValueError("A GitHub check is still running")
        if c.get("conclusion") not in OK_CONCLUSIONS:
            raise ValueError("A GitHub check failed")
    status = github("GET", f"/repos/{REPO}/commits/{sha}/status")
    if status.get("total_count", 0) and status.get("state") != "success":
        raise ValueError("A commit status is pending or failed")

def validate_publishable(pr, expected_sha):
    if not armed():
        raise ValueError("Publishing is not configured or authorized")
    if pr.get("head", {}).get("sha") != expected_sha:
        raise ValueError("PR SHA changed after approval")
    if pr.get("draft"):
        raise ValueError("Draft PR must be marked ready for review")
    if PROPOSAL_LABEL not in {x.get("name") for x in pr.get("labels") or []}:
        raise ValueError("PR is missing the seo-agent-proposal label")
    if pr.get("mergeable") is not True:
        raise ValueError("PR must be confirmed mergeable")
    if not branch_protection_ok():
        raise ValueError("GitHub did not confirm protection rules on main")
    number = pr["number"]
    files = []
    page = 1
    while True:
        chunk = github("GET", f"/repos/{REPO}/pulls/{number}/files?per_page=100&page={page}")
        files.extend(chunk)
        if len(chunk) < 100:
            break
        page += 1
        if page > 2:
            raise ValueError("Refusing exceptionally large PR")
    if not files or len(files) > 50:
        raise ValueError("PR has no changes or too many files")
    for item in files:
        if (item.get("status") in ("removed", "renamed") or
                not allowed_path(item.get("filename", ""))):
            raise ValueError("Unapproved or high-risk file path")
    verify_checks(expected_sha)
    return [f["filename"] for f in files]

def slack_request(method, data, timeout=5):
    token = os.getenv("SLACK_BOT_TOKEN")
    if not token:
        raise RuntimeError("slack_not_configured")
    try:
        response = requests.post("https://slack.com/api/" + method, json=data,
                                 headers={"Authorization": "Bearer " + token},
                                 timeout=timeout)
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            raise RuntimeError("slack_api_rejected")
        return payload
    except requests.RequestException as e:
        raise RuntimeError("slack_network_error") from e

def plain_slack(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def approval_blocks(proposal_id, number, sha, title, summary):
    return [
        {"type": "header", "text": {"type": "plain_text", "text": "TradeLink SEO approval"}},
        {"type": "section", "text": {"type": "mrkdwn", "text":
            f"*{plain_slack(title[:140])}*\n"
            f"<https://github.com/{REPO}/pull/{number}|Review PR #{number}> • "
            f"Commit: `{sha[:12]}`\n{plain_slack(summary[:1200])}"}},
        {"type": "context", "elements": [{"type": "mrkdwn",
             "text": "Only this exact commit can be approved. Publication requires passing GitHub checks."}]},
        {"type": "actions", "elements": [
            {"type": "button", "action_id": "seo_approve", "style": "primary",
             "text": {"type": "plain_text", "text": "Approve & publish"},
             "confirm": {"title": {"type": "plain_text", "text": "Confirm publication"},
                "text": {"type": "mrkdwn",
                    "text": "Publish only this PR commit after the required checks pass?"},
                "confirm": {"type": "plain_text", "text": "Approve"},
                "deny": {"type": "plain_text", "text": "Cancel"}},
             "value": str(proposal_id)},
            {"type": "button", "action_id": "seo_reject", "style": "danger",
             "text": {"type": "plain_text", "text": "Reject"}, "value": str(proposal_id)},
            {"type": "button", "action_id": "seo_revise",
             "text": {"type": "plain_text", "text": "Request changes"},
             "value": str(proposal_id)},
        ]}
    ]

class CreateProposal(BaseModel):
    pr_number: int = Field(ge=1)
    summary: str = Field(min_length=10, max_length=1600)

def require_internal(authorization):
    secret = os.getenv("PROPOSAL_API_TOKEN", "")
    expected = "Bearer " + secret
    if not secret or not authorization or not hmac.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="Unauthorized")

@app.on_event("startup")
def startup():
    if not configured("DATABASE_URL"):
        log.warning("Missing DATABASE_URL; approval API is unconfigured")
        return
    try:
        with connection() as conn:
            conn.execute(DDL)
        if armed():
            threading.Thread(target=publish_loop, daemon=True, name="seo-publisher").start()
        if all(configured(x) for x in ("GITHUB_TOKEN", "SLACK_BOT_TOKEN",
                                        "SLACK_CHANNEL_ID", "SLACK_SIGNING_SECRET",
                                        "SLACK_APPROVER_IDS", "SLACK_TEAM_ID")):
            threading.Thread(target=proposal_discovery_loop, daemon=True,
                             name="seo-proposals").start()
        log.info("SEO approval service initialized; publishing_armed=%s", armed())
    except Exception:
        log.exception("Unable to initialize approval schema")
        raise

@app.get("/health")
def health():
    return {"ok": True, "database_configured": configured("DATABASE_URL"),
            "slack_configured": all(configured(x) for x in
                ("SLACK_SIGNING_SECRET", "SLACK_BOT_TOKEN", "SLACK_CHANNEL_ID",
                 "SLACK_TEAM_ID", "SLACK_APPROVER_IDS")),
            "github_token_configured": configured("GITHUB_TOKEN"),
            # Non-secret deploy provenance so operators can compare against reviewed SHAs.
            "deployed_branch": os.getenv("RAILWAY_GIT_BRANCH", ""),
            "deployed_commit": os.getenv("RAILWAY_GIT_COMMIT_SHA", "")[:12],
            "deployed_from_allowed_branch": deployed_from_allowed_branch(),
            "publishing_armed": armed()}

def post_proposal(proposal_id, number, sha, title, summary):
    result = slack_request("chat.postMessage", {
        "channel": os.environ["SLACK_CHANNEL_ID"],
        "text": f"TradeLink SEO: approval requested for PR #{number}",
        "blocks": approval_blocks(proposal_id, number, sha, title, summary)})
    with connection() as conn:
        conn.execute("UPDATE seo_approval_proposals SET slack_channel=%s,slack_ts=%s,"
                     "updated_at=now() WHERE id=%s",
                     (result["channel"], result["ts"], proposal_id))

def register_proposal(number, sha, title, summary, kind):
    """Insert or find the proposal for an exact SHA; returns the row."""
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE seo_approval_proposals SET status='superseded',updated_at=now() "
                "WHERE repo=%s AND pr_number=%s AND commit_sha<>%s AND status='pending'",
                (REPO, number, sha))
            cur.execute(
                "INSERT INTO seo_approval_proposals(id,repo,pr_number,commit_sha,title,summary,status) "
                "VALUES(%s,%s,%s,%s,%s,%s,'pending') "
                "ON CONFLICT(repo,pr_number,commit_sha) DO NOTHING",
                (uuid.uuid4(), REPO, number, sha, title, summary))
            inserted = cur.rowcount == 1
            cur.execute("SELECT * FROM seo_approval_proposals WHERE repo=%s AND pr_number=%s "
                        "AND commit_sha=%s", (REPO, number, sha))
            proposal = cur.fetchone()
            if inserted:
                record(cur, proposal["id"], kind, "system", sha)
            return proposal

@app.post("/internal/proposals")
def propose(item: CreateProposal, authorization: str | None = Header(None)):
    require_internal(authorization)
    if not configured("SLACK_CHANNEL_ID") or not configured("DATABASE_URL"):
        raise HTTPException(status_code=503, detail="Slack channel or database not configured")
    pr = pr_details(item.pr_number)
    sha = pr["head"]["sha"]
    title = pr.get("title", "SEO improvement")[:240]
    proposal = register_proposal(item.pr_number, sha, title, item.summary, "proposal_registered")
    if proposal["status"] != "pending":
        raise HTTPException(status_code=409, detail="Existing proposal is not pending")
    if not proposal["slack_ts"]:
        post_proposal(proposal["id"], item.pr_number, sha, title, proposal["summary"])
    return {"proposal_id": str(proposal["id"]), "status": "pending",
            "pr_number": item.pr_number, "sha": sha, "published": False}

def slack_authorized(payload):
    user = (payload.get("user") or {}).get("id", "")
    team = (payload.get("team") or {}).get("id", "")
    ids = {x.strip() for x in os.getenv("SLACK_APPROVER_IDS", "").split(",") if x.strip()}
    return bool(ids and user in ids and team == os.getenv("SLACK_TEAM_ID", "_"))

def channel_allowed(payload):
    expected = os.getenv("SLACK_CHANNEL_ID", "").strip()
    return bool(expected) and (payload.get("channel") or {}).get("id") == expected

def update_status(proposal_id, old_states, new_state, actor, detail=""):
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM seo_approval_proposals WHERE id=%s FOR UPDATE",
                        (proposal_id,))
            p = cur.fetchone()
            if not p or p["status"] not in old_states:
                return None
            cur.execute("UPDATE seo_approval_proposals SET status=%s,approved_by=%s,"
                        "updated_at=now() WHERE id=%s",
                        (new_state, actor if new_state == "approved_queued" else p["approved_by"],
                         proposal_id))
            record(cur, proposal_id, new_state, actor, detail)
            if new_state == "approved_queued":
                cur.execute("INSERT INTO seo_approval_jobs(proposal_id,state) VALUES(%s,'queued') "
                            "ON CONFLICT(proposal_id) DO NOTHING", (proposal_id,))
            if new_state == "rejected":
                cur.execute("DELETE FROM seo_approval_jobs WHERE proposal_id=%s AND state='queued'",
                            (proposal_id,))
            p["status"] = new_state
            return p

def background(target, *args):
    threading.Thread(target=target, args=args, daemon=True).start()

def notify_async(p, status):
    # Acknowledge Slack within 3 seconds; do not await notification HTTP.
    background(inform, p, status)

def inform(p, status):
    channel = p.get("slack_channel") or os.getenv("SLACK_CHANNEL_ID", "")
    if channel:
        try:
            slack_request("chat.postMessage", {"channel": channel,
                "text": f"TradeLink SEO PR #{p['pr_number']}: {status} "
                        f"(commit {p['commit_sha'][:12]}).",
                **({"thread_ts": p["slack_ts"]} if p.get("slack_ts") else {})})
        except Exception:
            log.exception("Slack notification delivery failed")

def respond(payload, text):
    """Slack ignores the HTTP body of block_actions; feedback must go to response_url."""
    url = payload.get("response_url") or ""
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname != "hooks.slack.com":
        return
    def send():
        try:
            requests.post(url, json={"response_type": "ephemeral", "replace_original": False,
                                     "text": text}, timeout=5)
        except requests.RequestException:
            log.warning("Slack response_url delivery failed")
    background(send)

@app.post("/slack/interactions")
async def interactions(request: Request):
    body = await request.body()
    signature = request.headers.get("X-Slack-Signature")
    if not signed_request(body, request.headers.get("X-Slack-Request-Timestamp"),
            signature, os.getenv("SLACK_SIGNING_SECRET", "")):
        raise HTTPException(status_code=401, detail="Invalid Slack signature")
    try:
        raw = parse_qs(body.decode("utf-8"), strict_parsing=True)["payload"][0]
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("payload")
    except (KeyError, ValueError, UnicodeError, json.JSONDecodeError):
        raise HTTPException(status_code=400, detail="Malformed Slack interaction")
    # Database and Slack calls are blocking; keep them off the event loop.
    return await run_in_threadpool(handle_interaction, payload, signature)

def handle_interaction(payload, signature):
    kind = payload.get("type")
    if kind == "view_submission":
        view = payload.get("view") or {}
        if view.get("callback_id") not in SLACK_VIEWS:
            return JSONResponse({})
    elif kind == "block_actions":
        action = (payload.get("actions") or [{}])[0]
        if action.get("action_id") not in SLACK_ACTIONS:
            # The Slack app is shared: other subsystems' interactions are not ours to answer.
            return JSONResponse({})
    else:
        return JSONResponse({})
    if not slack_authorized(payload):
        if kind == "view_submission":
            return JSONResponse({"response_action": "errors",
                                 "errors": {"revision_block": "You are not on the SEO approval allowlist."}})
        respond(payload, "You are not on the SEO approval allowlist.")
        return JSONResponse({})
    if kind == "block_actions" and not channel_allowed(payload):
        respond(payload, "SEO approvals are only accepted in the configured approvals channel.")
        return JSONResponse({})
    try:
        fresh = claim_delivery(signature)
    except Exception:
        log.exception("Could not record Slack delivery")
        raise HTTPException(status_code=503, detail="Approval storage unavailable")
    if not fresh:
        return JSONResponse({})
    user = payload["user"]["id"]
    if kind == "view_submission":
        return handle_revision_submission(payload, user)
    return handle_action(payload, action, user)

def handle_revision_submission(payload, user):
    view = payload.get("view") or {}
    try:
        pid = uuid.UUID(view["private_metadata"])
        instruction = view["state"]["values"]["revision_block"]["notes"]["value"].strip()
    except (KeyError, ValueError, TypeError, AttributeError):
        return JSONResponse({"response_action": "errors",
                             "errors": {"revision_block": "Please describe the revisions."}})
    if not instruction or len(instruction) > 2500:
        return JSONResponse({"response_action": "errors",
                             "errors": {"revision_block": "Enter 1–2500 characters."}})
    p = update_status(pid, ["pending"], "revision_requested", user, instruction)
    if p:
        notify_async(p, "revisions requested by " + user)
        if configured("GITHUB_TOKEN"):
            background(post_revision_comment, p["pr_number"], instruction)
    return JSONResponse({})

def handle_action(payload, action, user):
    kind = action.get("action_id")
    try:
        pid = uuid.UUID(action.get("value", ""))
    except (ValueError, AttributeError, TypeError):
        respond(payload, "Invalid proposal.")
        return JSONResponse({})
    if kind == "seo_revise":
        try:
            # trigger_id expires after 3 seconds, so this call cannot be deferred.
            slack_request("views.open", {"trigger_id": payload.get("trigger_id"), "view": {
                "type": "modal", "callback_id": "seo_revision_modal", "private_metadata": str(pid),
                "title": {"type": "plain_text", "text": "Request SEO revisions"},
                "submit": {"type": "plain_text", "text": "Request changes"},
                "close": {"type": "plain_text", "text": "Cancel"},
                "blocks": [{"type": "input", "block_id": "revision_block",
                            "label": {"type": "plain_text", "text": "What should change?"},
                            "element": {"type": "plain_text_input",
                                        "action_id": "notes", "multiline": True}}]}},
                timeout=2.5)
        except RuntimeError:
            respond(payload, "Could not open the revision form; nothing was changed.")
        return JSONResponse({})
    if kind == "seo_reject":
        p = update_status(pid, ["pending", "approved_queued"], "rejected", user)
        if p:
            notify_async(p, "rejected by " + user + "; no publication")
        respond(payload, "Rejected; publication stopped." if p
                else "This proposal is no longer pending.")
        return JSONResponse({})
    if not armed():
        respond(payload, "Approval executor is not armed; nothing was published.")
        return JSONResponse({})
    p = update_status(pid, ["pending"], "approved_queued", user)
    if p:
        notify_async(p, "approved by " + user + "; validation queued")
    respond(payload, "Approval recorded. Exact-commit checks are queued; publishing is not yet confirmed."
            if p else "This proposal is no longer pending.")
    return JSONResponse({})

def post_revision_comment(number, notes):
    try:
        github("POST", f"/repos/{REPO}/issues/{number}/comments",
               json={"body": "SEO revision requested (not approved to publish):\n\n" + notes})
    except Exception:
        log.exception("Could not post revision instructions to GitHub")

def discover_once():
    prs = github("GET", f"/repos/{REPO}/pulls?state=open&per_page=100")
    for pr in prs:
        labels = {x.get("name") for x in pr.get("labels", [])}
        if (PROPOSAL_LABEL not in labels or
                pr.get("base", {}).get("ref") != "main" or
                pr.get("head", {}).get("repo", {}).get("full_name") != REPO):
            continue
        sha = pr.get("head", {}).get("sha", "")
        if not SHA_PATTERN.fullmatch(sha):
            continue
        summary = (pr.get("body") or
            "SEO improvement proposed. Review the exact PR diff before approving.")[:1200]
        title = (pr.get("title") or "SEO improvement")[:240]
        proposal = register_proposal(pr["number"], sha, title, summary, "auto_discovered")
        # A failed Slack post leaves slack_ts empty and is retried on the next pass.
        if proposal["status"] == "pending" and not proposal["slack_ts"]:
            post_proposal(proposal["id"], pr["number"], sha, proposal["title"], proposal["summary"])

def proposal_discovery_loop():
    """Notify Slack about existing SEO-labelled PRs; safe without publication rights."""
    while True:
        try:
            discover_once()
        except Exception:
            log.exception("SEO proposal discovery encountered an error")
        time.sleep(120)

def normalize_html(data):
    """Remove differences a CDN may introduce (injected scripts, cf attributes, whitespace)."""
    text = data.decode("utf-8", errors="replace")
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = re.sub(r"<script\b[^>]*(?:cloudflare|/cdn-cgi/)[^>]*>.*?</script>", "", text,
                  flags=re.S | re.I)
    text = re.sub(r"\sdata-cf[a-z-]*=\"[^\"]*\"", "", text, flags=re.I)
    text = re.sub(r">\s+<", "><", text)
    return re.sub(r"\s+", " ", text).strip()

def live_url(path, merge_sha):
    page = path[:-len("index.html")] if path.endswith("index.html") else path
    return SITE + page + "?tl_verify=" + merge_sha[:12]

def verify_live_content(paths, merge_sha, attempts=5, delay=15):
    """Positive proof only: public content must match the merged files after CDN normalisation."""
    expected = {}
    for path in paths:
        try:
            source = github("GET", f"/repos/{REPO}/contents/{path}?ref={merge_sha}", raw=True)
        except Exception:
            return False
        expected[path] = normalize_html(source) if path.endswith(".html") else source
    for attempt in range(attempts):
        try:
            verified = True
            for path, source in expected.items():
                response = requests.get(live_url(path, merge_sha), timeout=12,
                    headers={"User-Agent": "TradeLinkSEOPublisher/1.0",
                             "Cache-Control": "no-cache"})
                body = (normalize_html(response.content) if path.endswith(".html")
                        else response.content)
                if response.status_code != 200 or body != source:
                    verified = False
                    break
            if verified:
                return True
        except requests.RequestException:
            pass
        if attempt < attempts - 1:
            time.sleep(delay)
    return False

def reap_stale_jobs():
    """A job left running (process crash mid-merge) needs a human; never retry it blindly."""
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT proposal_id FROM seo_approval_jobs WHERE state='running' "
                        "AND started_at < now() - make_interval(mins => %s) "
                        "FOR UPDATE SKIP LOCKED", (STALE_JOB_MINUTES,))
            stale = [r["proposal_id"] for r in cur.fetchall()]
            for pid in stale:
                cur.execute("UPDATE seo_approval_jobs SET state='done',finished_at=now(),"
                            "result='stale_running_job' WHERE proposal_id=%s", (pid,))
                cur.execute("UPDATE seo_approval_proposals SET status='needs_attention',"
                            "updated_at=now() WHERE id=%s", (pid,))
                record(cur, pid, "needs_attention", "system",
                       "Job exceeded running time; check GitHub before any retry")
    return stale

def publish_once():
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT p.* FROM seo_approval_proposals p
                JOIN seo_approval_jobs j ON p.id=j.proposal_id
                WHERE p.status='approved_queued' AND j.state='queued'
                ORDER BY j.created_at LIMIT 1 FOR UPDATE OF p,j SKIP LOCKED""")
            p = cur.fetchone()
            if not p:
                return False
            cur.execute("UPDATE seo_approval_jobs SET state='running',started_at=now() "
                        "WHERE proposal_id=%s", (p["id"],))
            cur.execute("UPDATE seo_approval_proposals SET status='executing',updated_at=now() "
                        "WHERE id=%s", (p["id"],))
            record(cur, p["id"], "execution_started", "system", p["commit_sha"])
    try:
        if not armed():
            raise ValueError("Publishing flag or required credentials disabled")
        pr = fresh_pr(p["pr_number"])
        paths = validate_publishable(pr, p["commit_sha"])
        result = github("PUT", f"/repos/{REPO}/pulls/{p['pr_number']}/merge",
                        json={"sha": p["commit_sha"], "merge_method": "squash",
                              "commit_title": "Approved SEO update: " + p["title"][:65]})
        if not result.get("merged"):
            raise ValueError("GitHub did not confirm merge")
        merged_sha = str(result.get("sha", ""))
        detail = "github_merged:" + merged_sha[:40]
        if merged_sha and verify_live_content(paths, merged_sha):
            status = "published_verified"
            detail += ";live_content_verified"
        else:
            status = "merged_verification_pending"
    except Exception as exc:
        detail = "blocked:" + exc.__class__.__name__ + ":" + str(exc)[:140]
        status = "needs_attention"
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE seo_approval_proposals SET status=%s,updated_at=now() "
                        "WHERE id=%s", (status, p["id"]))
            cur.execute("UPDATE seo_approval_jobs SET state='done',finished_at=now(),result=%s "
                        "WHERE proposal_id=%s", (detail, p["id"]))
            record(cur, p["id"], status, "system", detail)
    p["status"] = status
    inform(p, status + " (" + detail + ")")
    return True

def publish_loop():
    while True:
        try:
            reap_stale_jobs()
            if armed():
                publish_once()
        except Exception:
            log.exception("Approval job processor encountered an error")
        time.sleep(5)

@app.get("/internal/audit")
def audit(authorization: str | None = Header(None)):
    require_internal(authorization)
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT e.id,e.proposal_id,e.kind,e.actor,e.detail,e.created_at,
                               p.pr_number,p.commit_sha
                           FROM seo_approval_events e JOIN seo_approval_proposals p
                             ON p.id=e.proposal_id
                           ORDER BY e.id DESC LIMIT 100""")
            data = cur.fetchall()
    return {"events": data}

@app.get("/internal/opportunities")
def opportunities(authorization: str | None = Header(None)):
    require_internal(authorization)
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.seo_search_performance') IS NOT NULL AS gsc, "
                        "to_regclass('public.seo_semrush_keyword_metrics') IS NOT NULL AS semrush")
            available = cur.fetchone()
            if not available["gsc"]:
                return {"source": "none", "candidates": [],
                        "note": "Search Console data has not been collected yet; run seo-auditor first."}
            cur.execute("""
                SELECT page,query,SUM(impressions)::int AS impressions,
                       SUM(clicks)::int AS clicks,
                       ROUND((SUM(position * impressions) /
                             NULLIF(SUM(impressions),0))::numeric,1) AS position
                FROM seo_search_performance
                WHERE data_date >= current_date-35
                GROUP BY page,query
                HAVING SUM(impressions)>=20
                ORDER BY impressions DESC LIMIT 100
            """)
            rows = cur.fetchall()
            latest = {}
            if available["semrush"]:
                cur.execute("""
                    SELECT DISTINCT ON (keyword) keyword,search_volume,
                        keyword_difficulty,cpc_usd,intents,collected_on
                    FROM seo_semrush_keyword_metrics
                    WHERE country='US'
                    ORDER BY keyword,collected_on DESC
                """)
                latest = {r["keyword"]: r for r in cur.fetchall()}
    candidates = []
    for row in rows:
        if not 4 <= float(row["position"] or 0) <= 25:
            continue
        term = row["query"].strip().lower()
        m = latest.get(term)
        item = dict(row)
        item["semrush"] = (
            {"search_volume": m["search_volume"],
             "keyword_difficulty": float(m["keyword_difficulty"]) if m["keyword_difficulty"] is not None else None,
             "cpc_usd": float(m["cpc_usd"]) if m["cpc_usd"] is not None else None,
             "intents": m["intents"], "collected_on": m["collected_on"].isoformat()}
            if m else None)
        candidates.append(item)
    return {"source": "gsc+semrush" if latest else "gsc",
            "candidates": candidates[:30],
            "note": "Research candidates only; Semrush is optional and budget-controlled. No drafts or site changes are published."}
