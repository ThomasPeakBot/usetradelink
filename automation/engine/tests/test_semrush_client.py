import unittest

from engine.providers import semrush

CSV = ("Keyword;Search Volume;CPC;Competition;Number of Results;Intent;Keyword Difficulty Index\r\n"
       "futures trade copier;140;3.42;0.24;118;1;6\r\n"
       "tradovate group trade;170;7.88;0.24;80;1,0;21\r\n")


class ParseTests(unittest.TestCase):
    def test_parse_csv_maps_headers(self):
        rows = semrush.parse_csv(CSV)
        self.assertEqual(rows[0]["keyword"], "futures trade copier")
        self.assertEqual(rows[0]["volume"], "140")
        self.assertEqual(rows[0]["kd"], "6")
        self.assertEqual(semrush.intents(rows[1]["intents"]), ["1", "0"])
        self.assertEqual(semrush.parse_csv(""), [])

    def test_numbers(self):
        self.assertEqual(semrush.to_number("140", int), 140)
        self.assertIsNone(semrush.to_number("n/a"))
        self.assertIsNone(semrush.to_number(""))

    def test_caps_fail_closed(self):
        import os
        from unittest.mock import patch
        with patch.dict(os.environ, {"X": "oops"}):
            self.assertEqual(semrush.capped("X", 100), 0)
        with patch.dict(os.environ, {"X": "999999"}):
            self.assertEqual(semrush.capped("X", 100), 100)
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(semrush.capped("X", 100), 0)


if __name__ == "__main__":
    unittest.main()
