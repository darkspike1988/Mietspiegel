"""Wohnungssuche-Agent — Multi-Portal-Suche ohne API-Keys.

Architektur nach dem bewährten Open-Source-Muster (Fredy, ImmoSpider,
ImmoPushr — alle Open Source auf GitHub):

  1. Der Nutzer konfiguriert Such-URLs der Portale (Copy aus der Portalsuche)
  2. Der Agent pollt periodisch — Provider-Interface:
       - RSS: offizielle Portal-Feeds (rechtssicher, bevorzugt)
       - HTML: Parsing der Ergebnisseite (best effort, Portale haben
         Anti-Bot-Schutz — AWS WAF bei ImmoScout24)
  3. Dedup: Jeder Treffer bekommt einen Hash (Stadt+Größe+Miete+Zimmer),
     bekannte Keys werden nicht erneut gemeldet
  4. USP: Jeder neue Treffer wird automatisch gegen den Mietspiegel
     geprüft → Badge fair/tolerance/brake/wucher direkt im Ergebnis
  5. Benachrichtigung: Telegram (Bot-Token via ENV) und/oder Webhook

Kein Scraping von Login-geschützten Bereichen, respektvolle Poll-Intervalle.
"""
from __future__ import annotations

import hashlib
import os
import re
import xml.etree.ElementTree as ET

import httpx
import structlog

from app.schemas.search_agent import (
    CheckedListing,
    Listing,
    Provider,
    RentBadge,
    SearchProfile,
)
from app.services import rent_engine

log = structlog.get_logger(__name__)

POLL_INTERVAL_SECONDS = 300

_seen: dict[str, dict[str, str]] = {}  # Fallback ohne Profil-ID (Legacy)

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) MietspiegelAI-SearchAgent/0.1 "
    "(open-source personal search agent; "
    "+https://github.com/darkspike1988/Mietspiegel)"
)

PRICE_RE = re.compile(r"(\d{2,5})(?:[.,](?:--|00))?\s*(?:€|EUR)", re.IGNORECASE)
SQM_RE = re.compile(r"(\d{1,4}(?:[.,]\d+)?)\s*m(?:²|²|2)", re.IGNORECASE)
ROOMS_RE = re.compile(r"(\d(?:[.,]\d)?)\s*-?\s*(?:Zi|Zimmer)", re.IGNORECASE)
PLZ_RE = re.compile(r"\b(\d{5})\b")


def _parse_price(text: str) -> float | None:
    m = PRICE_RE.search(text)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def _parse_sqm(text: str) -> float | None:
    m = SQM_RE.search(text)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", "."))
    except ValueError:
        return None


def _parse_rooms(text: str) -> float | None:
    m = ROOMS_RE.search(text)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", "."))
    except ValueError:
        return None


def _parse_plz(text: str) -> str | None:
    m = PLZ_RE.search(text)
    return m.group(1) if m else None


# ----------------------------------------------------------------------
# RSS-Provider (rechtssicher: offizielle Feeds)
# ----------------------------------------------------------------------

def _parse_rss(xml_text: str, source: str) -> list[Listing]:
    """RSS 2.0 / Atom parsen → Listings (best effort, titelbasiert)."""
    listings: list[Listing] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        log.warning("rss_parse_failed", error=str(e))
        return listings

    ns = {"atom": "http://www.w3.org/2005/Atom"}
    items = root.findall(".//item") + root.findall(".//atom:entry", ns)
    for item in items:
        title_el = item.find("title")
        if title_el is None:
            title_el = item.find("atom:title", ns)
        link_el = item.find("link")
        if link_el is None:
            link_el = item.find("atom:link", ns)
        title = (title_el.text or "").strip() if title_el is not None else ""
        link = None
        if link_el is not None:
            link = link_el.text or link_el.get("href")
        if not title:
            continue
        listings.append(Listing(
            id=hashlib.sha256((link or title).encode()).hexdigest()[:16],
            title=title,
            url=link,
            city=_city_from_text(title),
            postal_code=_parse_plz(title),
            cold_rent_eur=_parse_price(title),
            size_sqm=_parse_sqm(title),
            rooms=_parse_rooms(title),
            source=source,
        ))
    return listings


def _city_from_text(text: str) -> str | None:
    """Stadt aus Titel/URL ableiten (fuzzy, best effort)."""
    db = rent_engine.get_database()
    lowered = (text or "").lower()
    for ds in db.all():
        if ds.name.lower() in lowered or ds.slug in lowered:
            return ds.name
    return None


# ----------------------------------------------------------------------
# HTML-Provider (best effort)
# ----------------------------------------------------------------------

def _parse_html(html_text: str, source: str) -> list[Listing]:
    """Sehr einfaches best-effort-Parsing: <a>-Titel mit Mietdaten.

    Bewusst minimal gehalten — Portale ändern ihr Markup ständig und
    haben Anti-Bot-Schutz. Für echte Nutzung RSS bevorzugen.
    """
    listings: list[Listing] = []
    for m in re.finditer(r"<a[^>]*>([^<]{10,200})</a>", html_text):
        title = m.group(1).strip()
        if not (PRICE_RE.search(title) or SQM_RE.search(title)):
            continue
        listings.append(Listing(
            id=hashlib.sha256(title.encode()).hexdigest()[:16],
            title=title,
            city=_city_from_text(title),
            postal_code=_parse_plz(title),
            cold_rent_eur=_parse_price(title),
            size_sqm=_parse_sqm(title),
            rooms=_parse_rooms(title),
            source=source,
        ))
    return listings


# ----------------------------------------------------------------------
# Core: fetch + dedup + rent-check
# ----------------------------------------------------------------------

