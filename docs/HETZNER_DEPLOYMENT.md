# Hetzner-Deployment: 1.000 User, 24/7

## Warum das mit einem kleinen Server funktioniert

**URL-Sharing-Prinzip:** Der Scheduler pollt nicht 1.000 Profile, sondern
**unique Such-URLs**. 1.000 User suchen typischerweise in ~100-200
Städten/Bezirken → ~100-200 Polls alle 10 Min. Das trägt selbst ein
CX22 (2 vCPU, 4 GB, ~4 €/Monat) locker; SQLite reicht bei Webservorkload
durchaus bis in die 1.000er-User-Region (WAL-Modus ist aktiv).

## Setup (Ubuntu/Debian auf Hetzner)

```bash
# 1. System + Python
apt update && apt install -y python3-venv nginx
python3 -m venv /opt/app/venv
/opt/app/venv/bin/pip install -e /opt/mietspiegel/backend

# 2. Frontend bauen
cd /opt/mietspiegel/frontend && npm ci && npm run build
# dist/ nach /var/www/ legen

# 3. ENV (siehe backend/.env.example):
#    MISTRAL_API_KEY (optional), TELEGRAM_BOT_TOKEN, SMTP_*,
#    GOOGLE_*/APPLE_* (OAuth), OAUTH_REDIRECT_BASE=https://deine-domain.de

# 4. systemd-Service (24/7 + Auto-Restart):
cat > /etc/systemd/system/mietspiegel.service <<UNIT
[Unit]
After=network.target
[Service]
WorkingDirectory=/opt/mietspiegel/backend
EnvironmentFile=/opt/mietspiegel/backend/.env
ExecStart=/opt/app/venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8765
Restart=always
[Install]
WantedBy=multi-user.target
UNIT
systemctl enable --now mietspiegel

# 5. nginx Reverse Proxy + HTTPS (Let's Encrypt gratis):
#    frontend statisch ausliefern, /api → 127.0.0.1:8765
apt install -y certbot python3-certbot-nginx
```

## Benachrichtigungs-Kanäle (alle kostenlos)

| Kanal | Setup | Kosten |
|---|---|---|
| **Telegram** | Bot bei @BotFather (2 Min), User schickt /start → chat_id | 0 € |
| **ntfy-Push** | User abonniert sein privates Topic in der ntfy-App | 0 € |
| **E-Mail** | Hetzner-eigener Mailserver oder beliebiges Postfach via SMTP | 0 € |
| ~~WhatsApp~~ | Business-API kostet + Meta-Verifizierung | bewusst nicht |

## OAuth-Apps registrieren (einmalig, ~15 Min je)

- **Google**: console.cloud.google.com → OAuth 2.0 Client ID (Web),
  Redirect: `https://deine-domain.de/auth/callback/google`
  → GOOGLE_CLIENT_ID/SECRET in .env
- **Apple**: developer.apple.com → "Sign in with Apple" aktivieren,
  Services-ID + Key (.p8) erstellen, Redirect registrieren
  → APPLE_* in .env (Client-Secret-JWT generiert der Code selbst)

## 2FA

TOTP (RFC 6238) — läuft mit Google Authenticator, Apple-Code, Authy,
1Password. User-Flow: `/auth/2fa/setup` → QR scannen → `/auth/2fa/enable`.

## Skalierungs-Pfad (wenn >1.000 User)

1. SQLite → PostgreSQL (Storage-Layer ist gekapselt — nur storage.py tauschen)
2. Scheduler als eigener Worker (separter systemd-Service) auslagern
3. Redis-Cache aktivieren (REDIS_URL ist vorbereitet)
