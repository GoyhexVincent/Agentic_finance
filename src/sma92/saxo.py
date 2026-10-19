"""Client REST Saxo OpenAPI pour l'environnement de simulation.

Le module privilégie deux briques REST plutôt que la souscription temps réel
``/trade/v1/optionschain`` :

* ``/ref/v1/instruments/contractoptionspaces/{root}`` fournit l'espace des
  options et les UIC de chaque contrat ;
* ``/trade/v1/infoprices`` fournit un snapshot bid/ask/grecques ;
* ``/chart/v3/charts`` fournit l'historique OHLC par UIC.

Cela suffit pour un premier radar quotidien, sans gérer un contexte de
streaming. Le client est volontairement tolérant aux champs optionnels de
l'API : les schémas Saxo évoluent et tous les champs ne sont pas disponibles
pour chaque place.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable

import requests

from .market_data import parse_date

SIM_BASE_URL = "https://gateway.saxobank.com/sim/openapi"
LIVE_BASE_URL = "https://gateway.saxobank.com/openapi"
ENVIRONMENTS = {"sim": SIM_BASE_URL, "live": LIVE_BASE_URL}


class SaxoError(RuntimeError):
    """Erreur générique du client Saxo."""


class SaxoAuthError(SaxoError):
    """Jeton absent, expiré ou insuffisant."""


class SaxoAPIError(SaxoError):
    """Réponse HTTP non exploitable de Saxo."""

    def __init__(self, message: str, *, status_code: int, code: str | None = None, payload: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.payload = payload


@dataclass(frozen=True)
class Underlying:
    uic: int
    symbol: str
    description: str
    currency: str
    exchange_id: str
    spot: float
    as_of: datetime


@dataclass
class OptionContract:
    uic: int
    expiry: date
    strike: float
    kind: str
    contract_size: float = 100.0
    bid: float | None = None
    ask: float | None = None
    mid: float | None = None
    last: float | None = None
    volume: float | None = None
    open_interest: float | None = None
    delta: float | None = None
    gamma: float | None = None
    vega: float | None = None
    theta: float | None = None
    quote_time: str | None = None
    price_type_bid: str | None = None
    price_type_ask: str | None = None

    @property
    def premium_per_contract(self) -> float | None:
        if self.mid is None:
            return None
        return self.mid * self.contract_size


@dataclass(frozen=True)
class PriceBar:
    day: date
    open: float | None
    high: float | None
    low: float | None
    close: float
    volume: float | None = None


@dataclass
class MarketSnapshot:
    underlying: Underlying
    underlying_history: tuple[PriceBar, ...]
    contracts: tuple[OptionContract, ...]
    option_history: dict[int, tuple[PriceBar, ...]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    environment: str = "sim"
    root_id: int | None = None
    synthetic: bool = False
    source: str = "Saxo OpenAPI"


def _as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _as_text(value: Any) -> str | None:
    return str(value) if value not in (None, "") else None


def _first_float(container: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        value = _as_float(container.get(key))
        if value is not None:
            return value
    return None


def _parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _quote_from_item(item: dict[str, Any]) -> dict[str, Any]:
    quote = item.get("Quote") or {}
    details = item.get("PriceInfoDetails") or {}
    greeks = item.get("Greeks") or {}

    bid = _as_float(quote.get("Bid"))
    ask = _as_float(quote.get("Ask"))
    mid = _as_float(quote.get("Mid"))
    if mid is None and bid is not None and ask is not None:
        mid = 0.5 * (bid + ask)
    last = _first_float(details, ("LastTraded",))
    if last is None or last <= 0:
        last = _first_float(details, ("LastClose", "OfficialClosingPrice"))
    return {
        "bid": bid,
        "ask": ask,
        "mid": mid,
        "last": last,
        "volume": _first_float(details, ("Volume", "LastTradedSize")),
        "open_interest": _first_float(details, ("OpenInterest",)),
        "delta": _first_float(greeks, ("Delta", "DeltaPct")),
        "gamma": _first_float(greeks, ("Gamma",)),
        "vega": _first_float(greeks, ("Vega",)),
        "theta": _first_float(greeks, ("Theta",)),
        "quote_time": _as_text(item.get("LastUpdated")),
        "price_type_bid": _as_text(quote.get("PriceTypeBid")),
        "price_type_ask": _as_text(quote.get("PriceTypeAsk")),
    }


def normalize_symbol(value: str) -> str:
    return "".join(ch for ch in value.upper().strip() if ch.isalnum())


def _score_candidate(item: dict[str, Any], symbol: str, exchange: str | None) -> tuple[int, str]:
    candidate_symbol = normalize_symbol(str(item.get("Symbol", "")))
    wanted = normalize_symbol(symbol)
    description = str(item.get("Description", "")).lower()
    exchange_id = str(item.get("ExchangeId", "")).upper()
    score = 0
    reasons: list[str] = []

    aliases = {wanted, "SMA92", "S92", "SMASOLAR"}
    if candidate_symbol in aliases:
        score += 100
        reasons.append("symbole exact/alias")
    elif wanted and (wanted in candidate_symbol or candidate_symbol in wanted):
        score += 60
        reasons.append("symbole proche")
    if "sma solar" in description:
        score += 30
        reasons.append("description SMA Solar")
    if exchange and exchange_id == exchange.upper():
        score += 20
        reasons.append(f"place {exchange_id}")
    elif exchange_id == "XETR":
        score += 5
        reasons.append("place XETR")
    if str(item.get("SummaryType", "")).lower() == "instrument":
        score += 5
    return score, ", ".join(reasons) or "aucun critère fort"


def resolve_underlying(
    client: "SaxoClient",
    *,
    symbol: str,
    keywords: str | None = None,
    uic: int | None = None,
    exchange: str | None = None,
) -> tuple[Underlying, list[dict[str, Any]], dict[str, Any]]:
    """Résout l'UIC du sous-jacent et retourne aussi les détails instrument."""
    if uic is not None:
        details = client.instrument_details(uic, "Stock")
        underlying = Underlying(
            uic=uic,
            symbol=str(details.get("Symbol") or symbol),
            description=str(details.get("Description") or symbol),
            currency=str(details.get("CurrencyCode") or details.get("PriceCurrency") or "EUR"),
            exchange_id=str((details.get("Exchange") or {}).get("ExchangeId") or ""),
            spot=0.0,
            as_of=datetime.now(timezone.utc),
        )
        return underlying, [], details

    queries: list[str] = []
    for query in (keywords, symbol, "SMA Solar"):
        if query and query not in queries:
            queries.append(query)

    candidates_by_id: dict[int, dict[str, Any]] = {}
    for query in queries:
        rows = client.search(query, asset_types="Stock", top=50)
        for item in rows:
            identifier = item.get("Identifier")
            if identifier is not None:
                candidates_by_id.setdefault(int(identifier), item)
        if candidates_by_id:
            best_so_far = max(
                _score_candidate(item, symbol, exchange)[0] for item in candidates_by_id.values()
            )
            if best_so_far >= 50:
                break
    candidates = list(candidates_by_id.values())
    if not candidates:
        raise SaxoError(
            f"Aucun instrument actions trouvé pour {symbol!r}. "
            "Essayer --keywords 'SMA Solar' ou fournir --uic."
        )

    ranked = sorted(
        ((_score_candidate(item, symbol, exchange)[0], item) for item in candidates),
        key=lambda pair: pair[0],
        reverse=True,
    )
    best_score, best = ranked[0]
    if best_score < 50:
        names = ", ".join(f"{item.get('Symbol')} ({item.get('Description')})" for item in candidates[:5])
        raise SaxoError(
            f"Résolution ambiguë pour {symbol!r} ; candidats : {names}. "
            "Utiliser --uic pour forcer l'instrument."
        )

    details = client.instrument_details(int(best["Identifier"]), "Stock")
    underlying = Underlying(
        uic=int(best["Identifier"]),
        symbol=str(best.get("Symbol") or details.get("Symbol") or symbol),
        description=str(best.get("Description") or details.get("Description") or symbol),
        currency=str(details.get("CurrencyCode") or details.get("PriceCurrency") or "EUR"),
        exchange_id=str(best.get("ExchangeId") or (details.get("Exchange") or {}).get("ExchangeId") or ""),
        spot=0.0,
        as_of=datetime.now(timezone.utc),
    )
    return underlying, candidates, details


