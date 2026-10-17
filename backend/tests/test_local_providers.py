"""Tests für lokale Versorger je PLZ."""
from __future__ import annotations

from app.services import local_providers as lp


class TestLocalProviders:
    def test_berlin_plz(self) -> None:
        r = lp.get_local_providers("10117")
        assert r.city == "berlin"
        assert r.grundversorger is not None
        assert "Vattenfall" in r.grundversorger.name
        assert "GASAG" in r.lokale_versorger

    def test_munich_prefix(self) -> None:
        r = lp.get_local_providers("81667")
        assert r.city == "münchen"
        assert "SWM" in r.grundversorger.name

    def test_unknown_plz_has_bnetza_fallback(self) -> None:
        r = lp.get_local_providers("99999")
        assert r.grundversorger is None
        assert "bundesnetzagentur" in r.bnetza_grundversorger_url
        assert "BNetzA" in r.hint

    def test_breitband_url_always(self) -> None:
        r = lp.get_local_providers("50667")
        assert "gigabitgrundbuch" in r.breitband_check_url
