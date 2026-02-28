"""FastAPI endpoint tests — REST API for moves, stats, players, and winning cells."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

from app.connection_manager import manager
from tests.conftest import _seed_redis_board, fake_redis_instance

# ---------------------------------------------------------------------------
# Move mechanics
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_make_move_returns_200(client: AsyncClient) -> None:
    """A valid move should return 200 with correct board state."""
    _seed_redis_board("game1")
    resp = await client.post(
        "/games/game1/move",
        json={"game_id": "game1", "player": 1, "column": 3},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["player"] == 1
    assert body["column"] == 3
    assert body["row"] == 5  # bottom row


@pytest.mark.anyio
async def test_full_column_returns_422(client: AsyncClient) -> None:
    """Dropping into a full column should return 422."""
    from app.game import ROWS

    _seed_redis_board("full")
    for turn_index in range(ROWS):
        player = 1 if turn_index % 2 == 0 else 2
        await client.post(
            "/games/full/move",
            json={"game_id": "full", "player": player, "column": 0},
        )
    resp = await client.post(
        "/games/full/move",
        json={"game_id": "full", "player": 1, "column": 0},
    )
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_win_detected_in_response(client: AsyncClient) -> None:
    """Player 1 wins horizontally across columns 0-3."""
    _seed_redis_board("g2")
    for col in range(3):
        await client.post("/games/g2/move", json={"game_id": "g2", "player": 1, "column": col})
        await client.post("/games/g2/move", json={"game_id": "g2", "player": 2, "column": 6})
    resp = await client.post("/games/g2/move", json={"game_id": "g2", "player": 1, "column": 3})
    assert resp.status_code == 200
    assert resp.json()["winner"] == 1


@pytest.mark.anyio
async def test_get_game_returns_board(client: AsyncClient) -> None:
    """GET /games/{id} should return the current board."""
    _seed_redis_board("g3")
    await client.post("/games/g3/move", json={"game_id": "g3", "player": 1, "column": 2})
    resp = await client.get("/games/g3")
    assert resp.status_code == 200
    board = resp.json()["board"]
    assert board[5][2] == 1


@pytest.mark.anyio
async def test_game_id_mismatch_returns_422(client: AsyncClient) -> None:
    """Mismatched game_id in URL vs body should return 422."""
    resp = await client.post(
        "/games/game-A/move",
        json={"game_id": "game-B", "player": 1, "column": 0},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Winning cells in responses
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_winning_cells_in_move_response(client: AsyncClient) -> None:
    """A winning move should include the coordinates of the winning cells."""
    _seed_redis_board("wc")
    for col in range(3):
        await client.post("/games/wc/move", json={"game_id": "wc", "player": 1, "column": col})
        await client.post("/games/wc/move", json={"game_id": "wc", "player": 2, "column": 6})
    resp = await client.post("/games/wc/move", json={"game_id": "wc", "player": 1, "column": 3})
    assert resp.status_code == 200
    body = resp.json()
    assert body["winner"] == 1
    assert len(body["winning_cells"]) >= 4
    for cell in body["winning_cells"]:
        assert cell[0] == 5  # all on the bottom row


@pytest.mark.anyio
async def test_winning_cells_in_get_game(client: AsyncClient) -> None:
    """GET /games/{id} should also include winning_cells after a win."""
    _seed_redis_board("wg")
    for col in range(3):
        await client.post("/games/wg/move", json={"game_id": "wg", "player": 1, "column": col})
        await client.post("/games/wg/move", json={"game_id": "wg", "player": 2, "column": 6})
    await client.post("/games/wg/move", json={"game_id": "wg", "player": 1, "column": 3})

    resp = await client.get("/games/wg")
    body = resp.json()
    assert body["winner"] == 1
    assert len(body["winning_cells"]) >= 4


@pytest.mark.anyio
async def test_no_winning_cells_mid_game(client: AsyncClient) -> None:
    """Mid-game responses should have empty winning_cells."""
    _seed_redis_board("mid")
    resp = await client.post(
        "/games/mid/move",
        json={"game_id": "mid", "player": 1, "column": 0},
    )
    body = resp.json()
    assert body["winner"] is None
    assert body["winning_cells"] == []


# ---------------------------------------------------------------------------
# Live stats
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_live_stats_returns_counts(client: AsyncClient) -> None:
    """Stats should return zero counts when no WebSocket connections exist."""
    resp = await client.get("/stats")
    assert resp.status_code == 200
    body = resp.json()
    assert body["active_games"] == 0
    assert body["online_players"] == 0


# ---------------------------------------------------------------------------
# Player registration
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Player games
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_player_games_returns_list(client: AsyncClient) -> None:
    """GET /players/{id}/games should return the player's game history."""
    player_id = uuid.uuid4()
    game_id = uuid.uuid4()
    mock_game = SimpleNamespace(
        id=game_id,
        player1_id=player_id,
        player2_id=uuid.uuid4(),
        status=SimpleNamespace(value="finished"),
        winner_id=player_id,
    )

    with patch("app.routes.players.get_player_games", new=AsyncMock(return_value=[mock_game])):
        resp = await client.get(f"/players/{player_id}/games?limit=5")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["id"] == str(game_id)
    assert body[0]["winner_id"] == str(player_id)


