"""Integration tests — full user journeys across REST, WebSocket, Redis, and game logic.

Each test exercises multiple layers together (HTTP → game → Redis → audit, or
WS → game → Redis → DB mock → broadcast), verifying that the system produces
correct end-to-end behaviour rather than testing components in isolation.
"""

from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from starlette.testclient import TestClient

from app.game import COLS, ROWS
from app.main import app
from tests.conftest import fake_redis_instance

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _empty_board() -> list[list[int]]:
    """Return a fresh 6×7 board filled with zeros."""
    return [[0] * COLS for _ in range(ROWS)]


def _seed_redis_board(game_id: str, board: list[list[int]] | None = None) -> None:
    """Pre-load a board into FakeRedis for the given game."""
    fake_redis_instance._store[f"game:{game_id}"] = json.dumps(board or _empty_board())


def _mock_session_factory(session: AsyncMock) -> MagicMock:
    """Return a callable that yields *session* inside an async context manager."""

    @asynccontextmanager
    async def _factory():  # noqa: ANN202
        yield session

    return MagicMock(side_effect=_factory)


def _mock_player(player_id: uuid.UUID, username: str, elo_rating: int = 1000) -> SimpleNamespace:
    """Lightweight mock player record."""
    return SimpleNamespace(id=player_id, username=username, elo_rating=elo_rating)


def _mock_game(
    game_id: uuid.UUID,
    player1_id: uuid.UUID,
    player2_id: uuid.UUID | None = None,
    status_value: str = "waiting",
    winner_id: uuid.UUID | None = None,
) -> SimpleNamespace:
    """Lightweight mock game record."""
    return SimpleNamespace(
        id=game_id,
        player1_id=player1_id,
        player2_id=player2_id,
        status=SimpleNamespace(value=status_value),
        winner_id=winner_id,
    )


def _receive_non_status(ws: Any) -> dict[str, Any]:
    """Read WS messages, skip ``player_status``, return the first real one."""
    while True:
        data = json.loads(ws.receive_text())
        if data.get("type") == "player_status":
            continue
        return data


# ---------------------------------------------------------------------------
# 1. Complete game via REST — play to a win, verify board + audit
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_full_game_to_win_via_rest(client: AsyncClient) -> None:
    """Play a complete game through REST endpoints until Player 1 wins horizontally.

    Journey: create empty board → alternate moves → detect victory.
    Validates: board gravity, turn alternation, winner detection, winning cells,
    and that the board state retrieval reflects all moves.
    """
    game_id = "integ-rest-win"
    _seed_redis_board(game_id)

    # Player 1 builds bottom-row columns 0-2, Player 2 stacks on column 6
    for col in range(3):
        p1_resp = await client.post(
            f"/games/{game_id}/move",
            json={"game_id": game_id, "player": 1, "column": col},
        )
        assert p1_resp.status_code == 200
        body = p1_resp.json()
        assert body["row"] == 5, "Piece should land on bottom row"
        assert body["winner"] is None, "Game should not be over yet"

        p2_resp = await client.post(
            f"/games/{game_id}/move",
            json={"game_id": game_id, "player": 2, "column": 6},
        )
        assert p2_resp.status_code == 200

    # Winning move: Player 1 → column 3
    win_resp = await client.post(
        f"/games/{game_id}/move",
        json={"game_id": game_id, "player": 1, "column": 3},
    )
    assert win_resp.status_code == 200
    win_body = win_resp.json()
    assert win_body["winner"] == 1
    assert len(win_body["winning_cells"]) >= 4
    # All winning cells should be on the bottom row
    for cell in win_body["winning_cells"]:
        assert cell[0] == 5

    # GET board should reflect the final state including win
    get_resp = await client.get(f"/games/{game_id}")
    assert get_resp.status_code == 200
    get_body = get_resp.json()
    assert get_body["winner"] == 1
    assert get_body["board"][5][0:4] == [1, 1, 1, 1]

    # Attempting another move should fail (game over)
    post_resp = await client.post(
        f"/games/{game_id}/move",
        json={"game_id": game_id, "player": 2, "column": 0},
    )
    assert post_resp.status_code == 409


