"""Pydantic schema contracts: request/response shape + validator behavior."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.contracts import (
    ComparisonData,
    LocationQuality,
    ParsedLease,
    RentCheckRequest,
    RentCheckResponse,
    SourceCitation,
    Verdict,
)


# ----- ParsedLease ------------------------------------------------------------


def test_parsed_lease_minimal_required_fields():
    lease = ParsedLease(
        address="Hauptstr 1, 10115 Berlin",
        cold_rent_eur=600.0,
        size_sqm=60.0,
    )
    assert lease.postal_code is None
    assert lease.build_year is None
    assert lease.location_quality is None
    assert lease.extras == []


def test_parsed_lease_normalizes_location_quality_case_insensitive():
    lease = ParsedLease(
        address="Hauptstr 1",
        cold_rent_eur=600.0,
        size_sqm=60.0,
        location_quality="GUT",
    )
    assert lease.location_quality == LocationQuality.GUT


def test_parsed_lease_normalizes_invalid_location_quality_to_none():
    """Robust degradation: unknown labels are stored as None (neutral)."""
    lease = ParsedLease(
        address="Hauptstr 1",
        cold_rent_eur=600.0,
        size_sqm=60.0,
        location_quality="extrem_gut",
    )
    assert lease.location_quality is None


def test_parsed_lease_rejects_negative_rent():
    with pytest.raises(ValidationError):
        ParsedLease(
            address="Hauptstr 1",
            cold_rent_eur=-1.0,
            size_sqm=60.0,
        )


def test_parsed_lease_build_year_out_of_range():
    with pytest.raises(ValidationError):
        ParsedLease(
            address="Hauptstr 1",
            cold_rent_eur=600.0,
            size_sqm=60.0,
            build_year=2100,
        )


# ----- RentCheckRequest: bug-fix regression -----------------------------------


def test_rent_check_request_accepts_top_level_postal_code_and_build_year():
    """Regression test for the postal_code/build_year bug.

    Before the fix, RentCheckRequest had no top-level PLZ/Baujahr fields.
    When a caller used Mode 2 (no `lease` object), the engine silently
    lost these values, so fair-rent calculations never applied their
    factors. The fix: request now carries them itself.
    """
    req = RentCheckRequest(
        address="Hauptstr 1",
        cold_rent_eur=600.0,
        size_sqm=60.0,
        postal_code="10115",
        build_year=1985,
        location_quality=LocationQuality.GUT,
    )
    assert req.postal_code == "10115"
    assert req.build_year == 1985
    assert req.location_quality == LocationQuality.GUT


# ----- SourceCitation ---------------------------------------------------------


def test_source_citation_rejects_high_below_low():
    """Validator must reject degenerate spreads."""
    with pytest.raises(ValidationError) as exc_info:
        SourceCitation(
            title="X",
            publisher="Y",
            url="https://example.com",
            year=2024,
            kind="qualifizierter_mietspiegel",
            spread_eur_per_sqm_low=12.0,
            spread_eur_per_sqm_high=8.0,  # < low ⇒ must fail
        )
    # Pydantic 2 wraps in ValidationError; ensure message mentions the validator.
    assert "spread" in str(exc_info.value).lower() or "low" in str(exc_info.value).lower()


def test_source_citation_accepts_valid_spread():
    sc = SourceCitation(
        title="Berliner Mietspiegel 2024",
        publisher="Senatsverwaltung Berlin",
        url="https://example.com/mietspiegel",
        year=2024,
        kind="qualifizierter_mietspiegel",
        spread_eur_per_sqm_low=5.50,
        spread_eur_per_sqm_high=14.20,
    )
    assert sc.spread_eur_per_sqm_high == 14.20


# ----- RentCheckResponse: top-level quellen + comparison.fetched_at -----------


def _make_comparison() -> ComparisonData:
    return ComparisonData(
        city="Berlin",
        city_average_eur_per_sqm=8.45,
        reference_size_sqm=60.0,
        source="Berliner Mietspiegel 2024",
        fetched_at="2026-07-01T00:00:00+00:00",
    )


def _make_citation() -> SourceCitation:
    return SourceCitation(
        title="Berliner Mietspiegel 2024",
        publisher="Senatsverwaltung Berlin",
        url="https://example.com",
        year=2024,
        kind="qualifizierter_mietspiegel",
        spread_eur_per_sqm_low=5.50,
        spread_eur_per_sqm_high=14.20,
    )


def test_rent_check_response_serializes_with_quellen():
    resp = RentCheckResponse(
        requested_rent_eur=900.0,
        fair_rent_eur=507.0,
        delta_percent=77.5,
        verdict=Verdict.WUCHER,
        legal_basis="§5 WiStrG",
        explanation="Wucher.",
        comparison=_make_comparison(),
        quellen=[_make_citation()],
        recommendation="Anwalt",
    )
    data = resp.model_dump()
    assert "quellen" in data
    assert isinstance(data["quellen"], list)
    assert len(data["quellen"]) == 1
    assert data["quellen"][0]["year"] == 2024
    # round-trip via JSON
    j = resp.model_dump_json()
    assert "quellen" in j
    parsed = RentCheckResponse.model_validate_json(j)
    assert parsed.quellen[0].publisher == "Senatsverwaltung Berlin"


def test_rent_check_response_quellen_defaults_empty():
    resp = RentCheckResponse(
        requested_rent_eur=900.0,
        fair_rent_eur=507.0,
        delta_percent=77.5,
        verdict=Verdict.WUCHER,
        legal_basis="§5 WiStrG",
        explanation="Wucher.",
        comparison=_make_comparison(),
        recommendation="Anwalt",
    )
    assert resp.quellen == []
