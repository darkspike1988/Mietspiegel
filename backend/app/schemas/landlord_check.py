"""Schemas für den Vermieter-Check."""
from __future__ import annotations

from pydantic import BaseModel


class LandlordInfo(BaseModel):
    key: str
    name: str
    hq: str
    apartments: str
    stock: str
    notes: str
    register_url: str


class LandlordNews(BaseModel):
    title: str
    link: str | None = None
    published: str | None = None
    source: str | None = None


class LandlordResponse(BaseModel):
    ok: bool = True
    query: str
    known: bool
    info: LandlordInfo | None = None
    news: list[LandlordNews] = []
    register_url: str
    bundesanzeiger_url: str
    hint: str


__all__ = ["LandlordInfo", "LandlordNews", "LandlordResponse"]
