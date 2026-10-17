"""OAuth-Login: Google (OIDC) + Apple (Sign-In).

Offizielle Specs, keine Reverse-Engineering:
- Google: OpenID Connect. authorize_url → code → token_endpoint →
  id_token (JWT) → userinfo daraus. Doku: developers.google.com/identity
- Apple: Sign in with Apple JS/Web. Besonderheit: Apple verlangt ein
  Client-Secret als ES256-JWT, das aus dem privaten .p8-Key erzeugt
  wird — wir generieren es dynamisch (gültig max. 180 Tage, hier 30 Min).

ENV (nach Registrierung der Apps in den Dev-Konsolen):
  GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET
  APPLE_CLIENT_ID (Services-ID) / APPLE_TEAM_ID / APPLE_KEY_ID /
  APPLE_PRIVATE_KEY (.p8-Inhalt) / APPLE_BUNDLE_ID (Web-Domain)
  OAUTH_REDIRECT_BASE (z.B. https://deine-domain.de)

Die Provider-Accounts werden per provider+subject an bestehende
E-Mail-Accounts gekoppelt (oder neu angelegt) — ein User kann also
E-Mail-Passwort UND Google UND Apple an einem Profil haben.
"""
from __future__ import annotations

import base64
import json
import os
import time

import httpx
import structlog

log = structlog.get_logger(__name__)

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
APPLE_AUTH_URL = "https://appleid.apple.com/auth/authorize"
APPLE_TOKEN_URL = "https://appleid.apple.com/auth/token"


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def redirect_uri(provider: str) -> str:
    base = _env("OAUTH_REDIRECT_BASE").rstrip("/") or "http://localhost:4321"
    return f"{base}/auth/callback/{provider}"


# ----------------------------------------------------------------------
# Google (OIDC)
# ----------------------------------------------------------------------

def google_authorize_url(state: str) -> str | None:
    client_id = _env("GOOGLE_CLIENT_ID")
    if not client_id:
        return None
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri("google"),
        "response_type": "code",
        "scope": "openid email",
        "state": state,
        "prompt": "select_account",
    }
    q = "&".join(f"{k}={httpx.QueryParams({k: v})}" for k, v in params.items())
    return f"{GOOGLE_AUTH_URL}?{q}"


async def google_exchange_code(code: str) -> dict | None:
    """code → id_token → E-Mail + subject. None bei Fehler."""
    client_id = _env("GOOGLE_CLIENT_ID")
    client_secret = _env("GOOGLE_CLIENT_SECRET")
    if not (client_id and client_secret):
        return None
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(GOOGLE_TOKEN_URL, data={
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri("google"),
            "grant_type": "authorization_code",
        })
    if resp.status_code != 200:
        log.warning("google_token_failed", status=resp.status_code)
        return None
    id_token = resp.json().get("id_token")
    if not id_token:
        return None
    return _decode_jwt_payload(id_token)


# ----------------------------------------------------------------------
# Apple (Sign in with Apple)
# ----------------------------------------------------------------------

def _apple_client_secret() -> str | None:
    """ES256-JWT als Client-Secret aus dem .p8-Key (dynamisch, 30 Min)."""
    team_id = _env("APPLE_TEAM_ID")
    key_id = _env("APPLE_KEY_ID")
    client_id = _env("APPLE_CLIENT_ID")
    private_key = _env("APPLE_PRIVATE_KEY")
    if not (team_id and key_id and client_id and private_key):
        return None
    try:
        import jwt  # PyJWT optional; fallback: ohne Apple läuft nicht
    except ImportError:
        log.warning("pyjwt_missing", hint="pip install pyjwt[crypto] für Apple-Login")
        return None

    now = int(time.time())
    headers = {"alg": "ES256", "kid": key_id}
    payload = {
        "iss": team_id,
        "iat": now,
        "exp": now + 1800,
        "aud": "https://appleid.apple.com",
        "sub": client_id,
    }
    return jwt.encode(payload, private_key, algorithm="ES256", headers=headers)


def apple_authorize_url(state: str) -> str | None:
    client_id = _env("APPLE_CLIENT_ID")
    if not client_id or not _apple_client_secret():
        return None
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri("apple"),
        "response_type": "code",
        "scope": "name email",
        "response_mode": "form_post",
        "state": state,
    }
    from urllib.parse import urlencode
    return f"{APPLE_AUTH_URL}?{urlencode(params)}"


async def apple_exchange_code(code: str) -> dict | None:
    secret = _apple_client_secret()
    client_id = _env("APPLE_CLIENT_ID")
    if not (secret and client_id):
        return None
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(APPLE_TOKEN_URL, data={
            "code": code,
            "client_id": client_id,
            "client_secret": secret,
            "redirect_uri": redirect_uri("apple"),
            "grant_type": "authorization_code",
        })
    if resp.status_code != 200:
        log.warning("apple_token_failed", status=resp.status_code)
        return None
    id_token = resp.json().get("id_token")
    if not id_token:
        return None
    return _decode_jwt_payload(id_token)


# ----------------------------------------------------------------------
# JWT-Decode (id_token trusted nach HTTPS-Token-Exchange — wir holen
# den Token direkt beim Provider, also reicht die Payload-Dekodierung
# ohne Signaturprüfung; die Integrität stellt der HTTPS-Kanal sicher.)
# ----------------------------------------------------------------------

def _decode_jwt_payload(token: str) -> dict | None:
    try:
        payload_b64 = token.split(".")[1]
        # Base64url auffüllen
        payload_b64 += "=" * (-len(payload_b64) % 4)
        return json.loads(base64.urlsafe_b64decode(payload_b64))
    except (IndexError, ValueError) as e:
        log.warning("jwt_decode_failed", error=str(e))
        return None


def extract_identity(payload: dict, provider: str) -> dict | None:
    """Aus dem id_token-Payload: E-Mail + stabile Provider-Subject-ID."""
    email = payload.get("email")
    subject = payload.get("sub")
    if not (email and subject):
        return None
    return {
        "provider": provider,
        "provider_sub": subject,
        "email": email.strip().lower(),
    }
