"""SMARD-Provider — offizielle Strommarktdaten der Bundesnetzagentur.

Öffentliche, keyless JSON-API (OpenAPI-dokumentiert auf smard.api.bund.dev):
  https://www.smard.de/app/chart_data/{filter}/{region}/index_{resolution}.json
  https://www.smard.de/app/chart_data/{filter}/{region}/{filter}_{region}_{resolution}_{timestamp}.json

Verwendungszweck im Mieter-Cockpit:
  - Day-Ahead-Börsenpreise (EUR/MWh) → für Mieter mit Dynamic Tariff /
    Variable Tarife relevant: "Wann ist Strom morgen günstig?"
  - Marktkontext für den Versorger-Ratgeber

Für ECHTE Haushalts-Tarife (Angebote von Anbietern) ist diese API nicht
die Quelle — dafür gibt es die offiziellen Vergleichsportal-Partner-APIs
(Verivox Webservice, Check24 Partner), die echte Tarife inkl.
Abschluss-Provision liefern. Siehe docs/RESEARCH_VERSORGER_API.md.

Lizenz: Daten der BNetzA, CC BY 4.0, Quelle kenntlich machen.
"""
from __future__ import annotations

from datetime import datetime, timezone

import httpx
import structlog

log = structlog.get_logger(__name__)

BASE = "https://www.smard.de/app/chart_data"
FILTER_DAY_AHEAD = "4169"  # Day-ahead Preise (EUR/MWh)
REGION_DE = "DE"


async def fetch_day_ahead_prices(resolution: str = "hour") -> list[dict]:
    """Aktuelle Day-Ahead-Börsenpreise für Deutschland.

    Liefert eine Liste von {timestamp_iso, price_eur_per_mwh}.
    Die Börsenpreise sind nicht die Haushaltspreise — für Dynamic-
    Tariff-Stundenpreise gilt grob: (Börsenpreis/MWh + Aufschlag)/1000.
    """
    async with httpx.AsyncClient(timeout=25.0) as client:
        idx = await client.get(
            f"{BASE}/{FILTER_DAY_AHEAD}/{REGION_DE}/index_{resolution}.json"
        )
        idx.raise_for_status()
        timestamps = idx.json().get("timestamps", [])
        if not timestamps:
            return []
        latest = timestamps[-1]
        series_resp = await client.get(
            f"{BASE}/{FILTER_DAY_AHEAD}/{REGION_DE}/"
            f"{FILTER_DAY_AHEAD}_{REGION_DE}_{resolution}_{latest}.json"
        )
        series_resp.raise_for_status()
        series = series_resp.json().get("series", [])

    result: list[dict] = []
    for ts_ms, price in series:
        if price is None:
            continue
        result.append({
            "timestamp": datetime.fromtimestamp(
                ts_ms / 1000, tz=timezone.utc
            ).isoformat(),
            "price_eur_per_mwh": float(price),
            "price_ct_per_kwh": round(float(price) / 10.0, 2),
        })
    return result


def cheapest_hours(prices: list[dict], n: int = 5) -> list[dict]:
    """Die n günstigsten Stunden (für Dynamic-Tariff-Tipps)."""
    ranked = sorted(prices, key=lambda p: p["price_eur_per_mwh"])
    return ranked[:n]


def most_expensive_hours(prices: list[dict], n: int = 5) -> list[dict]:
    """Die n teuersten Stunden (Spitzenlast vermeiden)."""
    ranked = sorted(prices, key=lambda p: -p["price_eur_per_mwh"])
    return ranked[:n]
