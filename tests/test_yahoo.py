from __future__ import annotations

import json
import unittest
from datetime import date
from pathlib import Path

from finance_tracker import yahoo

FIXTURE = Path(__file__).parent / "fixtures" / "yahoo_chart.json"


class TestYahooParsing(unittest.TestCase):
    def setUp(self):
        self.payload = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_parse_chart_returns_sorted_deduplicated_rows(self):
        meta, rows = yahoo.parse_chart(self.payload, expected_symbol="TEST")
        self.assertEqual(meta["currency"], "USD")
        self.assertEqual(len(rows), 3)
        self.assertEqual([row["trade_date"] for row in rows], [
            date(2024, 1, 2),
            date(2024, 1, 3),
            date(2024, 1, 4),
        ])
        # La dernière ligne du 4 janvier écrase la précédente.
        self.assertEqual(rows[-1]["close"], 103.0)
        self.assertEqual(rows[-1]["adj_close"], 103.0)

    def test_parse_chart_rejects_wrong_symbol(self):
        with self.assertRaises(yahoo.YahooError):
            yahoo.parse_chart(self.payload, expected_symbol="AUTRE")

    def test_parse_chart_handles_missing_adjclose(self):
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        payload["chart"]["result"][0]["indicators"].pop("adjclose")
        _, rows = yahoo.parse_chart(payload, expected_symbol="TEST")
        self.assertEqual(rows[0]["adj_close"], rows[0]["close"])

    def test_parse_chart_handles_error(self):
        with self.assertRaises(yahoo.YahooError):
            yahoo.parse_chart({"chart": {"result": [], "error": {"description": "boom"}}})


class TestPeriodBounds(unittest.TestCase):
    def test_period_is_exclusive_at_end(self):
        start, end = yahoo._period_bounds(date(2024, 1, 1), date(2024, 1, 2))
        self.assertLess(start, end)
        self.assertEqual(end - start, 2 * 86400)
