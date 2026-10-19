"""Accès PostgreSQL : schéma, chargement bulk, journal d'exécution, requêtes."""

from __future__ import annotations

import csv
import io
import logging
from datetime import date
from pathlib import Path

import psycopg2
from psycopg2 import sql
from psycopg2.extras import Json, RealDictCursor, execute_values

from . import config
from .assets import Asset

logger = logging.getLogger(__name__)


def connect():
    kwargs = dict(config.DB_CONFIG)
    kwargs["application_name"] = "finance_tracker"
    kwargs["connect_timeout"] = 10
    return psycopg2.connect(**kwargs)


def init_schema(conn) -> None:
    """Rejoue le DDL idempotent (tables, vues, fonctions, événements)."""
    ddl = config.SCHEMA_FILE.read_text(encoding="utf-8")
    try:
        with conn.cursor() as cur:
            cur.execute(ddl)
        conn.commit()
    except psycopg2.Error:
        conn.rollback()
        raise
    logger.info("Schéma %s vérifié (%s)", config.SCHEMA, config.SCHEMA_FILE.name)


def advisory_lock(conn) -> None:
    """Empêche deux ETL concurrents d'écrire en même temps."""
    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(hashtext('finance_tracker_etl'))")


# ───────────────────────────── Référentiel ─────────────────────────────
def load_assets(conn, assets: tuple[Asset, ...]) -> int:
    rows = [
        (
            asset.ticker,
            asset.yahoo_symbol,
            asset.name,
            asset.asset_type,
            asset.category,
            asset.universe,
            asset.currency,
            asset.exchange,
            asset.is_benchmark,
            None,
            asset.display_order,
            Json(asset.metadata),
        )
        for asset in assets
    ]
    statement = f"""
        INSERT INTO {config.SCHEMA}.assets
            (ticker, yahoo_symbol, name, asset_type, category, universe,
             currency, exchange, is_benchmark, inception_date, display_order, metadata)
        VALUES %s
        ON CONFLICT (ticker) DO UPDATE SET
            yahoo_symbol   = EXCLUDED.yahoo_symbol,
            name           = EXCLUDED.name,
            asset_type     = EXCLUDED.asset_type,
            category       = EXCLUDED.category,
            universe       = EXCLUDED.universe,
            currency       = EXCLUDED.currency,
            exchange       = EXCLUDED.exchange,
            is_benchmark   = EXCLUDED.is_benchmark,
            inception_date = COALESCE(EXCLUDED.inception_date, {config.SCHEMA}.assets.inception_date),
            metadata       = EXCLUDED.metadata,
            updated_at     = NOW()
    """
    with conn.cursor() as cur:
        execute_values(cur, statement, rows, page_size=100)
        written = cur.rowcount
    return written


# ───────────────────────────── Chargement bulk ─────────────────────────────
def _num(value: float | None, decimals: int = 6) -> str:
    if value is None:
        return ""
    return f"{value:.{decimals}f}"


