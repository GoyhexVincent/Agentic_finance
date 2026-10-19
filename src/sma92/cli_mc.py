"""Interface ligne de commande de la simulation Monte Carlo SMA92."""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .market_data import PriceSeries, load_price_csv
from .simulation import SimulationConfig, SimulationResult, run_simulation

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PRICES = PROJECT_ROOT / "data" / "sma_solar_daily.csv"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "output" / "monte_carlo"


def _fr(value: float | None, digits: int = 2) -> str:
    if value is None or not math.isfinite(value):
        return "—"
    text = f"{value:,.{digits}f}"
    return text.replace(",", "\u202f").replace(".", ",")


def _pct(value: float | None, digits: int = 1) -> str:
    return "—" if value is None else f"{_fr(value * 100, digits)} %"


def _parse_strikes(raw: str | None) -> tuple[float, ...]:
    if not raw:
        return ()
    values: list[float] = []
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        value = float(token)
        if value <= 0:
            raise ValueError("les strikes doivent être positifs")
        values.append(value)
    return tuple(values)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Simulation Monte Carlo du sous-jacent SMA92 et prix d'options européennes.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--prices-csv", type=Path, default=DEFAULT_PRICES, help="Historique daily date,close")
    parser.add_argument("--spot", type=float, help="Spot initial ; par défaut, dernier close du CSV")
    parser.add_argument("--vol", type=float, help="Volatilité annualisée ; par défaut, vol réalisée 60 j")
    parser.add_argument("--strike", type=float, help="Strike principal ; par défaut, spot arrondi au 0,5")
    parser.add_argument("--strikes", type=str, help="Strikes supplémentaires, séparés par des virgules")
    parser.add_argument("--days", type=int, default=30, help="Horizon calendaire en jours")
    parser.add_argument("--rate", type=float, default=0.02, help="Taux sans risque continu")
    parser.add_argument("--dividend", type=float, default=0.0, help="Rendement de dividende continu")
    parser.add_argument("--paths", type=int, default=200_000, help="Nombre de trajectoires (pair)")
    parser.add_argument("--steps", type=int, help="Pas de simulation ; par défaut 252 pas/an")
    parser.add_argument("--seed", type=int, default=42, help="Graine aléatoire")
    parser.add_argument(
        "--model",
        choices=("gbm", "bootstrap", "student-t"),
        default="gbm",
        help="Loi des chocs : GBM, bootstrap historique ou Student-t",
    )
    parser.add_argument("--student-t-df", type=float, default=5.0, help="Degrés de liberté Student-t")
    parser.add_argument("--no-antithetic", action="store_true", help="Désactiver les variables antithétiques")
    parser.add_argument("--contract-size", type=float, default=100.0, help="Multiplicateur d'un contrat")
    parser.add_argument("--instrument", default="SMA92 — SMA Solar Technology (S92.XETR)")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--quiet", action="store_true", help="Ne pas afficher le résumé dans le terminal")
    return parser


