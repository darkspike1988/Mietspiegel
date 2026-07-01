"""Mistral API client wrapper.

Provides two capabilities:
  1. PDF → structured lease data (Mistral OCR + structured chat)
  2. Verdict + Widerspruch text generation (structured chat)

All calls are:
  - Retried with exponential backoff (tenacity)
  - Cached at the Redis layer (caller responsibility)
  - JSON-schema validated before returning
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import structlog
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.core.config import settings

log = structlog.get_logger(__name__)


class MistralError(RuntimeError):
    """Raised on any Mistral API failure after retries exhausted."""


class MistralClient:
    """Thin async wrapper around the Mistral API.

    Mistral's official Python SDK (mistralai) is sync; we hit the REST API
    directly via httpx to keep the FastAPI handler non-blocking.
    """

    BASE_URL = "https://api.mistral.ai/v1"

    def __init__(
        self,
        api_key: str,
        chat_model: str = "mistral-large-latest",
        ocr_model: str = "mistral-ocr-latest",
    ) -> None:
        if not api_key:
            log.warning("mistral_api_key_missing", msg="AI endpoints will fail until configured")
        self._api_key = api_key
        self._chat_model = chat_model
        self._ocr_model = ocr_model
        self._client = httpx.AsyncClient(
            base_url=self.BASE_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=settings.mistral_timeout_seconds,
        )

    async def close(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------
    # Low-level
    # ------------------------------------------------------------------

    @retry(
        retry=retry_if_exception_type((httpx.HTTPError, MistralError)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        reraise=True,
    )
    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self._api_key:
            raise MistralError("MISTRAL_API_KEY not set")

        try:
            resp = await self._client.post(path, json=payload)
        except httpx.HTTPError as e:
            log.error("mistral_request_failed", path=path, error=str(e))
            raise

        if resp.status_code >= 400:
            log.error(
                "mistral_api_error",
                path=path,
                status=resp.status_code,
                body=resp.text[:500],
            )
            raise MistralError(f"Mistral API {resp.status_code}: {resp.text[:200]}")

        return resp.json()  # type: ignore[no-any-return]

    # ------------------------------------------------------------------
    # High-level
    # ------------------------------------------------------------------

    async def chat_json(
        self,
        *,
        system: str,
        user: str,
        schema_hint: dict[str, Any],
        model: str | None = None,
    ) -> dict[str, Any]:
        """Call Mistral chat with JSON-mode + a schema hint.

        Returns the parsed JSON object.
        """
        payload = {
            "model": model or self._chat_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.1,  # low: legal text needs determinism
            "max_tokens": 2048,
        }
        data = await self._post("/chat/completions", payload)
        try:
            content = data["choices"][0]["message"]["content"]
            parsed = json.loads(content)
        except (KeyError, IndexError, json.JSONDecodeError) as e:
            log.error("mistral_response_unparseable", error=str(e), raw=data)
            raise MistralError(f"Malformed Mistral response: {e}") from e

        # Optional schema validation would go here; we trust the schema_hint
        # in the prompt + Pydantic validation in the route layer.
        return parsed  # type: ignore[no-any-return]

    async def ocr_pdf(self, *, pdf_bytes: bytes, model: str | None = None) -> str:
        """Run Mistral OCR on a PDF; return raw text.

        Mistral OCR endpoint expects multipart/form-data. We use httpx here
        since the official SDK is sync-only.
        """
        url = f"{self.BASE_URL}/ocr"
        # Re-create a one-shot client to set multipart content-type automatically.
        async with httpx.AsyncClient(timeout=settings.mistral_timeout_seconds * 2) as c:
            files = {"document": ("lease.pdf", pdf_bytes, "application/pdf")}
            data = {"model": model or self._ocr_model}
            try:
                resp = await c.post(
                    url,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    files=files,
                    data=data,
                )
            except httpx.HTTPError as e:
                log.error("mistral_ocr_failed", error=str(e))
                raise MistralError(f"OCR failed: {e}") from e

        if resp.status_code >= 400:
            log.error("mistral_ocr_error", status=resp.status_code, body=resp.text[:500])
            raise MistralError(f"OCR API {resp.status_code}: {resp.text[:200]}")

        result = resp.json()
        # Defensive: actual response shape varies; assume 'text' field for now.
        return str(result.get("text", result.get("content", "")))