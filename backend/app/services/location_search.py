"""Einfache Ort-Suche: User tippt Ort oder Ort+Straße → Portal-URLs.

Der User soll NICHT mehr URLs kopieren müssen. Er tippt:
  "Kreuzberg" / "Berlin" / "Hamburg, St. Pauli" / "München Schwabing"
und wir generieren daraus automatisch die Such-URLs für alle
unterstützten Portale.

Strategie (deterministisch, kein Geo-Dienst nötig):
1. Stadt aus dem Mietspiegel-Datensatz matchen (19 Städte mit slugs)
2. PLZ aus dem Mitgesuchten: findet der User "10117" o.ä., wird sie
   für Portalen genutzt, die PLZ-support haben (Kleinanzeigen: cityId)
3. Für Straßen: Portale filtern i.d.R. nicht nach Straße auf Suchebene
   — wir geben die Straße als Volltext-Filter mit, wo unterstützt
   (ImmoScout fulltext-Param), und benennen das im Hinweis.
"""
from __future__ import annotations

import re

import structlog
from pydantic import BaseModel, Field

log = structlog.get_logger(__name__)

PLZ_RE = re.compile(r"\b(\d{5})\b")


class LocationQuery(BaseModel):
    """Der einfache Such-Input: ein Textfeld."""
    query: str = Field(..., min_length=2, max_length=120,
                       description='z.B. "Kreuzberg", "Berlin, Friedrichstr", "80331"')
    max_rent: float | None = Field(None, gt=0, description="Optional: max Kaltmiete")
    rooms_min: float | None = Field(None, gt=0, description="Optional: min Zimmer")


class SearchUrls(BaseModel):
    """Generierte Portal-URLs aus einem Ort."""
    portal: str
    url: str
    hint: str | None = None


class LocationSearchResponse(BaseModel):
    ok: bool = True
    query: str
    city: str | None
    city_slug: str | None
    postal_code: str | None
    street: str | None
    urls: list[SearchUrls]
    hint: str


def resolve_location(query: str) -> dict:
    """Ort-Eingabe parsen: Stadt (aus Mietspiegel-Slugs) + PLZ + Straße."""
    from app.services.data_loader import get_database

    q = (query or "").strip()
    lowered = q.lower()

    # PLZ extrahieren falls angegeben
    plz_m = PLZ_RE.search(q)
    plz = plz_m.group(1) if plz_m else None

    # Stadt aus Datenbank matchen (Name oder Slug im Text)
    db = get_database()
    city_name = None
    city_slug = None
    for ds in db.all():
        if ds.name.lower() in lowered or ds.slug in lowered:
            city_name = ds.name
            city_slug = ds.slug
            break

    # Fallback: häufigste Großstädte auch ohne Mietspiegel-Eintrag
    known = {
        "leipzig": "Leipzig", "dresden": "Dresden", "stuttgart": "Stuttgart",
        "düsseldorf": "Düsseldorf", "bremen": "Bremen", "hannover": "Hannover",
        "nürnberg": "Nürnberg", "frankfurt": "Frankfurt", "köln": "Köln",
        "münchen": "München", "hamburg": "Hamburg", "berlin": "Berlin",
        "essen": "Essen", "bonn": "Bonn", "mannheim": "Mannheim",
        "münster": "Münster", "karlsruhe": "Karlsruhe", "kiel": "Kiel",
        "dortmund": "Dortmund", "freiburg": "Freiburg",
    }
    if city_name is None:
        for slug, name in known.items():
            if slug in lowered:
                city_name = name
                city_slug = slug
                break
    # Bezirke/Kieze → Stadt ableiten (wichtig für Berlin & Co.)
    districts = {
        "kreuzberg": ("Berlin", "berlin"), "prenzlauer berg": ("Berlin", "berlin"),
        "friedrichshain": ("Berlin", "berlin"), "neukölln": ("Berlin", "berlin"),
        "mitte": ("Berlin", "berlin"), "charlottenburg": ("Berlin", "berlin"),
        "wedding": ("Berlin", "berlin"), "schöneberg": ("Berlin", "berlin"),
        "wilmersdorf": ("Berlin", "berlin"), "moabit": ("Berlin", "berlin"),
        "steglitz": ("Berlin", "berlin"), "zehlendorf": ("Berlin", "berlin"),
        "tempelhof": ("Berlin", "berlin"), "spandau": ("Berlin", "berlin"),
        "weissensee": ("Berlin", "berlin"), "pankow": ("Berlin", "berlin"),
        "hohenschönhausen": ("Berlin", "berlin"), "treptow": ("Berlin", "berlin"),
        "köpenick": ("Berlin", "berlin"), "marzahn": ("Berlin", "berlin"),
        "hellersdorf": ("Berlin", "berlin"),
        "st. pauli": ("Hamburg", "hamburg"), "schanze": ("Hamburg", "hamburg"),
        "altbau": (None, None),  # kein Bezirk
        "schwabing": ("München", "muenchen"), "giesing": ("München", "muenchen"),
        "haidhausen": ("München", "muenchen"), "bogenhausen": ("München", "muenchen"),
        "ehrenfeld": ("Köln", "koeln"), "deutz": ("Köln", "koeln"),
        "sachsenhausen": ("Frankfurt", "frankfurt"), "bornheim": ("Frankfurt", "frankfurt"),
        "nordstadt": (None, None), "südstadt": (None, None),
    }
    if city_name is None:
        for district, (d_name, d_slug) in districts.items():
            if d_slug and district in lowered:
                city_name = d_name
                city_slug = d_slug
                break

    # Straße: Text nach dem Komma (wenn Stadt davor steht)
    street = None
    if city_name and "," in q:
        after_comma = q.split(",", 1)[1].strip()
        # PLZ aus dem Straßenteil entfernen
        after_comma = PLZ_RE.sub("", after_comma).strip(" ,")
        if after_comma:
            street = after_comma

    return {"city": city_name, "city_slug": city_slug, "plz": plz, "street": street}


