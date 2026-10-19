from __future__ import annotations

import unittest
from datetime import date, timedelta

from finance_tracker.analytics import (
    _binary_segmentation,
    _break_score,
    cluster_assets,
    detect_breaks,
)


class TestClustering(unittest.TestCase):
    def test_two_clear_groups(self):
        tickers = ["A", "B", "C", "D"]
        correlations = {
            frozenset(("A", "B")): 0.9,
            frozenset(("C", "D")): 0.9,
            frozenset(("A", "C")): -0.2,
            frozenset(("A", "D")): -0.2,
            frozenset(("B", "C")): -0.2,
            frozenset(("B", "D")): -0.2,
        }
        clusters = cluster_assets(tickers, correlations, k=2)
        self.assertEqual(len(clusters), 2)
        self.assertTrue(any(set(group) == {"A", "B"} for group in clusters))
        self.assertTrue(any(set(group) == {"C", "D"} for group in clusters))

    def test_single_asset(self):
        self.assertEqual(cluster_assets(["A"], {}, k=5), [["A"]])

    def test_k_is_clamped(self):
        clusters = cluster_assets(["A", "B"], {frozenset(("A", "B")): 0.5}, k=99)
        self.assertEqual(len(clusters), 2)


class TestBreaks(unittest.TestCase):
    def test_binary_segmentation_finds_step(self):
        values = [0.2] * 300 + [0.8] * 300
        breaks = _binary_segmentation(values, max_breaks=3, min_size=100, min_effect=0.1)
        self.assertEqual(len(breaks), 1)
        self.assertTrue(295 <= breaks[0] <= 305)

    def test_binary_segmentation_ignores_flat_series(self):
        values = [0.5] * 600
        self.assertEqual(_binary_segmentation(values, max_breaks=3, min_size=100), [])

    def test_detect_breaks_returns_dates(self):
        values = [0.2] * 300 + [0.8] * 300
        dates = [date(2020, 1, 1) + timedelta(days=i) for i in range(600)]
        method, breaks = detect_breaks(values, dates, max_breaks=3, min_size=100)
        self.assertIn(method, ("binary_segmentation", "ruptures_binseg"))
        self.assertEqual(len(breaks), 1)
        self.assertTrue(295 <= dates.index(breaks[0]["break_date"]) <= 305)

    def test_break_score_positive_for_step(self):
        values = [0.2] * 100 + [0.7] * 100
        self.assertGreater(_break_score(values, 100, 50), 0.4)


if __name__ == "__main__":
    unittest.main()
