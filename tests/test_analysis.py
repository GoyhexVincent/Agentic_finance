from __future__ import annotations

from pathlib import Path

import pytest

from sma92.analysis import run_analysis
from sma92.fixture import load_fixture

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def analysis():
    snapshot = load_fixture(ROOT / "data" / "fixtures" / "saxo_sim_snapshot.json")
    return run_analysis(snapshot, rate=0.02, dividend=0.0)


def test_metrics_are_complete(analysis) -> None:
    assert len(analysis.metrics) == 20
    assert len(analysis.expiry_summaries) == 2
    assert all(metric.mid is not None for metric in analysis.metrics)
    assert all(metric.iv is not None for metric in analysis.metrics)
    assert all(0.2 < metric.iv < 0.9 for metric in analysis.metrics)
    assert analysis.rv20 is not None and analysis.rv60 is None


def test_expiry_structure_and_skew(analysis) -> None:
    assert analysis.expiry_summaries[0].expiry < analysis.expiry_summaries[1].expiry
    assert analysis.expiry_summaries[0].atm_iv is not None
    assert analysis.term_slope is not None
    assert analysis.skew_25d is not None
    assert analysis.skew_25d > 0


def test_history_is_joined(analysis) -> None:
    with_history = [metric for metric in analysis.metrics if metric.history_points > 0]
    assert len(with_history) == 10
    assert all(metric.iv_start is not None for metric in with_history)
    assert all(metric.iv_change is not None for metric in with_history)


def test_rows_are_serializable(analysis) -> None:
    metric_rows = analysis.metric_rows()
    expiry_rows = analysis.expiry_rows()
    signal_rows = analysis.signal_rows()
    assert len(metric_rows) == 20
    assert len(expiry_rows) == 2
    assert signal_rows
    assert all("iv" in row and "flags" in row for row in metric_rows)


def test_synthetic_warnings(analysis) -> None:
    assert any("SYNTHÉTIQUES" in warning for warning in analysis.warnings)
