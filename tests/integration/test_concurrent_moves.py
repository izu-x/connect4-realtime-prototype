"""Race condition and concurrency tests for the move path.

Two sections:

1. ``acquire_game_lock`` invariants — pure async, a local FakeRedis, no WS needed.
   These prove the lock protocol itself is correct regardless of transport.

2. WebSocket board-consistency tests — two connections playing alternating moves,
   plus a simultaneous-move scenario that verifies exactly one piece lands when
   two connections race to make the same move at the same time.
"""

from __future__ import annotations

import asyncio
import json
import threading
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.testclient import TestClient

from app.game import COLS, ROWS
from app.main import app
from app.store import acquire_game_lock
from tests.conftest import FakeRedis, fake_redis_instance

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_GAME_UUID: uuid.UUID = uuid.uuid4()
_GAME_ID: str = str(_GAME_UUID)
_P1_ID: uuid.UUID = uuid.uuid4()
_P2_ID: uuid.UUID = uuid.uuid4()


def _empty_board() -> list[list[int]]:
    """Return a fresh 6×7 board filled with zeros."""
    return [[0] * COLS for _ in range(ROWS)]


def _seed_board(board: list[list[int]] | None = None) -> None:
    """Write a board (default: empty) into the shared fake Redis store."""
    fake_redis_instance._store[f"game:{_GAME_ID}"] = json.dumps(board or _empty_board())


def _mock_session_factory(session: AsyncMock) -> MagicMock:
    """Return an async-context-manager factory that yields *session*."""

    @asynccontextmanager
    async def _factory():  # noqa: ANN202
        yield session

    return MagicMock(side_effect=_factory)


def _recv_skip_status(ws) -> dict:  # noqa: ANN001
    """Read from *ws*, discard player_status frames, return first non-status message."""
    while True:
        data = json.loads(ws.receive_text())
        if data.get("type") == "player_status":
            continue
        return data


def _db_game() -> SimpleNamespace:
    return SimpleNamespace(id=_GAME_UUID, player1_id=_P1_ID, player2_id=_P2_ID, winner_id=None)


# ===========================================================================
# Section 1 — acquire_game_lock invariants (pure async, no network)
# ===========================================================================


@pytest.mark.anyio
async def test_concurrent_lock_only_one_acquired() -> None:
    """Two coroutines racing for the same lock — exactly one succeeds, one is denied."""
    redis = FakeRedis()
    results: list[bool] = []

    async def _try_lock() -> None:
        async with acquire_game_lock(redis, "game-race") as locked:
            results.append(locked)
            await asyncio.sleep(0)  # yield while holding so the sibling coroutine can attempt

    await asyncio.gather(_try_lock(), _try_lock())

    assert results.count(True) == 1, f"Expected exactly 1 acquisition, got: {results}"
    assert results.count(False) == 1


@pytest.mark.anyio
async def test_lock_reacquirable_after_holder_exits() -> None:
    """Once the first holder's context exits, a fresh acquire must succeed."""
    redis = FakeRedis()

    async with acquire_game_lock(redis, "game-seq") as first:
        assert first is True

    async with acquire_game_lock(redis, "game-seq") as second:
        assert second is True


@pytest.mark.anyio
async def test_lock_released_even_after_exception() -> None:
    """Lock must be released (key deleted) when the protected block raises."""
    redis = FakeRedis()

    with pytest.raises(RuntimeError):
        async with acquire_game_lock(redis, "game-exc") as locked:
            assert locked is True
            raise RuntimeError("simulated failure inside lock")

    assert await redis.get("lock:game-exc") is None, "lock key must be cleared after exception"

    # Confirm a subsequent acquire works normally
    async with acquire_game_lock(redis, "game-exc") as locked_again:
        assert locked_again is True


@pytest.mark.anyio
async def test_eight_concurrent_attempts_exactly_one_wins() -> None:
    """Eight coroutines racing for the same game lock — exactly 1 wins, 7 are rejected."""
    redis = FakeRedis()
    results: list[bool] = []

    async def _try_lock() -> None:
        async with acquire_game_lock(redis, "game-8way") as locked:
            results.append(locked)
            await asyncio.sleep(0)  # hold long enough for siblings to arrive

    await asyncio.gather(*[_try_lock() for _ in range(8)])

    assert results.count(True) == 1, f"Expected 1 winner, got: {results}"
    assert results.count(False) == 7


