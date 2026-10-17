"""Schemas für das Eigentums-Portfolio (Pro)."""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class ObjektArt(Enum):
    ETW = "etw"
    HAUS = "haus"


class ImmobilienObjekt(BaseModel):
    """Eine Eigentumswohnung oder ein Haus im Portfolio."""
    bezeichnung: str = Field(..., min_length=1, max_length=100)
    art: ObjektArt = ObjektArt.ETW
    adresse: str | None = None
    plz: str | None = Field(None, pattern=r"^\d{5}$")
    stadt: str | None = None
    wohnflaeche_qm: float | None = Field(None, gt=0)
    kaufpreis_eur: float | None = Field(None, gt=0)
    jahresnettokaltmiete_eur: float | None = Field(None, ge=0)
    nebenkosten_eur: float = 0
    baujahr: int | None = Field(None, ge=1800, le=2030)
    verwaltungskosten_eur: float = 0


class PortfolioStatistik(BaseModel):
    anzahl_objekte: int
    gesamt_kaufpreis_eur: float
    gesamt_nettojahresmiete_eur: float
    gesamtrendite_prozent: float | None = None
    avg_miete_pro_qm_eur: float | None = None
    markt_uebersicht: str


class PortfolioResponse(BaseModel):
    ok: bool = True
    objekte: list[dict] = []
    gesamt: PortfolioStatistik


__all__ = ["ObjektArt", "ImmobilienObjekt", "PortfolioStatistik", "PortfolioResponse"]
