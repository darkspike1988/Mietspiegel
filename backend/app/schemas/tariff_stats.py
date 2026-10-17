"""Schemas für die statistische Tarif-Auswertung.

Zwei Datenquellen — beide deterministisch ausgewertet:

1. TARIFF-BEOBACHTUNGEN (User liest Preise aus den Portalen ab):
   Der User (oder später ein Partner-API-Feed) übermittelt Tarif-
   Beobachtungen {portal, produkt, plz, ct/kWh, grundgebühr}.
   Daraus berechnen wir: Portal-Ranking (wer ist wirklich am
   günstigsten), Preis-Statistik je Portal (min/median/durchschnitt),
   zeitliche Entwicklung.

2. SMARD-ZEITREIHE (offizielle BNetzA-Börsenpreise):
   Deterministische Aggregation nach Tagesstunde, Wochentag und
   Monat — 'wann ist Strom am günstigsten' für Dynamic-Tariffs.

Wir speichern bewusst keine personenbezogenen Daten — nur
anonyme Preis-Beobachtungen.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class Portal(Enum):
    VERIVOX = "verivox"
    CHECK24 = "check24"
    IDEALO = "idealo"
    ANBIETER_DIREKT = "anbieter"


class TariffObservation(BaseModel):
    """Eine anonyme Preis-Beobachtung aus einem Vergleichsportal."""
    portal: Portal
    product: str = Field(..., description="strom | gas | dsl | mobilfunk")
    postal_code: str = Field(..., pattern=r"^\d{5}$")
    tariff_name: str = Field(..., min_length=1, max_length=120)
    ct_per_kwh: float | None = Field(None, ge=0, le=200)
    base_fee_eur_per_year: float = Field(0, ge=0)
    bonus_eur_first_year: float = Field(0, ge=0)
    observed_at: datetime = Field(
        default_factory=lambda: datetime.now().astimezone(),
        description="Zeitpunkt der Beobachtung",
    )


class PortalStats(BaseModel):
    """Statistik-Zusammenfassung je Portal."""
    portal: str
    observation_count: int
    min_ct_kwh: float | None = None
    max_ct_kwh: float | None = None
    median_ct_kwh: float | None = None
    avg_ct_kwh: float | None = None
    cheapest_tariff: str | None = None


class TariffStatisticsResponse(BaseModel):
    ok: bool = True
    product: str
    postal_code: str
    observation_count: int
    ranking: list[PortalStats] = Field(
        ..., description="Nach günstigstem Median sortiert — der deterministische Gewinner steht vorn"
    )
    insight: str = Field(..., description="Klartext: Wer ist wirklich am günstigsten")


class SmardTimeStats(BaseModel):
    """Deterministische Zeit-Aggregation der Börsenpreise."""
    by_hour_of_day: list[dict] = Field(
        ..., description="[{'hour': 3, 'avg_ct_kwh': 8.1, 'min': .., 'max': ..}]"
    )
    by_weekday: list[dict] = Field(
        ..., description="[{'weekday': 'Mo', 'avg_ct_kwh': .., 'min': .., 'max': ..}]"
    )
    cheapest_hour: dict | None = None
    most_expensive_hour: dict | None = None
    cheapest_weekday: dict | None = None
    observation_count: int
    insight: str


__all__ = [
    "Portal",
    "TariffObservation",
    "PortalStats",
    "TariffStatisticsResponse",
    "SmardTimeStats",
]


class MarketStats(BaseModel):
    """Mietspiegel-Marktstatistik über alle Suchtreffer eines Profils."""
    total_listings: int
    fair_count: int = 0
    tolerance_count: int = 0
    brake_count: int = 0
    wucher_count: int = 0
    unknown_count: int = 0
    overpriced_percent: float = 0.0
    avg_delta_percent: float | None = None
    avg_overpayment_eur_per_year: float | None = None
    total_overpayment_eur_per_year: float = 0.0
    region: str = "unbekannt"
    insight: str


__all__.append("MarketStats")
