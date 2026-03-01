"""Tests for WebSocket handler — DB persistence, ELO updates, and rematch."""

from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from starlette.testclient import TestClient

from app.game import COLS, ROWS
from app.main import app
from tests.conftest import fake_redis_instance

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_GAME_UUID: uuid.UUID = uuid.uuid4()
_GAME_ID: str = str(_GAME_UUID)
_PLAYER1_ID: uuid.UUID = uuid.uuid4()
_PLAYER2_ID: uuid.UUID = uuid.uuid4()


def _empty_board() -> list[list[int]]:
    """Return a fresh 6x7 board filled with zeros."""
    return [[0] * COLS for _ in range(ROWS)]


def _mock_session_factory(session: AsyncMock) -> MagicMock:
    """Return a callable that produces an async-context-manager wrapping *session*."""

    @asynccontextmanager
    async def _factory():  # noqa: ANN202
        yield session

    return MagicMock(side_effect=_factory)


def _db_game(winner_id: uuid.UUID | None = None) -> SimpleNamespace:
    """Return a fake DB game record."""
    return SimpleNamespace(
        id=_GAME_UUID,
        player1_id=_PLAYER1_ID,
        player2_id=_PLAYER2_ID,
        winner_id=winner_id,
    )


def _seed_redis_board(board: list[list[int]] | None = None) -> None:
    """Pre-load a board into the fake Redis store so ``load_game`` picks it up."""
    fake_redis_instance._store[f"game:{_GAME_ID}"] = json.dumps(board or _empty_board())


def _receive_non_status(ws) -> dict:  # noqa: ANN001
    """Read WS messages, skipping player_status, and return the first non-status one."""
    while True:
        data = json.loads(ws.receive_text())
        if data.get("type") == "player_status":
            continue
        return data


# ---------------------------------------------------------------------------
# Tests — all synchronous (starlette TestClient is blocking)
# ---------------------------------------------------------------------------


def test_ws_move_calls_record_move() -> None:
    """After a valid WS move, ``record_move`` must be called with correct args."""
    fake_redis_instance.reset()
    _seed_redis_board()
    mock_session = AsyncMock()
    mock_session.commit = AsyncMock()
    mock_record = AsyncMock()
    mock_get_game = AsyncMock(return_value=None)

    with (
        patch("app.store._redis_client", fake_redis_instance),
        patch("app.store.get_redis", return_value=fake_redis_instance),
        patch("app.websocket.async_session_factory", _mock_session_factory(mock_session)),
        patch("app.websocket.record_move", mock_record),
        patch("app.websocket.get_game_by_id", mock_get_game),
        patch("app.websocket.get_player_by_id", AsyncMock(return_value=None)),
    ):
        client = TestClient(app)
        with client.websocket_connect(f"/ws/{_GAME_ID}") as ws:
            ws.send_text(json.dumps({"player": 1, "column": 3}))
            data = _receive_non_status(ws)

    assert data["player"] == 1
    assert data["column"] == 3
    assert data["row"] == 5  # bottom row
    mock_record.assert_awaited_once()
    call_args = mock_record.call_args
    assert call_args[0][1] == _GAME_UUID  # game_id
    assert call_args[0][2] == 1  # player
    assert call_args[0][3] == 3  # column
    assert call_args[0][4] == 5  # row


