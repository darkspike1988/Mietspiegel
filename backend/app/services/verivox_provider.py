"""Verivox-Integration — echte Strom-/Gas-/DSL-/Mobilfunk-Tarife.

Zwei offizielle Wege (kein Scraping, keine inoffiziellen APIs):

1. PARTNERPROGRAMM (empfohlen für den Start, kostenlos):
   - Registrierung: https://www.verivox.de/partnerprogramm/
     (bzw. über das Partnernetzwerk Awin: https://ui.awin.com/merchant-profile/14797)
   - Liefert: iframe-Whitelabel-Tarifrechner (Strom, Gas, DSL, Mobilfunk)
     + Tracking-Links + Vergütung (~55 €/Sale Strom&Gas)
   - Technisch: iframe-Einbindung mit Partner-ID — kein eigenes
     Backend-API-Handling nötig. Für die Webapp ideal: Tarifrechner
     direkt auf /versorger einbetten.

2. ENERGIE WEBSERVICE (B2B, kostenpflichtig — für Versorger):
   - https://www.verivox.de/marktdaten/energie/energiewebservice/
   - White-Label-Tarifrechner mit Echtzeit-Tarifdatenbank
   - Kündigung: 6 Wochen zum Quartalsende
   - Für uns erst relevant, wenn die App eigene Tarif-Listen mit
     Abschlussstrecke ohne iframe will.

Dieser Provider liefert KEINE Tarifdaten selbst (das macht der
iframe), sondern:
   a) die korrekten Partner-URLs für Strom/Gas/DSL/Mobilfunk je PLZ
   b) einen Platzhalter-Endpunkt, der klar sagt: Partner-ID fehlt
"""
from __future__ import annotations

import os
import structlog

log = structlog.get_logger(__name__)

# Partner-ID nach Registrierung im Verivox-Partnerprogramm (Awin PID)
# oder direkt über Verivox. Per ENV konfigurierbar:
#   VERIVOX_PARTNER_ID=IhreID
PARTNER_ID_ENV = "VERIVOX_PARTNER_ID"
CHECK24_PARTNER_ID_ENV = "CHECK24_PARTNER_ID"
IDEALO_PARTNER_ID_ENV = "IDEALO_PARTNER_ID"


def get_partner_id() -> str | None:
    """Verivox-Partner-ID (None = noch nicht registriert)."""
    """Partner-ID aus der Environment lesen (None = noch nicht registriert)."""
    pid = os.environ.get(PARTNER_ID_ENV, "").strip()
    return pid or None


def get_portal_partner_id(portal: str) -> str | None:
    """Partner-ID je Portal (None = nicht registriert, Link ohne Provision).

    Check24: https://www.check24.de/partner/ (eigenes Partnerprogramm,
    vergleichbare Vergütung wie Verivox)
    Idealo: Affiliate-Programm über Partnernetzwerke (u.a. Awin /
    TradeTracker) — Anmeldung über das jeweilige Netzwerk.
    """
    env_map = {
        "verivox": PARTNER_ID_ENV,
        "check24": CHECK24_PARTNER_ID_ENV,
        "idealo": IDEALO_PARTNER_ID_ENV,
    }
    var = env_map.get(portal.lower())
    if not var:
        return None
    val = os.environ.get(var, "").strip()
    return val or None


def build_tariff_calculator_url(
    product: str, postal_code: str, partner_id: str | None = None
) -> dict:
    """Deep-Link auf den offiziellen Verivox-Tarifrechner bauen.

    product: "strom" | "gas" | "dsl" | "mobilfunk"
    postal_code: 5-stellige PLZ des Mieters

    Returns dict mit url, status ("ok" | "no_partner_id"),
    produkt und plz. Ohne Partner-ID wird die öffentliche Verivox-
    Vergleichsseite verlinkt (funktioniert, aber ohne Provision).
    """
    pid = partner_id or get_partner_id()
    base_urls = {
        "strom": "https://www.verivox.de/strom/stromtarife/?plz={plz}",
        "gas": "https://www.verivox.de/gas/gastarife/?plz={plz}",
        "dsl": "https://www.verivox.de/dsl/",
        "mobilfunk": "https://www.verivox.de/mobilfunk/",
    }
    url = base_urls.get(product, base_urls["strom"]).format(plz=postal_code)

    if pid:
        # Mit Partner-ID: Tracking-Parameter (konkrete Form liefert Verivox
        # nach Registrierung — üblich ist ein subid/aff-Parameter)
        url = f"{url}&partner={pid}"
        return {"url": url, "status": "ok", "product": product, "plz": postal_code}

    return {
        "url": url,
        "status": "no_partner_id",
        "product": product,
        "plz": postal_code,
        "hint": (
            "Noch ohne Partner-ID — Registrierung unter "
            "https://www.verivox.de/partnerprogramm/ . "
            "Der Link funktioniert (öffentlicher Vergleich), Provision "
            "wird erst mit Partner-ID getrackt."
        ),
    }


IFRAME_URL_ENV = "VERIVOX_IFRAME_URL"


def get_iframe_url() -> str | None:
    """White-Label-iframe-URL aus dem Verivox-Partner-Dashboard (ENV).

    Nach der Freischaltung steht im Partner-Dashboard unter
    'Werbemittel' der iframe-HTML-Schnipsel — seine src-URL hier
    eintragen (backend/.env): VERIVOX_IFRAME_URL=https://...
    Damit rendert /versorger den kompletten Tarifrechner inkl.
    Abschlussstrecke direkt eingebettet.
    """
    url = os.environ.get(IFRAME_URL_ENV, "").strip()
    return url or None


__all__ = [
    "get_partner_id",
    "build_tariff_calculator_url",
    "get_iframe_url",
    "PARTNER_ID_ENV",
    "IFRAME_URL_ENV",
]
