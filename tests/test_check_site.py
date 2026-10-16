import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from check_site import check  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PAGE = """<!doctype html><html lang="en"><head><meta name="viewport" content="width=device-width">
<title>Guide</title><meta name="description" content="A short, specific description.">
<link rel="canonical" href="https://usetradelink.com/guide/">
<script type="application/ld+json">{"@context":"https://schema.org","@graph":[
{"@type":"FAQPage","mainEntity":[{"@type":"Question","name":"Is it free?",
"acceptedAnswer":{"@type":"Answer","text":"No. It costs $40/month."}}]},
{"@type":"BreadcrumbList","itemListElement":[{"@type":"ListItem","position":1},{"@type":"ListItem","position":2}]}]}
</script></head><body><nav class="crumbs"><a href="/">Home</a> / Guide</nav>
<h1 id="top">Guide</h1><img src="/assets/a.png" alt="Screenshot">
<a href="/">Home</a><a href="/#pricing">Pricing</a><a href="#top">Top</a>
<div class="faq-item"><h3>Is it free?</h3><p>No. It costs $40/month.</p></div>
</body></html>"""
HOME = PAGE.replace("https://usetradelink.com/guide/", "https://usetradelink.com/").replace(
    '<h1 id="top">', '<h1 id="top"><span id="pricing"></span>')


class SiteCheckTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        self.write("index.html", HOME)
        self.write("guide/index.html", PAGE)
        self.write("assets/a.png", "png")
        self.write("robots.txt", "User-agent: *\nSitemap: https://usetradelink.com/sitemap.xml\n")
        self.write("llms.txt", "- [Guide](https://usetradelink.com/guide/)\n")
        self.sitemap(["https://usetradelink.com/", "https://usetradelink.com/guide/"])

    def write(self, rel, text):
        path = os.path.join(self.dir, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)

    def sitemap(self, urls):
        self.write("sitemap.xml", '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                   + "".join(f"<url><loc>{u}</loc></url>" for u in urls) + "</urlset>")

    def assertProblem(self, fragment):
        errors = check(self.dir)
        self.assertTrue(any(fragment in e for e in errors), errors)

    def test_repository_passes(self):
        self.assertEqual(check(ROOT), [])

    def test_fixture_passes(self):
        self.assertEqual(check(self.dir), [])

    def test_broken_links_and_anchors(self):
        self.write("guide/index.html", PAGE.replace('href="/#pricing"', 'href="/missing/"'))
        self.assertProblem("broken internal link /missing/")
        self.write("guide/index.html", PAGE.replace('href="/#pricing"', 'href="/#nope"'))
        self.assertProblem("missing #nope")

    def test_faq_schema_must_match_visible_answer(self):
        self.write("guide/index.html", PAGE.replace("<p>No. It costs $40/month.</p>", "<p>Yes.</p>"))
        self.assertProblem("FAQ schema answer differs")

    def test_breadcrumb_schema_must_match_visible_trail(self):
        self.write("guide/index.html", PAGE.replace("/ Guide</nav>", '/ <a href="/">Hub</a> / Guide</nav>'))
        self.assertProblem("BreadcrumbList has 2 items")

    def test_metadata_rules(self):
        self.write("guide/index.html", PAGE.replace("A short, specific description.", "x" * 200))
        self.assertProblem("meta description is 200")
        self.write("guide/index.html", PAGE.replace("specific description", "presale open now"))
        self.assertProblem("presale")
        self.write("guide/index.html", PAGE.replace("guide/\">", "guide\">"))
        self.assertProblem("canonical must be exactly")

    def test_sitemap_coverage_and_noindex(self):
        self.sitemap(["https://usetradelink.com/"])
        self.assertProblem("missing from sitemap.xml")
        self.sitemap(["https://usetradelink.com/", "https://usetradelink.com/guide/",
                      "https://usetradelink.com/gone/"])
        self.assertProblem("no page for https://usetradelink.com/gone/")
        self.write("guide/index.html", PAGE.replace("<title>", '<meta name="robots" content="noindex"><title>'))
        self.sitemap(["https://usetradelink.com/", "https://usetradelink.com/guide/"])
        self.assertProblem("noindex page is listed")

    def test_images_and_llms(self):
        self.write("guide/index.html", PAGE.replace('alt="Screenshot"', ""))
        self.assertProblem("has no alt attribute")
        self.write("llms.txt", "- [Gone](https://usetradelink.com/gone/)\n")
        self.assertProblem("llms.txt: broken link")


if __name__ == "__main__":
    unittest.main()