def test_ws_game_over_calls_finish_and_elo() -> None:
    """When a player wins via WS, ``finish_game`` and ``update_elo`` must be called."""
    fake_redis_instance.reset()
    # Board where player 1 has 3 in a row at bottom, column 3 wins
    board = _empty_board()
    board[5][0] = 1
    board[5][1] = 1
    board[5][2] = 1
    # Player 2 pieces to keep turn order valid (3 moves each)
    board[4][0] = 2
    board[4][1] = 2
    board[4][2] = 2
    _seed_redis_board(board)

    mock_session = AsyncMock()
    mock_session.commit = AsyncMock()
    mock_record = AsyncMock()
    mock_finish = AsyncMock()
    mock_elo = AsyncMock(return_value=(1016, 984))
    mock_get_game = AsyncMock(return_value=_db_game())

    with (
        patch("app.store._redis_client", fake_redis_instance),
        patch("app.store.get_redis", return_value=fake_redis_instance),
        patch("app.websocket.async_session_factory", _mock_session_factory(mock_session)),
        patch("app.websocket.record_move", mock_record),
        patch("app.websocket.finish_game", mock_finish),
        patch("app.websocket.update_elo", mock_elo),
        patch("app.websocket.get_game_by_id", mock_get_game),
        patch("app.websocket.get_player_by_id", AsyncMock(return_value=None)),
    ):
        client = TestClient(app)
        with client.websocket_connect(f"/ws/{_GAME_ID}") as ws:
            ws.send_text(json.dumps({"player": 1, "column": 3}))
            data = _receive_non_status(ws)

    assert data["winner"] == 1
    mock_finish.assert_awaited_once()
    finish_args = mock_finish.call_args[0]
    assert finish_args[1] == _GAME_UUID
    assert finish_args[2] == _PLAYER1_ID  # winner is player 1

    mock_elo.assert_awaited_once()
    elo_args = mock_elo.call_args[0]
    assert elo_args[1] == _PLAYER1_ID  # winner
    assert elo_args[2] == _PLAYER2_ID  # loser


def test_ws_draw_calls_finish_and_elo_draw() -> None:
    """When the board fills up via WS, ``finish_game`` and ``update_elo_draw`` must be called."""
    fake_redis_instance.reset()
    # Board designed to avoid any 4-in-a-row (blocks of 2 avoid diagonals).
    # P1=21 pieces, P2=20 pieces, one empty cell at (0,6). Next player is P2.
    board = [
        [1, 1, 2, 2, 1, 1, 0],
        [2, 2, 1, 1, 2, 2, 1],
        [1, 1, 2, 2, 1, 1, 2],
        [2, 2, 1, 1, 2, 2, 1],
        [1, 1, 2, 2, 1, 1, 2],
        [2, 2, 1, 1, 2, 2, 1],
    ]
    _seed_redis_board(board)

    mock_session = AsyncMock()
    mock_session.commit = AsyncMock()
    mock_record = AsyncMock()
    mock_finish = AsyncMock()
    mock_elo_draw = AsyncMock(return_value=(1000, 1000))
    mock_get_game = AsyncMock(return_value=_db_game())

    with (
        patch("app.store._redis_client", fake_redis_instance),
        patch("app.store.get_redis", return_value=fake_redis_instance),
        patch("app.websocket.async_session_factory", _mock_session_factory(mock_session)),
        patch("app.websocket.record_move", mock_record),
        patch("app.websocket.finish_game", mock_finish),
        patch("app.websocket.update_elo_draw", mock_elo_draw),
        patch("app.websocket.get_game_by_id", mock_get_game),
        patch("app.websocket.get_player_by_id", AsyncMock(return_value=None)),
    ):
        client = TestClient(app)
        with client.websocket_connect(f"/ws/{_GAME_ID}") as ws:
            ws.send_text(json.dumps({"player": 2, "column": 6}))
            data = _receive_non_status(ws)

    assert data["draw"] is True
    mock_finish.assert_awaited_once()
    mock_elo_draw.assert_awaited_once()
    elo_args = mock_elo_draw.call_args[0]
    assert elo_args[1] == _PLAYER1_ID
    assert elo_args[2] == _PLAYER2_ID


def test_ws_db_failure_still_broadcasts() -> None:
    """If the DB persistence fails, the move should still be broadcast to players."""
    fake_redis_instance.reset()
    _seed_redis_board()
    mock_session = AsyncMock()
    mock_session.commit = AsyncMock(side_effect=RuntimeError("DB down"))
    mock_record = AsyncMock(side_effect=RuntimeError("DB down"))

    with (
        patch("app.store._redis_client", fake_redis_instance),
        patch("app.store.get_redis", return_value=fake_redis_instance),
        patch("app.websocket.async_session_factory", _mock_session_factory(mock_session)),
        patch("app.websocket.record_move", mock_record),
        patch("app.websocket.get_player_by_id", AsyncMock(return_value=None)),
    ):
        client = TestClient(app)
        with client.websocket_connect(f"/ws/{_GAME_ID}") as ws:
            ws.send_text(json.dumps({"player": 1, "column": 0}))
            data = _receive_non_status(ws)

    # Move still broadcasts despite DB failure
    assert data["player"] == 1
    assert data["column"] == 0
    assert data["row"] == 5