def _copy_buffer(rows: list[list[str]]) -> io.StringIO:
    """Sérialise des lignes en CSV pour COPY (sans ligne d'en-tête)."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    for row in rows:
        writer.writerow(row)
    buffer.seek(0)
    return buffer


def replace_prices(conn, asset_id: int, currency: str, rows: list[dict]) -> int:
    """Remplace tout l'historique d'un actif (DELETE + COPY).

    Pourquoi un remplacement complet ?
      * les clôtures ajustées Yahoo changent après splits/dividendes ;
      * un simple ON CONFLICT DO NOTHING figerait des prix faux ;
      * un DELETE ciblé + COPY est plus rapide qu'un upsert ligne à ligne.
    """
    if not rows:
        return 0

    table = sql.SQL("{}.prices_daily").format(sql.Identifier(config.SCHEMA))
    copy_stmt = sql.SQL(
        "COPY {} (asset_id, trade_date, open, high, low, close, adj_close, "
        "volume, currency, source) FROM STDIN WITH (FORMAT CSV, NULL '')"
    ).format(table)

    data_rows = [
        [
            str(asset_id),
            row["trade_date"].isoformat(),
            _num(row.get("open")),
            _num(row.get("high")),
            _num(row.get("low")),
            _num(row["close"]),
            _num(row["adj_close"]),
            "" if row.get("volume") is None else str(int(row["volume"])),
            currency,
            "yahoo",
        ]
        for row in rows
    ]
    buffer = _copy_buffer(data_rows)

    with conn.cursor() as cur:
        cur.execute(
            sql.SQL("DELETE FROM {} WHERE asset_id = %s").format(table),
            (asset_id,),
        )
        cur.copy_expert(copy_stmt.as_string(conn), buffer)
    return len(rows)


def replace_fx(conn, currency: str, rows: list[dict]) -> int:
    if not rows:
        return 0

    table = sql.SQL("{}.fx_daily").format(sql.Identifier(config.SCHEMA))
    copy_stmt = sql.SQL(
        "COPY {} (currency, trade_date, eur_per_unit, source) "
        "FROM STDIN WITH (FORMAT CSV, NULL '')"
    ).format(table)

    data_rows = [
        [
            currency,
            row["trade_date"].isoformat(),
            _num(row["eur_per_unit"], 10),
            row.get("source", "yahoo"),
        ]
        for row in rows
    ]
    buffer = _copy_buffer(data_rows)

    with conn.cursor() as cur:
        cur.execute(
            sql.SQL("DELETE FROM {} WHERE currency = %s").format(table),
            (currency,),
        )
        cur.copy_expert(copy_stmt.as_string(conn), buffer)
    return len(rows)


def analyze(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(sql.SQL("ANALYZE {}").format(sql.SQL("{}.prices_daily").format(sql.Identifier(config.SCHEMA))))
        cur.execute(sql.SQL("ANALYZE {}").format(sql.SQL("{}.fx_daily").format(sql.Identifier(config.SCHEMA))))


# ───────────────────────────── Journal ─────────────────────────────
def start_run(conn, assets_requested: int) -> int:
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL("INSERT INTO {}.ingestion_runs (assets_requested) VALUES (%s) RETURNING run_id").format(
                sql.Identifier(config.SCHEMA)
            ),
            (assets_requested,),
        )
        run_id = cur.fetchone()[0]
    conn.commit()
    return run_id


def finish_run(conn, run_id: int, status: str, assets_ok: int, rows_upserted: int, message: str | None = None) -> None:
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                "UPDATE {}.ingestion_runs SET finished_at = NOW(), status = %s, "
                "assets_ok = %s, rows_upserted = %s, message = %s WHERE run_id = %s"
            ).format(sql.Identifier(config.SCHEMA)),
            (status, assets_ok, rows_upserted, message, run_id),
        )
    conn.commit()


def last_successful_run_age_hours(conn) -> float | None:
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                "SELECT EXTRACT(EPOCH FROM (NOW() - MAX(finished_at))) / 3600.0 "
                "FROM {}.ingestion_runs WHERE status IN ('success', 'partial')"
            ).format(sql.Identifier(config.SCHEMA))
        )
        value = cur.fetchone()[0]
    return None if value is None else float(value)


# ───────────────────────────── Clustering / ruptures ─────────────────────────────
def update_clusters(conn, assignments: list[dict]) -> None:
    if not assignments:
        return
    statement = sql.SQL(
        "UPDATE {}.assets AS a SET cluster_id = %s, cluster_label = %s, "
        "display_order = %s, updated_at = NOW() WHERE a.ticker = %s"
    ).format(sql.Identifier(config.SCHEMA))
    with conn.cursor() as cur:
        for item in assignments:
            cur.execute(
                statement,
                (
                    item["cluster_id"],
                    item["cluster_label"],
                    item["display_order"],
                    item["ticker"],
                ),
            )


def replace_breaks(conn, asset_id: int, window_days: int, method: str, breaks: list[dict]) -> None:
    schema = sql.Identifier(config.SCHEMA)
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                "DELETE FROM {}.correlation_breaks "
                "WHERE asset_id = %s AND window_days = %s"
            ).format(schema),
            (asset_id, window_days),
        )
        if breaks:
            execute_values(
                cur,
                sql.SQL(
                    "INSERT INTO {}.correlation_breaks "
                    "(asset_id, break_date, window_days, method, score) VALUES %s "
                    "ON CONFLICT (asset_id, break_date, window_days, method) DO UPDATE "
                    "SET score = EXCLUDED.score, created_at = NOW()"
                ).format(schema).as_string(conn),
                [(asset_id, b["break_date"], window_days, method, b.get("score")) for b in breaks],
                page_size=50,
            )


# ───────────────────────────── Requêtes d'analyse ─────────────────────────────
def fetch_pairs(conn, start: date, end: date, basis: str, min_obs: int) -> list[dict]:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            sql.SQL(
                "SELECT * FROM {}.corr_pairs(%s, %s, %s, %s)"
            ).format(sql.Identifier(config.SCHEMA)),
            (start, end, basis, min_obs),
        )
        return [dict(row) for row in cur.fetchall()]


def fetch_rolling_corr(
    conn,
    asset: str,
    benchmark: str,
    window: int,
    start: date,
    end: date,
    basis: str,
) -> list[dict]:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            sql.SQL(
                "SELECT * FROM {}.rolling_corr(%s, %s, %s, %s, %s, %s)"
            ).format(sql.Identifier(config.SCHEMA)),
            (asset, benchmark, window, start, end, basis),
        )
        return [dict(row) for row in cur.fetchall()]


def fetch_assets(conn) -> list[dict]:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            sql.SQL(
                "SELECT asset_id, ticker, name, universe, display_order "
                "FROM {}.assets ORDER BY display_order, ticker"
            ).format(sql.Identifier(config.SCHEMA))
        )
        return [dict(row) for row in cur.fetchall()]


def table_count(conn, table: str) -> int:
    if table not in ("assets", "prices_daily", "fx_daily", "ingestion_runs"):
        raise ValueError(f"Table non autorisée : {table!r}")
    with conn.cursor() as cur:
        cur.execute(sql.SQL("SELECT COUNT(*) FROM {}.{}").format(
            sql.Identifier(config.SCHEMA), sql.Identifier(table)
        ))
        return int(cur.fetchone()[0])


def schema_path() -> Path:
    return config.SCHEMA_FILE
