"""Hintergrund-Scheduler: pollt aktive Suchprofile regelmäßig.

Läuft als asyncio-Task im FastAPI-Lifespan. Intervall respektvoll
(Standard 10 Min — Fair-Use gegenüber den Portalen).
"""
from __future__ import annotations

import asyncio
import os

import structlog

from app.schemas.search_agent import Provider, SearchProfile

log = structlog.get_logger(__name__)

_task: asyncio.Task | None = None

POLL_SECONDS = int(os.environ.get("SEARCH_POLL_SECONDS", "600"))


async def poll_all_profiles() -> int:
    """Alle aktiven Profile einmal abfragen. Returns: Anzahl neuer Treffer.

    Benachrichtigungs-Logik:
    - User-Profile: Kanäle des Users (Telegram/ntfy/E-Mail — notifier)
    - Anonyme Profile: globaler Telegram-Bot (Legacy-Verhalten)
    - URL-Sharing: gleiche URL wird nur einmal gepollt, Ergebnis
      würde an alle Abonnenten gehen (derzeit pollt jedes Profil einmal;
      bei >500 Profilen lohnt die URL-Dedup-Optimierung)
    """
    from app.services import notifier, search_agent, storage, user_service

    profiles = storage.list_profiles(active_only=True)
    total_new = 0
    for row in profiles:
        try:
            profile = SearchProfile(
                name=row["name"], url=row["url"],
                provider=Provider(row["provider"] or "rss"),
                city=row["city"],
            )
            fresh, _ = await search_agent.refresh_profile(profile, row["id"])
            total_new += len(fresh)
            if not fresh:
                continue
            # Wer will diesen Fund sehen?
            recipients = user_service.get_notification_recipients(row["url"])
            if recipients:
                badge_lines = []
                for item in fresh[:10]:
                    badge = {"fair": "✅", "tolerance": "🟡",
                             "brake": "🟠", "wucher": "🔴"}.get(
                        item.rent_badge.value, "⚪")
                    price = f"{item.cold_rent_eur:.0f} €" if item.cold_rent_eur else "?"
                    sqm = f"{item.size_sqm:.0f} m²" if item.size_sqm else "?"
                    delta = f" (+{item.delta_percent:.0f}%)" if item.delta_percent else ""
                    badge_lines.append(
                        f"{badge} {item.title[:60]} · {price} · {sqm}{delta}"
                    )
                text = "Neue Wohnungs-Angebote:\n" + "\n".join(badge_lines)
                await notifier.notify_recipients(
                    recipients, f"🏠 {row['name']}", text
                )
            else:
                # Anonymes Profil → Legacy: globaler Bot
                await search_agent.notify_telegram(fresh)
        except Exception as e:
            log.warning("scheduler_profile_failed", profile=row["name"], error=str(e))
    if profiles:
        log.info("scheduler_run", profiles=len(profiles), new_listings=total_new)
    return total_new


async def _loop() -> None:
    log.info("scheduler_started", interval_seconds=POLL_SECONDS)
    while True:
        try:
            await poll_all_profiles()
        except Exception as e:
            log.error("scheduler_crashed", error=str(e))
        await asyncio.sleep(POLL_SECONDS)


def start() -> None:
    """Scheduler starten (idempotent)."""
    global _task
    if _task is None or _task.done():
        _task = asyncio.get_event_loop().create_task(_loop())


def stop() -> None:
    """Scheduler stoppen."""
    global _task
    if _task is not None:
        _task.cancel()
        _task = None
