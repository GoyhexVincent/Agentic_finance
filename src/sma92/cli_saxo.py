"""Point d'entrée Saxo : snapshot de la chaîne d'options + historique 7 jours."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from .analysis import AnalysisResult, run_analysis
from .fixture import load_fixture
from .saxo import (
    SaxoAPIError,
    SaxoAuthError,
    SaxoClient,
    SaxoError,
    fetch_snapshot,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FIXTURE = PROJECT_ROOT / "data" / "fixtures" / "saxo_sim_snapshot.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "output" / "saxo_options"


def _fr(value: float | None, digits: int = 2) -> str:
    if value is None or not math.isfinite(value):
        return "—"
    return f"{value:,.{digits}f}".replace(",", "\u202f").replace(".", ",")


def _pct(value: float | None, digits: int = 1, signed: bool = False) -> str:
    if value is None:
        return "—"
    sign = "+" if signed and value > 0 else ""
    return f"{sign}{_fr(value * 100, digits)} %"


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _read_token(token_file: Path | None) -> str | None:
    if token_file is not None:
        if not token_file.exists():
            raise SaxoAuthError(f"fichier de jeton introuvable : {token_file}")
        return token_file.read_text(encoding="utf-8").strip()
    return os.getenv("SAXO_ACCESS_TOKEN")


def _as_of(raw: str | None) -> datetime:
    if not raw:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Récupère la chaîne d'options SMA92 sur Saxo et son historique 7 jours.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--symbol", default="SMA92", help="Libellé de travail / symbole recherché")
    parser.add_argument("--keywords", help="Recherche Saxo ; par défaut le symbole puis 'SMA Solar'")
    parser.add_argument("--uic", type=int, help="UIC du sous-jacent (bypass de la recherche)")
    parser.add_argument("--exchange", help="Filtrer la place, par exemple XETR")
    parser.add_argument("--option-root-id", type=int, help="Forcer l'option root Saxo")
    parser.add_argument("--environment", choices=("sim", "live"), help="Défaut : SAXO_ENV ou sim")
    parser.add_argument("--token-file", type=Path, help="Fichier contenant le jeton (évite l'historique shell)")
    parser.add_argument("--env-file", type=Path, default=PROJECT_ROOT / ".env", help="Fichier .env optionnel")
    parser.add_argument("--as-of", help="Date/heure ISO de référence ; par défaut maintenant")
    parser.add_argument("--days", type=int, default=7, help="Fenêtre d'historique en jours calendaires")
    parser.add_argument("--rate", type=float, default=0.02, help="Taux sans risque pour l'IV")
    parser.add_argument("--dividend", type=float, default=0.0, help="Rendement de dividende continu")
    parser.add_argument("--max-expiries", type=int, default=3, help="Nombre d'échéances retenues")
    parser.add_argument("--max-days", type=int, default=120, help="Échéance maximale en jours")
    parser.add_argument("--strike-window", type=float, default=0.40, help="Fenêtre de strikes autour du spot")
    parser.add_argument("--max-contracts", type=int, default=80, help="Nombre maximal de contrats")
    parser.add_argument(
        "--history-scope",
        choices=("focus", "all", "none"),
        default="focus",
        help="Contrats pour lesquels récupérer l'historique 7 j",
    )
    parser.add_argument("--history-window", type=float, default=0.10, help="Fenêtre ATM pour le scope focus")
    parser.add_argument("--max-history-contracts", type=int, default=24, help="Plafond d'appels chart")
    parser.add_argument("--timeout", type=float, default=30.0, help="Timeout HTTP en secondes")
    parser.add_argument("--max-retries", type=int, default=4, help="Retries sur 429/5xx")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--dry-run", action="store_true", help="Utiliser une fixture locale, sans token")
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE, help="Fixture du mode dry-run")
    parser.add_argument("--quiet", action="store_true", help="Ne pas afficher le résumé")
    return parser


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _underlying_rows(analysis: AnalysisResult, as_of: datetime) -> list[dict[str, Any]]:
    bars = analysis.snapshot.underlying_history
    start = as_of.date() - timedelta(days=7)
    previous: float | None = None
    rows: list[dict[str, Any]] = []
    for bar in bars:
        log_return = math.log(bar.close / previous) if previous and previous > 0 else None
        if bar.day >= start:
            rows.append(
                {
                    "date": bar.day.isoformat(),
                    "open": bar.open,
                    "high": bar.high,
                    "low": bar.low,
                    "close": bar.close,
                    "volume": bar.volume,
                    "log_return": log_return,
                }
            )
        previous = bar.close
    return rows


def _option_history_rows(
    analysis: AnalysisResult,
    *,
    rate: float,
    dividend: float,
) -> list[dict[str, Any]]:
    from .black_scholes import implied_vol

    snapshot = analysis.snapshot
    underlying = snapshot.underlying_history
    by_uic = {metric.contract.uic: metric for metric in analysis.metrics}
    rows: list[dict[str, Any]] = []
    for uic, bars in sorted(snapshot.option_history.items()):
        metric = by_uic.get(uic)
        if metric is None:
            continue
        for bar in bars:
            hist_spot = next((item.close for item in reversed(underlying) if item.day <= bar.day), None)
            iv = None
            if hist_spot and hist_spot > 0:
                tau = max((metric.contract.expiry - bar.day).days, 1) / 365.0
                try:
                    iv = implied_vol(
                        bar.close,
                        hist_spot,
                        metric.contract.strike,
                        tau,
                        rate,
                        dividend,
                        metric.contract.kind,
                    )
                except ValueError:
                    iv = None
            rows.append(
                {
                    "uic": uic,
                    "expiry": metric.contract.expiry.isoformat(),
                    "strike": metric.contract.strike,
                    "kind": metric.contract.kind,
                    "date": bar.day.isoformat(),
                    "open": bar.open,
                    "high": bar.high,
                    "low": bar.low,
                    "close": bar.close,
                    "volume": bar.volume,
                    "underlying_close": hist_spot,
                    "iv": iv,
                }
            )
    return rows


def _data_quality(analysis: AnalysisResult) -> dict[str, Any]:
    metrics = analysis.metrics
    spreads = [metric.spread_pct for metric in metrics if metric.spread_pct is not None]
    ivs = [metric.iv for metric in metrics if metric.iv is not None]
    return {
        "contracts": len(metrics),
        "quoted": sum(1 for metric in metrics if metric.mid is not None),
        "two_sided": sum(1 for metric in metrics if metric.quote_quality == "two-sided"),
        "with_iv": len(ivs),
        "with_history": len(analysis.snapshot.option_history),
        "liquid": sum(1 for metric in metrics if metric.liquid),
        "median_spread_pct": sorted(spreads)[len(spreads) // 2] if spreads else None,
    }


def _summary_payload(
    analysis: AnalysisResult,
    *,
    as_of: datetime,
    rate: float,
    dividend: float,
) -> dict[str, Any]:
    snapshot = analysis.snapshot
    movers = [
        metric
        for metric in analysis.metrics
        if metric.iv_change is not None and metric.iv is not None
    ]
    movers.sort(key=lambda metric: abs(metric.iv_change or 0.0), reverse=True)
    return {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "as_of": as_of.isoformat(timespec="seconds"),
            "environment": snapshot.environment,
            "synthetic": snapshot.synthetic,
            "source": snapshot.source,
            "root_id": snapshot.root_id,
            "disclaimer": (
                "Radar de recherche, pas un conseil en investissement. Les données Saxo SIM "
                "peuvent être simulées ou incomplètes ; vérifier chaque instrument avant tout ordre."
            ),
        },
        "underlying": {
            "uic": snapshot.underlying.uic,
            "symbol": snapshot.underlying.symbol,
            "description": snapshot.underlying.description,
            "exchange": snapshot.underlying.exchange_id,
            "currency": snapshot.underlying.currency,
            "spot": snapshot.underlying.spot,
        },
        "assumptions": {"rate": rate, "dividend": dividend, "history_days": 7},
        "volatility": {
            "rv7": analysis.rv7,
            "rv20": analysis.rv20,
            "rv60": analysis.rv60,
            "ewma": analysis.ewma,
            "term_slope": analysis.term_slope,
            "skew_25d": analysis.skew_25d,
        },
        "data_quality": _data_quality(analysis),
        "expiries": analysis.expiry_rows(),
        "signals": analysis.signal_rows(),
        "top_iv_movers": [
            {
                "uic": metric.contract.uic,
                "expiry": metric.contract.expiry.isoformat(),
                "strike": metric.contract.strike,
                "kind": metric.contract.kind,
                "iv": metric.iv,
                "iv_start": metric.iv_start,
                "iv_change": metric.iv_change,
                "iv_start_day": metric.iv_start_day.isoformat() if metric.iv_start_day else None,
            }
            for metric in movers[:10]
        ],
        "warnings": analysis.warnings,
    }


def _report_markdown(
    analysis: AnalysisResult,
    *,
    as_of: datetime,
    rate: float,
    dividend: float,
) -> str:
    snapshot = analysis.snapshot
    quality = _data_quality(analysis)
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# Radar options — SMA92",
        "",
        f"**Instrument :** {snapshot.underlying.symbol} — {snapshot.underlying.description}  ",
        f"**Source :** {snapshot.source}  ",
        f"**Snapshot :** {as_of.strftime('%Y-%m-%d %H:%M UTC')}  ",
        f"**Rapport généré :** {generated}",
        "",
    ]
    if snapshot.synthetic:
        lines += [
            "> **DÉMONSTRATION : données synthétiques.** Ce rapport montre le pipeline ; "
            "il ne décrit pas le marché réel.",
            "",
        ]
    if snapshot.environment == "sim" and not snapshot.synthetic:
        lines += [
            "> **Saxo SIM.** Les prix et volumes peuvent être simulés, retardés ou incomplets. "
            "Ne pas utiliser ce snapshot comme preuve de liquidité ou comme backtest de market data.",
            "",
        ]

    lines += [
        "## Photo du marché",
        "",
        "| Indicateur | Valeur |",
        "|---|---:|",
        f"| Spot | {_fr(snapshot.underlying.spot)} {snapshot.underlying.currency} |",
        f"| Vol réalisée 7 j | {_pct(analysis.rv7)} |",
        f"| Vol réalisée 20 j | {_pct(analysis.rv20)} |",
        f"| Vol réalisée 60 j | {_pct(analysis.rv60)} |",
        f"| EWMA | {_pct(analysis.ewma)} |",
        f"| Pente IV front - back | {_pct(analysis.term_slope, signed=True)} |",
        f"| Skew 25 delta (put - call) | {_pct(analysis.skew_25d, signed=True)} |",
        f"| Contrats / quotes bipaires | {quality['contracts']} / {quality['two_sided']} |",
        f"| Spread médian | {_pct(quality['median_spread_pct'])} |",
        f"| Taux / dividende | {_pct(rate, 2)} / {_pct(dividend, 2)} |",
        "",
        "## Structure par terme",
        "",
        "| Échéance | DTE | Strike ATM | IV ATM | RV20 | VRP | Straddle ATM | Spread médian | OI |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in analysis.expiry_summaries:
        lines.append(
            f"| {item.expiry.isoformat()} | {item.dte} | {_fr(item.atm_strike)} | "
            f"{_pct(item.atm_iv)} | {_pct(item.rv20)} | {_pct(item.vrp, signed=True)} | "
            f"{_fr(item.atm_straddle)} € | {_pct(item.median_spread_pct)} | {_fr(item.open_interest, 0)} |"
        )

    near = sorted(
        analysis.metrics,
        key=lambda metric: (metric.contract.expiry, abs(metric.contract.strike - snapshot.underlying.spot)),
    )[:20]
    lines += [
        "",
        "## Chaîne près du spot (20 contrats)",
        "",
        "Prix par action ; la valeur d'un contrat est le mid multiplié par la taille de contrat.",
        "",
        "| Échéance | K | Type | Bid | Ask | Mid | IV | Δ | OI | Spread | Flags |",
        "|---|---:|:---:|---:|---:|---:|---:|---:|---:|---:|:---|",
    ]
    for metric in near:
        contract = metric.contract
        lines.append(
            f"| {contract.expiry.isoformat()} | {_fr(contract.strike)} | {contract.kind} | "
            f"{_fr(contract.bid)} | {_fr(contract.ask)} | {_fr(metric.mid)} | {_pct(metric.iv)} | "
            f"{_fr(contract.delta, 3)} | {_fr(contract.open_interest, 0)} | {_pct(metric.spread_pct)} | "
            f"{', '.join(metric.flags) or '—'} |"
        )

    movers = [metric for metric in analysis.metrics if metric.iv_change is not None]
    movers.sort(key=lambda metric: abs(metric.iv_change or 0.0), reverse=True)
    lines += [
        "",
        "## Variations d'IV sur la fenêtre",
        "",
        "La variation compare la première observation disponible dans les 7 derniers jours au snapshot courant, "
        "pour le même contrat. Elle est indicative si le carnet était peu liquide.",
        "",
        "| Échéance | K | Type | IV début | IV actuelle | Δ IV | Date début |",
        "|---|---:|:---:|---:|---:|---:|---:|",
    ]
    for metric in movers[:10]:
        lines.append(
            f"| {metric.contract.expiry.isoformat()} | {_fr(metric.contract.strike)} | {metric.contract.kind} | "
            f"{_pct(metric.iv_start)} | {_pct(metric.iv)} | {_pct(metric.iv_change, signed=True)} | "
            f"{metric.iv_start_day.isoformat() if metric.iv_start_day else '—'} |"
        )
    if not movers:
        lines.append("| — | — | — | — | — | — | — |")

    lines += [
        "",
        "## Écrans heuristiques",
        "",
        "Ces lignes ne sont **pas des recommandations**. Elles signalent des écarts à investiguer.",
        "",
        "| Périmètre | Signal | Niveau | Valeur | Lecture |",
        "|:---|:---|:---:|---:|:---|",
    ]
    for signal in analysis.signals:
        value = _pct(signal.value, signed=True) if signal.unit == "pct" else _fr(signal.value)
        lines.append(
            f"| {signal.scope} | {signal.name} | {signal.level} | {value} | {signal.interpretation} |"
        )

    lines += [
        "",
        "## Qualité des données",
        "",
        f"- Contrats : **{quality['contracts']}**, avec mid : **{quality['quoted']}**, "
        f"quotes bipaires : **{quality['two_sided']}**, IV calculées : **{quality['with_iv']}**, "
        f"historiques : **{quality['with_history']}**, liquides selon le filtre : **{quality['liquid']}**.",
    ]
    if analysis.warnings:
        for warning in analysis.warnings:
            lines.append(f"- ⚠️ {warning}")
    else:
        lines.append("- Aucun avertissement de qualité de données.")

    lines += [
        "",
        "## Méthodologie et limites",
        "",
        "- L'IV est inversée en Black-Scholes européen à partir du mid, avec taux et dividende constants.",
        "- Le VRP est la différence entre l'IV ATM et la volatilité réalisée 20 j ; c'est un écran, "
        "pas une preuve d'arbitrage.",
        "- Le skew 25 delta est approximé par les contrats dont le delta est le plus proche de ±0,25 "
        "dans la première échéance. Il dépend des grecques fournies par Saxo.",
        "- L'historique Saxo est un OHLC par contrat, pas un historisation complète du carnet. "
        "Un close sans volume peut être un report de prix.",
        "- Les données SIM ne doivent pas servir à calibrer un backtest de production. "
        "Pour cela, il faut un flux live ou un fournisseur historique d'options.",
        "",
    ]
    return "\n".join(lines)


def _print_summary(analysis: AnalysisResult, output_dir: Path) -> None:
    snapshot = analysis.snapshot
    quality = _data_quality(analysis)
    print("")
    print(f"Options SMA92 — {snapshot.source}")
    print(f"Spot {_fr(snapshot.underlying.spot)} {snapshot.underlying.currency} | "
          f"RV20 {_pct(analysis.rv20)} | IV ATM front {_pct(analysis.expiry_summaries[0].atm_iv if analysis.expiry_summaries else None)}")
    print(
        f"{quality['contracts']} contrats | {quality['two_sided']} quotes bipaires | "
        f"{quality['with_history']} historiques | spread médian {_pct(quality['median_spread_pct'])}"
    )
    for signal in analysis.signals:
        if signal.level in {"watch", "alert"}:
            print(f"  [{signal.level}] {signal.name} : {signal.interpretation}")
    print(f"Rapport : {output_dir / 'report.md'}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        _load_env_file(args.env_file)
        as_of = _as_of(args.as_of)
        output_dir: Path = args.output_dir
        output_dir.mkdir(parents=True, exist_ok=True)

        environment = args.environment or os.getenv("SAXO_ENV", "sim")

        if args.dry_run:
            if not args.fixture.exists():
                raise FileNotFoundError(f"fixture introuvable : {args.fixture}")
            snapshot = load_fixture(args.fixture)
            logger: Callable[[str], None] = lambda message: print(message, file=sys.stderr)
            logger(f"Mode dry-run : {args.fixture}")
        else:
            token = _read_token(args.token_file)
            with SaxoClient(
                token,
                environment=environment,
                timeout=args.timeout,
                max_retries=args.max_retries,
            ) as client:
                snapshot = fetch_snapshot(
                    client,
                    symbol=args.symbol,
                    keywords=args.keywords,
                    uic=args.uic,
                    exchange=args.exchange,
                    as_of=as_of,
                    max_expiries=args.max_expiries,
                    max_days=args.max_days,
                    strike_window=args.strike_window,
                    max_contracts=args.max_contracts,
                    history_days=args.days,
                    history_scope=args.history_scope,
                    history_window=args.history_window,
                    max_history_contracts=args.max_history_contracts,
                    option_root_id=args.option_root_id,
                    logger=lambda message: print(message, file=sys.stderr),
                )

        analysis_as_of = as_of if args.as_of else snapshot.underlying.as_of
        analysis = run_analysis(snapshot, rate=args.rate, dividend=args.dividend, as_of=analysis_as_of)

        _write_csv(
            output_dir / "options_chain_7d.csv",
            analysis.metric_rows(),
            [
                "uic",
                "expiry",
                "dte",
                "kind",
                "strike",
                "moneyness",
                "bid",
                "ask",
                "mid",
                "spread",
                "spread_pct",
                "last",
                "volume",
                "open_interest",
                "iv",
                "iv_start",
                "iv_change",
                "iv_start_day",
                "intrinsic",
                "time_value",
                "premium_per_contract",
                "breakeven",
                "delta",
                "gamma",
                "vega",
                "theta",
                "quote_quality",
                "history_points",
                "history_volume",
                "flags",
            ],
        )
        _write_csv(
            output_dir / "expiry_summary.csv",
            analysis.expiry_rows(),
            [
                "expiry",
                "dte",
                "atm_strike",
                "atm_iv",
                "atm_call_iv",
                "atm_put_iv",
                "atm_straddle",
                "rv20",
                "vrp",
                "median_spread_pct",
                "open_interest",
                "volume",
                "contracts",
            ],
        )
        _write_csv(
            output_dir / "signals.csv",
            analysis.signal_rows(),
            ["scope", "signal", "level", "value", "unit", "interpretation", "caveat"],
        )
        _write_csv(
            output_dir / "underlying_7d.csv",
            _underlying_rows(analysis, analysis_as_of),
            ["date", "open", "high", "low", "close", "volume", "log_return"],
        )
        _write_csv(
            output_dir / "option_history_7d.csv",
            _option_history_rows(analysis, rate=args.rate, dividend=args.dividend),
            [
                "uic",
                "expiry",
                "strike",
                "kind",
                "date",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "underlying_close",
                "iv",
            ],
        )
        (output_dir / "summary.json").write_text(
            json.dumps(
                _summary_payload(analysis, as_of=analysis_as_of, rate=args.rate, dividend=args.dividend),
                indent=2,
                ensure_ascii=False,
                default=str,
            )
            + "\n",
            encoding="utf-8",
        )
        (output_dir / "report.md").write_text(
            _report_markdown(analysis, as_of=analysis_as_of, rate=args.rate, dividend=args.dividend),
            encoding="utf-8",
        )
    except (SaxoAuthError, SaxoAPIError, SaxoError, ValueError, FileNotFoundError) as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 2

    if not args.quiet:
        _print_summary(analysis, output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
