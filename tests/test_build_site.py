import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from build_site import build, is_public  # noqa: E402
from check_site import check  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


class BuildSiteTests(unittest.TestCase):
    def test_private_paths_never_published(self):
        for rel in ("automation/approvals/app.py", "automation/seo/README.md", "docs/plan.md",
                    ".github/workflows/x.yml", "scripts/build_site.py", "tests/test_x.py",
                    "requirements.txt", ".env", "automation/approvals/index.html",
                    "docs/index.html", "CNAME", ".nojekyll", "research/snapshot.json"):
            self.assertFalse(is_public(rel), rel)
        for rel in ("index.html", "resources/index.html", "assets/logo.png", "styles.css",
                    "main.js", "sitemap.xml", "robots.txt", "llms.txt", "_headers", "404.html"):
            self.assertTrue(is_public(rel), rel)

    def test_build_of_repository_is_complete_and_valid(self):
        out = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, out)
        copied = build(ROOT, out)
        self.assertFalse([c for c in copied if c.startswith(("automation/", "docs/", "scripts/"))])
        tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True,
                                 text=True).stdout.split()
        public = sorted(t for t in tracked if is_public(t))
        self.assertEqual(sorted(copied), public)
        # The built output must pass the same site checks as the repository.
        self.assertEqual(check(out), [])

    def test_private_file_types_abort_the_build(self):
        src = tempfile.mkdtemp()
        out = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, src)
        self.addCleanup(shutil.rmtree, out)
        for name in ("index.html", "404.html", "sitemap.xml", "robots.txt"):
            open(os.path.join(src, name), "w").close()
        self.assertEqual(len(build(src, os.path.join(out, "site"))), 4)
        os.remove(os.path.join(src, "robots.txt"))
        with self.assertRaises(SystemExit):
            build(src, os.path.join(out, "site"))


if __name__ == "__main__":
    unittest.main()
