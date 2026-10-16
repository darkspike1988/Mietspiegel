"""Tests für den SMARD-Provider (offline mit Fixture-Shape)."""
from __future__ import annotations

from app.services import smard_provider as sp


class TestSmardHelpers:
    def test_cheapest_hours(self) -> None:
        prices = [
            {"timestamp": "t1", "price_eur_per_mwh": 100.0, "price_ct_per_kwh": 10.0},
            {"timestamp": "t2", "price_eur_per_mwh": 50.0, "price_ct_per_kwh": 5.0},
            {"timestamp": "t3", "price_eur_per_mwh": 75.0, "price_ct_per_kwh": 7.5},
        ]
        cheapest = sp.cheapest_hours(prices, n=2)
        assert cheapest[0]["price_eur_per_mwh"] == 50.0
        assert cheapest[1]["price_eur_per_mwh"] == 75.0

    def test_most_expensive_hours(self) -> None:
        prices = [
            {"timestamp": "t1", "price_eur_per_mwh": 100.0, "price_ct_per_kwh": 10.0},
            {"timestamp": "t2", "price_eur_per_mwh": 50.0, "price_ct_per_kwh": 5.0},
        ]
        top = sp.most_expensive_hours(prices, n=1)
        assert top[0]["price_eur_per_mwh"] == 100.0

    def test_empty_input(self) -> None:
        assert sp.cheapest_hours([], n=3) == []
        assert sp.most_expensive_hours([], n=3) == []
