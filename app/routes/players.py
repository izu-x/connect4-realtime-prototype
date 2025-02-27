"""Player registration, stats, leaderboard, and game history endpoints."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.connection_manager import manager
from app.database import get_db
from app.models import (
    GameResponse,
    LeaderboardEntry,
    PlayerCreate,
    PlayerResponse,
    game_to_response,
)
from app.repository import (
    create_player,
    get_active_game,
    get_leaderboard,
    get_player_by_id,
    get_player_by_username,
    get_player_games,
    get_player_stats,
)

router: APIRouter = APIRouter(tags=["players"])


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


@router.post("/players", response_model=PlayerResponse, status_code=status.HTTP_201_CREATED)
async def register_player(body: PlayerCreate, db: AsyncSession = Depends(get_db)) -> PlayerResponse:
    """Register a new player, or return an existing one with their game history.

    Args:
        body: Request containing the desired username.
        db: Injected database session.

    Returns:
        The player (new or existing). Returning players include their game history.
    """
    existing = await get_player_by_username(db, body.username)
    if existing is not None:
        games = await get_player_games(db, existing.id, limit=50)
        game_responses = [game_to_response(game) for game in games]
        return PlayerResponse(
            id=existing.id,
            username=existing.username,
            elo_rating=existing.elo_rating,
            is_returning=True,
            games=game_responses,
        )
    player = await create_player(db, body.username)
    return PlayerResponse.model_validate(player)


# ---------------------------------------------------------------------------
# Stats & history
# ---------------------------------------------------------------------------


@router.get("/players/{player_id}/stats", status_code=status.HTTP_200_OK)
async def player_stats(player_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Return aggregate statistics for a player.

    Args:
        player_id: UUID of the player.
        db: Injected database session.

    Returns:
        Dictionary with wins, losses, draws, win_rate, avg_game_duration, streak.
    """
    player = await get_player_by_id(db, player_id)
    if player is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Player not found.")
    stats = await get_player_stats(db, player_id)
    stats["elo_rating"] = player.elo_rating
    stats["username"] = player.username
    return stats


@router.get("/players/{player_id}/active-game", status_code=status.HTTP_200_OK)
async def active_game(
    player_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Return the player's currently in-progress game, if any.

    Only returns games that have live WebSocket connections.
    Games in the DB with WAITING/PLAYING status but no active connections
    are stale leftovers and are ignored.

    Args:
        player_id: UUID of the player.
        db: Injected database session.

    Returns:
        Dictionary with game details and the player's role, or null game.
    """
    game = await get_active_game(db, player_id)
    if game is None:
        return {"game": None}

    # Only report a game as active if it has live WebSocket connections
    game_id_str = str(game.id)
    live_connections = manager._rooms.get(game_id_str, [])
    if not live_connections:
        return {"game": None}

    my_player = 1 if game.player1_id == player_id else 2
    return {
        "game": {
            "id": game_id_str,
            "player1_id": str(game.player1_id),
            "player2_id": str(game.player2_id) if game.player2_id else None,
            "status": game.status.value,
            "my_player": my_player,
        },
    }


@router.get("/players/{player_id}/games", response_model=list[GameResponse], status_code=status.HTTP_200_OK)
async def player_games(
    player_id: uuid.UUID, limit: int = Query(default=20, ge=1, le=100), db: AsyncSession = Depends(get_db)
) -> list[GameResponse]:
    """Return a player's recent games.

    Args:
        player_id: UUID of the player.
        limit: Maximum number of games (default 20).
        db: Injected database session.

    Returns:
        List of games the player participated in.
    """
    games = await get_player_games(db, player_id, limit=limit)
    return [game_to_response(game) for game in games]


# ---------------------------------------------------------------------------
# Leaderboard
# ---------------------------------------------------------------------------


@router.get("/leaderboard", response_model=list[LeaderboardEntry], status_code=status.HTTP_200_OK)
async def leaderboard(
    limit: int = Query(default=10, ge=1, le=100), db: AsyncSession = Depends(get_db)
) -> list[LeaderboardEntry]:
    """Return the top players by ELO rating.

    Args:
        limit: Maximum number of entries (default 10).
        db: Injected database session.

    Returns:
        Ordered list of leaderboard entries.
    """
    rows = await get_leaderboard(db, limit=limit)
    return [LeaderboardEntry.model_validate(row) for row in rows]
