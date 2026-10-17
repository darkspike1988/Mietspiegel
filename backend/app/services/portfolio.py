"""Eigentums-Portfolio (Pro-Feature): ETW & Häuser + statistische Auswertung.

Für Eigentümer, die mehrere Objekte halten: Verwaltung + Rendite-Analyse
pro Objekt + Markt-Badge (über die bestehende Mietspiegel-Engine!) +
Portfoliio-Statistik über alle Objekte.
"""
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

import structlog

from app.schemas.portfolio import (
    ImmobilienObjekt,
    PortfolioResponse,
    PortfolioStatistik,
)
from app.services import storage

log = structlog.get_logger(__name__)


def _ensure_schema() -> sqlite3.Connection:
    storage._get_conn()
    conn = storage._conn
    assert conn is not None
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS portfolio_properties (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            bezeichnung TEXT NOT NULL,
            art TEXT NOT NULL DEFAULT 'etw',
            adresse TEXT,
            plz TEXT,
            stadt TEXT,
            wohnflaeche_qm REAL,
            kaufpreis_eur REAL,
            jahresnettokaltmiete_eur REAL,
            nebenkosten_eur REAL DEFAULT 0,
            baujahr INTEGER,
            verwaltungskosten_eur REAL DEFAULT 0,
            Created_at TEXT NOT NULL
        );
        """
    )
    # Spaltennamen-Konsistenz (created_at klein)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS portfolio_props (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            bezeichnung TEXT NOT NULL,
            art TEXT DEFAULT 'etw',
            adresse TEXT,
            plz TEXT,
            stadt TEXT,
            wohnflaeche_qm REAL,
            kaufpreis_eur REAL,
            jahresnettokaltmiete_eur REAL,
            nebenkosten_eur REAL DEFAULT 0,
            baujahr INTEGER,
            verwaltungskosten_eur REAL DEFAULT 0,
            created_at TEXT NOT NULL
        );
        """
    )
    conn.commit()
    return conn


def add_property(user_id: int, obj: ImmobilienObjekt) -> int:
    """Objekt ins Portfolio des Users aufnehmen."""
    from app.services.plan_service import get_user_plan, limit_for

    limit = limit_for(get_user_plan(user_id), "portfolio_properties")
    conn = _ensure_schema()
    count = conn.execute(
        "SELECT COUNT(*) FROM portfolio_props WHERE user_id = ?", (user_id,)
    ).fetchone()[0]
    if count >= limit:
        raise ValueError(
            f"Portfolio-Limit erreicht ({limit} Objekte). "
            "Pro-Plan (12,99 €) erlaubt 25 Objekte."
        )
    with storage._lock:
        cur = conn.execute(
            "INSERT INTO portfolio_props (user_id, bezeichnung, art, adresse, plz, "
            "stadt, wohnflaeche_qm, kaufpreis_eur, jahresnettokaltmiete_eur, "
            "nebenkosten_eur, baujahr, verwaltungskosten_eur, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, obj.bezeichnung, obj.art.value, obj.adresse, obj.plz,
             obj.stadt, obj.wohnflaeche_qm, obj.kaufpreis_eur,
             obj.jahresnettokaltmiete_eur, obj.nebenkosten_eur, obj.baujahr,
             obj.verwaltungskosten_eur,
             datetime.now(UTC).isoformat()),
        )
        conn.commit()
        return int(cur.lastrowid or 0)


def list_properties(user_id: int) -> list[dict]:
    """Alle Objekte des Users."""
    conn = _ensure_schema()
    rows = conn.execute(
        "SELECT * FROM portfolio_props WHERE user_id = ? ORDER BY id",
        (user_id,),
    ).fetchall()
    cols = [d[0] for d in conn.execute(
        "SELECT * FROM portfolio_props LIMIT 0").description]
    return [dict(zip(cols, r, strict=False)) for r in rows]