@pytest.mark.anyio
async def test_player_games_empty_for_new_player(client: AsyncClient) -> None:
    """A brand-new player should have zero games."""
    with patch("app.routes.players.get_player_games", new=AsyncMock(return_value=[])):
        resp = await client.get(f"/players/{uuid.uuid4()}/games")

    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# Active game (rejoin)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_active_game_returns_game_info(client: AsyncClient) -> None:
    """GET /players/{id}/active-game should return the in-progress game when WS connections exist."""
    player_id = uuid.uuid4()
    game_id = uuid.uuid4()
    mock_game = SimpleNamespace(
        id=game_id,
        player1_id=player_id,
        player2_id=uuid.uuid4(),
        status=SimpleNamespace(value="playing"),
        winner_id=None,
    )

    # Simulate a live WebSocket connection for the game
    manager._rooms[str(game_id)] = [MagicMock()]

    with patch("app.routes.players.get_active_game", new=AsyncMock(return_value=mock_game)):
        resp = await client.get(f"/players/{player_id}/active-game")

    assert resp.status_code == 200
    body = resp.json()
    assert body["game"] is not None
    assert body["game"]["id"] == str(game_id)
    assert body["game"]["my_player"] == 1


@pytest.mark.anyio
async def test_active_game_returns_null_when_none(client: AsyncClient) -> None:
    """GET /players/{id}/active-game should return null game when idle."""
    with patch("app.routes.players.get_active_game", new=AsyncMock(return_value=None)):
        resp = await client.get(f"/players/{uuid.uuid4()}/active-game")

    assert resp.status_code == 200
    assert resp.json()["game"] is None


@pytest.mark.anyio
async def test_active_game_player2_gets_correct_role(client: AsyncClient) -> None:
    """Player 2 should get my_player=2 from the active-game endpoint."""
    player_id = uuid.uuid4()
    game_id = uuid.uuid4()
    mock_game = SimpleNamespace(
        id=game_id,
        player1_id=uuid.uuid4(),
        player2_id=player_id,
        status=SimpleNamespace(value="playing"),
        winner_id=None,
    )

    # Simulate a live WebSocket connection for the game
    manager._rooms[str(game_id)] = [MagicMock()]

    with patch("app.routes.players.get_active_game", new=AsyncMock(return_value=mock_game)):
        resp = await client.get(f"/players/{player_id}/active-game")

    assert resp.status_code == 200
    assert resp.json()["game"]["my_player"] == 2


@pytest.mark.anyio
async def test_active_game_returns_waiting_game(client: AsyncClient) -> None:
    """GET /players/{id}/active-game should also find games in WAITING status when WS is live."""
    player_id = uuid.uuid4()
    game_id = uuid.uuid4()
    mock_game = SimpleNamespace(
        id=game_id,
        player1_id=player_id,
        player2_id=None,
        status=SimpleNamespace(value="waiting"),
        winner_id=None,
    )

    # Simulate a live WebSocket connection for the game
    manager._rooms[str(game_id)] = [MagicMock()]

    with patch("app.routes.players.get_active_game", new=AsyncMock(return_value=mock_game)):
        resp = await client.get(f"/players/{player_id}/active-game")

    assert resp.status_code == 200
    body = resp.json()
    assert body["game"] is not None
    assert body["game"]["status"] == "waiting"
    assert body["game"]["my_player"] == 1


