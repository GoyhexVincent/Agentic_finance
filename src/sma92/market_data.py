"""Chargement de séries de prix et estimation de volatilité."""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np

from .black_scholes import ewma_volatility, realized_volatility


def parse_date(value: str) -> date:
    """Parse une date ISO ou un timestamp Yahoo en date UTC."""
    text = value.strip()
    if not text:
        raise ValueError("date vide")
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        pass
    normalized = text.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized).astimezone(timezone.utc).date()


@dataclass(frozen=True)
class PriceSeries:
    dates: tuple[date, ...]
    closes: np.ndarray
    source: str = "csv"

    def __post_init__(self) -> None:
        if len(self.dates) != len(self.closes):
            raise ValueError("dates et closes doivent avoir la même longueur")
        if len(self.dates) < 2:
            raise ValueError("il faut au moins deux observations")
        if np.any(~np.isfinite(self.closes)) or np.any(self.closes <= 0):
            raise ValueError("les closes doivent être finis et strictement positifs")
        if any(self.dates[i] >= self.dates[i + 1] for i in range(len(self.dates) - 1)):
            raise ValueError("les dates doivent être strictement croissantes")

    @property
    def returns(self) -> np.ndarray:
        return np.diff(np.log(self.closes))

    @property
    def last_date(self) -> date:
        return self.dates[-1]

    @property
    def last_close(self) -> float:
        return float(self.closes[-1])

    def volatility_estimates(self) -> dict[str, float | None]:
        """Volatilités réalisées annualisées sur plusieurs fenêtres."""
        n_returns = len(self.returns)
        estimates: dict[str, float | None] = {}
        for label, window in (("rv20", 20), ("rv60", 60), ("rv252", 252)):
            if n_returns >= max(5, window // 2):
                estimates[label] = realized_volatility(self.closes, window=min(window, n_returns))
            else:
                estimates[label] = None
        estimates["ewma"] = (
            ewma_volatility(self.closes) if n_returns >= 10 else estimates.get("rv20")
        )
        return estimates

    def window(self, start: date, end: date) -> "PriceSeries":
        selected = [(d, float(c)) for d, c in zip(self.dates, self.closes) if start <= d <= end]
        if len(selected) < 2:
            raise ValueError(f"moins de deux observations entre {start} et {end}")
        return PriceSeries(
            dates=tuple(d for d, _ in selected),
            closes=np.asarray([c for _, c in selected], dtype=float),
            source=self.source,
        )


def load_price_csv(path: str | Path, *, source: str | None = None) -> PriceSeries:
    """Charge un CSV avec une colonne date et une colonne close.

    Les colonnes sont détectées sans tenir compte de la casse : ``date``/``time``
    et ``close``/``adj close``/``last``. Les lignes commençant par ``#`` sont
    ignorées, ce qui permet d'ajouter un en-tête de provenance.
    """
    csv_path = Path(path)
    if not csv_path.exists():
        raise FileNotFoundError(f"fichier de prix introuvable : {csv_path}")

    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = [row for row in csv.reader(handle) if row and not row[0].lstrip().startswith("#")]
    if len(rows) < 2:
        raise ValueError(f"CSV vide ou sans données : {csv_path}")

    header = [cell.strip().lower() for cell in rows[0]]
    date_candidates = ("date", "time", "datetime", "timestamp")
    close_candidates = ("close", "adj close", "adj_close", "last", "price")
    date_idx = next((header.index(name) for name in date_candidates if name in header), None)
    close_idx = next((header.index(name) for name in close_candidates if name in header), None)
    if date_idx is None or close_idx is None:
        raise ValueError(
            f"colonnes date/close introuvables dans {csv_path.name}; en-tête={header}"
        )

    by_date: dict[date, float] = {}
    for row in rows[1:]:
        if len(row) <= max(date_idx, close_idx):
            continue
        try:
            day = parse_date(row[date_idx])
            close = float(row[close_idx].strip().replace(" ", "").replace(",", "."))
        except (ValueError, TypeError):
            continue
        if not math.isfinite(close) or close <= 0:
            continue
        by_date[day] = close

    if len(by_date) < 2:
        raise ValueError(f"pas assez de lignes valides dans {csv_path}")
    ordered = sorted(by_date.items())
    return PriceSeries(
        dates=tuple(day for day, _ in ordered),
        closes=np.asarray([close for _, close in ordered], dtype=float),
        source=source or str(csv_path),
    )


def annualized_realized_vol(prices: PriceSeries, window: int = 20) -> float | None:
    """Volatilité réalisée annualisée, ou ``None`` si l'historique est trop court."""
    if len(prices.closes) < max(5, window // 2):
        return None
    return realized_volatility(prices.closes, window=min(window, len(prices.closes) - 1))


def daily_returns_from_closes(closes: list[float] | np.ndarray) -> np.ndarray:
    values = np.asarray(closes, dtype=float)
    if len(values) < 2:
        raise ValueError("au moins deux closes requis")
    return np.diff(np.log(values))
