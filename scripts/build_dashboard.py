#!/usr/bin/env python3
"""Génère grafana/dashboard_finance.json.

Le JSON Grafana est volumineux et facile à casser à la main. Ce script le
construit à partir de briques réutilisables, puis l'écrit dans le dépôt.

    python scripts/build_dashboard.py
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "grafana" / "dashboard_finance.json"

DS = {"type": "postgres", "uid": "${DS_POSTGRESQL}"}

# Libellés lisibles des tickers affichés dans les matrices, les tableaux et
# les listes déroulantes. Le ticker reste la valeur technique utilisée par
# les requêtes SQL ; seul l'affichage est enrichi.
ASSET_LABELS: dict[str, str] = {
    "XLK": "Technologie",
    "XLV": "Santé",
    "XLF": "Secteur financier",
    "XLY": "Conso. discrétionnaire",
    "XLC": "Services de communication",
    "XLI": "Industrie",
    "XLP": "Consommation de base",
    "XLE": "Énergie",
    "XLU": "Services publics",
    "XLRE": "Immobilier",
    "XLB": "Matériaux",
    "URTH": "MSCI World (marché large)",
    "BTC": "Bitcoin",
    "GLD": "Or",
    "TLT": "Obligations 20 ans",
}

DEFAULT_ASSETS = ("XLK", "XLE", "XLU", "GLD", "TLT")


def ticker_label(ticker: str) -> str:
    """Retourne « TICKER — libellé », ou le ticker seul s'il n'a pas de libellé."""
    label = ASSET_LABELS.get(ticker)
    return f"{ticker} — {label}" if label else ticker


def sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def ticker_text_sql(column: str = "a.ticker") -> str:
    """Expression SQL qui renvoie le libellé lisible d'un ticker."""
    whens = " ".join(
        f"WHEN {sql_literal(ticker)} THEN {sql_literal(ticker_label(ticker))}"
        for ticker in ASSET_LABELS
    )
    return f"CASE {column} {whens} ELSE {column} END"


def ticker_value_mappings() -> list[dict]:
    """Mappings Grafana pour afficher les tickers avec leur libellé."""
    return [
        {
            "type": "value",
            "options": {
                ticker: {"index": index, "text": ticker_label(ticker)}
                for index, ticker in enumerate(ASSET_LABELS)
            },
        }
    ]


def ticker_label_override() -> dict:
    """Override de champ : transforme les valeurs asset/ticker/benchmark."""
    return {
        "matcher": {"id": "byRegexp", "options": "^(asset|benchmark|ticker)$"},
        "properties": [{"id": "mappings", "value": ticker_value_mappings()}],
    }


def ticker_width_override(width: int) -> dict:
    """Élargit la colonne des libellés pour éviter les noms tronqués."""
    return {
        "matcher": {"id": "byRegexp", "options": "^(asset|ticker)$"},
        "properties": [
            {"id": "custom.width", "value": width},
            {"id": "custom.wrapText", "value": True},
        ],
    }


def ticker_rename_transformations() -> list[dict]:
    """Transformations qui renomment les colonnes XLK, XLV, etc."""
    return [
        {
            "id": "renameByRegex",
            "options": {
                "regex": f"^{re.escape(ticker)}$",
                "renamePattern": ticker_label(ticker),
            },
        }
        for ticker in ASSET_LABELS
    ]


def rows_to_fields(name_field: str, value_field: str, ignored: tuple[str, ...] = ()) -> dict:
    """Transforme une ligne par actif en un champ par actif (bar gauge)."""
    mappings = [
        {"fieldName": name_field, "handlerKey": "field.name"},
        {"fieldName": value_field, "handlerKey": "field.value"},
    ]
    mappings.extend({"fieldName": field, "handlerKey": "__ignore"} for field in ignored)
    return {"id": "rowsToFields", "options": {"mappings": mappings}}