# ---------------------------------------------------------------------------
# Player registration
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_register_new_player(client: AsyncClient) -> None:
    """Registering a new username should create the player."""
    player_id = uuid.uuid4()
    mock_player = SimpleNamespace(id=player_id, username="alice", elo_rating=1000)

    with (
        patch("app.routes.players.get_player_by_username", new=AsyncMock(return_value=None)),
        patch("app.routes.players.create_player", new=AsyncMock(return_value=mock_player)),
    ):
        resp = await client.post("/players", json={"username": "alice"})

    assert resp.status_code == 201
    body = resp.json()
    assert body["username"] == "alice"
    assert body["is_returning"] is False
    assert body["games"] == []


@pytest.mark.anyio
async def test_register_returning_player(client: AsyncClient) -> None:
    """Registering with an existing username should mark as returning."""
    player_id = uuid.uuid4()
    mock_player = SimpleNamespace(id=player_id, username="bob", elo_rating=1200)

    with (
        patch("app.routes.players.get_player_by_username", new=AsyncMock(return_value=mock_player)),
        patch("app.routes.players.get_player_games", new=AsyncMock(return_value=[])),
    ):
        resp = await client.post("/players", json={"username": "bob"})

    assert resp.status_code == 201
    body = resp.json()
    assert body["username"] == "bob"
    assert body["is_returning"] is True
    assert body["elo_rating"] == 1200


# ---------------------------------------------------------------------------
# Move error paths
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_game_over_move_returns_409(client: AsyncClient) -> None:
    """Dropping after a win should return 409 Conflict."""
    _seed_redis_board("over")
    # Build a game where player 1 wins
    for col in range(3):
        await client.post("/games/over/move", json={"game_id": "over", "player": 1, "column": col})
        await client.post("/games/over/move", json={"game_id": "over", "player": 2, "column": 6})
    await client.post("/games/over/move", json={"game_id": "over", "player": 1, "column": 3})

    # Now try another move — game is over
    resp = await client.post("/games/over/move", json={"game_id": "over", "player": 2, "column": 0})
    assert resp.status_code == 409


@pytest.mark.anyio
async def test_invalid_turn_returns_422(client: AsyncClient) -> None:
    """Playing out of turn should return 422."""
    _seed_redis_board("turn")
    resp = await client.post("/games/turn/move", json={"game_id": "turn", "player": 2, "column": 0})
    assert resp.status_code == 422
    assert "turn" in resp.json()["detail"].lower()


@pytest.mark.anyio
async def test_lock_contention_returns_409(client: AsyncClient) -> None:
    """If the game lock is held, the endpoint should return 409."""
    # Pre-acquire the lock in FakeRedis
    await fake_redis_instance.set("lock:locked-game", "1", nx=True)

    resp = await client.post(
        "/games/locked-game/move",
        json={"game_id": "locked-game", "player": 1, "column": 0},
    )
    assert resp.status_code == 409
    assert "retry" in resp.json()["detail"].lower()


@pytest.mark.anyio
async def test_draw_detected_in_rest_response(client: AsyncClient) -> None:
    """A draw should be reported in the move response."""
    import json

    # Build an almost-full board that draws (no 4-in-a-row)
    # Use the known draw pattern with one empty cell at (0,6)
    # Verified: max run length = 3 in any direction
    board = [
        [2, 2, 1, 2, 2, 1, 0],  # one empty at (0,6)
        [1, 1, 2, 1, 1, 2, 1],
        [2, 2, 1, 2, 2, 1, 2],
        [1, 1, 2, 1, 1, 2, 1],
        [2, 2, 1, 2, 2, 1, 2],
        [1, 1, 2, 1, 1, 2, 1],
    ]
    fake_redis_instance._store["game:draw-game"] = json.dumps(board)

    # 41 pieces → odd → next_player = 2
    resp = await client.post("/games/draw-game/move", json={"game_id": "draw-game", "player": 2, "column": 6})
    assert resp.status_code == 200
    body = resp.json()
    assert body["draw"] is True


