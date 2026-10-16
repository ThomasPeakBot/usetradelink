import unittest

from main import PageParser, extract_sitemap, normalized_url

class SeoAuditTests(unittest.TestCase):
    def test_sitemap_accepts_domain_only(self):
        xml = """<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
        <url><loc>https://usetradelink.com/a/</loc></url>
        <url><loc>https://other.example/a/</loc></url>
        <url><loc>https://usetradelink.com/a/</loc></url></urlset>"""
        self.assertEqual(extract_sitemap(xml), ["https://usetradelink.com/a/"])

    def test_title_description_canonical_links(self):
        p = PageParser()
        p.feed("""<html><head><title>TradeLink Trade Copier</title>
        <meta name="description" content="Guide to copying trades">
        <link rel="canonical" href="https://usetradelink.com/guide/"></head>
        <body><h1>Trade Copier</h1><a href="/resources/">Resources</a></body></html>""")
        self.assertEqual(p.title, "TradeLink Trade Copier")
        self.assertEqual(p.h1_count, 1)
        self.assertEqual(p.description, "Guide to copying trades")
        self.assertEqual(p.canonical, "https://usetradelink.com/guide/")
        self.assertEqual(p.links, ["/resources/"])

    def test_normalization_drops_tracking_query(self):
        self.assertEqual(normalized_url("https://usetradelink.com/guide?utm_medium=cpc#section"),
                         "https://usetradelink.com/guide/")

if __name__ == "__main__":
    unittest.main()
