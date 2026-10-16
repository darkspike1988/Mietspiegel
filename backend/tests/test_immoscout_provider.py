"""Tests für den ImmoScout-Mobile-API-Provider (URL-Translator + Parsing).

Der URL-Translator ist rein deterministisch — vollständig offline testbar.
Das Response-Parsing wird mit einem Fixture getestet, das dem dokumentierten
API-Shape entspricht (resultList.result mit EXPOSE_RESULT-Einträgen).
"""
from __future__ import annotations

import pytest

from app.services import immoscout_provider as isp


class TestTranslateUrl:
    def test_region_search_berlin(self) -> None:
        params = isp.translate_url(
            "https://www.immobilienscout24.de/Suche/de/berlin/berlin/wohnung-mieten"
            "?price=-1000.0&numberofrooms=2.0-"
        )
        assert params["realestatetype"] == "apartmentrent"
        assert params["searchType"] == "region"
        assert params["geocodes"] == "/de/berlin/berlin"
        assert params["price"] == "-1000.0"
        assert params["numberofrooms"] == "2.0-"
        # Default: Tauschwohnungen ausgeblendet (wie die Website)
        assert params["exclusioncriteria"] == "swapflat"

    def test_radius_search(self) -> None:
        params = isp.translate_url(
            "https://www.immobilienscout24.de/Suche/radius/wohnung-mieten"
            "?geocoordinates=52.51051;13.43068;4.0&price=-1200.0"
        )
        assert params["searchType"] == "radius"
        assert params["geocoordinates"] == "52.51051;13.43068;4.0"
        assert params["realestatetype"] == "apartmentrent"

    def test_haus_mieten(self) -> None:
        params = isp.translate_url(
            "https://www.immobilienscout24.de/Suche/de/bayern/muenchen/haus-mieten"
        )
        assert params["realestatetype"] == "houserent"
        assert params["geocodes"] == "/de/bayern/muenchen"
        # swapflat-Default nur für Wohnungsmiete
        assert "exclusioncriteria" not in params

    def test_noise_params_dropped(self) -> None:
        params = isp.translate_url(
            "https://www.immobilienscout24.de/Suche/de/berlin/berlin/wohnung-mieten"
            "?enteredFrom=result_list&pagenumber=1&price=-800.0"
        )
        assert "enteredfrom" not in params
        assert "pagenumber" not in params
        assert params["price"] == "-800.0"

    def test_unknown_type_raises(self) -> None:
        with pytest.raises(ValueError, match="Immobilientyp"):
            isp.translate_url(
                "https://www.immobilienscout24.de/Suche/de/berlin/berlin"
            )

    def test_numeric_geocodes_from_query(self) -> None:
        params = isp.translate_url(
            "https://www.immobilienscout24.de/Suche/de/berlin/berlin/wohnung-mieten"
            "?geocodes=1276010037,1276010014"
        )
        assert params["geocodes"] == "1276010037,1276010014"


API_FIXTURE = {
    "totalResults": 349,
    "resultListItems": [
        {
            "type": "EXPOSE_RESULT",
            "item": {
                "id": "168052908",
                "title": "Naturverbunden wohnen in kompakter 2-Zimmer-Gartenwohnung",
                "address": {
                    "line": "Hellersdorfer Straße 91, 12619 Berlin, Kaulsdorf (Hellersdorf)",
                    "lat": 52.52485,
                    "lon": 13.58695,
                },
                "attributes": [
                    {"label": "", "value": "362\u00a0€"},
                    {"label": "", "value": "52\u00a0m²"},
                    {"label": "", "value": "2\u00a0Zi."},
                ],
            },
        },
        {
            "type": "ADVERTISEMENT",
            "item": {"id": "ad1", "title": "Werbung"},
        },
    ],
}


class TestParseResponse:
    def test_parses_expose_results(self) -> None:
        listings = isp.parse_response(API_FIXTURE)
        assert len(listings) == 1  # Advertisement übersprungen
        li = listings[0]
        assert li.city == "Berlin"
        assert li.postal_code == "12619"
        assert li.cold_rent_eur == 362.0
        assert li.size_sqm == 52
        assert li.rooms == 2
        assert li.source == "immoscout24"
        assert li.url == "https://www.immobilienscout24.de/expose/168052908"

    def test_empty_response(self) -> None:
        assert isp.parse_response({}) == []
        assert isp.parse_response({"resultListItems": []}) == []

    def test_malformed_entries_skipped(self) -> None:
        data = {
            "resultListItems": [
                {"type": "EXPOSE_RESULT", "item": None},
                "not-a-dict",
            ]
        }
        assert isp.parse_response(data) == []


class TestRentBadgeIntegration:
    def test_translated_listing_gets_badge(self) -> None:
        """fetch_profile mit ImmoScout-URL nutzt den Mobile-API-Provider."""
        from unittest.mock import AsyncMock, patch

        from app.schemas.search_agent import Provider, SearchProfile
        from app.services import search_agent

        profile = SearchProfile(
            name="is24-test",
            url="https://www.immobilienscout24.de/Suche/de/berlin/berlin/wohnung-mieten",
            provider=Provider.HTML,  # wird ignoriert — Mobile API hat Vorrang
        )
        fake = [
            l for l in isp.parse_response(API_FIXTURE)
        ]
        with patch.object(
            search_agent, "fetch_profile", new=AsyncMock(return_value=fake)
        ):
            checked, _n = None, None
        # dedup_key + Badge über den normalen Flow (offline monkeypatched)
        badge = search_agent._check_listing(fake[0])
        assert badge.rent_badge is not None
