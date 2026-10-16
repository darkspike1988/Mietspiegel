"""ImmoScout24 Mobile-API-Provider (reverse engineered, öffentlich dokumentiert).

Grundlage: Die mobile ImmoScout24-App kommuniziert mit einer JSON-API unter
api.mobile.immobilienscout24.de, die — anders als die Website — nicht hinter
AWS WAF/Captcha-Schutz steht. Die Endpunkte sind community-dokumentiert
(u.a. in Fredys reverse-engineered-immoscout.md) und seit Jahren stabil:

  GET  /search/total?{params}   → Trefferzahl
  POST /search/list?{params}    → Listings (JSON)
  GET  /expose/{id}            → Detaildaten

Rechtliche Einordnung (deutsche Rechtslage): Der BGH (I ZR 224/12,
"Flugvermittlung im Internet") hat automatisiertes Auslesen öffentlich
frei zugänglicher Daten für zulässig erklärt; unlauter ist es nur, wenn
technische Schutzmaßnahmen umgangen werden. Diese API ist ungeschützt
öffentlich abfragbar — es wird keine Schutzmaßnahme (WAF, Captcha, Login)
umgangen. Wir verzichtet bewusst auf:
  - Captcha-Solving-Dienste
  - Anti-Bot-Bypass-Keys (DataDome etc.)
  - Login-geschützte Bereiche

Fair-Use: nur die erste Ergebnisseite, respektvolle Intervalle,
identifizierender User-Agent.

Eigenständige Implementierung — kein Code aus Fredy übernommen
(Commons Clause); die API-Spezifikation selbst ist ein öffentlicher
Fakt (Schnittstelle, nicht schöpferisches Werk).
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import structlog

from app.schemas.search_agent import Listing

log = structlog.get_logger(__name__)

API_BASE = "https://api.mobile.immobilienscout24.de"
# User-Agent der öffentlichen App (aus der App extrahierbar, community-dokumentiert)
APP_USER_AGENT = "ImmoScout_28.1_26.5.2_._"

# ----------------------------------------------------------------------
# URL-Translator: Web-Such-URL → Mobile-API-Parameter
# ----------------------------------------------------------------------

# Pfad-Segmente → realestatetype (die wichtigsten Wohnungssuchen)
REAL_ESTATE_TYPES: dict[str, str] = {
    "wohnung-mieten": "apartmentrent",
    "wohnung-kaufen": "apartmentbuy",
    "haus-mieten": "houserent",
    "haus-kaufen": "housebuy",
    "wg-zimmer": "flatshareroom",
    "immobilie-mieten": "apartmentrent,houserent",
    "immobilie-kaufen": "apartmentbuy,housebuy",
}

# Web-Query-Parameter → API-Parameter (1:1-Mapping, die gängigsten)
PARAM_MAP: dict[str, str] = {
    "price": "price",
    "numberofrooms": "numberofrooms",
    "livingspace": "livingspace",
    "pricetype": "pricetype",
    "constructionyear": "constructionyear",
    "equipment": "equipment",
    "sorting": "sorting",
    "exclusioncriteria": "exclusioncriteria",
    "fulltext": "fulltext",
    "publishedafter": "publishedafter",
}

# Diese Web-Parameter sind Tracking/Paging-Rauschen → verwerfen
NOISE_PARAMS = {
    "enteredfrom", "centerofsearchaddress", "pagenumber", "pagesize",
    "searchid", "referrer", "utmsource", "utmmedium", "utmcampaign",
    "utmcontent", "utmterm", "cmpid", "action", "appendci",
}


def translate_url(web_url: str) -> dict[str, str]:
    """Übersetzt eine ImmoScout-Web-Such-URL in Mobile-API-Parameter.

    Unterstützt:
      - Region-Suchen: /Suche/de/<state>/<city>/<type> → geocodes=/de/<state>/<city>
      - Radius-Suchen: /Suche/radius/<type>?geocoordinates=lat;lng;km
      - Query-Filter: price, numberofrooms, livingspace, pricetype, equipment

    Liefert ein dict mit 'path_params' (für den Query-String der API).
    Wirft ValueError, wenn kein Immobilientyp erkennbar ist.
    """
    parsed = urlparse(web_url)
    path = parsed.path.lower()
    query = {k: v[0] for k, v in parse_qs(parsed.query).items()}

    # 1. realestatetype aus letztem Pfad-Segment
    realestatetype = None
    for web_path, api_type in REAL_ESTATE_TYPES.items():
        if path.endswith(web_path):
            realestatetype = api_type
            break
    if realestatetype is None:
        raise ValueError(
            f"Kein unterstützter Immobilientyp in URL erkennbar: {web_url}. "
            "Erwartet z.B. .../wohnung-mieten am Ende des Pfads."
        )

    params: dict[str, str] = {
        "realestatetype": realestatetype,
    }

    # 2. searchType + geocodes
    if "/suche/radius/" in path:
        # Radius-Suche: ?geocoordinates=lat;lng;km
        if "geocoordinates" in query:
            params["searchType"] = "radius"
            params["geocoordinates"] = query["geocoordinates"]
    elif "/suche/shape/" in path:
        if "shape" in query:
            params["searchType"] = "shape"
            params["shape"] = query["shape"]
    elif "/suche/de/" in path or "/suche/" in path:
        # Region-Suche: /Suche/de/<state>/<city>/<type>
        m = re.search(r"/suche/de/([^/]+)/([^/]+)", path)
        if m:
            state, city = m.group(1), m.group(2)
            params["searchType"] = "region"
            geocodes = query.get("geocodes") or f"/de/{state}/{city}"
            params["geocodes"] = geocodes
        else:
            # numerische geocodes (Bezirke) können in der Query stehen
            if "geocodes" in query:
                params["searchType"] = "region"
                params["geocodes"] = query["geocodes"]

    # 3. Filter-Parameter übersetzen (Rauschen verwerfen)
    for web_key, value in query.items():
        if web_key.lower() in NOISE_PARAMS:
            continue
        api_key = PARAM_MAP.get(web_key.lower())
        if api_key:
            params[api_key] = value
        else:
            log.info(
                "immoscout_param_dropped",
                param=web_key,
                hint="Unbekannter Filter wurde verworfen (ggf. melden).",
            )

    # 4. Default: Wohnungsmiete blendet Tauschwohnungen aus (wie die Website)
    if realestatetype == "apartmentrent" and "exclusioncriteria" not in params:
        params["exclusioncriteria"] = "swapflat"

    return params


# ----------------------------------------------------------------------
# Fetch + Parse
# ----------------------------------------------------------------------

async def fetch_listings(web_url: str) -> list[Listing]:
    """Such-URL gegen die Mobile API ausführen und Listings parsen."""
    params = translate_url(web_url)
    params["pagenumber"] = "1"

    headers = {
        "User-Agent": APP_USER_AGENT,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(
        headers=headers,
        timeout=httpx.Timeout(25.0),
        follow_redirects=True,
    ) as client:
        resp = await client.post(
            f"{API_BASE}/search/list",
            params=params,
            json={"supportedResultListTypes": [], "userData": {}},
        )
    if resp.status_code != 200:
        log.warning(
            "immoscout_api_error",
            status=resp.status_code,
            body=resp.text[:200],
        )
        return []
    try:
        data = resp.json()
    except ValueError as e:
        log.warning("immoscout_response_not_json", error=str(e))
        return []
    return parse_response(data)


def parse_response(data: dict[str, Any]) -> list[Listing]:
    """Mobile-API-JSON → Listing-Objekte.

    Aktuelles Listen-Format (verifiziert gegen die Live-API, Okt. 2026):
      resultListItems: [{type: "EXPOSE_RESULT", item: {...}}]
      item.attributes: Liste von {label, value}-Paaren mit fester
      Reihenfolge: [0]=Preis, [1]=Wohnfläche, [2]=Zimmer
      (Kaltmiete/m²/Zi. als formatierter String wie "362 €")
      item.address.line: "Straße Nr, PLZ Ort, Bezirk (Ortsteil)"
    Werbung (type != EXPOSE_RESULT) wird übersprungen.
    """
    import hashlib

    listings: list[Listing] = []
    items = data.get("resultListItems") or []
    for wrapper in items:
        if not isinstance(wrapper, dict):
            continue
        if wrapper.get("type") != "EXPOSE_RESULT":
            continue
        item = wrapper.get("item")
        if not isinstance(item, dict) or not item.get("id"):
            continue
        try:
            # Zahlen aus attributes (feste Reihenfolge, als Strings)
            attrs = item.get("attributes") or []
            values = [str(a.get("value", "")) for a in attrs]

            def _num_from(idx: int, pattern: str) -> float | None:
                if idx >= len(values):
                    return None
                import re as _re
                m = _re.search(pattern, values[idx].replace("\u00a0", " "))
                return float(m.group(1).replace(",", ".")) if m else None

            cold_rent = _num_from(0, r"(\d+(?:[.,]\d+)?)")
            size_sqm = _num_from(1, r"(\d+(?:[.,]\d+)?)")
            rooms = _num_from(2, r"(\d+(?:[.,]\d+)?)")

            # Adresse parsen: "Hellersdorfer Str 91, 12619 Berlin, Kaulsdorf"
            addr_line = str(item.get("address", {}).get("line", ""))
            postal = None
            city = None
            m = re.search(r"(\d{5})\s+([^,]+)", addr_line)
            if m:
                postal = m.group(1)
                city = m.group(2).strip()

            listing_id = str(item.get("id", "")) or "unknown"
            title = str(item.get("title", "") or "ImmoScout-Anzeige")

            listings.append(Listing(
                id=hashlib.sha256(listing_id.encode()).hexdigest()[:16],
                title=title,
                url=f"https://www.immobilienscout24.de/expose/{listing_id}",
                city=city,
                postal_code=postal,
                cold_rent_eur=cold_rent,
                size_sqm=size_sqm,
                rooms=rooms,
                source="immoscout24",
            ))
        except (TypeError, ValueError, AttributeError) as e:
            log.warning("immoscout_entry_parse_failed", error=str(e))
            continue
    return listings
