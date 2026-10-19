"""Analyse de la chaîne d'options : IV, structure par terme, skew et signaux."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from statistics import median
from typing import Any

import numpy as np

from .black_scholes import implied_vol, realized_volatility
from .saxo import MarketSnapshot, OptionContract, PriceBar


@dataclass(frozen=True)
class OptionMetric:
    contract: OptionContract
    dte: int
    tau: float
    mid: float | None
    spread: float | None
    spread_pct: float | None
    intrinsic: float | None
    time_value: float | None
    moneyness: float
    iv: float | None
    iv_start: float | None
    iv_change: float | None
    iv_start_day: date | None
    premium_per_contract: float | None
    breakeven: float | None
    quote_quality: str
    flags: tuple[str, ...]
    history_points: int
    history_volume: float | None

    @property
    def liquid(self) -> bool:
        return (
            self.quote_quality == "two-sided"
            and self.spread_pct is not None
            and self.spread_pct <= 0.15
            and (self.contract.open_interest or 0) >= 10
        )


@dataclass(frozen=True)
class ExpirySummary:
    expiry: date
    dte: int
    atm_strike: float
    atm_iv: float | None
    atm_call_iv: float | None
    atm_put_iv: float | None
    atm_straddle: float | None
    rv20: float | None
    vrp: float | None
    median_spread_pct: float | None
    open_interest: float
    volume: float
    contracts: int


@dataclass(frozen=True)
class Signal:
    scope: str
    name: str
    level: str
    value: float | None
    unit: str
    interpretation: str
    caveat: str


@dataclass
class AnalysisResult:
    snapshot: MarketSnapshot
    metrics: tuple[OptionMetric, ...]
    expiry_summaries: tuple[ExpirySummary, ...]
    signals: tuple[Signal, ...]
    rv7: float | None
    rv20: float | None
    rv60: float | None
    ewma: float | None
    term_slope: float | None
    skew_25d: float | None
    warnings: list[str] = field(default_factory=list)

    def metric_rows(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for metric in self.metrics:
            contract = metric.contract
            rows.append(
                {
                    "uic": contract.uic,
                    "expiry": contract.expiry.isoformat(),
                    "dte": metric.dte,
                    "kind": contract.kind,
                    "strike": contract.strike,
                    "moneyness": metric.moneyness,
                    "bid": contract.bid,
                    "ask": contract.ask,
                    "mid": metric.mid,
                    "spread": metric.spread,
                    "spread_pct": metric.spread_pct,
                    "last": contract.last,
                    "volume": contract.volume,
                    "open_interest": contract.open_interest,
                    "iv": metric.iv,
                    "iv_start": metric.iv_start,
                    "iv_change": metric.iv_change,
                    "iv_start_day": metric.iv_start_day.isoformat() if metric.iv_start_day else None,
                    "intrinsic": metric.intrinsic,
                    "time_value": metric.time_value,
                    "premium_per_contract": metric.premium_per_contract,
                    "breakeven": metric.breakeven,
                    "delta": contract.delta,
                    "gamma": contract.gamma,
                    "vega": contract.vega,
                    "theta": contract.theta,
                    "quote_quality": metric.quote_quality,
                    "history_points": metric.history_points,
                    "history_volume": metric.history_volume,
                    "flags": ",".join(metric.flags),
                }
            )
        return rows

    def expiry_rows(self) -> list[dict[str, Any]]:
        return [
            {
                "expiry": item.expiry.isoformat(),
                "dte": item.dte,
                "atm_strike": item.atm_strike,
                "atm_iv": item.atm_iv,
                "atm_call_iv": item.atm_call_iv,
                "atm_put_iv": item.atm_put_iv,
                "atm_straddle": item.atm_straddle,
                "rv20": item.rv20,
                "vrp": item.vrp,
                "median_spread_pct": item.median_spread_pct,
                "open_interest": item.open_interest,
                "volume": item.volume,
                "contracts": item.contracts,
            }
            for item in self.expiry_summaries
        ]

    def signal_rows(self) -> list[dict[str, Any]]:
        return [
            {
                "scope": signal.scope,
                "signal": signal.name,
                "level": signal.level,
                "value": signal.value,
                "unit": signal.unit,
                "interpretation": signal.interpretation,
                "caveat": signal.caveat,
            }
            for signal in self.signals
        ]


def _normalize_delta(value: float | None) -> float | None:
    if value is None:
        return None
    return value / 100.0 if abs(value) > 1.5 else value


def _underlying_close_at(bars: tuple[PriceBar, ...], day: date) -> float | None:
    eligible = [bar for bar in bars if bar.day <= day]
    return eligible[-1].close if eligible else (bars[0].close if bars else None)


def _metric_for_contract(
    contract: OptionContract,
    *,
    spot: float,
    as_of: date,
    rate: float,
    dividend: float,
    underlying_bars: tuple[PriceBar, ...],
    history: tuple[PriceBar, ...],
    rv20: float | None,
) -> OptionMetric:
    dte = max((contract.expiry - as_of).days, 0)
    tau = max(dte, 1) / 365.0
    mid = contract.mid if contract.mid is not None and contract.mid > 0 else contract.last
    two_sided = (
        contract.bid is not None
        and contract.ask is not None
        and contract.bid > 0
        and contract.ask >= contract.bid
    )
    spread = (contract.ask - contract.bid) if two_sided else None
    spread_pct = spread / mid if spread is not None and mid and mid > 0 else None

    intrinsic = (
        max(spot - contract.strike, 0.0) if contract.kind == "call" else max(contract.strike - spot, 0.0)
    )
    time_value = mid - intrinsic if mid is not None else None

    iv: float | None = None
    iv_error: str | None = None
    if mid is not None and mid > 0:
        try:
            iv = implied_vol(mid, spot, contract.strike, tau, rate, dividend, contract.kind)
        except ValueError as exc:
            iv_error = str(exc)

    iv_start: float | None = None
    iv_start_day: date | None = None
    if history and mid is not None:
        target = as_of - timedelta(days=7)
        bar = min(history, key=lambda item: abs((item.day - target).days))
        iv_start_day = bar.day
        hist_spot = _underlying_close_at(underlying_bars, bar.day)
        hist_tau = max((contract.expiry - bar.day).days, 1) / 365.0
        if hist_spot and hist_spot > 0:
            try:
                iv_start = implied_vol(
                    bar.close,
                    hist_spot,
                    contract.strike,
                    hist_tau,
                    rate,
                    dividend,
                    contract.kind,
                )
            except ValueError:
                iv_start = None

    if two_sided:
        quote_quality = "two-sided"
    elif contract.bid is not None or contract.ask is not None:
        quote_quality = "one-sided"
    elif contract.last is not None:
        quote_quality = "last-only"
    else:
        quote_quality = "none"

    flags: list[str] = []
    if mid is None:
        flags.append("NO_QUOTE")
    elif not two_sided:
        flags.append("ONE_SIDED")
    if spread_pct is not None and spread_pct > 0.15:
        flags.append("WIDE_SPREAD")
    if contract.open_interest is not None and contract.open_interest < 10:
        flags.append("LOW_OI")
    if contract.volume is not None and contract.volume == 0:
        flags.append("NO_VOLUME")
    if iv is None:
        flags.append("IV_UNAVAILABLE")
    if iv is not None and iv > 3.0:
        flags.append("IV_OUTLIER")
    if iv is not None and rv20 is not None:
        if iv - rv20 > 0.15:
            flags.append("IV_RICH")
        elif iv - rv20 < -0.10:
            flags.append("IV_CHEAP")
    if abs(contract.strike / spot - 1.0) <= 0.03:
        flags.append("ATM")
    if iv_error:
        flags.append("IV_BOUND")

    history_volume = (
        float(sum(bar.volume for bar in history if bar.volume is not None)) if history else None
    )
    breakeven = contract.strike + mid if contract.kind == "call" and mid is not None else (
        contract.strike - mid if contract.kind == "put" and mid is not None else None
    )

    return OptionMetric(
        contract=contract,
        dte=dte,
        tau=tau,
        mid=mid,
        spread=spread,
        spread_pct=spread_pct,
        intrinsic=intrinsic,
        time_value=time_value,
        moneyness=contract.strike / spot,
        iv=iv,
        iv_start=iv_start,
        iv_change=(iv - iv_start) if iv is not None and iv_start is not None else None,
        iv_start_day=iv_start_day,
        premium_per_contract=mid * contract.contract_size if mid is not None else None,
        breakeven=breakeven,
        quote_quality=quote_quality,
        flags=tuple(flags),
        history_points=len(history),
        history_volume=history_volume,
    )


def _expiry_summaries(
    metrics: list[OptionMetric],
    *,
    spot: float,
    rv20: float | None,
    as_of: date,
) -> tuple[ExpirySummary, ...]:
    grouped: dict[date, list[OptionMetric]] = {}
    for metric in metrics:
        grouped.setdefault(metric.contract.expiry, []).append(metric)

    summaries: list[ExpirySummary] = []
    for expiry in sorted(grouped):
        group = grouped[expiry]
        atm = min(group, key=lambda item: abs(item.contract.strike - spot))
        at_strike = [item for item in group if item.contract.strike == atm.contract.strike]
        call = next((item for item in at_strike if item.contract.kind == "call" and item.iv is not None), None)
        put = next((item for item in at_strike if item.contract.kind == "put" and item.iv is not None), None)
        ivs = [item.iv for item in (call, put) if item.iv is not None]
        atm_iv = float(np.mean(ivs)) if ivs else None
        straddle_mids = [item.mid for item in at_strike if item.mid is not None]
        spreads = [item.spread_pct for item in group if item.spread_pct is not None]
        summaries.append(
            ExpirySummary(
                expiry=expiry,
                dte=max((expiry - as_of).days, 0),
                atm_strike=atm.contract.strike,
                atm_iv=atm_iv,
                atm_call_iv=call.iv if call else None,
                atm_put_iv=put.iv if put else None,
                atm_straddle=float(sum(straddle_mids)) if straddle_mids else None,
                rv20=rv20,
                vrp=(atm_iv - rv20) if atm_iv is not None and rv20 is not None else None,
                median_spread_pct=float(median(spreads)) if spreads else None,
                open_interest=float(sum(item.contract.open_interest or 0 for item in group)),
                volume=float(sum(item.contract.volume or 0 for item in group)),
                contracts=len(group),
            )
        )
    return tuple(summaries)


def _find_by_delta(group: list[OptionMetric], target_abs: float, kind: str) -> OptionMetric | None:
    candidates = [
        metric
        for metric in group
        if metric.contract.kind == kind
        and metric.iv is not None
        and _normalize_delta(metric.contract.delta) is not None
    ]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda item: abs(abs(_normalize_delta(item.contract.delta) or 0.0) - target_abs),
    )


def _signals(
    expiries: tuple[ExpirySummary, ...],
    *,
    skew_25d: float | None,
    term_slope: float | None,
    median_spread: float | None,
    two_sided_ratio: float,
) -> tuple[Signal, ...]:
    signals: list[Signal] = []
    for item in expiries:
        if item.vrp is None:
            continue
        if item.vrp > 0.08:
            level, text = "watch", "IV ATM nettement au-dessus de la vol réalisée 20 j."
            caveat = "Une prime peut être justifiée par un événement (résultats, guidance, tarifs)."
        elif item.vrp < -0.05:
            level, text = "watch", "IV ATM sous la vol réalisée 20 j : options relativement bon marché."
            caveat = "Une IV basse peut refléter une anticipation de baisse de volatilité, pas une erreur de prix."
        else:
            level, text = "info", "Écart IV / vol réalisée dans une zone neutre."
            caveat = "Seuil heuristique, à recalculer avec votre propre historique."
        signals.append(
            Signal(
                scope=item.expiry.isoformat(),
                name="Prime de variance (IV ATM - RV20)",
                level=level,
                value=item.vrp,
                unit="pct",
                interpretation=text,
                caveat=caveat,
            )
        )

    if term_slope is not None:
        if term_slope > 0.03:
            level, text = "watch", "Courbe des IV inversée : le front est plus cher que le back."
            caveat = "Souvent lié à un événement daté ; vérifier le calendrier et la liquidité."
        elif term_slope < -0.03:
            level, text = "info", "Courbe des IV en contango (front moins cher que le back)."
            caveat = "Configuration normale, mais peut signaler un stress de liquidité sur le front."
        else:
            level, text = "info", "Structure par terme plate."
            caveat = "Pas de signal directionnel en soi."
        signals.append(
            Signal(
                scope="term-structure",
                name="Pente front - back",
                level=level,
                value=term_slope,
                unit="pct",
                interpretation=text,
                caveat=caveat,
            )
        )

    if skew_25d is not None:
        if skew_25d > 0.10:
            level, text = "watch", "Skew baissier riche : les puts 25 delta sont chers face aux calls."
            caveat = "Un skew élevé peut être un régime persistant ; ne pas vendre des tails sans couverture."
        elif skew_25d < 0.02:
            level, text = "watch", "Skew 25 delta faible : protection baissière relativement peu chère."
            caveat = "Vérifier la profondeur et le spread des puts avant toute conclusion."
        else:
            level, text = "info", "Skew 25 delta dans une zone habituelle."
            caveat = "Le skew dépend fortement de l'échéance et de la méthode d'interpolation."
        signals.append(
            Signal(
                scope="skew",
                name="Skew 25 delta (put - call)",
                level=level,
                value=skew_25d,
                unit="pct",
                interpretation=text,
                caveat=caveat,
            )
        )

    if median_spread is not None:
        if median_spread > 0.15 or two_sided_ratio < 0.5:
            level, text = "alert", "Liquidité faible : spreads larges ou nombreuses quotes non bipaires."
            caveat = "Un backtest sur des mids illiquides peut surestimer fortement la performance."
        else:
            level, text = "info", "Liquidité apparente correcte sur l'échantillon."
            caveat = "La liquidité affichée ne garantit pas une exécution à la taille souhaitée."
        signals.append(
            Signal(
                scope="liquidity",
                name="Spread médian",
                level=level,
                value=median_spread,
                unit="pct",
                interpretation=text,
                caveat=caveat,
            )
        )
    return tuple(signals)


def run_analysis(
    snapshot: MarketSnapshot,
    *,
    rate: float = 0.02,
    dividend: float = 0.0,
    as_of: datetime | None = None,
) -> AnalysisResult:
    """Calcule IV, indicateurs de chaîne et écrans heuristiques."""
    moment = as_of or snapshot.underlying.as_of or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    today = moment.astimezone(timezone.utc).date()
    spot = snapshot.underlying.spot
    if spot <= 0:
        raise ValueError("spot sous-jacent invalide")

    closes = np.asarray([bar.close for bar in snapshot.underlying_history], dtype=float)
    rv7 = realized_volatility(closes, window=7) if len(closes) >= 8 else None
    rv20 = realized_volatility(closes, window=20) if len(closes) >= 21 else None
    rv60 = realized_volatility(closes, window=60) if len(closes) >= 61 else None
    ewma = None
    if len(closes) >= 21:
        from .black_scholes import ewma_volatility

        ewma = ewma_volatility(closes)

    metrics = [
        _metric_for_contract(
            contract,
            spot=spot,
            as_of=today,
            rate=rate,
            dividend=dividend,
            underlying_bars=snapshot.underlying_history,
            history=snapshot.option_history.get(contract.uic, ()),
            rv20=rv20,
        )
        for contract in snapshot.contracts
    ]
    metrics.sort(key=lambda item: (item.contract.expiry, item.contract.strike, item.contract.kind))

    expiries = _expiry_summaries(metrics, spot=spot, rv20=rv20, as_of=today)

    term_slope = None
    if len(expiries) >= 2 and expiries[0].atm_iv is not None and expiries[-1].atm_iv is not None:
        term_slope = expiries[0].atm_iv - expiries[-1].atm_iv

    skew_25d = None
    if expiries:
        front = expiries[0].expiry
        group = [metric for metric in metrics if metric.contract.expiry == front]
        put_25 = _find_by_delta(group, 0.25, "put")
        call_25 = _find_by_delta(group, 0.25, "call")
        if put_25 and call_25 and put_25.iv is not None and call_25.iv is not None:
            skew_25d = put_25.iv - call_25.iv

    spreads = [metric.spread_pct for metric in metrics if metric.spread_pct is not None]
    median_spread = float(median(spreads)) if spreads else None
    two_sided = sum(1 for metric in metrics if metric.quote_quality == "two-sided")
    two_sided_ratio = two_sided / len(metrics) if metrics else 0.0
    signals = _signals(
        expiries,
        skew_25d=skew_25d,
        term_slope=term_slope,
        median_spread=median_spread,
        two_sided_ratio=two_sided_ratio,
    )

    warnings = list(snapshot.warnings)
    if snapshot.synthetic:
        warnings.append("DONNÉES SYNTHÉTIQUES : ce run ne reflète pas le marché réel.")
    if snapshot.environment == "sim":
        warnings.append(
            "Environnement Saxo SIM : les prix, volumes et grecques peuvent être simulés, retardés ou incomplets. "
            "Ils ne constituent pas une source de market data historique fiable."
        )
    if two_sided_ratio < 0.5:
        warnings.append(
            f"Seulement {two_sided}/{len(metrics)} options ont une quote bid/ask complète. "
            "Les IV calculées sur des mids théoriques sont fragiles."
        )
    if not snapshot.option_history:
        warnings.append("Aucun historique d'option n'a été récupéré : les variations d'IV 7 j sont indisponibles.")

    return AnalysisResult(
        snapshot=snapshot,
        metrics=tuple(metrics),
        expiry_summaries=expiries,
        signals=signals,
        rv7=rv7,
        rv20=rv20,
        rv60=rv60,
        ewma=ewma,
        term_slope=term_slope,
        skew_25d=skew_25d,
        warnings=warnings,
    )
