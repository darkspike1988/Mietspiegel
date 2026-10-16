# Research: Wohnungssuche-Agent — Lösungsansätze der Open-Source-Welt

Stand: intensive Analyse von 6 Projekten, bevor wir den Agenten weiter ausbauen.

## Die 6 Projekte im Vergleich

| Projekt | Sprache | Ansatz | Anti-Bot-Strategie | Lizenz |
|---|---|---|---|---|
| **Fredy** (orangecoding) | Node.js | Web-UI, URL-Einfügen, **URL-Translator pro Portal**, **reverse-engineered ImmoScout Mobile API**, Dedup über Wohnungsdaten, Scam-Detection, Reisezeiten, MCP-Server | Residential Proxies bei Block; Mobile API statt HTML | Apache-2.0 + Commons Clause (keine OSI mehr, Kommerz-Verbot) |
| **Flathunter** (flathunters) | Python | config.yaml, **URL-Kopieren**, headless Chrome/Selenium, 2Captcha/Imagetyperz für Captchas, reese84-Cookie aus Browser kopieren als Workaround | Captcha-Solve-Dienste (~3 $/1000), Chrome-Simulation "menschenähnlich" | MIT |
| **ImmoPushr** (tldev-de) | PHP | Docker + Cron, Selenium + 2Captcha, URL-Kopieren, nur 1. Ergebnisseite (nach Datum sortiert) | 2Captcha; wenn Captcha ohne Token → Skip | GPL-3.0 |
| **ImmoSpider** (asmaier) | Python/Scrapy | URL-Kopieren, 10-Min-Intervall, SQLite "seen", E-Mail via SendGrid | Scrapy mit Standard-Headern (2024 noch funktionsfähig) | MIT |
| **flat_sniper** (Menschomat) | Python | config_default.yaml, **zufällige Wartezeiten** (min/max ms) damit Requests menschlicher wirken | Random-Delay, Telegram-Push | oA |
| **Va3elina/Wohnungsbot** | Python | Telegram-Bot, **IMMOSCOUT_CLIENT_ID/SECRET** (reverse-engineered Mobile-App-Credentials!), Immowelt mit **DataDome-Bypass-Keys**, SQLite | Mobile-API-Credentials + DataDome-Keys + Proxy | oA |

## Kern-Erkenntnisse: Es gibt genau 4 Lösungs-Muster

### Muster 1: URL-Kopieren + Polling (Industrie-Standard)
Alle 6 Projekte! Der Nutzer konfiguriert die Suche im Portal UI und
kopiert die URL. Warum: Portale ändern ihre URLs selten, aber ihr
Markup ständig — die URL ist der stabilste Kontrakt, den es gibt.

### Muster 2: RSS/Feeds nutzen (wo vorhanden, rechtssicher)
Manche Portale (v.a. Kleinanzeigen-Schnittstellen) bieten Feeds.
Fredy nutzt das nicht primär — aber unser RSS-First-Ansatz bleibt
für Verfügbarkeits-Portale wie immowelt-wg-gesucht sinnvoll.

### Muster 3: Mobile-API reverse-engineering (der mächtigste Trick)
- Fredy: ImmoScout **Mobile API** (die App selbst nutzt sie!)
- Va3elina: nutzt die (öffentlich extrahierbaren) App-Credentials
  `ImmobilienScout24-iPhone-Wohnen-AppKey` als CLIENT_ID
- Vorteil: umgeht den Browser-Anti-Bot-Komplex komplett
- Nachteil: bricht, wenn das Portal die App-API ändert; wartungsintensiv

### Muster 4: Captcha-Dienste + Browser-Simulation (aufwendigster Weg)
- Flathunter/ImmoPushr: Selenium + 2Captcha (~3 $/1000 Captchas)
- flat_sniper: Random-Delays für menschenähnliches Verhalten
- Flathunter-Workaround: reese84-Cookie aus dem echten Browser kopieren
- Nachteil: Kosten, Fragilität, Wartungsballast

## Empfehlung für unseren Agenten (Phi-Reihenfolge)

1. **RSS-First** (implementiert): rechtssicher, kein Wartungsaufwand
2. **HTML best-effort** (implementiert): für Portale ohne Feed, bewusst
   minimal; bei Block: sauber skippen wie ImmoPushr
3. **ImmoScout Mobile API** als Provider (Fredy-Muster): der einzige
   Weg, ImmoScout dauerhaft ohne Captcha-Dienst abzufragen. Mobile-API-
   Credentials sind aus der öffentlichen App extrahierbar.
4. **KEIN Captcha-Solving**: widerspricht dem "kostenlos"-Prinzip
   (3 $/1000) und erzeugt Wartungsballast. Fredy und der Va3elina-
   Wohnungsbot zeigen: Mobile-API ist die bessere Route.
5. **Random-Delay + respektvolle Intervalle** (flat_sniper-Muster)
   in den Fetch-Loop einbauen —廉价, menschlicher, weniger Blocks.

## Was wir NICHT übernehmen

- Va3elinas **DataDome-Bypass-Keys** — das ist aktives Umgehen von
  Schutzmaßnahmen mit extrahierten Keys; rechtlich wie ethisch
  Grauzone. Fredy löst das sauberer über die Mobile API.
- Fredy-Code direkt: Commons Clause verbietet uns nichts beim
  Konzept-Kopieren, aber der Code darf nicht 1:1 übernommen werden.
  Wir implementieren eigenständig (MIT, freie Nutzung).

## Fair-Use-Grundsatz (alle Projekte teilen ihn)

- Nur 1. Ergebnisseite laden (nach Datum sortiert) — nicht tief crawlen
- Intervall nicht unter 5-10 Minuten
- Identifizierenden User-Agent mit Kontakt-URL mitschicken
  (wie unser aktueller) — das ist "gutes Benehmen" im Crawler-Umfeld
