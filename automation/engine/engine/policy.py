"""Paths the engine may propose. Must match automation/approvals/policy.py (a test enforces it):
the approval executor refuses anything else."""
import re

GITHUB_ALLOWED_CONTENT = re.compile(
    r"^(?:resources|compare|blog|blogs|guides|tradovate-[a-z0-9-]+|"
    r"futures-[a-z0-9-]+|cloud-futures-[a-z0-9-]+|"
    r"prop-firm-[a-z0-9-]+|apex-[a-z0-9-]+|best-futures-[a-z0-9-]+)/"
    r"[a-z0-9/_-]*index\.html$"
)
ALLOWED_ROOTS = {"sitemap.xml", "llms.txt"}


def allowed_path(path):
    return path in ALLOWED_ROOTS or bool(GITHUB_ALLOWED_CONTENT.fullmatch(path))