def test_ws_rematch_resets_game() -> None:
    """When both players vote rematch, the Redis game state should be cleared."""
    fake_redis_instance.reset()
    _seed_redis_board()
    mock_session = AsyncMock()
    mock_session.add = MagicMock()  # session.add is sync; stops AsyncMock producing unawaited coroutines

    with (
        patch("app.store._redis_client", fake_redis_instance),
        patch("app.store.get_redis", return_value=fake_redis_instance),
        patch("app.websocket.async_session_factory", _mock_session_factory(mock_session)),
        patch("app.websocket.get_player_by_id", AsyncMock(return_value=None)),
    ):
        client = TestClient(app)
        with (
            client.websocket_connect(f"/ws/{_GAME_ID}") as ws1,
            client.websocket_connect(f"/ws/{_GAME_ID}") as ws2,
        ):
            ws1.send_text(json.dumps({"action": "rematch", "player": 1}))
            # ws2 receives: player_status (ws1 identified) + rematch_waiting
            waiting = _receive_non_status(ws2)
            assert waiting.get("rematch_waiting") is True

            ws2.send_text(json.dumps({"action": "rematch", "player": 2}))
            # Both get: player_status (ws2 identified) + rematch broadcast
            rematch1 = _receive_non_status(ws1)
            rematch2 = _receive_non_status(ws2)
            assert rematch1.get("rematch") is True
            assert rematch2.get("rematch") is True

    # After rematch, Redis should hold a fresh empty board (not be absent)
    stored = fake_redis_instance._store.get(f"game:{_GAME_ID}")
    assert stored is not None
    assert json.loads(stored) == _empty_board()


def test_ws_identify_broadcasts_username() -> None:
    """When a player sends identify with a username, it should appear in player_status."""
    fake_redis_instance.reset()
    _seed_redis_board()
    mock_session = AsyncMock()
    with (
        patch("app.store._redis_client", fake_redis_instance),
        patch("app.store.get_redis", return_value=fake_redis_instance),
        patch("app.websocket.async_session_factory", _mock_session_factory(mock_session)),
        patch("app.websocket.get_player_by_id", AsyncMock(return_value=None)),
    ):
        client = TestClient(app)
        with client.websocket_connect(f"/ws/{_GAME_ID}") as ws:
            ws.send_text(json.dumps({"action": "identify", "player": 1, "username": "Alice"}))
            # Read the player_status message
            data = json.loads(ws.receive_text())

    assert data["type"] == "player_status"
    assert data["connected_players"] == [1]
    assert data["usernames"] == {"1": "Alice"}


# ---------------------------------------------------------------------------
# WS error paths
# ---------------------------------------------------------------------------


def test_ws_column_full_sends_error() -> None:
    """Dropping into a full column via WS should send an error (not crash)."""
    fake_redis_instance.reset()
    # Build a board with column 0 completely full
    board = _empty_board()
    for row in range(ROWS):
        board[row][0] = 1 if row % 2 == 0 else 2
    _seed_redis_board(board)

    mock_session = AsyncMock()
    mock_session.commit = AsyncMock()

    with (
        patch("app.store._redis_client", fake_redis_instance),
        patch("app.store.get_redis", return_value=fake_redis_instance),
        patch("app.websocket.async_session_factory", _mock_session_factory(mock_session)),
        patch("app.websocket.get_player_by_id", AsyncMock(return_value=None)),
    ):
        client = TestClient(app)
        with client.websocket_connect(f"/ws/{_GAME_ID}") as ws:
            # Try to drop into full column 0 — next_player is determined by piece count
            # Piece count = 6, so next_player = 1
            ws.send_text(json.dumps({"player": 1, "column": 0}))
            data = _receive_non_status(ws)

    assert "error" in data
    assert "full" in data["error"].lower()


