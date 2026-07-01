"""PDF parsing service.

Strategy: Mistral OCR first (handles scanned + native PDFs), fall back to
pdfplumber for clean native PDFs (faster, no API call).

Both paths produce raw text; structured extraction happens in a second
LLM call via MistralClient.chat_json().
"""

from __future__ import annotations

import json
import structlog

import pdfplumber

from app.integrations.mistral import MistralClient, MistralError

log = structlog.get_logger(__name__)


EXTRACTION_SYSTEM = """\
Du bist ein juristischer Assistent für deutsches Mietrecht.
Extrahiere aus dem Vertragstext strukturierte Felder.
WICHTIG:
- Antworte IMMER in valides JSON gemäß dem Schema unten.
- Wenn ein Feld nicht im Text steht: setze es auf null.
- Adressen: vollständig mit PLZ und Stadt.
- Zahlen in EUR (Kaltmiete, Kaution) und m² (Wohnfläche).
- Baujahr als 4-stellige Jahreszahl.
"""

EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "address": {"type": "string"},
        "city": {"type": "string"},
        "postal_code": {"type": "string"},
        "cold_rent_eur": {"type": "number"},
        "warm_rent_eur": {"type": "number"},
        "additional_costs_eur": {"type": "number"},
        "deposit_eur": {"type": "number"},
        "size_sqm": {"type": "number"},
        "rooms": {"type": "number"},
        "build_year": {"type": "integer"},
        "location_quality": {"type": ["string", "null"], "enum": ["einfach", "mittel", "gut", None]},
        "extras": {"type": "array", "items": {"type": "string"}},
        "contract_date": {"type": ["string", "null"]},
    },
    "required": ["address", "cold_rent_eur", "size_sqm"],
}


async def extract_text_from_pdf(pdf_bytes: bytes, mistral: MistralClient) -> tuple[str, str]:
    """Return (text, parser_used).

    Tries Mistral OCR first; falls back to pdfplumber on error.
    """
    try:
        text = await mistral.ocr_pdf(pdf_bytes=pdf_bytes)
        if text and len(text.strip()) > 50:
            return text, "mistral-ocr"
    except MistralError as e:
        log.warning("ocr_fallback", error=str(e))

    # Fallback: pdfplumber for native digital PDFs.
    log.info("using_pdfplumber_fallback")
    text_parts: list[str] = []
    with pdfplumber.open(__import__("io").BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text() or ""
            text_parts.append(page_text)
    return "\n\n".join(text_parts), "pdfplumber"


async def extract_lease_structured(raw_text: str, mistral: MistralClient) -> dict:
    """Second-stage: raw text → structured JSON via Mistral chat."""
    user_prompt = (
        "Extrahiere folgende Daten aus dem Mietvertrag-Text:\n\n"
        f"```\n{raw_text[:8000]}\n```\n\n"  # truncate to stay within context
        "Antworte als JSON mit diesen Feldern:\n"
        f"{json.dumps(EXTRACTION_SCHEMA, indent=2, ensure_ascii=False)}"
    )
    parsed = await mistral.chat_json(
        system=EXTRACTION_SYSTEM,
        user=user_prompt,
        schema_hint=EXTRACTION_SCHEMA,
    )
    return parsed