"""Configuration du Finance Tracker.

Toute la configuration passe par des variables d'environnement. Un fichier
`.env` à la racine du dépôt est chargé automatiquement s'il existe ; il est
ignoré par Git (voir .gitignore). Aucun secret n'est présent dans le code.
"""

from __future__ import annotations

import os
import re
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SQL_DIR = ROOT / "sql"
SCHEMA_FILE = SQL_DIR / "10_schema.sql"


def _load_dotenv() -> None:
    """Charge .env sans écraser les variables déjà définies dans l'environnement."""
    path = ROOT / ".env"
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


_load_dotenv()


def _str(name: str, default: str) -> str:
    value = os.getenv(name)
    return default if value is None or not value.strip() else value.strip()


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise SystemExit(f"ERROR : {name} doit être un entier (reçu : {raw!r}).") from exc


def _float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise SystemExit(f"ERROR : {name} doit être un nombre (reçu : {raw!r}).") from exc


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in ("1", "true", "yes", "oui", "on")


def _iso_date(name: str, default: date) -> date:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return date.fromisoformat(raw.strip())
    except ValueError as exc:
        raise SystemExit(
            f"ERROR : {name} doit être une date ISO (AAAA-MM-JJ), reçu : {raw!r}."
        ) from exc


# Base de données. En production sur le Raspberry Pi :
#   PGDATABASE=marine_database, PGUSER=finance, PGPASSWORD=...
# En développement local, un .env peut pointer vers la base `finance` et le
# rôle `oryx` (authentification peer, sans mot de passe).
DB_CONFIG = {
    "host": _str("PGHOST", "localhost"),
    "port": _int("PGPORT", 5432),
    "dbname": _str("PGDATABASE", "finance"),
    "user": _str("PGUSER", "finance"),
    "password": os.getenv("PGPASSWORD") or None,
}

SCHEMA = _str("FINANCE_SCHEMA", "finance")
if not re.fullmatch(r"[a-z_][a-z0-9_]*", SCHEMA):
    raise SystemExit(f"ERROR : FINANCE_SCHEMA invalide : {SCHEMA!r}")

START_DATE = _iso_date("FINANCE_START_DATE", date(2015, 1, 1))
END_DATE = _iso_date("FINANCE_END_DATE", date.today())
BASE_CURRENCY = _str("FINANCE_BASE_CURRENCY", "EUR").upper()
if BASE_CURRENCY not in ("EUR", "LOCAL"):
    raise SystemExit("ERROR : FINANCE_BASE_CURRENCY doit être EUR ou LOCAL.")

MIN_OBS = _int("FINANCE_MIN_OBS", 60)
MIN_IMPORT_INTERVAL_HOURS = _float("FINANCE_MIN_IMPORT_INTERVAL_HOURS", 18.0)
FORCE_IMPORT = _bool("FINANCE_FORCE_IMPORT", False)

HTTP_TIMEOUT = _int("FINANCE_HTTP_TIMEOUT", 30)
HTTP_RETRIES = _int("FINANCE_HTTP_RETRIES", 3)
HTTP_DELAY = _float("FINANCE_HTTP_DELAY", 0.4)
USER_AGENT = _str(
    "FINANCE_USER_AGENT",
    "Finance-Tracker/1.0 (+https://github.com/GoyhexVincent/Finance_tracker)",
)
YAHOO_CHART_URL = _str(
    "FINANCE_YAHOO_CHART_URL",
    "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
)

# Analyse
CLUSTER_K = _int("FINANCE_CLUSTER_K", 5)
CLUSTER_LOOKBACK_YEARS = _int("FINANCE_CLUSTER_LOOKBACK_YEARS", 3)
BREAK_WINDOW = _int("FINANCE_BREAK_WINDOW", 126)
BREAK_MAX = _int("FINANCE_BREAK_MAX", 3)
BREAK_MIN_SIZE = _int("FINANCE_BREAK_MIN_SIZE", 252)

LOG_LEVEL = _str("FINANCE_LOG_LEVEL", "INFO").upper()


def require_remote_password() -> None:
    """Sur une base distante, impose un mot de passe explicite."""
    if DB_CONFIG["password"]:
        return
    host = str(DB_CONFIG["host"])
    if host not in ("localhost", "127.0.0.1", "::1") and not host.startswith("/"):
        raise SystemExit(
            "ERROR : PGPASSWORD n'est pas défini pour une connexion distante "
            f"(PGHOST={host})."
        )
