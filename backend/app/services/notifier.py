"""Multi-Kanal-Benachrichtiger: Telegram, ntfy-Push, E-Mail.

Alle Kanäle kostenlos:
- Telegram: eigener Bot (TELEGRAM_BOT_TOKEN global)
- ntfy.sh: Gratis-Push aufs Handy (iOS/Android/Desktop), User wählt
  sich ein privates Topic — kein Anbieter-Zulassungsprozess
- E-Mail: SMTP (z.B. Hetzner-eigener Mailserver oder kostenloses
  Postfach), Env-konfiguriert

WhatsApp-Business-API wäre kostenpflichtig + Meta-Verifizierung —
bewusst nicht eingebaut; Telegram + ntfy decken Push vollständig ab.
"""
from __future__ import annotations

import asyncio
import os
import smtplib
from email.message import EmailMessage

import httpx
import structlog

log = structlog.get_logger(__name__)


async def send_telegram(chat_id: str, text: str, bot_token: str | None = None) -> bool:
    token = bot_token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not (token and chat_id):
        return False
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text[:3900]},
            )
        return resp.status_code < 400
    except httpx.HTTPError as e:
        log.warning("telegram_send_failed", error=str(e))
        return False


async def send_ntfy(topic: str, title: str, text: str) -> bool:
    """Gratis-Push via ntfy.sh — kein API-Key, kein App-Store-Zwang."""
    if not topic:
        return False
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"https://ntfy.sh/{topic}",
                content=text[:3800].encode("utf-8"),
                headers={"Title": title[:80], "Priority": "default"},
            )
        return resp.status_code < 400
    except httpx.HTTPError as e:
        log.warning("ntfy_send_failed", error=str(e))
        return False


def send_email_smtp(to_email: str, subject: str, body: str) -> bool:
    """SMTP-Versand (blocking — im Scheduler-Kontext via to_thread)."""
    host = os.environ.get("SMTP_HOST", "")
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER", "")
    password = os.environ.get("SMTP_PASSWORD", "")
    sender = os.environ.get("SMTP_FROM", user or "noreply@mietspiegel.local")
    if not (host and to_email):
        return False
    try:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = sender
        msg["To"] = to_email
        msg.set_content(body)
        with smtplib.SMTP(host, port, timeout=20) as server:
            server.starttls()
            if user:
                server.login(user, password)
            server.send_message(msg)
        return True
    except (OSError, smtplib.SMTPException) as e:
        log.warning("email_send_failed", error=str(e))
        return False


async def notify_user(recipient: dict, title: str, text: str) -> int:
    """Ein User über ALLE seine aktiven Kanäle benachrichtigen.

    Returns: Anzahl erfolgreicher Zustellungen.
    """
    delivered = 0
    if recipient.get("telegram_chat_id") and await send_telegram(
        recipient["telegram_chat_id"], f"{title}\n\n{text}"
    ):
        delivered += 1
    if recipient.get("ntfy_topic") and await send_ntfy(
        recipient["ntfy_topic"], title, text
    ):
        delivered += 1
    if recipient.get("notify_email") and recipient.get("email"):
        ok = await asyncio.to_thread(
            send_email_smtp, recipient["email"], title, text
        )
        delivered += 1 if ok else 0
    return delivered


async def notify_recipients(recipients: list[dict], title: str, text: str) -> int:
    """Alle Abonnenten einer Such-URL benachrichtigen (parallel)."""
    results = await asyncio.gather(
        *(notify_user(r, title, text) for r in recipients),
        return_exceptions=True,
    )
    total = sum(r for r in results if isinstance(r, int))
    return total
