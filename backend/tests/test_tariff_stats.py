"""Tests für die statistische Tarif-Auswertung."""
from __future__ import annotations

import pytest

from app.schemas.tariff_stats import Portal, TariffObservation
from app.services import tariff_stats as ts


@pytest.fixture(autouse=True)
def _clean():
    ts.reset_store()
    yield
    ts.reset_store()


class TestPortalRanking:
    def test_ranking_by_median(self) -> None:
        # Verivox: 3 Beobachtungen (Median 28), Check24: 2 (Median 30)
        for ct in (27, 28, 29):
            ts.add_observation(TariffObservation(
                portal=Portal.VERIVOX, product="strom", postal_code="10117",
                tariff_name=f"Tarif V{ct}", ct_per_kwh=ct,
            ))
        for ct in (28, 32):
            ts.add_observation(TariffObservation(
                portal=Portal.CHECK24, product="strom", postal_code="10117",
                tariff_name=f"Tarif C{ct}", ct_per_kwh=ct,
            ))
        r = ts.compute_portal_ranking("strom", "10117")
        assert r.observation_count == 5
        assert r.ranking[0].portal == "verivox"
        assert r.ranking[0].median_ct_kwh == 28.0
        assert r.ranking[1].portal == "check24"
        assert "verivox" in r.insight.lower()

    def test_empty_state_hint(self) -> None:
        r = ts.compute_portal_ranking("strom", "10117")
        assert r.observation_count == 0
        assert "keine Beobachtungen" in r.insight

    def test_min_tariff_named(self) -> None:
        ts.add_observation(TariffObservation(
            portal=Portal.IDEALO, product="strom", postal_code="10117",
            tariff_name="ÖkoBillig", ct_per_kwh=25,
        ))
        r = ts.compute_portal_ranking("strom", "10117")
        assert r.ranking[0].cheapest_tariff == "ÖkoBillig"
        assert r.ranking[0].min_ct_kwh == 25.0

    def test_plz_filter_isolated(self) -> None:
        ts.add_observation(TariffObservation(
            portal=Portal.VERIVOX, product="strom", postal_code="10117",
            tariff_name="A", ct_per_kwh=30,
        ))
        r = ts.compute_portal_ranking("strom", "80331")
        assert r.observation_count == 0


class TestSmardAggregation:
    def test_hour_and_weekday_aggregation(self) -> None:
        from datetime import datetime, timezone
        def mk(iso: str, ct: float) -> dict:
            return {"timestamp": iso, "price_eur_per_mwh": ct * 10, "price_ct_per_kwh": ct}
        # 3 Uhr: 8 ct (Montag), 18 Uhr: 20 ct (Montag)
        prices = [
            mk("2026-01-05T03:00:00+00:00", 8.0),
            mk("2026-01-05T18:00:00+00:00", 20.0),
            mk("2026-01-06T03:00:00+00:00", 9.0),
        ]
        r = ts.aggregate_smard_by_time(prices)
        assert r.observation_count == 3
        assert r.cheapest_hour["hour"] == 3
        assert r.most_expensive_hour["hour"] == 18
        assert "3:00" in r.insight
        # Wochentage: Mo (2 Werte), Di (1 Wert)
        wds = {w["weekday"]: w for w in r.by_weekday}
        assert wds["Mo"]["n"] == 2
        assert wds["Di"]["n"] == 1

    def test_empty_prices(self) -> None:
        r = ts.aggregate_smard_by_time([])
        assert r.observation_count == 0
        assert r.cheapest_hour is None
