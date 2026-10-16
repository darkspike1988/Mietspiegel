"""Tests for the free tenant tools (tools_engine + schemas).

All tools are deterministic — no LLM, no API key needed.
"""
from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from app.schemas.tools import (
    KautionCheckRequest,
    MieterhoehungCheckRequest,
    NebenkostenCheckRequest,
    NKLineItem,
)
from app.services import tools_engine

# ----------------------------------------------------------------------
# Nebenkosten
# ----------------------------------------------------------------------

class TestNebenkosten:
    def test_valid_request_passes(self) -> None:
        req = NebenkostenCheckRequest(
            period_start=date(2024, 1, 1),
            period_end=date(2024, 12, 31),
            statement_date=date(2025, 6, 1),
            items=[
                NKLineItem(label="Heizung", amount_eur=1200),
                NKLineItem(label="Müll", amount_eur=300),
            ],
            total_advance_payments_eur=1300,
            invoiced_total_eur=1500,
            claimed_balance_eur=200,
        )
        resp = tools_engine.check_nebenkosten(req)
        assert resp.arithmetic_ok
        assert resp.deadline_ok
        assert resp.deadline_missed_by_days is None
        assert resp.items_total_eur == 1500
        assert resp.expected_balance_eur == 200
        assert resp.overpayment_eur == 0
        assert all(f.severity != "error" for f in resp.findings)

    def test_deadline_missed_is_error(self) -> None:
        req = NebenkostenCheckRequest(
            period_start=date(2024, 1, 1),
            period_end=date(2024, 12, 31),
            statement_date=date(2026, 2, 1),
            items=[],
            total_advance_payments_eur=1000,
            invoiced_total_eur=1000,
            claimed_balance_eur=0,
        )
        resp = tools_engine.check_nebenkosten(req)
        assert not resp.deadline_ok
        assert resp.deadline_missed_by_days is not None and resp.deadline_missed_by_days > 0
        assert any(f.code == "frist_versaeumt" for f in resp.findings)
        assert "gravierende Mängel" in resp.recommendation

    def test_deadline_edge_exact_one_year_ok(self) -> None:
        req = NebenkostenCheckRequest(
            period_start=date(2024, 1, 1),
            period_end=date(2024, 12, 31),
            statement_date=date(2025, 12, 28),
            items=[],
            total_advance_payments_eur=500,
            invoiced_total_eur=500,
            claimed_balance_eur=0,
        )
        resp = tools_engine.check_nebenkosten(req)
        assert resp.deadline_ok

    def test_non_allocatable_item_creates_error_and_overpayment(self) -> None:
        req = NebenkostenCheckRequest(
            period_start=date(2024, 1, 1),
            period_end=date(2024, 12, 31),
            statement_date=date(2025, 3, 1),
            items=[
                NKLineItem(
                    label="Treppenhausreparatur",
                    amount_eur=400,
                    not_allocation_capable=True,
                    reason="Reparatur",
                ),
                NKLineItem(label="Heizung", amount_eur=1000),
            ],
            total_advance_payments_eur=1000,
            invoiced_total_eur=1400,
            claimed_balance_eur=400,
        )
        resp = tools_engine.check_nebenkosten(req)
        assert resp.non_allocatable_eur == 400
        assert resp.expected_balance_eur == 0
        assert resp.overpayment_eur == 400
        assert any(f.code == "nicht_umlagefaehig" for f in resp.findings)

    def test_missing_statement_date_is_warn(self) -> None:
        req = NebenkostenCheckRequest(
            period_start=date(2024, 1, 1),
            period_end=date(2024, 12, 31),
            items=[],
            total_advance_payments_eur=500,
            invoiced_total_eur=500,
            claimed_balance_eur=0,
        )
        resp = tools_engine.check_nebenkosten(req)
        assert resp.deadline_ok
        assert any(f.code == "datum_fehlt" for f in resp.findings)

    def test_arithmetic_mismatch_is_warn(self) -> None:
        req = NebenkostenCheckRequest(
            period_start=date(2024, 1, 1),
            period_end=date(2024, 12, 31),
            statement_date=date(2025, 3, 1),
            items=[NKLineItem(label="Wasser", amount_eur=400)],
            total_advance_payments_eur=500,
            invoiced_total_eur=700,
            claimed_balance_eur=200,
        )
        resp = tools_engine.check_nebenkosten(req)
        assert not resp.arithmetic_ok
        assert any(f.code == "summenabweichung" for f in resp.findings)

    def test_no_contract_allocation_is_error(self) -> None:
        req = NebenkostenCheckRequest(
            period_start=date(2024, 1, 1),
            period_end=date(2024, 12, 31),
            statement_date=date(2025, 3, 1),
            items=[],
            total_advance_payments_eur=500,
            invoiced_total_eur=500,
            claimed_balance_eur=0,
            contract_allows_allocation=False,
        )
        resp = tools_engine.check_nebenkosten(req)
        assert any(f.code == "keine_umlagevereinbarung" for f in resp.findings)

    def test_period_validation_rejects_reverse(self) -> None:
        with pytest.raises(ValidationError):
            NebenkostenCheckRequest(
                period_start=date(2024, 12, 31),
                period_end=date(2024, 1, 1),
                total_advance_payments_eur=0,
                invoiced_total_eur=0,
                claimed_balance_eur=0,
            )

    def test_statement_before_period_end_rejected(self) -> None:
        with pytest.raises(ValidationError):
            NebenkostenCheckRequest(
                period_start=date(2024, 1, 1),
                period_end=date(2024, 12, 31),
                statement_date=date(2024, 6, 1),
                total_advance_payments_eur=0,
                invoiced_total_eur=0,
                claimed_balance_eur=0,
            )


