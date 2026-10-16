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
from app.schemas.search_agent import (
    RefreshResponse,
    SearchProfile,
    SearchResultsResponse,
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


def _validate_lease(raw: dict) -> ParsedLease:  # type: ignore[name-defined]
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
