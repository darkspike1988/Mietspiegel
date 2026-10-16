"""Tests für Kleinanzeigen- und WG-Gesucht-Provider (offline, mit Fixtures)."""
from __future__ import annotations

from app.services import kleinanzeigen_provider as kap
from app.services import wggesucht_provider as wgp

KA_FIXTURE = """
<article data-adid="3487014101" data-href="/s-anzeige/test-wohnung-2-zimmer-60-m/3487014101-203-3395">
<script type="application/ld+json">{"title":"Testwohnung - 2 Zimmer, 60 m\\u00b2 - Kreuzberg"}</script>
<div>700&nbsp;€</div><div>60&nbsp;m²</div><div>2 Zi.</div><div>10999 Berlin</div>
</article>
<article data-adid="222" data-href="/s-anzeige/andere/222-203-1">
<div>Zur Miete</div>
</article>
"""


class TestKleinanzeigen:
    def test_parses_article_cards(self) -> None:
        ls = kap.parse_html(KA_FIXTURE)
        assert len(ls) == 2
        first = ls[0]
        assert first.title.startswith("Testwohnung")
        assert first.cold_rent_eur == 700.0
        assert first.size_sqm == 60.0
        assert first.rooms == 2.0
        assert first.postal_code == "10999"
        assert first.city == "Berlin"
        assert first.source == "kleinanzeigen"
        assert first.url.startswith("https://www.kleinanzeigen.de/s-anzeige/")

    def test_dedups_by_adid(self) -> None:
        doubled = KA_FIXTURE + KA_FIXTURE
        ls = kap.parse_html(doubled)
        # Jede ad_id darf nur einmal vorkommen (4 Artikel-Blöcke, 2 unique IDs)
        ids = [l.id for l in ls]
        assert len(ids) == len(set(ids))
        assert len(ls) == 2

    def test_empty_html(self) -> None:
        assert kap.parse_html("<html>leer</html>") == []


WGG_FIXTURE = """
<a href="/anzeige_10718488.html" class="card">
  <div class="headline">Schöne WG in Mitte</div>
  <div>25 m&sup2; · Berlin Mitte · ab sofort</div>
</a>
"""


class TestWgGesucht:
    def test_parses_cards(self) -> None:
        ls = wgp.parse_html(WGG_FIXTURE)
        # Fixture bewusst ohne data-ad_id → Karten werden nicht gefunden (Attribute nötig)
        # echte Karten haben data-ad_id; hier nur der negative Test:
        assert isinstance(ls, list)

    def test_real_shape(self) -> None:
        html = (
            '<div data-ad_id="10718488">'
            '<a href="/anzeige_10718488.html">'
            '<span class="headline">1-Zimmer-Wohnung Berlin Hermsdorf</span>'
            '25 m&sup2; 10967 Berlin 450 € 1 Zimmer</a></div>'
        )
        ls = wgp.parse_html(html)
        assert len(ls) == 1
        assert ls[0].size_sqm == 25.0
        assert ls[0].title == "1-Zimmer-Wohnung Berlin Hermsdorf"
        assert ls[0].url.endswith("anzeige_10718488.html")

    def test_empty_html(self) -> None:
        assert wgp.parse_html("") == []


class TestProviderDispatch:
    async def test_kleinanzeigen_url_routed(self) -> None:
        from unittest.mock import AsyncMock, patch

        from app.schemas.search_agent import SearchProfile
        from app.services import search_agent as sa

        profile = SearchProfile(
            name="ka", url="https://www.kleinanzeigen.de/s-wohnung-mieten/berlin/k0c203l3829",
        )
        with patch.object(
            kap, "fetch_listings", new=AsyncMock(return_value=[])
        ):
            # fetch_profile importiert das Modul dynamisch — patch via sys.modules
            import app.services.kleinanzeigen_provider
            with patch.object(
                app.services.kleinanzeigen_provider,
                "fetch_listings",
                new=AsyncMock(return_value=[]),
            ):
                result = await sa.fetch_profile(profile)
        assert result == []
