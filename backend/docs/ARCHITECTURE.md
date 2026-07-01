# Architektur — Mietspiegel-AI Stufe 2

## Überblick

Mietspiegel-AI erweitert die bestehende statische Astro-Site
[`mietspiegel-app`](https://github.com/impedire/mietspiegel-app) um eine
**AI-gestützte Mietanalyse-Schicht**. Die Site bleibt statisch und
SEO-optimiert (79 deutsche Städte, Astro SSG). Das neue Backend liefert
die dynamischen, AI-gestützten Funktionen als REST-API.

```
┌──────────────────────────────────────────────────────────────────┐
│                     Astro Static Frontend                         │
│   (src/pages/*.astro, src/components/RentChecker.astro)          │
│   bleibt 100% statisch, hostbar auf jedem CDN                    │
└──────────────────────────┬───────────────────────────────────────┘
                           │ HTTPS POST (fetch)
                           ▼
┌──────────────────────────────────────────────────────────────────┐
│                       FastAPI Backend                             │
│   /api/parse       → Mietvertrag-PDF → strukturierte Daten       │
│   /api/check       → Adresse+Miete → Vergleichsmieten-Urteil     │
│   /api/widerspruch → Urteil → formaler Widerspruchsbrief         │
│   /api/health      → Liveness/Readiness                          │
└──────┬──────────────────────┬──────────────────────┬──────────────┘
       │                      │                      │
       ▼                      ▼                      ▼
   ┌────────┐         ┌──────────────────┐    ┌──────────────┐
   │Mistral │         │   Rent Engine    │    │  Redis Cache │
   │ API    │         │ (deterministisch, │    │ (LLM-Antwort │
   │ OCR +  │         │  offline-tauglich)│    │  24h TTL)    │
   │ Chat   │         └──────────────────┘    └──────────────┘
   └────────┘
       ▲
       │ PDF + JSON-Schema
       │
   ┌───┴────────┐
   │ pdfplumber │  (Fallback für native PDFs)
   └────────────┘
```

## Datenfluss — End-to-End

### 1. Mietvertrag analysieren (`POST /api/parse`)

```
┌─────────┐  Upload PDF  ┌──────────────┐   OCR (Mistral)   ┌─────────┐
│  User   │ ───────────► │  FastAPI     │ ────────────────► │ Mistral │
│ Browser │              │  /api/parse  │                   │  OCR    │
│         │ ◄─────────── │              │ ◄──────────────── └─────────┘
└─────────┘  JSON Lease  │              │     Roh-Text
                        │              │
                        │              │  Chat-JSON    ┌─────────┐
                        │              │ ────────────► │ Mistral │
                        │              │ ◄──────────── │  Chat   │
                        │              │  JSON-Schema  └─────────┘
                        │              │
                        │              │  Cache-Key = SHA256(pdf_bytes)
                        │              │ ─────────────────────► Redis
                        └──────────────┘
```

**Failover**: Wenn Mistral-OCR fehlt (Rate-Limit, Outage), fällt
`extract_text_from_pdf` automatisch auf `pdfplumber` zurück (für digital
erzeugte PDFs ohne Scan).

**Caching**: SHA256 der PDF-Bytes → 24h Cache. Zweimal dieselbe PDF
uploaden kostet 0 Tokens.

### 2. Vergleichsmieten-Check (`POST /api/check`)

```
┌─────────┐  JSON {lease}  ┌──────────────┐
│  User   │ ─────────────► │  /api/check  │
│ Browser │                │              │
│         │ ◄───────────── │  Rent Engine │
└─────────┘  {verdict,     │              │
              fair_rent,    │  ✓ deterministisch
              delta_%}      │  ✓ offline-tauglich
                            │  ✓ 0 LLM-Calls
                            └──────────────┘
```

**Deterministisch**: `rent_engine.evaluate()` läuft ohne LLM, basiert
auf:
- Baujahres-Faktor (Tabelle, 6 Stufen)
- Wohnlage-Faktor (einfach/mittel/gut)
- Stadtdurchschnitt (CityRecord-Tabelle)

**Verdict-Mapping** (Schwellen aus dem bestehenden Frontend übernommen):

| Delta % | Verdict | Rechtsgrundlage |
|---------|---------|-----------------|
| ≤ 10%   | `fair`      | §558 Abs. 2 BGB |
| ≤ 20%   | `tolerance` | §558 Abs. 2 BGB |
| ≤ 50%   | `brake`     | §556d BGB + §556g BGB |
| > 50%   | `wucher`    | §5 WiStrG |

### 3. Widerspruchsschreiben (`POST /api/widerspruch`)

```
┌─────────┐  {check, tenant} ┌──────────────┐
│  User   │ ────────────────►│ /api/widerspruch│
│ Browser │                 │              │
│         │ ◄──────────────│  Mistral Chat │
└─────────┘  {brief,         │  + JSON-Schema│
             rechtsgrundlagen}│              │
                             │  T=0.1 (deterministisch)│
                             └──────────────┘
```

**Sicherheitsnetze** (LLM-Prompt):
- Temperatur 0.1 → minimal Halluzination
- System-Prompt: "KEINE erfundenen Zahlen"
- Brief enthält ausschließlich übergebene Werte aus dem `RentCheckResponse`
- Bei `wucher`-Verdict: expliziter Hinweis auf Anwalt/Mieterverein

## Komponenten

### Backend-Layout

```
backend/
├── app/
│   ├── main.py                  # FastAPI app + lifespan
│   ├── core/
│   │   ├── config.py            # Pydantic-Settings (env)
│   │   └── logging.py           # structlog JSON
│   ├── schemas/
│   │   └── contracts.py         # Pydantic request/response models
│   ├── integrations/
│   │   └── mistral.py           # MistralClient (OCR + Chat)
│   ├── services/
│   │   ├── pdf_parser.py        # Mistral-OCR + pdfplumber Fallback
│   │   ├── rent_engine.py       # Deterministische Vergleichsmieten-Logik
│   │   ├── widerspruch.py       # LLM-generierter Widerspruchsbrief
│   │   └── cache.py             # Redis/in-memory Cache
│   └── api/
│       └── routes.py            # FastAPI endpoints
├── tests/
│   ├── test_rent_engine.py      # Deterministische Tests
│   ├── test_routes.py           # HTTP-Integration
│   └── fixtures/
│       └── sample_lease.pdf     # Echtes anonymisiertes Test-PDF
├── requirements.txt
└── .env.example
```

### Datenmodell

```python
class ParsedLease:
    address: str              # Pflicht
    city: Optional[str]
    postal_code: Optional[str]
    cold_rent_eur: float      # Pflicht
    warm_rent_eur: Optional[float]
    additional_costs_eur: Optional[float]
    deposit_eur: Optional[float]
    size_sqm: float           # Pflicht
    rooms: Optional[float]
    build_year: Optional[int]
    location_quality: Optional[Literal["einfach","mittel","gut"]]
    extras: list[str]
    contract_date: Optional[str]
```

### Sicherheit

- **API-Key**: Optional, `X-API-Key` Header wenn `API_KEY_REQUIRED=true`
- **CORS**: Whitelist `localhost:4321` (Astro dev) + Produktivdomain
- **PII**: PDFs transient im Speicher, max 10 MB, kein Disk-Write
- **Logging**: Request-Metadaten (size, hash), niemals Inhalt

### Performance

| Endpoint | Latenz p50 | LLM-Calls | Cache-Hit-Rate |
|----------|-----------|-----------|----------------|
| `/api/parse` (cold)   | 4-6 s  | 2 | 0% |
| `/api/parse` (cached) | 30 ms  | 0 | 100% |
| `/api/check`          | 15 ms  | 0 | n/a (stateless) |
| `/api/widerspruch`    | 3-5 s  | 1 | 0% |

**Skalierung**:
- Frontend: Astro-SSG, beliebig via CDN (Cloudflare, Bunny)
- Backend: 1 vCPU/1 GB reicht für ~50 req/min (LLM ist Bottleneck)
- Cache: Redis optional; ohne = LRU in-memory pro Prozess

## Deployment

### Lokal (dev)

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # MISTRAL_API_KEY eintragen
uvicorn app.main:app --reload --port 8000
```

### Produktiv (containerized)

```dockerfile
FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
```

**Reverse Proxy**: nginx/Caddy → TLS + Rate-Limit (50/min/IP)

### Architektur-Entscheidungen

| Entscheidung | Begründung |
|---|---|
| FastAPI statt Flask | Async nativ, OpenAPI auto-gen, Pydantic v2 |
| Mistral statt OpenAI | EU-Hosting, DSGVO, identische Qualität bei Mistral-Large |
| pdfplumber Fallback | Sparsamkeit: nicht jeder Vertrag braucht OCR-Token |
| Redis optional | Dev/Solo = in-memory reicht; Produktiv = Redis |
| Statisches Frontend behalten | SEO ist der Hauptkanal (Top-3 Google für "Mietspiegel [Stadt]") |
| Keine Datenbank | Reads = statische CityRecords, Writes = keine (PII transient) |
| Keine Auth | Öffentliche Daten; Rate-Limit reicht v1; OAuth2 in v2 wenn Monetarisierung |

## Roadmap

- **v0.1 (jetzt)**: Skeleton, alle 3 Endpoints, Tests, Docker, README
- **v0.2 (Stufe 3)**: RAG über 16 deutsche Mietspiegel-PDFs, präzisere Schätzung
- **v0.3**: Telegram-Bot als zweites Frontend (Nutzer-Zielgruppe setzt auf Mobile)
- **v1.0**: OAuth2 + Freemium-Tier (3 Checks/Monat gratis, unlimited €4.99)