def _write_csv(path: Path, rows: list[dict[str, object]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _price_rows(result: SimulationResult) -> list[dict[str, object]]:
    return [
        {
            "strike": price.strike,
            "kind": price.kind,
            "mc_price": price.mc_price,
            "standard_error": price.standard_error,
            "ci95_low": price.ci_low,
            "ci95_high": price.ci_high,
            "bs_price": price.bs_price,
            "difference_vs_bs": price.difference_vs_bs,
            "probability_itm": price.probability_itm,
            "contract_value": price.contract_value,
            "delta_bs": price.delta_bs,
            "gamma_bs": price.gamma_bs,
            "vega_bs_per_vol_point": price.vega_bs,
            "theta_bs_per_day": price.theta_bs,
        }
        for price in result.option_prices
    ]


def _json_payload(
    result: SimulationResult,
    series: PriceSeries,
    vol_estimates: dict[str, float | None],
    *,
    instrument: str,
) -> dict[str, object]:
    config = result.config
    return {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "instrument": instrument,
            "disclaimer": (
                "Outil de recherche et de paper trading. Ce n'est pas un conseil en investissement. "
                "Les prix Saxo SIM peuvent être simulés, retardés ou incomplets."
            ),
        },
        "market_data": {
            "source": series.source,
            "last_date": series.last_date.isoformat(),
            "last_close": series.last_close,
            "observations": len(series.closes),
            "volatility_estimates": vol_estimates,
        },
        "config": {
            "spot": config.spot,
            "volatility": config.volatility,
            "strike": config.strike,
            "strikes": list(config.all_strikes),
            "days": config.days,
            "tau_years": config.tau,
            "rate": config.rate,
            "dividend": config.dividend,
            "n_paths": config.n_paths,
            "n_steps": config.steps,
            "seed": config.seed,
            "model": config.model,
            "antithetic": config.antithetic,
            "student_t_df": config.student_t_df,
            "contract_size": config.contract_size,
        },
        "terminal_distribution": result.terminal_summary | result.quantiles,
        "option_prices": [asdict(price) for price in result.option_prices],
        "warnings": result.warnings,
    }


def _report_markdown(
    result: SimulationResult,
    series: PriceSeries,
    vol_estimates: dict[str, float | None],
    *,
    instrument: str,
) -> str:
    cfg = result.config
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# Simulation Monte Carlo — SMA92",
        "",
        f"**Instrument de travail :** {instrument}  ",
        f"**Généré le :** {generated}  ",
        f"**Dernière donnée historique :** {series.last_date.isoformat()} — {_fr(series.last_close)} €",
        "",
        "> Hypothèse de travail : SMA92 correspond à SMA Solar Technology AG (S92.XETR). "
        "Si le code instrument est différent, remplacer le CSV, le spot et la volatilité.",
        "",
        "## Hypothèses",
        "",
        "| Paramètre | Valeur |",
        "|---|---:|",
        f"| Spot initial | {_fr(cfg.spot)} € |",
        f"| Volatilité annualisée | {_pct(cfg.volatility)} |",
        f"| Horizon | {cfg.days} jours calendaires ({_fr(cfg.tau, 4)} an) |",
        f"| Pas de simulation | {cfg.steps} |",
        f"| Taux sans risque | {_pct(cfg.rate, 2)} |",
        f"| Rendement de dividende | {_pct(cfg.dividend, 2)} |",
        f"| Modèle | {cfg.model} |",
        f"| Trajectoires | {cfg.n_paths:,} |".replace(",", "\u202f"),
        f"| Variables antithétiques | {'oui' if cfg.antithetic else 'non'} |",
        f"| Graine | {cfg.seed} |",
        "",
        "### Volatilités estimées sur l'historique",
        "",
        "| Fenêtre | Volatilité annualisée |",
        "|---|---:|",
    ]
    for label, value in vol_estimates.items():
        lines.append(f"| {label} | {_pct(value)} |")

    lines += [
        "",
        "## Distribution du prix terminal",
        "",
        "| Statistique | Valeur |",
        "|---|---:|",
        f"| Moyenne | {_fr(result.terminal_summary['mean'])} € |",
        f"| Médiane | {_fr(result.quantiles['q50'])} € |",
        f"| Écart-type | {_fr(result.terminal_summary['std'])} € |",
        f"| 5 % / 95 % | {_fr(result.quantiles['q05'])} € / {_fr(result.quantiles['q95'])} € |",
        f"| 1 % / 99 % | {_fr(result.quantiles['q01'])} € / {_fr(result.quantiles['q99'])} € |",
        f"| P(S_T < spot) | {_pct(result.terminal_summary['probability_below_spot'])} |",
        f"| Skewness | {_fr(result.terminal_summary['skewness'], 3)} |",
        f"| Kurtosis en excès | {_fr(result.terminal_summary['excess_kurtosis'], 3)} |",
        "",
        "## Prix des options européennes",
        "",
        "Prix par action. L'intervalle de confiance à 95 % est calculé par moyennes de blocs, "
        "ce qui reste conservateur avec des variables antithétiques.",
        "",
        "| Strike | Type | MC | IC 95 % | Black-Scholes | Écart | P(ITM) | 1 contrat |",
        "|---:|:---:|---:|---:|---:|---:|---:|---:|",
    ]
    for price in result.option_prices:
        lines.append(
            "| "
            + " | ".join(
                [
                    _fr(price.strike),
                    price.kind,
                    f"{_fr(price.mc_price)} €",
                    f"[{_fr(price.ci_low)} ; {_fr(price.ci_high)}]",
                    f"{_fr(price.bs_price)} €",
                    f"{_fr(price.difference_vs_bs, 3)} €",
                    _pct(price.probability_itm),
                    f"{_fr(price.contract_value)} €",
                ]
            )
            + " |"
        )

    lines += [
        "",
        "### Grecques Black-Scholes indicatives",
        "",
        "| Strike | Type | Delta | Gamma | Vega / pt de vol | Theta / jour |",
        "|---:|:---:|---:|---:|---:|---:|",
    ]
    for price in result.option_prices:
        lines.append(
            f"| {_fr(price.strike)} | {price.kind} | {_fr(price.delta_bs, 3)} | "
            f"{_fr(price.gamma_bs, 4)} | {_fr(price.vega_bs, 3)} € | {_fr(price.theta_bs, 3)} € |"
        )

    lines += [
        "",
        "## Lecture et limites",
        "",
        "- Le modèle GBM suppose une volatilité constante, des rendements gaussiens et aucun saut. "
        "Il sous-estime généralement les queues épaisses et la volatilité stochastique.",
        "- Le mode `bootstrap` rééchantillonne les rendements historiques standardisés : il conserve "
        "mieux l'asymétrie et les queues, mais ne garantit pas une mesure risque-neutre.",
        "- Le prix Monte Carlo est un prix de modèle, pas un prix de marché. La comparaison utile est "
        "l'écart entre prix de modèle et prix mid Saxo, puis la volatilité implicite associée.",
        "- Les grecques affichées sont analytiques (Black-Scholes) et non des grecques Monte Carlo.",
        "- Le taux sans risque et le dividende sont des hypothèses : les faire varier pour tester la sensibilité.",
        "",
    ]
    if result.warnings:
        lines.append("### Avertissements")
        lines.append("")
        for warning in result.warnings:
            lines.append(f"- {warning}")
        lines.append("")

    return "\n".join(lines)


