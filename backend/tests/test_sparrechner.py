"""Tests für den Versorger-Sparrechner."""
from __future__ import annotations

from app.schemas.tools import SparrechnerRequest
from app.services import tools_engine


class TestSparrechner:
    def test_typical_saving(self) -> None:
        # 4000 kWh, Grundversorger 40 ct, günstigster Tarif 28 ct
        r = tools_engine.compute_sparpotential(SparrechnerRequest(
            jahresverbrauch_kwh=4000,
            grundversorger_ct_kwh=40,
            guenstigster_tarif_ct_kwh=28,
        ))
        assert r.jahreskosten_grundversorger_eur == 1600.0
        assert r.jahreskosten_tarif_eur == 1120.0
        assert r.ersparnis_eur == 480.0
        assert r.ersparnis_prozent == 30.0
        assert "480" in r.empfehlung

    def test_with_grundgebuehr(self) -> None:
        r = tools_engine.compute_sparpotential(SparrechnerRequest(
            jahresverbrauch_kwh=3000,
            grundversorger_ct_kwh=35,
            guenstigster_tarif_ct_kwh=30,
            grundgebuehr_grundversorger_eur=120,
            grundgebuehr_tarif_eur=100,
        ))
        assert r.jahreskosten_grundversorger_eur == 1170.0
        assert r.jahreskosten_tarif_eur == 1000.0
        assert r.ersparnis_eur == 170.0

    def test_low_difference(self) -> None:
        r = tools_engine.compute_sparpotential(SparrechnerRequest(
            jahresverbrauch_kwh=2000,
            grundversorger_ct_kwh=30,
            guenstigster_tarif_ct_kwh=29,
        ))
        assert r.ersparnis_eur == 20.0
        assert "gering" in r.empfehlung.lower()