@pytest.mark.anyio
async def test_non_acquired_lock_does_not_delete_key_on_exit() -> None:
    """The losing coroutine must NOT release the lock owned by the winner.

    Verified behaviourally: after the failed (inner) acquire exits, a third
    acquire attempt must also fail, proving the winner's lock is still held.
    """
    redis = FakeRedis()

    async with acquire_game_lock(redis, "game-owner") as outer_locked:
        assert outer_locked is True

        # Inner acquire fails — the lock is already held by the outer context.
        async with acquire_game_lock(redis, "game-owner") as inner_locked:
            assert inner_locked is False

        # After the *failed* inner exits, the outer lock must still be active.
        # Verify by attempting yet another acquire — it must also be denied.
        async with acquire_game_lock(redis, "game-owner") as check_locked:
            assert check_locked is False, "Winner's lock was incorrectly released when the losing coroutine exited"

    # After the outer (winning) context exits the lock must finally be gone.
    async with acquire_game_lock(redis, "game-owner") as after_locked:
        assert after_locked is True, "Lock must be available after the winner releases it"


# ===========================================================================
# Section 2 — WebSocket board-consistency tests
# ===========================================================================


def test_two_ws_alternating_moves_board_stays_consistent() -> None:
    """P1 and P2 each make 3 moves via separate WS connections.

    After 6 alternating drops the board must contain exactly 6 pieces placed
    at the correct gravity positions with no overwrite or corruption.
    """
    fake_redis_instance.reset()
    _seed_board()

    mock_session = AsyncMock()
    mock_session.commit = AsyncMock()

    with (
        patch("app.store._redis_client", fake_redis_instance),
        patch("app.store.get_redis", return_value=fake_redis_instance),
        patch("app.websocket.async_session_factory", _mock_session_factory(mock_session)),
        patch("app.websocket.record_move", AsyncMock()),
        patch("app.websocket.get_game_by_id", AsyncMock(return_value=_db_game())),
        patch("app.websocket.get_player_by_id", AsyncMock(return_value=None)),
        patch("app.websocket.finish_game", AsyncMock()),
        patch("app.websocket.update_elo", AsyncMock()),
        patch("app.websocket.update_elo_draw", AsyncMock()),
        patch("app.websocket.join_game", AsyncMock()),
    ):
        client = TestClient(app)
        with (
            client.websocket_connect(f"/ws/{_GAME_ID}") as ws1,
            client.websocket_connect(f"/ws/{_GAME_ID}") as ws2,
        ):
            # Each tuple: (sender ws, player number, column)
            # P1 drops in col 0, P2 drops in col 1 — three rounds each.
            moves = [
                (ws1, 1, 0),
                (ws2, 2, 1),
                (ws1, 1, 0),
                (ws2, 2, 1),
                (ws1, 1, 0),
                (ws2, 2, 1),
            ]

            for move_index, (sender, player, col) in enumerate(moves):
                sender.send_text(json.dumps({"player": player, "column": col}))

                # Both connections receive the broadcast — consume from each.
                # _recv_skip_status skips player_status frames and returns the move frame.
                resp_primary = _recv_skip_status(sender)
                other_ws = ws2 if sender is ws1 else ws1
                _recv_skip_status(other_ws)

                assert "error" not in resp_primary, f"Move {move_index + 1} returned an error: {resp_primary['error']}"
                assert resp_primary["player"] == player
                assert resp_primary["column"] == col

                # Board piece count must match expected after each move
                piece_count = sum(resp_primary["board"][r][c] != 0 for r in range(ROWS) for c in range(COLS))
                assert piece_count == move_index + 1, (
                    f"After move {move_index + 1}: expected {move_index + 1} pieces, found {piece_count}"
                )

    # Final board in Redis must have exactly 6 pieces at the correct positions.
    raw = fake_redis_instance._store.get(f"game:{_GAME_ID}")
    assert raw is not None, "Board key must still exist in Redis after game play"
    final_board = json.loads(raw)

    total_pieces = sum(final_board[r][c] != 0 for r in range(ROWS) for c in range(COLS))
    assert total_pieces == 6

    # Col 0 has 3 P1 pieces stacked from the bottom; col 1 has 3 P2 pieces.
    assert final_board[ROWS - 1][0] == 1
    assert final_board[ROWS - 2][0] == 1
    assert final_board[ROWS - 3][0] == 1
    assert final_board[ROWS - 1][1] == 2
    assert final_board[ROWS - 2][1] == 2
    assert final_board[ROWS - 3][1] == 2


