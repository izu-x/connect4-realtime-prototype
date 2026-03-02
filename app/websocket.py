"""WebSocket endpoint — real-time game play, identify, and rematch."""

from __future__ import annotations

import json
import logging
import uuid
from contextlib import suppress
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.audit import log_event
from app.connection_manager import manager
from app.database import async_session_factory
from app.game import ColumnFullError, Connect4, GameOverError, InvalidColumnError, InvalidTurnError
from app.repository import (
    create_game,
    finish_game,
    get_game_by_id,
    get_player_by_id,
    join_game,
    record_move,
    update_elo,
    update_elo_draw,
)
from app.store import acquire_game_lock, get_redis, load_game, save_game

logger: logging.Logger = logging.getLogger(__name__)

router: APIRouter = APIRouter()


@router.websocket("/ws/{game_id}")
async def websocket_endpoint(websocket: WebSocket, game_id: str) -> None:
    """WebSocket connection for real-time game updates.

    Clients send: ``{"player": 1, "column": 3}``
    Server broadcasts the full move response JSON to all room participants.

    Args:
        websocket: Incoming WebSocket connection.
        game_id: Unique identifier of the game room.
    """
    await manager.connect(game_id, websocket)
    redis = await get_redis()
    player_identified: bool = False
    try:
        while True:
            data = await websocket.receive_text()
            try:
                payload = json.loads(data)
            except json.JSONDecodeError:
                await websocket.send_text(json.dumps({"error": "Invalid JSON payload."}))
                continue

            # Register player on first message that carries a "player" field.
            # This also handles an explicit `action: "identify"` payload.
            if not player_identified and "player" in payload:
                try:
                    await _handle_identify(game_id, websocket, payload)
                    player_identified = True
                except (ValueError, TypeError) as exc:
                    await websocket.send_text(json.dumps({"error": f"Invalid identify payload: {exc}"}))

            if payload.get("action") == "identify":
                continue

            if payload.get("action") == "rematch":
                await _handle_rematch(game_id, websocket, redis, payload)
                continue

            try:
                await _handle_move(game_id, websocket, redis, payload)
            except (KeyError, ValueError, TypeError) as exc:
                await websocket.send_text(json.dumps({"error": f"Invalid payload: {exc}"}))

    except WebSocketDisconnect:
        await manager.disconnect_and_notify(game_id, websocket)
    except Exception as exc:  # noqa: BLE001
        logger.exception(f"Unexpected WS error: {exc}")
        await manager.disconnect_and_notify(game_id, websocket)


# ---------------------------------------------------------------------------
# Message handlers
# ---------------------------------------------------------------------------


async def _handle_identify(game_id: str, websocket: WebSocket, payload: dict[str, Any]) -> None:
    """Process an identify message — register player number and seed names.

    Args:
        game_id: Unique identifier of the game room.
        websocket: The client's WebSocket connection.
        payload: Parsed JSON message containing ``player`` and optional ``username``.
    """
    player_number = int(payload["player"])
    if player_number not in (1, 2):
        raise ValueError(f"player must be 1 or 2, got {player_number}")
    manager._player_map.setdefault(game_id, {})[websocket] = player_number

    # Track username if provided
    username = payload.get("username")
    if username:
        manager._usernames.setdefault(game_id, {})[player_number] = str(username)

    # Seed opponent name from DB if not already known
    opponent_number = 3 - player_number
    if opponent_number not in manager._usernames.get(game_id, {}):
        try:
            game_uuid = uuid.UUID(game_id)
            async with async_session_factory() as db_session:
                db_game = await get_game_by_id(db_session, game_uuid)
                if db_game is not None:
                    opp_id = db_game.player2_id if player_number == 1 else db_game.player1_id
                    if opp_id:
                        opp_player = await get_player_by_id(db_session, opp_id)
                        if opp_player:
                            manager._usernames.setdefault(game_id, {})[opponent_number] = opp_player.username
        except Exception:  # noqa: BLE001
            pass  # Best-effort — name will arrive when opponent identifies

    await manager.broadcast(
        game_id,
        {
            "type": "player_status",
            "player": player_number,
            "status": "connected",
            "connected_players": manager._connected_player_numbers(game_id),
            "usernames": manager._usernames.get(game_id, {}),
        },
    )


