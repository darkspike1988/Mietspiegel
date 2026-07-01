# Mietspiegel-AI · Frontend

Astro 5 Static-Site. Lädt PDFs hoch, ruft das Backend (`/api/parse` + `/api/check`),
zeigt den Verdict mit Begründung und Empfehlung an.

## Stack

- **Astro 5** (Static Site, kein SSR, keine Hydration)
- **Vanilla TypeScript** in inline `<script>`-Blöcken (kein React/Vue/Svelte)
- **Pure CSS** mit CSS-Variablen in `src/styles/global.css`
- **PDF-Upload**: natives `fetch()` + `FormData` → Backend

## Setup

```bash
npm install
cp .env .env.local   # optional: PUBLIC_BACKEND_URL anpassen
npm run dev          # Dev-Server auf http://localhost:4321
npm run build        # baut dist/ für Production
```

## Backend-Anbindung

`PUBLIC_BACKEND_URL` (siehe `.env`) zeigt per Default auf
`http://localhost:8765`. Für Tailscale-Mesh:
```
PUBLIC_BACKEND_URL=http://zimacube:8765
```

CORS-seitig muss das Backend den Frontend-Origin erlauben — die Liste steht in
`../backend/app/core/config.py` → `allowed_origins`.

## Komponenten

| Datei | Zweck |
|---|---|
| `src/layouts/Layout.astro` | HTML-Shell, Title, Header/Footer |
| `src/pages/index.astro` | Landing-Page mit `<PdfUploader />` |
| `src/components/PdfUploader.astro` | PDF-Upload + Edit-Form + Result-View |
| `src/styles/global.css` | Theme, Form-Styles, Verdict-Badges |

## Build-Verhalten

`npm run build` erzeugt eine reine statische Seite (`output: "static"`).
Die `<script>`-Blöcke werden von Vite gebundlet und gehasht.
Keine Hydration, keine Client-JS-Framework-Kosten.

## Browser-Support

Letzte 2 Chrome/Firefox/Safari/Edge-Versionen. Nutzt `fetch`, `FormData`,
`URL.createObjectURL` — alles seit Jahren Standard.
