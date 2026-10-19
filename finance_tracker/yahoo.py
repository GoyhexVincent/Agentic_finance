"""Adaptateur Yahoo Finance.

On utilise l'endpoint public `v8/finance/chart` plutôt que yfinance : il ne
requiert ni pandas ni numpy, ce qui garde l'installation légère sur le
Raspberry Pi. Le parsing est isolé ici pour pouvoir changer de source
(Stooq, yfinance, Alpha Vantage…) sans toucher à l'ETL.

Réponse utile :
  chart.result[0].timestamp
  chart.result[0].indicators.quote[0].{open,high,low,close,volume}
  chart.result[0].indicators.adjclose[0].adjclose
  chart.result[0].meta.{symbol,currency,exchangeTimezoneName,...}

Les timestamps sont convertis dans le fuseau de la place de cotation, pas en
UTC : une séance américaine qui ouvre à 09:30 EST ne doit pas être datée de la
veille parce que le serveur est en Europe.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime, time as dtime, timedelta, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import config
from .assets import Asset, FX_PAIRS

logger = logging.getLogger(__name__)


class YahooError(RuntimeError):
    """Erreur de téléchargement ou de parsing Yahoo."""


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    return number


def _as_int(value: Any) -> int | None:
    number = _as_float(value)
    return None if number is None else int(number)


def _at(values: list[Any] | None, index: int) -> Any:
    if not values or index >= len(values):
        return None
    return values[index]


def _period_bounds(start: date, end: date) -> tuple[int, int]:
    """Convertit une plage de dates en timestamps UNIX (période exclusive)."""
    start_dt = datetime.combine(start, dtime.min, tzinfo=timezone.utc)
    end_dt = datetime.combine(end + timedelta(days=1), dtime.min, tzinfo=timezone.utc)
    return int(start_dt.timestamp()), int(end_dt.timestamp())


def _timezone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning("Fuseau Yahoo inconnu (%s), repli sur UTC.", name)
        return ZoneInfo("UTC")


def fetch_chart(symbol: str, start: date, end: date) -> dict:
    """Télécharge la réponse brute Yahoo pour un symbole."""
    period1, period2 = _period_bounds(start, end)
    query = urlencode(
        {
            "period1": period1,
            "period2": period2,
            "interval": "1d",
            "events": "div,splits",
            "includeAdjustedClose": "true",
        }
    )
    url = config.YAHOO_CHART_URL.format(symbol=quote(symbol, safe="")) + "?" + query
    headers = {
        "User-Agent": config.USER_AGENT,
        "Accept": "application/json",
    }

    last_error: Exception | None = None
    for attempt in range(1, config.HTTP_RETRIES + 1):
        try:
            request = Request(url, headers=headers)
            with urlopen(request, timeout=config.HTTP_TIMEOUT) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            last_error = exc
            if exc.code == 404:
                raise YahooError(f"Symbole introuvable sur Yahoo : {symbol} (404)") from exc
            if exc.code not in (429, 500, 502, 503, 504):
                raise YahooError(f"HTTP {exc.code} sur {symbol}") from exc
            logger.warning("HTTP %s sur %s (tentative %s/%s)", exc.code, symbol, attempt, config.HTTP_RETRIES)
        except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            last_error = exc
            logger.warning("Téléchargement %s impossible : %s (tentative %s/%s)",
                           symbol, exc, attempt, config.HTTP_RETRIES)
        if attempt < config.HTTP_RETRIES:
            time.sleep(config.HTTP_DELAY * (2 ** (attempt - 1)))
    raise YahooError(f"Échec du téléchargement de {symbol} : {last_error}")


def parse_chart(payload: dict, expected_symbol: str | None = None) -> tuple[dict, list[dict]]:
    """Parse une réponse chart en (meta, barres quotidiennes triées)."""
    chart = payload.get("chart") or {}
    error = chart.get("error")
    if error:
        description = error.get("description") if isinstance(error, dict) else str(error)
        raise YahooError(f"Réponse Yahoo en erreur : {description}")

    results = chart.get("result") or []
    if not results:
        raise YahooError("Réponse Yahoo vide (chart.result absent).")

    result = results[0]
    meta = result.get("meta") or {}
    symbol = meta.get("symbol")
    if expected_symbol and symbol and symbol.upper() != expected_symbol.upper():
        raise YahooError(
            f"Symbole inattendu : demandé {expected_symbol}, reçu {symbol}. "
            "Vérifier la table des correspondances."
        )

    timestamps = result.get("timestamp") or []
    indicators = result.get("indicators") or {}
    quote_blocks = indicators.get("quote") or []
    if not timestamps or not quote_blocks:
        return meta, []

    quote = quote_blocks[0] or {}
    adj_blocks = indicators.get("adjclose") or []
    adjclose = (adj_blocks[0] or {}).get("adjclose") if adj_blocks else None

    tz = _timezone(meta.get("exchangeTimezoneName"))
    by_date: dict[date, dict] = {}
    for index, timestamp in enumerate(timestamps):
        try:
            day = datetime.fromtimestamp(int(timestamp), tz=tz).date()
        except (TypeError, ValueError, OSError):
            continue

        close = _as_float(_at(quote.get("close"), index))
        if close is None or close <= 0:
            # Une séance sans clôture n'est pas exploitable. On ne fabrique pas
            # de prix par interpolation.
            continue

        adjusted = _as_float(_at(adjclose, index))
        if adjusted is None or adjusted <= 0:
            adjusted = close

        by_date[day] = {
            "trade_date": day,
            "open": _as_float(_at(quote.get("open"), index)),
            "high": _as_float(_at(quote.get("high"), index)),
            "low": _as_float(_at(quote.get("low"), index)),
            "close": close,
            "adj_close": adjusted,
            "volume": _as_int(_at(quote.get("volume"), index)),
            "currency": meta.get("currency"),
        }

    rows = sorted(by_date.values(), key=lambda row: row["trade_date"])
    return meta, rows


def fetch_asset(asset: Asset, start: date, end: date) -> tuple[dict, list[dict]]:
    """Télécharge et parse un actif."""
    payload = fetch_chart(asset.yahoo_symbol, start, end)
    meta, rows = parse_chart(payload, expected_symbol=asset.yahoo_symbol)
    expected_currency = asset.currency
    actual_currency = (meta.get("currency") or "").upper()
    if actual_currency and actual_currency != expected_currency:
        raise YahooError(
            f"Devise inattendue pour {asset.ticker} : attendu {expected_currency}, "
            f"reçu {actual_currency}."
        )
    return meta, rows


def fetch_fx(currency: str, start: date, end: date) -> list[dict]:
    """Télécharge un taux de change et le convertit en EUR par unité.

    Yahoo cote EURUSD=X en USD pour 1 EUR. Pour obtenir des EUR par USD :
        eur_per_unit = 1 / close
    """
    pair = FX_PAIRS.get(currency)
    if pair is None:
        raise ValueError(f"Pas de paire FX configurée pour {currency}")

    payload = fetch_chart(pair, start, end)
    meta, rows = parse_chart(payload, expected_symbol=pair)
    out: list[dict] = []
    for row in rows:
        close = row.get("close")
        if close is None or close <= 0:
            continue
        out.append(
            {
                "currency": currency,
                "trade_date": row["trade_date"],
                "eur_per_unit": 1.0 / close,
                "source": "yahoo",
            }
        )
    if not out:
        raise YahooError(f"Aucun taux exploitable pour {pair}")
    return out
