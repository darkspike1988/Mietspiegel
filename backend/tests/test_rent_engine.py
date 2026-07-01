"""Tests for app.services.rent_engine — fair-rent math, verdict thresholds,
PLZ/address resolution, end-to-end evaluate().

Run:  cd backend && source .venv/bin/activate && pytest tests/test_rent_engine.py
"""
from __future__ import annotations

import math

import pytest

from app.schemas.contracts import RentCheckRequest, RentCheckResponse, Verdict
from app.services.rent_engine import (
    BUILD_YEAR_FACTORS, LOCATION_FACTORS,
    build_year_factor, classify_verdict, compute_fair_rent,
    evaluate, location_factor, resolve_city,
)


# ----- 1. Baujahr-Faktor-Tabelle -----------------------------------

@pytest.mark.parametrize("year,expected", [
    (1500, 0.85),   # <1850
    (1849, 0.85),   # <1850
    (1850, 0.92),   # <1919 (Grenze inklusiv für den nächsten Bucket)
    (1900, 0.92),   # <1919
    (1950, 0.96),   # <1960
    (1985, 0.98),   # <1990
    (2005, 1.05),   # <2010
    (2025, 1.10),   # <2030 (Neubau)
])
def test_build_year_factor(year: int, expected: float):
    assert math.isclose(build_year_factor(year), expected, rel_tol=1e-9)


def test_build_year_factor_none_returns_one():
    """Kein Baujahr bekannt → neutraler Faktor (defensiv)."""
    assert build_year_factor(None) == 1.0


def test_build_year_factor_far_future_returns_one():
    """year=2100 (>2030) durchläuft die Schleife ohne Treffer → 1.0."""
    assert build_year_factor(2100) == 1.0


def test_build_year_factors_table_is_sorted_ascending():
    """build_year_factor() iteriert mit 'if year < max_year' von vorne;
    Reihenfolge muss ascending sein, sonst geraten Baujahre in falsche Buckets."""
    years = [max_year for max_year, _ in BUILD_YEAR_FACTORS]
    assert years == sorted(years), (
        f"BUILD_YEAR_FACTORS must be sorted ascending by max_year, got {years}"
    )


# ----- 2. Lagequalitäts-Faktor-Tabelle -----------------------------

@pytest.mark.parametrize("quality,expected", [
    ("einfach", 0.94),
    ("mittel", 1.00),
    ("gut", 1.08),
])
def test_location_factor_known(quality: str, expected: float):
    assert math.isclose(location_factor(quality), expected, rel_tol=1e-9)


def test_location_factor_unknown_defaults_to_one():
    """Unbekannte Lage darf nicht crashen — Fallback 1.0 ist defensiv."""
    assert location_factor("unbekannt") == 1.0


def test_location_factor_none_defaults_to_one():
    """location_factor(None) → 1.0 (siehe Engine-Implementierung)."""
    assert location_factor(None) == 1.0


def test_location_factors_table_size_is_three():
    """3 Wohnlagen-Kategorien — wenn später 'sehr_gut' dazukommt, bricht
    dieser Test und zwingt zur bewussten Erweiterung der Schwellenwerte."""
    assert len(LOCATION_FACTORS) == 3


# ----- 3. Fair-Rent-Berechnung (compute_fair_rent) -----------------

def test_compute_fair_rent_berlin_60sqm_year_1985_mittel():
    """60 m² · 8.45 €/m² · 0.98 (Baujahr 1985) · 1.00 (mittel) = 496.86 €."""
    fr = compute_fair_rent(
        city_avg_eur_per_sqm=8.45,
        size_sqm=60.0,
        build_year=1985,
        location_quality="mittel",
    )
    assert math.isclose(fr, 496.86, abs_tol=0.01)


def test_compute_fair_rent_berlin_60sqm_year_1995_mittel():
    """1990-2009 Bucket (Faktor 1.05). 8.45 × 1.05 × 60 = 532.35 €."""
    fr = compute_fair_rent(
        city_avg_eur_per_sqm=8.45,
        size_sqm=60.0,
        build_year=1995,
        location_quality="mittel",
    )
    assert math.isclose(fr, 532.35, abs_tol=0.01)


