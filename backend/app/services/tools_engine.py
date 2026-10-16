"""Deterministic tenant-tool engines (no LLM, no cost).

Swiss-army-knife module for tenants:

  - check_nebenkosten : Betriebskostenabrechnung prüfen (BetrKV, §556 BGB)
  - check_kaution     : Kautionshöhe prüfen (§551 BGB)
  - check_mieterhoehung: Mieterhöhung prüfen (§558, §558b BGB Kappungsgrenze)

Every function takes a validated Pydantic request and returns a validated
Pydantic response. All numbers are computed deterministically — no LLM
hallucination risk, no API key required, free to use.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from app.schemas.tools import (
    KautionCheckRequest,
    KautionCheckResponse,
    Kautionsanbieter,
    KautionsAngebot,
    KautionsVergleichRequest,
    KautionsVergleichResponse,
    MieterhoehungCheckRequest,
    MieterhoehungCheckResponse,
    NebenkostenCheckRequest,
    NebenkostenCheckResponse,
    NKFinding,
    SparrechnerRequest,
    SparrechnerResponse,
)

STATEMENT_DEADLINE_DAYS = 365  # §556 Abs. 3 BGB: 12 Monate nach Periodenende


def _end_of_month(year: int, month: int) -> date:
    """Letzter Tag des Monats (korrekt für 28/29/30/31-Tage-Monate)."""
    if month == 12:
        return date(year, 12, 31)
    return date(year, month + 1, 1) - timedelta(days=1)


def _deadline(period_end: date) -> date:
    """§556 Abs. 3 BGB-Frist: 12 Monate nach Periodenende.

    period_end + 1 Jahr, geklemmt auf das Monatsende (31.12. -> 31.12.,
    30.06. -> 30.06., 29.02. korrekt im Schaltjahr).
    """
    try:
        return period_end.replace(year=period_end.year + 1)
    except ValueError:  # 29.02. in Nicht-Schaltjahr
        return _end_of_month(period_end.year + 1, 2)
SPERRFRIST_MONTHS = 15  # §558 Abs. 1 BGB
KAPPUNG_PERCENT = 15.0  # §558 Abs. 3 BGB
DEPOSIT_MAX_MONTHS = 3  # §551 Abs. 1 BGB


def _months_between(start: date, end: date) -> int:
    return (end.year - start.year) * 12 + (end.month - start.month)


# ----------------------------------------------------------------------
# Nebenkostenabrechnung
# ----------------------------------------------------------------------

def check_nebenkosten(req: NebenkostenCheckRequest) -> NebenkostenCheckResponse:
    findings: list[NKFinding] = []

    # --- 1. Umlagefähigkeit (vertragliche Grundlage) ---
    if not req.contract_allows_allocation:
        findings.append(NKFinding(
            severity="error",
            code="keine_umlagevereinbarung",
            message=(
                "Der Mietvertrag enthält keine Umlagevereinbarung für "  # noqa: E501 (de)
                "Nebenkosten — die Abrechnung ist unzulässig (§556 Abs. 1 BGB)."
            ),
            legal_basis="§556 Abs. 1 BGB",
        ))

    # --- 2. 12-Monats-Frist (§556 Abs. 3 BGB) ---
    deadline = _deadline(req.period_end)
    deadline_ok = True
    deadline_missed_by_days: int | None = None
    if req.statement_date is not None:
        if req.statement_date > deadline:
            deadline_ok = False
            deadline_missed_by_days = (req.statement_date - deadline).days
            findings.append(NKFinding(
                severity="error",
                code="frist_versaeumt",
                message=(
                    f"Die Abrechnung ist {deadline_missed_by_days} Tage zu spät "
                    f"zugegangen (Spätester Termin: {deadline.isoformat()}). "
                    "Die Forderung ist verfallen — Vorauszahlungen dürfen nur "
                    "noch unter Vorbehalt angepasst werden."
                ),
                legal_basis="§556 Abs. 3 BGB",
                amount_eur=req.claimed_balance_eur,
            ))
        else:
            findings.append(NKFinding(
                severity="info",
                code="frist_ok",
                message=(
                    f"Abrechnung rechtzeitig zugegangen (Frist endete am "
                    f"{deadline.isoformat()})."
                ),
                legal_basis="§556 Abs. 3 BGB",
            ))
    else:
        findings.append(NKFinding(
            severity="warn",
            code="datum_fehlt",
            message=(
                "Zugangsdatum der Abrechnung nicht angegeben — die 12-Monats-Frist "
                "(§556 Abs. 3 BGB) konnte nicht geprüft werden."
            ),
        ))

    # --- 3. Nicht umlagefähige Positionen (Anlage 3 zu §2 BetrKV) ---
    non_allocatable = sum(
        it.amount_eur for it in req.items if it.not_allocation_capable
    )
    for it in req.items:
        if it.not_allocation_capable:
            findings.append(NKFinding(
                severity="error",
                code="nicht_umlagefaehig",
                message=(
                    f"Position '{it.label}' ({it.amount_eur:.2f} EUR) ist "
                    "wahrscheinlich nicht umlagefähig"
                    + (f": {it.reason}" if it.reason else "")
                    + ". Prüfe Anlage 3 zu §2 BetrKV."
                ),
                legal_basis="Anlage 3 zu §2 BetrKV",
                amount_eur=it.amount_eur,
            ))

    # --- 4. Arithmetik: Einzelpositionen vs. Gesamtsumme ---
    items_total = round(sum(it.amount_eur for it in req.items), 2)
    arithmetic_ok = abs(items_total - req.invoiced_total_eur) < 0.01
    if req.items and not arithmetic_ok:
        findings.append(NKFinding(
            severity="warn",
            code="summenabweichung",
            message=(
                f"Die Einzelpositionen ergeben {items_total:.2f} EUR, die "
                f"Abrechnung weist aber {req.invoiced_total_eur:.2f} EUR aus "
                f"(Differenz: {abs(items_total - req.invoiced_total_eur):.2f} EUR). "
                "Bitte Belege anfordern (§259 BGB)."
            ),
            amount_eur=abs(items_total - req.invoiced_total_eur),
        ))

    # --- 5. Saldo / Rückforderung ---
    expected_balance = round(
        req.invoiced_total_eur - non_allocatable
        - req.total_advance_payments_eur,
        2,
    )
    overpayment = max(0.0, req.claimed_balance_eur - expected_balance)
    if overpayment > 0:
        findings.append(NKFinding(
            severity="warn",
            code="ueberzahlung",
            message=(
                f"Vermutlich zu viel gezahlt: ~{overpayment:.2f} EUR Rückforderung "
                "möglich (nach Abzug nicht-umlagefähiger Positionen)."
            ),
            amount_eur=overpayment,
        ))

    has_error = any(f.severity == "error" for f in findings)
    if has_error:
        recommendation = (
            "Die Abrechnung weist gravierende Mängel auf. Widerspruch schreiben "
            "und Belegeinsicht verlangen (Anlage 4 zu §2 BetrKV, §259 BGB). "
            "Zahlung unter Vorbehalt leisten."
        )
    elif any(f.severity == "warn" for f in findings):
        recommendation = (
            "Die Abrechnung ist formal möglich, enthält aber Warnungen. "
            "Belege anfordern und Positionen im Detail prüfen."
        )
    else:
        recommendation = (
            "Die Abrechnung wirkt formal in Ordnung. Einzelne Positionen "
            "solltest du trotzdem anhand der Belege nachrechnen."
        )

    return NebenkostenCheckResponse(
        items_total_eur=items_total,
        arithmetic_ok=arithmetic_ok,
        deadline_ok=deadline_ok,
        deadline_missed_by_days=deadline_missed_by_days,
        non_allocatable_eur=round(non_allocatable, 2),
        expected_balance_eur=expected_balance,
        overpayment_eur=round(overpayment, 2),
        findings=findings,
        recommendation=recommendation,
    )


# ----------------------------------------------------------------------
# Kaution (§551 BGB)
# ----------------------------------------------------------------------

def check_kaution(req: KautionCheckRequest) -> KautionCheckResponse:
    findings: list[NKFinding] = []
    legal_max = round(req.cold_rent_eur * DEPOSIT_MAX_MONTHS, 2)
    excess = round(max(0.0, req.deposit_eur - legal_max), 2)
    ratio = round(req.deposit_eur / req.cold_rent_eur, 2)

    if excess > 0:
        findings.append(NKFinding(
            severity="error",
            code="kaution_zu_hoch",
            message=(
                f"Die Kaution überschreitet das zulässige Maximum von "
                f"{legal_max:.2f} EUR (3 Monatsmieten) um {excess:.2f} EUR. "
                "Die Überhöhung ist zurückzufordern (§551 Abs. 1 BGB)."
            ),
            legal_basis="§551 Abs. 1 BGB",
            amount_eur=excess,
        ))
    else:
        findings.append(NKFinding(
            severity="info",
            code="kaution_ok",
            message=f"Kaution im Rahmen des gesetzlichen Maximums ({legal_max:.2f} EUR).",
            legal_basis="§551 Abs. 1 BGB",
        ))

    if req.installments_allowed:
        findings.append(NKFinding(
            severity="info",
            code="ratenzahlung",
            message=(
                "Kaution darf in 3 gleichen Monatsraten gezahlt werden "
                "(§551 Abs. 2 BGB). Eine Zahlung 'in einer Summe bei Einzug' "
                "kann widersprochen werden."
            ),
            legal_basis="§551 Abs. 2 BGB",
        ))

    # Zinsanspruch: übliche Anlage auf einem Drei-Monats-Geldmarkt-Konto.
    # Konservativer pauschaler Ansatz ~0,5 % p.a. auf die Kaution.
    interest_claim = round(req.deposit_eur * 0.005, 2)
    findings.append(NKFinding(
        severity="info",
        code="zinsanspruch",
        message=(
            f"Die Kaution muss verzinst angelegt werden (§551 Abs. 3 BGB). "
            f"Pauschaler Zinsanspruch nach einem Jahr: ~{interest_claim:.2f} EUR. "
            "Realer Anspruch hängt vom konkreten Anlagekonto ab — Beleg verlangen."
        ),
        legal_basis="§551 Abs. 3 BGB, §247 BGB",
        amount_eur=interest_claim,
    ))

    recommendation = (
        f"Rückforderung von {excess:.2f} EUR einleiten" if excess > 0
        else "Kein Handlungsbedarf zur Höhe; Zinsanspruch bei Rückgabe geltend machen."
    )

    return KautionCheckResponse(
        legal_maximum_eur=legal_max,
        excess_eur=excess,
        deposit_ratio=ratio,
        interest_claim_eur=interest_claim,
        findings=findings,
        recommendation=recommendation,
    )


# ----------------------------------------------------------------------
# Mieterhöhung (§558 / §558b BGB)
# ----------------------------------------------------------------------

def check_mieterhoehung(req: MieterhoehungCheckRequest) -> MieterhoehungCheckResponse:
    findings: list[NKFinding] = []
    increase_eur = round(req.proposed_cold_rent_eur - req.current_cold_rent_eur, 2)
    increase_percent = round(
        increase_eur / req.current_cold_rent_eur * 100, 2
    )

    within_cap = increase_percent <= KAPPUNG_PERCENT
    if not within_cap:
        findings.append(NKFinding(
            severity="error",
            code="kappung_verletzt",
            message=(
                f"Die geforderte Erhöhung von {increase_percent:.1f}% "
                f"({increase_eur:.2f} EUR) überschreitet die Kappungsgrenze "
                "von 15% in 3 Jahren (§558 Abs. 3 BGB). Soweit überhöht "
                "kann die Miete nicht verlangt werden."
            ),
            legal_basis="§558 Abs. 3 BGB",
            amount_eur=increase_eur,
        ))
    else:
        findings.append(NKFinding(
            severity="info",
            code="kappung_ok",
            message=(
                f"Erhöhung von {increase_percent:.1f}% liegt innerhalb der "
                "Kappungsgrenze von 15% (§558 Abs. 3 BGB)."
            ),
            legal_basis="§558 Abs. 3 BGB",
        ))

    sperrfrist_ok = True
    if req.last_increase_date is not None:
        months = _months_between(req.last_increase_date, req.today)
        if months < SPERRFRIST_MONTHS:
            sperrfrist_ok = False
            findings.append(NKFinding(
                severity="error",
                code="sperrfrist",
                message=(
                    f"Erst {months} Monate seit der letzten Erhöhung — die "
                    "Sperrfrist von 15 Monaten ist noch nicht abgelaufen "
                    "(§558 Abs. 1 BGB). Die Erhöhung ist unzulässig."
                ),
                legal_basis="§558 Abs. 1 BGB",
            ))
        else:
            findings.append(NKFinding(
                severity="info",
                code="sperrfrist_ok",
                message=f"Sperrfrist von 15 Monaten eingehalten ({months} Monate).",
                legal_basis="§558 Abs. 1 BGB",
            ))
    else:
        findings.append(NKFinding(
            severity="info",
            code="sperrfrist_unbekannt",
            message=(
                "Keine letzte Erhöhung angegeben — die 15-Monats-Sperrfrist "
                "konnte nicht geprüft werden."
            ),
        ))

    formal_notice_hint = True
    findings.append(NKFinding(
        severity="info",
        code="formvoraussetzung",
        message=(
            "Der Vermieter muss die Erhöhung schriftlich begründen und auf "
            "ein Mietspiegel-Exemplar verweisen (§558b Abs. 1 BGB). "
            "Ohne Begründung musst du nicht zustimmen."
        ),
        legal_basis="§558b Abs. 1 BGB",
    ))

    if not within_cap or not sperrfrist_ok:
        recommendation = (
            "Die Erhöhung ist in Teilen unzulässig — nicht zustimmen, "
            "begründet widersprechen. Mieterverein prüfen lassen."
        )
    else:
        recommendation = (
            "Formal im Rahmen. Prüfe die Begründung anhand des Mietspiegels "
            "(§558b BGB) und gleiche mit dem Mietpreis-Check dieser App ab."
        )

    return MieterhoehungCheckResponse(
        increase_percent=increase_percent,
        increase_eur=increase_eur,
        within_15_percent_cap=within_cap,
        sperrfrist_ok=sperrfrist_ok,
        formal_notice_hint=formal_notice_hint,
        findings=findings,
        recommendation=recommendation,
    )

# ----------------------------------------------------------------------
# Kautionsanbieter-Vergleich (Daten: data/processed/kautionsanbieter.json)
# ----------------------------------------------------------------------

_KAUTION_DATA_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "data"
    / "processed"
    / "kautionsanbieter.json"
)
_kaution_cache: dict | None = None


def _load_providers() -> tuple[list[Kautionsanbieter], str]:
    global _kaution_cache
    if _kaution_cache is None:
        raw = json.loads(_KAUTION_DATA_PATH.read_text(encoding="utf-8"))
        _kaution_cache = raw
    raw = _kaution_cache
    providers = [Kautionsanbieter.model_validate(p) for p in raw["providers"]]  # type: ignore[index]
    return providers, str(raw["generated_at"])  # type: ignore[index]


def compare_kaution(req: KautionsVergleichRequest) -> KautionsVergleichResponse:
    """Vergleich aller Mietkautionsbürgschaft-Anbieter für eine Kautionssumme.

    Hinweis: Eine Bürgschaft kostet jährlich Geld und kommt nie zurück —
    sie löst ein Liquiditätsproblem, kein Kostenproblem. Der Vergleich
    zeigt deshalb auch, wie viel Kapital gebunden bleibt.
    """
    providers, updated_at = _load_providers()
    offers: list[KautionsAngebot] = []
    for p in providers:
        if p.max_deposit_eur is not None and req.deposit_eur > p.max_deposit_eur:
            continue
        fee = round(req.deposit_eur * p.percent_per_year / 100, 2)
        if p.min_fee_eur_per_year is not None:
            fee = round(max(fee, p.min_fee_eur_per_year), 2)
        offers.append(KautionsAngebot(
            provider=p,
            fee_eur_per_year=fee,
            total_cost_eur=round(fee * req.years, 2),
            blocked_capital_eur=0.0,
            note=(
                f"Ab {p.percent_per_year:.1f}% p.a."
                + (f", mind. {p.min_fee_eur_per_year:.0f} EUR" if p.min_fee_eur_per_year else "")
                + f" · Bonität via {p.bonitaet_check}"
            ),
        ))
    offers.sort(key=lambda o: o.total_cost_eur)
    cheapest = offers[0].total_cost_eur if offers else 0.0
    most_expensive = offers[-1].total_cost_eur if offers else 0.0
    return KautionsVergleichResponse(
        deposit_eur=req.deposit_eur,
        years=req.years,
        offers=offers,
        cheapest_offer_eur=cheapest,
        savings_vs_most_expensive_eur=round(most_expensive - cheapest, 2),
        dataset_updated_at=updated_at,
        disclaimer=(
            "Anbieterangaben ohne Gewähr; kein vollständiger Marktüberblick. "
            "Vor Abschluss immer die aktuelle Preisliste des Anbieters prüfen. "
            "Eine Bürgschaft bindet kein Kapital, kostet aber jährlich — "
            "bei verfügbarer Liquidität ist das klassische Kautionskonto meist günstiger."
        ),
    )


# ----------------------------------------------------------------------
# Versorger-Sparrechner (Grundversorgung vs. günstiger Markt-Tarif)
# ----------------------------------------------------------------------

def compute_sparpotential(req: SparrechnerRequest) -> SparrechnerResponse:
    """Sparpotenzial beim Wechsel von der Grundversorgung in einen
    günstigen Markttarif — mit den vom User abgelesenen Portal-Preisen."""
    grund_eur = round(req.jahresverbrauch_kwh * req.grundversorger_ct_kwh / 100, 2)
    markt_eur = round(req.jahresverbrauch_kwh * req.guenstigster_tarif_ct_kwh / 100, 2)
    grund_eur = round(grund_eur + req.grundgebuehr_grundversorger_eur, 2)
    markt_eur = round(markt_eur + req.grundgebuehr_tarif_eur, 2)
    ersparnis = round(grund_eur - markt_eur, 2)
    prozent = round(ersparnis / grund_eur * 100, 1) if grund_eur > 0 else 0.0

    empfehlung = (
        f"Wechsel spart rund {max(ersparnis, 0):.0f} €/Jahr ({prozent:.0f}%). "
        "Achte auf echte Preisgarantie (inkl. Steuern/Netzentgelte), "
        "keine Vorkasse, Bonus-Bedingungen und den Preis im 2. Jahr."
        if ersparnis > 50
        else "Geringer Unterschied — bei < 50 € Ersparnis lieber auf "
        "bessere Konditionen achten (Preisgarantie-Dauer, Ökostrom)."
    )
    return SparrechnerResponse(
        jahreskosten_grundversorger_eur=grund_eur,
        jahreskosten_tarif_eur=markt_eur,
        ersparnis_eur=max(ersparnis, 0.0),
        ersparnis_prozent=prozent,
        empfehlung=empfehlung,
    )
