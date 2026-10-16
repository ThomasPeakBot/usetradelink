"""Open a GitHub pull request for a draft that passed the quality gate.

Off unless ENGINE_PROPOSALS_ENABLED=true and ENGINE_GITHUB_TOKEN is set. The
token needs Contents and Pull requests write on this repository only; it never
merges. Branch protection plus the Slack approval executor decide publication.
"""
import base64
import os
import re
from datetime import date

import requests

from .policy import allowed_path

REPO = "ThomasPeakBot/usetradelink"
API = "https://api.github.com"
LABEL = "seo-agent-proposal"


class ProposalError(Exception):
    pass


class GitHub:
    def __init__(self, token=None, session=None):
        self.token = token if token is not None else os.getenv("ENGINE_GITHUB_TOKEN", "")
        self.session = session or requests.Session()

    def __call__(self, method, path, **kwargs):
        r = self.session.request(method, API + path, timeout=20, headers={
            "Authorization": "Bearer " + self.token, "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"}, **kwargs)
        if r.status_code >= 400:
            raise ProposalError(f"github_http_{r.status_code}")  # never log bodies or tokens
        return r.json() if r.content else {}


def enabled():
    return os.getenv("ENGINE_PROPOSALS_ENABLED", "false").lower() == "true" and bool(os.getenv("ENGINE_GITHUB_TOKEN"))


def add_to_sitemap(xml, url, lastmod):
    if f"<loc>{url}</loc>" in xml:
        return xml
    entry = f"  <url><loc>{url}</loc><lastmod>{lastmod}</lastmod></url>\n"
    return xml.replace("</urlset>", entry + "</urlset>")


def open_proposal(files, title, body, branch_hint, gh=None, max_open=None):
    """files: {path: text}. Returns the PR number."""
    if not files:
        raise ProposalError("nothing_to_propose")
    for path in files:
        if not allowed_path(path):
            raise ProposalError(f"path_not_allowed:{path}")
    gh = gh or GitHub()
    max_open = max_open if max_open is not None else int(os.getenv("ENGINE_MAX_OPEN_PROPOSALS", "3"))
    open_prs = gh("GET", f"/repos/{REPO}/pulls?state=open&per_page=100")
    if sum(LABEL in {l["name"] for l in pr.get("labels", [])} for pr in open_prs) >= max_open:
        raise ProposalError("too_many_open_proposals")
    base = gh("GET", f"/repos/{REPO}/git/ref/heads/main")["object"]["sha"]
    branch = "seo-agent/" + re.sub(r"[^a-z0-9-]+", "-", branch_hint.lower()).strip("-")[:50] + \
        "-" + date.today().strftime("%Y%m%d")
    gh("POST", f"/repos/{REPO}/git/refs", json={"ref": "refs/heads/" + branch, "sha": base})
    for path, text in files.items():
        payload = {"message": f"SEO agent: update {path}", "branch": branch,
                   "content": base64.b64encode(text.encode()).decode()}
        try:
            payload["sha"] = gh("GET", f"/repos/{REPO}/contents/{path}?ref={branch}")["sha"]
        except ProposalError:
            pass  # new file
        gh("PUT", f"/repos/{REPO}/contents/{path}", json=payload)
    pr = gh("POST", f"/repos/{REPO}/pulls", json={"title": title[:120], "head": branch, "base": "main",
                                                 "body": body[:60000], "draft": False})
    gh("POST", f"/repos/{REPO}/issues/{pr['number']}/labels", json={"labels": [LABEL]})
    return pr["number"]
