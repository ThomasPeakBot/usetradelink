import unittest
from datetime import date

from engine import keywords, opportunities
from helpers import page, sig

HOME = "https://usetradelink.com/"
TV = "https://usetradelink.com/tradovate-trade-copier/"
MULTI = "https://usetradelink.com/tradovate-multiple-accounts/"
PROP = "https://usetradelink.com/prop-firm-copy-trading/"
PAGES = [page(HOME, "TradeLink by PeakBot | Futures Copy Trading for Prop Traders"),
         page(TV, "Tradovate Trade Copier for Prop Accounts", headings=["Group Trade limits"], links=[PROP]),
         page(MULTI, "Copy Trades Across Multiple Tradovate Logins", links=[TV]),
         page(PROP, "Can You Copy Trades Between Prop Firm Accounts?", links=[TV],
              modified=date(2026, 1, 1))]
TODAY = date(2026, 10, 16)


def build(signals, **kw):
    return opportunities.build(signals, PAGES, ("tradesyncer", "tradecopia"), today=TODAY, **kw)


def by(items, action):
    return [o for o in items if o["action"] == action]


class KeywordTests(unittest.TestCase):
    def test_relevance_is_conservative(self):
        self.assertEqual(keywords.relevance("tradovate trade copier"), 1.0)
        self.assertEqual(keywords.relevance("mt4 trade copier"), 0.0)
        self.assertEqual(keywords.relevance("http track website copier"), 0.0)
        self.assertEqual(keywords.relevance("best crypto copy trading"), 0.0)
        self.assertLess(keywords.relevance("largest copy trading platforms"), 0.6)
        self.assertEqual(keywords.relevance("trade copier"), 0.6)
        self.assertEqual(keywords.relevance("tradesyncer", ("tradesyncer",)), 0.4)
        self.assertEqual(keywords.relevance("tradesyncer alternative", ("tradesyncer",)), 0.8)

    def test_intent_prefers_provider_codes(self):
        self.assertEqual(keywords.intent("tradovate trade copier", ["1"]), "informational")
        self.assertEqual(keywords.intent("tradesyncer vs tradecopia"), "commercial")
        self.assertEqual(keywords.intent("how to copy trade on tradovate"), "informational")

    def test_clustering_groups_variants(self):
        groups = keywords.cluster(["copy trades between prop firms", "copy trade between prop firm",
                                   "tradovate group trade"], {"copy trades between prop firms": 50})
        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[0]["head"], "copy trades between prop firms")


class OpportunityTests(unittest.TestCase):
    def test_no_evidence_no_action(self):
        items = build({"tradovate copier setup": sig()})
        self.assertEqual([o["action"] for o in items if o["head_keyword"] == "tradovate copier setup"],
                         ["no_action"])

    def test_new_page_needs_real_demand_and_relevance(self):
        items = build({"apex trade copier": sig(260, 12, competitors=[{"domain": "tradecopia.com", "position": 3}]),
                       "copygram": sig(880, 23)})
        new = by(items, "new_page")
        self.assertEqual([o["head_keyword"] for o in new], ["apex trade copier"])
        self.assertIn("tradecopia.com", new[0]["rationale"])
        self.assertGreater(new[0]["priority"], 0)

    def test_navigational_competitor_brand_is_not_a_page(self):
        items = build({"tradesyncer": sig(18100, 35, intents=["2"])})
        self.assertEqual(by(items, "new_page"), [])

    def test_ctr_and_position_rules(self):
        low_ctr = build({"tradovate trade copier": sig(170, 19, impressions=400, clicks=2, position=4.2,
                                                       pages={TV: 400})})
        self.assertEqual(low_ctr[0]["action"], "rewrite_title_meta")
        page_two = build({"tradovate trade copier": sig(170, 19, impressions=300, clicks=3, position=14,
                                                        pages={TV: 300})})
        self.assertEqual(page_two[0]["action"], "improve_page")
        self.assertEqual(page_two[0]["target_url"], TV)

    def test_cannibalization_from_search_console(self):
        items = build({"copy trades tradovate logins": sig(impressions=200, clicks=4, position=12,
                                                           pages={TV: 110, MULTI: 90})})
        self.assertEqual(items[0]["action"], "consolidate_cannibalization")
        self.assertIn(MULTI, items[0]["rationale"])

    def test_cooling_off_after_a_recent_change(self):
        items = build({"tradovate trade copier": sig(170, 19, impressions=300, clicks=3, position=14,
                                                     pages={TV: 300})},
                      recent_changes={TV: date(2026, 10, 1)})
        item = [o for o in items if o["target_url"] == TV][0]
        self.assertEqual(item["action"], "no_action")
        self.assertIn("waiting until", item["rationale"])

    def test_unanswered_question_on_closest_page(self):
        items = build({"how to copy trade on topstep": sig(140, 25)},
                      config={"owner_coverage": 0.3})
        found = [o for o in items if o["head_keyword"] == "how to copy trade on topstep"]
        self.assertIn(found[0]["action"], ("improve_page", "new_page"))

    def test_outdated_third_party_pages_flagged(self):
        items = build({})
        self.assertEqual([o["target_url"] for o in by(items, "refresh_outdated")], [PROP])

    def test_fingerprints_are_stable(self):
        a = build({"apex trade copier": sig(260, 12)})
        b = build({"apex trade copier": sig(300, 12)})
        self.assertEqual(a[0]["fingerprint"], b[0]["fingerprint"])


if __name__ == "__main__":
    unittest.main()
