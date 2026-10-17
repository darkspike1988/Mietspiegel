"""TOTP-Zweifaktor (RFC 6238) — kompatibel mit Google Authenticator,
Apple Passkeys-Code, Authy, 1Password & Co.

Stdlib-only (hmac/hashlib/base64) — keine Extra-Abhängigkeit.
Flow:
  1. setup_2fa() → Secret (base32) + otpauth-URL für QR-Code
  2. User scannt in Authenticator-App, bestätigt ersten Code
  3. verify_totp() prüft fortlaufend (±1 Zeitfenster, Drift-tolerant)
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time

PERIOD = 30
DIGITS = 6


def generate_secret() -> str:
    """Neues base32-Secret (20 Bytes Entropie)."""
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def otpauth_url(secret: str, email: str, issuer: str = "Mietspiegel-AI") -> str:
    """otpauth://-URL für QR-Code (Google Authenticator & Co.)."""
    from urllib.parse import quote
    return (
        f"otpauth://totp/{quote(issuer)}:{quote(email)}"
        f"?secret={secret}&issuer={quote(issuer)}"
        f"&algorithm=SHA1&digits={DIGITS}&period={PERIOD}"
    )


def _totp_at(secret_b32: str, counter: int) -> str:
    key = base64.b32decode(secret_b32 + "=" * (-len(secret_b32) % 4))
    msg = struct.pack(">Q", counter)
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(code % (10 ** DIGITS)).zfill(DIGITS)


def verify_totp(secret_b32: str, code: str, window: int = 1) -> bool:
    """Code prüfen (±window Zeitfenster für Uhr-Drift)."""
    if not (secret_b32 and code and code.isdigit() and len(code) == DIGITS):
        return False
    counter = int(time.time()) // PERIOD
    for drift in range(-window, window + 1):
        expected = _totp_at(secret_b32, counter + drift)
        if hmac.compare_digest(expected, code):
            return True
    return False
