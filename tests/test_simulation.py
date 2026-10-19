from __future__ import annotations

import numpy as np
import pytest

from sma92.simulation import SimulationConfig, run_simulation


def _config(**overrides: object) -> SimulationConfig:
    base = dict(
        spot=100.0,
        volatility=0.20,
        strike=100.0,
        days=365,
        rate=0.03,
        n_paths=120_000,
        n_steps=1,
        seed=123,
        model="gbm",
        antithetic=True,
    )
    base.update(overrides)
    return SimulationConfig(**base)  # type: ignore[arg-type]


def test_gbm_mc_converges_to_black_scholes() -> None:
    result = run_simulation(_config())
    call = next(price for price in result.option_prices if price.kind == "call")
    put = next(price for price in result.option_prices if price.kind == "put")
    assert abs(call.difference_vs_bs) < 0.06
    assert abs(put.difference_vs_bs) < 0.06
    assert call.ci_low < call.bs_price < call.ci_high
    assert put.ci_low < put.bs_price < put.ci_high


def test_same_seed_is_reproducible() -> None:
    first = run_simulation(_config(n_paths=10_000))
    second = run_simulation(_config(n_paths=10_000))
    assert np.array_equal(first.terminal, second.terminal)


def test_multiple_strikes() -> None:
    result = run_simulation(_config(strikes=(90.0, 110.0)))
    assert len(result.option_prices) == 6
    assert {price.strike for price in result.option_prices} == {90.0, 100.0, 110.0}


def test_bootstrap_model_runs() -> None:
    rng = np.random.default_rng(3)
    historical = tuple(float(x) for x in rng.normal(0.0, 0.015, 250))
    result = run_simulation(_config(model="bootstrap", historical_returns=historical, n_paths=20_000))
    assert len(result.option_prices) == 2
    assert np.isfinite(result.terminal).all()
    assert result.terminal.min() > 0


def test_student_t_model_runs() -> None:
    result = run_simulation(_config(model="student-t", student_t_df=5.0, n_paths=20_000))
    assert len(result.option_prices) == 2
    assert result.terminal_summary["excess_kurtosis"] > 0


def test_antithetic_requires_even_paths() -> None:
    with pytest.raises(ValueError, match="pair"):
        run_simulation(_config(n_paths=1001))


def test_bootstrap_requires_history() -> None:
    with pytest.raises(ValueError, match="bootstrap"):
        run_simulation(_config(model="bootstrap"))
