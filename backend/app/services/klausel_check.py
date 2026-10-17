"""LLM-Klausel-Check (Plus-Feature): Mietvertrag → unwirksame Klauseln.

Zweistufig, bewusst deterministisch wo möglich:
1. DETERMINISTISCHER PRE-SCREEN: Regex-Regeln für die klassischen
   unwirksamen Klauseln (BGH-Fundus — stabiler als LLM):
   - Sternchenskizze/Schönheitsreparaturen bei unrenovierter Übernahme
   - Formularmäßige Renovierungsklauseln mit festen Intervallen
   - Kaution > 3 Monatsmieten (§551 BGB)
   - unzulässige Schönheitsreparatur-Verteilung (§535 BGB)
2. LLM-TIEFENANALYSE (Mistral, optional): Klartext-Bewertung der
   gefundenen Stellen + Fundstellen im Vertrag.

Der LLM-Part wird nur genutzt, wenn ein Mistral-Key konfiguriert ist;
ohne Key liefert der deterministische Check allein schon Wert.
"""
from __future__ import annotations

import re

import structlog

from app.schemas.klausel_check import (
    KlauselBefund,
    KlauselCheckRequest,
    KlauselCheckResponse,
)

log = structlog.get_logger(__name__)

# Deterministische Regel-DB: (Muster, Titel, §-Begründung, Schwere)
REGELN: list[tuple[str, str, str, str]] = [
    (
        r"(schönheitsreparatur|renovierung).{0,120}(starren|festen|unveränderlichen)?.{0,40}"
        r"(zeitabständen|intervallen|jährlich|alle\s+\d+\s+jahre)",
        re.IGNORECASE | re.DOTALL,
    ) and ("Sternchenklausel",
        "Formularmäßige Renovierungsklausel mit festen Zeitabständen ist nach "
        "ständiger BGH-Rechtsprechung unzulässig (BGH VIII ZR 244/09)",
        "hoch") or None,
]


def _build_regeln() -> list[dict]:
    """Die Regel-DB (Muster → Befund) — gepflegt nach BGH-Fundus."""
    return [
        {
            "pattern": re.compile(
                r"(schönheitsreparatur\w*|renovier\w*).{0,200}(alle\s+(\d+)\s+jahre|"
                r"jährlich|in\s+zeitabständen\s+von\s+(\d+)\s+jahren)",
                re.IGNORECASE | re.DOTALL,
            ),
            "titel": "Renovierung mit festen Zeitabständen",
            "grund": (
                "Formularmäßige Schönheitsreparatur-/Renovierungsklauseln, die "
                "dem Mieter starre Zeitabstände auferlegen, sind regelmäßig "
                "unwirksam (§307 BGB; BGH-Rechtsprechung, z.B. VIII ZR 244/09). "
                "Nur individualvertraglich oder mit flexiblen Kriterien zulässig."
            ),
            "schwere": "hoch",
        },
        {
            "pattern": re.compile(
                r"(vertrag\w*|über\w*).{0,80}(unrenoviert|nicht\s+renoviert|im\s+derzeitigen\s+zustand)"
                r".{0,400}(schönheitsreparatur|renovierung).{0,120}(übernehmen|durchführen|ausführen)",
                re.IGNORECASE | re.DOTALL,
            ),
            "titel": "Renovierungspflicht bei unrenovierter Übernahme",
            "grund": (
                "Übernimmt der Mieter die Wohnung unrenoviert und soll trotzdem "
                "schönheitsreparaturen übernehmen, ist die Klausel nach der "
                "BGH-Rechtsprechung unzulässig (Bolturin II, VIII ZR 361/13) — "
                "die Renovierungspflicht erlischt nach durchgeführter Anfangs-"
                "renovierung nur bei klaren Übergangsregelungen."
            ),
            "schwere": "hoch",
        },
        {
            "pattern": re.compile(
                r"quotal\w*.{0,80}(renovierungs\w*|schönheitsreparatur)|"
                r"(mieter).{0,60}(endrenovierung|rückgabe\s+in\s+renoviertem\s+zustand)",
                re.IGNORECASE | re.DOTALL,
            ),
            "titel": "Endrenovierungs-/Quotenabgeltungsklausel",
            "grund": (
                "Formularmäßige Endrenovierungsklauseln und starre Quotenabgabe "
                "sind regelmäßig unwirksam (§307 BGB, BGH VIII ZR 183/15 u.a.) — "
                "eine anteilige Kostenübernahme kann u.U. zulässig sein."
            ),
            "schwere": "hoch",
        },
        {
            "pattern": re.compile(
                r"(kaution|mietkaution|sicherheit).{0,80}(drei|3).{0,10}(monats\w*|nett\w*).{0,20}"
                r"(mieten|kaltmieten)|kaution.{0,60}(?>.{0,3})>(\s*)?(drei|3)\s*monats",
                re.IGNORECASE | re.DOTALL,
            ),
            "titel": "Kaution über 3 Monatsmieten",
            "grund": (
                "§551 BGB begrenzt die Kaution auf das Dreifache der Kaltmiete "
                "(bei Zahlung zum 1. Monatsmieten). Höhere Forderungen sind "
                "unzulässig — Überzahlung zurückforderbar."
            ),
            "schwere": "mittel",
        },
        {
            "pattern": re.compile(
                r"(mieter\w*|sie).{0,100}(dürfen|können|ist\s+ihnen).{0,40}(nicht|keine).{0,60}"
                r"(unterschlag\w*|untervermiet\w*|überlassung\w*).{0,60}(nicht\s+zulassen|verboten)",
                re.IGNORECASE | re.DOTALL,
            ),
            "titel": "Pauschales Untermietungsverbot",
            "grund": (
                "Ein pauschales Untermietungsverbot ist unwirksam — der Mieter "
                "hat einen Anspruch auf Untermiet-Erlaubnis bei berechtigtem "
                "Interesse (§553 BGB)."
            ),
            "schwere": "mittel",
        },
        {
            "pattern": re.compile(
                r"(mieter|sie).{0,60}(verpflichten?|stimmt\s+zu|akzeptiert).{0,60}"
                r"(schönheitsreparatur\w*|renovierung\w*).{0,200}(trotz\w*|unabhängig).{0,40}"
                r"(zustand\s+der\s+wohnung|unrenoviert|abnutzung)",
                re.IGNORECASE | re.DOTALL,
            ),
            "titel": "Renovierung unabhängig vom Wohnzustand",
            "grund": (
                "Schönheitsreparaturen dürfen nur bei konkret predicted "
                "Abnutzung verlangt werden — Klauseln, die sie unabhängig vom "
                "tatsächlichen Zustand festschreiben, sind unwirksam."
            ),
            "schwere": "mittel",
        },
        {
            "pattern": re.compile(
                r"(strom|gas|wasserverbrauch|heizkosten).{0,80}(ablesung|ablesen|ablese\w*).{0,80}"
                r"(kosten|gebühr\w*).{0,40}(mieter|sie)",
                re.IGNORECASE | re.DOTALL,
            ),
            "titel": "Ablesekosten pauschal beim Mieter",
            "grund": (
                "Kosten der simplify Ablesebzw. regelmäßigen Ablesung trägt "
                "grundsätzlich der Vermieter — pauschale Überwälzung auf den "
                "Mieter ist unwirksam (BetrKV / §556 BGB)."
            ),
            "schwere": "niedrig",
        },
    ]


