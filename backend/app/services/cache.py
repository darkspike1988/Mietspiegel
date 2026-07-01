"""Redis cache wrapper.

Strategy: in-memory LRU when REDIS_URL is empty / dev mode,
real Redis when configured. Used for:
- Mistral chat completions (per-prompt-hash, TTL 24h)
- City reference data (TTL 7d)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any, Optional

import structlog

log = structlog.get_logger(__name__)


class CacheService:
    """Thin async wrapper around optional Redis."""

    def __init__(self, url: str, ttl_seconds: int = 86400) -> None:
        self._url = url
        self._ttl = ttl_seconds
        self._redis: Any = None
        self._mem: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        if not self._url:
            log.info("cache_disabled", msg="Using in-memory LRU; set REDIS_URL for distributed cache")
            return
        try:
            import redis.asyncio as aioredis

            self._redis = aioredis.from_url(self._url, decode_responses=True)
            await self._redis.ping()
            log.info("cache_redis_connected")
        except Exception as e:
            log.warning("cache_redis_unavailable_falling_back", error=str(e))
            self._redis = None

    async def disconnect(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()

    @staticmethod
    def _key(namespace: str, payload: Any) -> str:
        h = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:32]
        return f"msai:{namespace}:{h}"

    async def get(self, namespace: str, payload: Any) -> Optional[dict]:
        key = self._key(namespace, payload)
        if self._redis is not None:
            try:
                raw = await self._redis.get(key)
                return json.loads(raw) if raw else None
            except Exception as e:
                log.warning("cache_get_failed", error=str(e))
        async with self._lock:
            return json.loads(self._mem[key]) if key in self._mem else None

    async def set(self, namespace: str, payload: Any, value: dict, ttl: Optional[int] = None) -> None:
        key = self._key(namespace, payload)
        raw = json.dumps(value, ensure_ascii=False)
        ttl = ttl or self._ttl
        if self._redis is not None:
            try:
                await self._redis.set(key, raw, ex=ttl)
                return
            except Exception as e:
                log.warning("cache_set_failed", error=str(e))
        async with self._lock:
            self._mem[key] = raw