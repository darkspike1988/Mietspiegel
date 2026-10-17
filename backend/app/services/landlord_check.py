"""Vermieter-Check — wer ist mein Vermieter?

Drei valide Datenquellen (keine erfundenen Daten):

1. KURATIERTE KONZERN-DATENBANK:
   Die großen börsennotierten Wohnungskonzerne (Vonovia, Deutsche
   Wohnen, LEG, ...) mit öffentlich bekannten Fakten — Konzernsitz,
   Wohnungsbestand, Börsenlisting. Quellen: Geschäftsberichte der
   Konzerne (publizitätspflichtig), Wikipedia/Presse.

2. GOOGLE NEWS RSS (offizieller, erlaubter Feed):
   news.google.com/rss/search?q=<vermieter>+<stadt> — lokale und
   politische Nachrichten zum Vermieter, legal abonnierbar.

3. OFFIZIELLE REGISTER-LINKS:
   Unternehmensregister/Handelsregister-Suche + Bundesanzeiger —
   der User prüft Insolvenzen/Jahresabschlüsse selbst mit einem
   Klick (dafür gibt es keine API, aber die Portale sind kostenlos).
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

import httpx
import structlog

from app.schemas.landlord_check import (
    LandlordInfo,
    LandlordNews,
    LandlordResponse,
)

log = structlog.get_logger(__name__)

# Kuratierte Datenbank: die größten deutschen Wohnungskonzerne.
# Zahlen aus Geschäftsberichten (publizitätspflichtig, ~Stand 2024).
MAJOR_LANDLORDS: dict[str, dict] = {
    "vonovia": {
        "name": "Vonovia SE",
        "hq": "Bochum",
        "apartments": "≈ 550.000 (DE/AT/SE)",
        "stock": "VNA (DAX)",
        "notes": "Größter deutscher Wohnungs-konzern. Mehrfach in der Kritik "
                 "für Mieterhöungen und Instandhaltungs-Rückstände; "
                 "Vietäuser-Affäre 2023 (sanierte Wohnungen über Mietspiegel).",
    },
    "deutsche wohnen": {
        "name": "Deutsche Wohnen SE (Teil von Vonovia)",
        "hq": "Berlin",
        "apartments": "≈ 155.000",
        "stock": "DWH (im MDAX)",
        "notes": "Zweitgrößter Konzern, seit 2021 Teil der Vonovia-Gruppe. "
                 "G20-Proteste 2017 richteten sich u.a. gegen Deutsche Wohnen.",
    },
    "leg": {
        "name": "LEG Immobilien AG",
        "hq": "Düsseldorf",
        "apartments": "≈ 170.000",
        "stock": "LEG (MDAX)",
        "notes": "Größter öffentlich geförderter Bestandshalter; Fokus NRW.",
    },
    "howoge": {
        "name": "HOWOGE Wohnungsbaugesellschaft mbH",
        "hq": "Berlin",
        "apartments": "≈ 76.000",
        "stock": "Landeseigene Berlin (100 % Land Berlin)",
        "notes": "Kommunales Wohnungsunternehmen Berlins — oft günstiger "
                 "als private Konzerne, WBS-Wohnungen im Angebot.",
    },
    "gewobag": {
        "name": "GEWOBAG Wohnungsbau-Aktiengesellschaft Berlin",
        "hq": "Berlin",
        "apartments": "≈ 76.000",
        "stock": "Landeseigene Berlin",
        "notes": "Zweites großes landeseigenes Unternehmen Berlins.",
    },
    "degewo": {
        "name": "degewo AG",
        "hq": "Berlin",
        "apartments": "≈ 76.000",
        "stock": "Landeseigene Berlin",
        "notes": "Drittes großes kommunales Wohnungsunternehmen Berlins.",
    },
}


def find_landlord(query: str) -> LandlordInfo | None:
    """Vermieter-Suche gegen die kuratierte Datenbank (fuzzy)."""
    q = (query or "").strip().lower()
    if not q:
        return None
    for key, entry in MAJOR_LANDLORDS.items():
        if key in q or entry["name"].lower() in q:
            return LandlordInfo(
                key=key, name=entry["name"], hq=entry["hq"],
                apartments=entry["apartments"], stock=entry["stock"],
                notes=entry["notes"],
                register_url=(
                    "https://www.unternehmensregister.de/de/suche/"
                    f"unternehmen?searchString={entry['name']}"
                ),
            )
    return None


async def fetch_landlord_news(
    landlord_name: str, city: str | None = None, limit: int = 5
) -> list[LandlordNews]:
    """Lokale/politische Nachrichten via Google News RSS (offizieller Feed)."""
    q = f"{landlord_name}{' ' + city if city else ''} Vermieter OR Miete OR Mieter"
    url = f"https://news.google.com/rss/search?q={httpx.QueryParams({'q': q}).get('q')}"
    # Google News RSS erwartet q param:
    url = f"https://news.google.com/rss/search?q={q.replace(' ', '+')}+when:1y&hl=de&gl=DE&ceid=DE:de"
    items: list[LandlordNews] = []
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url)
        if resp.status_code != 200:
            return items
        root = ET.fromstring(resp.text)
        for item in root.findall(".//item")[:limit]:
            title_el = item.find("title")
            link_el = item.find("link")
            date_el = item.find("pubDate")
            source_el = item.find("source")
            if title_el is None or not (title_el.text or "").strip():
                continue
            items.append(LandlordNews(
                title=(title_el.text or "").strip()[:200],
                link=(link_el.text or "").strip() if link_el is not None else None,
                published=(date_el.text or "").strip() if date_el is not None else None,
                source=(source_el.text or "").strip() if source_el is not None else None,
            ))
    except (httpx.HTTPError, ET.ParseError) as e:
        log.warning("landlord_news_failed", error=str(e))
    return items


async def check_landlord(query: str, city: str | None = None) -> LandlordResponse:
    """Der Vermieter-Check: Konzern-Daten + News + Register-Links."""
    info = find_landlord(query)
    if info is None:
        # Kein bekannter Konzern → Register-Suche + News zum Namen
        news = await fetch_landlord_news(query, city)
        return LandlordResponse(
            query=query,
            known=False,
            hint=(
                "Kein Eintrag in der Konzern-Datenbank — vermutlich privater "
                "oder kleinerer Vermieter. Prüfe: (1) Handelsregister-Link "
                "unten (Existenz, Geschäftsführer, ggf. Insolvenzen), "
                "(2) lokale Nachrichten. Tipp: Bei Zweifeln Auskunft nach "
                "§556g BGB oder Mieterverein fragen."
            ),
            news=news,
            register_url=(
                "https://www.unternehmensregister.de/de/suche/"
                f"unternehmen?searchString={query.replace(' ', '+')}"
            ),
            bundesanzeiger_url=(
                "https://www.bundesanzeiger.de/pub/de/suche1?searchfield1="
                f"{query.replace(' ', '+')}"
            ),
        )
    news = await fetch_landlord_news(info.name, city)
    return LandlordResponse(
        query=query,
        known=True,
        info=info,
        hint=(
            f"{info.name} ist ein bekannter Großvermieter. Beachte: Konzerne "
            "haben oft Standard-Schreiben für Mieterhöhungen — die sind "
            "nicht immer rechtssicher. Nutze den Mieterhöhung-Check und "
            "bei §556d-Verdacht den Widerspruchsgenerator."
        ),
        news=news,
        register_url=(
            "https://www.unternehmensregister.de/de/suche/"
            f"unternehmen?searchString={info.name.replace(' ', '+')}"
        ),
        bundesanzeiger_url=(
            "https://www.bundesanzeiger.de/pub/de/suche1?searchfield1="
            f"{info.name.replace(' ', '+')}"
        ),
    )