def test_compute_fair_rent_neubau_gute_lage():
    """Neubau (2015, 1.10) · gute Lage (1.08) · 70 m² · 12 €/m²
    = 12 × 1.10 × 1.08 × 70 = 997.92 €."""
    fr = compute_fair_rent(
        city_avg_eur_per_sqm=12.0,
        size_sqm=70.0,
        build_year=2015,
        location_quality="gut",
    )
    assert math.isclose(fr, 997.92, abs_tol=0.01)


def test_compute_fair_rent_altbau_einfache_lage():
    """Vor 1919 (0.92) · einfache Lage (0.94) · 45 m² · 6 €/m²
    = 6 × 0.92 × 0.94 × 45 = 233.50 € (Engine rundet auf 2 NK)."""
    fr = compute_fair_rent(
        city_avg_eur_per_sqm=6.0,
        size_sqm=45.0,
        build_year=1910,
        location_quality="einfach",
    )
    assert math.isclose(fr, 233.50, abs_tol=0.01)


def test_compute_fair_rent_returns_rounded_float():
    """compute_fair_rent rundet auf 2 Nachkommastellen (EUR-Optik)."""
    fr = compute_fair_rent(
        city_avg_eur_per_sqm=8.456,
        size_sqm=60.0,
        build_year=1985,
        location_quality="mittel",
    )
    # 8.456 × 0.98 × 1.0 × 60 = 497.2128 → 497.21
    assert round(fr, 2) == fr


# ----- 4. Verdict-Schwellen ----------------------------------------

@pytest.mark.parametrize("delta_pct,expected", [
    (0.0, Verdict.FAIR),
    (10.0, Verdict.FAIR),        # genau an Grenze → fair (≤10)
    (10.01, Verdict.TOLERANCE),
    (20.0, Verdict.TOLERANCE),   # genau an Grenze → tolerance (≤20)
    (20.01, Verdict.BRAKE),
    (50.0, Verdict.BRAKE),       # genau an Grenze → brake (≤50)
    (50.01, Verdict.WUCHER),
    (200.0, Verdict.WUCHER),
])
def test_verdict_thresholds(delta_pct: float, expected: Verdict):
    """Verdict-Mapping:
       ≤10%  fair · ≤20% tolerance · ≤50% brake · >50% wucher
    """
    assert classify_verdict(delta_pct) == expected


# ----- 5. Stadt-Resolution -----------------------------------------

def test_resolve_city_via_plz_in_address():
    """resolve_city() extrahiert PLZ aus dem Adress-String (PLZ-first)."""
    result = resolve_city("Hauptstr. 1, 80331 München")
    assert result is not None
    assert result.via == "plz"
    assert result.dataset.slug == "muenchen"


def test_resolve_city_via_explicit_postal_code():
    """resolve_city(address, postal_code='10115') matcht Berlin direkt."""
    result = resolve_city("irgendwo", postal_code="10115")
    assert result is not None
    assert result.dataset.slug == "berlin"


def test_resolve_city_via_name_fallback():
    """Ohne PLZ wird auf Name/Slug substring-gematcht."""
    result = resolve_city("Wohnung in Hamburg, keine PLZ")
    assert result is not None
    assert result.dataset.slug == "hamburg"
    assert result.via == "name"


def test_resolve_city_unknown_returns_none():
    """Keine PLZ, kein matchbarer Stadtname → None."""
    assert resolve_city("Irgendwo in Utopia") is None


# ----- 6. End-to-End evaluate() ------------------------------------

def _req(
    *,
    address: str = "Hauptstr 1, 10115 Berlin",
    postal_code: str = "10115",
    cold_rent_eur: float = 600.0,
    size_sqm: float = 60.0,
    build_year: int = 1985,
    location_quality: str = "mittel",
) -> RentCheckRequest:
    return RentCheckRequest(
        address=address,
        postal_code=postal_code,
        cold_rent_eur=cold_rent_eur,
        size_sqm=size_sqm,
        build_year=build_year,
        location_quality=location_quality,  # type: ignore[arg-type]
    )


