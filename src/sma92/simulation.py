"""Moteur Monte Carlo pour l'action sous-jacente et les options vanilles."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal

import numpy as np

from .black_scholes import TRADING_DAYS, bs_greeks, bs_price, quantile_summary

ModelName = Literal["gbm", "bootstrap", "student-t"]


@dataclass(frozen=True)
class SimulationConfig:
    """Paramètres complets d'une simulation.

    ``days`` est un horizon calendaire. Les pas de simulation sont des pas de
    trading (par défaut 252 par an), mais l'actualisation reste en temps
    calendaire ACT/365.
    """

    spot: float
    volatility: float
    strike: float
    days: int = 30
    rate: float = 0.02
    dividend: float = 0.0
    n_paths: int = 200_000
    n_steps: int | None = None
    seed: int = 42
    model: ModelName = "gbm"
    antithetic: bool = True
    student_t_df: float = 5.0
    historical_returns: tuple[float, ...] = ()
    contract_size: float = 100.0
    strikes: tuple[float, ...] = ()

    @property
    def tau(self) -> float:
        return self.days / 365.0

    @property
    def all_strikes(self) -> tuple[float, ...]:
        ordered: list[float] = []
        for value in (self.strike, *self.strikes):
            if value not in ordered:
                ordered.append(value)
        return tuple(ordered)

    @property
    def steps(self) -> int:
        if self.n_steps is not None:
            return self.n_steps
        return max(1, round(self.days * TRADING_DAYS / 365.0))

    def validate(self) -> None:
        if self.spot <= 0:
            raise ValueError("spot doit être strictement positif")
        if any(value <= 0 for value in self.all_strikes):
            raise ValueError("tous les strikes doivent être strictement positifs")
        if self.volatility <= 0:
            raise ValueError("volatility doit être strictement positive")
        if self.days <= 0:
            raise ValueError("days doit être strictement positif")
        if self.n_paths < 2:
            raise ValueError("n_paths doit être >= 2")
        if self.antithetic and self.n_paths % 2:
            raise ValueError("n_paths doit être pair lorsque les variables antithétiques sont activées")
        if self.steps < 1:
            raise ValueError("n_steps doit être >= 1")
        if self.model == "bootstrap" and len(self.historical_returns) < 10:
            raise ValueError("le modèle bootstrap exige au moins 10 rendements historiques")
        if self.model == "student-t" and self.student_t_df <= 2:
            raise ValueError("student_t_df doit être > 2 pour une variance finie")
        if self.contract_size <= 0:
            raise ValueError("contract_size doit être strictement positif")


@dataclass(frozen=True)
class PriceStats:
    mean: float
    standard_error: float
    ci_low: float
    ci_high: float
    probability_itm: float


@dataclass(frozen=True)
class OptionPrice:
    strike: float
    kind: str
    mc_price: float
    standard_error: float
    ci_low: float
    ci_high: float
    bs_price: float
    difference_vs_bs: float
    probability_itm: float
    contract_value: float
    delta_bs: float
    gamma_bs: float
    vega_bs: float
    theta_bs: float


@dataclass
class SimulationResult:
    config: SimulationConfig
    terminal: np.ndarray
    terminal_summary: dict[str, float] = field(default_factory=dict)
    quantiles: dict[str, float] = field(default_factory=dict)
    option_prices: list[OptionPrice] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _unit_variance_shocks(
    rng: np.random.Generator,
    *,
    n_paths: int,
    n_steps: int,
    antithetic: bool,
    model: ModelName,
    student_t_df: float,
    historical_returns: tuple[float, ...],
) -> np.ndarray:
    """Génère des chocs standardisés, éventuellement antithétiques."""
    if antithetic:
        half = n_paths // 2
        if model == "bootstrap":
            history = np.asarray(historical_returns, dtype=float)
            std = float(np.std(history, ddof=1))
            if std <= 0:
                raise ValueError("les rendements historiques sont constants")
            residuals = (history - float(np.mean(history))) / std
            sampled = rng.choice(residuals, size=(half, n_steps), replace=True)
            return np.concatenate([sampled, -sampled], axis=0)
        if model == "student-t":
            raw = rng.standard_t(student_t_df, size=(half, n_steps))
            raw /= math.sqrt(student_t_df / (student_t_df - 2.0))
            return np.concatenate([raw, -raw], axis=0)
        normal = rng.standard_normal((half, n_steps))
        return np.concatenate([normal, -normal], axis=0)

    if model == "bootstrap":
        history = np.asarray(historical_returns, dtype=float)
        std = float(np.std(history, ddof=1))
        if std <= 0:
            raise ValueError("les rendements historiques sont constants")
        residuals = (history - float(np.mean(history))) / std
        return rng.choice(residuals, size=(n_paths, n_steps), replace=True)
    if model == "student-t":
        raw = rng.standard_t(student_t_df, size=(n_paths, n_steps))
        return raw / math.sqrt(student_t_df / (student_t_df - 2.0))
    return rng.standard_normal((n_paths, n_steps))


def simulate_growth(config: SimulationConfig, *, chunk_size: int = 100_000) -> np.ndarray:
    """Simule le facteur multiplicatif terminal ``S_T / S_0``.

    Le calcul est fait par blocs pour éviter d'allouer une matrice
    ``n_paths × n_steps`` complète lorsque le nombre de trajectoires est élevé.
    """
    config.validate()
    if chunk_size < 2:
        raise ValueError("chunk_size doit être >= 2")
    if config.antithetic and chunk_size % 2:
        chunk_size += 1

    rng = np.random.default_rng(config.seed)
    dt = config.tau / config.steps
    drift = (config.rate - config.dividend - 0.5 * config.volatility**2) * dt
    diffusion = config.volatility * math.sqrt(dt)

    chunks: list[np.ndarray] = []
    remaining = config.n_paths
    while remaining > 0:
        size = min(chunk_size, remaining)
        if config.antithetic and size % 2:
            size -= 1
        if size <= 0:
            break
        shocks = _unit_variance_shocks(
            rng,
            n_paths=size,
            n_steps=config.steps,
            antithetic=config.antithetic,
            model=config.model,
            student_t_df=config.student_t_df,
            historical_returns=config.historical_returns,
        )
        log_growth = drift * config.steps + diffusion * shocks.sum(axis=1)
        chunks.append(np.exp(log_growth))
        remaining -= size

    growth = np.concatenate(chunks)
    if len(growth) != config.n_paths:
        raise RuntimeError(f"nombre de trajectoires inattendu : {len(growth)} au lieu de {config.n_paths}")
    if np.any(~np.isfinite(growth)):
        raise FloatingPointError("trajectoires non finies : réduire l'horizon ou la volatilité")
    return growth


def _mean_statistics(values: np.ndarray) -> tuple[float, float, float, float]:
    """Moyenne, erreur-type conservatrice et IC 95 %."""
    n = len(values)
    mean = float(np.mean(values))
    se_iid = float(np.std(values, ddof=1) / math.sqrt(n)) if n > 1 else 0.0

    n_batches = min(64, max(8, n // 1_000))
    batch_size = n // n_batches
    if batch_size >= 2:
        usable = values[: n_batches * batch_size].reshape(n_batches, batch_size)
        batch_means = usable.mean(axis=1)
        se_batch = float(np.std(batch_means, ddof=1) / math.sqrt(n_batches))
    else:
        se_batch = se_iid

    se = max(se_iid, se_batch)
    return mean, se, mean - 1.96 * se, mean + 1.96 * se


def _terminal_summary(terminal: np.ndarray) -> dict[str, float]:
    n = len(terminal)
    mean = float(np.mean(terminal))
    std = float(np.std(terminal, ddof=1))
    standardized = (terminal - mean) / std if std > 0 else np.zeros_like(terminal)
    return {
        "mean": mean,
        "std": std,
        "skewness": float(np.mean(standardized**3)) if n > 2 else 0.0,
        "excess_kurtosis": float(np.mean(standardized**4) - 3.0) if n > 3 else 0.0,
        "min": float(np.min(terminal)),
        "max": float(np.max(terminal)),
    }


def run_simulation(config: SimulationConfig) -> SimulationResult:
    """Exécute la simulation et price calls et puts pour chaque strike."""
    config.validate()
    growth = simulate_growth(config)
    terminal = config.spot * growth

    result = SimulationResult(config=config, terminal=terminal)
    result.quantiles = quantile_summary(terminal)
    result.terminal_summary = _terminal_summary(terminal)
    result.terminal_summary["probability_below_spot"] = float(np.mean(terminal < config.spot))
    result.terminal_summary["expected_return"] = float(np.mean(terminal) / config.spot - 1.0)

    discount = math.exp(-config.rate * config.tau)
    for strike in config.all_strikes:
        for kind in ("call", "put"):
            payoff = np.maximum(terminal - strike, 0.0) if kind == "call" else np.maximum(strike - terminal, 0.0)
            discounted = discount * payoff
            mean, se, ci_low, ci_high = _mean_statistics(discounted)
            bs = bs_price(
                config.spot,
                strike,
                config.volatility,
                config.tau,
                config.rate,
                config.dividend,
                kind,
            )
            greeks = bs_greeks(
                config.spot,
                strike,
                config.volatility,
                config.tau,
                config.rate,
                config.dividend,
                kind,
            )
            probability_itm = float(np.mean(terminal > strike)) if kind == "call" else float(np.mean(terminal < strike))
            result.option_prices.append(
                OptionPrice(
                    strike=strike,
                    kind=kind,
                    mc_price=mean,
                    standard_error=se,
                    ci_low=ci_low,
                    ci_high=ci_high,
                    bs_price=bs,
                    difference_vs_bs=mean - bs,
                    probability_itm=probability_itm,
                    contract_value=mean * config.contract_size,
                    delta_bs=greeks["delta"],
                    gamma_bs=greeks["gamma"],
                    vega_bs=greeks["vega"],
                    theta_bs=greeks["theta"],
                )
            )

    if config.n_paths < 50_000:
        result.warnings.append(
            "Moins de 50 000 trajectoires : l'erreur Monte Carlo peut être matérielle, surtout sur les ailes."
        )
    if config.model == "bootstrap":
        result.warnings.append(
            "Le bootstrap empirique conserve skewness et queues épaisses, mais reste un modèle historique, "
            "pas une loi risque-neutre calibrée sur le marché."
        )
    if config.volatility > 1.0:
        result.warnings.append("Volatilité supérieure à 100 % : vérifier l'unité et la fenêtre d'estimation.")
    return result
