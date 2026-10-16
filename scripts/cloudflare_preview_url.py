"""Print the Cloudflare Pages preview URL for a commit, waiting for the deploy to finish.

Reads the "Cloudflare Pages" check run that Cloudflare's GitHub integration posts.
Needs GITHUB_TOKEN (checks: read), GITHUB_REPOSITORY and the commit SHA argument.
"""
import json
import os
import re
import sys
import time
import urllib.request

URL = re.compile(r"https://[a-z0-9]{8}\.usetradelink\.pages\.dev")


def check_runs(repo, sha, token):
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/commits/{sha}/check-runs?check_name=Cloudflare%20Pages",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r).get("check_runs", [])


def main(argv):
    sha, repo, token = argv[1], os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_TOKEN"]
    deadline = time.time() + int(os.getenv("PREVIEW_WAIT_SECONDS", "900"))
    while time.time() < deadline:
        for run in check_runs(repo, sha, token):
            if run.get("status") == "completed":
                if run.get("conclusion") != "success":
                    print(f"Cloudflare deploy concluded {run.get('conclusion')}", file=sys.stderr)
                    return 1
                m = URL.search((run.get("output") or {}).get("summary") or "")
                if m:
                    print(m.group(0))
                    return 0
        time.sleep(20)
    print("No Cloudflare Pages preview found for this commit", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
