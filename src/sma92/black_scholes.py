"""Outils Black-Scholes et volatilité utilisés par le laboratoire SMA92."""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np

TRADING_DAYS = 252.0


def norm_cdf(x: float) -> float:
    """Fonction de répartition de la loi normale centrée réduite."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def norm_pdf(x: float) -> float:
    """Densité de la loi normale centrée réduite."""
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _intrinsic(spot: float, strike: float, tau: float, rate: float, dividend: float, kind: str) -> float:
    if kind == "call":
        return max(spot * math.exp(-dividend * tau) - strike * math.exp(-rate * tau), 0.0)
    return max(strike * math.exp(-rate * tau) - spot * math.exp(-dividend * tau), 0.0)


def bs_price(
    spot: float,
    strike: float,
    vol: float,
    tau: float,
    rate: float = 0.0,
    dividend: float = 0.0,
    kind: str = "call",
) -> float:
    """Prix d'une option européenne vanille en Black-Scholes.

    ``tau`` est exprimé en années, ``vol`` en volatilité annualisée (0,55 = 55 %).
    Le rendement de dividende est continu.
    """
    if spot <= 0 or strike <= 0:
        raise ValueError("spot et strike doivent être strictement positifs")
    if tau < 0:
        raise ValueError("tau doit être positif ou nul")
    if vol < 0:
        raise ValueError("vol doit être positive ou nulle")
    if kind not in {"call", "put"}:
        raise ValueError("kind doit valoir 'call' ou 'put'")

    if tau == 0:
        return max(spot - strike, 0.0) if kind == "call" else max(strike - spot, 0.0)

    discounted_spot = spot * math.exp(-dividend * tau)
    discounted_strike = strike * math.exp(-rate * tau)

    if vol == 0:
        return max(discounted_spot - discounted_strike, 0.0) if kind == "call" else max(
            discounted_strike - discounted_spot, 0.0
        )

    sqrt_tau = math.sqrt(tau)
    d1 = (math.log(spot / strike) + (rate - dividend + 0.5 * vol * vol) * tau) / (vol * sqrt_tau)
    d2 = d1 - vol * sqrt_tau

    if kind == "call":
        return discounted_spot * norm_cdf(d1) - discounted_strike * norm_cdf(d2)
    return discounted_strike * norm_cdf(-d2) - discounted_spot * norm_cdf(-d1)


def bs_greeks(
    spot: float,
    strike: float,
    vol: float,
    tau: float,
    rate: float = 0.0,
    dividend: float = 0.0,
    kind: str = "call",
) -> dict[str, float]:
    """Grecques Black-Scholes.

    ``vega`` est pour +1 point de volatilité, ``theta`` pour un jour calendaire.
    """
    if tau <= 0 or vol <= 0:
        itm = spot > strike if kind == "call" else spot < strike
        return {
            "delta": float(itm) if kind == "call" else -float(itm),
            "gamma": 0.0,
            "vega": 0.0,
            "theta": 0.0,
        }

    sqrt_tau = math.sqrt(tau)
    d1 = (math.log(spot / strike) + (rate - dividend + 0.5 * vol * vol) * tau) / (vol * sqrt_tau)
    d2 = d1 - vol * sqrt_tau
    disc_q = math.exp(-dividend * tau)
    disc_r = math.exp(-rate * tau)
    pdf_d1 = norm_pdf(d1)

    gamma = disc_q * pdf_d1 / (spot * vol * sqrt_tau)
    vega = spot * disc_q * pdf_d1 * sqrt_tau / 100.0

    if kind == "call":
        delta = disc_q * norm_cdf(d1)
        theta = (
            -spot * disc_q * pdf_d1 * vol / (2.0 * sqrt_tau)
            - rate * strike * disc_r * norm_cdf(d2)
            + dividend * spot * disc_q * norm_cdf(d1)
        ) / 365.0
    else:
        delta = -disc_q * norm_cdf(-d1)
        theta = (
            -spot * disc_q * pdf_d1 * vol / (2.0 * sqrt_tau)
            + rate * strike * disc_r * norm_cdf(-d2)
            - dividend * spot * disc_q * norm_cdf(-d1)
        ) / 365.0

    return {"delta": delta, "gamma": gamma, "vega": vega, "theta": theta}


def implied_vol(
    price: float,
    spot: float,
    strike: float,
    tau: float,
    rate: float = 0.0,
    dividend: float = 0.0,
    kind: str = "call",
    *,
    low: float = 1e-6,
    high: float = 5.0,
    tolerance: float = 1e-8,
    max_iterations: int = 120,
) -> float:
    """Volatilité implicite par bissection.

    Lève ``ValueError`` si le prix est hors des bornes d'arbitrage ou si la
    racine n'est pas encadrée. Le cas échéant, l'appelant doit ignorer l'option
    plutôt que d'inventer une volatilité.
    """
    if tau <= 0:
        raise ValueError("tau doit être strictement positif pour inverser la volatilité")
    if price <= 0:
        raise ValueError("prix non strictement positif")

    intrinsic = _intrinsic(spot, strike, tau, rate, dividend, kind)
    upper = spot * math.exp(-dividend * tau) if kind == "call" else strike * math.exp(-rate * tau)
    if price < intrinsic - 1e-7:
        raise ValueError(f"prix sous la borne d'arbitrage ({price:.6f} < {intrinsic:.6f})")
    if price >= upper:
        raise ValueError(f"prix au-dessus de la borne supérieure ({price:.6f} >= {upper:.6f})")

    def objective(vol: float) -> float:
        return bs_price(spot, strike, vol, tau, rate, dividend, kind) - price

    f_low = objective(low)
    f_high = objective(high)
    if f_low > 0:
        return low
    if f_high < 0:
        raise ValueError("volatilité implicite supérieure à la borne de recherche")

    for _ in range(max_iterations):
        mid = 0.5 * (low + high)
        f_mid = objective(mid)
        if abs(f_mid) < tolerance or (high - low) < tolerance:
            return mid
        if f_mid > 0:
            high = mid
        else:
            low = mid
    return 0.5 * (low + high)


def realized_volatility(
    prices: Sequence[float] | np.ndarray,
    *,
    window: int | None = None,
    annualization: float = TRADING_DAYS,
) -> float:
    """Volatilité réalisée annualisée à partir de clôtures.

    Les rendements sont des log-rendements. ``window`` désigne le nombre de
    rendements conservés (les plus récents).
    """
    values = np.asarray(prices, dtype=float)
    if values.ndim != 1 or len(values) < 2:
        raise ValueError("il faut au moins deux prix")
    if np.any(~np.isfinite(values)) or np.any(values <= 0):
        raise ValueError("les prix doivent être finis et strictement positifs")

    returns = np.diff(np.log(values))
    if window is not None:
        if window < 2:
            raise ValueError("window doit être >= 2")
        returns = returns[-window:]

    if len(returns) < 2:
        raise ValueError("pas assez de rendements pour estimer la volatilité")
    return float(np.std(returns, ddof=1) * math.sqrt(annualization))


def ewma_volatility(
    prices: Sequence[float] | np.ndarray,
    *,
    decay: float = 0.94,
    annualization: float = TRADING_DAYS,
) -> float:
    """Volatilité EWMA (RiskMetrics), annualisée."""
    values = np.asarray(prices, dtype=float)
    if len(values) < 3:
        raise ValueError("il faut au moins trois prix")
    if not 0.0 < decay < 1.0:
        raise ValueError("decay doit être dans ]0, 1[")

    returns = np.diff(np.log(values))
    seed_size = min(20, len(returns))
    variance = float(np.var(returns[:seed_size], ddof=1)) if seed_size > 1 else float(returns[0] ** 2)
    for value in returns:
        variance = decay * variance + (1.0 - decay) * float(value * value)
    return float(math.sqrt(max(variance, 0.0) * annualization))


def quantile_summary(values: Sequence[float] | np.ndarray) -> dict[str, float]:
    """Quelques quantiles utiles pour un rapport de distribution."""
    arr = np.asarray(values, dtype=float)
    probs = (0.01, 0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95, 0.99)
    quantiles = np.quantile(arr, probs)
    return {f"q{int(p * 100):02d}": float(q) for p, q in zip(probs, quantiles, strict=True)}
