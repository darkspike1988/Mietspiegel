"""Telegram-Bot — die ganze App im Chat.

Der größte Reichweiten-Hebel: Deutsche Mieter-Communities leben in
Telegram. Der Bot beantwortet direkt im Chat:

  /check 850 60 10117        → Mietpreis-Check mit Badge + fairer Miete
  /kaution 850 2400           → Kaution-Check (§551 BGB)
  /mieterhoehung 850 1000     → Mieterhöhung-Check (Kappungsgrenze)
  /nk 2024-01-01 2024-12-31 1500 1500 0 → NK-Abrechnung (Frist prüfen)
  /wohnprofil 850 2500        → Budget-Check (30 %-Faustregel)
  /vermieter Vonovia          → Vermieter-Check (Konzern + News)
  /start, /hilfe              → Begrüßung + Übersicht

Betrieb: Long-Polling (getUpdates) als eigener asyncio-Task im
FastAPI-Lifespan — aktiviert sobald TELEGRAM_BOT_TOKEN gesetzt.
Fair-Use: 30-Sekunden-Poll-Intervall.
"""
from __future__ import annotations

import asyncio
import os

import httpx
import structlog

from app.schemas.search_agent import CheckedListing, Listing
from app.services import search_agent, tools_engine

log = structlog.get_logger(__name__)

API = "https://api.telegram.org"
POLL_TIMEOUT = 30
BADGE = {"fair": "✅", "tolerance": "🟡", "brake": "🟠", "wucher": "🔴", "unknown": "⚪"}

HELP_TEXT = (
    "🏠 *Mietspiegel-AI Bot* — kostenlos, mit Quellen.\n\n"
    "*Befehle:*\n"
    "• /check `<Kaltmiete> <m²> <PLZ>` — Ist die Miete fair?\n"
    "  z.B. `/check 850 60 10117`\n"
    "• /kaution `<Kaltmiete> <Kaution>` — Kaution-Check (§551)\n"
    "• /mieterhoehung `<aktuell> <neu>` — Kappungsgrenzen-Check\n"
    "• /wohnprofil `<Miete> <Einkommen>` — 30 %-Faustregel\n"
    "• /vermieter `<Name>` — Konzern-Daten + News\n"
    "• /nk `<von> <bis> <Vorauszahl.> <Summe> <Nachzahlung>` — NK-Frist\n\n"
    "Alle Checks deterministisch, Börsen-/Mietspiegel-Daten mit Quellenangabe. "
    "Web-App mit allen Tools: /app"
)


def _fmt_check_reply(checked: CheckedListing) -> str:
    b = BADGE.get(checked.rent_badge.value, "⚪")
    price = f"{checked.cold_rent_eur:.0f} €" if checked.cold_rent_eur else "?"
    sqm = f"{checked.size_sqm:.0f} m²" if checked.size_sqm else "?"
    lines = [
        f"{b} *Mietpreis-Check* ({price} · {sqm} · {checked.postal_code or '—'})",
        "",
    ]
    if checked.fair_rent_eur is not None:
        lines.append(f"Ortsübliche Miete: *{checked.fair_rent_eur:.0f} €*")
    if checked.delta_percent is not None:
        sign = "+" if checked.delta_percent > 0 else ""
        lines.append(f"Abweichung: *{sign}{checked.delta_percent:.1f} %*")
    if checked.overpayment_eur_per_year:
        lines.append(f"💸 Überzahlung: *{checked.overpayment_eur_per_year:.0f} €/Jahr*")
    if checked.legal_basis:
        lines.append(f"\n📄 {checked.legal_basis}")
    if checked.recommendation:
        lines.append(f"\n💡 {checked.recommendation}")
    if checked.rent_badge.value == "unknown":
        lines.append(
            "\n⚠️ Kein Mietspiegel-Datensatz für diese PLZ/Stadt — "
            "ergänze eine bekannte Stadt im Web-Check."
        )
    return "\n".join(lines)


async def handle_check(args: list[str]) -> str:
    """Mietpreis-Check im Chat."""
    if len(args) < 3:
        return "⚠️ Format: /check <Kaltmiete> <m²> <PLZ> — z.B. /check 850 60 10117"
    try:
        rent, sqm, plz = float(args[0].replace(",", ".")), float(args[1].replace(",", ".")), args[2]
    except ValueError:
        return "⚠️ Ungültige Zahlen. Beispiel: /check 850 60 10117"
    listing = Listing(
        id="tg", title=f"Wohnung {plz}", postal_code=plz,
        cold_rent_eur=rent, size_sqm=sqm, source="telegram",
    )
    checked = search_agent._check_listing(listing)
    return _fmt_check_reply(checked)


