from __future__ import annotations

import json
from datetime import date, datetime, timezone

import pytest

from sma92.saxo import (
    SaxoAPIError,
    SaxoAuthError,
    SaxoClient,
    _quote_from_item,
    extract_option_root,
    parse_option_space,
    resolve_underlying,
)


class FakeResponse:
    def __init__(self, status: int, payload: object = None, headers: dict[str, str] | None = None):
        self.status_code = status
        self._payload = payload
        self.headers = headers or {}
        self.text = "" if payload is None else json.dumps(payload)

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeSession:
    def __init__(self, responses: list[FakeResponse]):
        self.responses = responses
        self.calls: list[dict[str, object]] = []

    def request(self, method: str, url: str, **kwargs: object) -> FakeResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        if not self.responses:
            raise AssertionError("réponse fake non prévue")
        return self.responses.pop(0)

    def close(self) -> None:
        return None


def _client(session: FakeSession, **kwargs: object) -> SaxoClient:
    return SaxoClient(
        "test-token",
        session=session,
        min_interval=0.0,
        sleep=lambda _: None,
        clock=lambda: 0.0,
        **kwargs,
    )


def test_search_sends_auth_and_parses_data() -> None:
    session = FakeSession([FakeResponse(200, {"Data": [{"Symbol": "S92", "Identifier": 42}]})])
    client = _client(session)
    rows = client.search("SMA Solar", asset_types="Stock", top=5)
    assert rows[0]["Symbol"] == "S92"
    call = session.calls[0]
    assert call["headers"]["Authorization"] == "Bearer test-token"  # type: ignore[index]
    assert str(call["url"]).endswith("/ref/v1/instruments")
    assert call["params"]["Keywords"] == "SMA Solar"  # type: ignore[index]


def test_retry_on_429_then_success() -> None:
    sleeps: list[float] = []
    session = FakeSession(
        [
            FakeResponse(429, {"ErrorCode": "RateLimit"}, {"Retry-After": "0"}),
            FakeResponse(200, {"Data": [{"Symbol": "S92"}]}),
        ]
    )
    client = SaxoClient(
        "test-token",
        session=session,
        min_interval=0.0,
        sleep=sleeps.append,
        clock=lambda: 0.0,
    )
    assert client.search("SMA")[0]["Symbol"] == "S92"
    assert len(session.calls) == 2
    assert sleeps


def test_401_raises_actionable_auth_error() -> None:
    session = FakeSession([FakeResponse(401, {"Message": "Unauthorized"})])
    client = _client(session)
    with pytest.raises(SaxoAuthError, match="24 h"):
        client.search("SMA")


def test_400_raises_api_error() -> None:
    session = FakeSession([FakeResponse(400, {"ErrorCode": "BadRequest", "Message": "nope"})])
    client = _client(session)
    with pytest.raises(SaxoAPIError) as exc:
        client.search("SMA")
    assert exc.value.status_code == 400
    assert exc.value.code == "BadRequest"


def test_parse_option_space_filters_and_limits() -> None:
    payload = {
        "ContractSize": 100,
        "OptionSpace": [
            {
                "Expiry": "2026-11-20T00:00:00Z",
                "SpecificOptions": [
                    {"Uic": 1, "StrikePrice": 95, "PutCall": "Call"},
                    {"Uic": 2, "StrikePrice": 95, "PutCall": "Put"},
                    {"Uic": 3, "StrikePrice": 100, "PutCall": "Call"},
                    {"Uic": 4, "StrikePrice": 150, "PutCall": "Call"},
                ],
            },
            {
                "Expiry": "2026-12-18T00:00:00Z",
                "SpecificOptions": [
                    {"Uic": 5, "StrikePrice": 100, "PutCall": "Call"},
                    {"Uic": 6, "StrikePrice": 100, "PutCall": "Put"},
                ],
            },
            {"Expiry": "2027-12-18T00:00:00Z", "SpecificOptions": [{"Uic": 9, "StrikePrice": 100}]},
        ],
    }
    contracts, size, warnings = parse_option_space(
        payload,
        as_of=date(2026, 10, 19),
        spot=100.0,
        max_days=120,
        max_expiries=1,
        strike_window=0.2,
        max_contracts=10,
    )
    assert size == 100
    assert not warnings
    assert [contract.uic for contract in contracts] == [1, 2, 3]


def test_extract_option_root_prefers_default() -> None:
    details = {
        "RelatedOptionRootsEnhanced": [
            {"AssetType": "StockOption", "OptionType": "Weekly", "OptionRootId": 1},
            {"AssetType": "StockOption", "OptionType": "Default", "OptionRootId": 2},
        ]
    }
    assert extract_option_root(details) == 2


def test_quote_parsing() -> None:
    item = {
        "Uic": 10,
        "Quote": {"Bid": 1.0, "Ask": 1.2, "PriceTypeBid": "Indicative"},
        "PriceInfoDetails": {"LastTraded": 1.1, "Volume": 4, "OpenInterest": 55},
        "Greeks": {"Delta": 0.42, "Gamma": 0.03, "Vega": 0.1, "Theta": -0.02},
        "LastUpdated": "2026-10-19T15:30:00Z",
    }
    quote = _quote_from_item(item)
    assert quote["mid"] == pytest.approx(1.1)
    assert quote["delta"] == pytest.approx(0.42)
    assert quote["open_interest"] == 55


class StubResolver:
    def search(self, keywords: str, *, asset_types: str | None = None, top: int = 50):
        return [
            {"Symbol": "S92", "Description": "SMA Solar Technology AG", "Identifier": 123, "SummaryType": "Instrument"},
            {"Symbol": "SMA", "Description": "Other", "Identifier": 999, "SummaryType": "Instrument"},
        ]

    def instrument_details(self, uic: int, asset_type: str = "Stock"):
        return {
            "Uic": uic,
            "Symbol": "S92",
            "Description": "SMA Solar Technology AG",
            "CurrencyCode": "EUR",
            "Exchange": {"ExchangeId": "XETR"},
        }


def test_resolve_underlying_prefers_alias() -> None:
    underlying, candidates, details = resolve_underlying(StubResolver(), symbol="SMA92")  # type: ignore[arg-type]
    assert len(candidates) == 2
    assert underlying.uic == 123
    assert underlying.exchange_id == "XETR"
    assert details["Description"].startswith("SMA Solar")


def test_chart_returns_data() -> None:
    session = FakeSession([FakeResponse(200, {"Data": [{"Time": "2026-10-19T00:00:00Z", "Close": 55.7}]})])
    client = _client(session)
    rows = client.chart(123, "Stock", count=5)
    assert rows[0]["Close"] == 55.7
    assert session.calls[0]["params"]["Horizon"] == 1440  # type: ignore[index]
