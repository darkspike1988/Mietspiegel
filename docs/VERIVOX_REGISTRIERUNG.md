# Verivox-Partnerprogramm: Vorarbeit & Registrierungs-Checkliste

Ziel: Echte Strom-/Gas-/DSL-/Mobilfunk-Tarife in der Webapp — offiziell,
mit Provision (~55 €/Sale Strom&Gas), ohne Scraping.

## Was bereits gebaut ist (Fertig nach Registrierung)

| Baustein | Status |
|---|---|
| `verivox_provider.py` | ✅ URL-Builder für Strom/Gas/DSL/Mobilfunk je PLZ, mit/ohne Partner-ID |
| `GET /api/versorger/tarifrechner?product=strom&postal_code=10117` | ✅ Live — liefert Tracking-Link sobald `VERIVOX_PARTNER_ID` gesetzt ist |
| `GET /api/versorger/strompreise` | ✅ Live — SMARD-Börsenpreise (BNetzA, CC BY 4.0) + günstigste/teuerste Stunden |
| `.env.example` | ✅ `VERIVOX_PARTNER_ID=` vorbereitet |
| Tests | ✅ 10 Provider-Tests (URL-Formen, ENV-Handling, Fallbacks) |

Ohne Partner-ID funktioniert der Tarifrechner-Link bereits (öffentlicher
Vergleich) — nur ohne Provision. Nach der Registrierung also: **nur die
ENV-Variable setzen, fertig.**

## Registrierungs-Checkliste (ca. 15–30 Min)

1. **Registrieren** (kostenlos):
   - Direkt: https://www.verivox.de/partnerprogramm/
   - Oder über das Partnernetzwerk **Awin** (Verivox = Merchant 14797):
     https://ui.awin.com/merchant-profile/14797
   - Ansprechpartner: verivox.affiliate@dentsu.com

2. **Benötigte Angaben** (vorbereiten):
   - [ ] Webseiten-URL (deine App-Domain)
   - [ ] Impressum (deine App hat `/impressum` vor — Link bereithalten)
   - [ ] Datenschutz (Link auf die DSGVO-Erklärung)
   - [ ] Gewerbe: Privatperson mit "kommerziellem Interesse an der
     Vermittlung" ist laut Verivox-FAQ ausreichend; bei Unsicherheit
     reicht die Anmeldung als Publisher über Awin.

3. **Nach der Freischaltung**:
   - [ ] Partner-ID aus dem Partner-/Awin-Dashboard kopieren
   - [ ] In `backend/.env` eintragen: `VERIVOX_PARTNER_ID=deine-id`
   - [ ] Backend neu starten
   - [ ] Test: `GET /api/versorger/tarifrechner?product=strom&postal_code=10117`
     → URL enthält jetzt `&partner=deine-id`

4. **Werbemittel** (aus dem Partner-Dashboard):
   - [ ] **iframe-Tarifrechner** (White-Label): HTML-Schnipsel kopieren
     und auf `/versorger` einbetten — dann läuft der komplette
     Tarifvergleich inkl. Abschlussstrecke direkt in der App
   - [ ] Deep-Links für Strom/Gas/DSL/Mobilfunk (unsere API nutzt diese)
   - [ ] Die exakte Form des Tracking-Parameters (subid/aff/etc.)
     dokumentiert Verivox im Dashboard → ggf. den Parameter-Namen in
     `verivox_provider.py` (Zeile `&partner=`) anpassen

5. **Wichtig zu wissen**:
   - Vergütung Strom&Gas: nach geprüftem, vollständigem Anbietervertrag
   - DSL/Mobilfunk: nach Widerrufsfrist-Bestätigung
   - Nicht-vergütete Grundversorgungstarife werden mit Tracking-Link
     ausgeblendet (Verivox-Policy)
   - Provision kann die Server-/Hosting-Kosten decken → passt zum
     "kostenlos für Mieter"-Modell

## Abgrenzung: Energie Webservice (NICHT für uns nötig)

Der "Energie Webservice" (https://www.verivox.de/marktdaten/energie/energiewebservice/)
ist ein **bezahltes B2B-Produkt für Energieversorger** (White-Label-
Rechner mit eigener Tarifdatenbank, Kündigung 6 Wochen zum Quartalsende).
Erst relevant, wenn wir eigene Tarif-Tabellen ohne iframe rendern
wollen. Für den Start reicht das kostenlose Partnerprogramm völlig.

## Zukunft: Check24

Check24 hat ein paralleles Partnerprogramm (gleiche Mechanik). Nach
Verivox-Onboarding kann derselbe Provider doppelt angebunden werden
(`CHECK24_PARTNER_ID`), sodass Mieter beide Vergleiche sehen.