async def handle_kaution(args: list[str]) -> str:
    from app.schemas.tools import KautionCheckRequest

    if len(args) < 2:
        return "⚠️ Format: /kaution <Kaltmiete> <Kaution> — z.B. /kaution 850 2400"
    try:
        rent, deposit = float(args[0].replace(",", ".")), float(args[1].replace(",", "."))
    except ValueError:
        return "⚠️ Ungültige Zahlen."
    r = tools_engine.check_kaution(KautionCheckRequest(cold_rent_eur=rent, deposit_eur=deposit))
    status = "✅ im Rahmen" if r.excess_eur == 0 else f"🔴 {r.excess_eur:.0f} € überhöht (§551 BGB)"
    return (
        f"*Kaution-Check*\n\nMax. (3 Monatsmieten): *{r.legal_maximum_eur:.0f} €*\n"
        f"Deine Kaution: *{r.deposit_ratio} ×* Monatsmiete\n\n{status}\n\n"
        f"Zinsanspruch (pauschal/Jahr): ~{r.interest_claim_eur:.2f} € (§551 Abs. 3)"
    )


async def handle_mieterhoehung(args: list[str]) -> str:
    from datetime import date

    from app.schemas.tools import MieterhoehungCheckRequest

    if len(args) < 2:
        return "⚠️ Format: /mieterhoehung <aktuelle Miete> <geforderte Miete>"
    try:
        cur, new = float(args[0].replace(",", ".")), float(args[1].replace(",", "."))
    except ValueError:
        return "⚠️ Ungültige Zahlen."
    r = tools_engine.check_mieterhoehung(MieterhoehungCheckRequest(
        current_cold_rent_eur=cur, proposed_cold_rent_eur=new, today=date.today()))
    cap = "✅ Kappungsgrenze eingehalten" if r.within_15_percent_cap else (
        "🔴 Kappungsgrenze VERLETZT (max. 15 % in 3 Jahren, §558 BGB)"
    )
    return (
        f"*Mieterhöhung-Check*\n\n"
        f"Erhöhung: *+{r.increase_percent:.1f} %* (+{r.increase_eur:.0f} €/Monat)\n\n"
        f"{cap}\n\n"
        "💡 Der Vermieter muss schriftlich begründen (§558b BGB) — "
        "ohne Begründung musst du nicht zustimmen."
    )


async def handle_wohnprofil(args: list[str]) -> str:
    from app.schemas.wohnprofil import WohnprofilRequest
    from app.services.wohnprofil import compute_wohnprofil

    if len(args) < 2:
        return "⚠️ Format: /wohnprofil <Miete> <Nettoeinkommen> — z.B. /wohnprofil 850 2500"
    try:
        rent, income = float(args[0].replace(",", ".")), float(args[1].replace(",", "."))
    except ValueError:
        return "⚠️ Ungültige Zahlen."
    r = compute_wohnprofil(WohnprofilRequest(
        cold_rent_eur=rent, net_income_eur=income, strom_eur=60, nebenkosten_eur=150))
    amp = {"grün": "🟢", "gelb": "🟡", "rot": "🔴"}.get(r.ampel, "⚪")
    return (
        f"*Wohnprofil: {amp} {r.score}/100*\n\n"
        f"Wohnkosten: *{r.housing_cost_percent:.1f} %* des Einkommens "
        f"(Faustregel: max. 30 %)\n\n{r.empfehlung}"
    )


async def handle_vermieter(args: list[str]) -> str:
    from app.services import landlord_check

    name = " ".join(args).strip()
    if len(name) < 3:
        return "⚠️ Format: /vermieter <Name> — z.B. /vermieter Vonovia"
    r = await landlord_check.check_landlord(name)
    if not r.known or r.info is None:
        return (
            f"🔍 *Vermieter: {name}*\n\nKein bekannter Konzern — vermutlich "
            "privat/klein. Prüfe selbst:\n"
            f"• Unternehmensregister: {r.register_url}\n"
            f"• Bundesanzeiger: {r.bundesanzeiger_url}"
        )
    info = r.info
    news = "\n".join(
        f"• {n.title[:80]}" for n in r.news[:3]
    ) or "Keine aktuellen News gefunden."
    return (
        f"🏢 *{info.name}*\n"
        f"Sitz: {info.hq} · Bestand: {info.apartments} · Börse: {info.stock}\n\n"
        f"_{info.notes}_\n\n"
        f"📰 *News (12 Monate):*\n{news}\n\n"
        f"Register: {r.register_url}"
    )


