from __future__ import annotations

import math

import numpy as np
import pytest

from sma92.black_scholes import (
    bs_greeks,
    bs_price,
    ewma_volatility,
    implied_vol,
    realized_volatility,
)


def test_black_scholes_reference_value() -> None:
    price = bs_price(100.0, 100.0, 0.20, 1.0, 0.05, 0.0, "call")
    assert price == pytest.approx(10.4506, abs=1e-4)


def test_put_call_parity() -> None:
    spot, strike, vol, tau, rate, dividend = 87.3, 90.0, 0.31, 0.7, 0.03, 0.012
    call = bs_price(spot, strike, vol, tau, rate, dividend, "call")
    put = bs_price(spot, strike, vol, tau, rate, dividend, "put")
    assert call - put == pytest.approx(
        spot * math.exp(-dividend * tau) - strike * math.exp(-rate * tau), abs=1e-10
    )


def test_implied_vol_roundtrip() -> None:
    spot, strike, vol, tau, rate, dividend = 55.7, 60.0, 0.53, 32 / 365, 0.02, 0.0
    for kind in ("call", "put"):
        price = bs_price(spot, strike, vol, tau, rate, dividend, kind)
        recovered = implied_vol(price, spot, strike, tau, rate, dividend, kind)
        assert recovered == pytest.approx(vol, abs=1e-7)


def test_implied_vol_rejects_arbitrage_violation() -> None:
    with pytest.raises(ValueError):
        implied_vol(0.001, 100.0, 80.0, 0.5, 0.02, 0.0, "call")


def test_greeks_are_consistent() -> None:
    call = bs_greeks(100.0, 100.0, 0.2, 1.0, 0.03, 0.0, "call")
    put = bs_greeks(100.0, 100.0, 0.2, 1.0, 0.03, 0.0, "put")
    assert 0 < call["delta"] < 1
    assert -1 < put["delta"] < 0
    assert call["gamma"] == pytest.approx(put["gamma"])
    assert call["vega"] == pytest.approx(put["vega"])
    assert call["theta"] < 0 and put["theta"] < 0


def test_realized_and_ewma_volatility() -> None:
    rng = np.random.default_rng(7)
    returns = rng.normal(0.0002, 0.012, 500)
    closes = 100 * np.exp(np.cumsum(returns))
    rv = realized_volatility(closes, window=252)
    ewma = ewma_volatility(closes)
    assert 0.15 < rv < 0.25
    assert 0.15 < ewma < 0.25
