"""FastAPI routes."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import (
    APIRouter,
    File,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from pydantic import BaseModel, Field

from app.schemas.contracts import (
    CityInfo,
    ErrorResponse,
    HealthResponse,
    ParseResponse,
    RentCheckRequest,
    RentCheckResponse,
    WiderspruchRequest,
    WiderspruchResponse,
)
from app.schemas.local_providers import LocalProvidersResponse
from app.schemas.search_agent import (
    Provider,
    RefreshResponse,
    SearchProfile,
    SearchResultsResponse,
)
from app.schemas.tariff_stats import (
    SmardTimeStats,
    TariffObservation,
    TariffStatisticsResponse,
)
from app.schemas.tools import (
    KautionCheckRequest,
    KautionCheckResponse,
    KautionsVergleichRequest,
    KautionsVergleichResponse,
    MieterhoehungCheckRequest,
    MieterhoehungCheckResponse,
    NebenkostenCheckRequest,
    NebenkostenCheckResponse,
    SparrechnerRequest,
    SparrechnerResponse,
)
from app.schemas.wohnprofil import WohnprofilRequest, WohnprofilResponse
from app.services import (
    pdf_parser,
    rent_engine,
    search_agent,
    tools_engine,
    widerspruch,
)

api_router = APIRouter()


# ----------------------------------------------------------------------
# Health
# ----------------------------------------------------------------------


@api_router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    mistral = request.app.state.mistral
    return HealthResponse(
        status="ok",
        version="0.1.0",
        mistral_configured=bool(mistral._api_key),  # noqa: SLF001
    )


# ----------------------------------------------------------------------
# Cities
# ----------------------------------------------------------------------


@api_router.get("/cities", response_model=list[CityInfo])
async def cities() -> list[CityInfo]:
    """Return all available comparison datasets.

    Each entry includes the verbatim source URL so the frontend can render
    an attribution link next to the verdict (transparency/legal-defensibility).
    """
    return [
        CityInfo(
            slug=c["slug"],
            name=c["name"],
            average_eur_per_sqm=c["average_eur_per_sqm"],
            federal_state=c["federal_state"],
            source=c["source"],
            source_url=c["source_url"],
            year=c["year"],
            kind=c["kind"],
            publisher=c["publisher"],
            spread_eur_per_sqm_low=c["spread_eur_per_sqm_low"],
            spread_eur_per_sqm_high=c["spread_eur_per_sqm_high"],
        )
        for c in rent_engine.all_cities()
    ]


# ----------------------------------------------------------------------
# Parse (PDF → structured lease)
# ----------------------------------------------------------------------


@api_router.post(
    "/parse",
    response_model=ParseResponse,
    responses={400: {"model": ErrorResponse}, 413: {"model": ErrorResponse}},
)
async def parse_pdf(
    request: Request,
    file: Annotated[UploadFile, File(description="Mietvertrag-PDF, max 10 MB")],
) -> ParseResponse:
    if file.content_type not in {"application/pdf", "application/x-pdf"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Expected PDF, got {file.content_type}",
        )

    pdf_bytes = await file.read()
    if len(pdf_bytes) > 10 * 1024 * 1024:  # 10 MB
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="PDF >10 MB. Bitte vorab komprimieren.",
        )

    mistral = request.app.state.mistral
    cache = request.app.state.cache

    # Cache key on file hash to avoid re-parsing same PDF
    cache_payload = {"size": len(pdf_bytes), "sha256": _sha256(pdf_bytes)}
    cached = await cache.get("parse", cache_payload)
    if cached:
        return ParseResponse.model_validate(cached)

    text, parser_used = await pdf_parser.extract_text_from_pdf(pdf_bytes, mistral)
    if not text or len(text.strip()) < 50:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="PDF enthält keinen extrahierbaren Text (evtl. Scan ohne OCR möglich).",
        )

    raw = await pdf_parser.extract_lease_structured(text, mistral)
    lease = _validate_lease(raw)
    resp = ParseResponse(lease=lease, parser=parser_used)
    await cache.set("parse", cache_payload, resp.model_dump(mode="json"))
    return resp


def _sha256(data: bytes) -> str:
    import hashlib
    return hashlib.sha256(data).hexdigest()


def _validate_lease(raw: dict):
    from pydantic import ValidationError

    from app.schemas.contracts import ParsedLease

    try:
        return ParsedLease.model_validate(raw)
    except ValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"LLM extraction invalid: {e.errors()[:5]}",
        ) from e


# ----------------------------------------------------------------------
# Check (lease OR minimal input → verdict)
# ----------------------------------------------------------------------


@api_router.post("/check", response_model=RentCheckResponse)
async def check_rent(req: RentCheckRequest) -> RentCheckResponse:
    try:
        return await rent_engine.evaluate(req)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


# ----------------------------------------------------------------------
# Widerspruch (verdict → formal letter)
# ----------------------------------------------------------------------


@api_router.post("/widerspruch", response_model=WiderspruchResponse)
async def widerspruch_letter(
    request: Request, req: WiderspruchRequest
) -> WiderspruchResponse:
    if req.check_response.verdict.value == "fair":
        raise HTTPException(
            status_code=400,
            detail="Widerspruch nicht sinnvoll bei 'fair'-Einstufung.",
        )

    mistral = request.app.state.mistral
    return await widerspruch.generate_widerspruch(req, mistral)


# ----------------------------------------------------------------------
# Error handler — uniform JSON shape
# Registered on the FastAPI app instance in main.py (APIRouter doesn't
# support exception_handler).
# ----------------------------------------------------------------------
# ----------------------------------------------------------------------
# Free tenant tools (deterministic, no LLM, no cost)
# Schweizer Taschenmesser: Nebenkosten / Kaution / Mieterhöhung
# ----------------------------------------------------------------------

@api_router.post("/tools/nebenkosten", response_model=NebenkostenCheckResponse)
async def tool_nebenkosten(req: NebenkostenCheckRequest) -> NebenkostenCheckResponse:
    return tools_engine.check_nebenkosten(req)


@api_router.post("/tools/kaution", response_model=KautionCheckResponse)
async def tool_kaution(req: KautionCheckRequest) -> KautionCheckResponse:
    return tools_engine.check_kaution(req)


@api_router.post("/tools/sparrechner", response_model=SparrechnerResponse)
async def tool_sparrechner(req: SparrechnerRequest) -> SparrechnerResponse:
    return tools_engine.compute_sparpotential(req)


@api_router.post("/tools/mieterhoehung", response_model=MieterhoehungCheckResponse)
async def tool_mieterhoehung(
    req: MieterhoehungCheckRequest,
) -> MieterhoehungCheckResponse:
    return tools_engine.check_mieterhoehung(req)


@api_router.post("/tools/kaution/vergleich", response_model=KautionsVergleichResponse)
async def tool_kaution_vergleich(
    req: KautionsVergleichRequest,
) -> KautionsVergleichResponse:
    return tools_engine.compare_kaution(req)


# ----------------------------------------------------------------------
# Wohnungssuche-Agent (MVP: RSS-Polling + Mietpreis-Badge je Treffer)
# ----------------------------------------------------------------------

@api_router.post("/search/refresh", response_model=RefreshResponse)
async def search_refresh(profile: SearchProfile) -> RefreshResponse:
    """Suchprofil einmal abfragen; neue Treffer werden gegen den
    Mietspiegel geprüft und (falls TELEGRAM_BOT_TOKEN/CHAT_ID gesetzt)
    per Telegram benachrichtigt."""
    fresh, total_seen = await search_agent.refresh_profile(profile)
    if fresh:
        await search_agent.notify_telegram(fresh)
    return RefreshResponse(
        profile_name=profile.name,
        new_listings=fresh,
        total_seen=total_seen,
        checked_at=datetime.now(UTC).isoformat(),
    )


@api_router.post("/search/results", response_model=SearchResultsResponse)
async def search_results(profile: SearchProfile) -> SearchResultsResponse:
    """Alle bekannten Treffer eines Profils (aus dem Seen-Store)."""
    from app.services.search_agent import _seen

    seen = _seen.get(profile.name, {})
    return SearchResultsResponse(
        profile_name=profile.name,
        total_seen=len(seen),
        listings=[],
    )


# ----------------------------------------------------------------------
# Versorger-Tarife (Verivox-Partner-Integration + SMARD-Marktdaten)
# ----------------------------------------------------------------------

@api_router.get("/versorger/tarifrechner")
async def versorger_tarifrechner(
    product: str, postal_code: str
) -> dict:
    """Deep-Link zum offiziellen Verivox-Tarifrechner (Strom/Gas/DSL/Mobilfunk).

    Mit gesetzter VERIVOX_PARTNER_ID werden Tracking-Links erzeugt
    (Provision ~55 €/Sale Strom&Gas); ohne Partner-ID wird der
    öffentliche Vergleich verlinkt.
    """
    from app.services import verivox_provider

    return verivox_provider.build_tariff_calculator_url(product, postal_code)


@api_router.get("/versorger/vergleiche")
async def versorger_vergleiche(postal_code: str, product: str = "strom") -> dict:
    """Links zu ALLEN großen Vergleichsportalen je PLZ — der User
    vergleicht quer und findet wirklich den günstigsten Anbieter.

    Ein Portal allein zeigt oft nicht den besten Tarif (Ranking-
    Faktoren sind Provisionen). Erst der Quervergleich sichert ab.
    """
    from app.services import verivox_provider

    pid = verivox_provider.get_partner_id()
    plz = postal_code.strip()
    if len(plz) != 5 or not plz.isdigit():
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="postal_code muss 5-stellig sein")

    base = {
        "strom": [
            ("Verivox", f"https://www.verivox.de/strom/stromtarife/?plz={plz}"),
            ("Check24", f"https://www.check24.de/strom-anbieter/vergleich/?plz={plz}"),
            ("Idealo", f"https://www.idealo.de/strom/vergleich?zip={plz}"),
        ],
        "gas": [
            ("Verivox", f"https://www.verivox.de/gas/gastarife/?plz={plz}"),
            ("Check24", f"https://www.check24.de/gas-anbieter/vergleich/?plz={plz}"),
            ("Idealo", f"https://www.idealo.de/gas/vergleich?zip={plz}"),
        ],
        "dsl": [
            ("Verivox", f"https://www.verivox.de/dsl/?plz={plz}"),
            ("Check24", f"https://www.check24.de/dsl/?zipcode={plz}"),
        ],
        "mobilfunk": [
            ("Verivox", "https://www.verivox.de/mobilfunk/"),
            ("Check24", "https://www.check24.de/handy-tarif/"),
        ],
    }
    from app.services import verivox_provider as _vpx

    portals = base.get(product, base["strom"])
    result = []
    for name, url in portals:
        portal_pid = _vpx.get_portal_partner_id(name)
        if portal_pid:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}partner={portal_pid}"
        elif name.lower() == "verivox" and pid:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}partner={pid}"
        result.append({"portal": name, "url": url})
    from app.services import verivox_provider as _vp

    iframe_url = _vp.get_iframe_url()
    return {
        "ok": True,
        "product": product,
        "postal_code": plz,
        "vergleiche": result,
        "iframe_url": iframe_url,
        "tip": (
            "Vergleiche auf mindestens 2 Portalen: Rankings unterscheiden "
            "sich (Provisions-Bias). Prüfe beim günstigsten Tarif: "
            "echte Preisgarantie (inkl. Steuern/Netzentgelte), Bonus-"
            "Bedingungen, Vorkasse-Warnung, Neukunden-Bonus vs. Folgejahr."
        ),
    }


@api_router.get("/versorger/strompreise")
async def versorger_strompreise() -> dict:
    """Aktuelle Day-Ahead-Börsenpreise (SMARD/BNetzA, CC BY 4.0) +
    die 5 günstigsten/teuersten Stunden — für Dynamic-Tariff-Mieter."""
    from app.services import smard_provider

    prices = await smard_provider.fetch_day_ahead_prices()
    return {
        "ok": True,
        "source": "Bundesnetzagentur SMARD (CC BY 4.0)",
        "einheit": "EUR/MWh (Börse) — nicht Haushaltspreis",
        "count": len(prices),
        "cheapest_hours": smard_provider.cheapest_hours(prices, n=5),
        "most_expensive_hours": smard_provider.most_expensive_hours(prices, n=5),
        "series": prices,
    }


# ----------------------------------------------------------------------
# Statistische Tarif-Auswertung (Portal-Ranking + SMARD-Zeitreihe)
# ----------------------------------------------------------------------

@api_router.post("/versorger/beobachtung", response_model=TariffObservation)
async def versorger_beobachtung(obs: TariffObservation) -> TariffObservation:
    """Anonyme Preis-Beobachtung aus einem Portal speichern.

    Der User liest z.B. den günstigsten ct/kWh-Preis bei Verivox und
    Check24 ab und trägt beide ein — die Statistik rechnet daraus,
    welches Portal wirklich am günstigsten ist.
    """
    from app.services import tariff_stats

    return tariff_stats.add_observation(obs)


@api_router.get("/versorger/statistik", response_model=TariffStatisticsResponse)
async def versorger_statistik(product: str, postal_code: str) -> TariffStatisticsResponse:
    """Portal-Ranking für Produkt+PLZ: min/median/avg je Portal,
    nach Median sortiert — der deterministische Gewinner steht vorn."""
    from app.services import tariff_stats

    return tariff_stats.compute_portal_ranking(product, postal_code)


@api_router.get("/versorger/strompreise/statistik", response_model=SmardTimeStats)
async def versorger_strompreise_statistik() -> SmardTimeStats:
    """SMARD-Börsenpreise deterministisch aggregiert: nach Tagesstunde
    und Wochentag — wann ist Strom im Schnitt am günstigsten?"""
    from app.services import smard_provider, tariff_stats

    prices = await smard_provider.fetch_day_ahead_prices()
    return tariff_stats.aggregate_smard_by_time(prices)


# ----------------------------------------------------------------------
# Suchprofil-Verwaltung (persistiert in SQLite)
# ----------------------------------------------------------------------


class ProfileCreateRequest(SearchProfile):
    """Suchprofil anlegen (Name + URL, Provider wird automatisch erkannt)."""


class ProfileListResponse(BaseModel):
    ok: bool = True
    profiles: list[dict]


@api_router.post("/search/profiles", status_code=201)
async def create_search_profile(
    req: SearchProfile, api_token: str | None = None
) -> dict:
    """Suchprofil persistent anlegen. Mit api_token gehört es dem User
    (Benachrichtigungen laufen dann über seine Kanäle); ohne Token
    bleibt es anonym (wie bisher)."""
    from app.services import storage, user_service

    user_id = None
    if api_token:
        user = user_service.user_by_token(api_token)
        if user is None:
            raise HTTPException(status_code=401, detail="Ungültiger Token")
        user_id = user["id"]

    pid = storage.save_profile(
        name=req.name, url=str(req.url), provider=req.provider.value,
        city=req.city,
    )
    if user_id is not None:
        conn = storage._get_conn()
        with storage._lock:
            conn.execute(
                "UPDATE search_profiles SET user_id = ? WHERE id = ?",
                (user_id, pid),
            )
            conn.commit()
    return {"ok": True, "profile_id": pid, "name": req.name}


@api_router.get("/search/profiles", response_model=ProfileListResponse)
async def list_search_profiles() -> ProfileListResponse:
    from app.services import storage

    return ProfileListResponse(profiles=storage.list_profiles())


@api_router.post("/search/refresh/{profile_id}", response_model=RefreshResponse)
async def refresh_profile_by_id(profile_id: int) -> RefreshResponse:
    """Persistiertes Suchprofil abfragen: neue Treffer (SQLite-Dedup)
    werden geprüft, archiviert und ggf. per Telegram benachrichtigt."""
    from app.services import storage

    row = storage.get_profile(profile_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Profil {profile_id} nicht gefunden")
    profile = SearchProfile(
        name=row["name"], url=row["url"],
        provider=Provider(row["provider"] or "rss"), city=row["city"],
    )
    fresh, total_seen = await search_agent.refresh_profile(profile, profile_id)
    if fresh:
        await search_agent.notify_telegram(fresh)
    return RefreshResponse(
        profile_name=profile.name,
        new_listings=fresh,
        total_seen=total_seen,
        checked_at=datetime.now(UTC).isoformat(),
    )


@api_router.get("/search/listings/{profile_id}", response_model=SearchResultsResponse)
async def archived_listings(profile_id: int, limit: int = 100) -> SearchResultsResponse:
    """Archivierte Treffer eines Profils (SQLite, neueste zuerst)."""
    from app.services import storage

    row = storage.get_profile(profile_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Profil {profile_id} nicht gefunden")
    from app.schemas.search_agent import CheckedListing

    raw = storage.list_listings(profile_id, limit=limit)
    listings = []
    for item in raw:
        try:
            listings.append(CheckedListing.model_validate(item))
        except Exception:
            continue
    return SearchResultsResponse(
        profile_name=row["name"],
        total_seen=storage.count_listings(profile_id),
        listings=listings,
    )


# ----------------------------------------------------------------------
# Kombination: Suchtreffer × Mietspiegel-Abweichung × Wohnkosten
# ----------------------------------------------------------------------


class BudgetCheckRequest(BaseModel):
    """Wohnkosten-Budget: Setzt Miete + passende Anteile ins Verhältnis."""
    cold_rent_eur: float
    net_income_eur: float = Field(..., gt=0, description="Nettoeinkommen/Monat")
    nebenkosten_eur: float = Field(0, ge=0, description="NK-Vorauszahlung")
    strom_eur: float = Field(0, ge=0, description="Strom/Monat (Ø 40-60 €)")


class BudgetCheckResponse(BaseModel):
    ok: bool = True
    total_housing_cost_eur: float
    cost_percent_of_income: float
    fair_share_ok: bool
    hint: str


@api_router.post("/wohnkosten/budget", response_model=BudgetCheckResponse)
async def wohnkosten_budget(req: BudgetCheckRequest) -> BudgetCheckResponse:
    """30 %-Faustregel prüfen: Miete + NK + Strom vs. Einkommen."""
    total = round(req.cold_rent_eur + req.nebenkosten_eur + req.strom_eur, 2)
    pct = round(total / req.net_income_eur * 100, 1)
    ok = pct <= 30.0
    hint = (
        f"{pct} % des Einkommens — im grünen Bereich (Faustregel: max. 30 %)."
        if ok
        else (
            f"⚠️ {pct} % des Einkommens — über der 30 %-Faustregel. "
            "Entweder mehr Einkommen, kleinere Wohnung oder Mietspiegel-Widerspruch: "
            f"Bei Überhöhung kann die Mietpreisbremse (§556d BGB) entlasten — "
            "nutze den Mietpreis-Check."
        )
    )
    return BudgetCheckResponse(
        total_housing_cost_eur=total,
        cost_percent_of_income=pct,
        fair_share_ok=ok,
        hint=hint,
    )


@api_router.get("/search/market/{profile_id}")
async def search_market_stats(profile_id: int) -> dict:
    """Mietspiegel-Marktstatistik eines Profils: Wie viel % der Treffer
    sind überhöht? Was kostet der Markt im Schnitt über dem Mietspiegel?"""
    from app.schemas.search_agent import CheckedListing
    from app.services import storage, tariff_stats

    row = storage.get_profile(profile_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Profil {profile_id} nicht gefunden")
    raw = storage.list_listings(profile_id, limit=500)
    listings = []
    for item in raw:
        try:
            listings.append(CheckedListing.model_validate(item))
        except Exception:
            continue
    stats = tariff_stats.compute_market_stats(listings)
    return stats.model_dump()


# ----------------------------------------------------------------------
# Wohnprofil-Modell (kombiniert Mietspiegel × Budget × Markt)
# ----------------------------------------------------------------------

@api_router.post("/wohnprofil", response_model=WohnprofilResponse)
async def wohnprofil(req: WohnprofilRequest) -> WohnprofilResponse:
    """Das Kombinations-Modell: Eine Wohnung (z.B. aus dem Such-Agent)
    + Einkommen + optional Marktstatistik → Score 0-100 mit Ampel und
    nachvollziehbarer Begründung je Dimension (kein Blackbox)."""
    from app.services import wohnprofil as wp

    return wp.compute_wohnprofil(req)


# ----------------------------------------------------------------------
# Lokale Versorger je PLZ (Grundversorger + Stadtwerke + Breitband)
# ----------------------------------------------------------------------

@api_router.get("/versorger/lokal/{postal_code}", response_model=LocalProvidersResponse)
async def versorger_lokal(postal_code: str) -> LocalProvidersResponse:
    """Lokale Strom-/Gas-Anbieter für eine PLZ: Grundversorger (die
    wichtigste Info — ohne Vertrag zahlt man dessen teuersten Tarif),
    lokale Stadtwerke, offizieller Breitbandatlas + BNetzA-Verweise."""
    from app.services import local_providers

    if len(postal_code) != 5 or not postal_code.isdigit():
        raise HTTPException(status_code=400, detail="postal_code muss 5-stellig sein")
    return local_providers.get_local_providers(postal_code)


# ----------------------------------------------------------------------
# Auth: Register/Login, Google+Apple OAuth, 2FA (TOTP)
# ----------------------------------------------------------------------


class RegisterRequest(BaseModel):
    email: str
    password: str


class LoginRequest(BaseModel):
    email: str
    password: str
    totp_code: str | None = None


@api_router.post("/auth/register")
async def auth_register(req: RegisterRequest) -> dict:
    """Kostenloser Account: E-Mail + Passwort (≥8 Zeichen)."""
    from app.services import user_service

    try:
        result = user_service.register_user(req.email, req.password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "email": result["email"], "api_token": result["api_token"],
            "hint": "Token sicher aufbewahren — Profile & Benachrichtigungen hängen daran."}


@api_router.post("/auth/login")
async def auth_login(req: LoginRequest) -> dict:
    """Login → API-Token. Mit aktiviertem 2FA ist totp_code nötig."""
    from app.services import user_service

    try:
        token = user_service.login_user(req.email, req.password)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
    user = user_service.user_by_token(token)
    if user and not user_service.verify_2fa(user["id"], req.totp_code or ""):
        raise HTTPException(status_code=401, detail="2FA-Code falsch oder fehlt")
    return {"ok": True, "api_token": token}


@api_router.get("/auth/oauth/{provider}/start")
async def oauth_start(provider: str) -> dict:
    """OAuth-Login starten → Redirect-URL (Google/Apple).
    Nicht konfigurierte Provider liefern 503 mit Setup-Hinweis."""
    import secrets as _secrets

    from app.services import oauth_service

    state = _secrets.token_urlsafe(16)
    if provider == "google":
        url = oauth_service.google_authorize_url(state)
    elif provider == "apple":
        url = oauth_service.apple_authorize_url(state)
    else:
        raise HTTPException(status_code=400, detail="provider: google | apple")
    if url is None:
        raise HTTPException(
            status_code=503,
            detail=f"{provider}-Login nicht konfiguriert (CLIENT_ID etc. in .env setzen)",
        )
    return {"ok": True, "authorize_url": url, "state": state}


@api_router.get("/auth/oauth/{provider}/callback")
async def oauth_callback(provider: str, code: str, state: str | None = None) -> dict:
    """OAuth-Callback: code → id_token → User anlegen/koppeln → Token."""
    from app.services import oauth_service, user_service

    if provider == "google":
        payload = await oauth_service.google_exchange_code(code)
    elif provider == "apple":
        payload = await oauth_service.apple_exchange_code(code)
    else:
        raise HTTPException(status_code=400, detail="provider: google | apple")
    if payload is None:
        raise HTTPException(status_code=401, detail="OAuth-Exchange fehlgeschlagen")
    identity = oauth_service.extract_identity(payload, provider)
    if identity is None:
        raise HTTPException(status_code=401, detail="Keine E-Mail im OAuth-Token")
    result = user_service.login_or_register_oauth(identity)
    return {"ok": True, "api_token": result["api_token"],
            "created": result["created"]}


# ---- 2FA ----


@api_router.post("/auth/2fa/setup")
async def twofa_setup(api_token: str) -> dict:
    """2FA starten: liefert Secret + otpauth-URL (QR-Code scannen),
    danach mit /auth/2fa/enable + erstem Code bestätigen."""
    from app.services import twofactor, user_service

    user = user_service.user_by_token(api_token)
    if user is None:
        raise HTTPException(status_code=401, detail="Ungültiger Token")
    secret = twofactor.generate_secret()
    user_service.set_2fa_secret(user["id"], secret)
    return {
        "ok": True,
        "secret": secret,
        "otpauth_url": twofactor.otpauth_url(secret, user["email"]),
        "hint": "QR scannen (Google Authenticator / Apple / Authy), dann /auth/2fa/enable",
    }


@api_router.post("/auth/2fa/enable")
async def twofa_enable(api_token: str, code: str) -> dict:
    """2FA aktivieren — der erste Code aus der Authenticator-App."""
    from app.services import twofactor, user_service

    user = user_service.user_by_token(api_token)
    if user is None:
        raise HTTPException(status_code=401, detail="Ungültiger Token")
    state = user_service.get_2fa_state(user["id"])
    if not state["secret"] or not twofactor.verify_totp(state["secret"], code):
        raise HTTPException(status_code=400, detail="Code falsch — bitte erneut")
    user_service.enable_2fa(user["id"])
    return {"ok": True, "hint": "2FA aktiv — ab jetzt beim Login TOTP-Code mitgeben."}


@api_router.post("/auth/notifications")
async def auth_notifications(
    api_token: str,
    telegram_chat_id: str | None = None,
    ntfy_topic: str | None = None,
    notify_email: bool | None = None,
) -> dict:
    """Benachrichtigungskanäle setzen (alle optional, mehrere möglich)."""
    from app.services import user_service

    user = user_service.user_by_token(api_token)
    if user is None:
        raise HTTPException(status_code=401, detail="Ungültiger Token")
    user_service.update_notification_settings(
        user["id"], telegram_chat_id, ntfy_topic, notify_email
    )
    return {"ok": True, "hint": "Kanäle aktualisiert — neue Wohnungs-Treffer landen dort."}
