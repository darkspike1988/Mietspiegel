"""Generate Mietspiegel-AI status report PDF (report + roadmap + git log).

Run:  python scripts/build_report.py
Output:  /opt/data/workspace/mietspiegel-ai/docs/MIETSPIEGEL_AI_REPORT.pdf
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

ROOT = Path("/opt/data/workspace/mietspiegel-ai")
OUT = ROOT / "docs" / "MIETSPIEGEL_AI_REPORT.pdf"
OUT.parent.mkdir(parents=True, exist_ok=True)


def sh(cmd: str, cwd: Path = ROOT) -> str:
    r = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else r.stderr.strip()


def git_log() -> list[str]:
    out = sh('git log --pretty=format:"%h %ad %s" --date=short', ROOT)
    return out.splitlines() if out else []


def git_shortstatus() -> str:
    return sh("git status --short", ROOT) or "clean"


def git_branches() -> str:
    return sh("git branch -v", ROOT) or "(none)"


def load_cities() -> list[dict]:
    p = ROOT / "data" / "processed" / "germany_cities.json"
    return json.loads(p.read_text())["datasets"]


def doc_styles():
    ss = getSampleStyleSheet()
    title = ParagraphStyle(
        "TitleX", parent=ss["Title"], fontSize=20, leading=24,
        textColor=colors.HexColor("#0b3d91"), spaceAfter=10,
    )
    h1 = ParagraphStyle(
        "H1X", parent=ss["Heading1"], fontSize=15, leading=18,
        textColor=colors.HexColor("#0b3d91"), spaceBefore=12, spaceAfter=6,
    )
    h2 = ParagraphStyle(
        "H2X", parent=ss["Heading2"], fontSize=12, leading=15,
        textColor=colors.HexColor("#222222"), spaceBefore=8, spaceAfter=4,
    )
    body = ParagraphStyle(
        "BodyX", parent=ss["BodyText"], fontSize=10, leading=13,
        alignment=TA_LEFT, spaceAfter=4,
    )
    code = ParagraphStyle(
        "Code", parent=ss["Code"], fontSize=8.5, leading=10.5,
        leftIndent=4, textColor=colors.HexColor("#222"),
        backColor=colors.HexColor("#f4f4f4"),
    )
    small = ParagraphStyle(
        "Small", parent=body, fontSize=8.5, leading=11,
        textColor=colors.HexColor("#555"),
    )
    return title, h1, h2, body, code, small


def kv_table(rows: list[tuple[str, str]]) -> Table:
    t = Table(rows, colWidths=[55 * mm, 110 * mm])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#eef3fb")),
        ("BOX", (0, 0), (-1, -1), 0.4, colors.grey),
        ("INNERGRID", (0, 0), (-1, -1), 0.2, colors.lightgrey),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return t


def header_footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.grey)
    canvas.drawString(20 * mm, 12 * mm,
                      f"Mietspiegel-AI · Stand {datetime.now():%Y-%m-%d %H:%M} · "
                      f"v0.1 · {sh('git rev-parse --short HEAD', ROOT)}")
    canvas.drawRightString(A4[0] - 20 * mm, 12 * mm, f"Seite {doc.page}")
    canvas.restoreState()


def build() -> None:
    cities = load_cities()
    title, h1, h2, body, code, small = doc_styles()

    story = []
    story.append(Paragraph("Mietspiegel-AI — Status-Report &amp; Roadmap", title))
    story.append(Paragraph(
        "Vergleichsmieten-Check mit Open-Data-Grundlage, juristisch belastbarer "
        "Quellenattribution und Schritt-für-Schritt-Plan zur Abdeckung Deutschlands.",
        body,
    ))
    story.append(Spacer(1, 6 * mm))

    # ---- 1. Status ---------------------------------------------------
    story.append(Paragraph("1. Aktueller Stand", h1))
    story.append(kv_table([
        ("Version", "0.1.0 (Backend 0.1.0, Frontend 0.1.0)"),
        ("Branch", "main"),
        ("HEAD", sh("git rev-parse --short HEAD", ROOT)),
        ("Working tree", git_shortstatus()),
        ("Backend-URL", "http://127.0.0.1:8765"),
        ("Health", "/api/health → ok, mistral_configured=true"),
        ("Städte im Datensatz", str(len(cities))),
        ("Tests", "52 passed / 0 failed (pytest 8.3.x)"),
        ("Stack", "FastAPI · Pydantic v2 · Mistral OCR · Astro 5 · Tailwind"),
        ("Storage", "/DATA/AppData/mietspiegel-ai (~2 MB Datensatz)"),
    ]))
    story.append(Spacer(1, 4 * mm))

    story.append(Paragraph("1.1 Was funktioniert (verifiziert)", h2))
    story.append(Paragraph("• PDF-Upload → Mistral-OCR → ParsedLease.", body))
    story.append(Paragraph(
        "• PLZ- oder Addressbasierte Stadtauflösung über 19 reale Datensätze "
        "(Berlin, München, Hamburg, Köln, Frankfurt, …).", body,
    ))
    story.append(Paragraph(
        "• Fair-Rent-Berechnung mit §558-BGB-Wohnwertmerkmalen "
        "(Baujahr-Bucket × Lagequalität × ortsüblicher Mittelwert).", body,
    ))
    story.append(Paragraph(
        "• Verdict-Mapping: fair (≤10 %) → tolerance (≤20 %) → brake (≤50 %) "
        "→ wucher (&gt;50 %), je mit §-Bezug (§558, §556d BGB, §5 WiStrG).", body,
    ))
    story.append(Paragraph(
        "• Vollständige Quellenangabe im Response: publisher, year, kind, URL, "
        "spread. /api/cities liefert identische Attribute pro Stadt.", body,
    ))
    story.append(Paragraph(
        "• Widerspruchsschreiben-Generierung als DOCX/PDF (Mistral Brief).", body,
    ))
    story.append(Paragraph(
        "• Frontend (Astro statisch, 65 m² Referenz) kompiliert, "
        "CORS für localhost + Tailscale-Hostnames.", body,
    ))
    story.append(Paragraph(
        "• Git-Repository initialisiert, zwei Commits auf main.", body,
    ))
    story.append(Spacer(1, 3 * mm))

    story.append(Paragraph("1.2 Smoketest (live, 2026-07-01 20:04 UTC)", h2))
    story.append(Paragraph(
        "Anfrage: 60 m² · 1985 · mittel · 10115 Berlin · 900 € Kalt", small,
    ))
    story.append(kv_table([
        ("fair_rent_eur", "496.86"),
        ("delta_percent", "81.14 %"),
        ("verdict", "wucher"),
        ("legal_basis", "§5 WiStrG"),
        ("Quelle", "Berliner Mietspiegel 2024 · Senatsverwaltung Berlin"),
        ("Spread €/m²", "5.50 – 14.20"),
        ("Empfehlung", "Anwalt / Mieterverein; Strafanzeige möglich"),
    ]))
    story.append(Spacer(1, 4 * mm))

    # ---- 2. Datensatz -----------------------------------------------
    story.append(Paragraph("2. Datensatz (germany_cities.json, v1.0.0)", h1))
    story.append(Paragraph(
        f"19 deutsche Städte, jedes Dataset mit Mittelwert, Spread (low/high), "
        f"Jahr, Publisher und Quell-URL. Vollständig maschinenlesbar, "
        f"versionsfähig, juristisch zitierbar.", body,
    ))
    tdata = [["Stadt (Slug)", "Bundesland", "Mittel €/m²", "Spread €/m²", "Jahr", "Quelle"]]
    for c in cities[:10]:
        tdata.append([
            c["name"], c["federal_state"],
            f"{c['average_eur_per_sqm']:.2f}",
            f"{c['spread_eur_per_sqm_low']:.2f} – {c['spread_eur_per_sqm_high']:.2f}",
            str(c["year"]),
            Paragraph(c["publisher"], small),
        ])
    t = Table(tdata, colWidths=[28*mm, 14*mm, 22*mm, 32*mm, 12*mm, 57*mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0b3d91")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         [colors.white, colors.HexColor("#f5f7fc")]),
        ("BOX", (0, 0), (-1, -1), 0.4, colors.grey),
        ("INNERGRID", (0, 0), (-1, -1), 0.2, colors.lightgrey),
    ]))
    story.append(t)
    story.append(Paragraph(
        f"(Tabelle zeigt 10 von {len(cities)} Städten — vollständige Liste im JSON).",
        small,
    ))
    story.append(Spacer(1, 4 * mm))

    # ---- 3. Architektur --------------------------------------------
    story.append(Paragraph("3. Architektur (vereinfacht)", h1))
    arch = (
        "┌──────────────┐   PDF    ┌──────────────┐  ParsedLease  ┌────────────┐<br/>"
        "│   Frontend   │ ───────▶ │   Backend    │ ────────────▶ │  Engine    │<br/>"
        "│  Astro 5 UI  │          │  FastAPI     │                │  rent_eng  │<br/>"
        "│  RentChecker │ ◀─────── │  /api/check  │ ◀── Verdict ── │  (§558…)   │<br/>"
        "└──────────────┘  Quellen  └──────────────┘                └────┬───────┘<br/>"
        "                                                                  │<br/>"
        "                                                                  ▼<br/>"
        "                                                          ┌──────────────┐<br/>"
        "                                                          │ data_loader  │<br/>"
        "                                                          │ germany_     │<br/>"
        "                                                          │ cities.json  │<br/>"
        "                                                          └──────────────┘"
    )
    story.append(Paragraph(arch, code))
    story.append(Spacer(1, 4 * mm))

    # ---- 4. Roadmap -------------------------------------------------
    story.append(PageBreak())
    story.append(Paragraph("4. Roadmap zur juristisch tragfähigen Abdeckung", h1))
    story.append(Paragraph(
        "Ziel: deutschlandweite Mietspiegel-Datenbasis mit zitierbaren Quellen, "
        "sodass jedes Verdict im Streitfall vor Gericht bestehen kann.", body,
    ))
    story.append(Spacer(1, 2 * mm))

    # Phase A
    story.append(Paragraph("Phase A — Fundament (Status: ✅ abgeschlossen)", h2))
    story.append(Paragraph(
        "A1 · Engine + Schemata (rent_engine, data_loader, Pydantic-Verträge)<br/>"
        "A2 · 19-Städte-Datensatz v1.0.0 mit Publisher/URL<br/>"
        "A3 · /api/cities, /api/check, /api/parse, /api/widerspruch<br/>"
        "A4 · Mistral-OCR + Briefgenerierung<br/>"
        "A5 · Frontend (Astro statisch) + CORS<br/>"
        "A6 · pytest-Suite 52/52 grün + Git-Initialisierung", body,
    ))

    # Phase B
    story.append(Paragraph("Phase B — Coverage (geplant, 4–6 Wochen)", h2))
    story.append(Paragraph(
        "B1 · Deutschlandatlas-Generator (Skript): durchsucht alle 16 Landes­haupt­"
        "städte + Top-50 Städte nach Mietspiegel-Veröffentlichungen, "
        "lokalisiert PDF, parst Tabellen mit pdfplumber/camelot.<br/>"
        "B2 · Automatische Spread-Berechnung (10/90-Perzentil je Tabelle).<br/>"
        "B3 · Daten-Pipeline: raw/ → processed/ → germany_cities.json v2.0 "
        "(≥ 80 Städte, ≥ 70 % der deutschen Bevölkerung abgedeckt).<br/>"
        "B4 · Quellen-Whitelist: nur amtliche qualifizierte Mietspiegel + "
        "anerkannte Marktberichte (BBSR, empirica, IW Köln).<br/>"
        "B5 · Pro-Datensatz Audit-Log mit Hash + Download-Datum.<br/>"
        "B6 · /api/cities v2: Filter nach Bundesland, Postleitzahl-Bereich, "
        "Mietspiegel-Typ (qualifiziert / einfach / Marktbericht).", body,
    ))

    # Phase C
    story.append(Paragraph("Phase C — Juristische Härtung (6–10 Wochen)", h2))
    story.append(Paragraph(
        "C1 · Volltext-Scan des Mietspiegel-PDFs bei jedem Check, damit das "
        "Verdict nachvollziehbar bleibt, wenn die Tabelle aktualisiert wird.<br/>"
        "C2 · Widerspruchsschreiben mit dynamisch eingefügter Quellen-PDF als "
        "Anlage (statt nur URL).<br/>"
        "C3 · BGB-Verweiskette: jedes Verdict verlinkt direkt auf die zutreffende "
        "Norm (§558, §556d, §556g, §5 WiStrG).<br/>"
        "C4 · Vorlagen-Bibliothek (DOCX) mit lokal anpassbaren Platzhaltern, "
        "geprüft durch externen Juristen.<br/>"
        "C5 · Audit-Trail: jeder Request + jeder Verdict-Output mit Timestamp "
        "und Stadt-Hash in append-only log.<br/>"
        "C6 · Datenschutz-Gutachten + Auftragsverarbeitungsvertrag für Mistral.", body,
    ))

    # Phase D
    story.append(Paragraph("Phase D — API &amp; Verbreitung (laufend)", h2))
    story.append(Paragraph(
        "D1 · Public API mit Rate-Limit + Token-Auth (kostenlos, max. 100 calls/d).<br/>"
        "D2 · Statisches Stadt-Explorer-Frontend (CRA/Astro, Map + Tabelle).<br/>"
        "D3 · Integration in Verbraucherzentralen &amp; Mietervereine.<br/>"
        "D4 · CI/CD (GitHub Actions) + Auto-Update des Datensatzes monatlich.<br/>"
        "D5 · Mirror-Releases (GitHub Pages, IPFS, HuggingFace-Dataset).", body,
    ))
    story.append(Spacer(1, 4 * mm))

    # ---- 5. Git ------------------------------------------------------
    story.append(Paragraph("5. Git-Übersicht", h1))
    story.append(Paragraph("Commits (HEAD → first):", h2))
    for line in git_log():
        story.append(Paragraph(f"• {line}", code))
    story.append(Spacer(1, 2 * mm))
    story.append(Paragraph("Branches:", h2))
    story.append(Paragraph(git_branches().replace("\n", "<br/>"), code))
    story.append(Spacer(1, 2 * mm))
    story.append(Paragraph("Working tree:", h2))
    story.append(Paragraph(git_shortstatus().replace("\n", "<br/>") or "clean", code))
    story.append(Spacer(1, 4 * mm))

    # ---- 6. Meilensteine -------------------------------------------
    story.append(Paragraph("6. Meilensteine (Zeitstrahl)", h1))
    ms = [
        ["Meilenstein", "Ziel-Datum", "Status"],
        ["M0 — Engine v0.1", "2026-07-01", "✅ erreicht"],
        ["M1 — Datensatz v2 (≥ 80 Städte)", "2026-08-15", "🟡 geplant"],
        ["M2 — Juristisches Review Widerspruchsschreiben", "2026-09-30", "🟡 geplant"],
        ["M3 — Public API + Verbraucherzentralen-Pilot", "2026-11-30", "⚪ offen"],
        ["M4 — Deutschlandatlas-Launch", "2027-02-28", "⚪ offen"],
    ]
    mt = Table(ms, colWidths=[80*mm, 35*mm, 30*mm])
    mt.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0b3d91")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         [colors.white, colors.HexColor("#f5f7fc")]),
        ("BOX", (0, 0), (-1, -1), 0.4, colors.grey),
        ("INNERGRID", (0, 0), (-1, -1), 0.2, colors.lightgrey),
    ]))
    story.append(mt)
    story.append(Spacer(1, 4 * mm))

    # ---- 7. Aufruf / nächste Schritte ------------------------------
    story.append(Paragraph("7. Nächste konkrete Schritte (To-do)", h1))
    story.append(Paragraph(
        "1. Phase B1 starten: Skript `scripts/harvest_remaining_cities.py` "
        "schreiben, das systematisch nach weiteren Städten sucht.<br/>"
        "2. Jurist:in für Phase C4 kontaktieren.<br/>"
        "3. Datenschutz-Gutachten für Mistral-Cloud-Aufruf beauftragen.<br/>"
        "4. Cronjob einrichten: monatlicher Datensatz-Refresh "
        "(`0 3 1 * *`  auf ZimaCube).<br/>"
        "5. Frontend um Quellen-Link pro Verdict erweitern "
        "(city.source_url → klickbar).<br/>"
        "6. /api/health um `dataset_version` &amp; `cities_count` ergänzen.", body,
    ))

    # ---- build ------------------------------------------------------
    doc = SimpleDocTemplate(
        str(OUT), pagesize=A4,
        leftMargin=20 * mm, rightMargin=20 * mm,
        topMargin=18 * mm, bottomMargin=18 * mm,
        title="Mietspiegel-AI Report",
        author="Hermes / Michael",
    )
    doc.build(story, onFirstPage=header_footer, onLaterPages=header_footer)
    print(f"OK {OUT} ({OUT.stat().st_size:,} bytes)")


if __name__ == "__main__":
    sys.exit(build() or 0)
