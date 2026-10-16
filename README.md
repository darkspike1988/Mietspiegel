> **Projektstatus (28.09.2026):** Die aktive Entwicklung von Web-App und API wurde im privaten Repository `darkspike1988/mietspiegel-app` zusammengeführt. Dieses öffentliche Repository bleibt als bisheriger Quellstand und für seine Versionsgeschichte bestehen.

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

## 🚀 Kostenlos hosten (Fertig-zum-Hosten-Anleitung)

Die App besteht aus einem **statischen Frontend** und einem **Python-Backend**.
Beides lässt sich gemeinsam kostenlos betreiben — hier die einfachsten Wege:

### Variante A: Komplett kostenlos (empfohlen für den Start)

1. **Frontend (Astro)** → statisches `dist/` bauen und auf einen statischen Host pushen:
   - **GitHub Pages**: Repo → Settings → Pages → `GitHub Actions` (Astro deploy) — kostenlos
     ```bash
     cd frontend
     npm run build          # erzeugt dist/
     ```
   - Alternativ: **Cloudflare Pages** oder **Netlify** (免费 Tier) — `dist/` als Root
2. **Backend (FastAPI)** → kostenlos hosten auf:
   - **Fly.io** (free tier, ~3 shared VMs) oder **Render** (free web service)
     ```bash
     cd backend
     # Dockerfile ist vorhanden: docker build -t mietspiegel-ai .
     ```
3. **Frontend mit Backend verbinden**: In `frontend/.env` setzen:
   ```
   PUBLIC_BACKEND_URL=https://dein-backend.fly.dev
   ```
   Dann `npm run build` — die URL wird ins statische Bundle gebacken.

> **Hinweis**: Die deterministischen Werkzeuge (`/api/tools/*`) laufen **ohne
> LLM und ohne API-Key** — sie funktionieren also auch komplett ohne
> `MISTRAL_API_KEY` und verursachen **0 € Betriebskosten**. Nur die
> PDF-Extraktion und der Widerspruchsgenerator brauchen den (optionalen)
> Mistral-Key.

### Variante B: Nur statisch (0 € Backend, eingeschränkt)

Alle Ratgeber-Seiten (`/handbuch`, `/gesetze`) und Spenden-Links laufen
komplett ohne Backend. Der Mietpreis-Check und die Werkzeuge brauchen das
Backend — offline/self-hosted immer möglich (siehe oben, `uvicorn`).

## 💝 Unterstützen

Entwickelt als privates Open-Source-Projekt für Mieter. Alle Werkzeuge
bleiben kostenlos. Wenn dir das Projekt hilft:

- **Patreon**: <https://www.patreon.com/c/DARKSPIKE1988>
- **PayPal**: <https://www.paypal.com/paypalme/darkspike1988>

(Die Spenden-Links findest du auch auf jeder Ratgeber-Seite im Footer-Bereich.)

## 📚 Ratgeber & Werkzeuge

| Seite | Inhalt |
|---|---|
| `/` | Mietpreis-Check (PDF → Verdict) |
| `/werkzeuge` | Nebenkosten · Kaution · Mieterhöhung · **Kautionsanbieter-Vergleich** |
| `/handbuch` | **Handbuch Mietwucher**: Erkennen, Maschen großer Konzerne, Klage-Leitfaden, Videos, Open-Source-Tools |
| `/gesetze` | **Gesetze & Politik**: schematisch Bund → Land → Kommune, mit Links zu Original-Texten |
| `/versorger` | **Strom, Gas & Internet**: Tarif-Finder (PLZ → Verivox+Check24+Idealo auf einen Klick), Sparrechner, Preis-Beobachtungen + Portal-Ranking-Statistik (Median), SMARD-Live-Strompreise (Börsenpreis je Stunde/Wochentag) — siehe **Preisgarantie-Fallstricke, Betrugsmaschen, Glasfaser/Kabel/DSL/LTE-Statistik, Wechsel-Checkliste |
| `/suche` | **Wohnungssuche-Strategie + Such-Agent-UI**: Profile anlegen (Such-URL aus ImmoScout/Immowelt/Kleinanzeigen/WG-Gesucht einfügen), Live-Refresh mit Mietpreis-Badges (✅🟡🟠🔴), persistente Treffer-Liste, automatisches 10-Min-Polling + Telegram-Push |
 + Roadmap: Open-Source-Multi-Portal-Such-Agent (ImmoScout/Immowelt/Kleinanzeigen/WG-Gesucht), Duplikat-Erkennung, integrierter Mietpreis-Check, Mietbetrugs-Warnungen |
| `/wohnalltag` | **Handwerker, Umzugsfirmen & Hausordnungen**: Reparaturpflichten, Umzugs-Abzockmaschen, unwirksame Hausordnungs-Klauseln, GEZ, Schimmel-Minderung, Haustiere, Untervermietung, Übergabeprotokoll |

| API | Zweck |
|---|---|
| `POST /api/tools/nebenkosten` | NK-Abrechnung prüfen (Frist, Umlagefähigkeit, Arithmetik, Rückforderung) |
| `POST /api/tools/kaution` | Kaution prüfen (§551 BGB) |
| `POST /api/tools/kaution/vergleich` | **Kautionsbürgschaft-Anbieter vergleichen** (Daten: `data/processed/kautionsanbieter.json`, quartalsweise aktualisieren) |
| `POST /api/tools/mieterhoehung` | Mieterhöhung prüfen (§558 BGB) |
| `POST /api/tools/sparrechner` | Versorger-Sparpotenzial (Grundversorger vs. Tarif) |
| `POST /api/search/profiles` | Suchprofil persistent anlegen (SQLite) |
| `POST /api/search/refresh/{id}` | Profil abfragen: neue Treffer + Badge + Archiv + Telegram |
| `GET /api/search/listings/{id}` | Archivierte Treffer mit Badges |
| `POST /api/versorger/beobachtung` | Anonyme Preis-Beobachtung speichern |
| `GET /api/versorger/statistik` | Portal-Ranking nach Median |
| `GET /api/versorger/strompreise/statistik` | SMARD: günstigste/teuerste Stunde + Wochentag |

## Lizenz

Noch nicht festgelegt — bitte `LICENSE` hinzufügen vor erstem Push.
