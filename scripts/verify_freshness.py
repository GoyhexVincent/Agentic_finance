#!/usr/bin/env python3
"""Contrôle de fraîcheur et de cohérence du Finance Tracker.

À lancer après l'ETL (stage Jenkins). Sort en code 1 si :
  * la table des prix est vide ;
  * le benchmark URTH a plus de MAX_BENCHMARK_AGE_DAYS jours ;
  * un actif a plus de MAX_ASSET_AGE_DAYS jours ;
  * le dernier import réussi a plus de MAX_IMPORT_AGE_HOURS heures ;
  * les fonctions de corrélation ne renvoient aucune ligne.
"""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta
from pathlib import Path

from psycopg2.extras import RealDictCursor

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from finance_tracker import config, db


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        print(f"ERROR : {name} doit être un nombre (reçu : {raw!r}).")
        sys.exit(1)


def main() -> int:
    max_benchmark_age = _env_float("MAX_BENCHMARK_AGE_DAYS", 5)
    max_asset_age = _env_float("MAX_ASSET_AGE_DAYS", 10)
    max_import_age = _env_float("MAX_IMPORT_AGE_HOURS", 36)

    config.require_remote_password()
    conn = db.connect()
    failures: list[str] = []
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(f"SELECT * FROM {config.SCHEMA}.v_freshness ORDER BY display_order, ticker;")
            freshness = [dict(row) for row in cur.fetchall()]

            cur.execute(f"SELECT count(*) AS n FROM {config.SCHEMA}.prices_daily;")
            n_prices = cur.fetchone()["n"]

            cur.execute(f"SELECT count(*) AS n FROM {config.SCHEMA}.ingestion_runs;")
            n_runs = cur.fetchone()["n"]

            cur.execute(
                f"SELECT count(*) AS n FROM {config.SCHEMA}.corr_pairs(%s, %s, %s, %s);",
                (date.today() - timedelta(days=365 * 3), date.today(), config.BASE_CURRENCY, config.MIN_OBS),
            )
            n_pairs = cur.fetchone()["n"]

            cur.execute(
                f"SELECT count(*) AS n FROM {config.SCHEMA}.corr_matrix_core(%s, %s, %s, %s);",
                (date.today() - timedelta(days=365 * 3), date.today(), config.BASE_CURRENCY, config.MIN_OBS),
            )
            n_matrix_rows = cur.fetchone()["n"]
    finally:
        conn.close()

    print(f"Lignes de prix          : {n_prices}")
    print(f"Exécutions enregistrées : {n_runs}")
    print(f"Paires de corrélation   : {n_pairs}")
    print(f"Lignes matrice core     : {n_matrix_rows}")
    print()
    print(f"{'Ticker':<8} {'Dernière séance':<16} {'Âge (j)':>8} {'Univers':<8}")
    print("-" * 46)
    for row in freshness:
        print(
            f"{row['ticker']:<8} {str(row['last_trade_date']):<16} "
            f"{str(row['days_since_last_trade']):>8} {row['universe']:<8}"
        )

    if not n_prices:
        failures.append("La table des prix est vide.")
    if not freshness:
        failures.append("Aucun actif dans le référentiel.")
    if not n_pairs:
        failures.append("Aucune paire de corrélation : les fonctions SQL ne répondent pas.")
    if n_matrix_rows < 15:
        failures.append(f"Matrice core incomplète : {n_matrix_rows} lignes (attendu 15).")

    for row in freshness:
        age = row["days_since_last_trade"]
        if age is None:
            failures.append(f"{row['ticker']} : aucune donnée.")
            continue
        limit = max_benchmark_age if row["ticker"] == "URTH" else max_asset_age
        if age > limit:
            failures.append(f"{row['ticker']} : dernière séance il y a {age} j (seuil {limit:g} j).")

    last_run = next((row["last_run_finished_at"] for row in freshness if row["last_run_finished_at"]), None)
    if last_run is None:
        failures.append("Aucun import réussi enregistré.")
    else:
        hours = next(
            (row["hours_since_last_run"] for row in freshness if row["hours_since_last_run"] is not None),
            None,
        )
        print()
        print(f"Dernier import réussi   : {last_run} ({hours} h)")
        if hours is not None and hours > max_import_age:
            failures.append(f"Dernier import trop ancien : {hours} h (seuil {max_import_age:g} h).")

    if failures:
        print()
        for failure in failures:
            print(f"ERROR : {failure}")
        return 1

    print()
    print("OK : données présentes, fraîches et fonctions de corrélation opérationnelles.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