def test_board_cells_never_overwritten_during_multi_move_game() -> None:
    """Once a cell is claimed by a player it must never change to the other player's value."""
    fake_redis_instance.reset()
    _seed_board()

    mock_session = AsyncMock()
    mock_session.commit = AsyncMock()

    with (
        patch("app.store._redis_client", fake_redis_instance),
        patch("app.store.get_redis", return_value=fake_redis_instance),
        patch("app.websocket.async_session_factory", _mock_session_factory(mock_session)),
        patch("app.websocket.record_move", AsyncMock()),
        patch("app.websocket.get_game_by_id", AsyncMock(return_value=_db_game())),
        patch("app.websocket.get_player_by_id", AsyncMock(return_value=None)),
        patch("app.websocket.finish_game", AsyncMock()),
        patch("app.websocket.update_elo", AsyncMock()),
        patch("app.websocket.update_elo_draw", AsyncMock()),
        patch("app.websocket.join_game", AsyncMock()),
    ):
        client = TestClient(app)
        snapshots: list[list[list[int]]] = []

        with (
            client.websocket_connect(f"/ws/{_GAME_ID}") as ws1,
            client.websocket_connect(f"/ws/{_GAME_ID}") as ws2,
        ):
            moves = [(ws1, 1, 3), (ws2, 2, 3), (ws1, 1, 5), (ws2, 2, 5)]
            for sender, player, col in moves:
                sender.send_text(json.dumps({"player": player, "column": col}))
                resp = _recv_skip_status(sender)
                other = ws2 if sender is ws1 else ws1
                _recv_skip_status(other)
                if "board" in resp:
                    snapshots.append([row[:] for row in resp["board"]])

    # For every non-zero cell in snapshot N, the same cell must hold the same
    # value (or be zero, which can't happen as pieces don't disappear) in snapshot N+1.
    for snap_idx in range(len(snapshots) - 1):
        before = snapshots[snap_idx]
        after = snapshots[snap_idx + 1]
        for r in range(ROWS):
            for c in range(COLS):
                if before[r][c] != 0:
                    assert after[r][c] == before[r][c], (
                        f"Cell ({r},{c}) changed from {before[r][c]} to {after[r][c]} "
                        f"between snapshots {snap_idx} and {snap_idx + 1}"
                    )


def test_simultaneous_p1_clones_only_one_move_lands() -> None:
    """Two WS connections both identifying as P1 fire a move concurrently via threads.

    The locking + turn-validation path must ensure that exactly one piece ends up
    on the board. The board must have precisely 1 piece after both moves resolve.

    How it works: both threads connect to the game, wait at a threading.Barrier so
    their move messages are dispatched simultaneously, then each reads back its
    response. The asyncio event loop serialises the two handlers, so one move lands
    and the other receives either a lock-collision or an InvalidTurnError — both
    leave the board with a single piece.
    """
    fake_redis_instance.reset()
    _seed_board()

    mock_session = AsyncMock()
    mock_session.commit = AsyncMock()

    barrier = threading.Barrier(2)
    received: dict[int, list[dict]] = {1: [], 2: []}

    def _run_player(player_key: int, ws) -> None:  # noqa: ANN001
        barrier.wait()  # synchronise: both threads send exactly together
        ws.send_text(json.dumps({"player": 1, "column": 0}))
        # Drain messages until we hit a non-status response (move result or error).
        # We consume at most 5 frames to avoid blocking forever on any ordering.
        for _ in range(5):
            try:
                msg = json.loads(ws.receive_text())
                received[player_key].append(msg)
                if msg.get("type") != "player_status":
                    break
            except Exception:  # noqa: BLE001
                break

    with (
        patch("app.store._redis_client", fake_redis_instance),
        patch("app.store.get_redis", return_value=fake_redis_instance),
        patch("app.websocket.async_session_factory", _mock_session_factory(mock_session)),
        patch("app.websocket.record_move", AsyncMock()),
        patch("app.websocket.get_game_by_id", AsyncMock(return_value=_db_game())),
        patch("app.websocket.get_player_by_id", AsyncMock(return_value=None)),
        patch("app.websocket.finish_game", AsyncMock()),
        patch("app.websocket.update_elo", AsyncMock()),
        patch("app.websocket.update_elo_draw", AsyncMock()),
        patch("app.websocket.join_game", AsyncMock()),
    ):
        client = TestClient(app)
        with (
            client.websocket_connect(f"/ws/{_GAME_ID}") as ws1,
            client.websocket_connect(f"/ws/{_GAME_ID}") as ws2,
        ):
            t1 = threading.Thread(target=_run_player, args=(1, ws1))
            t2 = threading.Thread(target=_run_player, args=(2, ws2))
            t1.start()
            t2.start()
            t1.join(timeout=10)
            t2.join(timeout=10)

    # The core invariant: regardless of which move won, exactly ONE piece
    # must be on the board after both concurrent move attempts resolve.
    raw = fake_redis_instance._store.get(f"game:{_GAME_ID}")
    assert raw is not None, "Board key must exist in Redis"
    final_board = json.loads(raw)
    total_pieces = sum(final_board[r][c] != 0 for r in range(ROWS) for c in range(COLS))
    assert total_pieces == 1, (
        f"Expected exactly 1 piece after two simultaneous P1 moves, found {total_pieces} — board corruption detected"
    )
