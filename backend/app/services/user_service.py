"""User-Accounts (MVP: E-Mail + Passwort, stdlib-krypto, keine Extra-Deps).

Für 1.000 User auf einem Hetzner-Server bewusst schlank:
- Passwort-Hashing via hashlib.pbkdf2_hmac (stdlib, sicher)
- E-Mail als unique Key, Verifikation per Token (MVP: direkt aktiv)
- API-Token je User für die Clients (statt Session-Overhead)

Benachrichtigungskanäle je User (alle optional, mehrere möglich):
- telegram_chat_id (gratis, via eigener Bot)
- ntfy_topic (gratis Push aufs Handy via ntfy.sh — keine App-Zulassung nötig)
- email (SMTP via ENV konfiguriert)
"""
from __future__ import annotations

import contextlib
import hashlib
import hmac
import secrets
import sqlite3
from datetime import UTC, datetime

import structlog

from app.services import storage

log = structlog.get_logger(__name__)

_PBKDF2_ITERATIONS = 260_000


def _hash_password(password: str, salt: bytes | None = None) -> tuple[bytes, bytes]:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ITERATIONS)
    return salt, digest


def verify_password(password: str, salt: bytes, expected: bytes) -> bool:
    _, digest = _hash_password(password, salt)
    return hmac.compare_digest(digest, expected)


def register_user(email: str, password: str) -> dict:
    """User anlegen. Wirft ValueError bei Duplikat/schwachem Passwort."""
    email = email.strip().lower()
    if "@" not in email or "." not in email.split("@")[-1]:
        raise ValueError("Ungültige E-Mail-Adresse")
    if len(password) < 8:
        raise ValueError("Passwort muss mindestens 8 Zeichen haben")
    salt, digest = _hash_password(password)
    token = secrets.token_urlsafe(24)
    try:
        conn = _users_conn()
        with storage._lock:
            conn.execute(
                "INSERT INTO users (email, password_salt, password_hash, api_token, "
                "created_at) VALUES (?, ?, ?, ?, ?)",
                (email, salt, digest, token,
                 datetime.now(UTC).isoformat()),
            )
            conn.commit()
    except sqlite3.IntegrityError as e:
        raise ValueError("E-Mail bereits registriert") from e
    return {"email": email, "api_token": token}


def login_user(email: str, password: str) -> str:
    """Login → API-Token. Wirft ValueError bei Fehlschlag."""
    email = email.strip().lower()
    conn = _users_conn()
    row = conn.execute(
        "SELECT id, password_salt, password_hash, api_token FROM users WHERE email = ?",
        (email,),
    ).fetchone()
    if row is None:
        raise ValueError("E-Mail oder Passwort falsch")
    user_id, salt, digest, token = row
    if not verify_password(password, salt, digest):
        raise ValueError("E-Mail oder Passwort falsch")
    return str(token)


def user_by_token(token: str) -> dict | None:
    conn = _users_conn()
    row = conn.execute(
        "SELECT id, email, telegram_chat_id, ntfy_topic, notify_email, active "
        "FROM users WHERE api_token = ? AND active = 1",
        (token,),
    ).fetchone()
    if row is None:
        return None
    return {
        "id": row[0], "email": row[1], "telegram_chat_id": row[2],
        "ntfy_topic": row[3], "notify_email": bool(row[4]), "active": bool(row[5]),
    }


def update_notification_settings(
    user_id: int,
    telegram_chat_id: str | None = None,
    ntfy_topic: str | None = None,
    notify_email: bool | None = None,
) -> None:
    conn = _users_conn()
    sets, params = [], []
    if telegram_chat_id is not None:
        sets.append("telegram_chat_id = ?")
        params.append(telegram_chat_id)
    if ntfy_topic is not None:
        sets.append("ntfy_topic = ?")
        params.append(ntfy_topic)
    if notify_email is not None:
        sets.append("notify_email = ?")
        params.append(1 if notify_email else 0)
    if not sets:
        return
    params.append(user_id)
    with storage._lock:
        conn.execute(f"UPDATE users SET {', '.join(sets)} WHERE id = ?", params)
        conn.commit()


def get_notification_recipients(url: str) -> list[dict]:
    """Alle User, die ein Profil mit dieser URL abonniert haben —
    mit ihren Benachrichtigungskanälen. Kernstück des Scale-Designs:
    eine URL wird einmal gepollt, Ergebnis an alle Abonnenten verteilt."""
    conn = _users_conn()
    rows = conn.execute(
        """
        SELECT DISTINCT u.id, u.email, u.telegram_chat_id, u.ntfy_topic, u.notify_email
        FROM users u
        JOIN search_profiles sp ON sp.user_id = u.id
        WHERE sp.url = ? AND u.active = 1 AND sp.active = 1
        """,
        (url,),
    ).fetchall()
    return [
        {"id": r[0], "email": r[1], "telegram_chat_id": r[2],
         "ntfy_topic": r[3], "notify_email": bool(r[4])}
        for r in rows
    ]