async def _handle_rematch(
    game_id: str,
    websocket: WebSocket,
    redis: Any,
    payload: dict[str, Any],
) -> None:
    """Process a rematch vote — two votes triggers a board reset.

    Args:
        game_id: Unique identifier of the game room.
        websocket: The voting client's WebSocket connection.
        redis: Async Redis client.
        payload: Parsed JSON message containing ``player``.
    """
    try:
        player = int(payload.get("player", 0))
    except (ValueError, TypeError):
        await websocket.send_text(json.dumps({"error": "Invalid player value in rematch payload."}))
        return

    if player not in (1, 2):
        await websocket.send_text(json.dumps({"error": "player must be 1 or 2 to vote for rematch."}))
        return

    # Validate rematch vote comes from the identified player on this connection
    identified = manager._player_map.get(game_id, {}).get(websocket)
    if identified is not None and player != identified:
        await websocket.send_text(json.dumps({"error": "Rematch vote must match your identified player number."}))
        return

    votes = manager._rematch_votes.setdefault(game_id, set())
    votes.add(player)

    if len(votes) >= 2:
        manager._rematch_votes.pop(game_id, None)
        await redis.delete(f"game:{game_id}")
        await save_game(redis, game_id, Connect4())  # re-initialise board so first move after rematch succeeds
        # Create a fresh DB game row so this rematch is tracked as a separate game.
        # The WebSocket room (game_id) stays the same; only the DB UUID changes.
        try:
            game_uuid = uuid.UUID(game_id)
            async with async_session_factory() as db_session:
                original = await get_game_by_id(db_session, game_uuid)
                if original is not None and original.player2_id is not None:
                    new_game = await create_game(db_session, original.player1_id)
                    joined = await join_game(db_session, new_game.id, original.player2_id)
                    if joined is not None:
                        await db_session.commit()
                        manager._db_game_id[game_id] = str(new_game.id)
                    else:
                        logger.warning(
                            "Rematch join_game returned None for room %s; rematch moves will not be persisted to DB.",
                            game_id,
                        )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to create rematch game row in DB: %s", exc, exc_info=True)
        await manager.broadcast(game_id, {"rematch": True})
    else:
        for ws in list(manager._rooms.get(game_id, [])):
            if ws is not websocket:
                with suppress(Exception):  # noqa: BLE001
                    await ws.send_text(json.dumps({"rematch_waiting": True}))


async def _handle_move(
    game_id: str,
    websocket: WebSocket,
    redis: Any,
    payload: dict[str, Any],
) -> None:
    """Process a player move — lock, validate, persist, and broadcast.

    Args:
        game_id: Unique identifier of the game room.
        websocket: The moving player's WebSocket connection.
        redis: Async Redis client.
        payload: Parsed JSON message containing ``player`` and ``column``.
    """
    player = int(payload["player"])
    column = int(payload["column"])

    if player not in (1, 2):
        await websocket.send_text(json.dumps({"error": "Invalid player number."}))
        return

    # Validate move comes from the identified player on this connection
    identified = manager._player_map.get(game_id, {}).get(websocket)
    if identified is not None and player != identified:
        msg = f"You are player {identified}, cannot move as player {player}."
        await websocket.send_text(json.dumps({"error": msg}))
        return

    async with acquire_game_lock(redis, game_id) as locked:
        if not locked:
            await websocket.send_text(json.dumps({"error": "Move collision, please retry."}))
            return

        try:
            game = await load_game(redis, game_id)
        except KeyError:
            # Auto-recover: Redis key expired or lost — create a fresh board
            game = Connect4()
            await save_game(redis, game_id, game)
        try:
            row = game.drop(player, column)
        except (ColumnFullError, GameOverError, InvalidTurnError, InvalidColumnError) as exc:
            await websocket.send_text(json.dumps({"error": str(exc)}))
            return

        await save_game(redis, game_id, game)

    # Persist move and handle game-over in PostgreSQL.
    # After a rematch the room keeps the same ws game_id but the DB row is new;
    # manager._db_game_id tracks the current DB UUID for this room.
    move_number = sum(1 for r in game.board for c in r if c != 0)
    try:
        game_uuid = uuid.UUID(manager._db_game_id.get(game_id, game_id))
        async with async_session_factory() as db_session:
            await record_move(db_session, game_uuid, player, column, row, move_number)
            if game.winner is not None or game.is_draw:
                db_game = await get_game_by_id(db_session, game_uuid)
                if db_game is not None and db_game.player2_id is not None:
                    winner_uuid: uuid.UUID | None = None
                    if game.winner == 1:
                        winner_uuid = db_game.player1_id
                    elif game.winner == 2:
                        winner_uuid = db_game.player2_id
                    await finish_game(db_session, game_uuid, winner_uuid, game.is_draw)
                    if winner_uuid is not None:
                        loser_id = db_game.player1_id
                        if winner_uuid == db_game.player1_id:
                            loser_id = db_game.player2_id
                        await update_elo(db_session, winner_uuid, loser_id)
                    elif game.is_draw:
                        await update_elo_draw(db_session, db_game.player1_id, db_game.player2_id)
            await db_session.commit()
    except Exception as db_exc:  # noqa: BLE001
        logger.warning(f"Failed to persist move to DB: {db_exc}")

    await log_event(
        "MOVE_WS",
        {
            "game_id": game_id,
            "player": player,
            "column": column,
            "row": row,
            "winner": game.winner,
            "draw": game.is_draw,
        },
    )

    response = {
        "game_id": game_id,
        "player": player,
        "column": column,
        "row": row,
        "winner": game.winner,
        "draw": game.is_draw,
        "board": game.board,
        "winning_cells": [list(cell) for cell in game.winning_cells],
    }
    await manager.broadcast(game_id, response)