def test_ws_game_over_sends_error() -> None:
    """Attempting a move after a win via WS should send an error."""
    fake_redis_instance.reset()
    # Board where player 1 already won (horizontal at bottom)
    board = _empty_board()
    board[5][0] = 1
    board[5][1] = 1
    board[5][2] = 1
    board[5][3] = 1
    board[4][0] = 2
    board[4][1] = 2
    board[4][2] = 2
    _seed_redis_board(board)

    mock_session = AsyncMock()
    mock_session.commit = AsyncMock()

    with (
        patch("app.store._redis_client", fake_redis_instance),
        patch("app.store.get_redis", return_value=fake_redis_instance),
        patch("app.websocket.async_session_factory", _mock_session_factory(mock_session)),
        patch("app.websocket.get_player_by_id", AsyncMock(return_value=None)),
    ):
        client = TestClient(app)
        with client.websocket_connect(f"/ws/{_GAME_ID}") as ws:
            ws.send_text(json.dumps({"player": 2, "column": 4}))
            data = _receive_non_status(ws)

    assert "error" in data
    assert "over" in data["error"].lower()


def test_ws_invalid_turn_sends_error() -> None:
    """Playing out of turn via WS should send an error."""
    fake_redis_instance.reset()
    _seed_redis_board()

    mock_session = AsyncMock()
    mock_session.commit = AsyncMock()

    with (
        patch("app.store._redis_client", fake_redis_instance),
        patch("app.store.get_redis", return_value=fake_redis_instance),
        patch("app.websocket.async_session_factory", _mock_session_factory(mock_session)),
        patch("app.websocket.get_player_by_id", AsyncMock(return_value=None)),
    ):
        client = TestClient(app)
        with client.websocket_connect(f"/ws/{_GAME_ID}") as ws:
            # Player 2 tries to go first on empty board
            ws.send_text(json.dumps({"player": 2, "column": 0}))
            data = _receive_non_status(ws)

    assert "error" in data
    assert "turn" in data["error"].lower()


def test_ws_lock_contention_sends_retry_message() -> None:
    """When the game lock is held, WS should send a retry error."""
    fake_redis_instance.reset()
    _seed_redis_board()
    # Pre-acquire the lock
    fake_redis_instance._locks.add(f"lock:{_GAME_ID}")

    mock_session = AsyncMock()
    mock_session.commit = AsyncMock()

    with (
        patch("app.store._redis_client", fake_redis_instance),
        patch("app.store.get_redis", return_value=fake_redis_instance),
        patch("app.websocket.async_session_factory", _mock_session_factory(mock_session)),
        patch("app.websocket.get_player_by_id", AsyncMock(return_value=None)),
    ):
        client = TestClient(app)
        with client.websocket_connect(f"/ws/{_GAME_ID}") as ws:
            ws.send_text(json.dumps({"player": 1, "column": 0}))
            data = _receive_non_status(ws)

    assert "error" in data
    assert "collision" in data["error"].lower() or "retry" in data["error"].lower()


def test_ws_identify_then_move() -> None:
    """After identifying, subsequent moves should work normally."""
    fake_redis_instance.reset()
    _seed_redis_board()

    mock_session = AsyncMock()
    mock_session.commit = AsyncMock()
    mock_record = AsyncMock()
    mock_get_game = AsyncMock(return_value=None)

    with (
        patch("app.store._redis_client", fake_redis_instance),
        patch("app.store.get_redis", return_value=fake_redis_instance),
        patch("app.websocket.async_session_factory", _mock_session_factory(mock_session)),
        patch("app.websocket.record_move", mock_record),
        patch("app.websocket.get_game_by_id", mock_get_game),
        patch("app.websocket.get_player_by_id", AsyncMock(return_value=None)),
    ):
        client = TestClient(app)
        with client.websocket_connect(f"/ws/{_GAME_ID}") as ws:
            # First: identify
            ws.send_text(json.dumps({"action": "identify", "player": 1, "username": "TestUser"}))
            _id_status = json.loads(ws.receive_text())
            assert _id_status["type"] == "player_status"

            # Then: make a move
            ws.send_text(json.dumps({"player": 1, "column": 3}))
            data = _receive_non_status(ws)

    assert data["player"] == 1
    assert data["column"] == 3
    assert data["row"] == 5


# ---------------------------------------------------------------------------
# Player identity validation
# ---------------------------------------------------------------------------


