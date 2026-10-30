from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from app.scan_lock import ScanLock


class _RedisDouble:
    def __init__(self):
        self.available = False
        self.value: str | None = None
        self.eval_calls = 0

    async def set(self, key: str, value: str, *, nx: bool, ex: int) -> bool:
        if not self.available:
            raise OSError("redis offline")
        if nx and self.value is not None:
            return False
        self.value = value
        return True

    async def eval(self, script: str, key_count: int, key: str, token: str) -> int:
        self.eval_calls += 1
        if not self.available:
            raise OSError("redis offline")
        if self.value == token:
            self.value = None
            return 1
        return 0


class ScanLockTests(unittest.TestCase):
    def test_local_fallback_serializes_scans_and_redis_recovers(self) -> None:
        async def inspect() -> None:
            redis = _RedisDouble()
            lock = ScanLock(redis, "scan-lock")
            with patch("app.scan_lock.logger.exception"):
                self.assertTrue(await lock.acquire("first"))
                self.assertFalse(await lock.acquire("second"))
                await lock.release("first")
                self.assertTrue(await lock.acquire("second"))
                await lock.release("second")

            redis.available = True
            self.assertTrue(await lock.acquire("third"))
            await lock.release("third")
            self.assertEqual(redis.eval_calls, 1)
            self.assertIsNone(redis.value)

        asyncio.run(inspect())


if __name__ == "__main__":
    unittest.main()
