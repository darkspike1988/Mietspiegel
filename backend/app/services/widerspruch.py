"""Widerspruchsgenerator (Mietrecht).

Generates a formal German objection letter to the landlord when rent
exceeds the local comparison rent. Uses Mistral LLM with strict JSON
output schema and low temperature for legal determinism.

NEVER:
- Sends the letter (we don't have SMTP / letter-service)
- Stores any tenant PII beyond the request lifetime

ALWAYS:
- Cites relevant §-references
- Provides concrete next steps
- Warns about Mieterverein consultation for wucher cases
"""

from __future__ import annotations

import json
import structlog

from app.integrations.mistral import MistralClient
from app.schemas.contracts import (
    WiderspruchRequest,
    WiderspruchResponse,
)

log = structlog.get_logger(__name__)


SYSTEM_PROMPT = """\
Du bist ein juristischer Assistent für deutsches Mietrecht.
Aufgabe: Formuliere ein Widerspruchsschreiben an den Vermieter, wenn die
Miete die ortsübliche Vergleichsmiete überschreitet.

WICHTIG:
- Antworte IMMER in valides JSON gemäß Schema.
- Ton: sachlich, höflich, bestimmt. Keine Drohungen.
- Im Brief: konkrete Zahlen (aktuelle Miete, Vergleichsmiete, Abweichung in %).
- Im Brief: relevante Paragraphen (§558 BGB, §556d BGB, §5 WiStrG) erwähnen.
- Im Brief: Frist setzen (i.d.R. 14 Tage zur Stellungnahme).
- KEINE erfundenen Zahlen — nur die übergebenen Werte verwenden.
- KEINE Beratung über Mietminderung oder andere Rechtsfragen (Hinweis Mieterverein).
"""

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "subject": {"type": "string", "description": "Betreff-Zeile des Briefs"},
        "body": {"type": "string", "description": "Briefkorpus"},
        "references": {"type": "array", "items": {"type": "string"}, "description": "Rechtsgrundlagen"},
        "next_steps": {"type": "array", "items": {"type": "string"}, "description": "Konkrete Schritte"},
    },
    "required": ["subject", "body", "references", "next_steps"],
}


async def generate_widerspruch(
    req: WiderspruchRequest, mistral: MistralClient
) -> WiderspruchResponse:
    """Generate a formal objection letter."""
    check = req.check_response

    user_prompt = (
        "Erstelle ein Widerspruchsschreiben für folgenden Fall:\n\n"
        f"Mieter: {req.tenant_name}\n"
        f"Mietadresse: {check.comparison.city}\n"
        f"Vermieter: {req.landlord_name or '[Vermieter einfügen]'}\n"
        f"Anschrift Mieter: {req.tenant_address}\n\n"
        "Berechnete Daten:\n"
        f"- Aktuelle Kaltmiete: {check.requested_rent_eur:.2f} EUR\n"
        f"- Ortsübliche Vergleichsmiete: {check.fair_rent_eur:.2f} EUR\n"
        f"- Abweichung: {check.delta_percent:+.1f}%\n"
        f"- Einstufung: {check.verdict.value}\n"
        f"- Rechtsgrundlage: {check.legal_basis}\n"
        f"- Datenquelle: {check.comparison.source}\n\n"
        "Antworte als JSON:\n"
        f"{json.dumps(OUTPUT_SCHEMA, indent=2, ensure_ascii=False)}"
    )

    parsed = await mistral.chat_json(
        system=SYSTEM_PROMPT,
        user=user_prompt,
        schema_hint=OUTPUT_SCHEMA,
    )

    # Defensive defaults
    return WiderspruchResponse(
        subject=str(parsed.get("subject", "Widerspruch gegen die Miethöhe")),
        body=str(parsed.get("body", "")),
        references=list(parsed.get("references", [])),
        next_steps=list(parsed.get("next_steps", [])),
    )