# ---------------------------------------------------------------------------
# Game CRUD endpoints
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_create_game(client: AsyncClient) -> None:
    """POST /games should create a game with WAITING status."""
    player_id = uuid.uuid4()
    game_id = uuid.uuid4()
    mock_game = SimpleNamespace(
        id=game_id,
        player1_id=player_id,
        player2_id=None,
        status=SimpleNamespace(value="waiting"),
        winner_id=None,
    )

    with patch("app.routes.games.create_game", new=AsyncMock(return_value=mock_game)):
        resp = await client.post("/games", json={"player1_id": str(player_id)})

    assert resp.status_code == 201
    body = resp.json()
    assert body["id"] == str(game_id)
    assert body["status"] == "waiting"
    assert body["player2_id"] is None


@pytest.mark.anyio
async def test_join_game(client: AsyncClient) -> None:
    """POST /games/{id}/join should transition game to PLAYING."""
    game_id = uuid.uuid4()
    p1_id = uuid.uuid4()
    p2_id = uuid.uuid4()
    mock_game = SimpleNamespace(
        id=game_id,
        player1_id=p1_id,
        player2_id=p2_id,
        status=SimpleNamespace(value="playing"),
        winner_id=None,
    )

    with patch("app.routes.games.join_game", new=AsyncMock(return_value=mock_game)):
        resp = await client.post(f"/games/{game_id}/join", json={"player2_id": str(p2_id)})

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "playing"
    assert body["player2_id"] == str(p2_id)


@pytest.mark.anyio
async def test_join_nonexistent_game_returns_404(client: AsyncClient) -> None:
    """Joining a game that doesn't exist should return 404."""
    with patch("app.routes.games.join_game", new=AsyncMock(return_value=None)):
        resp = await client.post(
            f"/games/{uuid.uuid4()}/join",
            json={"player2_id": str(uuid.uuid4())},
        )

    assert resp.status_code == 404


@pytest.mark.anyio
async def test_game_status_found(client: AsyncClient) -> None:
    """GET /games/{id}/status should return the game's DB status with player names."""
    game_id = uuid.uuid4()
    p1_id = uuid.uuid4()
    p2_id = uuid.uuid4()
    mock_game = SimpleNamespace(
        id=game_id,
        player1_id=p1_id,
        player2_id=p2_id,
        status=SimpleNamespace(value="playing"),
        winner_id=None,
    )
    mock_p1 = SimpleNamespace(username="Alice")
    mock_p2 = SimpleNamespace(username="Bob")

    async def _get_player(_db: Any, pid: uuid.UUID) -> SimpleNamespace | None:
        if pid == p1_id:
            return mock_p1
        if pid == p2_id:
            return mock_p2
        return None

    with (
        patch("app.routes.games.get_game_by_id", new=AsyncMock(return_value=mock_game)),
        patch("app.routes.games.get_player_by_id", new=AsyncMock(side_effect=_get_player)),
    ):
        resp = await client.get(f"/games/{game_id}/status")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "playing"
    assert body["player1_name"] == "Alice"
    assert body["player2_name"] == "Bob"


@pytest.mark.anyio
async def test_game_status_not_found(client: AsyncClient) -> None:
    """GET /games/{id}/status for a nonexistent game should return 404."""
    with patch("app.routes.games.get_game_by_id", new=AsyncMock(return_value=None)):
        resp = await client.get(f"/games/{uuid.uuid4()}/status")

    assert resp.status_code == 404


@pytest.mark.anyio
async def test_recent_games_returns_list(client: AsyncClient) -> None:
    """GET /games/recent should return recently finished games."""
    game_id = uuid.uuid4()
    mock_game = SimpleNamespace(
        id=game_id,
        player1_id=uuid.uuid4(),
        player2_id=uuid.uuid4(),
        status=SimpleNamespace(value="finished"),
        winner_id=uuid.uuid4(),
    )

    with patch("app.routes.games.get_recent_games", new=AsyncMock(return_value=[mock_game])):
        resp = await client.get("/games/recent?limit=5")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["status"] == "finished"


