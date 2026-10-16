"""Tests für die SQLite-Persistenz."""
from __future__ import annotations

import pytest

from app.services import storage


@pytest.fixture(autouse=True)
def _clean_db():
    storage.reset_db_for_tests()
    yield
    storage.reset_db_for_tests()


class TestProfiles:
    def test_save_and_get(self) -> None:
        pid = storage.save_profile("Berlin 2Zi", "https://example.com/s", "rss", "Berlin")
        p = storage.get_profile(pid)
        assert p is not None
        assert p["name"] == "Berlin 2Zi"
        assert p["url"] == "https://example.com/s"
        assert p["city"] == "Berlin"
        assert p["active"] is True

    def test_list_profiles(self) -> None:
        storage.save_profile("A", "https://a.de")
        storage.save_profile("B", "https://b.de")
        names = [p["name"] for p in storage.list_profiles()]
        assert "A" in names and "B" in names

    def test_get_missing(self) -> None:
        assert storage.get_profile(99999) is None


class TestSeenListings:
    def test_dedup_roundtrip(self) -> None:
        pid = storage.save_profile("P", "https://x.de")
        assert not storage.is_seen(pid, "abc")
        storage.save_seen_listing(pid, "abc", {"title": "Wohnung", "cold_rent_eur": 500})
        assert storage.is_seen(pid, "abc")
        # Zweiter Versuch mit gleichem Key: kein Duplikat
        storage.save_seen_listing(pid, "abc", {"title": "Wohnung"})
        assert storage.count_listings(pid) == 1
        listings = storage.list_listings(pid)
        assert listings[0]["title"] == "Wohnung"
        assert listings[0]["cold_rent_eur"] == 500
        assert "first_seen_at" in listings[0]


class TestObservations:
    def test_roundtrip(self) -> None:
        storage.save_observation("verivox", "strom", "10117", "TarifA", 27.5, 100, 50)
        storage.save_observation("check24", "strom", "10117", "TarifB", 30.0)
        obs = storage.load_observations("strom", "10117")
        assert len(obs) == 2
        by_portal = {o["portal"]: o for o in obs}
        assert by_portal["verivox"]["ct_per_kwh"] == 27.5
        assert by_portal["verivox"]["base_fee_eur_per_year"] == 100
        assert by_portal["check24"]["tariff_name"] == "TarifB"

    def test_plz_isolation(self) -> None:
        storage.save_observation("verivox", "strom", "10117", "T", 27.5)
        assert len(storage.load_observations("strom", "80331")) == 0

    def test_none_price(self) -> None:
        storage.save_observation("idealo", "dsl", "10117", "Tarif ohne Preis", None)
        obs = storage.load_observations("dsl", "10117")
        assert obs[0]["ct_per_kwh"] is None