def build_portal_urls(city: str | None, city_slug: str | None,
                      plz: str | None, street: str | None,
                      max_rent: float | None, rooms_min: float | None) -> list[SearchUrls]:
    """Aus Ort-Eingabe → fertige Portal-Such-URLs (kein Copy mehr nötig)."""
    from urllib.parse import quote

    urls: list[SearchUrls] = []
    if not (city or plz):
        return urls

    # 1. ImmoScout — Region-Suche per Stadt-Slug
    if city_slug:
        u = (
            f"https://www.immobilienscout24.de/Suche/de/{city_slug}/{city_slug}/wohnung-mieten"
            + (f"?price=-{max_rent:.0f}.0" if max_rent else "")
            + (f"&numberofrooms={rooms_min:g}.0-" if rooms_min else "")
            + (f"&fulltext={quote(street)}" if street else "")
        )
        urls.append(SearchUrls(
            portal="ImmoScout24", url=u,
            hint="Größtes Portal — Mobile-API-Anbindung aktiv" if not street else
                 "Straße als Volltext-Filter (Portal filtert selbst)",
        ))

    # 2. Immowelt — /liste/<slug>/wohnungen/mieten
    if city_slug:
        u = (
            f"https://www.immowelt.de/liste/{city_slug}/wohnungen/mieten"
        )
        urls.append(SearchUrls(portal="Immowelt", url=u,
                               hint="Live-Anbindung aktiv; Filter im Portal setzen"))

    # 3. Kleinanzeigen — Stadt-slug + Kategorie Wohnungsmiete
    if city_slug:
        u = f"https://www.kleinanzeigen.de/s-wohnung-mieten/{city_slug}/k0c203"
        if max_rent:
            u += f"?price=-{max_rent:.0f}"
        urls.append(SearchUrls(portal="Kleinanzeigen", url=u,
                               hint="Private Vermieter — oft günstiger!"))

    # 4. WG-Gesucht — 1-Zimmer-Wohnungen (ganze Wohnung) je Stadt
    if city_slug:
        u = f"https://www.wg-gesucht.de/wohnungen-in-{city.capitalize()}.8.2.1.0.html"
        urls.append(SearchUrls(portal="WG-Gesucht", url=u,
                               hint="Ganze Wohnungen + Zwischenmieten"))

    return urls


async def search_by_location(req: LocationQuery) -> LocationSearchResponse:
    """Hauptfunktion: einfache Ort-Eingabe → alle Portal-URLs."""
    loc = resolve_location(req.query)
    urls = build_portal_urls(
        loc["city"], loc["city_slug"], loc["plz"], loc["street"],
        req.max_rent, req.rooms_min,
    )
    if not urls:
        return LocationSearchResponse(
            query=req.query, city=None, city_slug=None,
            postal_code=loc["plz"], street=loc["street"], urls=[],
            hint=(
                "Ort nicht erkannt. Bitte eine Stadt (z.B. 'Berlin') oder "
                "PLZ (z.B. '10117') eingeben."
            ),
        )
    hint = (
        f"Ort erkannt: {loc['city']}"
        + (f", Straße: {loc['street']} (als Textfilter)" if loc["street"] else "")
        + ". Alle Portale bereit — einfach durchklicken oder als Such-Agent-"
        "Profil speichern (URL wird automatisch hinterlegt)."
    )
    return LocationSearchResponse(
        query=req.query, city=loc["city"], city_slug=loc["city_slug"],
        postal_code=loc["plz"], street=loc["street"], urls=urls, hint=hint,
    )