def extract_option_root(details: dict[str, Any]) -> int | None:
    related = details.get("RelatedOptionRootsEnhanced") or []
    stock_roots = [
        root
        for root in related
        if str(root.get("AssetType", "")) == "StockOption"
        and str(root.get("OptionType", "Default")).lower() in {"default", "none", ""}
    ]
    if stock_roots:
        return int(stock_roots[0]["OptionRootId"])
    fallback = [root for root in related if str(root.get("AssetType", "")) == "StockOption"]
    return int(fallback[0]["OptionRootId"]) if fallback else None


def parse_option_space(
    payload: dict[str, Any],
    *,
    as_of: date,
    spot: float,
    max_days: int,
    max_expiries: int,
    strike_window: float,
    max_contracts: int,
) -> tuple[list[OptionContract], int, list[str]]:
    """Extrait les contrats pertinents d'une réponse contractoptionspaces."""
    warnings: list[str] = []
    contract_size = _as_float(payload.get("ContractSize")) or 100.0
    expiries = payload.get("OptionSpace") or []

    eligible: list[dict[str, Any]] = []
    for expiry_entry in expiries:
        raw_expiry = expiry_entry.get("Expiry")
        if not raw_expiry:
            continue
        try:
            expiry = parse_date(str(raw_expiry))
        except ValueError:
            continue
        dte = (expiry - as_of).days
        if 0 <= dte <= max_days:
            eligible.append(expiry_entry)
    eligible.sort(key=lambda entry: parse_date(str(entry["Expiry"])))
    selected_expiries = eligible[:max_expiries]
    if not selected_expiries:
        raise SaxoError(f"Aucune échéance dans les {max_days} prochains jours.")

    contracts: list[OptionContract] = []
    for expiry_entry in selected_expiries:
        expiry = parse_date(str(expiry_entry["Expiry"]))
        options = expiry_entry.get("SpecificOptions") or []
        if not options:
            warnings.append(f"Échéance {expiry.isoformat()} sans détail de strikes (OptionSpaceSegment).")
            continue
        for option in options:
            kind = str(option.get("PutCall", "")).lower()
            if kind not in {"call", "put"}:
                continue
            strike = _as_float(option.get("StrikePrice"))
            option_uic = _as_float(option.get("Uic"))
            if strike is None or option_uic is None or strike <= 0:
                continue
            if spot > 0 and abs(strike / spot - 1.0) > strike_window:
                continue
            contracts.append(
                OptionContract(
                    uic=int(option_uic),
                    expiry=expiry,
                    strike=strike,
                    kind=kind,
                    contract_size=contract_size,
                )
            )

    if not contracts:
        raise SaxoError("Aucun contrat ne correspond à la fenêtre de strikes demandée.")

    if len(contracts) > max_contracts:
        contracts.sort(key=lambda c: (abs(c.strike / spot - 1.0), c.expiry, c.kind))
        contracts = contracts[:max_contracts]
        warnings.append(
            f"Chaîne tronquée à {max_contracts} contrats ; augmenter --max-contracts pour tout conserver."
        )

    contracts.sort(key=lambda c: (c.expiry, c.strike, c.kind))
    return contracts, contract_size, warnings


