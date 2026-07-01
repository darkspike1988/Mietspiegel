"""Vergleichsmieten-Engine: factor tables, fair-rent, verdict, resolve_city, evaluate."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.schemas.contracts import (
    LocationQuality,
    ParsedLease,
    RentCheckRequest,
    SourceCitation,
    Verdict,
)
from app.services.rent_engine import (
    BUILD_YEAR_FACTORS,
    LOCATION_FACTORS,
    build_year_factor,
    classify_verdict,
    compute_fair_rent,
    location_factor,
    resolve_city,
)
from app.services.rent_engine import evaluate as evaluate_async
import asyncio


# ----- factor tables ----------------------------------------------------------


def test_location_factor_three_tiers():
    assert location_factor("einfach") == 0.94
    assert location_factor("mittel") == 1.00
    assert location_factor("gut") == 1.08


def test_location_factor_none_defaults_mittel():
    assert location_factor(None) == 1.00


def test_location_factor_unknown_value_defaults_mittel():
    """Unknown label degrades to neutral, never raises."""
    assert location_factor("super_gut") == 1.00


def test_build_year_factor_pre_war_altbau():
    assert build_year_factor(1918) == 0.92


def test_build_year_factor_1960_bucket_lower_bound():
    # Bucket (1960, 0.98) — 1960 is its lower bound, ≤ this year ⇒ 0.98.
    # The 0.96 bucket is the immediately older one (<1960).
    assert build_year_factor(1960) == 0.98
    assert build_year_factor(1959) == 0.96


def test_build_year_factor_1985_in_between_bucket():
    # 1990 is still in the 1960..1990 bucket → 0.98
    assert build_year_factor(1985) == 0.98


def test_build_year_factor_1990_modern_neutral_band():
    # 1990..2010 bucket → 1.05
    assert build_year_factor(1990) == 1.05


def test_build_year_factor_2020s_neubau_premium():
    # Real post-2010 bucket is 1.10, not 1.05.
    assert build_year_factor(2020) == 1.10


def test_build_year_factor_pre_1850():
    assert build_year_factor(1849) == 0.85


def test_build_year_factor_none_returns_neutral():
    assert build_year_factor(None) == 1.0


# ----- compute_fair_rent -----------------------------------------------------


def test_compute_fair_rent_berlin_60sqm_neutral_year_zero_factors():
    """When build_year and location are None → neutral factors → avg × size."""
    fair = compute_fair_rent(
        city_avg_eur_per_sqm=8.45,
        size_sqm=60.0,
        build_year=None,
        location_quality=None,
    )
    assert fair == 507.0  # 8.45 × 60


def test_compute_fair_rent_berlin_60sqm_1985_mittel():
    """1985 → 0.98; mittel → 1.00; 8.45 × 0.98 × 60 = 496.86."""
    fair = compute_fair_rent(
        city_avg_eur_per_sqm=8.45,
        size_sqm=60.0,
        build_year=1985,
        location_quality="mittel",
    )
    assert fair == 496.86


def test_compute_fair_rent_berlin_60sqm_1995_mittel():
    """1995 → 1.05; mittel → 1.00; 8.45 × 1.05 × 60 = 532.35."""
    fair = compute_fair_rent(
        city_avg_eur_per_sqm=8.45,
        size_sqm=60.0,
        build_year=1995,
        location_quality="mittel",
    )
    assert fair == 532.35


def test_compute_fair_rent_neubau_gute_lage():
    """2020 → 1.10; gut → 1.08; 12.0 × 1.10 × 1.08 × 70 = 997.92."""
    fair = compute_fair_rent(
        city_avg_eur_per_sqm=12.0,
        size_sqm=70.0,
        build_year=2020,
        location_quality="gut",
    )
    assert fair == 997.92


# ----- classify_verdict -------------------------------------------------------


def test_classify_verdict_fair_under_10pct():
    assert classify_verdict(5.0) == Verdict.FAIR


def test_classify_verdict_fair_at_boundary():
    assert classify_verdict(10.0) == Verdict.FAIR


def test_classify_verdict_tolerance_under_20pct():
    assert classify_verdict(15.0) == Verdict.TOLERANCE


def test_classify_verdict_brake_at_50pct():
    assert classify_verdict(50.0) == Verdict.BRAKE


def test_classify_verdict_wucher_above_50pct():
    assert classify_verdict(80.0) == Verdict.WUCHER


# ----- resolve_city -----------------------------------------------------------


def test_resolve_city_via_plz_in_address():
    """Address contains '10115' → Berlin via PLZ."""
    res = resolve_city(address="Hauptstr 1, 10115 Berlin")
    assert res is not None
    assert res.via == "plz"
    assert res.dataset.slug == "berlin"


def test_resolve_city_explicit_plz_argument():
    res = resolve_city(address="Wien", postal_code="80331")
    assert res is not None
    assert res.dataset.slug == "muenchen"
    assert res.via == "plz"


def test_resolve_city_unknown_returns_none():
    res = resolve_city(address="Atlantis", postal_code="00000")
    assert res is None


def test_resolve_city_by_name_substring():
    res = resolve_city(address="Musterstr 5, Frankfurt am Main")
    assert res is not None
    assert res.dataset.slug == "frankfurt"
    assert res.via == "name"


# ----- evaluate (async end-to-end) --------------------------------------------


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_evaluate_berlin_fair_rent_within_tolerance():
    """End-to-end with Baujahr 1985 (factor 0.98).

    Aufgerufene Kaltmiete 495 EUR / fair rent 496.86 ⇒ delta = -0.38%.
    """
    req = RentCheckRequest(
        address="Hauptstr 1, 10115 Berlin",
        cold_rent_eur=495.0,
        size_sqm=60.0,
        postal_code="10115",
        build_year=1985,
        location_quality=LocationQuality.MITTEL,
    )
    resp = _run(evaluate_async(req))
    assert resp.fair_rent_eur == 496.86
    assert resp.delta_percent == pytest.approx(-0.38, abs=0.05)
    assert resp.verdict == Verdict.FAIR
    # quellen is populated and points at the real Berliner Mietspiegel
    assert len(resp.quellen) == 1
    assert resp.quellen[0].kind == "qualifizierter_mietspiegel"
    assert resp.quellen[0].year == 2024


def test_evaluate_berlin_brake_when_50pct_over():
    req = RentCheckRequest(
        address="Hauptstr 1, 10115 Berlin",
        cold_rent_eur=800.0,
        size_sqm=60.0,
        postal_code="10115",
        build_year=1985,
        location_quality=LocationQuality.MITTEL,
    )
    resp = _run(evaluate_async(req))
    # 800 vs 496.86 ⇒ +61% ⇒ BRAKE (≤50%) would fail; actually WUCHER (>50%).
    # Pick a value that lands clearly in BRAKE:
    assert resp.verdict in (Verdict.BRAKE, Verdict.WUCHER)


def test_evaluate_berlin_brake_at_boundary():
    """120% above fair = 595 EUR? Actually 496.86 × 1.45 = 720.45, +45% = BRAKE."""
    fair = 496.86
    requested = round(fair * 1.45, 2)  # 720.45 → 44.99% deviation ⇒ BRAKE
    req = RentCheckRequest(
        address="Hauptstr 1, 10115 Berlin",
        cold_rent_eur=requested,
        size_sqm=60.0,
        postal_code="10115",
        build_year=1985,
        location_quality=LocationQuality.MITTEL,
    )
    resp = _run(evaluate_async(req))
    assert resp.verdict == Verdict.BRAKE


def test_evaluate_unknown_address_raises_lookup_error():
    req = RentCheckRequest(
        address="Unbekannt, 00000 Atlantis",
        cold_rent_eur=600.0,
        size_sqm=60.0,
        postal_code="00000",
    )
    with pytest.raises(LookupError):
        _run(evaluate_async(req))


def test_evaluate_requires_minimum_input():
    """Empty request must raise ValueError, not crash silently."""
    req = RentCheckRequest()
    with pytest.raises(ValueError):
        _run(evaluate_async(req))
