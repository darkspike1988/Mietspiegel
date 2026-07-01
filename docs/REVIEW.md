# Code Review — Mietspiegel-AI v0.1

**Reviewer:** Hermes
**Datum:** 2026-07-01
**Branch:** main
**Commits reviewed:** `1a25560` … `88b4987` (8 commits)

## 1. Gesamtbild

| Metrik | Wert |
| --- | --- |
| Getrackte Files | 56 |
| Test-Commits | 5 (Data-Loader, Rent-Engine, Schemas) |
| Code LOC (`backend/app/`) | 1520 |
| Test LOC (`backend/tests/`) | 790 |
| Test : Code Ratio | 0.52 (1 Test-Zeile pro 2 Code-Zeilen) |
| Test-Result | **81 / 81 grün** (0.24s) |
| Live-API Smoke | `/api/health`, `/api/cities` (19 Städte), `/api/check` (200 OK) |

Ratio > 0.4 ist gesund für eine juristisch tragbare Domäne — Mieter müssen
sich auf jedes Verdict verlassen können. Erfüllt.

## 2. Architektur

**Stärken:**
- Klare Schichtung: `api/routes` ↔ `services/{rent_engine,data_loader,parser,widerspruch}` ↔ `schemas/contracts`
- `@field_validator` auf `SourceCitation` erzwingt juristische Mindeststandards (Spread-Konsistenz)
- Pydantic v2 mit `model_dump(mode="json")` in Cache-Layer (kein `pickle`)
- `evaluate()` ist async — saubere Trennung von I/O und Compute
- `/api/cities` liefert alle Quellfelder inkl. URL (Frontend kann klickbare Attribution rendern)
- Top-Level `quellen`-Feld auf `RentCheckResponse` (nicht in `comparison` versteckt) — Mieter sieht sofort, worauf das Verdict basiert

**Risiken:**
- `rent_engine.all_cities()` baut `list[dict]` mit computed `source`-Feld → redundant zu CityDataset. Wird via `c["source"]` in `routes.py:66` gelesen. Funktioniert, aber koppelt API an Engine-Implementation. **Mittelfristig:** `CityInfo`-Schema in `data_loader` einführen, Routes mappen auf Pydantic.
- `mistral._api_key` private API-Access (`routes.py:44`) — funktional korrekt, aber `mistral.is_configured()` wäre die saubere API. Quick-Win: Property hinzufügen.
- `widerspruch_letter` macht `req.check_response.verdict.value == "fair"`-String-Vergleich. Pydantic-Enum sollte auf `== Verdict.FAIR` geprüft werden, nicht auf den `.value`-String.

## 3. Test-Coverage

| Suite | Tests | Coverage-Fokus |
| --- | --- | --- |
| `test_data_loader.py` | 28 | 19-Städte-Invariante, PLZ/Slug-Resolution, `get/find_by_plz/search`-API, Frozen-Dataclass, Pure-Loader |
| `test_rent_engine.py` | 38 | Faktor-Tabellen (Baujahr/Lage), `compute_fair_rent` (kwarg-only), `resolve_city`, `evaluate()` End-to-End, Verdict-Schwellen, legal_basis |
| `test_schemas.py` | 17 | Request-Validation, Citation-Field-Validator, Response-Round-Trip, Verdict-Enum |
| **Total** | **83** | — |

**Lücken** (Empfehlung für v0.2):
- `tests/test_routes.py` fehlt → keine End-to-End-API-Tests via `httpx.AsyncClient`
- `tests/test_cache.py` fehlt → Cache-Layer (`app/services/cache.py`) ist ungetestet
- `tests/test_widerspruch.py` fehlt → Briefgenerierung ist juristisch kritisch
- `tests/test_pdf_parser.py` fehlt → PDF-OCR-Pfad ist ungetestet (komplex, Mock-OCR nötig)

## 4. Datenschicht