@pytest.mark.anyio
async def test_recent_games_empty(client: AsyncClient) -> None:
    """GET /games/recent with no games should return empty list."""
    with patch("app.routes.games.get_recent_games", new=AsyncMock(return_value=[])):
        resp = await client.get("/games/recent")

    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.anyio
async def test_waiting_games_returns_list(client: AsyncClient) -> None:
    """GET /games/waiting should return games waiting for a second player."""
    game_id = uuid.uuid4()
    mock_game = SimpleNamespace(
        id=game_id,
        player1_id=uuid.uuid4(),
        player2_id=None,
        status=SimpleNamespace(value="waiting"),
        winner_id=None,
    )

    with patch("app.routes.games.get_waiting_games", new=AsyncMock(return_value=[mock_game])):
        resp = await client.get("/games/waiting")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["status"] == "waiting"


@pytest.mark.anyio
async def test_leaderboard_returns_entries(client: AsyncClient) -> None:
    """GET /leaderboard should return top players who have played games."""
    mock_row = {"username": "alice", "elo_rating": 1200, "total_games": 5}

    with patch("app.routes.players.get_leaderboard", new=AsyncMock(return_value=[mock_row])):
        resp = await client.get("/leaderboard?limit=5")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["username"] == "alice"
    assert body[0]["elo_rating"] == 1200
    assert body[0]["total_games"] == 5


@pytest.mark.anyio
async def test_leaderboard_empty(client: AsyncClient) -> None:
    """GET /leaderboard with no players should return empty list."""
    with patch("app.routes.players.get_leaderboard", new=AsyncMock(return_value=[])):
        resp = await client.get("/leaderboard")

    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.anyio
async def test_game_moves_returns_list(client: AsyncClient) -> None:
    """GET /games/{id}/moves should return the move history."""
    game_id = uuid.uuid4()
    mock_move = SimpleNamespace(player=1, column=3, row=5, move_number=1)

    with patch("app.routes.games.get_game_moves", new=AsyncMock(return_value=[mock_move])):
        resp = await client.get(f"/games/{game_id}/moves")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["player"] == 1
    assert body[0]["column"] == 3
    assert body[0]["move_number"] == 1


@pytest.mark.anyio
async def test_game_moves_empty(client: AsyncClient) -> None:
    """GET /games/{id}/moves for a game with no moves should return empty list."""
    with patch("app.routes.games.get_game_moves", new=AsyncMock(return_value=[])):
        resp = await client.get(f"/games/{uuid.uuid4()}/moves")

    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.anyio
async def test_get_game_moves_ordered_by_move_number() -> None:
    """get_game_moves ORDER BY clause must include move_number for deterministic replay."""
    from unittest.mock import MagicMock

    from app.repository import get_game_moves

    game_id = uuid.uuid4()

    scalars_mock = MagicMock()
    scalars_mock.all.return_value = []
    result_mock = MagicMock()
    result_mock.scalars.return_value = scalars_mock

    session = AsyncMock()
    session.execute = AsyncMock(return_value=result_mock)

    await get_game_moves(session, game_id)

    stmt = session.execute.call_args[0][0]
    compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "move_number" in compiled, "get_game_moves ORDER BY must include move_number"


