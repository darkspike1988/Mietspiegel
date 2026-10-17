"""Schemas für das kombinierte Wohnprofil-Modell."""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.tariff_stats import MarketStats


class DimensionScore(BaseModel):
    """Eine Bewertungs-Dimension des Wohnprofils."""
    weight: int = Field(..., description="Maximalpunkte dieser Dimension")
    points: float = Field(..., description="Erreichte Punkte")
    label: str
    detail: str = Field(..., description="Klartext-Begründung")


class WohnprofilRequest(BaseModel):
    """Input für das Gesamtmodell: eine konkrete Wohnung + Kontext."""
    cold_rent_eur: float = Field(..., gt=0)
    nebenkosten_eur: float = Field(0, ge=0)
    strom_eur: float = Field(0, ge=0, description="Stromgeschäft/Monat")
    net_income_eur: float = Field(..., gt=0)
    delta_percent: float | None = Field(
        None, description="Mietspiegel-Abweichung (aus CheckedListing oder /api/check)"
    )
    market_stats: MarketStats | None = Field(
        None, description="Optional: Marktstatistik der Region (aus /search/market/{id})"
    )


class WohnprofilResponse(BaseModel):
    ok: bool = True
    score: float = Field(..., ge=0, le=100, description="Gesamtscore 0-100")
    ampel: str = Field(..., description="grün | gelb | rot")
    housing_cost_percent: float
    empfehlung: str
    dimensions: list[DimensionScore]


__all__ = ["DimensionScore", "WohnprofilRequest", "WohnprofilResponse"]
