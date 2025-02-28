"""Shared test fixtures: FakeRedis with sorted-set support, patched HTTP client."""

from __future__ import annotations

import collections.abc
import json
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient  # noqa: F401

from app.connection_manager import manager
from app.database import get_db
from app.main import app

# ---------------------------------------------------------------------------
# FakeRedis — in-process fake with sorted-set support for matchmaking
# ---------------------------------------------------------------------------


class FakeRedis:
    """In-memory Redis fake that supports strings, locks, and sorted sets."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}
        self._locks: set[str] = set()
        self._sorted_sets: dict[str, dict[str, float]] = {}

    # -- string commands -----------------------------------------------------

    async def get(self, key: str) -> str | None:
        """Return value for *key*, or ``None``."""
        return self._store.get(key)

    async def set(
        self,
        key: str,
        value: str,
        nx: bool = False,
        ex: int | None = None,
    ) -> bool:
        """Set *key* to *value*.  NX mode emulates SETNX for locks."""
        if nx:
            if key in self._locks:
                return False
            self._locks.add(key)
            return True
        self._store[key] = value
        return True

    async def delete(self, key: str) -> None:
        """Remove *key* from all stores (strings, locks, and sorted sets)."""
        self._locks.discard(key)
        self._store.pop(key, None)
        self._sorted_sets.pop(key, None)

    # -- sorted-set commands -------------------------------------------------

    async def zadd(self, key: str, mapping: dict[str, float]) -> int:
        """Add members with scores to the sorted set at *key*."""
        sorted_set = self._sorted_sets.setdefault(key, {})
        added = 0
        for member, score in mapping.items():
            if member not in sorted_set:
                added += 1
            sorted_set[member] = score
        return added

    async def zrangebyscore(
        self,
        key: str,
        min: float,
        max: float,
        withscores: bool = False,
    ) -> list:
        """Return members with scores between *min* and *max*."""
        sorted_set = self._sorted_sets.get(key, {})
        results = [
            (member, score)
            for member, score in sorted(sorted_set.items(), key=lambda item: item[1])
            if min <= score <= max
        ]
        if withscores:
            return results
        return [member for member, _score in results]

    async def zrem(self, key: str, *members: str) -> int:
        """Remove *members* from the sorted set at *key*."""
        sorted_set = self._sorted_sets.get(key, {})
        removed = 0
        for member in members:
            if member in sorted_set:
                del sorted_set[member]
                removed += 1
        return removed

    async def zrank(self, key: str, member: str) -> int | None:
        """Return the 0-based rank of *member*, or ``None`` if absent."""
        sorted_set = self._sorted_sets.get(key, {})
        if member not in sorted_set:
            return None
        sorted_members = sorted(sorted_set.keys(), key=lambda m: sorted_set[m])
        return sorted_members.index(member)

    async def zcard(self, key: str) -> int:
        """Return the number of members in the sorted set at *key*."""
        return len(self._sorted_sets.get(key, {}))

    # -- lifecycle -----------------------------------------------------------

    async def aclose(self) -> None:
        """No-op for compatibility."""

    def reset(self) -> None:
        """Clear all data between tests."""
        self._store.clear()
        self._locks.clear()
        self._sorted_sets.clear()


fake_redis_instance: FakeRedis = FakeRedis()


def _empty_board() -> list[list[int]]:
    """Return a fresh 6-row × 7-column board filled with zeros."""
    return [[0] * 7 for _ in range(6)]


def _seed_redis_board(game_id: str, board: list[list[int]] | None = None) -> None:
    """Pre-load a board into FakeRedis for the given game.

    Mirrors what ``join_existing_game`` does in production so REST and WS
    tests start from a valid Redis state.

    Args:
        game_id: The game identifier string used as the Redis key suffix.
        board: Optional custom board; defaults to an empty 6×7 board.
    """
    fake_redis_instance._store[f"game:{game_id}"] = json.dumps(board or _empty_board())


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_test_state() -> collections.abc.Generator[None]:
    """Reset shared state (Redis fake + ConnectionManager) between tests."""
    fake_redis_instance.reset()
    manager._rooms.clear()
    manager._player_map.clear()
    manager._rematch_votes.clear()
    manager._usernames.clear()
    manager._presence.clear()
    manager._db_game_id.clear()
    yield


@pytest.fixture
def anyio_backend() -> str:
    """Use asyncio as the default async backend for pytest-anyio."""
    return "asyncio"


async def _mock_get_db() -> collections.abc.AsyncGenerator[AsyncMock]:
    """Dependency override that returns a mock database session."""
    yield AsyncMock()


@pytest.fixture
async def client() -> collections.abc.AsyncGenerator[AsyncClient]:
    """HTTP test client with Redis faked and DB dependency overridden.

    Endpoints that use ``Depends(get_db)`` receive a mock session.
    Tests must patch repository functions individually if they are
    called by the endpoint under test.
    """
    app.dependency_overrides[get_db] = _mock_get_db
    with (
        patch("app.store._redis_client", fake_redis_instance),
        patch("app.store.get_redis", return_value=fake_redis_instance),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as async_client:
            yield async_client
    app.dependency_overrides.clear()
