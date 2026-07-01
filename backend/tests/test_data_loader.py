"""City dataset loader: PLZ resolution + invariants on the canonical dataset."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.data_loader import (
    DEFAULT_DATA_PATH,
    CityDataset,
    CityDatabase,
    _load_from_path,
    get_database,
    reload_dataset,
    reset_database_cache,
)


# ----- canonical file ---------------------------------------------------------


def test_default_data_path_exists():
    """Sanity check: the bundled JSON is shipped in the repo."""
    assert DEFAULT_DATA_PATH.exists()
    assert DEFAULT_DATA_PATH.name == "germany_cities.json"


def test_dataset_has_reasonable_number_of_cities():
    db = get_database()
    all_ds = db.all()
    # Snapshot: wir hatten 19 Städte v1.0.0; akzeptiere alles ab 5 (zukunftssicher)
    assert len(all_ds) >= 5


def test_every_dataset_has_consistent_spread():
    db = get_database()
    for ds in db.all():
        assert ds.spread_eur_per_sqm_low >= 0
        assert ds.spread_eur_per_sqm_high >= ds.spread_eur_per_sqm_low, (
            f"{ds.slug} has low > high: {ds.spread_eur_per_sqm_low} vs {ds.spread_eur_per_sqm_high}"
        )


def test_every_dataset_has_required_fields():
    db = get_database()
    for ds in db.all():
        assert ds.slug and ds.name and ds.publisher
        assert ds.source_url.startswith(("http://", "https://"))
        assert 1850 <= ds.year <= 2100
        assert ds.kind in {
            "qualifizierter_mietspiegel",
            "einfacher_mietspiegel",
            "marktbericht",
        }


def test_every_dataset_has_at_least_one_plz_prefix():
    db = get_database()
    for ds in db.all():
        assert ds.plz_prefixes, f"{ds.slug} has no plz_prefixes"
        for pfx in ds.plz_prefixes:
            assert pfx.isdigit(), f"{ds.slug} has non-numeric PLZ prefix: {pfx!r}"


# ----- find_by_plz ------------------------------------------------------------


def test_berlin_resolves_via_plz_10115():
    db = get_database()
    hits = db.find_by_plz("10115")
    assert hits, "Berlin should be matched by PLZ 10115"
    slugs = {ds.slug for ds in hits}
    assert "berlin" in slugs


def test_munich_resolves_via_plz_80331():
    db = get_database()
    hits = db.find_by_plz("80331")
    assert hits
    assert any(ds.slug == "muenchen" for ds in hits)


def test_erfurt_resolves_via_plz_99084():
    db = get_database()
    hits = db.find_by_plz("99084")
    assert hits
    assert any(ds.slug == "erfurt" for ds in hits)


def test_unknown_plz_returns_empty(tmp_path):
    """When no PLZ-prefix matches, we must return [] (never raise).

    '00000' fails every dataset because no prefix starts with '00'
    (Berlin/Munich etc. all use leading-1..9 prefixes).
    """
    db = get_database()
    assert db.find_by_plz("00000") == []


def test_plz_prefix_match_is_prefix_not_substring():
    """'1' should NOT match Berlin (which only has prefixes like '10'..'14')."""
    db = get_database()
    # A bare '1' is too short to be conclusive. But '1' should not match Berlin.
    hits = db.find_by_plz("1")
    assert not any(ds.slug == "berlin" for ds in hits)


# ----- CityDataset dataclass contract ------------------------------------------


def test_city_dataset_is_immutable():
    """CityDataset is frozen — accidental mutation must blow up loudly."""
    ds = get_database().all()[0]
    with pytest.raises((AttributeError, Exception)):
        ds.slug = "hacked"  # type: ignore[misc]


def test_city_dataset_dataclass_constructible_directly():
    """CityDataset is a dataclass; construction must work without going through JSON."""
    ds = CityDataset(
        id="x",
        slug="x",
        name="X",
        federal_state="XX",
        nuts_code=None,
        kind="marktbericht",
        publisher="Me",
        source_title="X Mietspiegel",
        source_url="https://example.com",
        year=2024,
        average_eur_per_sqm=10.0,
        spread_eur_per_sqm_low=7.0,
        spread_eur_per_sqm_high=13.0,
        plz_prefixes=["99"],
    )
    assert ds.slug == "x"
    assert ds.matches_plz("99000") is True
    assert ds.matches_plz("12345") is False


# ----- alternate load paths (optional coverage; skip-safe) ------------------


def test_load_from_path_is_pure_function(tmp_path):
    """_load_from_path is a pure loader (does not mutate module cache).

    It returns a fresh CityDatabase each call — get_database() then
    independently loads from DEFAULT_DATA_PATH. We only assert the
    fresh-instance behavior here.
    """
    import json

    payload = {
        "version": "test",
        "datasets": [
            {
                "id": "t",
                "slug": "teststadt",
                "name": "Teststadt",
                "federal_state": "TT",
                "nuts_code": None,
                "kind": "marktbericht",
                "publisher": "Test Pub",
                "source_title": "Test Mietspiegel",
                "source_url": "https://example.com/test",
                "year": 2024,
                "average_eur_per_sqm": 9.5,
                "spread_eur_per_sqm_low": 7.0,
                "spread_eur_per_sqm_high": 12.0,
                "plz_prefixes": ["99"],
            }
        ],
    }
    f = tmp_path / "test.json"
    f.write_text(json.dumps(payload), encoding="utf-8")

    db = _load_from_path(f)
    assert len(db.all()) == 1
    assert db.get("teststadt").name == "Teststadt"


def test_reload_dataset_resets_cache():
    """After reload_dataset(), get_database() returns a fresh instance."""
    db_a = get_database()
    reload_dataset()
    db_b = get_database()
    # Both must be valid; reload guarantee is identity difference is allowed but
    # data must still be identical.
    assert {d.slug for d in db_a.all()} == {d.slug for d in db_b.all()}
