"""Chargement d'un snapshot normalisé pour le mode démonstration sans réseau."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .saxo import MarketSnapshot, OptionContract, PriceBar, Underlying, _as_float


def _parse_datetime(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _bar(payload: dict[str, Any]) -> PriceBar:
    close = _as_float(payload.get("close"))
    if close is None or close <= 0:
        raise ValueError(f"barre sans close valide : {payload}")
    day = datetime.fromisoformat(str(payload["date"])[:10]).date()
    return PriceBar(
        day=day,
        open=_as_float(payload.get("open")),
        high=_as_float(payload.get("high")),
        low=_as_float(payload.get("low")),
        close=close,
        volume=_as_float(payload.get("volume")),
    )


def load_fixture(path: str | Path) -> MarketSnapshot:
    """Charge un JSON normalisé (même forme que les sorties du client)."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("fixture invalide : objet JSON attendu")

    raw_underlying = payload.get("underlying") or {}
    spot = _as_float(raw_underlying.get("spot"))
    if spot is None or spot <= 0:
        raise ValueError("fixture invalide : spot absent")
    as_of = _parse_datetime(payload.get("as_of"))
    underlying = Underlying(
        uic=int(raw_underlying.get("uic") or 0),
        symbol=str(raw_underlying.get("symbol") or "SMA92"),
        description=str(raw_underlying.get("description") or "SMA Solar Technology AG"),
        currency=str(raw_underlying.get("currency") or "EUR"),
        exchange_id=str(raw_underlying.get("exchange_id") or "XETR"),
        spot=spot,
        as_of=as_of,
    )

    bars = tuple(_bar(item) for item in payload.get("underlying_history") or [])
    if len(bars) < 2:
        raise ValueError("fixture invalide : historique sous-jacent trop court")

    contracts: list[OptionContract] = []
    for item in payload.get("contracts") or []:
        contract = OptionContract(
            uic=int(item["uic"]),
            expiry=datetime.fromisoformat(str(item["expiry"])[:10]).date(),
            strike=float(item["strike"]),
            kind=str(item["kind"]).lower(),
            contract_size=float(item.get("contract_size") or 100.0),
            bid=_as_float(item.get("bid")),
            ask=_as_float(item.get("ask")),
            mid=_as_float(item.get("mid")),
            last=_as_float(item.get("last")),
            volume=_as_float(item.get("volume")),
            open_interest=_as_float(item.get("open_interest")),
            delta=_as_float(item.get("delta")),
            gamma=_as_float(item.get("gamma")),
            vega=_as_float(item.get("vega")),
            theta=_as_float(item.get("theta")),
            quote_time=item.get("quote_time"),
            price_type_bid=item.get("price_type_bid"),
            price_type_ask=item.get("price_type_ask"),
        )
        if contract.kind not in {"call", "put"}:
            raise ValueError(f"type d'option invalide : {contract.kind}")
        contracts.append(contract)

    history: dict[int, tuple[PriceBar, ...]] = {}
    for raw_uic, rows in (payload.get("option_history") or {}).items():
        history[int(raw_uic)] = tuple(_bar(item) for item in rows)

    return MarketSnapshot(
        underlying=underlying,
        underlying_history=bars,
        contracts=tuple(contracts),
        option_history=history,
        warnings=list(payload.get("warnings") or []),
        environment=str(payload.get("environment") or "sim"),
        root_id=payload.get("root_id"),
        synthetic=bool(payload.get("synthetic", True)),
        source=str(payload.get("source") or "fixture synthétique"),
    )
