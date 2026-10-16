"""Tests für den Wohnungssuche-Agenten (RSS-Parsing, Dedup, Badge-Check).

Alle Tests laufen offline — kein echter Portal-Call nötig.
"""
from __future__ import annotations

import pytest

from app.schemas.search_agent import (
    Listing,
    Provider,
    RentBadge,
    SearchProfile,
)
from app.services import search_agent

pytestmark = pytest.mark.asyncio

RSS_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Test</title>
<item>
  <title>Schöne 2-Zimmer-Wohnung in Berlin, 60 m², 850 € Kaltmiete, 10117 Berlin</title>
  <link>https://example.com/wohnung/1</link>
</item>
<item>
  <title>3 Zimmer Wohnung München 80 m² 1900 €</title>
  <link>https://example.com/wohnung/2</link>
</item>
</channel></rss>"""


class TestParsing:
    def test_rss_items_parsed(self) -> None:
        listings = search_agent._parse_rss(RSS_SAMPLE, "immoscout24")
        assert len(listings) == 2
        first = listings[0]
        assert first.city == "Berlin"
        assert first.size_sqm == 60
        assert first.cold_rent_eur == 850
        assert first.rooms == 2
        assert first.postal_code == "10117"

    def test_munich_detected(self) -> None:
        listings = search_agent._parse_rss(RSS_SAMPLE, "immoscout24")
        assert listings[1].city == "München"

    def test_dedup_key_stable(self) -> None:
        li1 = Listing(
            id="a", title="A", city="Berlin",
            cold_rent_eur=850, size_sqm=60, rooms=2, source="x",
        )
        li2 = Listing(
            id="b", title="Andere Anzeige", city="Berlin",
            cold_rent_eur=850, size_sqm=60, rooms=2, source="y",
        )
        # gleiche Kern-Daten -> gleicher Key (Portal-übergreifender Dedup)
        assert li1.dedup_key() == li2.dedup_key()
        li3 = Listing(
            id="c", title="C", city="Berlin",
            cold_rent_eur=900, size_sqm=60, rooms=2, source="x",
        )
        assert li1.dedup_key() != li3.dedup_key()

    def test_price_patterns(self) -> None:
        assert search_agent._parse_price("Kaltmiete 850 €") == 850
        assert search_agent._parse_price("1900 EUR") == 1900
        assert search_agent._parse_price("kein Preis") is None
        assert search_agent._parse_sqm("60 m²") == 60
        assert search_agent._parse_rooms("2 Zi") == 2


class TestRentBadge:
    def test_fair_listing_gets_fair_badge(self) -> None:
        # Berlin Ø 8.45 €/m² -> fair für 60 m² ca. 507 €; 560 € = ~+10.4%
        listing = Listing(
            id="x", title="Wohnung 10117 Berlin 60 m² 560 € 2 Zi",
            city="Berlin", postal_code="10117",
            cold_rent_eur=560, size_sqm=60, rooms=2, source="test",
        )
        checked = search_agent._check_listing(listing)
        assert checked.rent_badge in (
            RentBadge.FAIR, RentBadge.TOLERANCE,
        )

    def test_overpriced_gets_brake_or_wucher(self) -> None:
        listing = Listing(
            id="y", title="Wohnung 10117 Berlin 60 m² 1100 € 2 Zi",
            city="Berlin", postal_code="10117",
            cold_rent_eur=1100, size_sqm=60, rooms=2, source="test",
        )
        checked = search_agent._check_listing(listing)
        assert checked.rent_badge in (RentBadge.BRAKE, RentBadge.WUCHER)
        assert checked.delta_percent is not None and checked.delta_percent > 50

    def test_uncheckable_gets_unknown(self) -> None:
        listing = Listing(id="z", title="Kein Preis", source="test")
        checked = search_agent._check_listing(listing)
        assert checked.rent_badge == RentBadge.UNKNOWN


class TestDedupFlow:
    async def test_refresh_dedups(self) -> None:
        import app.services.search_agent as sa

        profile = SearchProfile(
            name="berlin-test",
            url="https://example.com/feed.rss",
            provider=Provider.RSS,
        )
        # fetch_profile monkeypatchen (offline)
        async def fake_fetch(p):
            return sa._parse_rss(RSS_SAMPLE, "immoscout24")
        sa.fetch_profile = fake_fetch
        sa._seen.pop("berlin-test", None)

        fresh1, total1 = await sa.refresh_profile(profile)
        assert len(fresh1) == 2
        assert total1 == 2

        fresh2, total2 = await sa.refresh_profile(profile)
        assert fresh2 == []
        assert total2 == 2

    async def test_new_listing_detected(self) -> None:
        import app.services.search_agent as sa

        profile = SearchProfile(
            name="berlin-test-2",
            url="https://example.com/feed.rss",
        )

        async def fake_fetch(p):
            return sa._parse_rss(
                RSS_SAMPLE.replace("</channel>", """<item>
                <title>Neu! 4-Zimmer Berlin 100 m² 1200 € 10115 Berlin</title>
                <link>https://example.com/wohnung/3</link>
                </item></channel>"""),
                "immoscout24",
            )
        sa.fetch_profile = fake_fetch
        sa._seen.pop("berlin-test-2", None)

        fresh1, total1 = await sa.refresh_profile(profile)
        assert len(fresh1) == 3
        fresh2, total2 = await sa.refresh_profile(profile)
        assert fresh2 == [] and total2 == 3