def compute_objekt_statistik(obj: dict) -> dict:
    """Statistische Auswertung EINES Objekts (Rendite + Markt-Badge)."""
    kauf = obj.get("kaufpreis_eur") or 0
    miete_jahr = obj.get("jahresnettokaltmiete_eur") or 0
    bewirt = obj.get("verwaltungskosten_eur") or 0
    qm = obj.get("wohnflaeche_qm") or 0

    netto_jahr = miete_jahr - bewirt
    bruttorendite = round(netto_jahr / kauf * 100, 2) if kauf > 0 else None
    miete_qm = round(miete_jahr / 12 / qm, 2) if qm > 0 else None

    # Markt-Badge über die bestehende Engine (falls Stadt bekannt)
    markt_delta = None
    markt_badge = None
    from app.services import rent_engine
    if obj.get("plz") and qm and miete_jahr:
        listing_miete = miete_jahr / 12
        match = rent_engine.resolve_city(
            str(obj.get("stadt") or ""), str(obj.get("plz"))
        )
        if match:
            fair = rent_engine.compute_fair_rent(
                city_avg_eur_per_sqm=match.dataset.average_eur_per_sqm,
                size_sqm=qm,
                build_year=obj.get("baujahr"),
                location_quality=None,
            )
            if fair > 0:
                markt_delta = round((listing_miete - fair) / fair * 100, 1)
                markt_badge = rent_engine.classify_verdict(markt_delta).value

    return {
        "bruttorendite_prozent": bruttorendite,
        "nettojahresmiete_eur": round(netto_jahr, 2),
        "miete_pro_qm_eur": miete_qm,
        "markt_delta_prozent": markt_delta,
        "markt_badge": markt_badge,
    }


def compute_portfolio_statistik(user_id: int) -> PortfolioStatistik:
    """Statistik über ALLE Objekte: Gesamt-Rendite, Ø-m²-Preis, Badges."""
    props = list_properties(user_id)
    if not props:
        return PortfolioStatistik(
            anzahl_objekte=0,
            gesamt_kaufpreis_eur=0,
            gesamt_nettojahresmiete_eur=0,
            gesamtrendite_prozent=None,
            avg_miete_pro_qm_eur=None,
            markt_uebersicht="Noch keine Objekte im Portfolio.",
        )

    gesamt_kauf = sum(p.get("kaufpreis_eur") or 0 for p in props)
    gesamt_netto = sum(
        (p.get("jahresnettokaltmiete_eur") or 0) - (p.get("verwaltungskosten_eur") or 0)
        for p in props
    )
    gesamtrendite = (
        round(gesamt_netto / gesamt_kauf * 100, 2) if gesamt_kauf > 0 else None
    )
    mieten_qm = [
        (p.get("jahresnettokaltmiete_eur") or 0) / 12 / (p.get("wohnflaeche_qm") or 0)
        for p in props
        if p.get("wohnflaeche_qm")
    ]
    avg_qm = round(sum(mieten_qm) / len(mieten_qm), 2) if mieten_qm else None

    # Markt-Verteilung der Badges
    badges: list[str] = []
    for p in props:
        stats = compute_objekt_statistik(p)
        if stats["markt_badge"]:
            badges.append(stats["markt_badge"])
    if badges:
        verteilung = ", ".join(
            f"{b}: {badges.count(b)}" for b in sorted(set(badges))
        )
        markt_text = f"Markt-Badges: {verteilung}"
    else:
        markt_text = "Markt-Badges: keine Mietspiegel-Daten für die Objekte."

    return PortfolioStatistik(
        anzahl_objekte=len(props),
        gesamt_kaufpreis_eur=round(gesamt_kauf, 2),
        gesamt_nettojahresmiete_eur=round(gesamt_netto, 2),
        gesamtrendite_prozent=gesamtrendite,
        avg_miete_pro_qm_eur=avg_qm,
        markt_uebersicht=markt_text,
    )


def get_portfolio_response(user_id: int) -> PortfolioResponse:
    """Komplettes Portfolio inkl. je-Objekt-Statistik + Gesamtstatistik."""
    props = list_properties(user_id)
    objekte = []
    for p in props:
        stats = compute_objekt_statistik(p)
        p_out = dict(p)
        p_out["statistik"] = stats
        objekte.append(p_out)
    gesamt = compute_portfolio_statistik(user_id)
    return PortfolioResponse(objekte=objekte, gesamt=gesamt)