# ----------------------------------------------------------------------
# Kaution
# ----------------------------------------------------------------------

class TestKaution:
    def test_exact_three_months_is_ok(self) -> None:
        resp = tools_engine.check_kaution(
            KautionCheckRequest(cold_rent_eur=800, deposit_eur=2400)
        )
        assert resp.excess_eur == 0
        assert resp.deposit_ratio == 3
        assert resp.legal_maximum_eur == 2400

    def test_excess_is_reported(self) -> None:
        resp = tools_engine.check_kaution(
            KautionCheckRequest(cold_rent_eur=800, deposit_eur=3000)
        )
        assert resp.excess_eur == 600
        assert any(f.code == "kaution_zu_hoch" for f in resp.findings)
        assert "600.00 EUR" in resp.recommendation

    def test_interest_claim_positive(self) -> None:
        resp = tools_engine.check_kaution(
            KautionCheckRequest(cold_rent_eur=800, deposit_eur=2400)
        )
        assert resp.interest_claim_eur == 12

    def test_installments_hint_present(self) -> None:
        resp = tools_engine.check_kaution(
            KautionCheckRequest(cold_rent_eur=800, deposit_eur=2400)
        )
        assert any(f.code == "ratenzahlung" for f in resp.findings)


# ----------------------------------------------------------------------
# Mieterhöhung
# ----------------------------------------------------------------------

class TestMieterhoehung:
    def test_within_cap(self) -> None:
        resp = tools_engine.check_mieterhoehung(
            MieterhoehungCheckRequest(
                current_cold_rent_eur=1000,
                proposed_cold_rent_eur=1100,
                last_increase_date=date(2024, 1, 15),
                today=date(2025, 6, 15),
            )
        )
        assert resp.increase_percent == 10
        assert resp.within_15_percent_cap
        assert resp.sperrfrist_ok

    def test_cap_violation(self) -> None:
        resp = tools_engine.check_mieterhoehung(
            MieterhoehungCheckRequest(
                current_cold_rent_eur=1000,
                proposed_cold_rent_eur=1200,
                today=date(2025, 6, 15),
            )
        )
        assert not resp.within_15_percent_cap
        assert any(f.code == "kappung_verletzt" for f in resp.findings)

    def test_cap_edge_exactly_15(self) -> None:
        resp = tools_engine.check_mieterhoehung(
            MieterhoehungCheckRequest(
                current_cold_rent_eur=1000,
                proposed_cold_rent_eur=1150,
                today=date(2025, 6, 15),
            )
        )
        assert resp.within_15_percent_cap

    def test_sperrfrist_violation(self) -> None:
        resp = tools_engine.check_mieterhoehung(
            MieterhoehungCheckRequest(
                current_cold_rent_eur=1000,
                proposed_cold_rent_eur=1050,
                last_increase_date=date(2025, 3, 1),
                today=date(2025, 6, 15),
            )
        )
        assert not resp.sperrfrist_ok
        assert any(f.code == "sperrfrist" for f in resp.findings)

    def test_no_last_increase_is_info(self) -> None:
        resp = tools_engine.check_mieterhoehung(
            MieterhoehungCheckRequest(
                current_cold_rent_eur=1000,
                proposed_cold_rent_eur=1050,
            )
        )
        assert any(f.code == "sperrfrist_unbekannt" for f in resp.findings)

    def test_formal_notice_hint_always_present(self) -> None:
        resp = tools_engine.check_mieterhoehung(
            MieterhoehungCheckRequest(
                current_cold_rent_eur=1000,
                proposed_cold_rent_eur=1050,
            )
        )
        assert resp.formal_notice_hint
        assert any(f.code == "formvoraussetzung" for f in resp.findings)

    def test_decrease_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MieterhoehungCheckRequest(
                current_cold_rent_eur=1000,
                proposed_cold_rent_eur=900,
            )


class TestDeadlineEdgeCases:
    """Bugfix-Tests: §556-Abs.3-Frist bei Monatsenden (31./30./29.02.)."""

    def test_deadline_31_dec_is_31_dec_next_year(self) -> None:
        from app.services.tools_engine import _deadline
        assert _deadline(date(2024, 12, 31)) == date(2025, 12, 31)

    def test_deadline_30_jun_is_30_jun_next_year(self) -> None:
        from app.services.tools_engine import _deadline
        assert _deadline(date(2024, 6, 30)) == date(2025, 6, 30)

    def test_deadline_feb_29_leap_year(self) -> None:
        from app.services.tools_engine import _deadline
        assert _deadline(date(2024, 2, 29)) == date(2025, 2, 28)

    def test_statement_on_period_end_month_next_year_is_ok(self) -> None:
        req = NebenkostenCheckRequest(
            period_start=date(2024, 1, 1),
            period_end=date(2024, 10, 31),
            statement_date=date(2025, 10, 31),
            items=[],
            total_advance_payments_eur=500,
            invoiced_total_eur=500,
            claimed_balance_eur=0,
        )
        resp = tools_engine.check_nebenkosten(req)
        assert resp.deadline_ok
