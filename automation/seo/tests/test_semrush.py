import os
import unittest
from unittest.mock import patch

from semrush import DEFAULT_SEEDS, enrich, parse_metrics, positive_limited

class SemrushTests(unittest.TestCase):
    def test_fixture_metrics_and_cpc(self):
        payload = {
            "meta": {"success": True},
            "data": {"search_volume": "230", "keyword_difficulty": 23,
                     "cpc": "145", "intents": ["COMMERCIAL", "INFORMATIONAL"],
                     "competitive_density": 12}
        }
        result = parse_metrics(payload)
        self.assertEqual(result["volume"], 230)
        self.assertEqual(float(result["cpc_usd"]), 1.45)
        self.assertEqual(result["intents"], ["COMMERCIAL", "INFORMATIONAL"])

    def test_bad_payload_is_rejected(self):
        for payload in ({}, {"meta": {"success": False}}, {"meta": {"success": True}, "data": {}}):
            with self.assertRaises(ValueError):
                parse_metrics(payload)

    @patch.dict(os.environ, {"SEMRUSH_ENABLED": "false", "SEMRUSH_API_KEY": "DO-NOT-USE"})
    def test_no_api_request_when_not_enabled(self):
        class NoSession:
            def get(self, *_a, **_kw):
                raise AssertionError("no API call should happen")
        result = enrich("invalid database", NoSession())
        self.assertEqual(result["status"], "disabled")
        self.assertEqual(result["requests"], 0)

    @patch.dict(os.environ, {"SEMRUSH_ENABLED": "true"}, clear=True)
    def test_no_api_request_if_key_missing(self):
        self.assertEqual(enrich("invalid")["status"], "missing_key")

    @patch.dict(os.environ, {"SEMRUSH_ENABLED": "true", "SEMRUSH_API_KEY": "unused",
                             "SEMRUSH_MAX_UNITS_PER_RUN": "0"})
    def test_budget_zero_blocks_all_calls(self):
        self.assertEqual(enrich("invalid")["status"], "budget_disabled")

    def test_safe_budget_limits(self):
        with patch.dict(os.environ, {"TEST_CAP": "999999"}):
            self.assertEqual(positive_limited("TEST_CAP", 100, 400), 400)
        with patch.dict(os.environ, {"TEST_CAP": "oops"}):
            self.assertEqual(positive_limited("TEST_CAP", 100, 400), 0)

if __name__ == "__main__":
    unittest.main()
