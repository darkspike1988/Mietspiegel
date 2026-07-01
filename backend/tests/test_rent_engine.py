"""Tests for app.services.rent_engine — fair-rent math, verdict thresholds,
PLZ/address resolution, end-to-end evaluate().

Run:  cd backend && source .venv/bin/activate && pytest tests/test_rent_engine.py
"""
from __future__ import annotations

import math

import pytest

from app.services.rent_engine import (
    LeaseEvaluation, Verdict, build_year_factor, compute_fair_rent,
    evaluate, location_factor, resolve_city,
)


# ----- 1. Baujahr-Faktor-Tabelle -----------------------------------

@pytest.mark.parametrize("year,expected", [
    (1918, 0.92),  # Altbau vor 1919
    (1930, 0.96),  # Zwischenkriegsbau
    (1960, 0.98),  # 1960-1989 Bucket
    (1975, 0.98),
    (1989, 0.98),
    (1990, 1.05),  # 1990-2009 Bucket
    (2000, 1.05),
    (2009, 1.05),
    (2010, 1.10),  # 2010-2030 Bucket
    (2020, 1.10),  # 2020er-Neubau
    (2025, 1.10),
])
def test_build_year_factor(year: int, expected: float):
    assert math.isclose(build_year_factor(year), expected, rel_tol=1e-9)


# ----- 2. Lagequalitäts-Faktor-Tabelle -----------------------------

@pytest.mark.parametrize("quality,expected", [
    ("einfach", 0.92),
    ("mittel", 1.00),
    ("gut", 1.08),
    ("sehr_gut", 1.15),
])
def test_location_factor(quality: str, expected: float):
    assert math.isclose(location_factor(quality), expected, rel_tol=1e-9)


