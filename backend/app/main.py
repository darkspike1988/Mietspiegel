"""Mietspiegel-AI Backend Service.

FastAPI application exposing:
  - POST /api/parse    : Mietvertrag-PDF → strukturierte Daten
  - POST /api/check    : Adresse+Miete → Vergleichsmieten-Urteil
  - POST /api/widerspruch: Zu hohe Miete → formaler Widerspruchstext
  - GET  /api/health   : Health-Endpoint
  - GET  /api/cities   : Liste der verfügbaren Städte
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import api_router
from app.core.config import settings
from app.core.logging import configure_logging
from app.integrations.mistral import MistralClient
from app.schemas.contracts import ErrorResponse
from app.services.cache import CacheService

configure_logging(settings.log_level)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup/Shutdown: init external clients and caches."""
    app.state.mistral = MistralClient(
        api_key=settings.mistral_api_key,
        ocr_model=settings.mistral_ocr_model,
        chat_model=settings.mistral_chat_model,
    )
    app.state.cache = CacheService(
        url=settings.redis_url,
        ttl_seconds=settings.cache_ttl_seconds,
    )
    await app.state.cache.connect()
    yield
    await app.state.cache.disconnect()
    await app.state.mistral.close()


app = FastAPI(
    title="Mietspiegel-AI API",
    version="0.1.0",
    description="AI-gestützter Mietcheck + Widerspruchsgenerator (§558 / §556d BGB).",
    lifespan=lifespan,
)

# CORS: Astro-Frontend (statische Site) ruft API auf.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-API-Key"],
)

app.include_router(api_router, prefix="/api")


# ----------------------------------------------------------------------
# Uniform JSON error responses
# ----------------------------------------------------------------------


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:  # noqa: ARG001
    """Map HTTPException to a uniform JSON shape across all endpoints."""
    detail_str = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
    error_code = detail_str if isinstance(exc.detail, str) else "Error"
    return JSONResponse(
        status_code=exc.status_code,
        content=ErrorResponse(error=error_code, detail=detail_str).model_dump(),
    )


@app.get("/")
async def root() -> dict[str, str]:
    """Root redirect hint."""
    return {"service": "mietspiegel-ai", "docs": "/docs", "health": "/api/health"}