@pytest.mark.anyio
async def test_player_stats_not_found(client: AsyncClient) -> None:
    """GET /players/{id}/stats for unknown player should return 404."""
    with patch("app.routes.players.get_player_by_id", new=AsyncMock(return_value=None)):
        resp = await client.get(f"/players/{uuid.uuid4()}/stats")

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Query parameter validation (limit / offset bounds)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_recent_games_limit_too_large_returns_422(client: AsyncClient) -> None:
    """GET /games/recent?limit=99999 must return 422 (le=100 constraint)."""
    resp = await client.get("/games/recent?limit=99999")
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_waiting_games_limit_too_large_returns_422(client: AsyncClient) -> None:
    """GET /games/waiting?limit=99999 must return 422 (le=200 constraint)."""
    resp = await client.get("/games/waiting?limit=99999")
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_leaderboard_limit_too_large_returns_422(client: AsyncClient) -> None:
    """GET /leaderboard?limit=99999 must return 422 (le=100 constraint)."""
    resp = await client.get("/leaderboard?limit=99999")
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_pagination_limit_zero_returns_422(client: AsyncClient) -> None:
    """GET /games/recent?limit=0 must return 422 (ge=1 constraint)."""
    resp = await client.get("/games/recent?limit=0")
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Auto-recovery: GET /games/{game_id} when Redis key is missing
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_game_auto_recovery_from_db(client: AsyncClient) -> None:
    """GET /games/{game_id} should return an empty board when the Redis key is missing
    but the game exists in PostgreSQL.

    Regression: after matchmaking created a game, Redis keys could expire or
    be lost.  Without auto-recovery this returned 404, leaving the player
    stuck on a blank screen.
    """
    game_id = uuid.uuid4()
    game_id_str = str(game_id)
    db_game = SimpleNamespace(
        id=game_id,
        player1_id=uuid.uuid4(),
        player2_id=uuid.uuid4(),
        status=SimpleNamespace(value="playing"),
        winner_id=None,
    )

    # Deliberately do NOT seed Redis — the key is "missing"
    assert fake_redis_instance._store.get(f"game:{game_id_str}") is None

    mock_session = AsyncMock()
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("app.routes.games.async_session_factory", return_value=mock_ctx),
        patch("app.routes.games.get_game_by_id", new=AsyncMock(return_value=db_game)),
    ):
        resp = await client.get(f"/games/{game_id_str}")

    assert resp.status_code == 200, f"Auto-recovery failed: {resp.json()}"
    body = resp.json()
    assert body["game_id"] == game_id_str
    board = body["board"]
    assert len(board) == 6
    assert all(len(row) == 7 for row in board)
    assert all(cell == 0 for row in board for cell in row), "Recovered board should be empty"

    # Verify the board was re-saved to Redis for subsequent requests
    assert fake_redis_instance._store.get(f"game:{game_id_str}") is not None, (
        "Auto-recovery must persist the board back to Redis"
    )


@pytest.mark.anyio
async def test_get_game_returns_404_when_missing_from_both(client: AsyncClient) -> None:
    """GET /games/{game_id} should return 404 when neither Redis nor DB has the game."""
    game_id = uuid.uuid4()
    game_id_str = str(game_id)

    mock_session = AsyncMock()
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("app.routes.games.async_session_factory", return_value=mock_ctx),
        patch("app.routes.games.get_game_by_id", new=AsyncMock(return_value=None)),
    ):
        resp = await client.get(f"/games/{game_id_str}")

    assert resp.status_code == 404


@pytest.mark.anyio
async def test_move_after_redis_key_loss_still_works(client: AsyncClient) -> None:
    """POST /games/{id}/move should still work when the Redis key was lost mid-game.

    The REST move endpoint currently does NOT auto-recover (it returns 404),
    but this test documents the expected behaviour.  If auto-recovery is
    added later, change the assertion accordingly.
    """
    game_id = str(uuid.uuid4())
    # No _seed_redis_board — Redis key is absent
    resp = await client.post(
        f"/games/{game_id}/move",
        json={"game_id": game_id, "player": 1, "column": 3},
    )
    # REST move does NOT auto-recover — returns 404
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# DB persistence on REST move
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_move_endpoint_calls_record_move(client: AsyncClient) -> None:
    """The /games/{id}/move endpoint should attempt DB persistence."""
    game_id = str(uuid.uuid4())
    _seed_redis_board(game_id)

    mock_session = AsyncMock()
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("app.routes.games.async_session_factory", return_value=mock_ctx),
        patch("app.routes.games.record_move", new_callable=AsyncMock) as mock_record,
    ):
        resp = await client.post(
            f"/games/{game_id}/move",
            json={"game_id": game_id, "player": 1, "column": 3},
        )
        assert resp.status_code == 200
        mock_record.assert_called_once()