CORR_THRESHOLDS = {
    "mode": "absolute",
    "steps": [
        {"color": "dark-red", "value": None},
        {"color": "red", "value": -0.6},
        {"color": "orange", "value": -0.3},
        {"color": "gray", "value": -0.1},
        {"color": "light-blue", "value": 0.1},
        {"color": "blue", "value": 0.3},
        {"color": "dark-blue", "value": 0.6},
        {"color": "purple", "value": 0.8},
    ],
}


def target(sql: str, fmt: str = "table", ref: str = "A") -> dict:
    return {"format": fmt, "rawSql": sql, "refId": ref}


def stat_panel(
    panel_id: int,
    title: str,
    sql: str,
    grid: dict,
    description: str = "",
    unit: str = "none",
) -> dict:
    return {
        "id": panel_id,
        "title": title,
        "type": "stat",
        "gridPos": grid,
        "datasource": DS,
        "targets": [target(sql)],
        "options": {
            "colorMode": "none",
            "graphMode": "none",
            "justifyMode": "center",
            "orientation": "auto",
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "textMode": "auto",
        },
        "fieldConfig": {
            "defaults": {
                "mappings": [],
                "thresholds": {"mode": "absolute", "steps": [{"color": "text", "value": None}]},
                "unit": unit,
            },
            "overrides": [],
        },
        "description": description,
    }


def table_panel(
    panel_id: int,
    title: str,
    sql: str,
    grid: dict,
    description: str = "",
    correlation_colors: bool = False,
    unit: str = "none",
    decimals: int = 2,
    overrides: list | None = None,
    sort_by: str = "asset",
    rename_ticker_columns: bool = False,
    label_width: int | None = None,
) -> dict:
    defaults = {
        "custom": {
            "align": "center",
            "displayMode": "color-background" if correlation_colors else "auto",
            "inspect": False,
            "wrapHeaderText": True,
        },
        "decimals": decimals,
        "unit": unit,
    }
    if correlation_colors:
        defaults["thresholds"] = CORR_THRESHOLDS
        defaults["min"] = -1
        defaults["max"] = 1
    panel_overrides = [ticker_label_override()]
    if label_width is not None:
        panel_overrides.append(ticker_width_override(label_width))
    panel_overrides.extend(overrides or [])
    return {
        "id": panel_id,
        "title": title,
        "type": "table",
        "gridPos": grid,
        "datasource": DS,
        "targets": [target(sql)],
        "options": {
            "showHeader": True,
            "sortBy": [{"displayName": sort_by, "desc": False}],
        },
        "fieldConfig": {"defaults": defaults, "overrides": panel_overrides},
        "transformations": ticker_rename_transformations() if rename_ticker_columns else [],
        "description": description,
    }


def timeseries_panel(
    panel_id: int,
    title: str,
    sql: str,
    grid: dict,
    description: str = "",
    rename_ticker_columns: bool = False,
) -> dict:
    return {
        "id": panel_id,
        "title": title,
        "type": "timeseries",
        "gridPos": grid,
        "datasource": DS,
        "targets": [target(sql, "time_series")],
        "options": {
            "legend": {"displayMode": "list", "placement": "bottom", "calcs": ["lastNotNull"]},
            "tooltip": {"mode": "multi", "sort": "none"},
        },
        "fieldConfig": {
            "defaults": {
                "custom": {
                    "drawStyle": "line",
                    "lineInterpolation": "linear",
                    "lineWidth": 1,
                    "pointSize": 2,
                    "showPoints": "never",
                    "spanNulls": False,
                    "fillOpacity": 0,
                    "gradientMode": "none",
                    "stacking": {"mode": "none", "group": "A"},
                    "axisPlacement": "auto",
                    "axisLabel": "",
                    "scaleDistribution": {"type": "linear"},
                },
                "decimals": 2,
                "min": -1,
                "max": 1,
                "unit": "none",
                "thresholds": {"mode": "absolute", "steps": [{"color": "green", "value": None}]},
            },
            "overrides": [],
        },
        "transformations": ticker_rename_transformations() if rename_ticker_columns else [],
        "description": description,
    }


