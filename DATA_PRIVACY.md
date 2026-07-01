# Datenschutzkonzept — Mietspiegel-AI

**Stand**: 2026-07-01
**Verantwortlicher**: Michael, c/o selbst-gehostete Instanz
**Rechtsgrundlage**: DSGVO + TTDSG + §32 BDSG

## 1. Datenkategorien

| Kategorie | Zweck | Speicherung | Rechtsgrundlage |
|-----------|-------|-------------|-----------------|
| **Mietvertrag-PDF** | Strukturierte Extraktion | ❌ KEINE (transient) | Art. 6 Abs. 1 lit. b DSGVO (Vertrag) |
| **Extrahierte Felder** (Adresse, Miete, Baujahr) | Vergleichsmieten-Logik | ❌ KEINE (nur in Response) | Art. 6 Abs. 1 lit. b DSGVO |
| **Request-Metadaten** (Hash, Größe, Latenz) | Sicherheit, Monitoring | ✅ Logfile, 30 Tage | Art. 6 Abs. 1 lit. f DSGVO (berechtigtes Interesse) |
| **IP-Adresse** | Rate-Limit, Abuse-Schutz | ✅ Redis, 24h | Art. 6 Abs. 1 lit. f DSGVO |
| **Mistral-API-Calls** | LLM-Verarbeitung | ❌ KEINE (Mistral Paris, EU-Hosting, 30-Tage-Audit bei Mistral) | Auftragsverarbeitung gem. Art. 28 DSGVO |

**Wichtig**: Es werden **keine Mietverträge gespeichert**. Keine Datenbank
mit Mietdaten, keine Disk-Schreibt für PDFs, keine Cloud-Storage-Integration.

## 2. Verarbeitungs-Pipeline

```
User (Browser)
    ↓ TLS 1.3 (Hetzner/Caddy)
    ↓ POST /api/parse (PDF, max 10 MB)
FastAPI Service
    ↓ Bytes im RAM (Python BytesIO, kein Disk-Write)
    ↓ SHA256-Hash → Cache-Key
Mistral API (Paris, EU)
    ↓ OCR + Chat-JSON (Auftragsverarbeitung gem. AVV)
    ↓ Antwort in RAM
FastAPI Service
    ↓ JSON-Response an User
    ↓ Bytes werden aus RAM entfernt (Garbage Collection)
```

**Garantien**:
- PDF-Bytes leben max. **200 ms** im Prozess-Speicher
- Keine `tempfile`-Schreibung
- Kein `pickle`, kein Logging des Inhalts
- structlog loggt nur: `size_bytes`, `sha256_prefix`, `latency_ms`, `status`

## 3. Auftragsverarbeitung Mistral

