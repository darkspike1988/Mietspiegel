"""Wohnprofil-Modell — kombiniert alle Datenquellen in einem Score.

Das zentrale Kombinations-Modell der App: Für eine konkrete Wohnung
(Suchtreffer) werden alle verfügbaren Daten in EINEM bewerteten Modell
zusammengeführt:

  ┌────────────────────────────────────────────────────┐
  │                    WOHNPROFIL                      │
  ├───────────────┬────────────────────────────────────┤
  │ Mietspiegel   │ → delta %, Badge, Überzahlung/Jahr │
  │ Budget        │ → 30 %-Faustregel des Einkommens    │
  │ Versorger     │ → Strompreis-Faktor (SMARD/Markt)   │
  │ Markt         │ → wie überhöht ist das Umfeld?      │
  ├───────────────┴────────────────────────────────────┤
  │             SCORE 0-100 (je höher, desto besser)   │
  │   + Ampel-Empfehlung mit Begründung je Dimension   │
  └────────────────────────────────────────────────────┘

Gewichte (Summe 100):
  - Mietspiegel-Abweichung: 50 (Kernstück, rechtlich fundiert)
  - Budget-Belastung:      30 (30 %-Faustregel)
  - Markt-Kontext:         20 (Umgebung fair oder überhöht?)

Deterministisch und nachvollziehbar: Jede Dimension liefert ihre
Teilpunkte + Klartext-Begründung — kein Blackbox-Score.
"""
from __future__ import annotations

from app.schemas.tariff_stats import MarketStats
from app.schemas.wohnprofil import (
    DimensionScore,
    WohnprofilRequest,
    WohnprofilResponse,
)

# ----------------------------------------------------------------------
# Gewichte (Summe 100)
# ----------------------------------------------------------------------
WEIGHT_MIETSPIEGEL = 50
WEIGHT_BUDGET = 30
WEIGHT_MARKT = 20


def score_mietspiegel_dimension(delta_percent: float | None) -> DimensionScore:
    """50 Punkte: Mietspiegel-Abweichung → Punktzahl.

    fair (≤10 %) = volle 50, tolerance = 35, brake = 15, wucher = 0,
    unbekannt = 25 (neutral, keine Panik ohne Daten).
    """
    if delta_percent is None:
        return DimensionScore(
            weight=WEIGHT_MIETSPIEGEL,
            points=25.0,
            label="Mietspiegel",
            detail="Keine Mietspiegel-Daten für diese Wohnung ermittelbar — neutral bewertet.",
        )
    if delta_percent <= 10:
        pts = 50.0
        txt = f"{delta_percent:+.1f} % vs. ortsüblicher Miete — fair (§558 BGB)."
    elif delta_percent <= 20:
        pts = 35.0
        txt = f"{delta_percent:+.1f} % über Mietspiegel — noch im Toleranzbereich."
    elif delta_percent <= 50:
        pts = 15.0
        txt = (
            f"{delta_percent:+.1f} % über Mietspiegel — Mietpreisbremse-Verdacht "
            "(§556d BGB). Widerspruch prüfen!"
        )
    else:
        pts = 0.0
        txt = (
            f"{delta_percent:+.1f} % über Mietspiegel — Wucher-Verdacht "
            "(§5 WiStrG). Finger weg oder Mieterverein einschalten."
        )
    return DimensionScore(
        weight=WEIGHT_MIETSPIEGEL, points=pts, label="Mietspiegel", detail=txt
    )


def score_budget_dimension(housing_cost_percent: float) -> DimensionScore:
    """30 Punkte: 30 %-Faustregel → Punktzahl (linear)."""
    if housing_cost_percent <= 20:
        pts = 30.0
        txt = f"{housing_cost_percent:.1f} % des Einkommens — komfortabel."
    elif housing_cost_percent <= 30:
        pts = round(30.0 - (housing_cost_percent - 20) * 1.5, 1)
        txt = f"{housing_cost_percent:.1f} % des Einkommens — im Rahmen (Faustregel ≤30 %)."
    else:
        # über 30 %: linearer Abzug bis 60 % = 0 Punkte
        pts = max(0.0, round(30.0 - 30.0 - (housing_cost_percent - 30) * 1.0, 1))
        txt = (
            f"{housing_cost_percent:.1f} % des Einkommens — über der "
            "30 %-Faustregel. Hohe Belastungsquote."
        )
    return DimensionScore(
        weight=WEIGHT_BUDGET, points=pts, label="Budget", detail=txt
    )


def score_markt_dimension(stats: MarketStats | None) -> DimensionScore:
    """20 Punkte: Markt-Kontext — wie viele Angebote im Umfeld sind fair?"""
    if stats is None or stats.total_listings == 0:
        return DimensionScore(
            weight=WEIGHT_MARKT, points=10.0, label="Markt",
            detail="Keine Marktdaten für die Region — neutral bewertet.",
        )
    fair_share = 100 - stats.overpriced_percent
    pts = round(fair_share / 100 * 20, 1)
    if fair_share >= 70:
        txt = (
            f"{fair_share:.0f} % der Angebote in der Region sind ortsüblich — "
            "gesunder Markt."
        )
    elif fair_share >= 40:
        txt = (
            f"Nur {fair_share:.0f} % der regionalen Angebote sind fair — "
            "SPANnung Markt, genau hinschauen."
        )
    else:
        txt = (
            f"Nur {fair_share:.0f} % der regionalen Angebote sind fair — "
            "extrem überhitzter Markt, Widerspruchs-Chance ist hoch."
        )
    return DimensionScore(
        weight=WEIGHT_MARKT, points=pts, label="Markt", detail=txt
    )


def compute_wohnprofil(req: WohnprofilRequest) -> WohnprofilResponse:
    """Das Gesamtmodell: alle Dimensionen → ein Score + Empfehlung."""
    dim_miete = score_mietspiegel_dimension(req.delta_percent)
    total_housing = req.cold_rent_eur + req.nebenkosten_eur + req.strom_eur
    cost_pct = round(total_housing / req.net_income_eur * 100, 1) if req.net_income_eur > 0 else 0.0
    dim_budget = score_budget_dimension(cost_pct)
    dim_markt = score_markt_dimension(req.market_stats)

    score = round(
        dim_miete.points + dim_budget.points + dim_markt.points, 1
    )

    if score >= 70:
        ampel = "grün"
        empfehlung = (
            "Rundum gutes Angebot: Miete im Rahmen, Budget gesund. "
            "Besichtigungstermin sichern!"
        )
    elif score >= 45:
        ampel = "gelb"
        empfehlung = (
            "Brauchbares Angebot mit Schwächen — Verhandlungsgrundlage "
            "nutzen oder Mietpreisbremse-Widerspruch vorbereiten."
        )
    else:
        ampel = "rot"
        empfehlung = (
            "Von dieser Wohnung ist abzuraten: zu teuer für Mietspiegel "
            "oder Budget. Widerspruch oder weitersuchen."
        )

    return WohnprofilResponse(
        score=score,
        ampel=ampel,
        housing_cost_percent=cost_pct,
        empfehlung=empfehlung,
        dimensions=[dim_miete, dim_budget, dim_markt],
    )
