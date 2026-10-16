"""Schemas for the free tenant tools (Swiss-army-knife module).

All tools are deterministic (no LLM, no API key, no cost) so the
webapp stays 100% free to use:

  - Nebenkostenabrechnungs-Check  (BetrKV / §556 BGB)
  - Kaution-Check                 (§551 BGB)
  - Mieterhöhung-Check            (§558 / §558b BGB, Kappungsgrenze)
"""
from __future__ import annotations

from datetime import date
from enum import Enum

from pydantic import BaseModel, Field, model_validator

# ----------------------------------------------------------------------
# Nebenkostenabrechnungs-Check (/api/tools/nebenkosten)
# ----------------------------------------------------------------------

class NKCostType(Enum):
    HEIZUNG = "heizung"
    WARMWASSER = "warmwasser"
    KALTWASSER = "kaltwasser"
    ABWASSER = "abwasser"
    MUELL = "muell"
    STRASSENREINIGUNG = "strassenreinigung"
    HAUSMEISTER = "hausmeister"
    AUFWACHEN = "aufwachkosten"
    ALLGEMEINSTROM = "gemeindestrom"
    SCHORNSTEIN = "schornsteinfeger"
    VERSICHERUNG = "gebaeudeversicherung"
    WINTERDIENST = "winterdienst"
    GARTENPFLEGE = "gartenpflege"
    SONSTIGES = "sonstiges"


NOT_ALLOCATION_CAPABLE: dict[str, str] = {
    "reparatur": "Reparaturkosten sind nicht umlagefähig (Anlage 3 zu §2 BetrKV)",
    "verwaltung": "Verwaltungskosten des Vermieters sind nicht umlagefähig (§2 Abs. 4 BetrKV)",
    "kapital": "Kapitalkosten / Zinsen sind nicht umlagefähig",
    "innehaltung": "Kosten der Instandsetzung/Erhaltung sind nicht umlagefähig",
}


class NKLineItem(BaseModel):
    """Eine Position aus der Nebenkostenabrechnung."""
    label: str = Field(..., min_length=1, description="Kostenart laut Abrechnung")
    cost_type: NKCostType = Field(NKCostType.SONSTIGES, description="Kategorisierung")
    amount_eur: float = Field(..., ge=0, description="Gesamtbetrag der Position (Gebäude)")
    not_allocation_capable: bool = Field(
        False,
        description="Position ist laut Mieter nicht umlagefähig (z.B. Reparatur)",
    )
    reason: str | None = Field(None, description="Begründung wenn nicht umlagefähig")


class NebenkostenCheckRequest(BaseModel):
    period_start: date = Field(..., description="Beginn Abrechnungszeitraum")
    period_end: date = Field(..., description="Ende Abrechnungszeitraum")
    statement_date: date | None = Field(
        None,
        description="Datum, an dem die Abrechnung dem Mieter zugegangen ist",
    )
    items: list[NKLineItem] = Field(default_factory=list)
    total_advance_payments_eur: float = Field(
        ..., ge=0, description="Summe aller Vorauszahlungen im Zeitraum"
    )
    invoiced_total_eur: float = Field(
        ..., ge=0, description="Gesamtsumme laut Abrechnung"
    )
    claimed_balance_eur: float = Field(
        ...,
        description="Nachzahlung laut Abrechnung (negativ = Guthaben)",
    )
    size_sqm: float | None = Field(None, gt=0)
    building_total_sqm: float | None = Field(
        None, gt=0, description="Gesamtfläche aller Wohnungen im Gebäude"
    )
    contract_allows_allocation: bool = Field(
        True, description="Mietvertrag erlaubt Umlage der Nebenkosten (§556 BGB)"
    )

    @model_validator(mode="after")
    def _period_ok(self) -> NebenkostenCheckRequest:
        if self.period_end <= self.period_start:
            raise ValueError("period_end must be after period_start")
        if self.statement_date and self.statement_date < self.period_end:
            raise ValueError("statement_date cannot be before period_end")
        return self


class NKFinding(BaseModel):
    """Ein Prüfbefund."""
    severity: str = Field(..., description="info | warn | error")
    code: str = Field(..., description="Maschinenlesbarer Code")
    message: str = Field(..., description="Verständlicher Text für Mieter")
    legal_basis: str | None = None
    amount_eur: float | None = Field(None, description="Betroffener Betrag")


class NebenkostenCheckResponse(BaseModel):
    ok: bool = True
    items_total_eur: float = Field(..., description="Summe der Einzelpositionen")
    arithmetic_ok: bool = Field(..., description="Einzelposten-Summe == Abrechnungssumme")
    deadline_ok: bool = Field(..., description="12-Monats-Frist (§556 Abs. 3 BGB) eingehalten")
    deadline_missed_by_days: int | None = None
    non_allocatable_eur: float = Field(
        ..., description="Summe erkannter nicht-umlagefähiger Positionen"
    )
    expected_balance_eur: float = Field(
        ..., description="Erwarteter Saldo (nach Abzug nicht-umlagefähiger Positionen)"
    )
    overpayment_eur: float = Field(
        ..., description="Mögliche Rückforderung des Mieters (≥ 0)"
    )
    findings: list[NKFinding] = Field(default_factory=list)
    recommendation: str = Field(...)


