"""Redis client and readiness probe."""

import asyncio

from redis.asyncio import Redis


class RedisCache:
    """Own the shared Redis connection pool and its lifecycle."""

    name = "redis"

    def __init__(self, url: str, timeout_seconds: float) -> None:
        self._client: Redis = Redis.from_url(url, decode_responses=True)
        self._timeout_seconds = timeout_seconds

    async def ping(self) -> None:
        async with asyncio.timeout(self._timeout_seconds):
            await self._client.ping()

    async def close(self) -> None:
        await self._client.aclose()
