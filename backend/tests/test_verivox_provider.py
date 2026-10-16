"""Tests für den Verivox-Provider (URL-Builder, offline)."""
from __future__ import annotations

import pytest

from app.services import verivox_provider as vp


class TestVerivoxUrls:
    def test_strom_url_with_plz(self) -> None:
        result = vp.build_tariff_calculator_url("strom", "10117", partner_id="TEST123")
        assert result["status"] == "ok"
        assert "10117" in result["url"]
        assert "stromtarife" in result["url"]
        assert "TEST123" in result["url"]

    def test_gas_url(self) -> None:
        result = vp.build_tariff_calculator_url("gas", "80331", partner_id="X")
        assert "gastarife" in result["url"]
        assert "80331" in result["url"]

    def test_dsl_url(self) -> None:
        result = vp.build_tariff_calculator_url("dsl", "10117", partner_id="X")
        assert "dsl" in result["url"]

    def test_without_partner_id(self) -> None:
        result = vp.build_tariff_calculator_url("strom", "10117", partner_id=None)
        assert result["status"] == "no_partner_id"
        assert "partnerprogramm" in result["hint"]

    def test_unknown_product_falls_back_to_strom(self) -> None:
        result = vp.build_tariff_calculator_url("unbekannt", "10117", partner_id="X")
        assert "stromtarife" in result["url"]

    def test_partner_id_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(vp.PARTNER_ID_ENV, "ENV-42")
        assert vp.get_partner_id() == "ENV-42"
        result = vp.build_tariff_calculator_url("strom", "10117")
        assert "ENV-42" in result["url"]

    def test_empty_env_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(vp.PARTNER_ID_ENV, raising=False)
        assert vp.get_partner_id() is None
