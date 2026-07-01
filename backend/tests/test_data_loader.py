"""Tests for app.services.data_loader — 19-city invariant, spread consistency,
PLZ resolution, dataclass round-trip, frost-immutability, private loader purity.

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


# ----- 1. Loaded dataset invariants --------------------------------

def test_database_contains_19_cities():
    """19 Städte sind die aktuelle Mindestmenge (s. Phase-B1 Roadmap)."""
    db = get_database()
    assert len(db) == 19, f"expected 19 cities, got {len(db)}"


def test_each_dataset_has_required_fields():
    db = get_database()
    required = {
        "slug", "name", "federal_state", "average_eur_per_sqm",
        "spread_eur_per_sqm_low", "spread_eur_per_sqm_high",
        "year", "source_title", "publisher", "source_url", "kind",
    }
    for ds in db.all():
        missing = required - ds.to_dict().keys()
        assert not missing, f"{ds.slug} missing {missing}"


def test_spread_is_internally_consistent():
    """low <= avg <= high, low < high (sonst wäre das Spektrum wertlos)."""
    db = get_database()
    for ds in db.all():
        assert ds.spread_eur_per_sqm_low <= ds.average_eur_per_sqm, (
            f"{ds.slug}: low {ds.spread_eur_per_sqm_low} > avg {ds.average_eur_per_sqm}"
        )
        assert ds.average_eur_per_sqm <= ds.spread_eur_per_sqm_high, (
            f"{ds.slug}: avg {ds.average_eur_per_sqm} > high {ds.spread_eur_per_sqm_high}"
        )
        assert ds.spread_eur_per_sqm_low < ds.spread_eur_per_sqm_high, (
            f"{ds.slug}: low == high ({ds.spread_eur_per_sqm_low})"
        )


def test_average_and_spread_are_positive():
    db = get_database()
    for ds in db.all():
        assert ds.average_eur_per_sqm > 0
        assert ds.spread_eur_per_sqm_low > 0
        assert ds.spread_eur_per_sqm_high > 0


def test_year_is_reasonable():
    """Datensätze müssen aktuell genug für 2024+ sein."""
    db = get_database()
    for ds in db.all():
        assert 2020 <= ds.year <= 2030, f"{ds.slug} year={ds.year} out of range"


# ----- 2. PLZ-based lookup ----------------------------------------

def test_munich_resolves_via_plz_80331():
    db = get_database()
    ds = db.find_by_plz("80331")
    assert ds is not None
    assert ds.slug == "muenchen"  # Slug ist ASCII-umlautfrei (URL-safe)


def test_berlin_resolves_via_plz_10115():
    db = get_database()
    ds = db.find_by_plz("10115")
    assert ds is not None
    assert ds.slug == "berlin"


def test_erfurt_resolves_via_plz_99084():
    """PLZ-Präfix 99 deckt Erfurt ab — Test verifiziert die Fallback-Logik."""
    db = get_database()
    ds = db.find_by_plz("99084")
    assert ds is not None
    assert ds.slug == "erfurt"


def test_unknown_plz_returns_none():
    """PLZ 00000 hat kein reales Präfix — sauberes None-Signal."""
    db = get_database()
    assert db.find_by_plz("00000") is None


def test_plz_prefix_match_works_for_2digit_prefix():
    """PLZ 10 (Berlin) matcht 10115-Anfrage ohne '11' Präfix zu kennen."""
    db = get_database()
    # Echte Berliner PLZ → muss Berlin matchen
    ds = db.find_by_plz("10117")
    assert ds is not None and ds.slug == "berlin"


# ----- 3. Slug-based lookup ----------------------------------------

def test_slug_lookup_is_case_insensitive():
    db = get_database()
    a = db.find_by_slug("berlin")
    b = db.find_by_slug("BERLIN")
    assert a is b
    assert a is not None


def test_unknown_slug_returns_none():
    db = get_database()
    assert db.find_by_slug("nope") is None


# ----- 4. Dataclass round-trip -------------------------------------

def test_dataset_dataclass_round_trip():
    """CityDataset ist frozen dataclass — alle Felder müssen via Konstruktor
    gesetzt und via to_dict()/from_dict() round-trippable sein."""
    ds = CityDataset(
        slug="test_stadt",
        name="Teststadt",
        federal_state="XX",
        average_eur_per_sqm=9.99,
        spread_eur_per_sqm_low=7.50,
        spread_eur_per_sqm_high=12.40,
        year=2024,
        source_title="Test-Mietspiegel",
        publisher="Testverlag",
        source_url="https://example.com",
        kind="einfacher_mietspiegel",
        plz_prefixes=("99",),
    )
    payload = ds.to_dict()
    restored = CityDataset.from_dict(payload)
    assert restored == ds


def test_dataset_is_frozen():
    """dataclass(frozen=True) verhindert versehentliche Mutation."""
    ds = get_database().all()[0]
    with pytest.raises((AttributeError, Exception)):
        ds.slug = "changed"  # type: ignore[misc]


# ----- 5. Pure loader (private) ------------------------------------

def test_load_from_path_is_pure_function(tmp_path: Path):
    """_load_from_path ist ein reiner Loader: gibt frische CityDatabase zurück,
    mutiert nicht den Modul-Cache. get_database() lädt unabhängig von
    DEFAULT_DATA_PATH — gutes Design für Test-Isolation."""
    payload = {
        "version": "test",
        "datasets": [
            {
                "slug": "alpha",
                "name": "Alpha",
                "federal_state": "AA",
                "average_eur_per_sqm": 10.0,
                "spread_eur_per_sqm_low": 8.0,
                "spread_eur_per_sqm_high": 12.0,
                "year": 2024,
                "source_title": "Alpha-Mietspiegel",
                "publisher": "Alpha-Verlag",
                "source_url": "https://example.com/alpha",
                "kind": "qualifizierter_mietspiegel",
                "plz_prefixes": ["11"],
            }
        ],
    }
    f = tmp_path / "alpha.json"
    f.write_text(json.dumps(payload))
    db = _load_from_path(f)
    assert isinstance(db, CityDatabase)
    assert len(db) == 1
    assert db.find_by_slug("alpha") is not None
    # Modul-Cache darf NICHT überschrieben sein
    real_db = get_database()
    assert len(real_db) == 19


# ----- 6. Module-cache reset helper --------------------------------

def test_reset_database_cache_reload_fresh():
    """reset_database_cache() muss die nächste get_database()-Anfrage zum
    Neu-Lesen von Disk zwingen (relevant für Tests, die alternate Datensätze
    injizieren wollen)."""
    db_before = get_database()
    assert len(db_before) == 19
    reset_database_cache()
    db_after = get_database()
    assert db_after is not db_before  # frische Instanz
    assert len(db_after) == 19