def _users_conn() -> sqlite3.Connection:
    """Schema sicherstellen (users, oauth_identities, profiles.user_id)."""
    storage._get_conn()
    conn = storage._conn
    assert conn is not None
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL UNIQUE,
            password_salt BLOB NOT NULL,
            password_hash BLOB NOT NULL,
            api_token TEXT NOT NULL UNIQUE,
            telegram_chat_id TEXT,
            ntfy_topic TEXT,
            notify_email INTEGER DEFAULT 1,
            active INTEGER DEFAULT 1,
            created_at TEXT NOT NULL,
            twofactor_secret TEXT,
            twofactor_enabled INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS oauth_identities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            provider TEXT NOT NULL,
            provider_sub TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE (provider, provider_sub)
        );
        """
    )
    # search_profiles.user_id (idempotent)
    with contextlib.suppress(sqlite3.OperationalError):
        conn.execute(
            "ALTER TABLE search_profiles ADD COLUMN user_id INTEGER REFERENCES users(id)"
        )
    conn.commit()
    return conn


# ----------------------------------------------------------------------
# OAuth-Kopplung (Google/Apple) + 2FA
# ----------------------------------------------------------------------

def login_or_register_oauth(identity: dict) -> dict:
    """Provider-Identity → User (anlegen falls neu, koppeln falls
    E-Mail schon registriert). Returns User-Dict mit api_token."""
    import secrets as _secrets

    conn = _users_conn()
    # 1. Bestehende OAuth-Kopplung?
    row = conn.execute(
        "SELECT u.id, u.api_token FROM oauth_identities oi "
        "JOIN users u ON u.id = oi.user_id "
        "WHERE oi.provider = ? AND oi.provider_sub = ?",
        (identity["provider"], identity["provider_sub"]),
    ).fetchone()
    if row:
        return {"id": row[0], "api_token": row[1], "created": False}

    email = identity["email"]
    token = _secrets.token_urlsafe(24)
    with storage._lock:
        # 2. E-Mail existiert → OAuth an bestehenden Account koppeln
        existing = conn.execute(
            "SELECT id FROM users WHERE email = ?", (email,)
        ).fetchone()
        if existing:
            conn.execute(
                "INSERT INTO oauth_identities (user_id, provider, provider_sub, created_at) "
                "VALUES (?, ?, ?, ?)",
                (existing[0], identity["provider"], identity["provider_sub"],
                 datetime.now(UTC).isoformat()),
            )
            conn.commit()
            return {"id": existing[0], "api_token": token, "created": False,
                    "note": "OAuth an bestehenden Account gekoppelt"}

        # 3. Neu registrieren (Zufalls-Passwort, Login nur via OAuth)
        pw = _secrets.token_urlsafe(24)
        salt, digest = _hash_password(pw)
        cur = conn.execute(
            "INSERT INTO users (email, password_salt, password_hash, api_token, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (email, salt, digest, token,
             datetime.now(UTC).isoformat()),
        )
        uid = int(cur.lastrowid or 0)
        conn.execute(
            "INSERT INTO oauth_identities (user_id, provider, provider_sub, created_at) "
            "VALUES (?, ?, ?, ?)",
            (uid, identity["provider"], identity["provider_sub"],
             datetime.now(UTC).isoformat()),
        )
        conn.commit()
        return {"id": uid, "api_token": token, "created": True}


def set_2fa_secret(user_id: int, secret: str) -> None:
    conn = _users_conn()
    with storage._lock:
        conn.execute(
            "UPDATE users SET twofactor_secret = ? WHERE id = ?",
            (secret, user_id),
        )
        conn.commit()


def enable_2fa(user_id: int) -> None:
    conn = _users_conn()
    with storage._lock:
        conn.execute(
            "UPDATE users SET twofactor_enabled = 1 WHERE id = ? AND twofactor_secret IS NOT NULL",
            (user_id,),
        )
        conn.commit()


def get_2fa_state(user_id: int) -> dict:
    conn = _users_conn()
    row = conn.execute(
        "SELECT twofactor_secret, twofactor_enabled FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()
    if not row:
        return {"enabled": False, "secret": None}
    return {"enabled": bool(row[1]), "secret": row[0]}


def verify_2fa(user_id: int, code: str) -> bool:
    """2FA-Code prüfen (True wenn 2FA nicht aktiviert → nicht nötig)."""
    from app.services.twofactor import verify_totp

    state = get_2fa_state(user_id)
    if not state["enabled"]:
        return True
    return verify_totp(state["secret"] or "", code)
