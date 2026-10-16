"""Build the public website into _site/ from an explicit allowlist.

Cloudflare Pages currently publishes the repository root, so anything committed
(automation code, docs, workflows, tests) becomes a public URL. Point Pages at
this script instead:

    Build command:           python3 scripts/build_site.py
    Build output directory:  _site

Only website file types outside the private directories are copied, and the
build fails if anything private would be published.
"""
import os
import shutil
import sys

PUBLIC_EXTENSIONS = {".html", ".css", ".js", ".png", ".jpg", ".jpeg", ".webp", ".avif",
                     ".gif", ".svg", ".ico", ".mp4", ".webm", ".woff", ".woff2", ".xml",
                     ".txt", ".webmanifest"}
# Cloudflare Pages control files and other extension-less public files.
PUBLIC_NAMES = {"_headers", "_redirects"}
PRIVATE_DIRS = {"automation", "docs", "scripts", "tests", "_src", "_site", "node_modules",
                "research"}
PRIVATE_NAMES = {"requirements.txt", "CNAME", ".env", ".env.example"}
OUT = "_site"


def is_public(rel):
    parts = rel.split("/")
    if any(p.startswith(".") for p in parts):
        return False
    if parts[0] in PRIVATE_DIRS or parts[-1] in PRIVATE_NAMES:
        return False
    if parts[-1] in PUBLIC_NAMES and len(parts) == 1:
        return True
    return os.path.splitext(parts[-1])[1].lower() in PUBLIC_EXTENSIONS


def site_files(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and
                             not (os.path.relpath(dirpath, root) == "." and d in PRIVATE_DIRS))
        for name in sorted(filenames):
            rel = os.path.relpath(os.path.join(dirpath, name), root).replace(os.sep, "/")
            if is_public(rel):
                yield rel


def build(root=".", out=None):
    out = out or os.path.join(root, OUT)
    if os.path.exists(out):
        shutil.rmtree(out)
    copied = []
    for rel in site_files(root):
        dest = os.path.join(out, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copy2(os.path.join(root, rel), dest)
        copied.append(rel)
    for required in ("index.html", "404.html", "sitemap.xml", "robots.txt"):
        if required not in copied:
            raise SystemExit(f"build_site: required file {required} missing")
    leaked = [r for r in copied if r.endswith((".py", ".md", ".json", ".yml", ".yaml", ".sql"))]
    if leaked:
        raise SystemExit(f"build_site: refusing to publish private files: {leaked}")
    return copied


def main(argv):
    copied = build(argv[1] if len(argv) > 1 else ".")
    print(f"build_site: {len(copied)} public files written to {OUT}/")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
