"""Tests für den Immowelt-Provider (offline mit Live-Fixture-Shape)."""
from __future__ import annotations

from app.services import immowelt_provider as iwp

HTML_FIXTURE = """
<a href="https://www.immowelt.de/expose/d7234819-4f97-4f62-ae2c-bb2f4a2fc475"
   title="Wohnung zur Miete - Berlin - 1.299 € - 2 Zimmer, 48,2 m², 1. Geschoss, frei ab 01.12.2026">
   <img alt="Wohnung zur Miete 1.299 € 2 Zimmer 48,2 m² 1. Geschoss frei ab 01.12.2026 Stallschreiberstraße 23 Kreuzberg Berlin 10179" />
</a>
<a href="https://www.immowelt.de/expose/6d98fbb3-2ffc-4d65-b7f9-0d770cc18a2d"
   title="Wohnung zur Miete - Berlin - 900 € - 3 Zimmer, 70 m², frei ab sofort">
   <img alt="Wohnung zur Miete 900 € 3 Zimmer 70 m² frei ab sofort Hauptstr 1 Mitte Berlin 10623" />
</a>
"""


class TestImmoweltParse:
    def test_parses_title_attributes(self) -> None:
        ls = iwp.parse_html(HTML_FIXTURE)
        assert len(ls) == 2
        first = ls[0]
        assert first.cold_rent_eur == 1299.0
        assert first.size_sqm == 48.2
        assert first.rooms == 2.0
        assert first.postal_code == "10179"  # aus dem alt-Attribut
        assert first.city == "Berlin"
        assert first.source == "immowelt"
        assert first.url.startswith("https://www.immowelt.de/expose/")

    def test_second_listing(self) -> None:
        ls = iwp.parse_html(HTML_FIXTURE)
        second = ls[1]
        assert second.cold_rent_eur == 900.0
        assert second.size_sqm == 70.0
        assert second.rooms == 3.0
        assert second.postal_code == "10623"

    def test_dedup(self) -> None:
        ls = iwp.parse_html(HTML_FIXTURE + HTML_FIXTURE)
        assert len(ls) == 2

    def test_empty(self) -> None:
        assert iwp.parse_html("") == []

    def test_market_stats(self) -> None:
        html = HTML_FIXTURE + """
<script type="application/ld+json">{"@context": "https://schema.org", "@type": "Product",
"name": "Wohnungen mieten in Berlin",
"offers": {"@type": "AggregateOffer", "offerCount": 6323, "lowPrice": 120, "highPrice": 59152, "priceCurrency": "EUR"}}</script>
"""
        stats = iwp.parse_market_stats(html)
        assert stats == {
            "offer_count": 6323,
            "low_price_eur": 120.0,
            "high_price_eur": 59152.0,
        }

    def test_build_url(self) -> None:
        assert iwp.build_url("Berlin") == "https://www.immowelt.de/liste/berlin/wohnungen/mieten"
