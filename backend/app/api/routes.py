"""FastAPI routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import JSONResponse

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
from app.services import pdf_parser, rent_engine, widerspruch

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


def _validate_lease(raw: dict) -> "ParsedLease":  # type: ignore[name-defined]
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