class SaxoClient:
    """Petit client REST avec throttling, retries et messages d'erreur actionnables."""

    def __init__(
        self,
        token: str | None = None,
        *,
        environment: str = "sim",
        base_url: str | None = None,
        timeout: float = 30.0,
        max_retries: int = 4,
        min_interval: float = 0.15,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.token = (token or "").removeprefix("Bearer ").strip()
        if not self.token:
            raise SaxoAuthError(
                "Jeton Saxo absent. Définir SAXO_ACCESS_TOKEN, utiliser --token-file, "
                "ou lancer en --dry-run pour tester sans réseau."
            )
        if environment not in ENVIRONMENTS:
            raise ValueError(f"environnement inconnu : {environment!r}")
        self.environment = environment
        self.base_url = (base_url or ENVIRONMENTS[environment]).rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.min_interval = min_interval
        self.session = session or requests.Session()
        self._sleep = sleep
        self._clock = clock
        self._last_request = 0.0

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> "SaxoClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _throttle(self) -> None:
        if self.min_interval <= 0:
            return
        elapsed = self._clock() - self._last_request
        if elapsed < self.min_interval:
            self._sleep(self.min_interval - elapsed)
        self._last_request = self._clock()

    def _retry_after(self, response: requests.Response) -> float | None:
        raw = response.headers.get("Retry-After")
        if not raw:
            return None
        try:
            return max(0.0, float(raw))
        except ValueError:
            pass
        try:
            when = parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{self.base_url}{path}"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
            "User-Agent": "sma92-options-lab/0.1",
        }
        last_error: Exception | None = None

        for attempt in range(self.max_retries + 1):
            self._throttle()
            try:
                response = self.session.request(
                    method,
                    url,
                    params=params,
                    json=json_body,
                    headers=headers,
                    timeout=self.timeout,
                )
            except requests.RequestException as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    break
                self._sleep(0.5 * (2**attempt))
                continue

            status = int(response.status_code)
            if status in {200, 201, 204}:
                if status == 204 or not response.text:
                    return {}
                try:
                    return response.json()
                except ValueError as exc:
                    raise SaxoAPIError(
                        f"Réponse JSON invalide de Saxo ({status})",
                        status_code=status,
                        payload=response.text[:500],
                    ) from exc

            payload: Any
            try:
                payload = response.json()
            except ValueError:
                payload = response.text[:1000]

            code = None
            message = None
            if isinstance(payload, dict):
                code = _as_text(payload.get("ErrorCode") or payload.get("Code"))
                message = _as_text(payload.get("Message") or payload.get("ErrorDescription"))

            if status == 401:
                raise SaxoAuthError(
                    "Saxo a refusé le jeton (401). Le jeton 24 h du portail est valable uniquement en SIM "
                    "et expire après 24 h. Vérifier SAXO_ACCESS_TOKEN/--token-file."
                )

            if status in {429, 500, 502, 503, 504} and attempt < self.max_retries:
                delay = self._retry_after(response) or 0.5 * (2**attempt)
                self._sleep(min(delay, 30.0))
                last_error = SaxoAPIError(
                    f"Saxo {status} : {message or code or 'erreur temporaire'}",
                    status_code=status,
                    code=code,
                    payload=payload,
                )
                continue

            detail = message or code or str(payload)[:300]
            raise SaxoAPIError(
                f"Saxo {status} sur {method} {path} : {detail}",
                status_code=status,
                code=code,
                payload=payload,
            )

        raise SaxoError(f"Échec réseau Saxo après {self.max_retries + 1} tentatives : {last_error}")

    def search(self, keywords: str, *, asset_types: str | None = None, top: int = 50) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"Keywords": keywords, "$top": top}
        if asset_types:
            params["AssetTypes"] = asset_types
        payload = self._request("GET", "/ref/v1/instruments", params=params)
        if not isinstance(payload, dict):
            return []
        return list(payload.get("Data") or [])

    def instrument_details(self, uic: int, asset_type: str = "Stock") -> dict[str, Any]:
        payload = self._request("GET", f"/ref/v1/instruments/details/{uic}/{asset_type}")
        if not isinstance(payload, dict):
            raise SaxoAPIError("Réponse détails instrument inattendue", status_code=200, payload=payload)
        return payload

    def option_space(self, root_id: int) -> dict[str, Any]:
        payload = self._request(
            "GET",
            f"/ref/v1/instruments/contractoptionspaces/{root_id}",
            params={"OptionSpaceSegment": "AllDates"},
        )
        if not isinstance(payload, dict):
            raise SaxoAPIError("Réponse option space inattendue", status_code=200, payload=payload)
        return payload

    def info_price(self, uic: int, asset_type: str = "Stock") -> dict[str, Any]:
        payload = self._request(
            "GET",
            "/trade/v1/infoprices",
            params={
                "Uic": uic,
                "AssetType": asset_type,
                "FieldGroups": "Quote,PriceInfo,PriceInfoDetails,Greeks,DisplayAndFormat",
            },
        )
        return payload if isinstance(payload, dict) else {}

    def info_prices(self, uics: list[int], asset_type: str = "StockOption") -> dict[int, dict[str, Any]]:
        result: dict[int, dict[str, Any]] = {}
        for start in range(0, len(uics), 40):
            chunk = uics[start : start + 40]
            payload = self._request(
                "GET",
                "/trade/v1/infoprices/list",
                params={
                    "Uics": ",".join(str(uic) for uic in chunk),
                    "AssetType": asset_type,
                    "FieldGroups": "Quote,PriceInfo,PriceInfoDetails,Greeks,DisplayAndFormat",
                },
            )
            rows = payload.get("Data") if isinstance(payload, dict) else None
            for row in rows or []:
                if isinstance(row, dict) and row.get("Uic") is not None:
                    result[int(row["Uic"])] = row
        return result

    def chart(
        self,
        uic: int,
        asset_type: str,
        *,
        count: int = 10,
        horizon: int = 1440,
    ) -> list[dict[str, Any]]:
        payload = self._request(
            "GET",
            "/chart/v3/charts",
            params={
                "Uic": uic,
                "AssetType": asset_type,
                "Horizon": horizon,
                "Count": max(1, min(count, 1200)),
                "FieldGroups": "Data",
            },
        )
        if not isinstance(payload, dict):
            return []
        return list(payload.get("Data") or [])