def bargauge_panel(
    panel_id: int,
    title: str,
    sql: str,
    grid: dict,
    description: str = "",
    transformations: list | None = None,
) -> dict:
    return {
        "id": panel_id,
        "title": title,
        "type": "bargauge",
        "gridPos": grid,
        "datasource": DS,
        "targets": [target(sql)],
        "options": {
            "orientation": "horizontal",
            "displayMode": "gradient",
            "showUnfilled": True,
            "valueMode": "color",
            "namePlacement": "auto",
            "sizing": "auto",
            "minVizHeight": 16,
            "maxVizHeight": 300,
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
        },
        "fieldConfig": {
            "defaults": {
                "decimals": 2,
                "min": -1,
                "max": 1,
                "unit": "none",
                "thresholds": CORR_THRESHOLDS,
            },
            "overrides": [],
        },
        "transformations": transformations or [],
        "description": description,
    }


def text_panel(panel_id: int, title: str, content: str, grid: dict) -> dict:
    return {
        "id": panel_id,
        "title": title,
        "type": "text",
        "gridPos": grid,
        "options": {"mode": "markdown", "content": content},
        "fieldConfig": {"defaults": {}, "overrides": []},
    }


# ───────────────────────────── Requêtes ─────────────────────────────
MATRIX_ARGS = "($__timeFrom())::date, ($__timeTo())::date, '$basis', $min_obs"

