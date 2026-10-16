# Gap-Analyse: Was fehlt bis zum fertigen Produkt

## ✅ Fertig gebaut
- Backend-Engines: Mietpreis, NK, Kaution, Mieterhöhung, Kautionsvergleich, Sparrechner
- Such-Agent: 4 Portale (ImmoScout Mobile API, Immowelt, Kleinanzeigen, WG-Gesucht) + Badge
- Versorger: Verivox-Partner-Vorarbeit, SMARD, Statistik (Portal-Ranking, Zeit-Aggregation)
- Frontend: 7 Seiten, Nav, Tools, Ratgeber
- 163 Tests

## ❌ Fehlt (Bau-Reihenfolge)
1. **Persistenz**: SQLite für Suchprofil-Listings + Tarif-Beobachtungen
   (aktuell in-memory = Daten weg nach Restart)
2. **Frontend /suche**: Such-Agent-UI (Profil anlegen → Ergebnisse mit Badges)
   — der Agent ist nur per API nutzbar, kein User kann ihn bedienen!
3. **Frontend /versorger**: Statistik-Widget (Ranking + SMARD) + Beobachtungs-Formular
   — Statistik ist nur per API erreichbar
4. **Scheduler**: Automatisches Polling der Profile (asyncio-Task)
5. **README**: neue Features dokumentieren
6. **CI**: GitHub Actions (Tests + Lint + Frontend-Build)

## 🔲 Später (nach Verivox-Freischaltung)
- White-Label-iframe aktivieren (ENV-only)
- Partner-API-Feed → Statistik-Store automatisch befüllen
- SQLite-Monatsverläufe der Beobachtungen
