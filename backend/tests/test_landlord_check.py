"""Tests für den Vermieter-Check."""
from __future__ import annotations

from app.services import landlord_check as lc


class TestLandlordDatabase:
    def test_vonovia_known(self) -> None:
        info = lc.find_landlord("Vonovia")
        assert info is not None
        assert info.name == "Vonovia SE"
        assert "550" in info.apartments
        assert "Bochum" in info.hq

    def test_deutsche_wohnen(self) -> None:
        info = lc.find_landlord("Deutsche Wohnen SE")
        assert info is not None
        assert "Vonovia" in info.name

    def test_berlin_landeseigene(self) -> None:
        for name in ("howoge", "gewobag", "degewo"):
            info = lc.find_landlord(name)
            assert info is not None
            assert "Berlin" in info.hq

    def test_unknown_returns_none(self) -> None:
        assert lc.find_landlord("Privat Müller") is None

    def test_empty_query(self) -> None:
        assert lc.find_landlord("") is None


class TestCheckLandlord:
    async def test_unknown_landlord_fallback(self) -> None:
        r = await lc.check_landlord("Privat Müller GmbH", "Leipzig")
        assert r.known is False
        assert "Handelsregister" in r.hint
        assert "unternehmensregister" in r.register_url
        assert "bundesanzeiger" in r.bundesanzeiger_url
