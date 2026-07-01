"""Pydantic schemas for request/response validation.

All API contracts live here. Update this file when changing public endpoints.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


# ----------------------------------------------------------------------
# /api/parse — Mietvertrag-PDF Parser
# ----------------------------------------------------------------------


class LocationQuality(str, Enum):
    """Wohnlage per §558 BGB."""

    EINFACH = "einfach"
    MITTEL = "mittel"
    GUT = "gut"


class ParsedLease(BaseModel):
    """Structured extraction result from a Mietvertrag PDF."""

    address: str = Field(..., description="Full street address incl. city")
    city: Optional[str] = Field(None, description="City name (auto-matched from address)")
    postal_code: Optional[str] = Field(None, description="5-digit PLZ")
    cold_rent_eur: float = Field(..., gt=0, description="Kaltmiete in EUR")
    warm_rent_eur: Optional[float] = Field(None, description="Warmmiete in EUR")
    additional_costs_eur: Optional[float] = Field(None, description="Nebenkosten in EUR")
    deposit_eur: Optional[float] = Field(None, description="Kaution in EUR")
    size_sqm: float = Field(..., gt=0, description="Wohnfläche in m²")
    rooms: Optional[float] = Field(None, ge=0, le=20, description="Anzahl Zimmer")
    build_year: Optional[int] = Field(None, ge=1850, le=2030, description="Baujahr")
    location_quality: Optional[LocationQuality] = Field(
        None, description="Wohnlage per §558 BGB"
    )
    extras: list[str] = Field(default_factory=list, description="Ausstattungsmerkmale (Balkon, Aufzug, ...)")
    contract_date: Optional[str] = Field(None, description="Datum des Vertrags ISO 8601")

    @field_validator("location_quality", mode="before")
    @classmethod
    def _norm_location(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip().lower()
        return v if v in {"einfach", "mittel", "gut"} else None


class ParseRequest(BaseModel):
    """Multipart upload of a single PDF (max 10 MB)."""

    file_name: str = Field(..., description="Original filename for logging")


class ParseResponse(BaseModel):
    ok: bool = True
    lease: ParsedLease
    parser: str = Field(description="Which parser was used: 'mistral-ocr' | 'pdfplumber'")


# ----------------------------------------------------------------------
# /api/check — Vergleichsmieten-Check
# ----------------------------------------------------------------------


class Verdict(str, Enum):
    FAIR = "fair"  # ≤10% über Vergleichsmiete
    TOLERANCE = "tolerance"  # ≤20% über Vergleichsmiete
    BRAKE = "brake"  # ≤50% über Vergleichsmiete (Mietpreisbremse-Schwelle)
    WUCHER = "wucher"  # >50% über Vergleichsmiete (§5 WiStrG)


class RentCheckRequest(BaseModel):
    """Either provide full parsed lease OR minimal fields for direct check."""

    # Mode 1: pre-parsed lease (from /api/parse)
    lease: Optional[ParsedLease] = None
    # Mode 2: minimal direct input
    address: Optional[str] = None
    cold_rent_eur: Optional[float] = Field(None, gt=0)
    size_sqm: Optional[float] = Field(None, gt=0)
    postal_code: Optional[str] = Field(
        None,
        description="5-digit PLZ — improves city resolution when not in lease.address",
    )
    build_year: Optional[int] = Field(
        None, ge=1850, le=2030, description="Baujahr des Gebäudes (Wohnwertmerkmal)"
    )
    location_quality: Optional[LocationQuality] = Field(
        None, description="Wohnlage per §558 BGB (einfach | mittel | gut)"
    )

    @field_validator("lease")
    @classmethod
    def _either_or(cls, v: Optional[ParsedLease]) -> Optional[ParsedLease]:
        # Top-level validator can't enforce mutually-exclusive w/ other fields easily;
        # we do the check in the route handler.
        return v


class ComparisonData(BaseModel):
    """The comparison dataset used for the check."""

    city: str
    city_average_eur_per_sqm: float
    reference_size_sqm: float = Field(..., description="Normwohnung (typically 65 m²)")
    source: str = Field(..., description="Datenquelle, e.g. 'BORIS-NRW 2025'")
    fetched_at: str


class SourceCitation(BaseModel):
    """Single, citable reference for the comparison dataset.

    Every RentCheckResponse MUST carry at least one citation so the
    resulting verdict is auditable (Mieter can click through to the
    official Mietspiegel/Marktbericht the verdict was derived from).
    """

    title: str = Field(..., description="Document title, e.g. 'Berliner Mietspiegel 2024'")
    publisher: str = Field(..., description="Herausgeber, e.g. 'Senatsverwaltung Berlin'")
    url: str = Field(..., description="Direct URL to the document / PDF / data portal")
    year: int = Field(..., description="Stichtag/Jahr des Datensatzes")
    kind: str = Field(
        ...,
        description="qualifizierter_mietspiegel | einfacher_mietspiegel | marktbericht | andere",
    )
    spread_eur_per_sqm_low: float = Field(..., description="Untergrenze ortsübliche Spanne (€/m²)")
    spread_eur_per_sqm_high: float = Field(..., description="Obergrenze ortsübliche Spanne (€/m²)")

    @field_validator("spread_eur_per_sqm_high")
    @classmethod
    def _spread_high_must_exceed_low(cls, v: float, info) -> float:
        low = info.data.get("spread_eur_per_sqm_low")
        if low is not None and v < low:
            raise ValueError(
                f"spread_eur_per_sqm_high ({v}) must be ≥ spread_eur_per_sqm_low ({low})"
            )
        return v


class RentCheckResponse(BaseModel):
    ok: bool = True
    requested_rent_eur: float
    fair_rent_eur: float = Field(..., description="Vergleichsmiete ortsüblich")
    delta_percent: float = Field(..., description="Abweichung in % (positiv = zu teuer)")
    verdict: Verdict
    legal_basis: str = Field(..., description="Welche Norm greift: §558 BGB / §556d BGB / §5 WiStrG")
    explanation: str = Field(..., description="Kurzform: was das Urteil bedeutet")
    comparison: ComparisonData
    quellen: list[SourceCitation] = Field(
        default_factory=list,
        description="Citable sources the verdict was derived from (URL + Jahr + Publisher)",
    )
    recommendation: str = Field(..., description="Konkrete Handlungsempfehlung")


# ----------------------------------------------------------------------
# /api/widerspruch — Widerspruchsgenerator
# ----------------------------------------------------------------------


class WiderspruchRequest(BaseModel):
    """Input for generating a formal objection letter to the landlord."""

    check_response: RentCheckResponse
    landlord_name: Optional[str] = Field(None, description="Name des Vermieters")
    tenant_name: str = Field(..., min_length=2)
    tenant_address: str = Field(..., min_length=5)


class WiderspruchResponse(BaseModel):
    ok: bool = True
    subject: str = Field(..., description="Betreff-Zeile")
    body: str = Field(..., description="Briefkorpus")
    references: list[str] = Field(
        default_factory=list,
        description="Rechtsgrundlagen die im Brief zitiert werden",
    )
    next_steps: list[str] = Field(
        default_factory=list,
        description="Konkrete nächste Schritte für Mieter",
    )


# ----------------------------------------------------------------------
# Generic
# ----------------------------------------------------------------------


class ErrorResponse(BaseModel):
    ok: bool = False
    error: str
    detail: Optional[str] = None


class HealthResponse(BaseModel):
    status: str
    version: str
    mistral_configured: bool


class CityInfo(BaseModel):
    slug: str
    name: str
    average_eur_per_sqm: float
    federal_state: str
    source: str
    source_url: str = Field(..., description="Direkter Link zum Mietspiegel/Marktbericht")
    year: int = Field(..., description="Stichtag/Jahr des Datensatzes")
    kind: str = Field(..., description="qualifizierter_mietspiegel | einfacher_mietspiegel | marktbericht")
    publisher: str = Field(..., description="Herausgeber (z.B. Senatsverwaltung Berlin)")
    spread_eur_per_sqm_low: float = Field(..., description="Untergrenze ortsübliche Spanne (€/m²)")
    spread_eur_per_sqm_high: float = Field(..., description="Obergrenze ortsübliche Spanne (€/m²)")