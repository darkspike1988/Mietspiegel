"""Tests für das 3-Stufen-Modell."""
from __future__ import annotations

import pytest

from app.services import plan_service, storage, user_service


@pytest.fixture(autouse=True)
def _clean():
    user_service._users_conn()
    plan_service._ensure_schema()
    storage.reset_db_for_tests()
    yield
    storage.reset_db_for_tests()


class TestPlans:
    def test_plan_structure(self) -> None:
        assert set(plan_service.PLANS) == {"free", "plus", "pro"}
        assert plan_service.PLANS["free"]["price_eur"] == 0.0
        assert plan_service.PLANS["plus"]["price_eur"] == 4.99
        assert plan_service.PLANS["pro"]["price_eur"] == 12.99

    def test_limits_escalate(self) -> None:
        assert plan_service.limit_for("free", "search_profiles") == 1
        assert plan_service.limit_for("plus", "search_profiles") == 10
        assert plan_service.limit_for("pro", "search_profiles") == 50
        # LLM nur ab Plus
        assert plan_service.limit_for("free", "llm_analyses_per_month") == 0
        assert plan_service.limit_for("pro", "llm_analyses_per_month") == 100
        # Portfolio nur Pro
        assert plan_service.limit_for("plus", "portfolio_properties") == 0
        assert plan_service.limit_for("pro", "portfolio_properties") == 25

    def test_unknown_plan_falls_back_to_free(self) -> None:
        assert plan_service.get_plan("xyz") == plan_service.PLANS["free"]


class TestPlanLifecycle:
    def test_default_is_free(self) -> None:
        r = user_service.register_user("p@t.de", "sicher123")
        token = user_service.login_user("p@t.de", "sicher123")
        user = user_service.user_by_token(token)
        assert plan_service.get_user_plan(user["id"]) == "free"

    def test_upgrade_and_limits(self) -> None:
        r = user_service.register_user("p2@t.de", "sicher123")
        token = user_service.login_user("p2@t.de", "sicher123")
        user = user_service.user_by_token(token)
        plan_service.set_user_plan(user["id"], "plus")
        assert plan_service.get_user_plan(user["id"]) == "plus"
        # Limit-Sprung greift
        allowed, _ = plan_service.check_profile_limit(user["id"])
        assert allowed  # Plus: 10 Profile, noch 0 angelegt

    def test_invalid_plan_rejected(self) -> None:
        r = user_service.register_user("p3@t.de", "sicher123")
        token = user_service.login_user("p3@t.de", "sicher123")
        user = user_service.user_by_token(token)
        with pytest.raises(ValueError, match="Unbekannt"):
            plan_service.set_user_plan(user["id"], "premium-plus")


class TestProfileGating:
    def test_free_limit_one_profile(self) -> None:
        r = user_service.register_user("p4@t.de", "sicher123")
        token = user_service.login_user("p4@t.de", "sicher123")
        user = user_service.user_by_token(token)
        # 1 Profil anlegen → ok
        conn = storage._get_conn()
        with storage._lock:
            conn.execute(
                "INSERT INTO search_profiles (name, url, provider, user_id, created_at) "
                "VALUES ('A', 'https://a.de', 'rss', ?, datetime('now'))",
                (user["id"],),
            )
            conn.commit()
        allowed, msg = plan_service.check_profile_limit(user["id"])
        assert not allowed
        assert "Free-Limit" in msg

    def test_free_zero_profiles_ok(self) -> None:
        r = user_service.register_user("p5@t.de", "sicher123")
        token = user_service.login_user("p5@t.de", "sicher123")
        user = user_service.user_by_token(token)
        allowed, _ = plan_service.check_profile_limit(user["id"])
        assert allowed
