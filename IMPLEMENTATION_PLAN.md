# Implementierungsplan — Mietspiegel-AI Stufe 2

## Ziel

In 4 Phasen (à 1 Woche) das AI-Layer produktionsreif machen:
Mietvertrag-PDF-Upload → Strukturierte Extraktion → Vergleichsmieten-Urteil →
Widerspruchsbrief-Generierung. Datenschutz-konform, testbar, deploybar.

## Annahmen & Constraints

- **Stack**: Python 3.13, FastAPI 0.115, Pydantic v2, Mistral API (EU)
- **LLM-Provider**: Mistral (Mistral-Large für Reasoning, Mistral-OCR für PDFs)
- **Infrastruktur**: ZimaCube (i5-8500T, 31 GB RAM) oder Hetzner CX22 (4 €)
- **Frontend**: Bestehende Astro-Site unverändert, nur `frontend/components/Uploader.astro` + `/result` Page neu
- **Datenschutz**: Keine Persistenz von Mieter-PII, EU-Hosting (Mistral Paris)
- **Entwickler**: Solo (Michael) mit Vibe CLI als Coding-Subagent für Boilerplate

## Rollout-Phasen

### Phase 1 — Foundation (Tag 1-7)
**Ziel**: Lokal lauffähiger FastAPI-Service mit Health-Check, Konfiguration,
Tests grün.

| # | Task | Aufwand | Akzeptanzkriterium |
|---|------|---------|--------------------|
| 1.1 | `requirements.txt`, `.env.example`, `app/main.py` mit Lifespan | 1h | `uvicorn app.main:app` startet ohne Fehler |
| 1.2 | `core/config.py` (Pydantic-Settings) + `core/logging.py` (structlog) | 1h | `.env` korrekt geladen, JSON-Logs in Prod |
| 1.3 | `schemas/contracts.py` (Pydantic v2) | 2h | Alle Request/Response-Modelle inkl. `Verdict` Enum |
| 1.4 | `services/rent_engine.py` aus Frontend-Logik portieren | 4h | Determinismus-Tests grün, gleiche Outputs wie Frontend |
| 1.5 | `api/routes.py` mit Stub-Endpoints + Health | 2h | `GET /api/health` liefert 200, OpenAPI-Docs erreichbar |
| 1.6 | `pytest` Setup + erste Tests (`test_rent_engine.py`) | 2h | `pytest` grün, Coverage ≥80% für rent_engine |
| 1.7 | `Dockerfile` + `.dockerignore` | 1h | `docker build` <30s, Image <300 MB |
| 1.8 | GitHub Actions CI (lint + test) | 2h | Bei Push auf `main` läuft CI grün |

**Deliverable**: `git tag v0.1.0`, lokal startbar via `docker run -p 8000:8000`

### Phase 2 — Mistral-Integration (Tag 8-14)
**Ziel**: `/api/parse` und `/api/widerspruch` funktionieren mit echten Mietverträgen.

| # | Task | Aufwand | Akzeptanzkriterium |
|---|------|---------|--------------------|
| 2.1 | `integrations/mistral.py` (MistralClient async) | 4h | OCR + Chat-JSON gegen Mistral-Test-Account |
| 2.2 | `services/pdf_parser.py` (OCR + pdfplumber-Fallback) | 4h | 3 verschiedene Test-PDFs (digital, Scan, mehrseitig) korrekt extrahiert |
| 2.3 | `services/widerspruch.py` (LLM-Brief-Generator) | 4h | Brief mit korrekten Zahlen + §-Verweisen, keine Halluzinationen |
| 2.4 | `services/cache.py` (Redis + in-memory Fallback) | 2h | Cache-Hit spart 100% LLM-Tokens |
| 2.5 | Rate-Limiting (`slowapi` o.ä.) | 2h | Max 10 PDF-Uploads/min/IP |
| 2.6 | Integration-Tests (`test_routes.py`) | 4h | End-to-End `/api/parse` mit echtem Test-PDF |
| 2.7 | Token-Budget-Tracking + 429-Handling | 2h | Bei `mistral_api_error` → 503 mit Retry-After |