def test_ws_move_from_wrong_player_rejected() -> None:
    """If identified as player 1, sending a move as player 2 should error."""
    fake_redis_instance.reset()
    _seed_redis_board()
    mock_session = AsyncMock()

    with (
        patch("app.store._redis_client", fake_redis_instance),
        patch("app.store.get_redis", return_value=fake_redis_instance),
        patch("app.websocket.async_session_factory", _mock_session_factory(mock_session)),
        patch("app.websocket.get_player_by_id", AsyncMock(return_value=None)),
    ):
        client = TestClient(app)
        with client.websocket_connect(f"/ws/{_GAME_ID}") as ws:
            # Identify as player 1
            ws.send_text(json.dumps({"action": "identify", "player": 1, "username": "Alice"}))
            _id_status = json.loads(ws.receive_text())
            assert _id_status["type"] == "player_status"

            # Try to move as player 2 (should be rejected)
            ws.send_text(json.dumps({"player": 2, "column": 3}))
            data = _receive_non_status(ws)

    assert "error" in data


def test_ws_rematch_from_wrong_player_rejected() -> None:
    """If identified as player 1, voting rematch as player 2 should error."""
    fake_redis_instance.reset()
    _seed_redis_board()
    mock_session = AsyncMock()

    with (
        patch("app.store._redis_client", fake_redis_instance),
        patch("app.store.get_redis", return_value=fake_redis_instance),
        patch("app.websocket.async_session_factory", _mock_session_factory(mock_session)),
        patch("app.websocket.get_player_by_id", AsyncMock(return_value=None)),
    ):
        client = TestClient(app)
        with client.websocket_connect(f"/ws/{_GAME_ID}") as ws:
            # Identify as player 1
            ws.send_text(json.dumps({"action": "identify", "player": 1, "username": "Alice"}))
            _id_status = json.loads(ws.receive_text())
            assert _id_status["type"] == "player_status"

            # Try rematch as player 2 (should be rejected)
            ws.send_text(json.dumps({"action": "rematch", "player": 2}))
            data = _receive_non_status(ws)

    assert "error" in data


# ---------------------------------------------------------------------------
# Payload validation
# ---------------------------------------------------------------------------


def test_ws_missing_player_key_sends_error() -> None:
    """Missing 'player' key in move payload should send an error."""
    fake_redis_instance.reset()
    _seed_redis_board()
    mock_session = AsyncMock()

    with (
        patch("app.store._redis_client", fake_redis_instance),
        patch("app.store.get_redis", return_value=fake_redis_instance),
        patch("app.websocket.async_session_factory", _mock_session_factory(mock_session)),
        patch("app.websocket.get_player_by_id", AsyncMock(return_value=None)),
    ):
        client = TestClient(app)
        with client.websocket_connect(f"/ws/{_GAME_ID}") as ws:
            ws.send_text(json.dumps({"column": 3}))
            data = _receive_non_status(ws)

    assert "error" in data


def test_ws_missing_column_key_sends_error() -> None:
    """Missing 'column' key in move payload should send an error."""
    fake_redis_instance.reset()
    _seed_redis_board()
    mock_session = AsyncMock()

    with (
        patch("app.store._redis_client", fake_redis_instance),
        patch("app.store.get_redis", return_value=fake_redis_instance),
        patch("app.websocket.async_session_factory", _mock_session_factory(mock_session)),
        patch("app.websocket.get_player_by_id", AsyncMock(return_value=None)),
    ):
        client = TestClient(app)
        with client.websocket_connect(f"/ws/{_GAME_ID}") as ws:
            ws.send_text(json.dumps({"player": 1}))
            data = _receive_non_status(ws)

    assert "error" in data


# ---------------------------------------------------------------------------
# Auto-recovery: WS move when Redis key is missing
# ---------------------------------------------------------------------------


