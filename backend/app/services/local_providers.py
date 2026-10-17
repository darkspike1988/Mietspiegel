"""Lokale Versorger-Provider — wer versorgt eine PLZ konkret?

Der Mieter will bei jedem Wohnprofil/Wohnungstreff sofort sehen, WER
lokal lieferbar ist — nicht nur Vergleichsportale. Quellen:

1. GRUNDVERSORGER (deterministisch, die wichtigste Info):
   Der Grundversorger je PLZ ist die legally definierte Ersatzversorgung
   (§36 EnWG). Einen konsolidierten offenen Datensatz gibt es nicht als
   Maschinenlesbare API — wir pflegen daher die größten Städte als
   Datensatz (wie der Mietspiegel) mit Quellenangabe, und liefern für
   alle anderen PLZ den Verweis auf die BNetzA-Grundversorger-Suche.

2. LOKALE VERSORGER (Stadtwerke-Verbünde):
   Neben dem Grundversorger gibt es lokale Stadtwerke — wir zeigen die
   wichtigsten Anbieter je Stadt.

3. BREITBAND: Verweis auf Breitbandatlas (offiziell, CC BY 4.0) für
   die Verfügbarkeits-Abfrage je Adresse.
"""
from __future__ import annotations

from app.schemas.local_providers import (
    GrundversorgerInfo,
    LocalProvidersResponse,
)

# Stadt → Grundversorger (Strom) — gepflegter Datensatz, größte Städte.
# Quelle je Eintrag: Website des Grundversorgers / BNetzA-Veröffentlichung.
GRUNDVERSORGER_STROM: dict[str, dict] = {
    "berlin": {
        "name": "Vattenfall Europe Berlin (Strom) / GASAG (Gas)",
        "website": "https://www.vattenfall.de",
        "city_match": ["10117", "10115", "10119", "10999", "12043", "10557",
                       "10437", "13347", "12163", "12619"],
    },
    "hamburg": {
        "name": "Vattenfall Europe Hamburg / EWE (Gas)",
        "website": "https://www.vattenfall.de",
        "city_match": ["20095", "20099", "20359", "22765", "22087"],
    },
    "münchen": {
        "name": "SWM München (SWM Versorgung)",
        "website": "https://www.swm.de",
        "city_match": ["80331", "80333", "80539", "81667", "81541"],
    },
    "köln": {
        "name": "RheinEnergie (Stadtwerke Köln)",
        "website": "https://www.rheinenergie.com",
        "city_match": ["50667", "50668", "50676", "50823", "51103"],
    },
    "frankfurt": {
        "name": "Mainova (Stadtwerke Frankfurt)",
        "website": "https://www.mainova.de",
        "city_match": ["60311", "60313", "60314", "60316", "60594"],
    },
}

# Bekannte Stadtwerke/lokale Versorger je Stadt (Auswahl)
LOKALE_VERSORGER: dict[str, list[str]] = {
    "berlin": ["GASAG", "Berliner Stadtwerke", "Grundgrün Energie"],
    "hamburg": ["EWE", "Hamburg Energie", "Stromnetz Hamburg"],
    "münchen": ["SWM", "Münchner Stadtwerke"],
    "köln": ["RheinEnergie", "Energiepool"],
    "frankfurt": ["Mainova", "EWR"],
}


def _city_from_plz(plz: str) -> str | None:
    """Stadt aus PLZ-Präfix ableiten (grob, Top-Städte)."""
    for city, entry in GRUNDVERSORGER_STROM.items():
        if plz in entry.get("city_match", []):
            return city
    # PLZ-Präfix-Bereiche als Fallback
    prefixes = {
        "10": "berlin", "11": "berlin", "12": "berlin", "13": "berlin", "14": "berlin",
        "20": "hamburg", "21": "hamburg", "22": "hamburg",
        "80": "münchen", "81": "münchen", "82": "münchen",
        "50": "köln", "51": "köln",
        "60": "frankfurt", "61": "frankfurt", "65": "frankfurt",
    }
    return prefixes.get(plz[:2])


def get_local_providers(postal_code: str) -> LocalProvidersResponse:
    """Lokale Strom-/Gas-Anbieter + Breitband für eine PLZ anzeigen."""
    city_key = _city_from_plz(postal_code)
    grund = GRUNDVERSORGER_STROM.get(city_key or "", {})
    lokale = LOKALE_VERSORGER.get(city_key or "", [])

    grundinfo = GrundversorgerInfo(
        name=grund.get("name") or "Siehe BNetzA-Grundversorger-Suche",
        website=grund.get("website"),
    ) if grund else None

    return LocalProvidersResponse(
        postal_code=postal_code,
        city=city_key,
        grundversorger=grundinfo,
        lokale_versorger=lokale,
        breitband_check_url=(
            "https://gigabitgrundbuch.bund.de/GIGA/DE/Breitbandatlas/start.html"
        ),
        bnetza_grundversorger_url=(
            "https://www.bundesnetzagentur.de/DE/Fachthemen/Energie/"
            "VersorgendeUnternehmen/Grundversorgung/grundversorgung.html"
        ),
        hint=(
            "Ohne Vertrag bist du automatisch im (teuersten) Grundversorger-"
            "Tarif — aktiver Wechsel spart oft 200-400 €/Jahr. Die lokal "
            "verfügbaren Anbieter findest du bei den Vergleichsportalen "
            "unten auf /versorger; die Grundversorger-Suche der BNetzA "
            "liefert den legalen Grundversorger je PLZ."
            if grundinfo
            else (
                "Für diese PLZ liegt kein gepflegter Grundversorger-Datensatz "
                "vor — nutze die offizielle BNetzA-Grundversorger-Suche "
                "(Link), um den Grundversorger zu bestimmen. Der Vergleich "
                "über die Portale funktioniert PLZ-genau."
            )
        ),
    )