def test_location_factor_unknown_defaults_to_one():
    """Unbekannte Lage darf nicht crashen — Fallback auf 1.0 ist defensiv
    sinnvoll, weil die Engine dann zumindest eine Schätzung liefert."""
    assert location_factor("unbekannt") == 1.0


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
    = 12 × 1.10 × 1.08 × 70 = 998.64 €."""
    fr = compute_fair_rent(
        city_avg_eur_per_sqm=12.0,
        size_sqm=70.0,
        build_year=2015,
        location_quality="gut",
    )
    assert math.isclose(fr, 998.64, abs_tol=0.01)


def test_compute_fair_rent_altbau_einfache_lage():
    """Vor 1919 (0.92) · einfache Lage (0.92) · 45 m² · 6 €/m²
    = 6 × 0.92 × 0.92 × 45 = 228.53 €."""
    fr = compute_fair_rent(
        city_avg_eur_per_sqm=6.0,
        size_sqm=45.0,
        build_year=1910,
        location_quality="einfach",
    )
    assert math.isclose(fr, 228.53, abs_tol=0.01)


# ----- 4. Verdict-Schwellen ----------------------------------------

@pytest.mark.parametrize("delta_pct,expected", [
    (0.0, Verdict.FAIR),
    (10.0, Verdict.FAIR),       # genau an Grenze → fair
    (10.01, Verdict.TOLERANCE),  # 10.01 → tolerance
    (20.0, Verdict.TOLERANCE),
    (20.01, Verdict.BRAKE),
    (50.0, Verdict.BRAKE),
    (50.01, Verdict.WUCHER),
    (200.0, Verdict.WUCHER),
])
def test_verdict_thresholds(delta_pct: float, expected: Verdict):
    """Verdict-Mapping:
       ≤10%  fair · ≤20% tolerance · ≤50% brake · >50% wucher
    """
    from app.services.rent_engine import classify_verdict
    assert classify_verdict(delta_pct) == expected


# ----- 5. Stadt-Resolution -----------------------------------------

def test_resolve_city_via_plz_in_address():
    """resolve_city() muss PLZ aus dem Adress-String extrahieren."""
    ds = resolve_city("Hauptstr. 1, 80331 München")
    assert ds is not None
    assert ds.slug == "muenchen"


def test_resolve_city_unknown_address_returns_none():
    """Adresse ohne bekannte PLZ → None, kein Crash."""
    assert resolve_city("Atlantis, 99999 Traumland") is None


# ----- 6. End-to-End evaluate() ------------------------------------

def _req(
    *,
    address: str = "Hauptstr 1, 10115 Berlin",
    postal_code: str = "10115",
    cold_rent_eur: float = 600.0,
    size_sqm: float = 60.0,
    build_year: int = 1985,
    location_quality: str = "mittel",
) -> dict:
    return {
        "address": address,
        "postal_code": postal_code,
        "cold_rent_eur": cold_rent_eur,
        "size_sqm": size_sqm,
        "build_year": build_year,
        "location_quality": location_quality,
    }


def test_evaluate_berlin_fair_rent_within_tolerance():
    """600 € Miete auf 60 m² Berlin 1985 mittel:
    fair=496.86 €, delta=(600-496.86)/496.86 = +20.77% → tolerance."""
    out = evaluate(_req(cold_rent_eur=600.0))
    assert isinstance(out, LeaseEvaluation)
    assert math.isclose(out.fair_rent_eur, 496.86, abs_tol=0.01)
    assert out.verdict in (Verdict.TOLERANCE, Verdict.BRAKE)  # exakt +20.77%
    assert out.delta_percent > 0
    # Quellenangabe muss enthalten sein (juristisch zitierbar)
    assert out.quellen and len(out.quellen) >= 1
    assert "Berlin" in out.quellen[0].publisher or "Berlin" in out.quellen[0].title


def test_evaluate_brake_threshold_50_percent():
    """Miete bei fair_rent × 1.50 → genau 50% drüber → brake."""
    fair = compute_fair_rent(8.45, 60.0, 1985, "mittel")
    brake_rent = fair * 1.50
    out = evaluate(_req(cold_rent_eur=brake_rent))
    assert out.verdict == Verdict.BRAKE


def test_evaluate_wucher_threshold_over_50_percent():
    """Miete > 50% über fair_rent → wucher (§5 WiStrG)."""
    fair = compute_fair_rent(8.45, 60.0, 1985, "mittel")
    wucher_rent = fair * 1.81  # +81%
    out = evaluate(_req(cold_rent_eur=wucher_rent))
    assert out.verdict == Verdict.WUCHER
    assert "WiStrG" in out.legal_basis or "§5" in out.legal_basis


def test_evaluate_fair_rent_zero_delta_is_fair():
    """Miete == fair_rent → 0% delta → fair."""
    fair = compute_fair_rent(8.45, 60.0, 1985, "mittel")
    out = evaluate(_req(cold_rent_eur=fair))
    assert math.isclose(out.delta_percent, 0.0, abs_tol=1e-6)
    assert out.verdict == Verdict.FAIR


def test_evaluate_unknown_address_raises_lookup_error():
    """Ungültige Adresse (PLZ 00000) → ValueError, kein leises None."""
    with pytest.raises(ValueError, match="(?i)(stadt|plz|unknown)"):
        evaluate(_req(postal_code="00000", address="Atlantis, 00000 Utopia"))


def test_evaluate_explanation_text_present_for_all_verdicts():
    """Jedes Verdict braucht eine Erklärung — juristisch zwingend."""
    for rent, expected in [
        (300.0, Verdict.FAIR),     # weit unter fair
        (520.0, Verdict.TOLERANCE),  # knapp drüber
        (760.0, Verdict.BRAKE),    # ~53% drüber
        (1000.0, Verdict.WUCHER),  # ~101% drüber
    ]:
        out = evaluate(_req(cold_rent_eur=rent))
        assert out.verdict == expected, f"rent={rent}, expected={expected}"
        assert out.explanation and len(out.explanation) > 20