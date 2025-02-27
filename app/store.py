"""Redis-backed state store — the 'hot data' / Present Truth layer."""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Final

import redis.asyncio as aioredis

from app.game import Connect4

_REDIS_URL: Final[str] = os.getenv("REDIS_URL", "redis://localhost:6379")
_TTL_SECONDS: Final[int] = int(os.getenv("GAME_TTL_SECONDS", "86400"))

_redis_client: aioredis.Redis | None = None


async def get_redis() -> aioredis.Redis:
    """Return the shared Redis client, creating it on first call.

    Returns:
        Async Redis client instance.
    """
    global _redis_client
    if _redis_client is None:
        _redis_client = aioredis.from_url(_REDIS_URL, decode_responses=True)
    return _redis_client


async def close_redis() -> None:
    """Close the shared Redis client and release the connection."""
    global _redis_client
    if _redis_client is not None:
        await _redis_client.aclose()
        _redis_client = None


@asynccontextmanager
async def acquire_game_lock(redis: aioredis.Redis, game_id: str) -> AsyncIterator[bool]:
    """SETNX-based distributed lock to prevent race conditions on the same game.

    Args:
        redis: Async Redis client.
        game_id: Unique identifier of the game to lock.

    Yields:
        True when the lock is acquired; the caller should handle
        the False case to return a 409 Conflict response.
    """
    lock_key = f"lock:{game_id}"
    acquired = await redis.set(lock_key, "1", nx=True, ex=5)
    try:
        yield bool(acquired)
    finally:
        if acquired:
            await redis.delete(lock_key)


async def load_game(redis: aioredis.Redis, game_id: str) -> Connect4:
    """Load game state from Redis.

    Args:
        redis: Async Redis client.
        game_id: Unique identifier of the game to load.

    Returns:
        Connect4 instance with the current board state.

    Raises:
        KeyError: If the game key does not exist in Redis (never created or TTL expired).
    """
    raw = await redis.get(f"game:{game_id}")
    if raw is None:
        raise KeyError(f"game:{game_id}")
    board = json.loads(raw)
    return Connect4(board=board)


async def save_game(redis: aioredis.Redis, game_id: str, game: Connect4) -> None:
    """Persist board state to Redis with a sliding TTL.

    Args:
        redis: Async Redis client.
        game_id: Unique identifier of the game to persist.
        game: Connect4 instance whose board will be serialised.
    """
    await redis.set(f"game:{game_id}", json.dumps(game.board), ex=_TTL_SECONDS)
