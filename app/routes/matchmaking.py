"""ELO-band matchmaking endpoints — queue, poll, and leave."""

from __future__ import annotations

import json
import uuid
from typing import Any, Final

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.game import Connect4
from app.models import GameCreate
from app.repository import create_game, get_player_by_id, join_game
from app.store import get_redis, save_game

MATCHMAKING_KEY: Final[str] = "matchmaking:queue"
MATCHMAKING_RESULT_PREFIX: Final[str] = "matchmaking:result:"
MATCHMAKING_EXPIRY_PREFIX: Final[str] = "matchmaking:expiry:"
MATCHMAKING_RESULT_TTL: Final[int] = 120
MATCHMAKING_QUEUE_TTL: Final[int] = 300  # seconds before a queued player is evicted
ELO_BAND: Final[int] = 200

router: APIRouter = APIRouter(prefix="/matchmaking", tags=["matchmaking"])


@router.post("/join", status_code=status.HTTP_200_OK)
async def matchmaking_join(
    body: GameCreate,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Add a player to the matchmaking queue and attempt to find a match.

    Players are matched within an ELO band of +/- 200 points. If a match
    is found, a new game is created and both players are removed from the
    queue.

    Args:
        body: Request containing the player's id.
        db: Injected database session.

    Returns:
        Dictionary with status ("matched" or "queued") and game details if matched.

    Raises:
        HTTPException: 404 if the player is not found.
    """
    redis = await get_redis()
    player = await get_player_by_id(db, body.player1_id)
    if player is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Player not found.")

    player_elo = player.elo_rating
    player_id_str = str(body.player1_id)

    # Evict own stale entry before scanning (prevents self-match)
    await redis.zrem(MATCHMAKING_KEY, player_id_str)
    await redis.delete(f"{MATCHMAKING_EXPIRY_PREFIX}{player_id_str}")

    # Check for a match within ELO band
    candidates = await redis.zrangebyscore(
        MATCHMAKING_KEY,
        min=player_elo - ELO_BAND,
        max=player_elo + ELO_BAND,
        withscores=True,
    )

    matched_player_id: str | None = None
    for candidate_id, _score in candidates:
        cid = str(candidate_id)
        if cid == player_id_str:
            continue
        # Evict candidates whose queue TTL has expired
        expiry_key = f"{MATCHMAKING_EXPIRY_PREFIX}{cid}"
        still_valid = await redis.get(expiry_key)
        if still_valid is None:
            await redis.zrem(MATCHMAKING_KEY, cid)
            continue
        # Verify candidate still exists in the database
        candidate_player = await get_player_by_id(db, uuid.UUID(cid))
        if candidate_player is None:
            await redis.zrem(MATCHMAKING_KEY, cid)
            await redis.delete(expiry_key)
            continue
        matched_player_id = cid
        break

    if matched_player_id is not None:
        # Remove matched player from queue and its expiry sentinel
        await redis.zrem(MATCHMAKING_KEY, matched_player_id)
        await redis.delete(f"{MATCHMAKING_EXPIRY_PREFIX}{matched_player_id}")

        # Create a game with matched player as player1, joining player as player2
        game = await create_game(db, uuid.UUID(matched_player_id))
        game = await join_game(db, game.id, body.player1_id)
        if game is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create matched game.",
            )

        # Initialize empty board in Redis so the first WS move can load it
        await save_game(redis, str(game.id), Connect4())

        # Store match result for the waiting player to discover
        result_payload = json.dumps(
            {
                "game_id": str(game.id),
                "opponent_id": str(body.player1_id),
                "my_player": 1,
            }
        )
        await redis.set(
            f"{MATCHMAKING_RESULT_PREFIX}{matched_player_id}",
            result_payload,
            ex=MATCHMAKING_RESULT_TTL,
        )

        return {
            "status": "matched",
            "game_id": str(game.id),
            "opponent_id": matched_player_id,
            "my_player": 2,
        }

    # No match found — add to queue with a TTL sentinel so stale entries are evicted
    await redis.zadd(MATCHMAKING_KEY, {player_id_str: player_elo})
    await redis.set(f"{MATCHMAKING_EXPIRY_PREFIX}{player_id_str}", "1", ex=MATCHMAKING_QUEUE_TTL)
    return {"status": "queued"}


@router.get("/status/{player_id}", status_code=status.HTTP_200_OK)
async def matchmaking_status(player_id: uuid.UUID) -> dict[str, Any]:
    """Check if a player in the matchmaking queue has been matched.

    If a match was found by another player, returns the game details
    so this player can join immediately.

    Args:
        player_id: UUID of the player to check.

    Returns:
        Dictionary with status ("matched", "queued", or "not_queued").
    """
    redis = await get_redis()

    # Check if another player already matched us
    result_key = f"{MATCHMAKING_RESULT_PREFIX}{player_id}"
    match_result = await redis.get(result_key)
    if match_result is not None:
        await redis.delete(result_key)
        match_data = json.loads(match_result)
        return {
            "status": "matched",
            "game_id": match_data["game_id"],
            "opponent_id": match_data["opponent_id"],
            "my_player": match_data["my_player"],
        }

    rank = await redis.zrank(MATCHMAKING_KEY, str(player_id))
    if rank is None:
        return {"status": "not_queued", "position": 0}
    queue_size = await redis.zcard(MATCHMAKING_KEY)
    return {"status": "queued", "position": rank + 1, "queue_size": queue_size}


@router.delete("/leave/{player_id}", status_code=status.HTTP_200_OK)
async def matchmaking_leave(player_id: uuid.UUID) -> dict[str, str]:
    """Remove a player from the matchmaking queue.

    Args:
        player_id: UUID of the player to remove.

    Returns:
        Confirmation message.
    """
    redis = await get_redis()
    await redis.zrem(MATCHMAKING_KEY, str(player_id))
    await redis.delete(f"{MATCHMAKING_EXPIRY_PREFIX}{player_id}")
    await redis.delete(f"{MATCHMAKING_RESULT_PREFIX}{player_id}")
    return {"status": "left"}