**Deliverable**: `git tag v0.2.0`, Demo-Skript `scripts/demo.sh` verarbeitet Test-Mietvertrag end-to-end

### Phase 3 — Frontend-Integration (Tag 15-21)
**Ziel**: User kann auf der Astro-Site PDF hochladen und Widerspruch per Mail-Client öffnen.

| # | Task | Aufwand | Akzeptanzkriterium |
|---|------|---------|--------------------|
| 3.1 | `frontend/src/components/Uploader.astro` (Drag&Drop + Preview) | 4h | UX: <3 Klicks bis Upload, Progress-Bar |
| 3.2 | `frontend/src/pages/check.astro` (Wizard: Upload → Result) | 6h | Flow funktioniert mit Mock-Backend, dann echtem |
| 3.3 | `frontend/src/pages/widerspruch.astro` (Brief-Anzeige + Copy/Print) | 4h | Brief wird korrekt formatiert, Mailto-Link funktioniert |
| 3.4 | DSGVO-Hinweis-Banner auf allen AI-Pages | 1h | "Deine PDF wird nicht gespeichert" prominent |
| 3.5 | `vitest`-Setup für bestehende JS-Logik (`RentChecker.astro`) | 4h | Smoke-Tests für die Frontend-Scoring-Funktion |
| 3.6 | E2E-Test (`playwright`): Upload → Result → Brief | 6h | Playwright-Test grün im CI |

**Deliverable**: `git tag v0.3.0`, manuelle User-Tests mit 3 echten Test-Mietverträgen

### Phase 4 — Härtung & Launch (Tag 22-28)
**Ziel**: Produktiv-Deployment auf Hetzner, Monitoring, Doku.