# ----------------------------------------------------------------------
# Kaution-Check (/api/tools/kaution)
# ----------------------------------------------------------------------

class KautionCheckRequest(BaseModel):
    cold_rent_eur: float = Field(..., gt=0, description="Kaltmiete (Monatsmiete, netto)")
    deposit_eur: float = Field(..., ge=0, description="Höhe der geleisteten Kaution")
    installments_allowed: bool = Field(
        True,
        description="Ratenzahlung vereinbart (3 Monatsraten, §551 Abs. 2 BGB)",
    )
    months_paid_upfront: int = Field(
        0, ge=0, le=12, description="Bereits bei Einzug gezahlte Monatsmieten"
    )


class KautionCheckResponse(BaseModel):
    ok: bool = True
    legal_maximum_eur: float = Field(..., description="3 × Kaltmiete (§551 Abs. 1 BGB)")
    excess_eur: float = Field(..., description="Überhöhung (≥ 0)")
    deposit_ratio: float = Field(..., description="Kaution / Monatsmiete")
    interest_claim_eur: float = Field(
        ..., ge=0, description="Übliche Zinsforderung (Rückforderung nach §§ 551, 247 BGB)"
    )
    findings: list[NKFinding] = Field(default_factory=list)
    recommendation: str = Field(...)


# ----------------------------------------------------------------------
# Mieterhöhung-Check (/api/tools/mieterhoehung)
# ----------------------------------------------------------------------

class MieterhoehungCheckRequest(BaseModel):
    current_cold_rent_eur: float = Field(..., gt=0)
    proposed_cold_rent_eur: float = Field(..., gt=0)
    last_increase_date: date | None = Field(
        None, description="Datum der letzten Mieterhöhung ( falls bekannt)"
    )
    today: date = Field(
        default_factory=date.today,
        description="Referenzdatum für Fristberechnung",
    )

    @model_validator(mode="after")
    def _rents_ok(self) -> MieterhoehungCheckRequest:
        if self.proposed_cold_rent_eur < self.current_cold_rent_eur:
            raise ValueError("proposed must be >= current (use check for Erhöhung)")
        return self


class MieterhoehungCheckResponse(BaseModel):
    ok: bool = True
    increase_percent: float = Field(..., description="Erhöhung in %")
    increase_eur: float = Field(..., description="Erhöhung in EUR/Monat")
    within_15_percent_cap: bool = Field(..., description="Kappungsgrenze §558 BGB")
    sperrfrist_ok: bool = Field(
        ..., description="15-Monats-Sperrfrist seit letzter Erhöhung (§558 Abs. 1 BGB)"
    )
    formal_notice_hint: bool = Field(
        ..., description="Schriftform + Begründung erforderlich (§558b Abs. 1 BGB)"
    )
    findings: list[NKFinding] = Field(default_factory=list)
    recommendation: str = Field(...)


__all__ = [
    "NKCostType",
    "NKLineItem",
    "NebenkostenCheckRequest",
    "NebenkostenCheckResponse",
    "NKFinding",
    "KautionCheckRequest",
    "KautionCheckResponse",
    "MieterhoehungCheckRequest",
    "MieterhoehungCheckResponse",
    "NOT_ALLOCATION_CAPABLE",
]


# ----------------------------------------------------------------------
# Kautionsanbieter-Vergleich (/api/tools/kaution/vergleich)
# ----------------------------------------------------------------------

class Kautionsanbieter(BaseModel):
    """Ein Mietkautionsbürgschaft-Anbieter (Kautionsschutzbrief)."""
    id: str
    name: str
    percent_per_year: float = Field(..., ge=0, le=20, description="% der Kautionssumme p.a.")
    min_fee_eur_per_year: float | None = Field(None, ge=0)
    max_deposit_eur: float | None = Field(None, ge=0)
    risk_carrier: str = Field(..., description="Versicherer im Hintergrund")
    bonitaet_check: str = Field(..., description="SCHUFA | Infoscore | nicht veröffentlicht")
    pays_on_first_demand: bool = Field(False, description="Zahlt auf erstes Anfordern")
    notes: str = ""
    source_url: str


class KautionsVergleichRequest(BaseModel):
    deposit_eur: float = Field(..., gt=0, le=100000, description="Kautionssumme")
    years: int = Field(1, ge=1, le=30, description="Voraussichtliche Mietdauer in Jahren")


class KautionsAngebot(BaseModel):
    """Berechnetes Angebot eines Anbieters."""
    provider: Kautionsanbieter
    fee_eur_per_year: float
    total_cost_eur: float
    blocked_capital_eur: float
    note: str | None = None


class KautionsVergleichResponse(BaseModel):
    ok: bool = True
    deposit_eur: float
    years: int
    offers: list[KautionsAngebot] = Field(..., description="Nach Gesamtkosten sortiert")
    cheapest_offer_eur: float
    savings_vs_most_expensive_eur: float
    dataset_updated_at: str
    disclaimer: str


__all__.append("Kautionsanbieter")
__all__.append("KautionsVergleichRequest")
__all__.append("KautionsAngebot")
__all__.append("KautionsVergleichResponse")
