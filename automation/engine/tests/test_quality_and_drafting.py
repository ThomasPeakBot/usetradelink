import json
import os
import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from engine import drafting, quality
from helpers import ENGINE, REPO, good_article

PATH = "guides/how-to-copy-trade-on-tradovate/index.html"
COMP = {"TradeSyncer": "tradesyncer.com"}


def check(html, existing=None, **kw):
    return quality.check(html, PATH, existing or {}, "article", "copy trades on tradovate",
                         competitor_domains=COMP, **kw)


class QualityGateTests(unittest.TestCase):
    def test_substantive_original_page_passes(self):
        r = check(good_article())
        self.assertTrue(r["passed"], r["errors"])

    def test_duplicate_of_existing_page_fails(self):
        html = good_article()
        r = check(html, {"https://usetradelink.com/x/": quality.Blocks and " ".join(
            b["text"] for b in _blocks(html))})
        self.assertFalse(r["passed"])
        self.assertTrue(any("too similar" in e for e in r["errors"]))

    def test_repeated_disclosure_note_is_allowed(self):
        note = ('<p class="note">Published by PeakBot, the maker of TradeLink. We identify where other tools are '
                'a better fit and link to original sources whenever we compare products.</p>')
        existing = {"https://usetradelink.com/x/": "Published by PeakBot, the maker of TradeLink. We identify where "
                    "other tools are a better fit and link to original sources whenever we compare products."}
        self.assertTrue(check(good_article(note * 3), existing)["passed"])
        copied = good_article(note.replace(' class="note"', "") * 3)
        self.assertFalse(check(copied, existing)["passed"])

    def test_thin_and_structural_problems(self):
        r = check("<html><head><title>x</title></head><body><h1>a</h1><h1>b</h1><p>Short.</p></body></html>")
        joined = " ".join(r["errors"])
        for needle in ("exactly one <h1>", "meta description", "canonical", "thin content"):
            self.assertIn(needle, joined)

    def test_keyword_stuffing(self):
        r = check(good_article("<p>" + "copy trades on tradovate " * 40 + "</p>"))
        self.assertTrue(any("stuffing" in e for e in r["errors"]))

    def test_forbidden_claims_but_disclaimers_allowed(self):
        bad = check(good_article("<p>TradeLink is guaranteed to match every fill.</p>"))
        self.assertTrue(any("forbidden claim" in e for e in bad["errors"]))
        ok = check(good_article("<p>Copying does not guarantee identical fills.</p>"))
        self.assertTrue(ok["passed"], ok["errors"])
        q = check(good_article("<p>Can a copier guarantee identical fills?</p>"))
        self.assertTrue(q["passed"], q["errors"])

    def test_filler_phrasing(self):
        r = check(good_article("<p>In today's fast-paced markets, delve into seamless copying.</p>"))
        self.assertTrue(any("filler" in e for e in r["errors"]))

    def test_disputed_and_unverified_product_claims(self):
        r = check(good_article("<p>TradeLink supports NinjaTrader accounts.</p>"))
        self.assertTrue(any("disputed fact 'ninjatrader'" in e for e in r["errors"]))
        r = check(good_article("<p>TradeLink works with Rithmic and TopstepX.</p>"))
        self.assertTrue(any("compatibility" in e for e in r["errors"]))
        ok = check(good_article("<p>TradeLink does not support Rithmic yet; it is coming soon.</p>"))
        self.assertTrue(ok["passed"], ok["errors"])
        r = check(good_article("<p>TradeLink has ten protection rules.</p>"))
        self.assertTrue(any("ten_rules" in e for e in r["errors"]))
        # A TradeLink-only phrase is caught in a comparison-table cell that doesn't name TradeLink.
        r = check(good_article("<table><tr><td>Risk rules</td><td>Ten protection rules, such as "
                               "daily loss limits</td></tr></table>"))
        self.assertTrue(any("ten_rules" in e for e in r["errors"]))

    def test_competitor_figures_need_inline_source_and_date(self):
        r = check(good_article("<p>TradeSyncer's Basic plan costs $49 per month.</p>"))
        self.assertTrue(any("unsourced figure" in e for e in r["errors"]))
        sourced = good_article('<p>TradeSyncer lists its Basic plan at $49 per month '
                               '(<a href="https://tradesyncer.com/pricing/">pricing</a>).</p>')
        r = check(sourced)
        self.assertTrue(any("last verified" in e for e in r["errors"]))
        r = check(sourced.replace("</body>", "<p>Last verified October 16, 2026.</p></body>"))
        self.assertTrue(r["passed"], r["errors"])

    def test_published_tradelink_price_needs_no_citation(self):
        self.assertTrue(check(good_article("<p>TradeLink costs $40 a month.</p>"))["passed"])


def _blocks(html):
    b = quality.Blocks()
    b.feed(html)
    return b.blocks


BRIEF = {"target_url": "https://usetradelink.com/guides/how-to-copy-trade-on-tradovate/", "path": PATH,
         "internal_links": [{"url": "https://usetradelink.com/tradovate-trade-copier/", "title": "Tradovate copier"}],
         "action": "new_page", "content_type": "article", "head_keyword": "how to copy trade on tradovate",
         "keywords": ["how to copy trade on tradovate"], "why": "test", "questions_people_search": [],
         "current_queries": [], "competitor_pages": [], "allowed_facts": [], "blocked_facts": [],
         "forbidden_claims": [], "requirements": []}


