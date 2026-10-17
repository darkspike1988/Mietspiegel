"""Schemas für lokale Versorger-Informationen."""
from __future__ import annotations

from pydantic import BaseModel


class GrundversorgerInfo(BaseModel):
    name: str
    website: str | None = None


class LocalProvidersResponse(BaseModel):
    ok: bool = True
    postal_code: str
    city: str | None = None
    grundversorger: GrundversorgerInfo | None = None
    lokale_versorger: list[str] = []
    breitband_check_url: str
    bnetza_grundversorger_url: str
    hint: str


__all__ = ["GrundversorgerInfo", "LocalProvidersResponse"]
