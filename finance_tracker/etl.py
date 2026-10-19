"""ETL Finance Tracker — Yahoo Finance vers PostgreSQL.

Utilisation :
    python -m finance_tracker.etl
    python -m finance_tracker.etl --start 2018-01-01 --dry-run
    python -m finance_tracker.etl --assets XLK,URTH,ASML --skip-analytics

Le pipeline est volontairement simple :
  1. garde-fou de fréquence ;
  2. téléchargement de tous les actifs et des taux FX ;
  3. chargement bulk dans une transaction (DELETE ciblé + COPY) ;
  4. clustering et détection de ruptures ;
  5. journalisation et contrôle de sortie.

Les téléchargements ont lieu AVANT la transaction : une panne réseau ne laisse
pas la base dans un état intermédiaire. Si un actif secondaire échoue, son
ancien historique est conservé ; les autres sont mis à jour.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import date, timedelta

from . import analytics, config, db, yahoo
from .assets import ASSETS, ASSET_BY_TICKER, BENCHMARK, FX_PAIRS

logger = logging.getLogger("finance_tracker.etl")


def _selected_assets(raw: str | None):
    if not raw:
        return list(ASSETS)
    tickers = [part.strip().upper() for part in raw.split(",") if part.strip()]
    unknown = [ticker for ticker in tickers if ticker not in ASSET_BY_TICKER]
    if unknown:
        raise SystemExit(f"ERROR : actifs inconnus : {', '.join(unknown)}")
    return [ASSET_BY_TICKER[ticker] for ticker in tickers]


def _fetch_all(assets, start: date, end: date):
    prices: dict[str, list[dict]] = {}
    failures: list[str] = []
    for index, asset in enumerate(assets):
        logger.info("Téléchargement %s (%s)…", asset.ticker, asset.yahoo_symbol)
        try:
            meta, rows = yahoo.fetch_asset(asset, start, end)
            if not rows:
                raise yahoo.YahooError("aucune barre exploitable")
            prices[asset.ticker] = rows
            logger.info(
                "  %s : %s séances, %s → %s, devise %s",
                asset.ticker,
                len(rows),
                rows[0]["trade_date"],
                rows[-1]["trade_date"],
                meta.get("currency"),
            )
        except Exception as exc:  # YahooError, erreur réseau, devise inattendue…
            logger.error("  %s en échec : %s", asset.ticker, exc)
            failures.append(f"{asset.ticker}: {exc}")
        if index < len(assets) - 1:
            time.sleep(config.HTTP_DELAY)
    return prices, failures


def _fetch_fx(assets, start: date, end: date):
    currencies = sorted({asset.currency for asset in assets if asset.currency != "EUR"})
    fx: dict[str, list[dict]] = {}
    failures: list[str] = []
    for currency in currencies:
        if currency not in FX_PAIRS:
            continue
        logger.info("Téléchargement FX %s (%s)…", currency, FX_PAIRS[currency])
        try:
            rows = yahoo.fetch_fx(currency, start, end)
            fx[currency] = rows
            logger.info("  %s : %s taux, %s → %s", currency, len(rows), rows[0]["trade_date"], rows[-1]["trade_date"])
        except Exception as exc:
            logger.error("  FX %s en échec : %s", currency, exc)
            failures.append(f"FX {currency}: {exc}")
    return fx, failures


def _load(conn, prices: dict[str, list[dict]], fx: dict[str, list[dict]]) -> int:
    rows_loaded = 0
    with conn:
        db.advisory_lock(conn)
        db.load_assets(conn, ASSETS)
        asset_ids = {
            row["ticker"]: row["asset_id"]
            for row in db.fetch_assets(conn)
        }
        for ticker, rows in prices.items():
            asset = ASSET_BY_TICKER[ticker]
            asset_id = asset_ids.get(ticker)
            if asset_id is None:
                raise RuntimeError(f"asset_id introuvable pour {ticker}")
            rows_loaded += db.replace_prices(conn, asset_id, asset.currency, rows)
        for currency, rows in fx.items():
            rows_loaded += db.replace_fx(conn, currency, rows)
        db.analyze(conn)
    return rows_loaded


def _run_analytics(conn, end: date) -> None:
    start = end - timedelta(days=365 * config.CLUSTER_LOOKBACK_YEARS)
    try:
        analytics.compute_clusters(conn, start, end, config.BASE_CURRENCY, config.MIN_OBS, config.CLUSTER_K)
    except Exception as exc:
        logger.warning("Clustering en échec (les données restent disponibles) : %s", exc)
    try:
        analytics.compute_breaks(
            conn,
            start,
            end,
            config.BASE_CURRENCY,
            config.BREAK_WINDOW,
            config.BREAK_MAX,
            config.BREAK_MIN_SIZE,
        )
    except Exception as exc:
        logger.warning("Détection de ruptures en échec (les données restent disponibles) : %s", exc)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Finance Tracker — import Yahoo Finance vers PostgreSQL.")
    parser.add_argument("--start", type=date.fromisoformat, default=config.START_DATE)
    parser.add_argument("--end", type=date.fromisoformat, default=config.END_DATE)
    parser.add_argument("--assets", default=None, help="Liste de tickers séparés par des virgules.")
    parser.add_argument("--dry-run", action="store_true", help="Télécharge et affiche, sans écrire en base.")
    parser.add_argument("--skip-analytics", action="store_true")
    parser.add_argument("--force", action="store_true", help="Ignore le garde-fou de fréquence.")
    parser.add_argument("--log-level", default=config.LOG_LEVEL)
    args = parser.parse_args(argv)

    logging.basicConfig(level=args.log_level, format="%(asctime)s [%(levelname)s] %(message)s")
    assets = _selected_assets(args.assets)

    if args.start > args.end:
        raise SystemExit("ERROR : --start doit précéder --end.")

    if args.dry_run:
        prices, failures = _fetch_all(assets, args.start, args.end)
        fx, fx_failures = _fetch_fx(assets, args.start, args.end)
        print(f"Actifs OK       : {len(prices)}/{len(assets)}")
        print(f"Séances totales : {sum(len(rows) for rows in prices.values())}")
        print(f"FX OK           : {', '.join(sorted(fx)) or 'aucun'}")
        if failures:
            print("Échecs :")
            for failure in failures:
                print(f"  - {failure}")
        if fx_failures:
            print("Échecs FX :")
            for failure in fx_failures:
                print(f"  - {failure}")
        return 0 if not failures and not fx_failures else 1

    config.require_remote_password()
    conn = db.connect()
    run_id = None
    try:
        db.init_schema(conn)

        if not (args.force or config.FORCE_IMPORT) and config.MIN_IMPORT_INTERVAL_HOURS > 0:
            age = db.last_successful_run_age_hours(conn)
            if age is not None and age < config.MIN_IMPORT_INTERVAL_HOURS:
                logger.info(
                    "Import récent (%.1f h < %.1f h) : rien à faire. "
                    "Utiliser --force ou FINANCE_FORCE_IMPORT=1 pour passer outre.",
                    age,
                    config.MIN_IMPORT_INTERVAL_HOURS,
                )
                return 0

        run_id = db.start_run(conn, len(assets))
        prices, failures = _fetch_all(assets, args.start, args.end)
        fx, fx_failures = _fetch_fx(assets, args.start, args.end)

        if any(asset.ticker == BENCHMARK for asset in assets) and BENCHMARK not in prices:
            message = f"Benchmark {BENCHMARK} absent : " + "; ".join(failures)
            db.finish_run(conn, run_id, "failed", len(prices), 0, message[:4000])
            logger.error(message)
            return 1

        rows_loaded = _load(conn, prices, fx)
        if not args.skip_analytics:
            _run_analytics(conn, args.end)

        all_failures = failures + fx_failures
        status = "success" if not all_failures else "partial"
        message = None if not all_failures else "; ".join(all_failures)[:4000]
        db.finish_run(conn, run_id, status, len(prices), rows_loaded, message)

        logger.info(
            "Import terminé : %s/%s actifs, %s lignes, statut %s.",
            len(prices),
            len(assets),
            rows_loaded,
            status,
        )
        if all_failures:
            for failure in all_failures:
                logger.warning("Échec partiel : %s", failure)
            return 1
        return 0
    except Exception as exc:
        logger.exception("Échec de l'ETL : %s", exc)
        if run_id is not None:
            try:
                db.finish_run(conn, run_id, "failed", 0, 0, str(exc)[:4000])
            except Exception:
                logger.exception("Impossible de finaliser le journal d'exécution.")
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