class RenderTests(unittest.TestCase):
    def draft(self, **over):
        d = {"title": "How to Copy Trades on Tradovate", "meta_description": "x" * 100, "h1": "Copy trades on Tradovate",
             "intro_html": "<p>Intro <script>alert(1)</script><a href='javascript:alert(1)'>bad</a></p>",
             "sections": [{"heading": "Setup <b>steps</b>", "html": "<p onclick='x()'>Body <img src=x onerror=y></p>"}],
             "faq": [{"question": "Does Tradovate have a copier?", "answer": "<p>Group Trade copies within one login.</p>"}],
             "sources": [{"url": "https://support.tradovate.com/", "label": "Tradovate support"},
                         {"url": "javascript:alert(1)", "label": "bad"}], "unverifiable_points": []}
        d.update(over)
        return d

    def test_render_is_sanitized_and_valid(self):
        html = drafting.render_page(self.draft(), BRIEF, today=date(2026, 10, 16))
        for bad in ("<script>alert", "javascript:", "onclick", "onerror", "<img src=x"):
            self.assertNotIn(bad, html)
        self.assertIn("Setup &lt;b&gt;steps&lt;/b&gt;", html)
        self.assertIn('<link rel="canonical" href="https://usetradelink.com/guides/how-to-copy-trade-on-tradovate/">', html)
        self.assertIn("Last verified October 16, 2026", html)
        ld = json.loads(html.split('<script type="application/ld+json">')[1].split("</script>")[0])
        faq = [n for n in ld["@graph"] if n["@type"] == "FAQPage"][0]
        self.assertEqual(faq["mainEntity"][0]["acceptedAnswer"]["text"], "Group Trade copies within one login.")

    def test_rendered_page_passes_security_and_site_checks(self):
        import sys
        sys.path.insert(0, os.path.join(REPO, "automation", "approvals"))
        from content_guard import check_page
        html = drafting.render_page(self.draft(), BRIEF, today=date(2026, 10, 16))
        self.assertEqual(check_page(PATH, html), [])

    def test_llm_disabled_by_default(self):
        with patch.dict(os.environ, {"CONTENT_LLM_ENABLED": "false"}):
            self.assertEqual(drafting.draft_with_claude(BRIEF, []), (None, "content_llm_disabled"))

    def _client(self, stop_reason, text):
        message = SimpleNamespace(stop_reason=stop_reason, model="claude-opus-5-5",
                                  content=[SimpleNamespace(type="text", text=text)],
                                  usage=SimpleNamespace(input_tokens=10, output_tokens=20))
        stream = MagicMock()
        stream.__enter__.return_value.get_final_message.return_value = message
        client = MagicMock()
        client.beta.messages.stream.return_value = stream
        return client

    @patch.dict(os.environ, {"CONTENT_LLM_ENABLED": "true"})
    def test_llm_success_refusal_and_bad_json(self):
        client = self._client("end_turn", json.dumps(self.draft()))
        draft, usage = drafting.draft_with_claude(BRIEF, ["Existing page"], client=client)
        self.assertEqual(draft["h1"], "Copy trades on Tradovate")
        kwargs = client.beta.messages.stream.call_args.kwargs
        self.assertEqual(kwargs["model"], "claude-opus-5-5")
        self.assertEqual(kwargs["fallbacks"], "default")
        self.assertEqual(kwargs["output_config"]["format"]["type"], "json_schema")
        self.assertEqual(drafting.draft_with_claude(BRIEF, [], client=self._client("refusal", ""))[1], "refused")
        self.assertEqual(drafting.draft_with_claude(BRIEF, [], client=self._client("end_turn", "{oops"))[1],
                         "invalid_json")
        self.assertEqual(drafting.draft_with_claude(BRIEF, [], client=self._client("max_tokens", "{}"))[1],
                         "truncated")


class PolicyParityTests(unittest.TestCase):
    def test_engine_policy_matches_executor_policy(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "approvals_policy", os.path.join(REPO, "automation", "approvals", "policy.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        from engine import policy
        self.assertEqual(policy.GITHUB_ALLOWED_CONTENT.pattern, mod.GITHUB_ALLOWED_CONTENT.pattern)
        self.assertEqual(policy.ALLOWED_ROOTS, mod.ALLOWED_ROOTS)


class FactsRegistryTests(unittest.TestCase):
    def test_registry_is_well_formed(self):
        with open(os.path.join(ENGINE, "facts", "tradelink_facts.json")) as fh:
            facts = json.load(fh)
        ids = [f["id"] for f in facts["facts"]]
        self.assertEqual(len(ids), len(set(ids)))
        for f in facts["facts"]:
            self.assertIn(f["status"], ("published", "disputed", "needs_confirmation"))
            self.assertTrue(f["source"].startswith("https://usetradelink.com/"))
        self.assertEqual({f["id"] for f in facts["facts"] if f["status"] != "published"}, {"ninjatrader", "ten_rules"})


if __name__ == "__main__":
    unittest.main()
