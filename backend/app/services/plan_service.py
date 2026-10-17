"""3-Stufen-Modell: Free (kostenlos) · Plus · Pro.

Prinzipien:
- Free bleibt VOLL nutzbar (unser "kostenlos für Mieter"-Versprechen):
  alle Checks, Such-Agent, 1 Profil, Telegram-Bot, Ratgeber.
- Plus: mehr Automatisierung & Komfort (mehr Profile, mehr Kanäle,
  LLM-Auswertung des Mietvertrags-PDFs mit Klausel-Check).
- Pro: für Vielnutzer & Eigentümer (Immobilien-Portfolio: ETW/Haus,
  statistical Auswertung, LLM-Tieffanalyse, uneingeschränkt).

Feature-Gating via einfachem Plan-Feld am User. Bezahlung wird extern
abgewickelt (Patreon/PayPal-Links existieren schon; später Stripe).
Wir tracken nur: user.plan = free | plus | pro.
"""
from __future__ import annotations

from datetime import UTC, datetime

import structlog

from app.services import storage

log = structlog.get_logger(__name__)

PLANS = {
    "free": {
        "label": "Free",
        "price_eur": 0.0,
        "limits": {
            "search_profiles": 1,
            "notification_channels": 1,
            "llm_analyses_per_month": 0,
            "portfolio_properties": 0,
        },
        "features": [
            "Alle Checks (Miete, NK, Kaution, Mieterhöhung, Sparrechner)",
            "Such-Agent: 1 Profil, 10-Min-Polling",
            "Telegram-Bot komplett",
            "Ratgeber & Gesetze",
            "Mietpreis-Badges auf Treffern",
        ],
    },
    "plus": {
        "label": "Plus",
        "price_eur": 4.99,
        "limits": {
            "search_profiles": 10,
            "notification_channels": 3,  # Telegram + ntfy + E-Mail
            "llm_analyses_per_month": 10,
            "portfolio_properties": 0,
        },
        "features": [
            "Alles aus Free",
            "10 Such-Profile (mehrere Städte/Bezirke parallel)",
            "Alle 3 Benachrichtigungs-Kanäle",
            "🤖 LLM-Auswertung: Mietvertrag-PDF → Klausel-Check (10×/Monat)",
            "🤖 LLM-Widerspruchsbrief-Feinschliff",
            "Wohnprofil-Score mit Marktstatistik",
            "Fristen-Erinnerungen (NK, Mieterhöhung)",
        ],
    },
    "pro": {
        "label": "Pro",
        "price_eur": 12.99,
        "limits": {
            "search_profiles": 50,
            "notification_channels": 3,
            "llm_analyses_per_month": 100,
            "portfolio_properties": 25,
        },
        "features": [
            "Alles aus Plus",
            "50 Such-Profile + 5-Min-Polling",
            "🏠 Eigentums-Portfolio: ETW & Häuser verwalten",
            "📊 Statistische Auswertung des Portfolios",
            "  (Rendite, Vergleichsmiete je Objekt, Markt-Badges)",
            "🤖 LLM-Tiefenanalyse: 100×/Monat",
            "  (Kaufverträge, Mietverträge, Abrechnungen, Gutachten-Vorbereitung)",
            "Export (PDF/CSV) aller Reports",
            "Priority-Support",
        ],
    },
}


def get_plan(plan: str) -> dict:
    return PLANS.get(plan, PLANS["free"])


def limit_for(plan: str, key: str) -> int:
    return get_plan(plan).get("limits", {}).get(key, 0)


def set_user_plan(user_id: int, plan: str) -> None:
    """Plan ändern (nach externer Bezahlung, z.B. Patreon)."""
    if plan not in PLANS:
        raise ValueError(f"Unbekannter Plan: {plan}")
    conn = _ensure_schema()
    with storage._lock:
        conn.execute(
            "UPDATE users SET plan = ?, plan_since = ? WHERE id = ?",
            (plan, datetime.now(UTC).isoformat(), user_id),
        )
        conn.commit()


def get_user_plan(user_id: int) -> str:
    conn = _ensure_schema()
    row = conn.execute(
        "SELECT plan FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    return (row[0] if row else None) or "free"


def check_profile_limit(user_id: int) -> tuple[bool, str]:
    """Kann der User noch ein Suchprofil anlegen?"""
    plan = get_user_plan(user_id)
    limit = limit_for(plan, "search_profiles")
    conn = _ensure_schema()
    row = conn.execute(
        "SELECT COUNT(*) FROM search_profiles WHERE user_id = ? AND active = 1",
        (user_id,),
    ).fetchone()
    count = int(row[0] or 0) if row else 0
    if count >= limit:
        return False, (
            f"Free-Limit erreicht ({limit} Profil). "
            f"Plus (4,99 €/Mon.) erlaubt 10 Profile — Upgrade auf /login."
            if plan == "free" else
            f"Limit erreicht ({limit} Profile im {get_plan(plan)['label']}-Plan)."
        )
    return True, ""


def _ensure_schema():
    storage._get_conn()
    conn = storage._conn
    assert conn is not None
    import contextlib
    import sqlite3
    with contextlib.suppress(sqlite3.OperationalError):
        conn.execute("ALTER TABLE users ADD COLUMN plan TEXT DEFAULT 'free'")
    with contextlib.suppress(sqlite3.OperationalError):
        conn.execute("ALTER TABLE users ADD COLUMN plan_since TEXT")
    conn.commit()
    return conn
