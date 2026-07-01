# Mietspiegel-AI

Lokaler, DSGVO-konformer Mietpreis-Check mit Widerspruchsgenerator. Lade deinen
Mietvertrag als PDF hoch, prüfe Adresse & Miete, erfahre sofort, ob deine Miete
ortsüblich ist — und falls nicht, generiere ein fertiges Widerspruchsschreiben
nach §558 / §556d BGB bzw. §5 WiStrG (Wucher).

**Architektur**: Zwei kleine Services, keine Cloud, keine Tracker.

```
mietspiegel-ai/
├── backend/    FastAPI + Mistral API (OCR + Reasoning), Port 8765
└── frontend/   Astro 5 Static Site, Port 4321
```

## Features

- 📄 **PDF-Upload** → Mistral OCR extrahiert Adresse, Miete, m², Baujahr, Wohnlage
- 🏙️ **5 Städte** vorinitialisiert (Berlin, Hamburg, München, Köln, Frankfurt)
- ⚖️ **4 Verdict-Stufen**: fair / tolerance (≤+10%) / brake (≤+50%) / wucher (>+50%)
- 📝 **Widerspruchsgenerator** (geplant: §558 BGB Senkungsschreiben)
- 🔒 **Selbstgehostet**, API-Keys nur in `/opt/data/.env`, keine Logs nach extern

## Quickstart

### Backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

# Mistral-Key setzen (oder in .env schreiben)
export MISTRAL_API_KEY=sk-...

# Tests
pytest tests/ -v                 # 31/31 grün

# Server
uvicorn app.main:app --host 127.0.0.1 --port 8765
```

### Frontend

```bash
cd frontend
npm install
npm run build                    # statisches dist/
cd dist && python3 -m http.server 4321
```

Browser: **http://localhost:4321**

### Tailscale-Mesh

Vom Mac/iPhone aus erreichbar via:
- `http://zimacube.taila1334a.ts.net:4321` (Frontend)
- `http://zimacube.taila1334a.ts.net:8765` (Backend, z.B. für Tests)

CORS ist explizit für `http://localhost:4321`, `http://zimacube:4321` und
`http://zimacube.taila1334a.ts.net:4321` freigeschaltet
(siehe `backend/app/core/config.py` → `allowed_origins`).

## API

| Endpoint | Methode | Zweck |
|---|---|---|
| `/api/health` | GET | Health-Check + `mistral_configured`-Status |
| `/api/cities` | GET | Liste der unterstützten Städte mit Ø m²-Preis |
| `/api/parse` | POST (multipart) | PDF-Upload → extrahierte Mietvertragsfelder |
| `/api/check` | POST (JSON) | Verdict: fair/tolerance/brake/wucher + fair_rent |
| `/api/widerspruch` | POST (JSON) | Widerspruchsschreiben-Generator |
| `/docs` | GET | OpenAPI/Swagger-UI (FastAPI auto) |

Beispiel-Request `/api/check`:

```bash
curl -X POST http://127.0.0.1:8765/api/check \
  -H "Content-Type: application/json" \
  -d '{
    "lease": {
      "address": "Friedrichstr 100, 10117 Berlin",
      "city": "Berlin",
      "cold_rent_eur": 850,
      "size_sqm": 60,
      "build_year": 1985,
      "location_quality": "mittel",
      "extras": []
    }
  }'
```

Antwort (gekürzt):
```json
{
  "ok": true,
  "verdict": "fair",
  "requested_rent_eur": 850.0,
  "fair_rent_eur": 793.8,
  "delta_percent": 7.08,
  "legal_basis": "§558 BGB (ortsübliche Vergleichsmiete)",
  "comparison": {"city": "Berlin", "city_average_eur_per_sqm": 13.5, ...},
  "recommendation": "..."
}
```

## Entwicklung

- **Backend-Tests**: 31 Tests in `backend/tests/` (pytest, asyncio)
- **Frontend-Build**: `npm run build` → `dist/`
- **TypeScript strict**, **Python 3.13** mit `from __future__ import annotations`

## Status

| Komponente | Status |
|---|---|
| Backend (FastAPI + Mistral) | ✅ läuft auf :8765 |
| Backend Tests (31) | ✅ grün |
| Frontend (Astro 5) | ✅ läuft auf :4321 |
| CORS Tailscale-Mesh | ✅ konfiguriert |
| Docker-Compose | ⏭️ übersprungen (ZimaOS hat keinen dockerd) |
| Widerspruchsgenerator-UI | ⏳ Backend fertig, UI pending |
| GitHub-Repo / Open-Source | ⏳ pending |

## Lizenz

Noch nicht festgelegt — bitte `LICENSE` hinzufügen vor erstem Push.