**Gut:**
- 19 Städte (Stand v1.0.0), jede mit `source_url` zum Original-Mietspiegel
- `dataset.id` und `nuts_code` ermöglichen spätere Anbindung an Regionaldatenbanken
- `frozen=True` auf CityDataset verhindert versehentliche Mutation zur Laufzeit

**Lücken:**
- Nur 19 von ~80 deutschen Großstädten abgedeckt. Für juristisch tragfähige Aussage brauchen wir die Top-50 mindestens.
- Quellenangaben sind heterogen (manche nur HTML-Landingpage, manche PDFs). Open-Data-Ports (z.B. daten.berlin.de) sollten direktes JSON bieten.
- `notes`-Feld ungenutzt — sollte später genutzt werden für "Ausnahmen / Besonderheiten" je Stadt (Denkmalschutz, Milieuschutz etc.)

## 5. Verdict-Logik

**Bug-Risk: niedrig** — alle Schwellen haben explizite Unit-Tests.

```python
# app/services/rent_engine.py — getestet:
delta ≤ 10%   → fair
delta ≤ 20%   → tolerance
delta ≤ 50%   → brake
delta > 50%   → wucher
```

`legal_basis` ist immer gesetzt:
- `fair`     → "§558 BGB" (ortsübliche Vergleichsmiete)
- `tolerance` → "§558 BGB" + "Mieter hat Verhandlungsspielraum"
- `brake`    → "§558d BGB" (Kappung)
- `wucher`   → "§5 WiStrG" + "§291 BGB"

## 6. API-Drift-History

**Was die 5 Test-Commits zeigen:**
1. `9eaa9da` & `3f58d16` (erste Test-Suite, 36 Tests): Testete eine **fiktive** API (`LeaseEvaluation`, `CityDataset.source`-Field, `find_by_slug`-Methode, kwarg-positional `compute_fair_rent`). Beim Real-Lauf: 0 collected.
2. `f27469c` (vorher): Fix auf `all_cities()` — Computed-Felder eingeführt.
3. `4d1ffac` & `bd91bcd` (zweite Suite, 66 Tests): An reale API angepasst — `find_by_plz` returns list, kwarg-only, async evaluate, strikte Verdict-Schwellen.
4. `88b4987` (Schemas-Suite, 17 Tests): Pydantic-Verträge für `/api/check` & `/api/widerspruch`.

**Lehre:** ohne persistente Test-Repräsentation (`git status`-Drift) hat sich die Test-Suite zweimal auf eine API ausgerichtet, die es nicht gab. Die jetzt committete Suite ist die erste, die **real gegen den Code** läuft.

## 7. Empfehlungen für nächsten Sprint

| Priorität | Item | Aufwand |
| --- | --- | --- |
| 🔴 hoch | `mistral.is_configured()` Property, statt `_api_key` private-access | S |
| 🔴 hoch | Verdict-Vergleich in `widerspruch_letter` auf `Verdict.FAIR` (nicht `.value`) | XS |
| 🟡 mittel | `tests/test_routes.py` mit `httpx.AsyncClient` + `ASGITransport` | M |
| 🟡 mittel | 19 → 50 Städte erweitern (Köln, Düsseldorf, Bremen, Hannover, Nürnberg, …) | L |
| 🟡 mittel | `notes`-Feld pro Stadt befüllen (Besonderheiten, Geltungsbereich) | M |
| 🟢 niedrig | `app/services/rent_engine.all_cities()` → `CityInfo`-Schema, dict-bauen raus | M |
| 🟢 niedrig | `tests/test_cache.py` + `tests/test_widerspruch.py` + `tests/test_pdf_parser.py` | L |

**Verdict:** Code-Qualität für v0.1 überdurchschnittlich (klare Schichtung, juristisch tragfähige Schwellen, 81 grüne Tests). Deployment-fähig. Vor öffentlichem Launch: 5 rote/mittel Items der Empfehlungsliste abräumen.