def _fair() -> float:
    return compute_fair_rent(city_avg_eur_per_sqm=8.45, size_sqm=60.0, build_year=1985, location_quality="mittel")


@pytest.mark.asyncio
async def test_evaluate_berlin_fair_rent_around_500_eur():
    """600 € auf 60 m² Berlin 1985 mittel:
    fair=496.86 €, delta=(600-496.86)/496.86 ≈ +20.77% → brake (>20% Schwelle)."""
    out = await evaluate(_req(cold_rent_eur=600.0))
    assert isinstance(out, RentCheckResponse)
    assert math.isclose(out.fair_rent_eur, 496.86, abs_tol=0.01)
    assert out.verdict == Verdict.BRAKE
    assert out.delta_percent > 0
    # Quellenangabe muss enthalten sein (juristisch zitierbar)
    assert out.quellen and len(out.quellen) >= 1
    assert "Berlin" in out.quellen[0].publisher or "Berlin" in out.quellen[0].title


@pytest.mark.asyncio
async def test_evaluate_brake_threshold_just_above_50_percent():
    """Miete bei fair_rent × 1.21 → +21% → brake (knapp über 20%-Schwelle)."""
    fair = _fair()
    out = await evaluate(_req(cold_rent_eur=fair * 1.21))
    assert out.verdict == Verdict.BRAKE


@pytest.mark.asyncio
async def test_evaluate_wucher_threshold_over_50_percent():
    """Miete > 50% über fair_rent → wucher (§5 WiStrG)."""
    fair = _fair()
    wucher_rent = fair * 1.81  # +81%
    out = await evaluate(_req(cold_rent_eur=wucher_rent))
    assert out.verdict == Verdict.WUCHER
    assert "WiStrG" in out.legal_basis


@pytest.mark.asyncio
async def test_evaluate_fair_rent_zero_delta_is_fair():
    """Miete == fair_rent → 0% delta → fair."""
    fair = _fair()
    out = await evaluate(_req(cold_rent_eur=fair))
    assert math.isclose(out.delta_percent, 0.0, abs_tol=1e-6)
    assert out.verdict == Verdict.FAIR


@pytest.mark.asyncio
async def test_evaluate_legal_basis_is_set_for_every_verdict():
    """Jedes Verdict braucht eine Rechtsgrundlage — juristisch zwingend."""
    fair = _fair()
    cases = [
        (fair * 0.95, Verdict.FAIR),       # -5% unter fair
        (fair * 1.15, Verdict.TOLERANCE),  # +15% → tolerance
        (fair * 1.40, Verdict.BRAKE),      # +40% → brake
        (fair * 1.81, Verdict.WUCHER),     # +81% → wucher
    ]
    for rent, expected in cases:
        out = await evaluate(_req(cold_rent_eur=rent))
        assert out.verdict == expected, f"rent={rent:.0f}, expected={expected}"
        assert out.legal_basis, (
            f"missing legal_basis for verdict={out.verdict}"
        )
        assert "BGB" in out.legal_basis or "WiStrG" in out.legal_basis


@pytest.mark.asyncio
async def test_evaluate_quellen_cite_real_dataset():
    """Die SourceCitation im Response muss auf den echten Datensatz verweisen
    (publisher, URL, Spread). Frontend rendert das als Klick-Link."""
    out = await evaluate(_req(cold_rent_eur=900.0))
    assert out.quellen
    cite = out.quellen[0]
    assert cite.url.startswith("http")
    assert 2020 <= cite.year <= 2030
    assert cite.spread_eur_per_sqm_low < cite.spread_eur_per_sqm_high


@pytest.mark.asyncio
async def test_evaluate_comparison_has_real_city_average():
    """ComparisonData.city_average_eur_per_sqm muss der Berliner Stadtavg (8.45) entsprechen."""
    out = await evaluate(_req(cold_rent_eur=600.0))
    assert out.comparison.city == "Berlin"
    assert math.isclose(out.comparison.city_average_eur_per_sqm, 8.45, abs_tol=1e-9)
    assert out.comparison.reference_size_sqm == 60.0