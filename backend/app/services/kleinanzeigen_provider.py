"""Kleinanzeigen-Provider — HTML-Parsing der Suchergebnisseite.

Live verifiziert (Okt. 2026): Die Suchseite liefert 200 OK mit
/s-anzeige/-Links und Preisangaben. Kleinanzeigen nutzt für die
Suchergebnisse klassische HTML-Karten; die mobile App-API
(api.kleinanzeigen.de) erfordert App-Authentifizierung und wird
bewusst nicht genutzt.

Rechtlich: öffentlich frei zugängliche Suchergebnisse, keine
Schutzmaßnahme wird umgangen (BGH I ZR 224/12). Fair-Use: Seite 1,
respektvolle Intervalle.
"""
from __future__ import annotations

import hashlib
import re

import httpx
import structlog

from app.schemas.search_agent import Listing

log = structlog.get_logger(__name__)

BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120"

# Eine Listing-Karte: <article ... data-adid-or-class> ... title, price, details
# Verifiziert: Links folgen /s-anzeige/<slug>/<id>-<category>-<city>
ARTICLE_RE = re.compile(
    r'<article[^>]*data-adid="(\d+)"[^>]*data-href="([^"]+)"[^>]*>(.*?)</article>',
    re.DOTALL,
)
PRICE_RE = re.compile(r'(\d[\d.,]*)\s*€')
DETAIL_RE = re.compile(r'(\d+(?:[.,]\d+)?)\s*m(?:²|2)')
ROOMS_RE = re.compile(r'(\d+(?:[.,]\d+)?)\s*-?\s*Zi\.?', re.IGNORECASE)
PLZ_CITY_RE = re.compile(r'(\d{5})\s+([A-ZÄÖÜ][\wäöü-]+)')


def parse_html(html: str) -> list[Listing]:
    """Kleinanzeigen-Suchergebnis-HTML → Listings.

    Live verifizierte Struktur (Okt. 2026):
      <article data-adid="3487014101" data-href="/s-anzeige/<slug>/...">
        mit JSON-LD (title) + Karten-Text (Preis, m², Zi., PLZ Stadt)
    """
    listings: list[Listing] = []
    seen_ids: set[str] = set()

    for m in ARTICLE_RE.finditer(html):
        ad_id, href, block = m.group(1), m.group(2), m.group(3)
        if ad_id in seen_ids:
            continue
        seen_ids.add(ad_id)
        text = block.replace("&nbsp;", " ").replace("&amp;", "&")
        # HTML-Kommentare/Tags raus für Text-Suche
        plain = re.sub(r"<script.*?</script>", " ", text, flags=re.DOTALL)
        plain = re.sub(r"<[^>]+>", " ", plain)

        # Titel aus JSON-LD nehmen (sauberer), sonst Slug
        title = None
        import json as _json
        tm = re.search(r'"title":"((?:[^"\\]|\\.)*)"', text)
        if tm:
            try:
                title = _json.loads(f'"{tm.group(1)}"')
            except (ValueError, TypeError):
                title = tm.group(1)
        if not title:
            slug = href.split("/s-anzeige/")[1].split("/")[0] if "/s-anzeige/" in href else ad_id
            title = slug.replace("-", " ").strip()

        price = None
        pm = PRICE_RE.search(plain)
        if pm:
            try:
                price = float(pm.group(1).replace(".", "").replace(",", "."))
            except ValueError:
                price = None

        size = None
        sm = DETAIL_RE.search(plain)
        if sm:
            try:
                size = float(sm.group(1).replace(",", "."))
            except ValueError:
                size = None

        rooms = None
        rm = ROOMS_RE.search(plain)
        if rm:
            try:
                rooms = float(rm.group(1).replace(",", "."))
            except ValueError:
                rooms = None

        plz = None
        city = None
        cm = PLZ_CITY_RE.search(plain)
        if cm:
            plz = cm.group(1)
            city = cm.group(2)
        if not city:
            # Stadt aus URL /s-wohnung-mieten/<city>/
            um = re.search(r"/s-[^/]+/([^/]+)", href)
            if um:
                city = um.group(1).capitalize()

        listings.append(Listing(
            id=hashlib.sha256(ad_id.encode()).hexdigest()[:16],
            title=title[:120],
            url=f"https://www.kleinanzeigen.de{href}",
            city=city,
            postal_code=plz,
            cold_rent_eur=price,
            size_sqm=size,
            rooms=rooms,
            source="kleinanzeigen",
        ))
    return listings


async def fetch_listings(search_url: str) -> list[Listing]:
    """Kleinanzeigen-Such-URL abfragen und parsen."""
    if "/s-" not in search_url:
        # Standard-Form ableiten: /s-wohnung-mieten/<city>/k0c203l<cityid>
        raise ValueError(
            "Erwartet eine Kleinanzeigen-Such-URL wie "
            "https://www.kleinanzeigen.de/s-wohnung-mieten/berlin/k0c203l3829"
        )
    async with httpx.AsyncClient(
        headers={"User-Agent": BROWSER_UA, "Accept-Language": "de-DE,de;q=0.9"},
        timeout=httpx.Timeout(25.0),
        follow_redirects=True,
    ) as client:
        resp = await client.get(search_url)
    if resp.status_code != 200:
        log.warning("kleinanzeigen_fetch_failed", status=resp.status_code)
        return []
    return parse_html(resp.text)
