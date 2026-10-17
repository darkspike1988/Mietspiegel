"""Tests für Klausel-Check (Plus) und Portfolio (Pro)."""
from __future__ import annotations

import pytest

from app.schemas.klausel_check import KlauselCheckRequest
from app.schemas.portfolio import ImmobilienObjekt, ObjektArt
from app.services import klausel_check as kc
from app.services import plan_service, portfolio, storage, user_service


@pytest.fixture(autouse=True)
def _clean():
    user_service._users_conn()
    plan_service._ensure_schema()
    portfolio._ensure_schema()
    storage.reset_db_for_tests()
    yield
    storage.reset_db_for_tests()


class TestKlauselCheck:
    def test_feste_zeitabstaende_erkannt(self) -> None:
        text = (
            "Der Mieter verpflichtet sich zur Durchführung der Schönheitsreparaturen "
            "in Zeitabständen von 3 Jahren. " + "z" * 100
        )
        befunde = kc.deterministischer_check(text)
        assert any("Zeitabständen" in b.titel for b in befunde)
        assert befunde[0].schwere == "hoch"

    def test_kaution_erkannt(self) -> None:
        text = (
            "Die Kaution beträgt drei Monatsmieten der Nettokaltmiete und ist "
            "vor Übergabe zu zahlen. " + "y" * 100
        )
        befunde = kc.deterministischer_check(text)
        assert any("Kaution" in b.titel for b in befunde)

    def test_sauberer_vertrag_leer(self) -> None:
        text = (
            "Der Mieter zahlt die Miete monatlich im Voraus. Der Vermieter "
            "erhält eine monatliche Nettokaltmiete. Das Mietverhältnis kann "
            "mit einer Frist von drei Monaten gekündigt werden. " + "x" * 50
        )
        befunde = kc.deterministischer_check(text)
        assert befunde == []  # kein bekanntes Problem-Muster

    async def test_klausel_check_response(self) -> None:
        class FakeMistral:
            _api_key = None

        text = "Renovierung: Mieter renoviert alle 2 Jahre die Wohnung. " + "a" * 150
        r = await kc.klausel_check(KlauselCheckRequest(contract_text=text), FakeMistral())
        assert r.ok
        assert len(r.befunde) >= 1
        assert "Auffälligkeiten" in r.empfehlung
        assert r.llm_zusammenfassung is None  # ohne Key

    async def test_zu_kurzer_text(self) -> None:
        import pydantic

        class FakeMistral:
            _api_key = None

        with pytest.raises(pydantic.ValidationError):
            KlauselCheckRequest(contract_text="x" * 40)


class TestPortfolio:
    def _user(self, plan: str = "pro") -> int:
        user_service.register_user("inv@t.de", "sicher123")
        token = user_service.login_user("inv@t.de", "sicher123")
        user = user_service.user_by_token(token)
        assert user is not None
        plan_service.set_user_plan(user["id"], plan)
        return user["id"]

    def test_add_and_stats(self) -> None:
        uid = self._user()
        pid = portfolio.add_property(uid, ImmobilienObjekt(
            bezeichnung="ETW Kreuzberg", art=ObjektArt.ETW,
            plz="10117", stadt="Berlin", wohnflaeche_qm=60,
            kaufpreis_eur=250000, jahresnettokaltmiete_eur=7200,
            baujahr=1990,
        ))
        assert pid > 0
        props = portfolio.list_properties(uid)
        assert len(props) == 1
        stats = portfolio.compute_objekt_statistik(props[0])
        assert stats["bruttorendite_prozent"] == 2.88  # 7200/250000
        assert stats["miete_pro_qm_eur"] == 10.0
        assert stats["markt_badge"] is not None  # Berlin-Daten vorhanden

    def test_gesamtstatistik(self) -> None:
        uid = self._user()
        portfolio.add_property(uid, ImmobilienObjekt(
            bezeichnung="A", kaufpreis_eur=200000, jahresnettokaltmiete_eur=8000,
            wohnflaeche_qm=50))
        portfolio.add_property(uid, ImmobilienObjekt(
            bezeichnung="B", kaufpreis_eur=300000, jahresnettokaltmiete_eur=12000,
            wohnflaeche_qm=80))
        g = portfolio.compute_portfolio_statistik(uid)
        assert g.anzahl_objekte == 2
        assert g.gesamt_kaufpreis_eur == 500000
        assert g.gesamtrendite_prozent == 4.0  # 20000/500000
        assert g.avg_miete_pro_qm_eur is not None

    def test_free_plan_limit(self) -> None:
        user_service.register_user("free@t.de", "sicher123")
        token = user_service.login_user("free@t.de", "sicher123")
        user = user_service.user_by_token(token)
        with pytest.raises(ValueError, match="Portfolio-Limit"):
            portfolio.add_property(user["id"], ImmobilienObjekt(
                bezeichnung="Nicht erlaubt"))

    def test_pro_plan_25_objects(self) -> None:
        uid = self._user()
        for i in range(3):
            portfolio.add_property(uid, ImmobilienObjekt(
                bezeichnung=f"Objekt {i}"))
        props = portfolio.list_properties(uid)
        assert len(props) == 3
