"""Schemas für den Wohnungssuche-Agenten (/api/search/*).

Design nach dem bewährten Open-Source-Muster (Fredy, ImmoSpider, ImmoPushr):
Der Nutzer konfiguriert eine oder mehrere Such-URLs der Portale — der
Agent pollt sie periodisch, dedupliziert per Eindeutig-Hash und
benachrichtigt bei neuen Treffern. Kein API-Key nötig, keine ToS-Grauzone
bei RSS-Feeds.

Zusätzliches Alleinstellungsmerkmal: Jeder Treffer wird automatisch
gegen den Mietspiegel geprüft (Badge fair/überhöht) — das kann kein
kommerzieller Anbieter.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, HttpUrl


class Provider(Enum):
    """Unterstützte Datenquellen-Protokolle.

    RSS ist der rechtssichere Erstkontakt (offizielle Portal-Feeds).
    HTML ist für Portale ohne Feed — bewusst als 'best effort' markiert,
    da Portale Anti-Bot-Schutz (AWS WAF etc.) einsetzen können.
    """
    RSS = "rss"
    HTML = "html"


class SearchProfile(BaseModel):
    """Ein Suchprofil: Portal-Such-URL + Optionen."""
    name: str = Field(..., min_length=1, max_length=100, description="Anzeigename")
    url: HttpUrl = Field(..., description="Such-URL des Portals (per Copy aus der Portalsuche)")
    provider: Provider = Field(Provider.RSS, description="rss (bevorzugt) | html (best effort)")
    city: str | None = Field(
        None,
        description="Stadt für den Mietpreis-Check (falls leer: Auto-Erkennung aus Treffer-PLZ)",
    )
    active: bool = True


class Listing(BaseModel):
    """Ein gefundenes Angebot."""
    id: str = Field(..., description="Portal-ID oder Hash")
    title: str
    url: str | None = None
    city: str | None = None
    postal_code: str | None = None
    cold_rent_eur: float | None = None
    size_sqm: float | None = None
    rooms: float | None = None
    source: str = Field(..., description="Portal-Name, z.B. 'immoscout24'")
    first_seen_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())

    def dedup_key(self) -> str:
        """Eindeutiger Hash für Duplikat-Erkennung über Portale hinweg.

        Bewusst grob (Stadt + gerundete Größe + gerundete Miete), damit
        dieselbe Wohnung auf verschiedenen Portalen erkannt wird.
        """
        import hashlib

        raw = "|".join(
            str(x or "")
            for x in [
                self.city,
                round(self.size_sqm or 0),
                round(self.cold_rent_eur or 0),
                self.rooms,
            ]
        )
        return hashlib.sha256(raw.encode()).hexdigest()[:16]


class RentBadge(Enum):
    """Mietpreis-Badge aus der bestehenden Engine."""
    FAIR = "fair"
    TOLERANCE = "tolerance"
    BRAKE = "brake"
    WUCHER = "wucher"
    UNKNOWN = "unknown"


class CheckedListing(Listing):
    """Treffer mit integriertem Mietpreis-Check (Wohnprofil)."""
    rent_badge: RentBadge = RentBadge.UNKNOWN
    fair_rent_eur: float | None = None
    delta_percent: float | None = None
    overpayment_eur_per_year: float | None = Field(
        None,
        description="Überzahlung vs. Mietspiegel in €/Jahr (nur wenn Miete > fair)",
    )
    legal_basis: str | None = Field(
        None, description="§556d BGB / §5 WiStrG je nach Badge-Stufe"
    )
    recommendation: str | None = Field(
        None, description="Konkrete Handlungsempfehlung für diese Wohnung"
    )


class RefreshResponse(BaseModel):
    ok: bool = True
    profile_name: str
    new_listings: list[CheckedListing] = Field(default_factory=list)
    total_seen: int = Field(..., ge=0, description="Alle jemals gesehenen Treffer")
    checked_at: str


class SearchResultsResponse(BaseModel):
    ok: bool = True
    listings: list[CheckedListing] = Field(default_factory=list)
    profile_name: str
    total_seen: int


__all__ = [
    "Provider",
    "SearchProfile",
    "Listing",
    "CheckedListing",
    "RentBadge",
    "RefreshResponse",
    "SearchResultsResponse",
]