def test_ws_move_auto_recovers_when_redis_key_missing() -> None:
    """A WS move should succeed even when the Redis board key is absent.

    Regression: if matchmaking created the DB game but never called
    ``save_game`` (the bug that shipped to production), or if the Redis
    key expired mid-game, ``_handle_move`` should auto-create a fresh
    board instead of returning an error or hanging.
    """
    fake_redis_instance.reset()
    # Deliberately do NOT call _seed_redis_board() — key is missing
    assert fake_redis_instance._store.get(f"game:{_GAME_ID}") is None

    mock_session = AsyncMock()
    mock_session.commit = AsyncMock()
    mock_record = AsyncMock()
    mock_get_game = AsyncMock(return_value=None)

    with (
        patch("app.store._redis_client", fake_redis_instance),
        patch("app.store.get_redis", return_value=fake_redis_instance),
        patch("app.websocket.async_session_factory", _mock_session_factory(mock_session)),
        patch("app.websocket.record_move", mock_record),
        patch("app.websocket.get_game_by_id", mock_get_game),
        patch("app.websocket.get_player_by_id", AsyncMock(return_value=None)),
    ):
        client = TestClient(app)
        with client.websocket_connect(f"/ws/{_GAME_ID}") as ws:
            ws.send_text(json.dumps({"player": 1, "column": 3}))
            data = _receive_non_status(ws)

    # Move should succeed — auto-recovery creates a fresh board
    assert "error" not in data, f"Move should auto-recover but got error: {data}"
    assert data["player"] == 1
    assert data["column"] == 3
    assert data["row"] == 5  # bottom row of fresh board
    mock_record.assert_awaited_once()

    # Verify the board was persisted to Redis after auto-recovery
    assert (
        fake_redis_instance._store.get(f"game:{_GAME_ID}") is not None
    ), "Auto-recovery must save the board to Redis so subsequent moves work"


def test_ws_two_moves_after_auto_recovery() -> None:
    """After auto-recovery, a second move from player 2 should also work.

    This end-to-end test ensures the auto-recovered board is truly
    playable, not just a one-shot fix.
    """
    fake_redis_instance.reset()
    # No _seed_redis_board — trigger auto-recovery on first move

    mock_session = AsyncMock()
    mock_session.commit = AsyncMock()
    mock_record = AsyncMock()
    mock_get_game = AsyncMock(return_value=None)

    with (
        patch("app.store._redis_client", fake_redis_instance),
        patch("app.store.get_redis", return_value=fake_redis_instance),
        patch("app.websocket.async_session_factory", _mock_session_factory(mock_session)),
        patch("app.websocket.record_move", mock_record),
        patch("app.websocket.get_game_by_id", mock_get_game),
        patch("app.websocket.get_player_by_id", AsyncMock(return_value=None)),
    ):
        client = TestClient(app)
        with (
            client.websocket_connect(f"/ws/{_GAME_ID}") as ws1,
            client.websocket_connect(f"/ws/{_GAME_ID}") as ws2,
        ):
            # Player 1 moves — triggers auto-recovery
            ws1.send_text(json.dumps({"player": 1, "column": 0}))
            p1_data = _receive_non_status(ws1)
            # Also read the broadcast on ws2
            _receive_non_status(ws2)

            assert p1_data["player"] == 1
            assert p1_data["row"] == 5

            # Player 2 moves — should work on the recovered board
            ws2.send_text(json.dumps({"player": 2, "column": 6}))
            p2_data = _receive_non_status(ws2)

    assert "error" not in p2_data, f"Second move failed: {p2_data}"
    assert p2_data["player"] == 2
    assert p2_data["column"] == 6
    assert p2_data["row"] == 5


# ---------------------------------------------------------------------------
# Rematch edge cases
# ---------------------------------------------------------------------------


def test_ws_rematch_single_vote_waits() -> None:
    """A single rematch vote should notify the other player to wait."""
    fake_redis_instance.reset()
    _seed_redis_board()
    mock_session = AsyncMock()

    with (
        patch("app.store._redis_client", fake_redis_instance),
        patch("app.store.get_redis", return_value=fake_redis_instance),
        patch("app.websocket.async_session_factory", _mock_session_factory(mock_session)),
        patch("app.websocket.get_player_by_id", AsyncMock(return_value=None)),
    ):
        client = TestClient(app)
        with (
            client.websocket_connect(f"/ws/{_GAME_ID}") as ws1,
            client.websocket_connect(f"/ws/{_GAME_ID}") as ws2,
        ):
            # Player 1 votes for rematch
            ws1.send_text(json.dumps({"action": "rematch", "player": 1}))
            # Player 2 should get the rematch_waiting notification
            waiting = _receive_non_status(ws2)
            assert waiting.get("rematch_waiting") is True
