import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import main
from main import canonical_target, inspect_page, run_status

URL = "https://usetradelink.com/guide/"


class Resp:
    def __init__(self, text="", status=200, url=URL, history=(), headers=None):
        self.text = text
        self.status_code = status
        self.url = url
        self.history = list(history)
        self.headers = {"content-type": "text/html; charset=utf-8", **(headers or {})}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise main.requests.HTTPError(str(self.status_code))


class Session:
    def __init__(self, pages):
        self.pages = pages
        self.headers = {}

    def get(self, url, **_kw):
        value = self.pages[url]
        if isinstance(value, Exception):
            raise value
        return value


def html(title="Guide", desc="A useful guide", canonical=URL, extra=""):
    return (f"<html><head><title>{title}</title><meta name='description' content='{desc}'>"
            f"<link rel='canonical' href='{canonical}'>{extra}</head><body><h1>G</h1>"
            "<a href='/resources/'>r</a><a href='https://other.example/'>x</a></body></html>")


class InspectTests(unittest.TestCase):
    def issues(self, resp):
        return inspect_page(Session({URL: resp}), URL, [URL])["issues"]

    def test_clean_page(self):
        self.assertEqual(self.issues(Resp(html())), [])

    def test_trailing_slash_canonical_mismatch_is_reported(self):
        self.assertIn("canonical_mismatch",
                      self.issues(Resp(html(canonical="https://usetradelink.com/guide"))))
        self.assertEqual(canonical_target(URL, "/guide/"), URL)

    def test_redirected_sitemap_url_is_reported(self):
        self.assertIn("sitemap_url_redirects", self.issues(Resp(html(), history=[object()])))

    def test_off_domain_redirect_and_errors(self):
        self.assertIn("redirect_off_domain", self.issues(Resp(html(), url="https://evil.example/")))
        self.assertIn("http_non_200", self.issues(Resp("", status=404)))
        self.assertIn("fetch_error:Timeout", self.issues(main.requests.Timeout()))

    def test_lengths_noindex_and_h1(self):
        found = self.issues(Resp(html(title="T" * 90, desc="D" * 200),
                                 headers={"x-robots-tag": "noindex"}))
        for issue in ("title_too_long", "description_too_long", "noindex"):
            self.assertIn(issue, found)
        self.assertIn("h1_count_not_one", self.issues(Resp(html().replace("<h1>G</h1>", ""))))

    def test_run_status(self):
        self.assertEqual(run_status("ok"), "complete")
        self.assertEqual(run_status("not_configured"), "complete")
        self.assertEqual(run_status("error:HTTPError"), "partial")


class DryRunTests(unittest.TestCase):
    def test_dry_run_never_calls_gsc_db_or_semrush(self):
        sitemap = ("<urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>"
                   f"<url><loc>{URL}</loc></url></urlset>")
        session = Session({main.SITEMAP: Resp(sitemap), URL: Resp(html())})
        out = io.StringIO()
        with patch("main.requests.Session", return_value=session), \
                patch("main.read_gsc") as gsc, patch("main.save_database") as db, \
                patch("main.enrich_semrush") as semrush, \
                patch.dict("os.environ", {"DATABASE_URL": "x", "GSC_SERVICE_ACCOUNT_JSON": "{}"}), \
                redirect_stdout(out):
            self.assertEqual(main.main(["--dry-run"]), 0)
        gsc.assert_not_called(); db.assert_not_called(); semrush.assert_not_called()
        report = json.loads(out.getvalue())
        self.assertEqual((report["pages"], report["storage"], report["publishing_enabled"]),
                         (1, "none", False))

    def test_empty_sitemap_fails(self):
        session = Session({main.SITEMAP: Resp("<urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'/>")})
        with patch("main.requests.Session", return_value=session):
            with self.assertRaises(RuntimeError):
                main.main(["--dry-run"])


if __name__ == "__main__":
    unittest.main()
