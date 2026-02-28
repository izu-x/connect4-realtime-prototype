"""Tests for presence tracking (heartbeat) and stale game filtering.

Covers:
  - Active-game endpoint: only report games with live WebSocket connections.
  - Presence tracking: heartbeat-based online_players count via POST /heartbeat.
  - Stale matchmaking queue: cleared on startup so ghost players are not matched.
  - cleanup_stale_games: repository sets FINISHED status with no winner_id.
"""

from __future__ import annotations

import time
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

from app.connection_manager import HEARTBEAT_TIMEOUT_SECONDS, manager

# ---------------------------------------------------------------------------
# Stale game filtering: active-game with no WS = null
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_active_game_null_without_ws_connections(client: AsyncClient) -> None:
    """A DB game with no live WebSocket connections should NOT be reported as active."""
    player_id = uuid.uuid4()
    game_id = uuid.uuid4()
    mock_game = SimpleNamespace(
        id=game_id,
        player1_id=player_id,
        player2_id=uuid.uuid4(),
        status=SimpleNamespace(value="playing"),
        winner_id=None,
    )

    # DB returns a game, but manager._rooms has NO connections for it
    with patch("app.routes.players.get_active_game", new=AsyncMock(return_value=mock_game)):
        resp = await client.get(f"/players/{player_id}/active-game")

    assert resp.status_code == 200
    assert resp.json()["game"] is None, "Stale DB game with no WS should return null"


@pytest.mark.anyio
async def test_active_game_null_with_empty_ws_list(client: AsyncClient) -> None:
    """A game whose room exists but has zero connections should return null."""
    player_id = uuid.uuid4()
    game_id = uuid.uuid4()
    mock_game = SimpleNamespace(
        id=game_id,
        player1_id=player_id,
        player2_id=uuid.uuid4(),
        status=SimpleNamespace(value="playing"),
        winner_id=None,
    )

    # Room exists but connection list is empty (all disconnected)
    manager._rooms[str(game_id)] = []

    with patch("app.routes.players.get_active_game", new=AsyncMock(return_value=mock_game)):
        resp = await client.get(f"/players/{player_id}/active-game")

    assert resp.status_code == 200
    assert resp.json()["game"] is None, "Empty connection list should be treated as no active game"


@pytest.mark.anyio
async def test_active_game_returned_with_live_ws(client: AsyncClient) -> None:
    """A DB game WITH live WebSocket connections should be reported as active."""
    player_id = uuid.uuid4()
    game_id = uuid.uuid4()
    mock_game = SimpleNamespace(
        id=game_id,
        player1_id=player_id,
        player2_id=uuid.uuid4(),
        status=SimpleNamespace(value="playing"),
        winner_id=None,
    )

    # Simulate a live connection
    manager._rooms[str(game_id)] = [MagicMock()]

    with patch("app.routes.players.get_active_game", new=AsyncMock(return_value=mock_game)):
        resp = await client.get(f"/players/{player_id}/active-game")

    assert resp.status_code == 200
    body = resp.json()
    assert body["game"] is not None
    assert body["game"]["id"] == str(game_id)


@pytest.mark.anyio
async def test_active_game_waiting_without_ws_is_null(client: AsyncClient) -> None:
    """A WAITING game with no WS connections is stale — user refreshed and left."""
    player_id = uuid.uuid4()
    game_id = uuid.uuid4()
    mock_game = SimpleNamespace(
        id=game_id,
        player1_id=player_id,
        player2_id=None,
        status=SimpleNamespace(value="waiting"),
        winner_id=None,
    )

    # No WebSocket connections exist for this game
    with patch("app.routes.players.get_active_game", new=AsyncMock(return_value=mock_game)):
        resp = await client.get(f"/players/{player_id}/active-game")

    assert resp.status_code == 200
    assert resp.json()["game"] is None, "WAITING game without WS = stale, should return null"


# ---------------------------------------------------------------------------
# Presence tracking: heartbeat updates online_players count
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_heartbeat_registers_player_presence(client: AsyncClient) -> None:
    """POST /heartbeat should register the player as online."""
    player_id = str(uuid.uuid4())

    resp = await client.post("/heartbeat", json={"player_id": player_id})
    assert resp.status_code == 204

    assert player_id in manager._presence
    assert manager.online_count() == 1


@pytest.mark.anyio
async def test_heartbeat_without_player_id_is_noop(client: AsyncClient) -> None:
    """POST /heartbeat with no player_id should not crash or add entries."""
    resp = await client.post("/heartbeat", json={"player_id": None})
    assert resp.status_code == 204
    assert manager.online_count() == 0


@pytest.mark.anyio
async def test_stats_reflects_heartbeat_presence(client: AsyncClient) -> None:
    """GET /stats should count players who have sent recent heartbeats."""
    # Register two players via heartbeat
    pid1 = str(uuid.uuid4())
    pid2 = str(uuid.uuid4())
    await client.post("/heartbeat", json={"player_id": pid1})
    await client.post("/heartbeat", json={"player_id": pid2})

    resp = await client.get("/stats")
    assert resp.status_code == 200
    body = resp.json()
    assert body["online_players"] == 2
    assert body["active_games"] == 0


@pytest.mark.anyio
async def test_stats_zero_players_without_heartbeats(client: AsyncClient) -> None:
    """GET /stats should return 0 online players when nobody has sent heartbeats."""
    resp = await client.get("/stats")
    assert resp.status_code == 200
    assert resp.json()["online_players"] == 0