# ---------------------------------------------------------------------------
# 2. Complete game via REST — play to a draw
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_full_game_to_draw_via_rest(client: AsyncClient) -> None:
    """Fill the board without any four-in-a-row to produce a draw.

    Uses a pre-seeded board with one empty cell so a single move
    triggers the draw condition.
    """
    game_id = "integ-rest-draw"
    # 41 pieces placed, 1 empty at (0,6), no 4-in-a-row.
    # Piece count = 41 (odd) → next_player = 2
    board = [
        [1, 1, 2, 2, 1, 1, 0],
        [2, 2, 1, 1, 2, 2, 1],
        [1, 1, 2, 2, 1, 1, 2],
        [2, 2, 1, 1, 2, 2, 1],
        [1, 1, 2, 2, 1, 1, 2],
        [2, 2, 1, 1, 2, 2, 1],
    ]
    _seed_redis_board(game_id, board)

    resp = await client.post(
        f"/games/{game_id}/move",
        json={"game_id": game_id, "player": 2, "column": 6},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["draw"] is True
    assert body["winner"] is None

    # Board should show draw on GET as well
    get_resp = await client.get(f"/games/{game_id}")
    assert get_resp.json()["draw"] is True


# ---------------------------------------------------------------------------
# 3. Two concurrent games — board isolation
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_concurrent_games_board_isolation(client: AsyncClient) -> None:
    """Moves in one game must never leak into another game's board.

    Play moves in two different games and verify each board
    is independently correct.
    """
    game_a = "integ-iso-a"
    game_b = "integ-iso-b"
    _seed_redis_board(game_a)
    _seed_redis_board(game_b)

    # Player 1 drops in column 0 for game A
    await client.post(f"/games/{game_a}/move", json={"game_id": game_a, "player": 1, "column": 0})
    # Player 1 drops in column 6 for game B
    await client.post(f"/games/{game_b}/move", json={"game_id": game_b, "player": 1, "column": 6})

    board_a = (await client.get(f"/games/{game_a}")).json()["board"]
    board_b = (await client.get(f"/games/{game_b}")).json()["board"]

    # Game A: piece at (5,0) only
    assert board_a[5][0] == 1
    assert board_a[5][6] == 0

    # Game B: piece at (5,6) only
    assert board_b[5][6] == 1
    assert board_b[5][0] == 0


# ---------------------------------------------------------------------------
# 4. Vertical win detection via REST
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_vertical_win_via_rest(client: AsyncClient) -> None:
    """Player 1 stacks four pieces in column 0 for a vertical win.

    Player 2 plays in column 1 to keep turn order valid.
    """
    game_id = "integ-vert-win"
    _seed_redis_board(game_id)

    for move_index in range(3):
        await client.post(f"/games/{game_id}/move", json={"game_id": game_id, "player": 1, "column": 0})
        await client.post(f"/games/{game_id}/move", json={"game_id": game_id, "player": 2, "column": 1})

    # 4th piece → vertical win
    resp = await client.post(f"/games/{game_id}/move", json={"game_id": game_id, "player": 1, "column": 0})
    body = resp.json()
    assert body["winner"] == 1
    # Winning cells should all be in column 0
    for cell in body["winning_cells"]:
        assert cell[1] == 0


# ---------------------------------------------------------------------------
# 5. Diagonal win detection via REST
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_diagonal_win_via_rest(client: AsyncClient) -> None:
    """Player 1 wins via a rising diagonal (bottom-left to top-right).

    Pre-seeded board (10 pieces, next_player = 1):

        row 0: . . . . . . .
        row 1: . . . . . . .
        row 2: . . . . . . .     ← P1 will place here at col 3
        row 3: . . 1  2 . . .
        row 4: . 1 2  2 . . .
        row 5: 1 2 1  1 2 . .

    Winning diagonal: (5,0)→(4,1)→(3,2)→(2,3)
    """
    game_id = "integ-diag-win"

    board = _empty_board()
    # P1 pieces: (5,0), (4,1), (3,2), (5,2), (5,3) — 5 total
    board[5][0] = 1
    board[4][1] = 1
    board[3][2] = 1
    board[5][2] = 1
    board[5][3] = 1
    # P2 pieces: (5,1), (4,2), (3,3), (4,3), (5,4) — 5 total
    board[5][1] = 2
    board[4][2] = 2
    board[3][3] = 2
    board[4][3] = 2
    board[5][4] = 2
    _seed_redis_board(game_id, board)

    # Winning move: P1 → column 3, lands at (2,3), completing the diagonal
    resp = await client.post(f"/games/{game_id}/move", json={"game_id": game_id, "player": 1, "column": 3})
    body = resp.json()
    assert body["winner"] == 1
    assert len(body["winning_cells"]) >= 4


# ---------------------------------------------------------------------------
# 6. Full WS game lifecycle — two players, win, DB persist, ELO
# ---------------------------------------------------------------------------


def test_full_ws_game_lifecycle_win_and_elo() -> None:
    """Two WebSocket players play a complete game to a win.

    Verifies: every move broadcast, record_move called per move,
    finish_game + update_elo called exactly once on game over,
    and session.commit called for each DB operation.
    """
    game_uuid = uuid.uuid4()
    game_id = str(game_uuid)
    p1_id = uuid.uuid4()
    p2_id = uuid.uuid4()

    _seed_redis_board(game_id)

    mock_session = AsyncMock()
    mock_session.commit = AsyncMock()
    mock_record = AsyncMock()
    mock_finish = AsyncMock()
    mock_elo = AsyncMock(return_value=(1016, 984))
    db_game = SimpleNamespace(
        id=game_uuid,
        player1_id=p1_id,
        player2_id=p2_id,
        winner_id=None,
    )
    mock_get_game = AsyncMock(return_value=db_game)

    with (
        patch("app.store._redis_client", fake_redis_instance),
        patch("app.store.get_redis", return_value=fake_redis_instance),
        patch("app.database.async_session_factory", _mock_session_factory(mock_session)),
        patch("app.main.cleanup_stale_games", AsyncMock()),
        patch("app.websocket.async_session_factory", _mock_session_factory(mock_session)),
        patch("app.websocket.record_move", mock_record),
        patch("app.websocket.finish_game", mock_finish),
        patch("app.websocket.update_elo", mock_elo),
        patch("app.websocket.get_game_by_id", mock_get_game),
        patch("app.websocket.get_player_by_id", AsyncMock(return_value=None)),
        TestClient(app) as starlette_client,
        starlette_client.websocket_connect(f"/ws/{game_id}") as ws1,
        starlette_client.websocket_connect(f"/ws/{game_id}") as ws2,
    ):
        # Play 7 moves: P1 horizontal win at bottom (cols 0-3)
        # P1 col 0, P2 col 6, P1 col 1, P2 col 6, P1 col 2, P2 col 6, P1 col 3
        # P1 moves sent from ws1, P2 moves from ws2 (auto-identify binds each ws)
        moves = [
            (1, 0, ws1),
            (2, 6, ws2),
            (1, 1, ws1),
            (2, 6, ws2),
            (1, 2, ws1),
            (2, 6, ws2),
            (1, 3, ws1),  # winning move
        ]

        collected_broadcasts: list[dict[str, Any]] = []
        for player, column, sender in moves:
            sender.send_text(json.dumps({"player": player, "column": column}))
            # Both clients receive the broadcast
            data1 = _receive_non_status(ws1)
            data2 = _receive_non_status(ws2)
            assert data1 == data2, "Both players must receive identical broadcast"
            collected_broadcasts.append(data1)

    # Verify all 7 moves were broadcast
    assert len(collected_broadcasts) == 7

    # Verify progressive game state
    for idx, broadcast in enumerate(collected_broadcasts[:-1]):
        assert broadcast["winner"] is None
        assert broadcast["draw"] is False
    # Last broadcast has the winner
    assert collected_broadcasts[-1]["winner"] == 1
    assert len(collected_broadcasts[-1]["winning_cells"]) >= 4

    # DB persistence: record_move called 7 times
    assert mock_record.await_count == 7

    # Game over: finish_game + update_elo called exactly once
    mock_finish.assert_awaited_once()
    mock_elo.assert_awaited_once()

    # ELO args: winner=p1, loser=p2
    elo_args = mock_elo.call_args[0]
    assert elo_args[1] == p1_id
    assert elo_args[2] == p2_id


# ---------------------------------------------------------------------------
# 7. WS game → rematch → second game (full cycle)
# ---------------------------------------------------------------------------


def test_ws_game_rematch_and_replay() -> None:
    """Play a game to completion, rematch, and verify a fresh board is available.

    Journey: play to win → both vote rematch → Redis key deleted → new moves work.
    """
    game_uuid = uuid.uuid4()
    game_id = str(game_uuid)
    p1_id = uuid.uuid4()
    p2_id = uuid.uuid4()

    _seed_redis_board(game_id)

    rematch_uuid = uuid.uuid4()
    mock_session = AsyncMock()
    mock_session.commit = AsyncMock()
    mock_record = AsyncMock()
    mock_finish = AsyncMock()
    mock_elo = AsyncMock(return_value=(1016, 984))
    db_game = SimpleNamespace(id=game_uuid, player1_id=p1_id, player2_id=p2_id, winner_id=None)
    mock_get_game = AsyncMock(return_value=db_game)
    mock_create_game_fn = AsyncMock(return_value=SimpleNamespace(id=rematch_uuid, player1_id=p1_id, status="waiting"))
    mock_join_game_fn = AsyncMock(
        return_value=SimpleNamespace(id=rematch_uuid, player1_id=p1_id, player2_id=p2_id, status="playing")
    )

    with (
        patch("app.store._redis_client", fake_redis_instance),
        patch("app.store.get_redis", return_value=fake_redis_instance),
        patch("app.database.async_session_factory", _mock_session_factory(mock_session)),
        patch("app.main.cleanup_stale_games", AsyncMock()),
        patch("app.websocket.async_session_factory", _mock_session_factory(mock_session)),
        patch("app.websocket.record_move", mock_record),
        patch("app.websocket.finish_game", mock_finish),
        patch("app.websocket.update_elo", mock_elo),
        patch("app.websocket.get_game_by_id", mock_get_game),
        patch("app.websocket.get_player_by_id", AsyncMock(return_value=None)),
        patch("app.websocket.create_game", mock_create_game_fn),
        patch("app.websocket.join_game", mock_join_game_fn),
        TestClient(app) as starlette_client,
        starlette_client.websocket_connect(f"/ws/{game_id}") as ws1,
        starlette_client.websocket_connect(f"/ws/{game_id}") as ws2,
    ):
        # --- Phase 1: Play to a quick win (P1 horizontal) ---
        # P1 moves from ws1, P2 moves from ws2 (auto-identify binds each ws)
        win_moves = [(1, 0, ws1), (2, 6, ws2), (1, 1, ws1), (2, 6, ws2), (1, 2, ws1), (2, 6, ws2), (1, 3, ws1)]
        for player, column, sender in win_moves:
            sender.send_text(json.dumps({"player": player, "column": column}))
            _receive_non_status(ws1)
            _receive_non_status(ws2)

        # Game is now over — verify Redis has the game state
        assert f"game:{game_id}" in fake_redis_instance._store

        # --- Phase 2: Rematch ---
        ws1.send_text(json.dumps({"action": "rematch", "player": 1}))
        waiting_msg = _receive_non_status(ws2)
        assert waiting_msg.get("rematch_waiting") is True

        ws2.send_text(json.dumps({"action": "rematch", "player": 2}))
        rematch1 = _receive_non_status(ws1)
        rematch2 = _receive_non_status(ws2)
        assert rematch1.get("rematch") is True
        assert rematch2.get("rematch") is True

        # _handle_rematch deletes then immediately re-seeds the board via save_game
        assert f"game:{game_id}" in fake_redis_instance._store, "Board re-seeded after rematch"
        rematch_board = json.loads(fake_redis_instance._store[f"game:{game_id}"])
        assert all(cell == 0 for row in rematch_board for cell in row), "Fresh board should be empty"

        # --- Phase 3: Play another move on the fresh board ---
        ws1.send_text(json.dumps({"player": 1, "column": 3}))
        new_move1 = _receive_non_status(ws1)
        new_move2 = _receive_non_status(ws2)
        assert new_move1["row"] == 5  # bottom of a fresh board
        assert new_move1["column"] == 3
        assert new_move1 == new_move2


# ---------------------------------------------------------------------------
# 7b. Rematch creates a new DB game row so both games count independently
# ---------------------------------------------------------------------------


def test_ws_rematch_stats_counted_independently() -> None:
    """Each rematch game must produce its own DB row so wins/losses are counted correctly.

    Bug: rematch reused the same game_id → finish_game overwrote game 1's result,
    only one row existed in games table, total_games was under-counted.

    Fix: _handle_rematch creates a new DB game row and stores its UUID in
    ConnectionManager._db_game_id; _handle_move uses that UUID for all DB calls.

    Journey:
        game 1: P1 wins → finish_game(original_uuid) + update_elo called once
        rematch: new DB row created (rematch_uuid)
        game 2: P1 wins → finish_game(rematch_uuid) + update_elo called again
    """
    game_uuid = uuid.uuid4()
    game_id = str(game_uuid)
    p1_id = uuid.uuid4()
    p2_id = uuid.uuid4()
    rematch_uuid = uuid.uuid4()

    _seed_redis_board(game_id)

    mock_session = AsyncMock()
    mock_session.commit = AsyncMock()
    mock_record = AsyncMock()
    mock_finish = AsyncMock()
    mock_elo = AsyncMock(return_value=(1016, 984))
    db_game = SimpleNamespace(id=game_uuid, player1_id=p1_id, player2_id=p2_id, winner_id=None)
    rematch_game_row = SimpleNamespace(id=rematch_uuid, player1_id=p1_id, player2_id=p2_id, status="playing")
    mock_get_game = AsyncMock(return_value=db_game)
    mock_create_game = AsyncMock(return_value=SimpleNamespace(id=rematch_uuid, player1_id=p1_id, status="waiting"))
    mock_join_game = AsyncMock(return_value=rematch_game_row)

    with (
        patch("app.store._redis_client", fake_redis_instance),
        patch("app.store.get_redis", return_value=fake_redis_instance),
        patch("app.database.async_session_factory", _mock_session_factory(mock_session)),
        patch("app.main.cleanup_stale_games", AsyncMock()),
        patch("app.websocket.async_session_factory", _mock_session_factory(mock_session)),
        patch("app.websocket.record_move", mock_record),
        patch("app.websocket.finish_game", mock_finish),
        patch("app.websocket.update_elo", mock_elo),
        patch("app.websocket.get_game_by_id", mock_get_game),
        patch("app.websocket.get_player_by_id", AsyncMock(return_value=None)),
        patch("app.websocket.create_game", mock_create_game),
        patch("app.websocket.join_game", mock_join_game),
        TestClient(app) as starlette_client,
        starlette_client.websocket_connect(f"/ws/{game_id}") as ws1,
        starlette_client.websocket_connect(f"/ws/{game_id}") as ws2,
    ):
        # --- Phase 1: Play game 1 to a P1 horizontal win ---
        win_moves = [(1, 0, ws1), (2, 6, ws2), (1, 1, ws1), (2, 6, ws2), (1, 2, ws1), (2, 6, ws2), (1, 3, ws1)]
        for player, column, sender in win_moves:
            sender.send_text(json.dumps({"player": player, "column": column}))
            _receive_non_status(ws1)
            _receive_non_status(ws2)

        # finish_game called once with the original game UUID
        assert mock_finish.call_count == 1
        assert mock_finish.call_args_list[0].args[1] == game_uuid
        assert mock_elo.call_count == 1

        # --- Phase 2: Both players vote rematch ---
        ws1.send_text(json.dumps({"action": "rematch", "player": 1}))
        _receive_non_status(ws2)  # rematch_waiting
        ws2.send_text(json.dumps({"action": "rematch", "player": 2}))
        _receive_non_status(ws1)  # rematch: True
        _receive_non_status(ws2)  # rematch: True

        # A new DB game row must have been created for the rematch
        mock_create_game.assert_called_once()
        mock_join_game.assert_called_once()

        # --- Phase 3: Play game 2 to a P1 win (fresh board after rematch) ---
        win_moves_2 = [(1, 0, ws1), (2, 6, ws2), (1, 1, ws1), (2, 6, ws2), (1, 2, ws1), (2, 6, ws2), (1, 3, ws1)]
        for player, column, sender in win_moves_2:
            sender.send_text(json.dumps({"player": player, "column": column}))
            _receive_non_status(ws1)
            _receive_non_status(ws2)

        # finish_game must have been called a second time with the NEW game UUID
        assert mock_finish.call_count == 2
        second_uuid = mock_finish.call_args_list[1].args[1]
        assert second_uuid == rematch_uuid
        assert second_uuid != game_uuid  # two separate DB rows

        # ELO updated once per completed game
        assert mock_elo.call_count == 2


# ---------------------------------------------------------------------------
# 8. WS identify → move → disconnect → reconnect state consistency
# ---------------------------------------------------------------------------


def test_ws_identify_move_disconnect_reconnect() -> None:
    """Player identifies, makes a move, disconnects, and a new connection sees the board.

    Verifies that Redis state survives WebSocket disconnections and that
    the ConnectionManager correctly tracks connect/disconnect events.
    """
    game_uuid = uuid.uuid4()
    game_id = str(game_uuid)
    _seed_redis_board(game_id)

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
        starlette_client = TestClient(app)

        # First connection: identify + make a move
        with starlette_client.websocket_connect(f"/ws/{game_id}") as ws:
            ws.send_text(json.dumps({"action": "identify", "player": 1, "username": "Alice"}))
            status_msg = json.loads(ws.receive_text())
            assert status_msg["type"] == "player_status"
            assert status_msg["usernames"] == {"1": "Alice"}

            ws.send_text(json.dumps({"player": 1, "column": 2}))
            move_data = _receive_non_status(ws)
            assert move_data["row"] == 5
            assert move_data["column"] == 2

        # After disconnect, Redis should still hold the board
        raw_board = fake_redis_instance._store.get(f"game:{game_id}")
        assert raw_board is not None
        board = json.loads(raw_board)
        assert board[5][2] == 1

        # Second connection: new player sees updated board via a move
        with starlette_client.websocket_connect(f"/ws/{game_id}") as ws2:
            ws2.send_text(json.dumps({"player": 2, "column": 4}))
            data = _receive_non_status(ws2)
            assert data["row"] == 5
            assert data["column"] == 4
            # Board should reflect both moves
            assert data["board"][5][2] == 1  # Alice's move
            assert data["board"][5][4] == 2  # New player's move


# ---------------------------------------------------------------------------
# 9. Mixed REST + WS state consistency
# ---------------------------------------------------------------------------


def test_rest_and_ws_share_redis_state() -> None:
    """Moves made via REST should be visible when reading state through WS (and vice versa).

    Play a move via REST (using httpx in async test), then connect via WS
    and verify the board reflects the REST move before making a WS move.
    """
    game_id = "integ-mixed-state"
    _seed_redis_board(game_id)

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
        starlette_client = TestClient(app)

        # Make a REST move (TestClient supports sync HTTP calls too)
        rest_resp = starlette_client.post(
            f"/games/{game_id}/move",
            json={"game_id": game_id, "player": 1, "column": 0},
        )
        assert rest_resp.status_code == 200
        assert rest_resp.json()["row"] == 5

        # Connect via WS and make the next move (Player 2)
        with starlette_client.websocket_connect(f"/ws/{game_id}") as ws:
            ws.send_text(json.dumps({"player": 2, "column": 1}))
            data = _receive_non_status(ws)
            # Board must include both the REST move and the WS move
            assert data["board"][5][0] == 1  # REST move
            assert data["board"][5][1] == 2  # WS move


# ---------------------------------------------------------------------------
# 10. WS error recovery — invalid moves don't corrupt board state
# ---------------------------------------------------------------------------


def test_ws_error_recovery_board_integrity() -> None:
    """Invalid moves (wrong turn, full column) must not corrupt board state.

    Play valid moves, attempt several invalid ones, then continue with
    valid moves and verify the board remains consistent throughout.
    """
    game_uuid = uuid.uuid4()
    game_id = str(game_uuid)
    _seed_redis_board(game_id)

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
        starlette_client = TestClient(app)
        with (
            starlette_client.websocket_connect(f"/ws/{game_id}") as ws1,
            starlette_client.websocket_connect(f"/ws/{game_id}") as ws2,
        ):
            # Valid: P1 → col 0 (ws1 auto-identifies as P1)
            ws1.send_text(json.dumps({"player": 1, "column": 0}))
            move1 = _receive_non_status(ws1)
            _receive_non_status(ws2)  # consume broadcast on ws2
            assert move1["row"] == 5

            # Invalid: P1 tries again (wrong turn)
            ws1.send_text(json.dumps({"player": 1, "column": 1}))
            err1 = _receive_non_status(ws1)
            assert "error" in err1

            # Invalid: P2 wrong turn from ws1 (ws1 is identified as P1)
            ws1.send_text(json.dumps({"player": 2, "column": 2}))
            err2 = _receive_non_status(ws1)
            assert "error" in err2

            # Valid: P2 → col 1 from ws2 (ws2 auto-identifies as P2)
            ws2.send_text(json.dumps({"player": 2, "column": 1}))
            move2 = _receive_non_status(ws2)
            _receive_non_status(ws1)  # consume broadcast on ws1
            assert move2["row"] == 5
            assert move2["column"] == 1

            # Board integrity check
            board = move2["board"]
            assert board[5][0] == 1  # P1's first valid move
            assert board[5][1] == 2  # P2's valid move
            # All other cells on bottom row should be empty
            for col_index in range(2, COLS):
                assert board[5][col_index] == 0


# ---------------------------------------------------------------------------
# 11. Matchmaking → game → play via WS (end-to-end pipeline)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_matchmaking_to_gameplay_pipeline(client: AsyncClient) -> None:
    """Full pipeline: two players matchmake → game created → moves via REST.

    Validates: matchmaking queuing → ELO-band matching → game creation → playable board.

    Regression: previously, matchmaking created the DB game but never initialised
    the Redis board (``save_game`` was missing).  This caused the first WS/REST
    move to fail with a 404 / KeyError.  The test must NOT call
    ``_seed_redis_board`` — the matchmaking route itself must initialise Redis.
    """
    alice_id = uuid.uuid4()
    bob_id = uuid.uuid4()
    game_id = uuid.uuid4()

    alice = _mock_player(alice_id, "alice", elo_rating=1000)
    bob = _mock_player(bob_id, "bob", elo_rating=1100)
    game = _mock_game(game_id, alice_id, bob_id, "playing")

    # Alice joins → queued
    with patch("app.routes.matchmaking.get_player_by_id", new=AsyncMock(return_value=alice)):
        resp_a = await client.post("/matchmaking/join", json={"player1_id": str(alice_id)})
    assert resp_a.json()["status"] == "queued"

    # Bob joins → matched with Alice
    # NOTE: create_game and join_game are mocked (DB layer) but save_game is NOT
    # mocked — it must actually write the board to (Fake)Redis.
    with (
        patch("app.routes.matchmaking.get_player_by_id", new=AsyncMock(return_value=bob)),
        patch("app.routes.matchmaking.create_game", new=AsyncMock(return_value=_mock_game(game_id, alice_id))),
        patch("app.routes.matchmaking.join_game", new=AsyncMock(return_value=game)),
    ):
        resp_b = await client.post("/matchmaking/join", json={"player1_id": str(bob_id)})

    match_body = resp_b.json()
    assert match_body["status"] == "matched"
    matched_game_id = match_body["game_id"]
    assert matched_game_id == str(game_id)

    # ⚠ No _seed_redis_board() here — the matchmaking route must have done it.
    # Verify Redis board exists (would have been None before the bug fix).
    raw = fake_redis_instance._store.get(f"game:{matched_game_id}")
    assert raw is not None, (
        "Matchmaking must initialise the Redis board on match — "
        "missing save_game() was the root cause of the blank-board production bug"
    )

    # Alice discovers match via status polling
    resp_status = await client.get(f"/matchmaking/status/{alice_id}")
    assert resp_status.json()["status"] == "matched"

    # Now play moves on the matched game via REST — these would 404 before the fix
    p1_resp = await client.post(
        f"/games/{matched_game_id}/move",
        json={"game_id": matched_game_id, "player": 1, "column": 3},
    )
    assert p1_resp.status_code == 200, f"First move failed: {p1_resp.json()}"
    assert p1_resp.json()["row"] == 5

    p2_resp = await client.post(
        f"/games/{matched_game_id}/move",
        json={"game_id": matched_game_id, "player": 2, "column": 4},
    )
    assert p2_resp.status_code == 200, f"Second move failed: {p2_resp.json()}"


# ---------------------------------------------------------------------------
# 11b. Matchmaking → WebSocket play (no manual Redis seed)
# ---------------------------------------------------------------------------


def test_matchmaking_to_websocket_play() -> None:
    """Full pipeline: matchmaking creates game → WS moves succeed.

    Regression: the original production bug was that matchmaking created the
    DB game but never called ``save_game`` to initialise the Redis board.
    The first WS move then failed with KeyError, leaving the board blank.

    This test exercises the *WebSocket* path (not REST) after matchmaking,
    which is the primary play path in production.  It does NOT call
    ``_seed_redis_board`` — the matchmaking endpoint must do it.
    """
    from starlette.testclient import TestClient as StarletteClient

    alice_id = uuid.uuid4()
    bob_id = uuid.uuid4()
    game_id = uuid.uuid4()
    game_id_str = str(game_id)

    alice = _mock_player(alice_id, "alice", elo_rating=1000)
    bob = _mock_player(bob_id, "bob", elo_rating=1100)
    game_obj = _mock_game(game_id, alice_id, bob_id, "playing")

    mock_session = AsyncMock()
    mock_session.commit = AsyncMock()

    with (
        patch("app.store._redis_client", fake_redis_instance),
        patch("app.store.get_redis", return_value=fake_redis_instance),
    ):
        sync_client = StarletteClient(app)

        # ----- Phase 1: matchmaking via REST -----
        # Alice joins
        with patch("app.routes.matchmaking.get_player_by_id", new=AsyncMock(return_value=alice)):
            resp_a = sync_client.post("/matchmaking/join", json={"player1_id": str(alice_id)})
        assert resp_a.json()["status"] == "queued"

        # Bob joins → matched (save_game runs for real against FakeRedis)
        with (
            patch("app.routes.matchmaking.get_player_by_id", new=AsyncMock(return_value=bob)),
            patch("app.routes.matchmaking.create_game", new=AsyncMock(return_value=_mock_game(game_id, alice_id))),
            patch("app.routes.matchmaking.join_game", new=AsyncMock(return_value=game_obj)),
        ):
            resp_b = sync_client.post("/matchmaking/join", json={"player1_id": str(bob_id)})
        assert resp_b.json()["status"] == "matched"

        # Verify Redis board exists — this was the root cause
        assert fake_redis_instance._store.get(f"game:{game_id_str}") is not None, (
            "Matchmaking must save_game() — this was the production bug"
        )

        # ----- Phase 2: play via WebSocket -----
        with (
            patch("app.websocket.async_session_factory", _mock_session_factory(mock_session)),
            patch("app.websocket.record_move", AsyncMock()),
            patch("app.websocket.get_game_by_id", AsyncMock(return_value=None)),
            patch("app.websocket.get_player_by_id", AsyncMock(return_value=None)),
            sync_client.websocket_connect(f"/ws/{game_id_str}") as ws1,
            sync_client.websocket_connect(f"/ws/{game_id_str}") as ws2,
        ):
            # Player 1 drops in column 0
            ws1.send_text(json.dumps({"player": 1, "column": 0}))
            p1_data = _receive_non_status(ws1)
            _receive_non_status(ws2)  # consume broadcast

            assert p1_data["player"] == 1
            assert p1_data["row"] == 5, "First move should land on bottom row"

            # Player 2 drops in column 6
            ws2.send_text(json.dumps({"player": 2, "column": 6}))
            p2_data = _receive_non_status(ws2)

            assert p2_data["player"] == 2
            assert p2_data["row"] == 5


# ---------------------------------------------------------------------------
# 12. Heartbeat + live stats integration
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_heartbeat_updates_live_stats(client: AsyncClient) -> None:
    """Heartbeats should increment online_players in /stats.

    Journey: stats shows 0 → heartbeat from two players → stats shows 2.
    """
    resp_before = await client.get("/stats")
    assert resp_before.json()["online_players"] == 0

    # Two players heartbeat
    await client.post("/heartbeat", json={"player_id": str(uuid.uuid4())})
    await client.post("/heartbeat", json={"player_id": str(uuid.uuid4())})

    resp_after = await client.get("/stats")
    assert resp_after.json()["online_players"] == 2


# ---------------------------------------------------------------------------
# 13. Gravity stacking — pieces stack correctly in the same column
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_gravity_stacking_same_column(client: AsyncClient) -> None:
    """Alternating players drop into the same column — pieces stack bottom-up."""
    game_id = "integ-gravity"
    _seed_redis_board(game_id)

    rows_expected = [5, 4, 3, 2, 1, 0]
    for idx, expected_row in enumerate(rows_expected):
        player = 1 if idx % 2 == 0 else 2
        resp = await client.post(
            f"/games/{game_id}/move",
            json={"game_id": game_id, "player": player, "column": 3},
        )
        assert resp.status_code == 200
        assert resp.json()["row"] == expected_row

    # Column 3 is now full — next drop should fail
    resp_full = await client.post(
        f"/games/{game_id}/move",
        json={"game_id": game_id, "player": 1, "column": 3},
    )
    assert resp_full.status_code == 422


# ---------------------------------------------------------------------------
# 14. WS two-player broadcast symmetry — both get identical data
# ---------------------------------------------------------------------------


def test_ws_two_player_broadcast_symmetry() -> None:
    """Every broadcast from a move must be byte-identical for both connected players."""
    game_uuid = uuid.uuid4()
    game_id = str(game_uuid)
    _seed_redis_board(game_id)

    mock_session = AsyncMock()
    mock_session.commit = AsyncMock()
    mock_record = AsyncMock()
    mock_get_game = AsyncMock(return_value=None)

    with (
        patch("app.store._redis_client", fake_redis_instance),
        patch("app.store.get_redis", return_value=fake_redis_instance),
        patch("app.database.async_session_factory", _mock_session_factory(mock_session)),
        patch("app.main.cleanup_stale_games", AsyncMock()),
        patch("app.websocket.async_session_factory", _mock_session_factory(mock_session)),
        patch("app.websocket.record_move", mock_record),
        patch("app.websocket.get_game_by_id", mock_get_game),
        patch("app.websocket.get_player_by_id", AsyncMock(return_value=None)),
        TestClient(app) as starlette_client,
        starlette_client.websocket_connect(f"/ws/{game_id}") as ws1,
        starlette_client.websocket_connect(f"/ws/{game_id}") as ws2,
    ):
        # P1 moves from ws1, P2 moves from ws2 (auto-identify binds each ws)
        moves = [(1, 0, ws1), (2, 1, ws2), (1, 2, ws1), (2, 3, ws2)]
        for player, column, sender in moves:
            sender.send_text(json.dumps({"player": player, "column": column}))
            data1 = _receive_non_status(ws1)
            data2 = _receive_non_status(ws2)
            assert data1 == data2, f"Mismatch on move ({player}, {column})"
            assert data1["player"] == player
            assert data1["column"] == column


# ---------------------------------------------------------------------------
# 15. WS DB failure resilience across multiple moves
# ---------------------------------------------------------------------------


def test_ws_db_failure_does_not_block_subsequent_moves() -> None:
    """Even if the DB is down, Redis state is updated and moves keep flowing.

    This validates the intentional design: DB persistence is best-effort
    in the WebSocket path, but gameplay must never stall.
    """
    game_uuid = uuid.uuid4()
    game_id = str(game_uuid)
    _seed_redis_board(game_id)

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
        starlette_client = TestClient(app)
        with (
            starlette_client.websocket_connect(f"/ws/{game_id}") as ws1,
            starlette_client.websocket_connect(f"/ws/{game_id}") as ws2,
        ):
            # Three moves with DB completely down — P1 from ws1, P2 from ws2
            ws1.send_text(json.dumps({"player": 1, "column": 0}))
            d1 = _receive_non_status(ws1)
            _receive_non_status(ws2)
            assert d1["player"] == 1 and d1["row"] == 5

            ws2.send_text(json.dumps({"player": 2, "column": 1}))
            d2 = _receive_non_status(ws2)
            _receive_non_status(ws1)
            assert d2["player"] == 2 and d2["row"] == 5

            ws1.send_text(json.dumps({"player": 1, "column": 2}))
            d3 = _receive_non_status(ws1)
            _receive_non_status(ws2)
            assert d3["player"] == 1 and d3["row"] == 5

    # Redis should still have the correct board despite no DB
    board = json.loads(fake_redis_instance._store[f"game:{game_id}"])
    assert board[5][0] == 1
    assert board[5][1] == 2
    assert board[5][2] == 1


# ---------------------------------------------------------------------------
# 16. Matchmaking leave-then-rejoin — queue consistency
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_matchmaking_leave_then_rejoin(client: AsyncClient) -> None:
    """A player who leaves the queue and re-joins should be properly re-queued.

    Validates that leave cleans up correctly and re-join works without
    duplicate entries or stale state.
    """
    alice_id = uuid.uuid4()
    alice = _mock_player(alice_id, "alice", elo_rating=1000)

    with patch("app.routes.matchmaking.get_player_by_id", new=AsyncMock(return_value=alice)):
        # Join
        resp1 = await client.post("/matchmaking/join", json={"player1_id": str(alice_id)})
        assert resp1.json()["status"] == "queued"

        # Verify in queue
        status1 = await client.get(f"/matchmaking/status/{alice_id}")
        assert status1.json()["status"] == "queued"

        # Leave
        leave_resp = await client.delete(f"/matchmaking/leave/{alice_id}")
        assert leave_resp.json()["status"] == "left"

        # Verify gone
        status2 = await client.get(f"/matchmaking/status/{alice_id}")
        assert status2.json()["status"] == "not_queued"

        # Rejoin
        resp2 = await client.post("/matchmaking/join", json={"player1_id": str(alice_id)})
        assert resp2.json()["status"] == "queued"

        # Verify back in queue at position 1
        status3 = await client.get(f"/matchmaking/status/{alice_id}")
        assert status3.json()["status"] == "queued"
        assert status3.json()["position"] == 1
        assert status3.json()["queue_size"] == 1


# ---------------------------------------------------------------------------
# 17. WS draw with ELO draw update
# ---------------------------------------------------------------------------


def test_ws_draw_end_to_end_with_elo_draw() -> None:
    """Play to a draw via WS, verify draw detection, finish_game, and update_elo_draw."""
    game_uuid = uuid.uuid4()
    game_id = str(game_uuid)
    p1_id = uuid.uuid4()
    p2_id = uuid.uuid4()

    # Pre-seed a nearly-full board with 1 cell empty, no 4-in-a-row
    board = [
        [1, 1, 2, 2, 1, 1, 0],
        [2, 2, 1, 1, 2, 2, 1],
        [1, 1, 2, 2, 1, 1, 2],
        [2, 2, 1, 1, 2, 2, 1],
        [1, 1, 2, 2, 1, 1, 2],
        [2, 2, 1, 1, 2, 2, 1],
    ]
    _seed_redis_board(game_id, board)

    mock_session = AsyncMock()
    mock_session.commit = AsyncMock()
    mock_record = AsyncMock()
    mock_finish = AsyncMock()
    mock_elo_draw = AsyncMock(return_value=(1000, 1000))
    db_game = SimpleNamespace(id=game_uuid, player1_id=p1_id, player2_id=p2_id, winner_id=None)
    mock_get_game = AsyncMock(return_value=db_game)

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
        starlette_client = TestClient(app)
        with starlette_client.websocket_connect(f"/ws/{game_id}") as ws:
            # 41 pieces, next_player = 2
            ws.send_text(json.dumps({"player": 2, "column": 6}))
            data = _receive_non_status(ws)

    assert data["draw"] is True
    assert data["winner"] is None

    mock_finish.assert_awaited_once()
    finish_args = mock_finish.call_args[0]
    assert finish_args[2] is None  # no winner
    assert finish_args[3] is True  # is_draw

    mock_elo_draw.assert_awaited_once()
    elo_args = mock_elo_draw.call_args[0]
    assert elo_args[1] == p1_id
    assert elo_args[2] == p2_id


# ---------------------------------------------------------------------------
# 18. Lock contention in REST does not corrupt board
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_lock_contention_leaves_board_intact(client: AsyncClient) -> None:
    """When a lock is held, a conflicting move returns 409 and the board is untouched."""
    game_id = "integ-lock"
    _seed_redis_board(game_id)

    # Make a valid move first
    resp1 = await client.post(f"/games/{game_id}/move", json={"game_id": game_id, "player": 1, "column": 0})
    assert resp1.status_code == 200

    # Pre-acquire the lock
    await fake_redis_instance.set(f"lock:{game_id}", "1", nx=True)

    # Second move should be rejected
    resp2 = await client.post(f"/games/{game_id}/move", json={"game_id": game_id, "player": 2, "column": 1})
    assert resp2.status_code == 409

    # Board should only have the first move
    board_resp = await client.get(f"/games/{game_id}")
    board = board_resp.json()["board"]
    assert board[5][0] == 1
    assert board[5][1] == 0  # rejected move never landed


# ---------------------------------------------------------------------------
# 19. WS identify with DB-seeded opponent name
# ---------------------------------------------------------------------------


def test_ws_identify_seeds_opponent_from_db() -> None:
    """When Player 1 identifies, the system should attempt to load Player 2's name from DB.

    Verifies the cross-layer identify flow: WS → ConnectionManager → DB query → broadcast.
    """
    game_uuid = uuid.uuid4()
    game_id = str(game_uuid)
    p1_id = uuid.uuid4()
    p2_id = uuid.uuid4()
    _seed_redis_board(game_id)

    mock_session = AsyncMock()
    mock_session.commit = AsyncMock()
    db_game = SimpleNamespace(id=game_uuid, player1_id=p1_id, player2_id=p2_id, winner_id=None)
    opponent = SimpleNamespace(username="BobFromDB")

    async def _get_player_by_id(_session: Any, pid: uuid.UUID) -> SimpleNamespace | None:
        if pid == p2_id:
            return opponent
        return None

    with (
        patch("app.store._redis_client", fake_redis_instance),
        patch("app.store.get_redis", return_value=fake_redis_instance),
        patch("app.websocket.async_session_factory", _mock_session_factory(mock_session)),
        patch("app.websocket.get_game_by_id", AsyncMock(return_value=db_game)),
        patch("app.websocket.get_player_by_id", AsyncMock(side_effect=_get_player_by_id)),
    ):
        starlette_client = TestClient(app)
        with starlette_client.websocket_connect(f"/ws/{game_id}") as ws:
            ws.send_text(json.dumps({"action": "identify", "player": 1, "username": "Alice"}))
            status_msg = json.loads(ws.receive_text())

    assert status_msg["type"] == "player_status"
    assert status_msg["usernames"]["1"] == "Alice"
    assert status_msg["usernames"]["2"] == "BobFromDB"


# ---------------------------------------------------------------------------
# 20. Full game via REST with audit log verification
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_rest_moves_generate_audit_entries(client: AsyncClient, tmp_path: Any) -> None:
    """Every REST move should append an entry to the audit log.

    Patches the audit log path to a temp file and verifies JSONL records.
    """
    audit_file = tmp_path / "test_events.log"
    game_id = "integ-audit"
    _seed_redis_board(game_id)

    with patch("app.audit._LOG_PATH", audit_file):
        await client.post(f"/games/{game_id}/move", json={"game_id": game_id, "player": 1, "column": 0})
        await client.post(f"/games/{game_id}/move", json={"game_id": game_id, "player": 2, "column": 1})
        await client.post(f"/games/{game_id}/move", json={"game_id": game_id, "player": 1, "column": 2})

    lines = audit_file.read_text().strip().split("\n")
    assert len(lines) == 3

    for idx, line in enumerate(lines):
        record = json.loads(line)
        assert record["event"] == "MOVE"
        assert record["game_id"] == game_id
        assert "ts" in record  # nanosecond timestamp

    # Timestamps should be monotonically increasing
    timestamps = [json.loads(line)["ts"] for line in lines]
    assert timestamps == sorted(timestamps)
    assert len(set(timestamps)) == 3, "Timestamps should be unique"


# ---------------------------------------------------------------------------
# 21. WS malformed JSON does not disconnect — sends error and continues
# ---------------------------------------------------------------------------


def test_ws_malformed_json_sends_error() -> None:
    """Sending non-JSON text over WebSocket should return an error, not disconnect.

    The connection must remain open so the client can send a valid message
    immediately afterwards.
    """
    game_uuid = uuid.uuid4()
    game_id = str(game_uuid)
    _seed_redis_board(game_id)

    mock_session = AsyncMock()
    mock_session.commit = AsyncMock()
    mock_record = AsyncMock()

    with (
        patch("app.store._redis_client", fake_redis_instance),
        patch("app.store.get_redis", return_value=fake_redis_instance),
        patch("app.websocket.async_session_factory", _mock_session_factory(mock_session)),
        patch("app.websocket.record_move", mock_record),
        patch("app.websocket.get_player_by_id", AsyncMock(return_value=None)),
    ):
        starlette_client = TestClient(app)
        with starlette_client.websocket_connect(f"/ws/{game_id}") as ws:
            # Send garbage — should get an error, not a disconnect
            ws.send_text("this is not valid JSON {{{")
            error_resp = json.loads(ws.receive_text())
            assert "error" in error_resp
            assert "json" in error_resp["error"].lower() or "invalid" in error_resp["error"].lower()

            # Connection is still alive — a valid move should work
            ws.send_text(json.dumps({"player": 1, "column": 3}))
            move_data = _receive_non_status(ws)
            assert move_data["row"] == 5
            assert move_data["column"] == 3


# ---------------------------------------------------------------------------
# 22. Player registration — new player created
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_player_register_creates_new_player(client: AsyncClient) -> None:
    """POST /players with a fresh username should create a new player (is_returning=False).

    Validates: 201 status, correct username/elo_rating, is_returning defaults to False.
    """
    player_id = uuid.uuid4()
    new_player = SimpleNamespace(id=player_id, username="newplayer", elo_rating=1000)

    with (
        patch("app.routes.players.get_player_by_username", AsyncMock(return_value=None)),
        patch("app.routes.players.create_player", AsyncMock(return_value=new_player)),
    ):
        resp = await client.post("/players", json={"username": "newplayer"})

    assert resp.status_code == 201
    body = resp.json()
    assert body["username"] == "newplayer"
    assert body["elo_rating"] == 1000
    assert body["is_returning"] is False
    assert body["games"] == []


# ---------------------------------------------------------------------------
# 23. Player registration — returning player identified
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_player_register_is_returning(client: AsyncClient) -> None:
    """POST /players with an existing username should return is_returning=True.

    The endpoint acts as upsert: existing players are returned with their
    current ELO instead of causing a conflict error.
    """
    player_id = uuid.uuid4()
    existing = SimpleNamespace(id=player_id, username="alice", elo_rating=1200)

    with (
        patch("app.routes.players.get_player_by_username", AsyncMock(return_value=existing)),
        patch("app.routes.players.get_player_games", AsyncMock(return_value=[])),
    ):
        resp = await client.post("/players", json={"username": "alice"})

    assert resp.status_code == 201
    body = resp.json()
    assert body["is_returning"] is True
    assert body["username"] == "alice"
    assert body["elo_rating"] == 1200
    assert body["games"] == []


# ---------------------------------------------------------------------------
# 24. WS move on a game with no prior Redis state starts a fresh board
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 25. Cancel waiting game — creator gets 204 and repo called with correct args
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_cancel_waiting_game_creator_gets_204(client: AsyncClient) -> None:
    """Game creator cancels their WAITING game → 204 No Content.

    Verifies: correct HTTP status, cancel_waiting_game called with the right
    game_id and player_id from the query string.
    """
    game_id = uuid.uuid4()
    creator_id = uuid.uuid4()

    mock_cancel = AsyncMock(return_value=True)
    with patch("app.routes.games.cancel_waiting_game", mock_cancel):
        resp = await client.delete(f"/games/{game_id}/cancel?player_id={creator_id}")

    assert resp.status_code == 204
    mock_cancel.assert_awaited_once()
    _session, called_game_id, called_player_id = mock_cancel.call_args[0]
    assert called_game_id == game_id
    assert called_player_id == creator_id


# ---------------------------------------------------------------------------
# 26. Cancel waiting game — non-creator / wrong status / missing game → 404
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_cancel_waiting_game_non_creator_returns_404(client: AsyncClient) -> None:
    """Non-creator, PLAYING game, or missing game all return 404.

    The repository returns False in all three situations; the route must
    convert that into a 404 with a descriptive message.
    """
    game_id = uuid.uuid4()
    intruder_id = uuid.uuid4()

    with patch("app.routes.games.cancel_waiting_game", AsyncMock(return_value=False)):
        resp = await client.delete(f"/games/{game_id}/cancel?player_id={intruder_id}")

    assert resp.status_code == 404
    detail = resp.json()["detail"].lower()
    assert "not found" in detail or "not the creator" in detail or "already started" in detail


# ---------------------------------------------------------------------------
# 27. Cancel waiting game — missing player_id query param → 422
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_cancel_waiting_game_missing_player_id_returns_422(client: AsyncClient) -> None:
    """Omitting the required player_id query parameter triggers validation → 422."""
    game_id = uuid.uuid4()
    resp = await client.delete(f"/games/{game_id}/cancel")
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 28. Cancel waiting game — game no longer visible in waiting list after cancel
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_cancelled_game_absent_from_waiting_list(client: AsyncClient) -> None:
    """After cancellation GET /games/waiting must not include the cancelled game.

    Journey: list waiting → game is present → cancel → list waiting → game is gone.
    """
    creator_id = uuid.uuid4()
    game_id = uuid.uuid4()
    waiting_game = _mock_game(game_id, creator_id)

    # Before cancel: game appears in the waiting list
    with patch("app.routes.games.get_waiting_games", AsyncMock(return_value=[waiting_game])):
        resp_before = await client.get("/games/waiting")
    assert any(g["id"] == str(game_id) for g in resp_before.json())

    # Cancel the game
    with patch("app.routes.games.cancel_waiting_game", AsyncMock(return_value=True)):
        cancel_resp = await client.delete(f"/games/{game_id}/cancel?player_id={creator_id}")
    assert cancel_resp.status_code == 204

    # After cancel: game is gone from the waiting list
    with patch("app.routes.games.get_waiting_games", AsyncMock(return_value=[])):
        resp_after = await client.get("/games/waiting")
    assert not any(g["id"] == str(game_id) for g in resp_after.json())


# ---------------------------------------------------------------------------
# 29. cancel_waiting_game repository — all four guard conditions
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_cancel_waiting_game_repository_all_cases() -> None:
    """Direct repository-level test covering all four guard conditions.

    Case A: creator cancels WAITING game    → True  (row deleted)
    Case B: non-creator on WAITING game     → False (player1_id guard)
    Case C: creator on PLAYING game         → False (status guard)
    Case D: game not found (None from DB)   → False (null guard)
    """
    from app.db_models import GameStatus
    from app.repository import cancel_waiting_game as repo_cancel

    creator_id = uuid.uuid4()
    intruder_id = uuid.uuid4()
    game_id = uuid.uuid4()

    waiting_game = SimpleNamespace(status=GameStatus.WAITING, player1_id=creator_id)

    # --- Case A: creator, WAITING → True, delete+flush called ---
    session_a = AsyncMock()
    session_a.get = AsyncMock(return_value=waiting_game)
    session_a.delete = AsyncMock()
    session_a.flush = AsyncMock()

    assert await repo_cancel(session_a, game_id, creator_id) is True
    session_a.delete.assert_awaited_once_with(waiting_game)
    session_a.flush.assert_awaited_once()

    # --- Case B: intruder, WAITING → False, no delete ---
    session_b = AsyncMock()
    session_b.get = AsyncMock(return_value=waiting_game)
    session_b.delete = AsyncMock()

    assert await repo_cancel(session_b, game_id, intruder_id) is False
    session_b.delete.assert_not_awaited()

    # --- Case C: creator, PLAYING → False ---
    playing_game = SimpleNamespace(status=GameStatus.PLAYING, player1_id=creator_id)
    session_c = AsyncMock()
    session_c.get = AsyncMock(return_value=playing_game)

    assert await repo_cancel(session_c, game_id, creator_id) is False

    # --- Case D: game not found → False ---
    session_d = AsyncMock()
    session_d.get = AsyncMock(return_value=None)

    assert await repo_cancel(session_d, game_id, creator_id) is False


# ---------------------------------------------------------------------------
# 30. POST /players — empty string username rejected by Pydantic (422)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_register_player_empty_username_returns_422(client: AsyncClient) -> None:
    """POST /players with an empty string username must be rejected (422).

    Pydantic enforces min_length=1 on PlayerCreate.username. This is the
    API-level guard that backs the frontend's client-side empty-check.
    """
    resp = await client.post("/players", json={"username": ""})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 31. POST /players — single-space username passes Pydantic (API gap)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_register_player_whitespace_username_rejected_by_api(client: AsyncClient) -> None:
    """POST /players with a whitespace-only username must be rejected (422).

    The server-side validator now strips whitespace and raises a validation error
    for blank usernames, closing the gap where the frontend guarded with .trim()
    but the API did not.
    """
    resp = await client.post("/players", json={"username": "   "})

    assert resp.status_code == 422, "Whitespace-only username must be rejected server-side"


# ---------------------------------------------------------------------------
# 32. GET /matchmaking/status — not_queued for a player who never joined
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_matchmaking_status_not_queued_for_unknown_player(client: AsyncClient) -> None:
    """Status check for a player who never joined returns not_queued.

    This is the exact server response that the fixed pollMatchmaking handler
    uses as the trigger to re-enable the 'Find Opponent' button.
    """
    unknown_id = uuid.uuid4()

    resp = await client.get(f"/matchmaking/status/{unknown_id}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "not_queued"


# ---------------------------------------------------------------------------
# 33. GET /leaderboard — response payload contains no 'rank' field
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_leaderboard_response_has_no_rank_field(client: AsyncClient) -> None:
    """Leaderboard entries must NOT contain a 'rank' field in the API response.

    The frontend removed the redundant lb-rank span; display order is driven
    by CSS counter(lb). Confirming the API never sends 'rank' prevents any
    future confusion about double-ranking.
    """
    mock_entries = [
        {"username": "alice", "elo_rating": 1200, "total_games": 10},
        {"username": "bob", "elo_rating": 1100, "total_games": 8},
        {"username": "carol", "elo_rating": 1000, "total_games": 3},
    ]
    with patch("app.routes.players.get_leaderboard", AsyncMock(return_value=mock_entries)):
        resp = await client.get("/leaderboard?limit=3")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 3
    for entry in body:
        assert "rank" not in entry
        assert set(entry.keys()) == {"username", "elo_rating", "total_games"}


# ---------------------------------------------------------------------------
# 34. POST /games/{id}/join — player cannot join their own game
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_join_own_game_returns_404(client: AsyncClient) -> None:
    """A player attempting to join their own WAITING game must receive 404.

    The repository returns None when player1_id == player2_id.
    """
    with patch("app.routes.games.join_game", AsyncMock(return_value=None)):
        player_id = uuid.uuid4()
        game_id = uuid.uuid4()
        resp = await client.post(
            f"/games/{game_id}/join",
            json={"player2_id": str(player_id)},
        )

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 35. cancel_waiting_game + waiting-games pipeline — no orphan after creator leaves
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_cancel_waiting_game_full_pipeline(client: AsyncClient) -> None:
    """Full pipeline: create game → verify in waiting list → cancel → verify absent.

    This covers the usability bug where clicking 'Cancel' in the UI used to
    leave an orphaned WAITING game that other players could join and find empty.
    """
    creator_id = uuid.uuid4()
    game_id = uuid.uuid4()

    created_game = _mock_game(game_id, creator_id, status_value="waiting")

    # Step 1: Create a waiting game
    with patch("app.routes.games.create_game", AsyncMock(return_value=created_game)):
        create_resp = await client.post("/games", json={"player1_id": str(creator_id)})
    assert create_resp.status_code == 201
    assert create_resp.json()["status"] == "waiting"

    # Step 2: Confirm it appears in the waiting-games list
    with patch("app.routes.games.get_waiting_games", AsyncMock(return_value=[created_game])):
        list_resp = await client.get("/games/waiting")
    assert any(g["id"] == str(game_id) for g in list_resp.json())

    # Step 3: Creator cancels
    with patch("app.routes.games.cancel_waiting_game", AsyncMock(return_value=True)):
        cancel_resp = await client.delete(f"/games/{game_id}/cancel?player_id={creator_id}")
    assert cancel_resp.status_code == 204

    # Step 4: Waiting list no longer includes the game — no orphan for others to join
    with patch("app.routes.games.get_waiting_games", AsyncMock(return_value=[])):
        list_after = await client.get("/games/waiting")
    assert not any(g["id"] == str(game_id) for g in list_after.json())


# ---------------------------------------------------------------------------
# 36. WS game — highlightColumn turn guard (column indicator only while it's your turn)
# ---------------------------------------------------------------------------


def test_ws_move_rejected_when_out_of_turn() -> None:
    """A move submitted for the wrong player number is rejected with an error.

    This is the server-side counterpart to the frontend's turn-guard in
    highlightColumn: moves sent out of turn get an error response, not a
    board update, so the UI can't be tricked by circumventing the highlight guard.
    """
    game_uuid = uuid.uuid4()
    game_id = str(game_uuid)
    _seed_redis_board(game_id)

    mock_session = AsyncMock()
    mock_session.commit = AsyncMock()
    mock_record = AsyncMock()

    with (
        patch("app.store._redis_client", fake_redis_instance),
        patch("app.store.get_redis", return_value=fake_redis_instance),
        patch("app.websocket.async_session_factory", _mock_session_factory(mock_session)),
        patch("app.websocket.record_move", mock_record),
        patch("app.websocket.get_player_by_id", AsyncMock(return_value=None)),
    ):
        starlette_client = TestClient(app)
        with starlette_client.websocket_connect(f"/ws/{game_id}") as ws:
            # Identify as Player 1 so the connection is permanently bound to player 1
            ws.send_text(json.dumps({"action": "identify", "player": 1, "username": "Tester"}))
            json.loads(ws.receive_text())  # consume player_status broadcast (type == "player_status")

            # Try to move as Player 2 over a Player-1-identified connection → error
            # (server rejects: "You are player 1, cannot move as player 2")
            ws.send_text(json.dumps({"player": 2, "column": 3}))
            err = _receive_non_status(ws)
            assert "error" in err
            # Board must be unchanged (no piece landed)
            board_resp = fake_redis_instance._store.get(f"game:{game_id}")
            import json as _json

            board = _json.loads(board_resp)
            assert all(cell == 0 for row in board for cell in row)

            # Now send a valid Player-1 move — must succeed
            ws.send_text(json.dumps({"player": 1, "column": 3}))
            move = _receive_non_status(ws)
            assert "error" not in move
            assert move["player"] == 1
            assert move["row"] == 5


def test_ws_fresh_board_first_move_lands_at_bottom() -> None:
    """First WebSocket move on a freshly seeded board lands on the bottom row.

    Simulates the state after ``join_existing_game`` calls
    ``save_game(redis, game_id, Connect4())`` to seed Redis.
    ``load_game`` raises ``KeyError`` on a missing key, so the board must be
    pre-seeded before the WebSocket move is sent; the first piece should land
    on row 5 (bottom) of the empty board.
    """
    game_uuid = uuid.uuid4()
    game_id = str(game_uuid)
    # Seed Redis as join_existing_game does — load_game raises KeyError on missing keys
    _seed_redis_board(game_id)

    mock_session = AsyncMock()
    mock_session.commit = AsyncMock()
    mock_record = AsyncMock()

    with (
        patch("app.store._redis_client", fake_redis_instance),
        patch("app.store.get_redis", return_value=fake_redis_instance),
        patch("app.websocket.async_session_factory", _mock_session_factory(mock_session)),
        patch("app.websocket.record_move", mock_record),
        patch("app.websocket.get_player_by_id", AsyncMock(return_value=None)),
    ):
        starlette_client = TestClient(app)
        with starlette_client.websocket_connect(f"/ws/{game_id}") as ws:
            ws.send_text(json.dumps({"player": 1, "column": 3}))
            data = _receive_non_status(ws)

    assert data["row"] == 5, "First piece must land on the bottom row"
    assert data["column"] == 3
    assert data["winner"] is None
    assert data["draw"] is False
    # Board should be empty everywhere except the one piece placed
    board = data["board"]
    assert board[5][3] == 1
    for col in range(COLS):
        if col != 3:
            assert board[5][col] == 0
    for row in range(ROWS - 1):  # rows 0-4 should all be empty
        assert all(cell == 0 for cell in board[row])
