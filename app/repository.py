"""Data access layer — all PostgreSQL queries live here."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import desc, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db_models import GameModel, GameStatus, MoveModel, PlayerModel

logger: logging.Logger = logging.getLogger(__name__)


async def create_player(session: AsyncSession, username: str) -> PlayerModel:
    """Register a new player.

    Args:
        session: Active database session.
        username: Unique username for the player.

    Returns:
        The newly created PlayerModel.
    """
    player = PlayerModel(username=username)
    session.add(player)
    await session.flush()
    return player


async def get_player_by_id(session: AsyncSession, player_id: uuid.UUID) -> PlayerModel | None:
    """Fetch a player by primary key.

    Args:
        session: Active database session.
        player_id: UUID of the player.

    Returns:
        PlayerModel if found, None otherwise.
    """
    return await session.get(PlayerModel, player_id)


async def get_player_by_username(session: AsyncSession, username: str) -> PlayerModel | None:
    """Fetch a player by username.

    Args:
        session: Active database session.
        username: Username to search for.

    Returns:
        PlayerModel if found, None otherwise.
    """
    result = await session.execute(select(PlayerModel).where(PlayerModel.username == username))
    return result.scalar_one_or_none()


async def create_game(session: AsyncSession, player1_id: uuid.UUID) -> GameModel:
    """Create a new game waiting for a second player.

    Args:
        session: Active database session.
        player1_id: UUID of the player creating the game.

    Returns:
        The newly created GameModel with status WAITING.
    """
    game = GameModel(player1_id=player1_id, status=GameStatus.WAITING)
    session.add(game)
    await session.flush()
    return game


async def get_game_by_id(session: AsyncSession, game_id: uuid.UUID) -> GameModel | None:
    """Fetch a game by its UUID.

    Args:
        session: Active database session.
        game_id: UUID of the game.

    Returns:
        GameModel if found, None otherwise.
    """
    return await session.get(GameModel, game_id)


async def join_game(session: AsyncSession, game_id: uuid.UUID, player2_id: uuid.UUID) -> GameModel | None:
    """Join an existing game as the second player.

    Args:
        session: Active database session.
        game_id: UUID of the game to join.
        player2_id: UUID of the joining player.

    Returns:
        Updated GameModel with status PLAYING, or None if not found/joinable.
    """
    game = await session.get(GameModel, game_id)
    if game is None or game.status != GameStatus.WAITING:
        return None
    if game.player1_id == player2_id:
        return None
    game.player2_id = player2_id
    game.status = GameStatus.PLAYING
    await session.flush()
    return game


async def record_move(
    session: AsyncSession,
    game_id: uuid.UUID,
    player: int,
    column: int,
    row: int,
    move_number: int,
) -> MoveModel:
    """Persist a single move to the database.

    Args:
        session: Active database session.
        game_id: UUID of the game.
        player: Player identifier (1 or 2).
        column: Column where the piece was dropped.
        row: Row where the piece landed.
        move_number: Sequential move counter (1-based).

    Returns:
        The newly created MoveModel.
    """
    move = MoveModel(
        game_id=game_id,
        player=player,
        column=column,
        row=row,
        move_number=move_number,
    )
    session.add(move)
    await session.flush()
    return move


async def finish_game(
    session: AsyncSession,
    game_id: uuid.UUID,
    winner_id: uuid.UUID | None,
    is_draw: bool,
) -> GameModel | None:
    """Mark a game as finished and record the winner.

    Args:
        session: Active database session.
        game_id: UUID of the game.
        winner_id: UUID of the winning player, or None for a draw.
        is_draw: Whether the game ended in a draw.

    Returns:
        Updated GameModel, or None if not found.
    """
    game = await session.get(GameModel, game_id)
    if game is None:
        return None
    game.status = GameStatus.DRAW if is_draw else GameStatus.FINISHED
    game.winner_id = winner_id
    game.finished_at = datetime.now(UTC)
    await session.flush()
    return game


async def get_leaderboard(session: AsyncSession, limit: int = 10) -> list[dict]:
    """Return top players who have played at least one game, sorted by ELO.

    Only players with at least one FINISHED or DRAW game appear.
    Ties in ELO are broken by number of games played (more games ranks higher).

    Args:
        session: Active database session.
        limit: Maximum number of players to return.

    Returns:
        List of dicts with username, elo_rating, and total_games.
    """
    games_played = (
        select(
            PlayerModel.id.label("pid"),
            func.count(GameModel.id).label("total_games"),
        )
        .outerjoin(
            GameModel,
            or_(
                GameModel.player1_id == PlayerModel.id,
                GameModel.player2_id == PlayerModel.id,
            ),
        )
        .where(
            GameModel.status.in_([GameStatus.FINISHED, GameStatus.DRAW])
            & ((GameModel.status == GameStatus.DRAW) | GameModel.winner_id.isnot(None))
        )
        .group_by(PlayerModel.id)
        .having(func.count(GameModel.id) > 0)
        .subquery()
    )

    stmt = (
        select(
            PlayerModel.username,
            PlayerModel.elo_rating,
            games_played.c.total_games,
        )
        .join(games_played, PlayerModel.id == games_played.c.pid)
        .order_by(desc(PlayerModel.elo_rating), desc(games_played.c.total_games))
        .limit(limit)
    )
    result = await session.execute(stmt)
    return [
        {"username": row.username, "elo_rating": row.elo_rating, "total_games": row.total_games} for row in result.all()
    ]


async def get_recent_games(session: AsyncSession, limit: int = 10) -> list[GameModel]:
    """Return the most recently finished games.

    Args:
        session: Active database session.
        limit: Maximum number of games to return.

    Returns:
        List of GameModel instances ordered by most recent finish time.
    """
    result = await session.execute(
        select(GameModel)
        .where(GameModel.status.in_([GameStatus.FINISHED, GameStatus.DRAW]))
        .order_by(desc(GameModel.finished_at))
        .limit(limit)
    )
    return list(result.scalars().all())


async def get_waiting_games(session: AsyncSession, limit: int = 50) -> list[GameModel]:
    """Return games waiting for a second player.

    Args:
        session: Active database session.
        limit: Maximum number of games to return.

    Returns:
        List of GameModel instances with WAITING status, newest first.
    """
    result = await session.execute(
        select(GameModel)
        .where(GameModel.status == GameStatus.WAITING)
        .order_by(desc(GameModel.created_at))
        .limit(limit)
    )
    return list(result.scalars().all())


async def get_game_moves(session: AsyncSession, game_id: uuid.UUID) -> list[MoveModel]:
    """Return all moves for a game in order.

    Ordered by ``created_at`` (server timestamp) as the primary sort key for
    chronological correctness, with ``move_number`` as a deterministic tie-break
    for moves recorded within the same millisecond. Rematches now create a new
    DB game row, so ``move_number`` no longer restarts within a single game row;
    the dual-key ordering is retained for safety and determinism.

    Args:
        session: Active database session.
        game_id: UUID of the game.

    Returns:
        List of MoveModel instances ordered by (created_at, move_number).
    """
    result = await session.execute(
        select(MoveModel).where(MoveModel.game_id == game_id).order_by(MoveModel.created_at, MoveModel.move_number)
    )
    return list(result.scalars().all())


async def get_player_games(session: AsyncSession, player_id: uuid.UUID, limit: int = 20) -> list[GameModel]:
    """Return a player's recent games.

    Args:
        session: Active database session.
        player_id: UUID of the player.
        limit: Maximum number of games to return.

    Returns:
        List of GameModel instances the player participated in.
    """
    result = await session.execute(
        select(GameModel)
        .where((GameModel.player1_id == player_id) | (GameModel.player2_id == player_id))
        .order_by(desc(GameModel.created_at))
        .limit(limit)
    )
    return list(result.scalars().all())


async def update_elo(
    session: AsyncSession,
    winner_id: uuid.UUID,
    loser_id: uuid.UUID,
) -> tuple[int, int]:
    """Update ELO ratings after a decisive game using the standard formula.

    Args:
        session: Active database session.
        winner_id: UUID of the winning player.
        loser_id: UUID of the losing player.

    Returns:
        Tuple of (new_winner_elo, new_loser_elo).
    """
    k_factor: int = 32
    winner = await session.get(PlayerModel, winner_id)
    loser = await session.get(PlayerModel, loser_id)
    if winner is None or loser is None:
        return (0, 0)

    expected_winner = 1.0 / (1.0 + 10 ** ((loser.elo_rating - winner.elo_rating) / 400))
    expected_loser = 1.0 - expected_winner

    winner.elo_rating = max(0, round(winner.elo_rating + k_factor * (1 - expected_winner)))
    loser.elo_rating = max(0, round(loser.elo_rating + k_factor * (0 - expected_loser)))
    await session.flush()
    return (winner.elo_rating, loser.elo_rating)


async def update_elo_draw(
    session: AsyncSession,
    player1_id: uuid.UUID,
    player2_id: uuid.UUID,
) -> tuple[int, int]:
    """Update ELO ratings after a draw.

    Args:
        session: Active database session.
        player1_id: UUID of the first player.
        player2_id: UUID of the second player.

    Returns:
        Tuple of (new_player1_elo, new_player2_elo).
    """
    k_factor: int = 32
    player1 = await session.get(PlayerModel, player1_id)
    player2 = await session.get(PlayerModel, player2_id)
    if player1 is None or player2 is None:
        return (0, 0)

    expected_p1 = 1.0 / (1.0 + 10 ** ((player2.elo_rating - player1.elo_rating) / 400))
    expected_p2 = 1.0 - expected_p1

    player1.elo_rating = max(0, round(player1.elo_rating + k_factor * (0.5 - expected_p1)))
    player2.elo_rating = max(0, round(player2.elo_rating + k_factor * (0.5 - expected_p2)))
    await session.flush()
    return (player1.elo_rating, player2.elo_rating)


async def get_active_game(session: AsyncSession, player_id: uuid.UUID) -> GameModel | None:
    """Return the currently active game for a player, if any.

    Looks for games in PLAYING or WAITING status so a player can rejoin
    both mid-game and lobby-waiting scenarios.

    Args:
        session: Active database session.
        player_id: UUID of the player.

    Returns:
        GameModel with PLAYING or WAITING status, or None if the player has no active game.
    """
    result = await session.execute(
        select(GameModel)
        .where(
            ((GameModel.player1_id == player_id) | (GameModel.player2_id == player_id))
            & (GameModel.status.in_([GameStatus.PLAYING, GameStatus.WAITING]))
        )
        .order_by(desc(GameModel.created_at))
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_player_stats(session: AsyncSession, player_id: uuid.UUID) -> dict:
    """Compute aggregate statistics for a player.

    Args:
        session: Active database session.
        player_id: UUID of the player.

    Returns:
        Dictionary with wins, losses, draws, total_games, win_rate,
        avg_game_duration_seconds, and current_streak.
    """
    result = await session.execute(
        select(GameModel)
        .where(
            ((GameModel.player1_id == player_id) | (GameModel.player2_id == player_id))
            & GameModel.status.in_([GameStatus.FINISHED, GameStatus.DRAW])
        )
        .order_by(desc(GameModel.finished_at))
    )
    games = list(result.scalars().all())

    wins: int = 0
    losses: int = 0
    draws: int = 0
    total_duration_seconds: float = 0
    games_with_duration: int = 0
    current_streak: int = 0
    streak_type: str = ""
    streak_decided: bool = False

    for game in games:
        if game.status == GameStatus.DRAW:
            draws += 1
            outcome = "draw"
        elif game.winner_id == player_id:
            wins += 1
            outcome = "win"
        elif game.status == GameStatus.FINISHED and game.winner_id is None:
            # Abandoned game (server-restart cleanup) — skip entirely from all stats
            continue
        else:
            losses += 1
            outcome = "loss"

        # Duration must be accumulated for every game, regardless of streak.
        if game.created_at and game.finished_at:
            duration = (game.finished_at - game.created_at).total_seconds()
            if duration > 0:
                total_duration_seconds += duration
                games_with_duration += 1

        # Streak calculation (most recent games first).
        # Once the streak breaks we stop extending it but keep looping so that
        # the counts and duration above are correct for the full history.
        if not streak_decided:
            if not streak_type:
                streak_type = outcome
                current_streak = 1
            elif outcome == streak_type:
                current_streak += 1
            else:
                streak_decided = True

    total_games = wins + losses + draws
    win_rate = round(wins / total_games * 100, 1) if total_games > 0 else 0
    avg_duration = round(total_duration_seconds / games_with_duration) if games_with_duration > 0 else 0

    return {
        "wins": wins,
        "losses": losses,
        "draws": draws,
        "total_games": total_games,
        "win_rate": win_rate,
        "avg_game_duration_seconds": avg_duration,
        "current_streak": current_streak,
        "streak_type": streak_type if streak_type else "none",
    }


async def cancel_waiting_game(session: AsyncSession, game_id: uuid.UUID, player_id: uuid.UUID) -> bool:
    """Delete a waiting game if it was created by the given player and has no opponent yet.

    Args:
        session: Active database session.
        game_id: UUID of the game to cancel.
        player_id: UUID of the player requesting cancellation (must be the creator).

    Returns:
        True if the game was deleted, False if not found, already started, or wrong player.
    """
    game = await session.get(GameModel, game_id)
    if game is None or game.status != GameStatus.WAITING:
        return False
    if game.player1_id != player_id:
        return False
    await session.delete(game)
    await session.flush()
    return True


async def cleanup_stale_games(session: AsyncSession) -> int:
    """Mark orphaned WAITING/PLAYING games as FINISHED on startup.

    When the server restarts, any games still in WAITING or PLAYING status
    have no active WebSocket connections and are effectively dead.
    This prevents zombie games from inflating stats or blocking players.

    Args:
        session: Active database session.

    Returns:
        Number of stale games cleaned up.
    """
    result = await session.execute(
        update(GameModel)
        .where(GameModel.status.in_([GameStatus.WAITING, GameStatus.PLAYING]))
        .values(status=GameStatus.FINISHED, finished_at=datetime.now(UTC))
    )
    cleaned: int = result.rowcount  # type: ignore[assignment]
    if cleaned > 0:
        logger.info("Cleaned up %d stale game(s) from previous session", cleaned)
    return cleaned