async def handle_nk(args: list[str]) -> str:
    from datetime import date

    from app.schemas.tools import NebenkostenCheckRequest

    if len(args) < 5:
        return (
            "⚠️ Format: /nk <von> <bis> <Vorauszahlungen> <Summe> <Nachzahlung>\n"
            "z.B. /nk 2024-01-01 2024-12-31 1500 1500 0"
        )
    try:
        req = NebenkostenCheckRequest(
            period_start=date.fromisoformat(args[0]),
            period_end=date.fromisoformat(args[1]),
            total_advance_payments_eur=float(args[2].replace(",", ".")),
            invoiced_total_eur=float(args[3].replace(",", ".")),
            claimed_balance_eur=float(args[4].replace(",", ".")),
        )
    except (ValueError, IndexError):
        return "⚠️ Ungültige Eingabe — Datum im Format JJJJ-MM-TT."
    r = tools_engine.check_nebenkosten(req)
    frist = "✅ Frist eingehalten" if r.deadline_ok else \
            f"🔴 Frist VERPASST ({r.deadline_missed_by_days} Tage — Forderung verfallen, §556 Abs. 3 BGB)"
    return (
        f"*Nebenkosten-Check*\n\n{frist}\n"
        f"Erwarteter Saldo: *{r.expected_balance_eur:.2f} €*\n"
        f"Mögliche Rückforderung: *{r.overpayment_eur:.2f} €*\n\n💡 {r.recommendation}"
    )


# ----------------------------------------------------------------------
# Command-Dispatcher
# ----------------------------------------------------------------------

async def process_message(text: str) -> str | None:
    """Text → Antwort (None = kein Befehl)."""
    parts = text.strip().split()
    if not parts:
        return None
    cmd = parts[0].split("@")[0].lower()
    args = parts[1:]
    if cmd in ("/start", "/hilfe", "/help"):
        return HELP_TEXT
    if cmd == "/check":
        return await handle_check(args)
    if cmd == "/kaution":
        return await handle_kaution(args)
    if cmd == "/mieterhoehung":
        return await handle_mieterhoehung(args)
    if cmd in ("/wohnprofil", "/budget"):
        return await handle_wohnprofil(args)
    if cmd == "/vermieter":
        return await handle_vermieter(args)
    if cmd == "/nk":
        return await handle_nk(args)
    if cmd == "/app":
        return "🌐 Alle Tools, Ratgeber und Such-Agent: https://github.com/darkspike1988/Mietspiegel"
    return None


async def _send_message(bot_token: str, chat_id: str, text: str) -> None:
    async with httpx.AsyncClient(timeout=15.0) as client:
        await client.post(
            f"{API}/bot{bot_token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text[:4000],
                "parse_mode": "Markdown",
            },
        )


async def run_bot_forever() -> None:
    """Long-Polling-Loop — startet im FastAPI-Lifespan wenn Token gesetzt."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        log.info("telegram_bot_disabled", hint="TELEGRAM_BOT_TOKEN nicht gesetzt")
        return
    log.info("telegram_bot_started")
    offset = 0
    async with httpx.AsyncClient(timeout=POLL_TIMEOUT + 10) as client:
        while True:
            try:
                resp = await client.get(
                    f"{API}/bot{token}/getUpdates",
                    params={"timeout": POLL_TIMEOUT, "offset": offset},
                )
                updates = resp.json().get("result", [])
                for upd in updates:
                    offset = upd["update_id"] + 1
                    msg = upd.get("message") or upd.get("edited_message")
                    if not msg:
                        continue
                    chat_id = str(msg.get("chat", {}).get("id", ""))
                    text = msg.get("text", "")
                    answer = await process_message(text)
                    if answer:
                        await _send_message(token, chat_id, answer)
            except asyncio.CancelledError:
                log.info("telegram_bot_stopped")
                raise
            except (httpx.HTTPError, ValueError) as e:
                log.warning("telegram_bot_poll_failed", error=str(e))
                await asyncio.sleep(5)


_task: asyncio.Task | None = None


def start() -> None:
    """Bot starten (idempotent, nur wenn Token konfiguriert)."""
    global _task
    if (_task is None or _task.done()) and os.environ.get("TELEGRAM_BOT_TOKEN", "").strip():
        _task = asyncio.get_event_loop().create_task(run_bot_forever())


def stop() -> None:
    global _task
    if _task is not None:
        _task.cancel()
        _task = None
