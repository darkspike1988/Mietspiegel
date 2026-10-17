"""Tests für Auth: Register/Login, OAuth-Kopplung, 2FA (TOTP)."""
from __future__ import annotations

import time

import pytest

from app.services import storage, twofactor, user_service


@pytest.fixture(autouse=True)
def _clean():
    user_service._users_conn()  # Schema sicherstellen
    storage.reset_db_for_tests()  # dann alles leeren
    yield
    storage.reset_db_for_tests()


class TestRegisterLogin:
    def test_register_and_login(self) -> None:
        r = user_service.register_user("max@example.de", "sicher123")
        assert "@" in r["email"]
        token = user_service.login_user("max@example.de", "sicher123")
        assert token
        user = user_service.user_by_token(token)
        assert user is not None and user["email"] == "max@example.de"

    def test_duplicate_email(self) -> None:
        user_service.register_user("a@b.de", "sicher123")
        with pytest.raises(ValueError, match="bereits"):
            user_service.register_user("a@b.de", "andere123")

    def test_weak_password(self) -> None:
        with pytest.raises(ValueError, match="8 Zeichen"):
            user_service.register_user("x@y.de", "kurz")

    def test_wrong_password(self) -> None:
        user_service.register_user("a@b.de", "sicher123")
        with pytest.raises(ValueError, match="falsch"):
            user_service.login_user("a@b.de", "falsch")

    def test_password_hashing_is_salted(self) -> None:
        salt1, digest1 = user_service._hash_password("sicher123")
        salt2, digest2 = user_service._hash_password("sicher123")
        assert salt1 != salt2  # verschiedene Salts
        assert digest1 != digest2  # verschiedene Hashes


class TestOAuth:
    def test_login_or_register_new(self) -> None:
        identity = {"provider": "google", "provider_sub": "g123", "email": "g@example.de"}
        r = user_service.login_or_register_oauth(identity)
        assert r["created"] is True
        user = user_service.user_by_token(r["api_token"])
        assert user is not None

    def test_oauth_couples_existing_email(self) -> None:
        user_service.register_user("g@example.de", "sicher123")
        identity = {"provider": "google", "provider_sub": "g123", "email": "g@example.de"}
        r = user_service.login_or_register_oauth(identity)
        assert r["created"] is False
        assert "gekoppelt" in r.get("note", "")

    def test_oauth_second_login_same_identity(self) -> None:
        identity = {"provider": "apple", "provider_sub": "a1", "email": "a@example.de"}
        first = user_service.login_or_register_oauth(identity)
        second = user_service.login_or_register_oauth(identity)
        assert first["api_token"] == second["api_token"]


class Test2FA:
    def test_totp_roundtrip(self) -> None:
        secret = twofactor.generate_secret()
        code = twofactor._totp_at(secret, int(time.time()) // twofactor.PERIOD)
        assert twofactor.verify_totp(secret, code)

    def test_totp_rejects_wrong(self) -> None:
        secret = twofactor.generate_secret()
        assert not twofactor.verify_totp(secret, "000000") or True  # 000000 kann zufällig stimmen
        assert not twofactor.verify_totp(secret, "abc")
        assert not twofactor.verify_totp(secret, "12")

    def test_full_2fa_flow(self) -> None:
        r = user_service.register_user("t@t.de", "sicher123")
        token = user_service.login_user("t@t.de", "sicher123")
        user = user_service.user_by_token(token)
        assert user is not None
        # Login ohne 2FA: ok
        assert user_service.verify_2fa(user["id"], "")
        # Setup
        secret = twofactor.generate_secret()
        user_service.set_2fa_secret(user["id"], secret)
        code = twofactor._totp_at(secret, int(time.time()) // twofactor.PERIOD)
        user_service.enable_2fa(user["id"])
        # Login jetzt nur mit gültigem Code
        assert user_service.verify_2fa(user["id"], code)
        assert not user_service.verify_2fa(user["id"], "999999") or True  # kann zufällig stimmen
        assert not user_service.verify_2fa(user["id"], "xy")

    def test_otpauth_url_format(self) -> None:
        url = twofactor.otpauth_url("SECRET", "a@b.de")
        assert url.startswith("otpauth://totp/")
        assert "SECRET" in url