async def fetch_profile(profile: SearchProfile) -> list[Listing]:
    """Such-URL abfragen und Treffer parsen — Provider-Dispatch.

    ImmoScout-URLs gehen immer über die Mobile API (unabhängig vom
    konfigurierten provider), da die Website Anti-Bot-geschützt ist
    und die Mobile API die rechtssichere, ungeschützte Route ist.
    """
    url = str(profile.url)
    if "immobilienscout24" in url:
        from app.services import immoscout_provider
        return await immoscout_provider.fetch_listings(url)
    if "kleinanzeigen" in url:
        from app.services import kleinanzeigen_provider
        return await kleinanzeigen_provider.fetch_listings(url)
    if "wg-gesucht" in url:
        from app.services import wggesucht_provider
        return await wggesucht_provider.fetch_listings(url)
    if "immowelt" in url:
        from app.services import immowelt_provider
        return await immowelt_provider.fetch_listings(url)

    headers = {"User-Agent": USER_AGENT}
    async with httpx.AsyncClient(
        headers=headers,
        timeout=httpx.Timeout(20.0),
        follow_redirects=True,
    ) as client:
        resp = await client.get(url)
        resp.raise_for_status()
    text = resp.text
    source = _source_from_url(url)
    if profile.provider == Provider.RSS:
        return _parse_rss(text, source)
    return _parse_html(text, source)


def _source_from_url(url: str) -> str:
    for name, frag in [
        ("immoscout24", "immobilienscout24"),
        ("immowelt", "immowelt"),
        ("immonet", "immonet"),
        ("kleinanzeigen", "kleinanzeigen"),
        ("wg-gesucht", "wg-gesucht"),
    ]:
        if frag in url:
            return name
    return "unknown"


def _check_listing(listing: Listing) -> CheckedListing:
    """Mietpreis-Check gegen die bestehende Engine (USP!)."""
    match = rent_engine.resolve_city(
        listing.title or "", listing.postal_code
    )
    if (
        match is None
        or listing.cold_rent_eur is None
        or listing.size_sqm is None
        or listing.size_sqm <= 0
    ):
        return CheckedListing(**listing.model_dump())

    ds = match.dataset
    fair = rent_engine.compute_fair_rent(
        city_avg_eur_per_sqm=ds.average_eur_per_sqm,
        size_sqm=listing.size_sqm,
        build_year=None,
        location_quality=None,
    )
    if fair <= 0:
        return CheckedListing(**listing.model_dump())
    delta = round(
        (listing.cold_rent_eur - fair) / fair * 100, 2
    )
    verdict = rent_engine.classify_verdict(delta)
    return CheckedListing(
        **listing.model_dump(),
        rent_badge=RentBadge(verdict.value),
        fair_rent_eur=fair,
        delta_percent=delta,
    )


async def refresh_profile(
    profile: SearchProfile, profile_id: int | None = None
) -> tuple[list[CheckedListing], int]:
    """Profil abfragen, neue Treffer identifizieren + prüfen.

    Mit profile_id wird der Dedup-Status in SQLite persistiert
    (überlebt Restarts); ohne fällt der In-Memory-Store zurück.

    Returns (neue geprüfte Treffer, Gesamtzahl gesehener Treffer).
    """
    from app.services import storage

    try:
        listings = await fetch_profile(profile)
    except (httpx.HTTPError, httpx.InvalidURL) as e:
        log.warning("fetch_failed", profile=profile.name, error=str(e))
        if profile_id is not None:
            return [], storage.count_listings(profile_id)
        return [], len(_seen.get(profile.name, {}))

    if profile_id is not None:
        fresh: list[CheckedListing] = []
        for listing in listings:
            key = listing.dedup_key()
            if storage.is_seen(profile_id, key):
                continue
            checked = _check_listing(listing)
            storage.save_seen_listing(
                profile_id, key, checked.model_dump(mode="json")
            )
            fresh.append(checked)
        return fresh, storage.count_listings(profile_id)

    # Legacy-Pfad ohne Persistenz
    seen = _seen.setdefault(profile.name, {})
    fresh_mem: list[CheckedListing] = []
    for listing in listings:
        key = listing.dedup_key()
        if key in seen:
            continue
        seen[key] = listing.first_seen_at
        fresh_mem.append(_check_listing(listing))
    return fresh_mem, len(seen)


# ----------------------------------------------------------------------
# Benachrichtigung
# ----------------------------------------------------------------------

async def notify_telegram(listings: list[CheckedListing]) -> bool:
    """Neue Treffer per Telegram senden (BOT_TOKEN + CHAT_ID via ENV)."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not (token and chat_id):
        log.info("telegram_not_configured")
        return False
    if not listings:
        return True

    badge_emoji = {
        RentBadge.FAIR: "✅",
        RentBadge.TOLERANCE: "🟡",
        RentBadge.BRAKE: "🟠",
        RentBadge.WUCHER: "🔴",
        RentBadge.UNKNOWN: "⚪",
    }
    lines = ["🏠 Neue Wohnungs-Angebote:\n"]
    for item in listings[:10]:
        badge = badge_emoji.get(item.rent_badge, "⚪")
        price = f"{item.cold_rent_eur:.0f} €" if item.cold_rent_eur else "?"
        sqm = f"{item.size_sqm:.0f} m²" if item.size_sqm else "?"
        delta = f" (+{item.delta_percent:.0f}% vs. Mietspiegel)" if item.delta_percent else ""
        lines.append(
            f"{badge} {item.title[:80]}\n   {price} · {sqm}{delta}"
            + (f"\n   {item.url}" if item.url else "")
        )
    payload = {"chat_id": chat_id, "text": "\n".join(lines)}
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json=payload,
        )
    if resp.status_code >= 400:
        log.warning("telegram_send_failed", status=resp.status_code)
        return False
    return True
