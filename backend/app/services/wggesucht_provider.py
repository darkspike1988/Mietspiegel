"""WG-Gesucht-Provider — HTML-Parsing der Suchergebnisseite.

Live verifiziert (Okt. 2026): Die Suchseite rendert Karten mit
data-ad_id, Titel, Wohnfläche, Ortsteil und Online-Datum. Ohne
JavaScript laden (server-side gerendert), direkt parsebar.

WG-Gesucht-URL-Schema:
  /wohnungen-in-<stadt>.8.2.1.0.html   (1-Zimmer-Wohnungen = ganze Wohnung)
  /wg-zimmer-in-<stadt>.8.0.1.0.html   (WG-Zimmer)

Rechtlich wie Kleinanzeigen: öffentlich frei zugängliche Suchergebnisse,
keine Schutzmaßnahme wird umgangen (BGH I ZR 224/12).
"""
from __future__ import annotations

import hashlib
import re

import httpx
import structlog

from app.schemas.search_agent import Listing

log = structlog.get_logger(__name__)

BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120"

# Karte: <... data-ad_id="123"...> bis </a>
CARD_RE = re.compile(
    r'<[^>]*data-ad_id="(\d+)"[^>]*>(.{0,3000}?)</a>',
    re.DOTALL,
)
TITLE_RE = re.compile(r'class="[^"]*headline[^"]*"[^>]*>([^<]+)<')
SIZE_RE = re.compile(r'(\d+(?:[.,]\d+)?)\s*(?:m&sup2;|m²|m2)')
PRICE_RE = re.compile(r'(\d{2,4})\s*(?:€|&euro;)')
PLZ_RE = re.compile(r'(\d{5})')
ROOMS_RE = re.compile(r'(\d+(?:[.,]\d+)?)\s*-?\s*Zimmer', re.IGNORECASE)


def parse_html(html: str) -> list[Listing]:
    """WG-Gesucht-Suchergebnis-HTML → Listings (best effort)."""
    listings: list[Listing] = []
    seen: set[str] = set()

    for m in CARD_RE.finditer(html):
        ad_id, block = m.group(1), m.group(2)
        if ad_id in seen:
            continue
        seen.add(ad_id)

        text = block.replace("&nbsp;", " ").replace("&sup2;", "²").replace("&euro;", "€")
        plain = re.sub(r"<script.*?</script>", " ", text, flags=re.DOTALL)
        plain = re.sub(r"<[^>]+>", " ", plain)
        plain_s = " ".join(plain.split())

        tm = TITLE_RE.search(text)
        title = " ".join(tm.group(1).split()) if tm else plain_s[:80]

        size = None
        sm = SIZE_RE.search(plain_s)
        if sm:
            try:
                size = float(sm.group(1).replace(",", "."))
            except ValueError:
                size = None

        price = None
        pm = PRICE_RE.search(plain_s)
        if pm:
            try:
                price = float(pm.group(1))
            except ValueError:
                price = None

        rooms = None
        rm = ROOMS_RE.search(title) or ROOMS_RE.search(plain_s)
        if rm:
            try:
                rooms = float(rm.group(1).replace(",", "."))
            except ValueError:
                rooms = None

        plz = None
        plm = PLZ_RE.search(plain_s)
        if plm:
            plz = plm.group(1)

        listings.append(Listing(
            id=hashlib.sha256(ad_id.encode()).hexdigest()[:16],
            title=title[:120],
            url=f"https://www.wg-gesucht.de/anzeige_{ad_id}.html",
            city=None,
            postal_code=plz,
            cold_rent_eur=price,
            size_sqm=size,
            rooms=rooms,
            source="wg-gesucht",
        ))
    return listings


async def fetch_listings(search_url: str) -> list[Listing]:
    """WG-Gesucht-Such-URL abfragen und parsen."""
    async with httpx.AsyncClient(
        headers={"User-Agent": BROWSER_UA, "Accept-Language": "de-DE,de;q=0.9"},
        timeout=httpx.Timeout(25.0),
        follow_redirects=True,
    ) as client:
        resp = await client.get(search_url)
    if resp.status_code != 200:
        log.warning("wgg_fetch_failed", status=resp.status_code)
        return []
    return parse_html(resp.text)
