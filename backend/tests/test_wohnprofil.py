"""Tests für das kombinierte Wohnprofil-Modell."""
from __future__ import annotations

from app.schemas.tariff_stats import MarketStats
from app.schemas.wohnprofil import WohnprofilRequest
from app.services import wohnprofil as wp


def _req(**kw) -> WohnprofilRequest:
    base = dict(cold_rent_eur=500, nebenkosten_eur=150, strom_eur=50,
                net_income_eur=2500, delta_percent=None, market_stats=None)
    base.update(kw)
    return WohnprofilRequest(**base)


class TestDimensions:
    def test_mietspiegel_fair(self) -> None:
        d = wp.score_mietspiegel_dimension(5.0)
        assert d.points == 50.0

    def test_mietspiegel_wucher(self) -> None:
        d = wp.score_mietspiegel_dimension(80.0)
        assert d.points == 0.0
        assert "WiStrG" in d.detail

    def test_mietspiegel_unknown(self) -> None:
        d = wp.score_mietspiegel_dimension(None)
        assert d.points == 25.0

    def test_budget_comfortable(self) -> None:
        d = wp.score_budget_dimension(20.0)
        assert d.points == 30.0

    def test_budget_over(self) -> None:
        d = wp.score_budget_dimension(45.0)
        assert d.points == 0.0
        assert "Faustregel" in d.detail

    def test_markt_healthy(self) -> None:
        stats = MarketStats(total_listings=10, fair_count=8, overpriced_percent=20.0,
                            region="Berlin", insight="x")
        d = wp.score_markt_dimension(stats)
        assert d.points == 16.0

    def test_markt_empty(self) -> None:
        d = wp.score_markt_dimension(None)
        assert d.points == 10.0


class TestGesamtmodell:
    def test_perfect_wohnung(self) -> None:
        # fair + komfortables Budget + gesunder Markt → grün
        stats = MarketStats(total_listings=10, fair_count=8, overpriced_percent=20.0,
                            region="Berlin", insight="x")
        r = wp.compute_wohnprofil(_req(delta_percent=5.0, market_stats=stats))
        assert r.score >= 70
        assert r.ampel == "grün"
        assert len(r.dimensions) == 4

    def test_ueberteuerte_wohnung(self) -> None:
        # wucher + Budget über 30 % → rot
        r = wp.compute_wohnprofil(
            _req(cold_rent_eur=1200, net_income_eur=2000, delta_percent=80.0))
        assert r.score < 45
        assert r.ampel == "rot"

    def test_score_bounds(self) -> None:
        r = wp.compute_wohnprofil(_req())
        assert 0 <= r.score <= 100
        assert r.housing_cost_percent == 28.0  # 700/2500

    def test_no_blackbox(self) -> None:
        """Jede Dimension liefert eine Klartext-Begründung."""
        r = wp.compute_wohnprofil(_req(delta_percent=60.0))
        for dim in r.dimensions:
            assert dim.detail
            assert dim.label in ("Mietspiegel", "Budget", "Markt", "Strom")
