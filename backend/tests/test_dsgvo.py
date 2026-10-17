"""Tests für die DSGVO-Rechte (Export Art. 20, Löschung Art. 17)."""
from __future__ import annotations

import pytest

from app.services import plan_service, portfolio, storage, user_service


@pytest.fixture(autouse=True)
def _clean():
    user_service._users_conn()
    plan_service._ensure_schema()
    portfolio._ensure_schema()
    storage.reset_db_for_tests()
    yield
    storage.reset_db_for_tests()


class TestDsgvo:
    async def test_export_and_delete(self) -> None:
        import asyncio

        from httpx import ASGITransport, AsyncClient
        from app.main import app

        user_service.register_user("dsgvo@t.de", "sicher123")
        token = user_service.login_user("dsgvo@t.de", "sicher123")
        user = user_service.user_by_token(token)
        plan_service.set_user_plan(user["id"], "plus")

        async with app.router.lifespan_context(app):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://t"
            ) as c:
                # Export vor Löschung
                r = await c.get(f"/api/dsgvo/export?api_token={token}")
                d = r.json()
                assert r.status_code == 200
                assert d["account"]["email"] == "dsgvo@t.de"
                assert "Art. 20" in d["hinweis"]
                # Löschung
                r2 = await c.delete(f"/api/dsgvo/loeschen?api_token={token}")
                d2 = r2.json()
                assert r2.status_code == 200
                assert "gelöscht" in d2["hinweis"]
                # Token ist danach ungültig
                r3 = await c.get(f"/api/dsgvo/export?api_token={token}")
                assert r3.status_code == 401

    def test_anonymization(self) -> None:
        user_service.register_user("x@y.de", "sicher123")
        token = user_service.login_user("x@y.de", "sicher123")
        user = user_service.user_by_token(token)
        assert user is not None
        conn = storage._get_conn()
        with storage._lock:
            conn.execute(
                "UPDATE users SET active = 0, email = 'geloescht+' || id || '@invalid', "
                "api_token = 'revoked-' || id WHERE id = ?", (user["id"],)
            )
            conn.commit()
        # Token nicht mehr gültig
        assert user_service.user_by_token(token) is None
