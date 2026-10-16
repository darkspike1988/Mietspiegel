"""Statistische Tarif-Auswertung — deterministisch.

1. Portal-Ranking aus User-Beobachtungen: welche Vergleichsplattform
   liefert realmente die günstigsten Tarife? (min/median/avg je Portal,
   sortiert — der Gewinner steht vorn.)
2. SMARD-Zeitreihen-Aggregation: Börsenpreise nach Tagesstunde,
   Wochentag — deterministisch aus offiziellen BNetzA-Daten.

Speicher: In-Memory-Liste (MVP) — für Produktion später SQLite.
Keine PII: nur anonyme Preis-Beobachtungen.
"""
from __future__ import annotations

from datetime import datetime
from statistics import median

import structlog

from app.schemas.tariff_stats import (
    Portal,
    PortalStats,
    SmardTimeStats,
    TariffObservation,
    TariffStatisticsResponse,
)

log = structlog.get_logger(__name__)

_observations: list[TariffObservation] = []


def add_observation(obs: TariffObservation) -> TariffObservation:
    """Beobachtung speichern (anonym, in-memory)."""
    _observations.append(obs)
    return obs


def get_observations(product: str, postal_code: str) -> list[TariffObservation]:
    """Alle Beobachtungen für Produkt+PLZ."""
    return [
        o for o in _observations
        if o.product == product and o.postal_code == postal_code
    ]


def _stats_for_portal(portal: str, obs: list[TariffObservation]) -> PortalStats:
    prices = [o.ct_per_kwh for o in obs if o.ct_per_kwh is not None]
    cheapest: TariffObservation | None = None
    if obs:
        with_price = [o for o in obs if o.ct_per_kwh is not None]
        if with_price:
            cheapest = min(with_price, key=lambda o: o.ct_per_kwh or 0)
    return PortalStats(
        portal=portal,
        observation_count=len(obs),
        min_ct_kwh=min(prices) if prices else None,
        max_ct_kwh=max(prices) if prices else None,
        median_ct_kwh=round(median(prices), 2) if prices else None,
        avg_ct_kwh=round(sum(prices) / len(prices), 2) if prices else None,
        cheapest_tariff=cheapest.tariff_name if cheapest else None,
    )


def compute_portal_ranking(
    product: str, postal_code: str
) -> TariffStatisticsResponse:
    """Portal-Ranking: Nach Median-Preis sortiert (deterministisch).

    Der Median ist robust gegen Ausreißer (Täusch-Angebote), der
    min-Preis zeigt das absolute Sparpotenzial.
    """
    obs = get_observations(product, postal_code)
    by_portal: dict[str, list[TariffObservation]] = {}
    for o in obs:
        by_portal.setdefault(o.portal.value, []).append(o)

    stats = [
        _stats_for_portal(portal, olist)
        for portal, olist in by_portal.items()
    ]
    # Sortierung: nach Median (None = ohne Preisnachweis nach hinten)
    stats.sort(key=lambda s: (s.median_ct_kwh is None, s.median_ct_kwh or 0))

    if not stats:
        return TariffStatisticsResponse(
            product=product,
            postal_code=postal_code,
            observation_count=0,
            ranking=[],
            insight=(
                "Noch keine Beobachtungen. Lies aus mindestens 2 Portalen "
                "den günstigsten ct/kWh-Preis ab und trage ihn hier ein — "
                "dann rechnet die Statistik, wer wirklich am günstigsten ist."
            ),
        )

    winner = stats[0]
    others = ", ".join(
        f"{s.portal} (Median {s.median_ct_kwh} ct)" for s in stats[1:] if s.median_ct_kwh
    )
    insight = (
        f"Günstigstes Portal nach Median: {winner.portal} "
        f"({winner.median_ct_kwh} ct/kWh über {winner.observation_count} "
        f"Beobachtungen)."
        + (f" Danach: {others}." if others else "")
        + " Median ist robust gegen Ausreißer — der min-Wert zeigt das "
        "Absolute Sparpotenzil eines einzelnen Top-Tarifs."
        if winner.median_ct_kwh is not None
        else "Zu wenige Preisangaben für ein Ranking."
    )
    return TariffStatisticsResponse(
        product=product,
        postal_code=postal_code,
        observation_count=len(obs),
        ranking=stats,
        insight=insight,
    )


# ----------------------------------------------------------------------
# SMARD-Zeitreihe: deterministische Aggregation
# ----------------------------------------------------------------------

WEEKDAYS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]


def aggregate_smard_by_time(prices: list[dict]) -> SmardTimeStats:
    """Börsenpreise nach Tagesstunde und Wochentag aggregieren.

    Input: Liste von {timestamp, price_eur_per_mwh, price_ct_per_kwh}
    (aus smard_provider.fetch_day_ahead_prices, ggf. mehrere Tage).
    """
    by_hour: dict[int, list[float]] = {}
    by_wd: dict[int, list[float]] = {}
    for p in prices:
        ts = datetime.fromisoformat(p["timestamp"])
        ct = p["price_ct_per_kwh"]
        by_hour.setdefault(ts.hour, []).append(ct)
        by_wd.setdefault(ts.weekday(), []).append(ct)

    def _agg(values: list[float]) -> dict:
        return {
            "avg_ct_kwh": round(sum(values) / len(values), 2),
            "min_ct_kwh": round(min(values), 2),
            "max_ct_kwh": round(max(values), 2),
            "n": len(values),
        }

    hours = [
        {"hour": h, **_agg(vals)} for h, vals in sorted(by_hour.items())
    ]
    weekdays = [
        {"weekday": WEEKDAYS[wd], **_agg(vals)} for wd, vals in sorted(by_wd.items())
    ]

    cheapest_hour = min(hours, key=lambda x: x["avg_ct_kwh"]) if hours else None
    expensive_hour = max(hours, key=lambda x: x["avg_ct_kwh"]) if hours else None
    cheapest_wd = min(weekdays, key=lambda x: x["avg_ct_kwh"]) if weekdays else None

    insight = ""
    if cheapest_hour and expensive_hour:
        spread = round(expensive_hour["avg_ct_kwh"] - cheapest_hour["avg_ct_kwh"], 2)
        insight = (
            f"Strom ist im Schnitt um {cheapest_hour['hour']}:00 Uhr am "
            f"günstigsten ({cheapest_hour['avg_ct_kwh']} ct/kWh), am teuersten "
            f"um {expensive_hour['hour']}:00 ({expensive_hour['avg_ct_kwh']} ct/kWh). "
            f"Spread: {spread} ct/kWh — Dynamictarif-Mieter verschieben "
            f"Waschmaschine/Spülmaschine in die Niedrigpreiszeit."
        )
        if cheapest_wd:
            insight += f" Günstigster Wochentag: {cheapest_wd['weekday']}."

    return SmardTimeStats(
        by_hour_of_day=hours,
        by_weekday=weekdays,
        cheapest_hour=cheapest_hour,
        most_expensive_hour=expensive_hour,
        cheapest_weekday=cheapest_wd,
        observation_count=len(prices),
        insight=insight,
    )


def reset_store() -> None:
    """Test-Hook: Beobachtungs-Speicher leeren."""
    _observations.clear()