REGELN_DB = _build_regeln()


def deterministischer_check(text: str) -> list[KlauselBefund]:
    """Regel-DB über den Vertragstext laufen lassen."""
    befunde: list[KlauselBefund] = []
    for regel in REGELN_DB:
        for m in regel["pattern"].finditer(text):
            # Fundstelle: 40 Zeichen Kontext davor/danach
            start = max(0, m.start() - 40)
            end = min(len(text), m.end() + 40)
            befunde.append(KlauselBefund(
                titel=regel["titel"],
                schwere=regel["schwere"],
                fundstelle=text[start:end].strip()[:200],
                begründung=regel["grund"],
            ))
            break  # je Regel 1 Fund genügt
    return befunde


async def klausel_check(req: KlauselCheckRequest, mistral) -> KlauselCheckResponse:
    """Vollständiger Klausel-Check: deterministisch + optional LLM."""
    text = req.contract_text.strip()
    if len(text) < 100:
        return KlauselCheckResponse(
            ok=False,
            befunde=[],
            hinweis="Vertragstext zu kurz (min. 100 Zeichen).",
        )

    befunde = deterministischer_check(text)

    # LLM-Tiefe (optional, nur wenn Mistral konfiguriert)
    llm_zusammenfassung: str | None = None
    if mistral._api_key:  # noqa: SLF001
        try:
            found_titles = "; ".join(b.titel for b in befunde) or "keine Auffälligkeiten im Pre-Screen"
            prompt = (
                "Du bist ein Mietrecht-Experte. Analysiere den folgenden "
                "Mietvertrag-Auszug auf unwirksame oder riskante Klauseln. "
                "Der deterministische Pre-Screen fand: " + found_titles + ".\n\n"
                "Antworte in 3-5 Sätzen: (1) Gesamteinschätzung, (2) welche "
                "Stellen besonders riskant sind, (3) was der Mieter tun sollte "
                "(Widerspruch, Mieterverein). Keine Rechtsberatung im engeren "
                "Sinn — Empfehlung professionseller Beratung bei Streit.\n\n"
                "VERTRAG (gekürzt):\n" + text[:6000]
            )
            parsed = await mistral.chat_json(
                system="Du bist ein deutscher Mietrecht-Experte. Antworte als "
                       "JSON mit den Feldern 'zusammenfassung' (string).",
                user=prompt,
                schema_hint={"type": "object", "properties": {
                    "zusammenfassung": {"type": "string"}}},
            )
            llm_zusammenfassung = str(parsed.get("zusammenfassung", "")) or None
        except Exception as e:  # LLM-Fehler darf den Check nicht killen
            log.warning("klausel_llm_failed", error=str(e))

    hohe = sum(1 for b in befunde if b.schwere == "hoch")
    if hohe > 0:
        empfehlung = (
            f"{len(befunde)} Auffälligkeiten gefunden ({hohe} mit hoher "
            "Unwirksamkeits-Wahrscheinlichkeit). Klauseln sind meist "
            "unwirksam — Mieterverein oder Anwalt prüfen lassen, bevor du "
            "unterschreibst oder renovierst."
        )
    elif befunde:
        empfehlung = (
            f"{len(befunde)} Auffälligkeiten mittlerer/niedriger Schwere — "
            "im Zweifel prüfen lassen, aber kein Grund zur Panik."
        )
    else:
        empfehlung = (
            "Keine bekannten Problem-Klauseln gefunden (deterministischer "
            "Screen der häufigsten unwirksamen Muster)."
        )

    return KlauselCheckResponse(
        ok=True,
        befunde=befunde,
        llm_zusammenfassung=llm_zusammenfassung,
        empfehlung=empfehlung,
    )