PANELS = [
    stat_panel(
        1,
        "Dernière séance",
        'SELECT EXTRACT(EPOCH FROM max(last_trade_date)) * 1000 AS "Dernière séance" FROM finance.v_freshness;',
        {"h": 4, "w": 6, "x": 0, "y": 0},
        "Date du dernier cours disponible, tous actifs confondus.",
        unit="time:YYYY-MM-DD",
    ),
    stat_panel(
        2,
        "Actifs suivis",
        "SELECT count(*) AS \"Actifs\" FROM finance.assets;",
        {"h": 4, "w": 6, "x": 6, "y": 0},
        "15 actifs core, 4 actions, 3 ETF tech/semi.",
        unit="short",
    ),
    stat_panel(
        3,
        "Dernier import",
        "SELECT EXTRACT(EPOCH FROM max(finished_at)) * 1000 AS \"Dernier import\" "
        "FROM finance.ingestion_runs WHERE status IN ('success', 'partial');",
        {"h": 4, "w": 6, "x": 12, "y": 0},
        "Dernier run ETL terminé avec succès (ou partiellement).",
        unit="time:YYYY-MM-DD HH:mm:ss",
    ),
    stat_panel(
        4,
        "Paires valides",
        f"SELECT count(*) AS \"Paires\" FROM finance.corr_pairs({MATRIX_ARGS});",
        {"h": 4, "w": 6, "x": 18, "y": 0},
        "Nombre de paires disposant d'assez d'observations sur la période.",
        unit="short",
    ),
    table_panel(
        5,
        "Matrice de corrélation — 15 actifs core",
        f"SELECT * FROM finance.corr_matrix_core({MATRIX_ARGS});",
        {"h": 16, "w": 24, "x": 0, "y": 4},
        "Corrélation de Pearson des rendements logarithmiques, en EUR par défaut. "
        "Chaque cellule est calculée sur les dates communes aux deux actifs ; "
        "les paires sans assez d'observations restent vides. "
        "Les lignes et les colonnes combinent le ticker et sa classe d'actif "
        "(ex. « XLK — Technologie »).",
        correlation_colors=True,
        rename_ticker_columns=True,
        label_width=300,
    ),
    bargauge_panel(
        6,
        "Corrélations avec URTH",
        "SELECT asset, correlation, n_obs, ci_low, ci_high "
        f"FROM finance.corr_benchmark(($__timeFrom())::date, ($__timeTo())::date, '$basis', 'URTH', $min_obs) "
        "ORDER BY correlation DESC;",
        {"h": 12, "w": 9, "x": 0, "y": 20},
        "Classement des actifs par corrélation avec le MSCI World. "
        "Chaque barre est libellée ticker + classe d'actif. "
        "La corrélation est une mesure de co-mouvement, pas de performance.",
        transformations=[rows_to_fields("asset", "correlation", ("n_obs", "ci_low", "ci_high"))]
        + ticker_rename_transformations(),
    ),
    table_panel(
        7,
        "Matrice 3 × 3 — ETF tech/semi",
        f"SELECT * FROM finance.corr_matrix_tech({MATRIX_ARGS});",
        {"h": 12, "w": 7, "x": 9, "y": 20},
        "Comparateurs pertinents pour ASML et BESI : SMH, SOXX et VGT.",
        correlation_colors=True,
    ),
    table_panel(
        8,
        "Bêta et R² — ${stock:text} vs ${etf:text}",
        "SELECT asset, benchmark, beta, r2, correlation, n_obs "
        "FROM finance.beta_vs_benchmark('$stock', '$etf', "
        "($__timeFrom())::date, ($__timeTo())::date, '$basis');",
        {"h": 12, "w": 8, "x": 16, "y": 20},
        "Bêta = pente de l'action contre l'ETF ; R² = part de variance expliquée. "
        "Une corrélation forte avec une bêta faible signifie un co-mouvement peu amplifié.",
    ),
    table_panel(
        9,
        "Matrice — 4 actions × 15 ETF",
        f"SELECT * FROM finance.corr_matrix_stocks({MATRIX_ARGS});",
        {"h": 9, "w": 24, "x": 0, "y": 32},
        "ASML, BESI, NOVO et SMA Solar contre les 15 actifs core. "
        "Les colonnes combinent le ticker et sa classe d'actif. "
        "Les corrélations sont généralement plus faibles que dans la matrice ETF : "
        "les ETF SPDR sont US et ne contiennent pas nécessairement ces valeurs européennes.",
        correlation_colors=True,
        rename_ticker_columns=True,
    ),
    timeseries_panel(
        10,
        "Corrélations glissantes vs URTH — fenêtre $window",
        "SELECT trade_date::timestamptz AS time, asset AS metric, correlation AS value "
        "FROM finance.rolling_corr_all('URTH', $window, "
        "($__timeFrom())::date, ($__timeTo())::date, '$basis') "
        "WHERE asset IN ($assets) ORDER BY trade_date;",
        {"h": 12, "w": 14, "x": 0, "y": 41},
        "Corrélation calculée sur les N dernières observations communes avec URTH. "
        "Fenêtres 63 / 126 / 252 séances. Les fenêtres longues sont plus stables, "
        "les courtes réagissent plus vite. Les courbes sont libellées ticker + classe d'actif.",
        rename_ticker_columns=True,
    ),
    timeseries_panel(
        11,
        "Corrélation glissante — ${stock:text} vs ${etf:text}",
        "SELECT trade_date::timestamptz AS time, correlation AS value "
        "FROM finance.rolling_corr('$stock', '$etf', $window, "
        "($__timeFrom())::date, ($__timeTo())::date, '$basis') ORDER BY trade_date;",
        {"h": 12, "w": 10, "x": 14, "y": 41},
        "Suivi dans le temps de la corrélation entre une action et un ETF. "
        "Comparer la ligne à la corrélation pleine période pour repérer un régime.",
    ),
    table_panel(
        12,
        "Corrélation conditionnelle en stress",
        "SELECT asset, correlation_stress, n_stress, correlation_all, n_all "
        "FROM finance.stress_corr('URTH', -0.01, "
        "($__timeFrom())::date, ($__timeTo())::date, '$basis', 20) "
        "ORDER BY correlation_stress DESC;",
        {"h": 10, "w": 12, "x": 0, "y": 53},
        "Corrélation uniquement les jours où URTH perd plus de 1 %, contre la corrélation "
        "sur toute la période. Les corrélations montent souvent dans les baisses : "
        "un actif diversifiant en temps normal peut ne plus l'être en stress.",
        correlation_colors=True,
        label_width=260,
        overrides=[
            {
                "matcher": {"id": "byName", "options": "n_stress"},
                "properties": [{"id": "custom.displayMode", "value": "auto"}],
            },
            {
                "matcher": {"id": "byName", "options": "n_all"},
                "properties": [{"id": "custom.displayMode", "value": "auto"}],
            },
        ],
    ),
    table_panel(
        13,
        "Statistiques par actif",
        "SELECT ticker, name, category, n_obs, annualized_vol, cumulative_return, max_drawdown "
        "FROM finance.v_asset_stats ORDER BY display_order;",
        {"h": 10, "w": 12, "x": 12, "y": 53},
        "Volatilité annualisée, rendement cumulé et drawdown maximal, calculés en EUR "
        "sur tout l'historique disponible (pas seulement la fenêtre Grafana).",
        sort_by="ticker",
        label_width=260,
        overrides=[
            {
                "matcher": {"id": "byName", "options": "annualized_vol"},
                "properties": [{"id": "unit", "value": "percentunit"}],
            },
            {
                "matcher": {"id": "byName", "options": "cumulative_return"},
                "properties": [{"id": "unit", "value": "percentunit"}],
            },
            {
                "matcher": {"id": "byName", "options": "max_drawdown"},
                "properties": [{"id": "unit", "value": "percentunit"}],
            },
        ],
    ),
    table_panel(
        16,
        "Groupes de corrélation",
        "SELECT ticker, cluster_id, cluster_label, universe "
        "FROM finance.assets ORDER BY cluster_id, display_order;",
        {"h": 7, "w": 10, "x": 0, "y": 63},
        "Clustering hiérarchique (average linkage sur 1 - corrélation), recalculé "
        "à chaque ETL sur les 3 dernières années. Les groupes changent avec la période : "
        "c'est une aide à la lecture, pas une classification officielle.",
        sort_by="ticker",
        label_width=200,
    ),
    table_panel(
        14,
        "Fraîcheur par actif",
        "SELECT ticker, name, universe, last_trade_date, days_since_last_trade, "
        "hours_since_last_run FROM finance.v_freshness ORDER BY display_order;",
        {"h": 7, "w": 14, "x": 10, "y": 63},
        "Contrôle opérationnel : âge de la dernière séance et du dernier import. "
        "Un actif européen peut avoir un jour de retard sur URTH selon les fériés.",
        sort_by="ticker",
        label_width=260,
        overrides=[
            {
                "matcher": {"id": "byName", "options": "days_since_last_trade"},
                "properties": [
                    {"id": "custom.displayMode", "value": "color-background"},
                    {
                        "id": "thresholds",
                        "value": {
                            "mode": "absolute",
                            "steps": [
                                {"color": "green", "value": None},
                                {"color": "orange", "value": 5},
                                {"color": "red", "value": 10},
                            ],
                        },
                    },
                ],
            }
        ],
    ),
    table_panel(
        17,
        "Corrélations annuelles vs URTH",
        "SELECT year, asset, correlation, n_obs "
        "FROM finance.corr_by_year(($__timeFrom())::date, ($__timeTo())::date, '$basis', 'URTH', 20) "
        "WHERE asset IN ($assets) ORDER BY year, asset;",
        {"h": 10, "w": 24, "x": 0, "y": 70},
        "Corrélation par année civile avec URTH. Permet de voir les grands régimes "
        "(COVID, resserrement 2022, rallye IA). Les années avec moins de 20 observations "
        "sont ignorées ; n_obs est affiché.",
        correlation_colors=True,
        sort_by="year",
        label_width=260,
    ),
    text_panel(
        15,
        "Méthodologie et limites",
        """
## Ce que mesure ce dashboard

Les corrélations portent sur les **rendements logarithmiques quotidiens** :
`r_t = ln(P_t / P_{t-1})`, où `P_t` est la clôture ajustée (splits et dividendes).
Deux bases sont disponibles :

* **EUR** (défaut) : conversion au taux de change du jour, représentative d'un
  investisseur européen ;
* **LOCAL** : devise de cotation, pour isoler l'effet action de l'effet change.

La corrélation de Pearson est calculée **par paire**, sur les dates où les deux
actifs ont une observation. Les paires sans assez d'observations sont masquées.
L'intervalle de confiance à 95 % utilise la transformation de Fisher.

## Fenêtres et régimes

Les fenêtres glissantes 63 / 126 / 252 séances correspondent à environ 3, 6 et
12 mois. Une fenêtre courte réagit vite mais est bruitée ; une fenêtre longue
est plus stable mais amortit les changements. Les **snapshots mensuels** et les
**corrélations annuelles** sont disponibles via les fonctions SQL.

Le clustering hiérarchique (average linkage sur `1 - corrélation`) identifie des
groupes et ordonne les listes. La matrice conserve l'ordre de référence pour que
les lignes et les colonnes coïncident : une matrice doit rester lisible en
diagonale. Les groupes sont visibles dans le panneau « Groupes de corrélation ».
Ils ne sont pas une classification officielle : c'est une aide à la lecture.

## Bêta, R² et stress

* **Bêta** : pente de régression de l'action sur l'ETF. Bêta > 1 = amplifie les
  mouvements de l'ETF ; bêta < 1 = les amortit.
* **R²** : part de la variance de l'action expliquée par l'ETF.
* **Corrélation de stress** : calculée uniquement les jours où URTH perd plus
  de 1 %. Elle peut être très différente de la corrélation moyenne.

## Précautions

* Corrélation ≠ causalité. Deux actifs peuvent être corrélés parce qu'ils
  partagent un facteur (marché, taux, dollar, secteur) ou par coïncidence.
* Les ETF SPDR sont **américains** : une action européenne peut avoir une
  corrélation faible simplement parce qu'elle n'est pas dans l'indice.
* Les fenêtres glissantes se chevauchent : les points ne sont pas indépendants.
* Les tests multiples sont nombreux (15 × 15 paires) : ne pas surinterpréter une
  corrélation isolée à 0,3.
* Les données Yahoo sont révisables (dividendes, splits). L'ETL remplace
  l'historique complet à chaque passage pour éviter les prix figés.
* Les annotations « ruptures » sont indicatives : `ruptures` (PELT/Binseg) ou,
  à défaut, une segmentation binaire interne. Ce n'est pas un test statistique
  de causalité.
""",
        {"h": 14, "w": 24, "x": 0, "y": 80},
    ),
]