def _bars_from_chart(rows: list[dict[str, Any]], *, start: date | None = None) -> tuple[PriceBar, ...]:
    bars: list[PriceBar] = []
    for row in rows:
        close = _as_float(row.get("Close"))
        raw_time = _as_text(row.get("Time"))
        if close is None or close <= 0 or not raw_time:
            continue
        try:
            day = parse_date(raw_time)
        except ValueError:
            continue
        if start is not None and day < start:
            continue
        bars.append(
            PriceBar(
                day=day,
                open=_as_float(row.get("Open")),
                high=_as_float(row.get("High")),
                low=_as_float(row.get("Low")),
                close=close,
                volume=_as_float(row.get("Volume")),
            )
        )
    bars.sort(key=lambda bar: bar.day)
    return tuple(bars)


def fetch_snapshot(
    client: SaxoClient,
    *,
    symbol: str = "SMA92",
    keywords: str | None = None,
    uic: int | None = None,
    exchange: str | None = None,
    as_of: datetime | None = None,
    max_expiries: int = 3,
    max_days: int = 120,
    strike_window: float = 0.40,
    max_contracts: int = 80,
    history_days: int = 7,
    history_scope: str = "focus",
    history_window: float = 0.10,
    max_history_contracts: int = 24,
    option_root_id: int | None = None,
    logger: Callable[[str], None] | None = None,
) -> MarketSnapshot:
    """Construit un snapshot complet : sous-jacent, chaîne, cotations, historique 7 j."""
    log = logger or (lambda _: None)
    now = as_of or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)
    today = now.date()

    log(f"Résolution de {symbol}…")
    underlying, candidates, details = resolve_underlying(
        client, symbol=symbol, keywords=keywords, uic=uic, exchange=exchange
    )
    if candidates:
        log(f"{len(candidates)} candidat(s),retenu : {underlying.symbol} — {underlying.description}")

    start_history = today - timedelta(days=max(history_days + 30, 120))
    underlying_rows = client.chart(underlying.uic, "Stock", count=60)
    underlying_bars = _bars_from_chart(underlying_rows, start=start_history)
    if not underlying_bars:
        raise SaxoError("Aucun historique de cours pour le sous-jacent.")

    spot_quote = {}
    try:
        spot_quote = client.info_price(underlying.uic, "Stock")
    except SaxoError as exc:
        log(f"Cotation temps réel indisponible, repli sur le dernier close ({exc.__class__.__name__}).")

    quote_values = _quote_from_item(spot_quote) if spot_quote else {}
    spot = quote_values.get("mid") or quote_values.get("last") or underlying_bars[-1].close
    if spot is None or spot <= 0:
        raise SaxoError("Spot sous-jacent invalide.")
    underlying = Underlying(
        uic=underlying.uic,
        symbol=underlying.symbol,
        description=underlying.description,
        currency=underlying.currency,
        exchange_id=underlying.exchange_id,
        spot=float(spot),
        as_of=now,
    )

    root_id = option_root_id or extract_option_root(details)
    warnings: list[str] = []
    if root_id is None:
        log("Aucun option root dans les détails, recherche d'un root via /ref/v1/instruments…")
        roots = client.search(underlying.description, asset_types="StockOption", top=50)
        root_candidates = [
            int(item["Identifier"])
            for item in roots
            if item.get("Identifier") is not None
            and str(item.get("SummaryType", "")).lower() in {"optionroot", "optionrootsummary"}
            and str(item.get("AssetType", "")) == "StockOption"
        ]
        if not root_candidates:
            raised = ", ".join(
                f"{item.get('Symbol')} ({item.get('SummaryType')})" for item in roots[:5]
            )
            raise SaxoError(
                "Aucun option root StockOption identifié pour cet instrument. "
                "Vérifier que l'option est proposée dans l'environnement SIM, puis fournir "
                f"--option-root-id. Candidats retournés : {raised or 'aucun'}."
            )
        root_id = root_candidates[0]
        warnings.append("Option root déduit par recherche textuelle : vérifier --option-root-id.")

    log(f"Chargement de l'espace d'options (root {root_id})…")
    option_space = client.option_space(root_id)
    contracts, contract_size, space_warnings = parse_option_space(
        option_space,
        as_of=today,
        spot=underlying.spot,
        max_days=max_days,
        max_expiries=max_expiries,
        strike_window=strike_window,
        max_contracts=max_contracts,
    )
    warnings.extend(space_warnings)
    log(f"{len(contracts)} contrats retenus sur {len({c.expiry for c in contracts})} échéance(s).")

    log("Récupération des cotations bid/ask et grecques…")
    quotes = client.info_prices([contract.uic for contract in contracts])
    missing = 0
    for contract in contracts:
        item = quotes.get(contract.uic)
        if not item:
            missing += 1
            continue
        parsed = _quote_from_item(item)
        for key, value in parsed.items():
            setattr(contract, key, value)
    if missing:
        warnings.append(f"{missing} contrat(s) sans cotation retournée par infoprices.")
    two_sided = sum(1 for contract in contracts if contract.bid is not None and contract.ask is not None)
    if two_sided == 0:
        warnings.append(
            "Aucune cotation bid/ask complète : les prix peuvent être des clos précédents ou des données SIM."
        )

    option_history: dict[int, tuple[PriceBar, ...]] = {}
    if history_scope != "none":
        selected_for_history = list(contracts)
        if history_scope == "focus":
            expiries = sorted({contract.expiry for contract in contracts})[:2]
            selected_for_history = [
                contract
                for contract in contracts
                if contract.expiry in expiries
                and abs(contract.strike / underlying.spot - 1.0) <= history_window
            ]
        selected_for_history = selected_for_history[:max_history_contracts]
        log(f"Historique {history_days} j pour {len(selected_for_history)} contrats…")
        for index, contract in enumerate(selected_for_history, start=1):
            try:
                rows = client.chart(
                    contract.uic,
                    "StockOption",
                    count=max(10, history_days + 5),
                )
                bars = _bars_from_chart(rows, start=today - timedelta(days=history_days))
                if bars:
                    option_history[contract.uic] = bars
                else:
                    warnings.append(f"Pas d'historique pour {contract.kind} {contract.strike:g} {contract.expiry}.")
            except SaxoError as exc:
                warnings.append(
                    f"Historique indisponible pour {contract.kind} {contract.strike:g} "
                    f"{contract.expiry} ({exc.__class__.__name__})."
                )
            if index % 8 == 0:
                log(f"  {index}/{len(selected_for_history)} historiques traités")

    return MarketSnapshot(
        underlying=underlying,
        underlying_history=underlying_bars,
        contracts=tuple(contracts),
        option_history=option_history,
        warnings=warnings,
        environment=client.environment,
        root_id=root_id,
        synthetic=False,
        source=f"Saxo OpenAPI ({client.environment})",
    )
