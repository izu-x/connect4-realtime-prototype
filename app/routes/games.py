"""Game CRUD and board-state endpoints."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import log_event
from app.database import async_session_factory, get_db
from app.game import ColumnFullError, Connect4, GameOverError, InvalidColumnError, InvalidTurnError
from app.models import (
    GameCreate,
    GameJoin,
    GameResponse,
    MoveRecord,
    MoveRequest,
    MoveResponse,
    game_to_response,
)
from app.repository import (
    cancel_waiting_game,
    create_game,
    finish_game,
    get_game_by_id,
    get_game_moves,
    get_player_by_id,
    get_recent_games,
    get_waiting_games,
    join_game,
    record_move,
    update_elo,
    update_elo_draw,
)
from app.store import acquire_game_lock, get_redis, load_game, save_game

logger: logging.Logger = logging.getLogger(__name__)

router: APIRouter = APIRouter(tags=["games"])


# ---------------------------------------------------------------------------
# Board state (Redis)
# ---------------------------------------------------------------------------


@router.post("/games/{game_id}/move", response_model=MoveResponse, status_code=status.HTTP_200_OK)
async def make_move(game_id: str, move: MoveRequest) -> MoveResponse:
    """Process a single player move via REST.

    Uses a Redis SETNX lock to guarantee that two simultaneous requests
    for the same game cannot corrupt each other's state.

    Args:
        game_id: Unique identifier of the game from the URL path.
        move: Validated move request containing player and column.

    Returns:
        MoveResponse with the resulting board state.

    Raises:
        HTTPException: On game_id mismatch (422), column full (422),
            game over (409), or lock contention (409).
    """
    if move.game_id != game_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="game_id in URL and body must match.",
        )

    redis = await get_redis()

    async with acquire_game_lock(redis, game_id) as locked:
        if not locked:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Another move is being processed for this game. Please retry.",
            )

        try:
            game = await load_game(redis, game_id)
        except KeyError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Game not found or expired.",
            )

        try:
            row = game.drop(move.player, move.column)
        except ColumnFullError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
        except GameOverError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        except InvalidTurnError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
        except InvalidColumnError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

        await save_game(redis, game_id, game)

    # Persist move and handle game-over in PostgreSQL (best-effort)
    move_number = sum(1 for r in game.board for c in r if c != 0)
    try:
        game_uuid = uuid.UUID(game_id)
        async with async_session_factory() as db_session:
            await record_move(db_session, game_uuid, move.player, move.column, row, move_number)
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
                        loser_id = db_game.player1_id if winner_uuid != db_game.player1_id else db_game.player2_id
                        await update_elo(db_session, winner_uuid, loser_id)
                    elif game.is_draw:
                        await update_elo_draw(db_session, db_game.player1_id, db_game.player2_id)
            await db_session.commit()
    except Exception:  # noqa: BLE001
        logger.warning("Failed to persist REST move to DB", exc_info=True)

    await log_event(
        "MOVE",
        {
            "game_id": game_id,
            "player": move.player,
            "column": move.column,
            "row": row,
            "winner": game.winner,
            "draw": game.is_draw,
        },
    )

    return MoveResponse(
        game_id=game_id,
        player=move.player,
        column=move.column,
        row=row,
        winner=game.winner,
        draw=game.is_draw,
        board=game.board,
        winning_cells=[list(cell) for cell in game.winning_cells],
    )


# ---------------------------------------------------------------------------
# Game lifecycle (PostgreSQL) — static routes BEFORE {game_id} to avoid shadowing
# ---------------------------------------------------------------------------


@router.get("/games/recent", response_model=list[GameResponse], status_code=status.HTTP_200_OK)
async def recent_games(
    limit: int = Query(default=10, ge=1, le=100), db: AsyncSession = Depends(get_db)
) -> list[GameResponse]:
    """Return the most recently finished games.

    Args:
        limit: Maximum number of games (default 10).
        db: Injected database session.

    Returns:
        List of recently finished games.
    """
    games = await get_recent_games(db, limit=limit)
    return [game_to_response(game) for game in games]


@router.get("/games/waiting", response_model=list[GameResponse], status_code=status.HTTP_200_OK)
async def waiting_games(
    limit: int = Query(default=50, ge=1, le=200), db: AsyncSession = Depends(get_db)
) -> list[GameResponse]:
    """Return games that are waiting for a second player.

    Args:
        limit: Maximum number of games (default 50).
        db: Injected database session.

    Returns:
        List of games with WAITING status.
    """
    games = await get_waiting_games(db, limit=limit)
    return [game_to_response(game) for game in games]


@router.get("/games/{game_id}", status_code=status.HTTP_200_OK)
async def get_game(game_id: str) -> dict[str, Any]:
    """Return the current board state for a game from Redis.

    Args:
        game_id: Unique identifier of the game to retrieve.

    Returns:
        Dictionary with game_id, board, winner, and draw status.
    """
    redis = await get_redis()
    try:
        game = await load_game(redis, game_id)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Game not found or expired.")
    return {
        "game_id": game_id,
        "board": game.board,
        "winner": game.winner,
        "draw": game.is_draw,
        "winning_cells": [list(cell) for cell in game.winning_cells],
    }


@router.get("/games/{game_id}/status", response_model=GameResponse, status_code=status.HTTP_200_OK)
async def game_status(
    game_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> GameResponse:
    """Return the database status of a game.

    Used by the frontend to poll whether an opponent has joined.

    Args:
        game_id: UUID of the game.
        db: Injected database session.

    Returns:
        Game details including current status and player names.

    Raises:
        HTTPException: 404 if game not found.
    """
    game = await get_game_by_id(db, game_id)
    if game is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Game not found.")
    p1 = await get_player_by_id(db, game.player1_id)
    p2 = await get_player_by_id(db, game.player2_id) if game.player2_id else None
    return game_to_response(
        game,
        player1_name=p1.username if p1 else None,
        player2_name=p2.username if p2 else None,
    )


@router.post("/games", response_model=GameResponse, status_code=status.HTTP_201_CREATED)
async def create_new_game(body: GameCreate, db: AsyncSession = Depends(get_db)) -> GameResponse:
    """Create a new game and wait for an opponent.

    Args:
        body: Request containing the creating player's id.
        db: Injected database session.

    Returns:
        The newly created game with WAITING status.
    """
    game = await create_game(db, body.player1_id)
    return game_to_response(game)


@router.post("/games/{game_id}/join", response_model=GameResponse, status_code=status.HTTP_200_OK)
async def join_existing_game(game_id: uuid.UUID, body: GameJoin, db: AsyncSession = Depends(get_db)) -> GameResponse:
    """Join an existing game as the second player.

    Args:
        game_id: UUID of the game to join.
        body: Request containing the joining player's id.
        db: Injected database session.

    Returns:
        The updated game with PLAYING status.

    Raises:
        HTTPException: 404 if game not found or not joinable.
    """
    game = await join_game(db, game_id, body.player2_id)
    if game is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Game not found or not joinable.")
    redis = await get_redis()
    await save_game(redis, str(game.id), Connect4())
    return game_to_response(game)


@router.delete("/games/{game_id}/cancel", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_game(
    game_id: uuid.UUID,
    player_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Cancel (delete) a waiting game before an opponent joins.

    Only the creator of a WAITING game can cancel it. Removes the row entirely
    so it no longer appears in the open-games list.

    Args:
        game_id: UUID of the game to cancel.
        player_id: UUID of the player requesting cancellation (query param).
        db: Injected database session.

    Raises:
        HTTPException: 404 if game not found, already started, or requester is not the creator.
    """
    cancelled = await cancel_waiting_game(db, game_id, player_id)
    if not cancelled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Game not found, already started, or you are not the creator.",
        )


@router.get("/games/{game_id}/moves", response_model=list[MoveRecord], status_code=status.HTTP_200_OK)
async def game_moves(
    game_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> list[MoveRecord]:  # no limit — full move history needed for replay
    """Return all moves for a game in order (enables replay).

    Args:
        game_id: UUID of the game.
        db: Injected database session.

    Returns:
        Ordered list of moves.
    """
    moves = await get_game_moves(db, game_id)
    return [MoveRecord.model_validate(move) for move in moves]
