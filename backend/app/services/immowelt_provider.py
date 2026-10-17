"""Immowelt-Provider — HTML-Parsing der /liste-Suchseite.

Live verifiziert (Okt. 2026): Die /liste/<stadt>/wohnungen/mieten-Seite
liefert 200 OK mit 20+ Expose-Karten. Alle Kerndaten stehen im
title-Attribut der Links:

  title="Wohnung zur Miete - Berlin - 1.299 € - 2 Zimmer, 48,2 m²,
         1. Geschoss, frei ab 01.12.2026"

Der Product-JSON-LD liefert zusätzlich die AggregateOffer
(offerCount, lowPrice, highPrice) für die Marktstatistik.

Hinweis: die URL /immobilien/mieten/... gibt 403 (DataDome) — die
/liste-Route ist die funktionierende. Kein Bypass von Schutzmaßnahmen:
Wir fragen exakt die Seite ab, die ein Browser bei normaler Nutzung
sieht, mit vollständigen Browser-Headern.
"""
from __future__ import annotations

import hashlib
import re
from urllib.parse import quote

import httpx
import structlog

from app.schemas.search_agent import Listing

log = structlog.get_logger(__name__)

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "de-DE,de;q=0.9",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Upgrade-Insecure-Requests": "1",
}

# Expose-Link mit title-Attribut (die Datenquelle)
LINK_RE = re.compile(
    r'<a[^>]*href="(https://www\.immowelt\.de/expose/([0-9a-f-]+))"[^>]*'
    r'title="([^"]*)"[^>]*>(.{0,4000}?)</a>',
    re.IGNORECASE | re.DOTALL,
)
# alt-Attribut der Karten-Bilder enthält die Volladresse inkl. PLZ:
# "Wohnung zur Miete 1.299 € 2 Zimmer 48,2 m² ... Stallschreiberstr 23 Kreuzberg Berlin 10179"
ALT_RE = re.compile(r'alt="([^"]*(?:\d{5})[^"]*)"', re.IGNORECASE)
PRICE_RE = re.compile(r"(\d[\d.,]*)\s*€")
SIZE_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*m(?:²|2)")
ROOMS_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*Zimmer")
PLZ_CITY_RE = re.compile(r"-\s*(\d{5})\s+([A-ZÄÖÜ][\wäöü-]+)")


def parse_html(html: str) -> list[Listing]:
    """Immowelt-Listen-HTML → Listings aus den title-Attributen.

    title-Attribut liefert Preis/m²/Zimmer; das alt-Attribut des
    Karten-Bildes enthält zusätzlich Straße + Bezirk + PLZ.
    """
    listings: list[Listing] = []
    seen: set[str] = set()
    alt_texts: list[str] = [m.group(1) for m in ALT_RE.finditer(html)]

    listing_index = 0
    for m in LINK_RE.finditer(html):
        url, expose_id, title_attr = (
            m.group(1), m.group(2), m.group(3),
        )
        if expose_id in seen:
            continue
        seen.add(expose_id)
        current_index = listing_index
        listing_index += 1

        text = title_attr.replace("&amp;", "&").replace("&nbsp;", " ")

        price = None
        pm = PRICE_RE.search(text)
        if pm:
            try:
                price = float(pm.group(1).replace(".", "").replace(",", "."))
            except ValueError:
                price = None

        size = None
        sm = SIZE_RE.search(text)
        if sm:
            try:
                size = float(sm.group(1).replace(",", "."))
            except ValueError:
                size = None

        rooms = None
        rm = ROOMS_RE.search(text)
        if rm:
            try:
                rooms = float(rm.group(1).replace(",", "."))
            except ValueError:
                rooms = None

        plz = None
        city = None
        cm = PLZ_CITY_RE.search(text)
        if cm:
            plz = cm.group(1)
            city = cm.group(2)
        # alt-Attribut des zugehörigen Karten-Bildes (gleiche Position
        # in alt_texts wie das Listing in der Karten-Reihenfolge) enthält
        # die Volladresse: "... Stallschreiberstr 23 Kreuzberg Berlin 10179"
        if not plz and current_index < len(alt_texts):
            alt_context = alt_texts[current_index]
            apm = re.search(r"\b(\d{5})\b", alt_context)
            if apm:
                plz = apm.group(1)
            bcm = re.search(
                r"([A-ZÄÖÜ][\wäöü-]+)\s+Berlin\s+\d{5}", alt_context
            )
            if bcm and not city:
                city = "Berlin"
        if not city:
            # "Wohnung zur Miete - Berlin - ..." Muster
            tm = re.search(r"-\s*([A-ZÄÖÜ][\wäöü-]+)\s*-", text)
            if tm:
                city = tm.group(1)

        title = text.split(" - ")[0] if " - " in text else text[:100]

        listings.append(Listing(
            id=hashlib.sha256(expose_id.encode()).hexdigest()[:16],
            title=title[:120],
            url=url,
            city=city,
            postal_code=plz,
            cold_rent_eur=price,
            size_sqm=size,
            rooms=rooms,
            source="immowelt",
        ))
    return listings


def parse_market_stats(html: str) -> dict[str, float | int] | None:
    """AggregateOffer aus dem Product-JSON-LD (Markt-Statistik)."""
    import json

    blocks = re.findall(
        r'<script type="application/ld\+json"[^>]*>(.*?)</script>', html, re.DOTALL
    )
    for block in blocks:
        try:
            d = json.loads(block.strip())
            if isinstance(d, dict) and d.get("@type") == "Product":
                offers = d.get("offers", {})
                return {
                    "offer_count": int(offers.get("offerCount", 0)),
                    "low_price_eur": float(offers.get("lowPrice", 0)),
                    "high_price_eur": float(offers.get("highPrice", 0)),
                }
        except (ValueError, TypeError):
            continue
    return None


def build_url(city: str) -> str:
    """Standard-Listen-URL für eine Stadt bauen."""
    return f"https://www.immowelt.de/liste/{quote(city.lower())}/wohnungen/mieten"


async def fetch_listings(search_url: str) -> list[Listing]:
    """Immowelt-Listen-URL abfragen und parsen."""
    async with httpx.AsyncClient(
        headers=BROWSER_HEADERS,
        timeout=httpx.Timeout(30.0),
        follow_redirects=True,
    ) as client:
        resp = await client.get(search_url)
    if resp.status_code != 200:
        log.warning("immowelt_fetch_failed", status=resp.status_code)
        return []
    return parse_html(resp.text)
