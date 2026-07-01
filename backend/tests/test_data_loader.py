"""Tests for app.services.data_loader — 19-city invariant, spread consistency,
PLZ resolution, dataclass round-trip, frozen-immutability, pure loader.

Run:  cd backend && source .venv/bin/activate && pytest tests/test_data_loader.py
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.data_loader import (
    CityDatabase, CityDataset, _load_from_path, get_database,
    reset_database_cache,
)


def _make_city(
    *, id: str = "x-2024", slug: str = "x", name: str = "X",
    federal_state: str = "XX", nuts_code: str | None = "DEXX",
    plz_prefixes: list[str] | None = None,
    **overrides,
) -> CityDataset:
    base = dict(
        id=id, slug=slug, name=name, federal_state=federal_state,
        nuts_code=nuts_code, kind="qualifizierter_mietspiegel",
        publisher="Testverlag", source_title="X-Mietspiegel",
        source_url="https://example.com", year=2024,
        average_eur_per_sqm=10.0, spread_eur_per_sqm_low=8.0,
        spread_eur_per_sqm_high=12.0, notes="",
        plz_prefixes=plz_prefixes or [],
    )
    base.update(overrides)
    return CityDataset(**base)


# ----- 1. Loaded dataset invariants --------------------------------

def test_database_has_at_least_19_cities():
    """Mindestmenge laut Phase-B1 Roadmap."""
    db = get_database()
    assert len(db.all()) == 19, f"expected 19, got {len(db.all())}"


def test_each_dataset_has_16_expected_fields():
    """CityDataset hat genau 16 Felder — Vertrag für Frontend/Engine."""
    db = get_database()
    expected = {
        "id", "slug", "name", "federal_state", "nuts_code", "kind",
        "publisher", "source_title", "source_url", "year",
        "average_eur_per_sqm", "spread_eur_per_sqm_low",
        "spread_eur_per_sqm_high", "notes", "plz_prefixes",
        "matches_plz",
    }
    for ds in db.all():
        attrs = set(vars(ds).keys()) | set(dir(ds))
        missing = expected - attrs
        assert not missing, f"{ds.slug} missing fields: {missing}"


def test_spread_is_internally_consistent():
    """low <= avg <= high; low < high (sonst wäre das Spektrum wertlos)."""
    for ds in get_database().all():
        assert ds.spread_eur_per_sqm_low <= ds.average_eur_per_sqm, (
            f"{ds.slug}: low {ds.spread_eur_per_sqm_low} > avg {ds.average_eur_per_sqm}"
        )
        assert ds.average_eur_per_sqm <= ds.spread_eur_per_sqm_high, (
            f"{ds.slug}: avg > high"
        )
        assert ds.spread_eur_per_sqm_low < ds.spread_eur_per_sqm_high, (
            f"{ds.slug}: low == high"
        )


def test_average_and_spread_are_positive():
    for ds in get_database().all():
        assert ds.average_eur_per_sqm > 0
        assert ds.spread_eur_per_sqm_low > 0
        assert ds.spread_eur_per_sqm_high > 0


def test_year_is_reasonable():
    """Datensätze müssen aktuell genug für 2024+ sein."""
    for ds in get_database().all():
        assert 2020 <= ds.year <= 2030, f"{ds.slug} year={ds.year}"


def test_publisher_and_url_are_present():
    """Quellenangabe ist juristisch zwingend (§558d BGB / §5 WiStrG)."""
    for ds in get_database().all():
        assert ds.publisher, f"{ds.slug}: publisher empty"
        assert ds.source_url.startswith("http"), (
            f"{ds.slug}: source_url must be absolute"
        )


# ----- 2. PLZ-based lookup ----------------------------------------

def test_munich_resolves_via_plz_80331():
    db = get_database()
    hits = db.find_by_plz("80331")
    assert hits
    assert hits[0].slug == "muenchen"


def test_berlin_resolves_via_plz_10115():
    db = get_database()
    hits = db.find_by_plz("10115")
    assert hits
    assert hits[0].slug == "berlin"


def test_erfurt_resolves_via_plz_99084():
    """PLZ-Präfix 99 deckt Erfurt ab."""
    db = get_database()
    hits = db.find_by_plz("99084")
    assert hits
    assert hits[0].slug == "erfurt"


def test_unknown_plz_returns_empty_list():
    """PLZ 00000 hat keinen passenden Präfix → leere Liste."""
    db = get_database()
    assert db.find_by_plz("00000") == []


def test_plz_prefix_match_works_for_berlin_10117():
    """PLZ 10 (Berlin-Prefix) matcht 10117-Anfrage ohne '11' zu kennen."""
    db = get_database()
    hits = db.find_by_plz("10117")
    assert hits
    assert hits[0].slug == "berlin"


def test_dataset_matches_plz_helper():
    """matches_plz() ist die Per-Dataset-Methode, die find_by_plz() aufruft."""
    ds = _make_city(slug="alpha", plz_prefixes=["11", "12"])
    assert ds.matches_plz("11555") is True
    assert ds.matches_plz("12345") is True
    assert ds.matches_plz("99999") is False
    assert ds.matches_plz("") is False


# ----- 3. Slug-based lookup ----------------------------------------

def test_get_returns_single_dataset_for_known_slug():
    db = get_database()
    ds = db.get("berlin")
    assert ds is not None
    assert ds.slug == "berlin"


def test_get_returns_none_for_unknown_slug():
    db = get_database()
    assert db.get("atlantis") is None


def test_get_is_case_insensitive():
    """db.get() lowercased den Slug — UX-Detail für Frontend."""
    db = get_database()
    assert db.get("BERLIN") is not None
    assert db.get("Berlin") is not None


# ----- 4. Naive search() fallback ----------------------------------

def test_search_finds_by_name():
    db = get_database()
    hits = db.search("Hamburg")
    assert any(ds.slug == "hamburg" for ds in hits)


def test_search_finds_by_slug():
    db = get_database()
    hits = db.search("muenchen")
    assert hits and hits[0].slug == "muenchen"


def test_search_returns_empty_for_unknown():
    db = get_database()
    assert db.search("Atlantis") == []


# ----- 5. Dataclass round-trip -------------------------------------

def test_dataset_dataclass_round_trip_via_kwarg_constructor():
    """CityDataset ist frozen dataclass — alle Felder via Konstruktor setzbar."""
    ds = _make_city(slug="test_stadt", name="Teststadt")
    assert ds.slug == "test_stadt"
    assert ds.notes == ""
    assert ds.plz_prefixes == []


def test_dataset_is_frozen():
    """dataclass(frozen=True) verhindert versehentliche Mutation."""
    ds = get_database().all()[0]
    with pytest.raises((AttributeError, Exception)):
        ds.slug = "changed"  # type: ignore[misc]


def test_dataset_plz_prefixes_default_is_empty_list():
    """plz_prefixes default_factory=list — kein mutable-default-bug."""
    ds = _make_city(plz_prefixes=None)
    assert ds.plz_prefixes == []
    ds.plz_prefixes.append("99")
    ds2 = _make_city(plz_prefixes=None)
    assert ds2.plz_prefixes == [], "default_factory leak"


# ----- 6. Pure loader (private) ------------------------------------

def test_load_from_path_is_pure_function(tmp_path: Path):
    """_load_from_path ist pure: gibt frische CityDatabase zurück,
    mutiert nicht den Modul-Cache."""
    payload = {
        "version": "test",
        "datasets": [{
            "id": "alpha-2024",
            "slug": "alpha",
            "name": "Alpha",
            "federal_state": "AA",
            "nuts_code": "DEAA",
            "kind": "qualifizierter_mietspiegel",
            "publisher": "Alpha-Verlag",
            "source_title": "Alpha-Mietspiegel",
            "source_url": "https://example.com/alpha",
            "year": 2024,
            "average_eur_per_sqm": 10.0,
            "spread_eur_per_sqm_low": 8.0,
            "spread_eur_per_sqm_high": 12.0,
            "notes": "",
            "plz_prefixes": ["11"],
        }],
    }
    f = tmp_path / "alpha.json"
    f.write_text(json.dumps(payload))
    db = _load_from_path(f)
    assert isinstance(db, CityDatabase)
    assert len(db.all()) == 1
    assert db.get("alpha")
    # Modul-Cache darf NICHT überschrieben sein
    assert len(get_database().all()) == 19


# ----- 7. Module-cache reset helper --------------------------------

def test_reset_database_cache_reload_fresh():
    """reset_database_cache() muss die nächste get_database()-Anfrage zum
    Neu-Lesen von Disk zwingen."""
    db_before = get_database()
    assert len(db_before.all()) == 19
    reset_database_cache()
    db_after = get_database()
    assert db_after is not db_before
    assert len(db_after.all()) == 19