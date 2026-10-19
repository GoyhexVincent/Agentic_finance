from __future__ import annotations

from datetime import date

from sma92.fixture import load_fixture
from sma92.market_data import load_price_csv


def test_load_price_csv_with_comments_and_duplicates(tmp_path) -> None:
    path = tmp_path / "prices.csv"
    path.write_text(
        "# source: test\n"
        "Date,Close\n"
        "2026-01-02,100.0\n"
        "2026-01-03,101.0\n"
        "2026-01-03,102.0\n"
        "2026-01-04,\n"
        "2026-01-05,103.0\n",
        encoding="utf-8",
    )
    series = load_price_csv(path)
    assert series.dates == (date(2026, 1, 2), date(2026, 1, 3), date(2026, 1, 5))
    assert series.closes.tolist() == [100.0, 102.0, 103.0]
    assert series.last_close == 103.0


def test_project_history_has_volatility_estimates() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    series = load_price_csv(root / "data" / "sma_solar_daily.csv")
    estimates = series.volatility_estimates()
    assert len(series.closes) > 200
    assert estimates["rv20"] is not None and 0.1 < estimates["rv20"] < 1.5
    assert estimates["rv60"] is not None and 0.1 < estimates["rv60"] < 1.5


def test_load_fixture_has_consistent_snapshot() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    snapshot = load_fixture(root / "data" / "fixtures" / "saxo_sim_snapshot.json")
    assert snapshot.synthetic is True
    assert snapshot.underlying.spot > 0
    assert len(snapshot.underlying_history) >= 30
    assert len(snapshot.contracts) == 20
    assert len(snapshot.option_history) == 10
    assert all(contract.expiry > snapshot.underlying.as_of.date() for contract in snapshot.contracts)
