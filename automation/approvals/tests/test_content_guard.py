import os
import unittest

from content_guard import CHECKOUT_URL, check_page, check_repo

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
PATH = "resources/index.html"


def page(head="", body=""):
    return ("<!doctype html><html><head><title>T</title>"
            '<link rel="canonical" href="https://usetradelink.com/resources/">'
            '<link rel="stylesheet" href="/seo.css"><script src="/tracking.js" async></script>'
            '<script type="application/ld+json">{"@context":"https://schema.org"}</script>'
            f"{head}</head><body><noscript><iframe src=\"https://www.googletagmanager.com/ns.html?id=GTM-NP74LT9B\">"
            f"</iframe></noscript><h1>H</h1><a href=\"{CHECKOUT_URL}\">Start</a>{body}</body></html>")


class ContentGuardTests(unittest.TestCase):
    def test_current_repository_passes(self):
        self.assertEqual(check_repo(REPO_ROOT), [])

    def test_baseline_page_passes(self):
        self.assertEqual(check_page(PATH, page()), [])

    def test_unsafe_markup_is_rejected(self):
        bad = {
            "external script": page(body='<script src="https://evil.example/x.js"></script>'),
            "inline script": page(body="<script>fetch('/x')</script>"),
            "event handler": page(body='<img src="/a.png" onerror="alert(1)">'),
            "javascript url": page(body='<a href="javascript:alert(1)">x</a>'),
            "iframe": page(body='<iframe src="https://evil.example"></iframe>'),
            "form": page(body='<form action="https://evil.example"></form>'),
            "meta refresh": page(head='<meta http-equiv="refresh" content="0;url=https://evil.example">'),
            "checkout swap": page(body='<a href="https://tlink.peakbot.com/checkout/price_other">x</a>'),
            "stylesheet": page(head='<link rel="stylesheet" href="https://evil.example/x.css">'),
            "noindex": page(head='<meta name="robots" content="noindex">'),
            "bad json-ld": page(head='<script type="application/ld+json">{oops</script>'),
            "svg": page(body="<svg><script>alert(1)</script></svg>"),
        }
        for name, html in bad.items():
            self.assertTrue(check_page(PATH, html), name)

    def test_canonical_and_headings_enforced(self):
        self.assertTrue(check_page("compare/x/index.html", page()))
        self.assertTrue(check_page(PATH, page(body="<h1>Second</h1>")))


if __name__ == "__main__":
    unittest.main()