VARIABLES = [
    {
        "current": {
            "selected": True,
            "text": "Finance-PostgreSQL",
            "value": "finance-postgresql",
        },
        "hide": 0,
        "includeAll": False,
        "label": "Datasource",
        "multi": False,
        "name": "DS_POSTGRESQL",
        "options": [],
        "query": "grafana-postgresql-datasource",
        "refresh": 1,
        "regex": "",
        "skipUrlSync": False,
        "type": "datasource",
    },
    {
        "current": {"selected": True, "text": "EUR", "value": "EUR"},
        "hide": 0,
        "includeAll": False,
        "label": "Base de calcul",
        "multi": False,
        "name": "basis",
        "options": [
            {"selected": True, "text": "EUR", "value": "EUR"},
            {"selected": False, "text": "LOCAL", "value": "LOCAL"},
        ],
        "query": "EUR,LOCAL",
        "skipUrlSync": False,
        "type": "custom",
    },
    {
        "current": {"selected": True, "text": "126", "value": "126"},
        "hide": 0,
        "includeAll": False,
        "label": "Fenêtre glissante (séances)",
        "multi": False,
        "name": "window",
        "options": [
            {"selected": False, "text": "63", "value": "63"},
            {"selected": True, "text": "126", "value": "126"},
            {"selected": False, "text": "252", "value": "252"},
        ],
        "query": "63,126,252",
        "skipUrlSync": False,
        "type": "custom",
    },
    {
        "current": {"selected": True, "text": "60", "value": "60"},
        "hide": 0,
        "includeAll": False,
        "label": "Observations minimales",
        "multi": False,
        "name": "min_obs",
        "options": [
            {"selected": False, "text": "30", "value": "30"},
            {"selected": True, "text": "60", "value": "60"},
            {"selected": False, "text": "120", "value": "120"},
            {"selected": False, "text": "252", "value": "252"},
        ],
        "query": "30,60,120,252",
        "skipUrlSync": False,
        "type": "custom",
    },
    {
        "current": {"selected": True, "text": "ASML", "value": "ASML"},
        "datasource": DS,
        "hide": 0,
        "includeAll": False,
        "label": "Action",
        "multi": False,
        "name": "stock",
        "options": [],
        "query": f"SELECT {ticker_text_sql('a.ticker')} AS __text, a.ticker AS __value "
        "FROM finance.assets a WHERE a.universe = 'stock' ORDER BY a.ticker;",
        "refresh": 1,
        "regex": "",
        "skipUrlSync": False,
        "sort": 1,
        "type": "query",
    },
    {
        "current": {"selected": True, "text": "SMH", "value": "SMH"},
        "datasource": DS,
        "hide": 0,
        "includeAll": False,
        "label": "ETF comparateur",
        "multi": False,
        "name": "etf",
        "options": [],
        "query": f"SELECT {ticker_text_sql('a.ticker')} AS __text, a.ticker AS __value "
        "FROM finance.assets a WHERE a.asset_type = 'etf' "
        "ORDER BY a.universe, a.ticker;",
        "refresh": 1,
        "regex": "",
        "skipUrlSync": False,
        "sort": 1,
        "type": "query",
    },
    {
        "current": {
            "selected": True,
            "text": [ticker_label(ticker) for ticker in DEFAULT_ASSETS],
            "value": list(DEFAULT_ASSETS),
        },
        "datasource": DS,
        "hide": 0,
        "includeAll": False,
        "label": "Actifs (courbes)",
        "multi": True,
        "name": "assets",
        "options": [
            {"selected": True, "text": ticker_label(ticker), "value": ticker}
            for ticker in DEFAULT_ASSETS
        ],
        "query": f"SELECT {ticker_text_sql('a.ticker')} AS __text, a.ticker AS __value "
        "FROM finance.assets a WHERE a.universe = 'core' ORDER BY a.display_order;",
        "refresh": 1,
        "regex": "",
        "skipUrlSync": False,
        "sort": 0,
        "type": "query",
    },
]