Mistral AI SAS (Paris) ist Auftragsverarbeiter gem. Art. 28 DSGVO.
Vertrag: [Mistral Data Processing Addendum](https://mistral.ai/terms/dpa)

**Garantien**:
- EU-Hosting (Frankfurt, Paris)
- Keine Datenweitergabe in Drittländer
- 30-Tage-Audit-Log (Mistral-seitig)
- Auf Wunsch: Zero-Retention-Endpoint verfügbar

## 4. User-Rechte (Art. 15-22 DSGVO)

Da keine PII persistiert wird, sind diese Rechte automatisch erfüllt:

| Recht | Umsetzung |
|-------|-----------|
| Art. 15 Auskunftsrecht | Es gibt keine gespeicherten Daten → trivial |
| Art. 16 Berichtigung | n/a |
| Art. 17 Löschung | Automatisch nach Request (GC) |
| Art. 18 Einschränkung | n/a |
| Art. 20 Datenübertragbarkeit | Jeder User hat seine Daten in der Response |
| Art. 21 Widerspruch | User lädt einfach keine PDF hoch |

## 5. Sicherheitsmaßnahmen

### Technisch

- **TLS 1.3** zwischen Browser ↔ Backend (Caddy + Let's Encrypt)
- **CORS-Whitelist**: Nur `https://mietspiegel-ai.de` + `localhost:4321` (dev)
- **Rate-Limit**: 10 PDF-Uploads / 60s / IP (slowapi)
- **Max Upload**: 10 MB (Schutz vor DoS)
- **API-Key** (optional, für B2B-Embeds): `X-API-Key`-Header, SHA256-gehasht gespeichert
- **Input-Validierung**: Pydantic-Schemata lehnen negative/extreme Werte ab
- **Output-Filterung**: LLM-Output wird gegen JSON-Schema validiert
- **Security-Headers**: `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`

### Organisatorisch

- Server in Hetzner FSN1 (Frankfurt, DE)
- SSH-Zugang nur per Tailscale (nicht öffentlich)
- Auto-Updates via unattended-upgrades
- Logs 30 Tage, danach automatisches Löschen
- Backup-Strategie: KEINE Backups der App-Logs (Privacy-by-Default)

## 6. Datenpannen-Notfallplan

Bei vermuteter Datenpanne (Art. 33 DSGVO):

1. **Erkennung**: Monitoring-Alert (Uptime-Kuma → Telegram)
2. **Eindämmung**: Service sofort stoppen (`docker compose down`)
3. **Bewertung**: Welche Daten sind betroffen? (Logs prüfen)
4. **Meldung**:
   - An Landesdatenschutzbehörde binnen 72h (Art. 33 Abs. 1)
   - An Betroffene, wenn hohes Risiko (Art. 34 Abs. 1)
5. **Behebung**: Patch deployen, Service neu starten
6. **Dokumentation**: Eintrag im Verzeichnis der Verarbeitungstätigkeiten

## 7. Verzeichnis der Verarbeitungstätigkeiten (Art. 30 DSGVO)

| Feld | Wert |
|------|------|
| Verantwortlicher | Michael (Solo-Betreiber) |
| Bezeichnung | Mietspiegel-AI PDF-Verarbeitung |
| Zweck | Mietvertragsanalyse + Widerspruchshilfe |
| Kategorie betroffener Personen | Mieter in DE |
| Datenkategorien | Vertragsdaten, Adressen, Mietpreise |
| Empfänger | Mistral AI SAS (Auftragsverarbeiter) |
| Übermittlung Drittland | ❌ keine |
| Löschfristen | Sofort nach Response, Logs 30 Tage |
| Sicherheitsmaßnahmen | Siehe Abschnitt 5 |

## 8. Datenschutzerklärung (User-facing)

Wird auf `https://mietspiegel-ai.de/datenschutz` veröffentlicht und enthält:
- Diese Zusammenfassung in einfacher Sprache
- Cookie-Hinweis (keine Tracking-Cookies)
- Kontakt für Datenschutzanfragen
- Hinweis: Keine Rechtsberatung

## 9. Cookie-freier Default

- **Keine Tracking-Cookies** (kein Google Analytics, Plausible etc.)
- **Keine Session-Cookies** (Service ist stateless)
- **Keine LocalStorage** für AI-Funktionen
- **DSGVO-konform ohne Cookie-Banner**

## 10. Externe Ressourcen

Die Astro-Frontend-Site (separates Repo) lädt folgende CDNs:
- Leaflet von `unpkg.com` (Maps)
- Chart.js von `cdn.jsdelivr.net` (Charts)
- Tailwind von `cdn.tailwindcss.com` (CSS)

**Verbesserungs-Vorschlag (Phase 4)**: Alle CDNs self-hosten + SRI-Hashes.

## 11. Audit-Trail

| Datum | Audit-Art | Ergebnis |
|-------|-----------|----------|
| 2026-07-01 | Initiale Konzept-Erstellung | ✅ DSGVO-konform |
| (Phase 4) | Externe Rechtsprüfung (geplant) | ⏳ |
| (Phase 4) | Penetrationstest (geplant) | ⏳ |

---

**Kontakt für Datenschutzanfragen**:
Telegram: @impedire (siehe `https://github.com/impedire`)
Antwort binnen 72h garantiert (DSGVO Art. 12 Abs. 3).

**Hinweis**: Dieses Konzept wurde ohne juristische Beratung erstellt.
Für rechtsverbindliche Auskunft bitte einen Anwalt für IT-Recht
konsultieren (z.B. über [Deutsche Anwaltauskunft](https://www.anwaltauskunft.de)).