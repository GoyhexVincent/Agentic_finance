"""Référentiel des actifs suivis.

La liste est volontairement explicite : elle sert de source de vérité pour
l'ETL, les tests et la documentation. Pour ajouter un actif, ajouter une
entrée ici puis relancer `python -m finance_tracker.etl`.

Univers :
  core  : les 15 actifs du script R correlations_etf.R
  stock : les 4 actions ciblées (ASML, BESI, NOVO, SMA Solar)
  tech  : les 3 ETF tech/semi complémentaires (SMH, SOXX, VGT)
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Asset:
    ticker: str
    yahoo_symbol: str
    name: str
    asset_type: str          # etf | equity | crypto
    category: str
    universe: str            # core | stock | tech
    currency: str
    exchange: str
    display_order: int
    is_benchmark: bool = False
    metadata: dict = field(default_factory=dict)


ASSETS: tuple[Asset, ...] = (
    # ── 11 secteurs SPDR ──────────────────────────────────────────────────
    Asset("XLK", "XLK", "Technology Select Sector SPDR", "etf", "technology", "core", "USD", "PCX", 10,
          metadata={"issuer": "State Street", "region": "US"}),
    Asset("XLV", "XLV", "Health Care Select Sector SPDR", "etf", "health_care", "core", "USD", "PCX", 20,
          metadata={"issuer": "State Street", "region": "US"}),
    Asset("XLF", "XLF", "Financial Select Sector SPDR", "etf", "financials", "core", "USD", "PCX", 30,
          metadata={"issuer": "State Street", "region": "US"}),
    Asset("XLY", "XLY", "Consumer Discretionary Select Sector SPDR", "etf", "consumer_discretionary", "core", "USD", "PCX", 40,
          metadata={"issuer": "State Street", "region": "US"}),
    Asset("XLC", "XLC", "Communication Services Select Sector SPDR", "etf", "communication", "core", "USD", "PCX", 50,
          metadata={"issuer": "State Street", "region": "US"}),
    Asset("XLI", "XLI", "Industrial Select Sector SPDR", "etf", "industrials", "core", "USD", "PCX", 60,
          metadata={"issuer": "State Street", "region": "US"}),
    Asset("XLP", "XLP", "Consumer Staples Select Sector SPDR", "etf", "consumer_staples", "core", "USD", "PCX", 70,
          metadata={"issuer": "State Street", "region": "US"}),
    Asset("XLE", "XLE", "Energy Select Sector SPDR", "etf", "energy", "core", "USD", "PCX", 80,
          metadata={"issuer": "State Street", "region": "US"}),
    Asset("XLU", "XLU", "Utilities Select Sector SPDR", "etf", "utilities", "core", "USD", "PCX", 90,
          metadata={"issuer": "State Street", "region": "US"}),
    Asset("XLRE", "XLRE", "Real Estate Select Sector SPDR", "etf", "real_estate", "core", "USD", "PCX", 100,
          metadata={"issuer": "State Street", "region": "US"}),
    Asset("XLB", "XLB", "Materials Select Sector SPDR", "etf", "materials", "core", "USD", "PCX", 110,
          metadata={"issuer": "State Street", "region": "US"}),

    # ── Marché large et actifs de diversification ─────────────────────────
    Asset("URTH", "URTH", "iShares MSCI World ETF", "etf", "broad_market", "core", "USD", "PCX", 120,
          is_benchmark=True, metadata={"issuer": "iShares", "region": "World"}),
    Asset("BTC", "BTC-USD", "Bitcoin", "crypto", "crypto", "core", "USD", "CCC", 130,
          metadata={"region": "Global"}),
    Asset("GLD", "GLD", "SPDR Gold Shares", "etf", "commodity", "core", "USD", "PCX", 140,
          metadata={"issuer": "State Street", "region": "Global"}),
    Asset("TLT", "TLT", "iShares 20+ Year Treasury Bond ETF", "etf", "bonds", "core", "USD", "NGM", 150,
          metadata={"issuer": "iShares", "region": "US"}),

    # ── Actions ciblées ───────────────────────────────────────────────────
    Asset("ASML", "ASML.AS", "ASML Holding N.V.", "equity", "semiconductor", "stock", "EUR", "AMS", 200,
          metadata={"region": "NL", "sector": "Semiconductors"}),
    Asset("BESI", "BESI.AS", "BE Semiconductor Industries N.V.", "equity", "semiconductor", "stock", "EUR", "AMS", 210,
          metadata={"region": "NL", "sector": "Semiconductors"}),
    Asset("NOVO", "NOVO-B.CO", "Novo Nordisk A/S (B)", "equity", "health_care", "stock", "DKK", "CPH", 220,
          metadata={"region": "DK", "sector": "Health Care"}),
    Asset("SMA92", "S92.DE", "SMA Solar Technology AG", "equity", "solar", "stock", "EUR", "GER", 230,
          metadata={"region": "DE", "sector": "Solar"}),

    # ── ETF tech/semi complémentaires ─────────────────────────────────────
    Asset("SMH", "SMH", "VanEck Semiconductor ETF", "etf", "semiconductor", "tech", "USD", "NGM", 300,
          metadata={"issuer": "VanEck", "region": "Global"}),
    Asset("SOXX", "SOXX", "iShares Semiconductor ETF", "etf", "semiconductor", "tech", "USD", "NGM", 310,
          metadata={"issuer": "iShares", "region": "US"}),
    Asset("VGT", "VGT", "Vanguard Information Technology ETF", "etf", "technology", "tech", "USD", "PCX", 320,
          metadata={"issuer": "Vanguard", "region": "US"}),
)

ASSET_BY_TICKER = {asset.ticker: asset for asset in ASSETS}
CORE_TICKERS = tuple(a.ticker for a in ASSETS if a.universe == "core")
STOCK_TICKERS = tuple(a.ticker for a in ASSETS if a.universe == "stock")
TECH_TICKERS = tuple(a.ticker for a in ASSETS if a.universe == "tech")
BENCHMARK = "URTH"

# Paires FX nécessaires. Yahoo les cote en « devise pour 1 EUR ».
FX_PAIRS = {
    "USD": "EURUSD=X",
    "DKK": "EURDKK=X",
}

assert BENCHMARK in ASSET_BY_TICKER
