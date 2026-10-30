from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


class ScanLock:
    """Use Redis across workers and an asyncio lock as a same-process fallback."""

    def __init__(self, redis, key: str, ttl_seconds: int = 120):
        self.redis = redis
        self.key = key
        self.ttl_seconds = ttl_seconds
        self._local = asyncio.Lock()
        self._local_token: str | None = None

    async def acquire(self, token: str) -> bool:
        try:
            return bool(await self.redis.set(self.key, token, nx=True, ex=self.ttl_seconds))
        except Exception:
            logger.exception("Redis lock unavailable; using a process-local scan lock")
            if self._local.locked():
                return False
            await self._local.acquire()
            self._local_token = token
            return True

    async def release(self, token: str) -> None:
        if self._local_token == token:
            self._local_token = None
            self._local.release()
            return
        try:
            await self.redis.eval(
                "if redis.call('get', KEYS[1]) == ARGV[1] then "
                "return redis.call('del', KEYS[1]) else return 0 end",
                1,
                self.key,
                token,
            )
        except Exception:
            logger.exception("Failed to release Redis scan lock")