ANNOTATIONS = {
    "list": [
        {
            "builtIn": 1,
            "datasource": {"type": "grafana", "uid": "-- Grafana --"},
            "enable": True,
            "hide": True,
            "iconColor": "rgba(0, 211, 255, 1)",
            "name": "Annotations & Alerts",
            "type": "dashboard",
        },
        {
            "datasource": DS,
            "enable": True,
            "iconColor": "red",
            "name": "Événements de marché",
            "target": {
                "format": "table",
                "rawQuery": True,
                "rawSql": "SELECT event_date::timestamptz AS time, title AS text, "
                "category AS tags FROM finance.events "
                "WHERE $__timeFilter(event_date) ORDER BY event_date;",
                "refId": "Anno",
            },
        },
        {
            "datasource": DS,
            "enable": True,
            "iconColor": "orange",
            "name": "Ruptures de corrélation",
            "target": {
                "format": "table",
                "rawQuery": True,
                "rawSql": "SELECT b.break_date::timestamptz AS time, "
                f"{ticker_text_sql('a.ticker')} || ' — rupture ' || b.method AS text, "
                "'break' AS tags "
                "FROM finance.correlation_breaks b "
                "JOIN finance.assets a USING (asset_id) "
                "WHERE $__timeFilter(b.break_date) ORDER BY b.break_date;",
                "refId": "Anno",
            },
        },
    ]
}

DASHBOARD = {
    "annotations": ANNOTATIONS,
    "editable": True,
    "graphTooltip": 1,
    "id": None,
    "links": [],
    "panels": PANELS,
    "refresh": "1h",
    "schemaVersion": 30,
    "tags": ["finance", "correlation", "etf"],
    "templating": {"list": VARIABLES},
    "time": {"from": "2018-07-01", "to": "now"},
    "timepicker": {},
    "timezone": "browser",
    "title": "Finance — Corrélations ETF & actions",
    "uid": "finance-correlation",
    "version": 1,
}


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(DASHBOARD, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Dashboard écrit : {OUT} ({len(PANELS)} panneaux)")


if __name__ == "__main__":
    main()
