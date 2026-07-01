"""Vergleichsmieten-Engine.

Property-based scoring (§558 BGB Wohnwertmerkmale) with city/PLZ-resolved
comparison data sourced from official Mietspiegel / Marktberichte.

Replaces the previous hardcoded city dictionary with a JSON-backed
`data_loader` lookup that carries full source attribution
(publisher, year, kind, URL). Every RentCheckResponse now points at the
exact document the verdict was derived from — required for any
Widerspruchsschreiben to be defensible.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from app.schemas.contracts import (
    ComparisonData,
    ParsedLease,
    RentCheckRequest,
    RentCheckResponse,
    SourceCitation,
    Verdict,
)
from app.services.data_loader import CityDataset, get_database


# ----------------------------------------------------------------------
# Property-based scoring constants (Wohnwertmerkmale §558 BGB)
# ----------------------------------------------------------------------

LOCATION_FACTORS = {
    "einfach": 0.94,
    "mittel": 1.00,
    "gut": 1.08,
}

BUILD_YEAR_FACTORS = [
    # (max_year_exclusive, factor) — sorted descending
    (1850, 0.85),   # pre-1850
    (1919, 0.92),   # Altbau pre-war
    (1960, 0.96),
    (1990, 0.98),
    (2010, 1.05),
    (2030, 1.10),   # post-2010 (Neubau premium)
]


def build_year_factor(year: Optional[int]) -> float:
    """Return the build-year premium factor (1.0 = neutral)."""
    if not year:
        return 1.0
    for max_year, factor in BUILD_YEAR_FACTORS:
        if year < max_year:
            return factor
    return 1.0


def location_factor(quality: Optional[str]) -> float:
    return LOCATION_FACTORS.get((quality or "mittel").lower(), 1.0)


# ----------------------------------------------------------------------
# Verdict → legal basis mapping
# ----------------------------------------------------------------------

def classify_verdict(delta_percent: float) -> Verdict:
    """Map deviation % to legal verdict.

    Thresholds (matches frontend RentChecker.astro):
      delta ≤ 10% : fair (ortsüblich)
      delta ≤ 20% : tolerance (gerade noch im Rahmen, §558 Abs. 2 BGB)
      delta ≤ 50% : brake (Mietpreisbremse-Schwelle, §556d BGB)
      delta > 50% : wucher (Wucher, §5 WiStrG, strafbar)
    """
    if delta_percent <= 10.0:
        return Verdict.FAIR
    if delta_percent <= 20.0:
        return Verdict.TOLERANCE
    if delta_percent <= 50.0:
        return Verdict.BRAKE
    return Verdict.WUCHER


LEGAL_BASIS = {
    Verdict.FAIR: "§558 Abs. 2 BGB (ortsübliche Vergleichsmiete)",
    Verdict.TOLERANCE: "§558 Abs. 2 BGB (innerhalb der 20%-Spanne)",
    Verdict.BRAKE: "§556d BGB i.V.m. §556g BGB (Mietpreisbremse, Verstoß vermutet)",
    Verdict.WUCHER: "§5 WiStrG (Wucher, Mietpreis über 50% ortsüblich)",
}

EXPLANATIONS = {
    Verdict.FAIR: "Die Miete liegt im ortsüblichen Rahmen.",
    Verdict.TOLERANCE: "Die Miete ist erhöht, aber noch innerhalb der zulässigen Spanne.",
    Verdict.BRAKE: "Die Miete überschreitet die Mietpreisbremse-Schwelle. Widerspruch prüfen.",
    Verdict.WUCHER: "Wucher-Verdacht: Miete liegt >50% über dem ortsüblichen Niveau. Sofort Widerspruch einlegen.",
}

RECOMMENDATIONS = {
    Verdict.FAIR: "Kein Handlungsbedarf. Vergleichsmiete ist plausibel.",
    Verdict.TOLERANCE: "Optional: Verhandlung über Modernisierung oder Ausstattung. Bei Neuverträgen kein automatisches Widerspruchsrecht.",
    Verdict.BRAKE: "Widerspruchsschreiben an Vermieter senden (Vorlage wird generiert). Bei Bestandsmieten Mietpreisbremse-Auskunft verlangen.",
    Verdict.WUCHER: "Anwalt oder Mieterverein kontaktieren. Wucher ist nach §5 WiStrG strafbar; Strafanzeige möglich.",
}


# ----------------------------------------------------------------------
# Core engine
# ----------------------------------------------------------------------

PLZ_PATTERN = re.compile(r"\b(\d{5})\b")


def compute_fair_rent(
    *,
    city_avg_eur_per_sqm: float,
    size_sqm: float,
    build_year: Optional[int],
    location_quality: Optional[str],
) -> float:
    """Compute the fair monthly rent (Kaltmiete) in EUR."""
    by = build_year_factor(build_year)
    loc = location_factor(location_quality)
    fair_per_m2 = city_avg_eur_per_sqm * by * loc
    return round(fair_per_m2 * size_sqm, 2)


def _build_comparison(ds: CityDataset, size_sqm: float) -> ComparisonData:
    """Compose the ComparisonData with full source attribution."""
    return ComparisonData(
        city=ds.name,
        city_average_eur_per_sqm=ds.average_eur_per_sqm,
        reference_size_sqm=size_sqm,  # we computed against the actual size
        source=(
            f"{ds.source_title} ({ds.year}) · {ds.publisher} · "
            f"{ds.kind.replace('_', ' ')} · {ds.source_url}"
        ),
        fetched_at=datetime.now(timezone.utc).isoformat(),
    )


def _build_citation(ds: CityDataset) -> SourceCitation:
    """Build a citable SourceCitation from a dataset."""
    return SourceCitation(
        title=ds.source_title,
        publisher=ds.publisher,
        url=ds.source_url,
        year=ds.year,
        kind=ds.kind,
        spread_eur_per_sqm_low=ds.spread_eur_per_sqm_low,
        spread_eur_per_sqm_high=ds.spread_eur_per_sqm_high,
    )


@dataclass
class CityMatchResult:
    """Internal record of which dataset was picked and why."""

    dataset: CityDataset
    via: str  # "slug" | "name" | "plz"


def resolve_city(
    address: str,
    postal_code: Optional[str] = None,
) -> Optional[CityMatchResult]:
    """Resolve a city from address text and optional explicit PLZ.

    Lookup priority:
      1. Explicit postal_code → PLZ-prefix lookup in the database
      2. Substring match on the address (name or slug)
    """
    db = get_database()

    # 1. PLZ-first
    plz = (postal_code or "").strip()
    if not plz:
        m = PLZ_PATTERN.search(address or "")
        if m:
            plz = m.group(1)

    if plz:
        hits = db.find_by_plz(plz)
        if hits:
            return CityMatchResult(dataset=hits[0], via="plz")

    # 2. Substring fallback
    if address:
        addr_lower = address.lower()
        for ds in db.all():
            if ds.name.lower() in addr_lower or ds.slug in addr_lower:
                return CityMatchResult(dataset=ds, via="name")

    return None


async def evaluate(req: RentCheckRequest) -> RentCheckResponse:
    """Public entry point: takes request, returns full response with verdict."""
    lease = req.lease
    if lease is None:
        if not (req.address and req.cold_rent_eur and req.size_sqm):
            raise ValueError(
                "Either `lease` or (address, cold_rent_eur, size_sqm) is required"
            )
        lease = ParsedLease(
            address=req.address,
            cold_rent_eur=req.cold_rent_eur,  # type: ignore[arg-type]
            size_sqm=req.size_sqm,  # type: ignore[arg-type]
            postal_code=req.postal_code,
            build_year=req.build_year,
            location_quality=(
                req.location_quality.value if req.location_quality else None
            ),
        )

    match = resolve_city(lease.address, lease.postal_code)
    if match is None:
        raise LookupError(
            f"Keine Vergleichsdaten für Adresse '{lease.address}'. "
            "Bitte Postleitzahl ergänzen oder Stadt manuell wählen."
        )

    ds = match.dataset

    fair = compute_fair_rent(
        city_avg_eur_per_sqm=ds.average_eur_per_sqm,
        size_sqm=lease.size_sqm,
        build_year=lease.build_year,
        location_quality=lease.location_quality,
    )

    delta_pct = (
        round((lease.cold_rent_eur - fair) / fair * 100, 2) if fair > 0 else 0.0
    )
    verdict = classify_verdict(delta_pct)

    return RentCheckResponse(
        requested_rent_eur=lease.cold_rent_eur,
        fair_rent_eur=fair,
        delta_percent=delta_pct,
        verdict=verdict,
        legal_basis=LEGAL_BASIS[verdict],
        explanation=EXPLANATIONS[verdict],
        comparison=_build_comparison(ds, lease.size_sqm),
        quellen=[_build_citation(ds)],
        recommendation=RECOMMENDATIONS[verdict],
    )


# ----------------------------------------------------------------------
# Backward-compat shim for /api/cities route (kept in routes.py)
# ----------------------------------------------------------------------

def all_cities() -> list[dict]:
    """Return all datasets as plain dicts (matches old CityRecord shape)."""
    db = get_database()
    return [
        {
            "slug": ds.slug,
            "name": ds.name,
            "federal_state": ds.federal_state,
            "average_eur_per_sqm": ds.average_eur_per_sqm,
            "source": f"{ds.source_title} ({ds.year}) — {ds.publisher}",
            "year": ds.year,
            "kind": ds.kind,
            "source_url": ds.source_url,
            "reference_size_sqm": 65.0,
        }
        for ds in db.all()
    ]