@pytest.mark.anyio
async def test_duplicate_heartbeats_count_once(client: AsyncClient) -> None:
    """Multiple heartbeats from the same player should count as one online player."""
    player_id = str(uuid.uuid4())
    await client.post("/heartbeat", json={"player_id": player_id})
    await client.post("/heartbeat", json={"player_id": player_id})
    await client.post("/heartbeat", json={"player_id": player_id})

    assert manager.online_count() == 1


def test_expired_heartbeats_are_pruned() -> None:
    """Players whose heartbeat is older than the timeout should be pruned."""
    player_id = str(uuid.uuid4())

    # Set a heartbeat far in the past (beyond the timeout window)
    manager._presence[player_id] = time.monotonic() - HEARTBEAT_TIMEOUT_SECONDS - 10

    assert manager.online_count() == 0, "Expired heartbeat should not count"
    assert player_id not in manager._presence, "Expired entry should be pruned"


def test_mixed_fresh_and_expired_heartbeats() -> None:
    """Only fresh heartbeats should count; expired ones get pruned."""
    fresh_id = str(uuid.uuid4())
    stale_id = str(uuid.uuid4())

    manager._presence[fresh_id] = time.monotonic()
    manager._presence[stale_id] = time.monotonic() - HEARTBEAT_TIMEOUT_SECONDS - 10

    assert manager.online_count() == 1, "Only fresh player should count"
    assert fresh_id in manager._presence
    assert stale_id not in manager._presence, "Stale entry should be pruned"


# ---------------------------------------------------------------------------
# Combined: stats with both presence and active games
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_stats_combines_games_and_presence(client: AsyncClient) -> None:
    """GET /stats should independently track active games (WS) and online players (heartbeat)."""
    # One player in lobby (heartbeat only, no game)
    lobby_player = str(uuid.uuid4())
    await client.post("/heartbeat", json={"player_id": lobby_player})

    # One active game room with a connection
    game_id = str(uuid.uuid4())
    manager._rooms[game_id] = [MagicMock()]

    resp = await client.get("/stats")
    body = resp.json()
    assert body["active_games"] == 1
    assert body["online_players"] == 1


@pytest.mark.anyio
async def test_stats_ignores_empty_game_rooms(client: AsyncClient) -> None:
    """Game rooms with empty connection lists should not count as active."""
    manager._rooms["abandoned-game"] = []
    manager._rooms["another-dead-game"] = []

    resp = await client.get("/stats")
    assert resp.json()["active_games"] == 0


# ---------------------------------------------------------------------------
# Stale matchmaking queue cleared on startup
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_matchmaking_queue_cleared_on_startup() -> None:
    """The lifespan startup handler clears stale matchmaking queue entries.

    httpx's ASGITransport dispatches HTTP scopes only and never sends the
    ASGI lifespan scope, so the ``client`` fixture does NOT trigger the
    lifespan handler.  This test invokes the lifespan directly to verify
    that the startup code path actually deletes stale queue entries.
    """
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.main import app as fastapi_app
    from app.main import lifespan
    from tests.conftest import fake_redis_instance

    @asynccontextmanager
    async def _fake_session():
        yield AsyncMock()

    mock_factory = MagicMock(side_effect=_fake_session)

    # Seed stale entries that simulate players left in the queue from a previous session
    await fake_redis_instance.zadd("matchmaking:queue", {"ghost-player-1": 1000.0, "ghost-player-2": 1100.0})
    assert await fake_redis_instance.zcard("matchmaking:queue") == 2

    # Run the real lifespan startup with the DB session mocked out (no DB needed)
    with (
        patch("app.database.async_session_factory", mock_factory),
        patch("app.main.cleanup_stale_games", new=AsyncMock()),
        patch("app.main.get_redis", return_value=fake_redis_instance),
        patch("app.main.close_redis", new=AsyncMock()),
        patch("app.database.close_db", new=AsyncMock()),
    ):
        async with lifespan(fastapi_app):
            # After the startup phase the stale queue must be gone
            assert (
                await fake_redis_instance.zcard("matchmaking:queue") == 0
            ), "Lifespan startup should clear stale matchmaking queue entries"


@pytest.mark.anyio
async def test_cleanup_stale_games_sets_finished_no_winner() -> None:
    """cleanup_stale_games must set status=FINISHED with no winner_id — never DRAW."""
    from unittest.mock import AsyncMock

    from app.repository import cleanup_stale_games

    async def fake_execute(stmt):  # noqa: ANN001
        result = AsyncMock()
        result.rowcount = 2
        return result

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=fake_execute)

    count = await cleanup_stale_games(session)
    assert count == 2

    call_args = session.execute.call_args[0][0]
    compiled = str(call_args.compile(compile_kwargs={"literal_binds": True}))
    assert "finished" in compiled.lower(), "Stale games must be set to FINISHED"
    assert "winner_id" not in compiled.lower() or "null" in compiled.lower(), "Stale games must not assign a winner"


@pytest.mark.anyio
async def test_solo_matchmaking_after_cleanup_is_queued(client: AsyncClient) -> None:
    """After stale queue cleanup, a solo player should be queued, not ghost-matched.

    End-to-end flow: stale queue cleared → player joins → gets 'queued' (not 'matched').
    """
    from tests.conftest import fake_redis_instance

    # Ensure queue is clean (simulating post-startup state)
    await fake_redis_instance.delete("matchmaking:queue")

    player_id = uuid.uuid4()
    mock_player = SimpleNamespace(id=player_id, username="fresh_player", elo_rating=1000)

    with patch("app.routes.matchmaking.get_player_by_id", new=AsyncMock(return_value=mock_player)):
        resp = await client.post(
            "/matchmaking/join",
            json={"player1_id": str(player_id)},
        )

    assert resp.status_code == 200
    assert (
        resp.json()["status"] == "queued"
    ), "Solo player after queue cleanup should be queued, not matched against a ghost"