| # | Task | Aufwand | Akzeptanzkriterium |
|---|------|---------|--------------------|
| 4.1 | `deploy/docker-compose.yml` (Backend + Caddy + Redis) | 4h | `docker compose up` startet alles auf `0.0.0.0:8000` |
| 4.2 | Caddyfile mit Auto-TLS (Let's Encrypt) | 2h | `https://api.mietspiegel-ai.de` erreichbar |
| 4.3 | Health-Check-Endpoint + Uptime-Kuma-Watchdog | 2h | Alarm-Telegram bei 5xx über 2 min |
| 4.4 | Logging-Aggregation (loki oder simpler journald) | 3h | Logs zentral durchsuchbar |
| 4.5 | `README.md` mit Use-Cases, API-Referenz, Screenshots | 4h | Externe Developer verstehen Service in <10 min |
| 4.6 | `CONTRIBUTING.md`, `LICENSE` (MIT), `CODE_OF_CONDUCT.md` | 2h | Repo ist OSS-ready |
| 4.7 | Lasttest (`locust`): 50 concurrent users, 5 min | 3h | p95 <2s für `/api/check`, <8s für `/api/parse` |
| 4.8 | Sicherheits-Audit (`bandit`, `pip-audit`) | 2h | 0 high/critical CVEs |

**Deliverable**: `git tag v1.0.0`, Production-URL live, Hacker-News-Post vorbereitet

## Daten-Pipeline

### Phase 1 — Seed-Daten (statisch)

`backend/data/cities.json`:
- 79 deutsche Städte mit Ø-Miete/m² + Quelle + Bundesland
- 10 deutsche Großstädte mit Bezirks-Detail (aus `districts.json`)

Loader: `services/data_loader.py` → in-memory Dict, O(1) Lookup.

### Phase 2 — RAG-Quelle (Stufe 3, später)

16 offizielle Mietspiegel-PDFs (Berlin, Hamburg, München, Köln, etc.)
→ Chunks + Mistral-Embeddings → FAISS-Index.

Im Frontend nicht relevant — Backend liefert präzisere Schätzungen.

## Risikoregister

| Risiko | Wahrscheinlichkeit | Impact | Mitigation |
|--------|-------------------|--------|------------|
| Mistral-API ändert Schema | Mittel | Hoch | Defensive Parsing, `pydantic.ValidationError` als 422 |
| LLM halluziniert Zahlen im Widerspruch | Mittel | Kritisch | Temperatur 0.1, System-Prompt "KEINE erfundenen Zahlen", Test-Suite mit 10 Beispielen |
| PDF-OCR fehlerhaft bei Handschrift | Hoch | Mittel | Klare Fehlermeldung, Hinweis auf manuelle Eingabe |
| Rate-Limit durch Mistral (5 req/s) | Mittel | Mittel | Token-Bucket + Cache + Wartezeit-Backoff |
| DSGVO-Verstoß bei PII-Speicherung | Niedrig | Kritisch | Kein Disk-Write, structlog ohne Inhalte, Audit-Log alle 30 Tage |
| Frontend SEO-Ranking bricht durch neue Pages | Niedrig | Mittel | AI-Pages haben `noindex`, statische Mietspiegel-Pages bleiben 1:1 |

## Test-Strategie

### Unit-Tests (`pytest`, Ziel: ≥85% Coverage)

```
backend/tests/
├── test_rent_engine.py        # 12 Tests: Baujahr/Wohnlage/Verdict-Logik
├── test_schemas.py            # 8 Tests: Pydantic-Validation, Edge-Cases
├── test_pdf_parser.py         # 6 Tests: Mocked Mistral, Fallback-Pfad
├── test_widerspruch.py        # 5 Tests: Brief-Inhalt, Paragraph-Liste
├── test_cache.py              # 4 Tests: Redis-Mock + in-memory
└── test_routes.py             # 10 Tests: HTTP-Integration, Errors
```

### Frontend-Tests (`vitest`)

```
frontend/src/components/__tests__/
├── RentChecker.spec.ts        # 8 Tests: Scoring-Funktionen
└── Uploader.spec.ts           # 4 Tests: File-Validation
```

### E2E (`playwright`)

```
e2e/
├── upload-flow.spec.ts        # Happy Path: Upload → Result → Brief
└── error-flow.spec.ts         # Falsche Datei, zu groß, Server-Down
```

### Manuelle Smoke-Tests (Phase 2)

3 echte, anonymisierte Mietverträge aus dem Bekanntenkreis:
1. **Berlin-Altbau** (90m², 1400€ Kalt, 1910) → erwartet: tolerance (10-20%)
2. **München-Neubau** (45m², 1100€ Kalt, 2018) → erwartet: fair
3. **Hamburg-70er-Jahre** (60m², 950€ Kalt, 1975) → erwartet: brake (20-50%)

## Subagent-Strategie

Da Michael Solo-Entwickler ist und Vibe CLI als Subagent verfügbar:

| Task-Typ | Manuell | Vibe-CLI |
|----------|---------|----------|
| Architektur-Entscheidungen | ✓ | ✗ |
| Scoring-Logik | ✓ | ✗ |
| Boilerplate (Pydantic-Schema, Routes) | ✗ | ✓ |
| Test-Fixtures generieren | ✗ | ✓ |
| README-Texte | ✗ | ✓ (mit Review) |
| Final-Code-Review | ✓ | ✗ |

**Workflow für Vibe-Jobs**:
```bash
cd /opt/data/workspace/mietspiegel-ai
vibe -p "$(cat tasks/wid_brief.md)" \
     --auto-approve --max-turns 15
```

## Meilensteine

- **M1** (Tag 7): `v0.1.0` — Backend lokal lauffähig, Tests grün
- **M2** (Tag 14): `v0.2.0` — Echte Mietverträge analysiert
- **M3** (Tag 21): `v0.3.0` — Frontend-Integration fertig, E2E-Test grün
- **M4** (Tag 28): `v1.0.0` — Produktiv deployed, OSS-ready

## Nächste sofortige Aktionen

1. ✅ Skeleton existiert unter `/opt/data/workspace/mietspiegel-ai/backend/`
2. ⏳ `requirements.txt` finalisieren + Virtualenv aufsetzen
3. ⏳ `pytest` ausführen — Validierung der rent_engine-Logik
4. ⏳ GitHub-Repo `mietspiegel-ai` anlegen + Initial-Commit
5. ⏳ Mistral-Account für Test-Calls (Rate-Limits beachten)
6. ⏳ Erste echte Test-PDF anonymisieren + ins `tests/fixtures/` legen