def _print_summary(result: SimulationResult, output_dir: Path) -> None:
    cfg = result.config
    print("")
    print(f"Simulation Monte Carlo — {cfg.n_paths:,} trajectoires, {cfg.model}".replace(",", " "))
    print(f"Spot {_fr(cfg.spot)} € | vol {_pct(cfg.volatility)} | horizon {cfg.days} j | strike {_fr(cfg.strike)} €")
    print(
        f"Terminal : médiane {_fr(result.quantiles['q50'])} € | "
        f"IC 90 % [{_fr(result.quantiles['q05'])} ; {_fr(result.quantiles['q95'])}] €"
    )
    for price in result.option_prices:
        print(
            f"  {price.kind:4} K={_fr(price.strike):>7} : "
            f"{_fr(price.mc_price)} € (IC95 {_fr(price.ci_low)}–{_fr(price.ci_high)}), "
            f"BS {_fr(price.bs_price)} €"
        )
    print(f"Rapport : {output_dir / 'mc_report.md'}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        series = load_price_csv(args.prices_csv)
        vol_estimates = series.volatility_estimates()
        spot = args.spot if args.spot is not None else series.last_close
        if spot <= 0:
            raise ValueError("--spot doit être positif")

        if args.vol is not None:
            volatility = args.vol
        else:
            volatility = vol_estimates.get("rv60") or vol_estimates.get("rv20") or vol_estimates.get("ewma")
            if volatility is None:
                raise ValueError("impossible d'estimer la volatilité ; utiliser --vol")
        if volatility <= 0:
            raise ValueError("--vol doit être strictement positive")

        extra_strikes = _parse_strikes(args.strikes)
        strike = args.strike if args.strike is not None else round(spot * 2.0) / 2.0
        if strike <= 0:
            raise ValueError("--strike doit être strictement positif")

        config = SimulationConfig(
            spot=spot,
            volatility=volatility,
            strike=strike,
            strikes=extra_strikes,
            days=args.days,
            rate=args.rate,
            dividend=args.dividend,
            n_paths=args.paths,
            n_steps=args.steps,
            seed=args.seed,
            model=args.model,
            antithetic=not args.no_antithetic,
            student_t_df=args.student_t_df,
            historical_returns=tuple(float(x) for x in series.returns),
            contract_size=args.contract_size,
        )
        result = run_simulation(config)

        output_dir = args.output_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        _write_csv(
            output_dir / "mc_prices.csv",
            _price_rows(result),
            [
                "strike",
                "kind",
                "mc_price",
                "standard_error",
                "ci95_low",
                "ci95_high",
                "bs_price",
                "difference_vs_bs",
                "probability_itm",
                "contract_value",
                "delta_bs",
                "gamma_bs",
                "vega_bs_per_vol_point",
                "theta_bs_per_day",
            ],
        )
        _write_csv(
            output_dir / "mc_terminal_quantiles.csv",
            [{"quantile": k, "price": v} for k, v in result.quantiles.items()],
            ["quantile", "price"],
        )
        (output_dir / "mc_report.json").write_text(
            json.dumps(
                _json_payload(result, series, vol_estimates, instrument=args.instrument),
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        (output_dir / "mc_report.md").write_text(
            _report_markdown(result, series, vol_estimates, instrument=args.instrument),
            encoding="utf-8",
        )
    except (ValueError, FileNotFoundError, FloatingPointError) as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 2

    if not args.quiet:
        _print_summary(result, output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
