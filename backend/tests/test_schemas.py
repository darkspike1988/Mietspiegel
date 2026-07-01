"""Tests for app.schemas.contracts — Pydantic validation, JSON round-trip,
invariant checks for public API contracts.

Run:  cd backend && source .venv/bin/activate && pytest tests/test_schemas.py
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.contracts import (
    ComparisonData, LocationQuality, ParsedLease, RentCheckRequest,
    RentCheckResponse, SourceCitation, Verdict, WiderspruchRequest,
)


# ----- 1. ParsedLease ---------------------------------------------

def test_parsed_lease_minimal_valid():
    lease = ParsedLease(
        address="Hauptstr 1, 10115 Berlin",
        cold_rent_eur=600.0,
        size_sqm=60.0,
    )
    assert lease.cold_rent_eur == 600.0
    assert lease.postal_code is None
    assert lease.build_year is None
    assert lease.extras == []  # default_factory


def test_parsed_lease_cold_rent_must_be_positive():
    with pytest.raises(ValidationError):
        ParsedLease(address="x", cold_rent_eur=0.0, size_sqm=60.0)
    with pytest.raises(ValidationError):
        ParsedLease(address="x", cold_rent_eur=-100.0, size_sqm=60.0)


def test_parsed_lease_size_must_be_positive():
    with pytest.raises(ValidationError):
        ParsedLease(address="x", cold_rent_eur=600.0, size_sqm=0.0)


def test_parsed_lease_build_year_bounds():
    """§558 BGB / BewG: Baujahre <1850 sind unplausibel, >2030 in der Zukunft."""
    with pytest.raises(ValidationError):
        ParsedLease(address="x", cold_rent_eur=600.0, size_sqm=60.0, build_year=1700)
    with pytest.raises(ValidationError):
        ParsedLease(address="x", cold_rent_eur=600.0, size_sqm=60.0, build_year=2050)


def test_parsed_lease_location_quality_normalized():
    """Unbekannte Werte werden zu None normalisiert (siehe @field_validator)."""
    lease = ParsedLease(
        address="x", cold_rent_eur=600.0, size_sqm=60.0,
        location_quality="SEHR_GUT",  # nicht im Enum
    )
    assert lease.location_quality is None


def test_parsed_lease_extras_default_is_empty_list():
    """extras ist mutables default_factory — darf nicht [] literal sein."""
    a = ParsedLease(address="x", cold_rent_eur=600.0, size_sqm=60.0)
    b = ParsedLease(address="y", cold_rent_eur=700.0, size_sqm=70.0)
    a.extras.append("Balkon")
    assert b.extras == [], "default_factory leak — Instanzen teilen Liste"


# ----- 2. RentCheckRequest -----------------------------------------

def test_rent_check_request_minimal_direct_mode():
    req = RentCheckRequest(
        address="Hauptstr 1, 10115 Berlin",
        cold_rent_eur=600.0,
        size_sqm=60.0,
        postal_code="10115",
        build_year=1985,
        location_quality=LocationQuality.MITTEL,
    )
    assert req.lease is None
    assert req.postal_code == "10115"


def test_rent_check_request_with_lease_mode():
    lease = ParsedLease(address="x", cold_rent_eur=600.0, size_sqm=60.0)
    req = RentCheckRequest(lease=lease)
    assert req.lease is lease


def test_rent_check_request_postal_code_optional():
    """postal_code darf fehlen (resolve_city macht substring-Fallback)."""
    req = RentCheckRequest(
        address="Wohnung in Hamburg",
        cold_rent_eur=600.0,
        size_sqm=60.0,
    )
    assert req.postal_code is None


# ----- 3. SourceCitation -------------------------------------------

def _citation_ok(**overrides) -> SourceCitation:
    base = {
        "title": "Berliner Mietspiegel 2024",
        "publisher": "Senatsverwaltung Berlin",
        "url": "https://example.com/mietspiegel.pdf",
        "year": 2024,
        "kind": "qualifizierter_mietspiegel",
        "spread_eur_per_sqm_low": 5.50,
        "spread_eur_per_sqm_high": 14.20,
    }
    base.update(overrides)
    return SourceCitation(**base)


def test_source_citation_round_trips_through_json():
    c = _citation_ok()
    payload = c.model_dump()
    restored = SourceCitation(**payload)
    assert restored == c


def test_source_citation_spread_high_must_strictly_exceed_low():
    """Field-Validator wirft ValueError wenn high < low.
    Equal-Werte sind erlaubt (1-Punkt-Spektrum = ausgewiesen gleiche Miete)."""
    with pytest.raises(ValidationError):
        _citation_ok(spread_eur_per_sqm_low=10.0, spread_eur_per_sqm_high=5.0)
    # 10 == 10 ist technisch valide (degenerierte Spanne, aber kein Validator-Trip)
    eq = _citation_ok(spread_eur_per_sqm_low=10.0, spread_eur_per_sqm_high=10.0)
    assert eq.spread_eur_per_sqm_low == 10.0


def test_source_citation_required_fields():
    """Alle 7 Felder sind juristisch zitierrelevant → required."""
    base = _citation_ok()
    for f in ["title", "publisher", "url", "year", "kind",
              "spread_eur_per_sqm_low", "spread_eur_per_sqm_high"]:
        kwargs = {k: v for k, v in base.model_dump().items() if k != f}
        with pytest.raises(ValidationError):
            SourceCitation(**kwargs)


# ----- 4. RentCheckResponse ----------------------------------------

def test_rent_check_response_full_round_trip():
    cite = _citation_ok()
    resp = RentCheckResponse(
        requested_rent_eur=900.0,
        fair_rent_eur=496.86,
        delta_percent=81.14,
        verdict=Verdict.WUCHER,
        legal_basis="§5 WiStrG",
        explanation="Wucher-Verdacht",
        comparison=ComparisonData(
            city="Berlin",
            city_average_eur_per_sqm=8.45,
            reference_size_sqm=60.0,
            source="Berliner Mietspiegel 2024",
            fetched_at="2026-07-01T12:00:00Z",
        ),
        quellen=[cite],
        recommendation="Anwalt kontaktieren",
    )
    payload = resp.model_dump()
    restored = RentCheckResponse(**payload)
    assert restored == resp
    assert restored.verdict == Verdict.WUCHER
    assert restored.quellen[0].url.startswith("http")


def test_rent_check_response_default_quellen_is_empty_list():
    """Default-Factory für quellen — sonst mutable-default-Bug."""
    resp = RentCheckResponse(
        requested_rent_eur=500.0,
        fair_rent_eur=496.86,
        delta_percent=0.63,
        verdict=Verdict.FAIR,
        legal_basis="§558 BGB",
        explanation="ortsüblich",
        comparison=ComparisonData(
            city="Berlin",
            city_average_eur_per_sqm=8.45,
            reference_size_sqm=60.0,
            source="Berliner Mietspiegel 2024",
            fetched_at="2026-07-01T12:00:00Z",
        ),
        recommendation="Kein Handlungsbedarf",
    )
    assert resp.quellen == []


# ----- 5. Verdict enum ---------------------------------------------

def test_verdict_has_four_states():
    """4 Verdict-Kategorien, exakt — wenn eine dazukommt, müssen
    alle Frontends und Briefgenerierung mitgeupdated werden."""
    assert {v.value for v in Verdict} == {"fair", "tolerance", "brake", "wucher"}


# ----- 6. WiderspruchRequest ---------------------------------------

def test_widerspruch_request_minimal_valid():
    cite = _citation_ok()
    resp = RentCheckResponse(
        requested_rent_eur=900.0,
        fair_rent_eur=496.86,
        delta_percent=81.14,
        verdict=Verdict.WUCHER,
        legal_basis="§5 WiStrG",
        explanation="Wucher-Verdacht",
        comparison=ComparisonData(
            city="Berlin",
            city_average_eur_per_sqm=8.45,
            reference_size_sqm=60.0,
            source="Berliner Mietspiegel 2024",
            fetched_at="2026-07-01T12:00:00Z",
        ),
        quellen=[cite],
        recommendation="Anwalt kontaktieren",
    )
    req = WiderspruchRequest(
        check_response=resp,
        tenant_name="Max Mustermann",
        tenant_address="Hauptstr 1, 10115 Berlin",
    )
    assert req.landlord_name is None  # optional
    assert req.tenant_name == "Max Mustermann"


def test_widerspruch_request_tenant_name_min_length():
    cite = _citation_ok()
    resp = RentCheckResponse(
        requested_rent_eur=900.0, fair_rent_eur=496.86, delta_percent=81.14,
        verdict=Verdict.WUCHER, legal_basis="§5 WiStrG", explanation="x",
        comparison=ComparisonData(
            city="Berlin", city_average_eur_per_sqm=8.45, reference_size_sqm=60.0,
            source="x", fetched_at="2026-07-01T12:00:00Z",
        ),
        quellen=[cite], recommendation="x",
    )
    with pytest.raises(ValidationError):
        WiderspruchRequest(
            check_response=resp, tenant_name="M", tenant_address="Hauptstr 1",
        )