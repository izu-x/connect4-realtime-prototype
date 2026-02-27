"""Tests for the Redis state store module."""

from __future__ import annotations

import json

import pytest

from app.game import Connect4
from app.store import acquire_game_lock, load_game, save_game
from tests.conftest import fake_redis_instance

# ---------------------------------------------------------------------------
# load_game
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_load_game_fresh() -> None:
    """Loading a non-existent game should raise KeyError."""
    with pytest.raises(KeyError, match="game:new-game"):
        await load_game(fake_redis_instance, "new-game")


@pytest.mark.anyio
async def test_load_game_existing() -> None:
    """Loading an existing game should reconstruct the board state."""
    board = [[0] * 7 for _ in range(6)]
    board[5][3] = 1
    board[5][4] = 2
    fake_redis_instance._store["game:existing"] = json.dumps(board)

    game = await load_game(fake_redis_instance, "existing")
    assert game.board[5][3] == 1
    assert game.board[5][4] == 2
    assert game.next_player == 1  # 2 pieces → even → player 1


# ---------------------------------------------------------------------------
# save_game
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_save_game_persists() -> None:
    """Saving a game should store its board in Redis."""
    game = Connect4()
    game.drop(1, 3)

    await save_game(fake_redis_instance, "save-test", game)

    raw = fake_redis_instance._store.get("game:save-test")
    assert raw is not None
    board = json.loads(raw)
    assert board[5][3] == 1


@pytest.mark.anyio
async def test_save_then_load_roundtrip() -> None:
    """Save and load should produce an equivalent game state."""
    game = Connect4()
    game.drop(1, 0)
    game.drop(2, 1)
    game.drop(1, 2)

    await save_game(fake_redis_instance, "roundtrip", game)
    loaded = await load_game(fake_redis_instance, "roundtrip")

    assert loaded.board == game.board
    assert loaded.next_player == game.next_player
    assert loaded.winner == game.winner


# ---------------------------------------------------------------------------
# acquire_game_lock
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_acquire_lock_success() -> None:
    """First lock attempt should succeed."""
    async with acquire_game_lock(fake_redis_instance, "lock-game") as locked:
        assert locked is True


@pytest.mark.anyio
async def test_acquire_lock_released_after_context() -> None:
    """Lock should be released after the context manager exits."""
    async with acquire_game_lock(fake_redis_instance, "release-game") as locked:
        assert locked is True
        assert "lock:release-game" in fake_redis_instance._locks

    # After context exit, lock should be removed
    assert "lock:release-game" not in fake_redis_instance._locks


@pytest.mark.anyio
async def test_acquire_lock_contention() -> None:
    """Second concurrent lock attempt should fail."""
    async with acquire_game_lock(fake_redis_instance, "contested") as first:
        assert first is True
        # Try to acquire again while still held
        async with acquire_game_lock(fake_redis_instance, "contested") as second:
            assert second is False


@pytest.mark.anyio
async def test_lock_not_released_on_contention_failure() -> None:
    """When lock acquisition fails, the finally block should not delete the lock."""
    # Pre-acquire the lock (simulate another process holding it)
    await fake_redis_instance.set("lock:held", "1", nx=True)

    async with acquire_game_lock(fake_redis_instance, "held") as locked:
        assert locked is False

    # The original lock should still be held (not deleted by the failed attempt)
    assert "lock:held" in fake_redis_instance._locks


# ---------------------------------------------------------------------------
# FakeRedis sorted set operations
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_fake_redis_zadd_and_zcard() -> None:
    """zadd should add members and zcard should count them."""
    await fake_redis_instance.zadd("test:set", {"a": 1.0, "b": 2.0})
    assert await fake_redis_instance.zcard("test:set") == 2


@pytest.mark.anyio
async def test_fake_redis_zrem() -> None:
    """zrem should remove a member from the sorted set."""
    await fake_redis_instance.zadd("test:set2", {"a": 1.0, "b": 2.0})
    removed = await fake_redis_instance.zrem("test:set2", "a")
    assert removed == 1
    assert await fake_redis_instance.zcard("test:set2") == 1


@pytest.mark.anyio
async def test_fake_redis_zrangebyscore() -> None:
    """zrangebyscore should return members within the score range."""
    await fake_redis_instance.zadd("elo:queue", {"p1": 1000.0, "p2": 1100.0, "p3": 1500.0})
    results = await fake_redis_instance.zrangebyscore("elo:queue", min=900, max=1200)
    assert "p1" in results
    assert "p2" in results
    assert "p3" not in results


@pytest.mark.anyio
async def test_fake_redis_zrangebyscore_withscores() -> None:
    """zrangebyscore with withscores=True should return (member, score) tuples."""
    await fake_redis_instance.zadd("elo:q2", {"p1": 1000.0, "p2": 1100.0})
    results = await fake_redis_instance.zrangebyscore("elo:q2", min=900, max=1200, withscores=True)
    assert len(results) == 2
    assert results[0] == ("p1", 1000.0)
    assert results[1] == ("p2", 1100.0)


@pytest.mark.anyio
async def test_fake_redis_zrank() -> None:
    """zrank should return the 0-based rank of a member."""
    await fake_redis_instance.zadd("rank:test", {"a": 1.0, "b": 2.0, "c": 3.0})
    assert await fake_redis_instance.zrank("rank:test", "a") == 0
    assert await fake_redis_instance.zrank("rank:test", "c") == 2
    assert await fake_redis_instance.zrank("rank:test", "missing") is None


@pytest.mark.anyio
async def test_fake_redis_delete_clears_sorted_set() -> None:
    """delete should also remove sorted sets."""
    await fake_redis_instance.zadd("del:set", {"a": 1.0})
    assert await fake_redis_instance.zcard("del:set") == 1

    await fake_redis_instance.delete("del:set")
    assert await fake_redis_instance.zcard("del:set") == 0


@pytest.mark.anyio
async def test_fake_redis_aclose() -> None:
    """aclose should be a no-op and not raise."""
    await fake_redis_instance.aclose()
