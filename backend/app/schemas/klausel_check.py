"""Schemas für den LLM-Klausel-Check."""
from __future__ import annotations

from pydantic import BaseModel, Field


class KlauselCheckRequest(BaseModel):
    """Vertragstext (aus PDF-Parsing oder direkt eingefügt)."""
    contract_text: str = Field(..., min_length=50, max_length=50000)


class KlauselBefund(BaseModel):
    titel: str
    schwere: str = Field(..., description="hoch | mittel | niedrig")
    fundstelle: str
    begründung: str


class KlauselCheckResponse(BaseModel):
    ok: bool = True
    befunde: list[KlauselBefund] = []
    llm_zusammenfassung: str | None = None
    empfehlung: str


__all__ = ["KlauselCheckRequest", "KlauselBefund", "KlauselCheckResponse"]
