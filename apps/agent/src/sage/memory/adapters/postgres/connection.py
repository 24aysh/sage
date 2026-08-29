"""Explicit bounded Psycopg pool lifecycle for short-lived solves."""

from __future__ import annotations

from psycopg_pool import AsyncConnectionPool

from sage.errors import MemoryStorageError


class MemoryConnectionPool:
    """Own one small runtime pool and never expose its DSN in errors."""

    def __init__(self, dsn: str, *, timeout_seconds: int, max_size: int = 4) -> None:
        self._timeout_seconds = timeout_seconds
        self._pool = AsyncConnectionPool(
            conninfo=dsn,
            min_size=0,
            max_size=max_size,
            timeout=float(timeout_seconds),
            open=False,
        )

    @property
    def pool(self) -> AsyncConnectionPool:
        return self._pool

    @property
    def timeout_seconds(self) -> int:
        return self._timeout_seconds

    async def open(self) -> None:
        try:
            await self._pool.open(wait=True)
        except Exception as error:
            raise MemoryStorageError("Canonical memory storage is unavailable.") from error

    async def close(self) -> None:
        await self._pool.close()
