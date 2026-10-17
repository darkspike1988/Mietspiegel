"""Tests für die einfache Ort-Suche."""
from __future__ import annotations

from app.services import location_search as ls


class TestResolveLocation:
    def test_city_only(self) -> None:
        r = ls.resolve_location("Berlin")
        assert r["city"] == "Berlin" and r["city_slug"] == "berlin"

    def test_city_with_street(self) -> None:
        r = ls.resolve_location("Berlin, Friedrichstraße 100")
        assert r["city"] == "Berlin"
        assert "Friedrichstraße" in r["street"]

    def test_district_berlin(self) -> None:
        r = ls.resolve_location("Kreuzberg")
        assert r["city"] == "Berlin"

    def test_district_more(self) -> None:
        assert ls.resolve_location("Neukölln")["city"] == "Berlin"
        assert ls.resolve_location("Schwabing")["city"] == "München"
        assert ls.resolve_location("Ehrenfeld")["city"] == "Köln"

    def test_plz(self) -> None:
        r = ls.resolve_location("10117 Berlin, Torstraße")
        assert r["plz"] == "10117"
        assert "Torstraße" in r["street"]

    def test_unknown(self) -> None:
        r = ls.resolve_location("Kleinkleckersdorf")
        assert r["city"] is None


class TestBuildUrls:
    def test_berlin_all_portals(self) -> None:
        urls = ls.build_portal_urls("Berlin", "berlin", None, None, 1000, 2)
        portals = {u.portal for u in urls}
        assert portals == {"ImmoScout24", "Immowelt", "Kleinanzeigen", "WG-Gesucht"}
        is24 = next(u for u in urls if u.portal == "ImmoScout24")
        assert "price=-1000" in is24.url
        assert "numberofrooms=2" in is24.url

    def test_street_filter(self) -> None:
        urls = ls.build_portal_urls("Berlin", "berlin", None, "Torstraße", None, None)
        is24 = next(u for u in urls if u.portal == "ImmoScout24")
        assert "fulltext=" in is24.url

    def test_no_city_no_urls(self) -> None:
        assert ls.build_portal_urls(None, None, None, None, None, None) == []


class TestEndToEnd:
    async def test_search_berlin(self) -> None:
        r = await ls.search_by_location(ls.LocationQuery(query="Kreuzberg", max_rent=900))
        assert r.city == "Berlin"
        assert len(r.urls) == 4
        assert "erkannt" in r.hint

    async def test_unknown_location(self) -> None:
        r = await ls.search_by_location(ls.LocationQuery(query="xyz"))
        assert r.urls == []
        assert "nicht erkannt" in r.hint
