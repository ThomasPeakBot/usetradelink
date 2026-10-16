"""Approval-first TradeLink SEO Slack gateway.

No publishing occurs unless the service is explicitly armed and a verified
Slack approver approves an immutable GitHub PR SHA.
"""
import base64
import hashlib
import hmac
import json
import logging
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from urllib.parse import parse_qs

import psycopg
import requests
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field
from psycopg.rows import dict_row

log = logging.getLogger("seo-approvals")
logging.basicConfig(level=logging.INFO)
app = FastAPI(title="TradeLink SEO approval gateway", docs_url=None, redoc_url=None)
REPO = "ThomasPeakBot/usetradelink"
API_BASE = "https://api.github.com"
GITHUB_ALLOWED_CONTENT = re.compile(
    r"^(?:resources|compare|blog|blogs|guides|tradovate-[a-z0-9-]+|"
    r"futures-[a-z0-9-]+|cloud-futures-[a-z0-9-]+|"
    r"prop-firm-[a-z0-9-]+|apex-[a-z0-9-]+|best-futures-[a-z0-9-]+)/"
    r"[a-z0-9/_-]*index\.html$"
)
ALLOWED_ROOTS = {"sitemap.xml", "llms.txt"}
SHA_PATTERN = re.compile(r"^[a-f0-9]{40}$")
GITHUB_CHECK = "seo-content-integrity"
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
"""

def configured(name):
    return bool(os.getenv(name, "").strip())

def armed():
    return (os.getenv("PUBLISHING_ENABLED", "").lower() == "true"
            and os.getenv("BRANCH_PROTECTION_VERIFIED", "").lower() == "true"
            and all(configured(x) for x in
                    ("GITHUB_TOKEN", "SLACK_BOT_TOKEN", "SLACK_SIGNING_SECRET",
                     "SLACK_APPROVER_IDS", "SLACK_TEAM_ID", "DATABASE_URL")))

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

def allowed_path(path):
    return path in ALLOWED_ROOTS or bool(GITHUB_ALLOWED_CONTENT.fullmatch(path))

def github(method, path, **kwargs):
    token = os.getenv("GITHUB_TOKEN", "")
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = "Bearer " + token
    response = requests.request(method, API_BASE + path, headers=headers, timeout=15, **kwargs)
    if response.status_code >= 400:
        # Avoid leaking GitHub auth or response bodies into logs.
        raise RuntimeError("github_http_" + str(response.status_code))
    return response.json()

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

def validate_publishable(pr, expected_sha):
    if not armed():
        raise ValueError("Publishing is not configured or authorized")
    if pr.get("head", {}).get("sha") != expected_sha:
        raise ValueError("PR SHA changed after approval")
    if pr.get("draft"):
        raise ValueError("Draft PR must be marked ready for review")
    if pr.get("mergeable") is not True:
        raise ValueError("PR must be confirmed mergeable")
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
    checks = github("GET", f"/repos/{REPO}/commits/{expected_sha}/check-runs?per_page=100")
    all_checks = checks.get("check_runs", [])
    if not any(c.get("name") == GITHUB_CHECK and c.get("conclusion") == "success"
               for c in all_checks):
        raise ValueError("Required seo-content-integrity check has not passed")
    if any(c.get("conclusion") in ("failure", "timed_out", "cancelled", "action_required")
           for c in all_checks):
        raise ValueError("A GitHub check failed")
    return [f["filename"] for f in files]

def slack_request(method, data):
    token = os.getenv("SLACK_BOT_TOKEN")
    if not token:
        raise RuntimeError("slack_not_configured")
    try:
        response = requests.post("https://slack.com/api/" + method, json=data,
                                 headers={"Authorization": "Bearer " + token},
                                 timeout=5)
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
            "publishing_armed": armed()}

@app.post("/internal/proposals")
def propose(item: CreateProposal, authorization: str | None = Header(None)):
    require_internal(authorization)
    if not configured("SLACK_CHANNEL_ID") or not configured("DATABASE_URL"):
        raise HTTPException(status_code=503, detail="Slack channel or database not configured")
    pr = pr_details(item.pr_number)
    sha = pr["head"]["sha"]
    title = pr.get("title", "SEO improvement")[:240]
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE seo_approval_proposals SET status='superseded',updated_at=now() "
                "WHERE repo=%s AND pr_number=%s AND commit_sha<>%s AND status='pending'",
                (REPO, item.pr_number, sha))
            cur.execute(
                "INSERT INTO seo_approval_proposals(id,repo,pr_number,commit_sha,title,summary,status) "
                "VALUES(%s,%s,%s,%s,%s,%s,'pending') "
                "ON CONFLICT(repo,pr_number,commit_sha) DO NOTHING",
                (uuid.uuid4(), REPO, item.pr_number, sha, title, item.summary))
            cur.execute("SELECT * FROM seo_approval_proposals WHERE repo=%s AND pr_number=%s "
                        "AND commit_sha=%s", (REPO, item.pr_number, sha))
            proposal = cur.fetchone()
            if proposal["status"] != "pending":
                raise HTTPException(status_code=409, detail="Existing proposal is not pending")
            record(cur, proposal["id"], "proposal_registered", "system", sha)
    if not proposal["slack_ts"]:
        result = slack_request("chat.postMessage", {
            "channel": os.environ["SLACK_CHANNEL_ID"],
            "text": f"TradeLink SEO: approval requested for PR #{item.pr_number}",
            "blocks": approval_blocks(proposal["id"], item.pr_number, sha,
                                      title, item.summary)})
        with connection() as conn:
            conn.execute("UPDATE seo_approval_proposals SET slack_channel=%s,slack_ts=%s,"
                         "updated_at=now() WHERE id=%s",
                         (result["channel"], result["ts"], proposal["id"]))
    return {"proposal_id": str(proposal["id"]), "status": "pending",
            "pr_number": item.pr_number, "sha": sha, "published": False}

def slack_authorized(payload):
    user = payload.get("user", {}).get("id", "")
    team = payload.get("team", {}).get("id", "")
    ids = {x.strip() for x in os.getenv("SLACK_APPROVER_IDS", "").split(",") if x.strip()}
    return bool(ids and user in ids and team == os.getenv("SLACK_TEAM_ID", "_"))

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

def notify_async(p, status):
    # Acknowledge Slack within 3 seconds; do not await notification HTTP.
    threading.Thread(target=inform, args=(p, status), daemon=True).start()

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

@app.post("/slack/interactions")
async def interactions(request: Request):
    body = await request.body()
    if not signed_request(body, request.headers.get("X-Slack-Request-Timestamp"),
            request.headers.get("X-Slack-Signature"),
            os.getenv("SLACK_SIGNING_SECRET", "")):
        raise HTTPException(status_code=401, detail="Invalid Slack signature")
    try:
        raw = parse_qs(body.decode("utf-8"), strict_parsing=True)["payload"][0]
        payload = json.loads(raw)
    except (KeyError, ValueError, UnicodeError, json.JSONDecodeError):
        raise HTTPException(status_code=400, detail="Malformed Slack interaction")
    if not slack_authorized(payload):
        return JSONResponse({"response_type": "ephemeral",
                             "text": "You are not on the SEO approval allowlist."})
    user = payload["user"]["id"]
    if payload.get("type") == "view_submission":
        view = payload.get("view", {})
        if view.get("callback_id") != "seo_revision_modal":
            return JSONResponse({})
        try:
            pid = uuid.UUID(view["private_metadata"])
            instruction = view["state"]["values"]["revision_block"]["notes"]["value"].strip()
        except (KeyError, ValueError, TypeError):
            return JSONResponse({"response_action": "errors",
                                 "errors": {"revision_block": "Please describe the revisions."}})
        if not instruction or len(instruction) > 2500:
            return JSONResponse({"response_action": "errors",
                                 "errors": {"revision_block": "Enter 1–2500 characters."}})
        p = update_status(pid, ["pending"], "revision_requested", user, instruction)
        if p:
            notify_async(p, "revisions requested by " + user)
            if configured("GITHUB_TOKEN"):
                threading.Thread(target=post_revision_comment,
                                 args=(p["pr_number"], instruction), daemon=True).start()
        return JSONResponse({})
    if payload.get("type") != "block_actions":
        return JSONResponse({})
    action = (payload.get("actions") or [{}])[0]
    kind = action.get("action_id")
    try:
        pid = uuid.UUID(action.get("value", ""))
    except (ValueError, AttributeError):
        return JSONResponse({"response_type": "ephemeral", "text": "Invalid proposal."})
    if kind == "seo_revise":
        if not configured("SLACK_BOT_TOKEN"):
            return JSONResponse({"response_type": "ephemeral", "text": "Slack is not configured."})
        slack_request("views.open", {"trigger_id": payload.get("trigger_id"), "view": {
            "type": "modal", "callback_id": "seo_revision_modal", "private_metadata": str(pid),
            "title": {"type": "plain_text", "text": "Request SEO revisions"},
            "submit": {"type": "plain_text", "text": "Request changes"},
            "close": {"type": "plain_text", "text": "Cancel"},
            "blocks": [{"type": "input", "block_id": "revision_block",
                        "label": {"type": "plain_text", "text": "What should change?"},
                        "element": {"type": "plain_text_input",
                                    "action_id": "notes", "multiline": True}}]}})
        return JSONResponse({})
    if kind == "seo_reject":
        p = update_status(pid, ["pending", "approved_queued"], "rejected", user)
        if p:
            notify_async(p, "rejected by " + user + "; no publication")
        return JSONResponse({"response_type": "ephemeral",
                             "text": "Rejected; publication stopped." if p
                                     else "This proposal is no longer pending."})
    if kind == "seo_approve":
        if not armed():
            return JSONResponse({"response_type": "ephemeral",
                        "text": "Approval executor is not armed; nothing was published."})
        p = update_status(pid, ["pending"], "approved_queued", user)
        if p:
            notify_async(p, "approved by " + user + "; validation queued")
        return JSONResponse({"response_type": "ephemeral",
                             "text": "Approval recorded. Exact-commit checks are queued; publishing is not yet confirmed."
                                     if p else "This proposal is no longer pending."})
    return JSONResponse({})

def post_revision_comment(number, notes):
    try:
        github("POST", f"/repos/{REPO}/issues/{number}/comments",
               json={"body": "SEO revision requested (not approved to publish):\n\n" + notes})
    except Exception:
        log.exception("Could not post revision instructions to GitHub")

def proposal_discovery_loop():
    """Notify Slack about existing SEO-labelled PRs; safe without publication rights."""
    while True:
        try:
            prs = github("GET", f"/repos/{REPO}/pulls?state=open&per_page=100")
            for pr in prs:
                labels = {x.get("name") for x in pr.get("labels", [])}
                if ("seo-agent-proposal" not in labels or
                        pr.get("base", {}).get("ref") != "main" or
                        pr.get("head", {}).get("repo", {}).get("full_name") != REPO):
                    continue
                sha = pr.get("head", {}).get("sha", "")
                if not SHA_PATTERN.fullmatch(sha):
                    continue
                with connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute("SELECT id FROM seo_approval_proposals WHERE repo=%s "
                                    "AND pr_number=%s AND commit_sha=%s",
                                    (REPO, pr["number"], sha))
                        exists = cur.fetchone()
                if exists:
                    continue
                summary = (pr.get("body") or
                    "SEO improvement proposed. Review the exact PR diff before approving.")[:1200]
                pid = uuid.uuid4()
                with connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute("UPDATE seo_approval_proposals SET status='superseded',"
                                    "updated_at=now() WHERE repo=%s AND pr_number=%s "
                                    "AND commit_sha<>%s AND status='pending'",
                                    (REPO, pr["number"], sha))
                        cur.execute("INSERT INTO seo_approval_proposals("
                                    "id,repo,pr_number,commit_sha,title,summary,status) "
                                    "VALUES(%s,%s,%s,%s,%s,%s,'pending') "
                                    "ON CONFLICT(repo,pr_number,commit_sha) DO NOTHING",
                                    (pid, REPO, pr["number"], sha, pr["title"][:240], summary))
                        if cur.rowcount == 0:
                            continue
                        record(cur, pid, "auto_discovered", "system", sha)
                response = slack_request("chat.postMessage", {
                    "channel": os.environ["SLACK_CHANNEL_ID"],
                    "text": f"TradeLink SEO: PR #{pr['number']} awaits review",
                    "blocks": approval_blocks(pid, pr["number"], sha, pr["title"], summary)})
                with connection() as conn:
                    conn.execute("UPDATE seo_approval_proposals SET slack_channel=%s,"
                                 "slack_ts=%s WHERE id=%s",
                                 (response["channel"], response["ts"], pid))
        except Exception:
            log.exception("SEO proposal discovery encountered an error")
        time.sleep(120)

def verify_live_content(paths, merge_sha, attempts=5, delay=15):
    """Strong positive proof only: compare exact public bytes to merged GitHub files."""
    expected = {}
    for path in paths:
        try:
            obj = github("GET", f"/repos/{REPO}/contents/{path}?ref={merge_sha}")
            expected[path] = base64.b64decode(obj["content"].replace("\\n", "").replace("\n", ""))
        except Exception:
            return False
    for attempt in range(attempts):
        try:
            verified = True
            for path, source in expected.items():
                url = "https://usetradelink.com/" + (
                    path[:-len("index.html")] if path.endswith("index.html") else path)
                response = requests.get(url, timeout=12,
                    headers={"User-Agent": "TradeLinkSEOPublisher/1.0"})
                if response.status_code != 200 or response.content != source:
                    verified = False
                    break
            if verified:
                return True
        except requests.RequestException:
            pass
        if attempt < attempts - 1:
            time.sleep(delay)
    return False

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
        pr = pr_details(p["pr_number"])
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
            detail += ";live_content_hash_verified"
        else:
            status = "merged_verification_pending"
        # Only exact production byte matches prove the deployed content matches this merge.
    except Exception as exc:
        detail = "blocked:" + exc.__class__.__name__ + ":" + str(exc)[:140]
        status = "needs_attention"
        paths = []
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
            cur.execute("""SELECT page,query,SUM(impressions)::int AS impressions,
                         SUM(clicks)::int AS clicks,
                         ROUND((SUM(position * impressions) /
                               NULLIF(SUM(impressions),0))::numeric,1) AS position
                         FROM seo_search_performance
                         WHERE data_date >= current_date-35
                         GROUP BY page,query
                         HAVING SUM(impressions)>=20
                         ORDER BY impressions DESC LIMIT 100""")
            rows = cur.fetchall()
    candidates = [r for r in rows if 4 <= float(r["position"] or 0) <= 25]
    return {"source": "gsc", "candidates": candidates[:30],
            "note": "Research candidates only: no drafts or site changes are published."}
