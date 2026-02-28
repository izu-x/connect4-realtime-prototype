"""Tests for ELO rating updates and player stats endpoint."""

from __future__ import annotations

import uuid
from datetime import UTC
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest  # noqa: I001

# ---------------------------------------------------------------------------
# ELO update functions (unit tests — no HTTP needed)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_update_elo_winner_gains_loser_drops() -> None:
    """After a decisive game, winner should gain ELO and loser should lose ELO."""
    from app.repository import update_elo

    winner_id = uuid.uuid4()
    loser_id = uuid.uuid4()

    class FakePlayer:
        def __init__(self, pid: uuid.UUID, elo: int) -> None:
            self.id = pid
            self.elo_rating = elo

    winner = FakePlayer(winner_id, 1000)
    loser = FakePlayer(loser_id, 1000)

    session = AsyncMock()
    session.get = AsyncMock(side_effect=lambda model, pid: winner if pid == winner_id else loser)
    session.flush = AsyncMock()

    new_winner_elo, new_loser_elo = await update_elo(session, winner_id, loser_id)

    assert new_winner_elo > 1000, "Winner should gain ELO"
    assert new_loser_elo < 1000, "Loser should lose ELO"
    assert new_winner_elo + new_loser_elo == 2000, "ELO should be zero-sum for equal opponents"


@pytest.mark.anyio
async def test_update_elo_underdog_wins_more() -> None:
    """If a lower-rated player beats a higher-rated one, they should gain more ELO."""
    from app.repository import update_elo

    underdog_id = uuid.uuid4()
    favourite_id = uuid.uuid4()

    class FakePlayer:
        def __init__(self, pid: uuid.UUID, elo: int) -> None:
            self.id = pid
            self.elo_rating = elo

    underdog = FakePlayer(underdog_id, 800)
    favourite = FakePlayer(favourite_id, 1200)

    session = AsyncMock()
    session.get = AsyncMock(side_effect=lambda model, pid: underdog if pid == underdog_id else favourite)
    session.flush = AsyncMock()

    new_underdog_elo, new_fav_elo = await update_elo(session, underdog_id, favourite_id)

    gain = new_underdog_elo - 800
    assert gain > 20, f"Underdog should gain significantly, got {gain}"


@pytest.mark.anyio
async def test_update_elo_draw_equal_players() -> None:
    """Draw between equal-rated players should change nothing."""
    from app.repository import update_elo_draw

    p1_id = uuid.uuid4()
    p2_id = uuid.uuid4()

    class FakePlayer:
        def __init__(self, pid: uuid.UUID, elo: int) -> None:
            self.id = pid
            self.elo_rating = elo

    player1 = FakePlayer(p1_id, 1000)
    player2 = FakePlayer(p2_id, 1000)

    session = AsyncMock()
    session.get = AsyncMock(side_effect=lambda model, pid: player1 if pid == p1_id else player2)
    session.flush = AsyncMock()

    new_p1, new_p2 = await update_elo_draw(session, p1_id, p2_id)

    assert new_p1 == 1000, "Equal draw should not change ELO"
    assert new_p2 == 1000, "Equal draw should not change ELO"


@pytest.mark.anyio
async def test_update_elo_draw_different_ratings() -> None:
    """Draw between differently-rated players should shift ELO toward the weaker player."""
    from app.repository import update_elo_draw

    p1_id = uuid.uuid4()
    p2_id = uuid.uuid4()

    class FakePlayer:
        def __init__(self, pid: uuid.UUID, elo: int) -> None:
            self.id = pid
            self.elo_rating = elo

    stronger = FakePlayer(p1_id, 1400)
    weaker = FakePlayer(p2_id, 1000)

    session = AsyncMock()
    session.get = AsyncMock(side_effect=lambda model, pid: stronger if pid == p1_id else weaker)
    session.flush = AsyncMock()

    new_strong, new_weak = await update_elo_draw(session, p1_id, p2_id)

    assert new_strong < 1400, "Stronger player should lose ELO on draw"
    assert new_weak > 1000, "Weaker player should gain ELO on draw"


# ---------------------------------------------------------------------------
# Player stats endpoint
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_player_stats_endpoint(client) -> None:
    """GET /players/{id}/stats should return aggregate statistics."""
    player_id = uuid.uuid4()
    mock_player = SimpleNamespace(id=player_id, username="alice", elo_rating=1050)
    mock_stats = {
        "wins": 5,
        "losses": 3,
        "draws": 1,
        "total_games": 9,
        "win_rate": 55.6,
        "avg_game_duration_seconds": 180,
        "current_streak": 2,
        "streak_type": "win",
    }

    with (
        patch("app.routes.players.get_player_by_id", new=AsyncMock(return_value=mock_player)),
        patch("app.routes.players.get_player_stats", new=AsyncMock(return_value=mock_stats)),
    ):
        resp = await client.get(f"/players/{player_id}/stats")

    assert resp.status_code == 200
    body = resp.json()
    assert body["wins"] == 5
    assert body["losses"] == 3
    assert body["draws"] == 1
    assert body["total_games"] == 9
    assert body["win_rate"] == 55.6
    assert body["elo_rating"] == 1050
    assert body["username"] == "alice"
    assert body["current_streak"] == 2
    assert body["streak_type"] == "win"


# ---------------------------------------------------------------------------
# ELO edge cases: missing players
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_update_elo_missing_winner() -> None:
    """update_elo with a missing winner should return (0, 0)."""
    from app.repository import update_elo

    session = AsyncMock()
    session.get = AsyncMock(return_value=None)

    result = await update_elo(session, uuid.uuid4(), uuid.uuid4())
    assert result == (0, 0)


@pytest.mark.anyio
async def test_update_elo_draw_missing_player() -> None:
    """update_elo_draw with a missing player should return (0, 0)."""
    from app.repository import update_elo_draw

    session = AsyncMock()
    session.get = AsyncMock(return_value=None)

    result = await update_elo_draw(session, uuid.uuid4(), uuid.uuid4())
    assert result == (0, 0)


@pytest.mark.anyio
async def test_update_elo_zero_sum() -> None:
    """ELO updates should always be zero-sum for equal-rated opponents."""
    from app.repository import update_elo

    w_id = uuid.uuid4()
    l_id = uuid.uuid4()

    class FakePlayer:
        def __init__(self, pid: uuid.UUID, elo: int) -> None:
            self.id = pid
            self.elo_rating = elo

    winner = FakePlayer(w_id, 1200)
    loser = FakePlayer(l_id, 1200)

    session = AsyncMock()
    session.get = AsyncMock(side_effect=lambda model, pid: winner if pid == w_id else loser)
    session.flush = AsyncMock()

    new_w, new_l = await update_elo(session, w_id, l_id)
    assert new_w + new_l == 2400, "ELO should be zero-sum"


# ---------------------------------------------------------------------------
# Streak calculation logic
# ---------------------------------------------------------------------------


def _simulate_streak(outcomes: list[str]) -> tuple[int, str]:
    """Replicate the streak logic from get_player_stats.

    Args:
        outcomes: List of outcomes in most-recent-first order
                  ("win", "loss", or "draw").

    Returns:
        Tuple of (current_streak, streak_type).
    """
    current_streak = 0
    streak_type = ""
    for outcome in outcomes:
        if not streak_type:
            streak_type = outcome
            current_streak = 1
        elif outcome == streak_type:
            current_streak += 1
        else:
            break
    return current_streak, streak_type


def test_streak_counts_consecutive_wins_only() -> None:
    """Given W-W-W-L-W-W (most recent first), streak should be 3."""
    streak, stype = _simulate_streak(["win", "win", "win", "loss", "win", "win"])
    assert streak == 3
    assert stype == "win"


def test_streak_zero_after_loss() -> None:
    """If the most recent game is a loss, streak should be 1 of type loss."""
    streak, stype = _simulate_streak(["loss", "win", "win", "win"])
    assert streak == 1
    assert stype == "loss"


def test_streak_all_wins() -> None:
    streak, stype = _simulate_streak(["win", "win", "win", "win", "win"])
    assert streak == 5
    assert stype == "win"


def test_streak_empty() -> None:
    streak, stype = _simulate_streak([])
    assert streak == 0
    assert stype == ""


def test_streak_draw_then_win() -> None:
    streak, stype = _simulate_streak(["draw", "win", "win"])
    assert streak == 1
    assert stype == "draw"


# ---------------------------------------------------------------------------
# ELO edge cases (continued)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_update_elo_favourite_wins_less() -> None:
    """A heavily favoured player winning should gain less ELO."""
    from app.repository import update_elo

    fav_id = uuid.uuid4()
    under_id = uuid.uuid4()

    class FakePlayer:
        def __init__(self, pid: uuid.UUID, elo: int) -> None:
            self.id = pid
            self.elo_rating = elo

    fav = FakePlayer(fav_id, 1600)
    underdog = FakePlayer(under_id, 800)

    session = AsyncMock()
    session.get = AsyncMock(side_effect=lambda model, pid: fav if pid == fav_id else underdog)
    session.flush = AsyncMock()

    new_fav, new_under = await update_elo(session, fav_id, under_id)
    gain = new_fav - 1600
    assert gain < 10, f"Favourite should gain very little, got {gain}"


# ---------------------------------------------------------------------------
# ELO floor: rating must never go negative
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_elo_never_goes_negative_loser() -> None:
    """A player already near 0 ELO should not drop below 0 after a loss."""
    from app.repository import update_elo

    winner_id = uuid.uuid4()
    loser_id = uuid.uuid4()

    class FakePlayer:
        def __init__(self, pid: uuid.UUID, elo: int) -> None:
            self.id = pid
            self.elo_rating = elo

    winner = FakePlayer(winner_id, 2000)  # big favourite
    loser = FakePlayer(loser_id, 1)  # almost 0 already

    session = AsyncMock()
    session.get = AsyncMock(side_effect=lambda model, pid: winner if pid == winner_id else loser)
    session.flush = AsyncMock()

    new_winner_elo, new_loser_elo = await update_elo(session, winner_id, loser_id)

    assert new_loser_elo >= 0, "Loser ELO must never be negative"


@pytest.mark.anyio
async def test_elo_draw_never_goes_negative() -> None:
    """update_elo_draw must also respect the 0-floor for the weaker player."""
    from app.repository import update_elo_draw

    p1_id = uuid.uuid4()
    p2_id = uuid.uuid4()

    class FakePlayer:
        def __init__(self, pid: uuid.UUID, elo: int) -> None:
            self.id = pid
            self.elo_rating = elo

    p1 = FakePlayer(p1_id, 2000)
    p2 = FakePlayer(p2_id, 1)

    session = AsyncMock()
    session.get = AsyncMock(side_effect=lambda model, pid: p1 if pid == p1_id else p2)
    session.flush = AsyncMock()

    new_p1_elo, new_p2_elo = await update_elo_draw(session, p1_id, p2_id)

    assert new_p2_elo >= 0, "Draw ELO result must never be negative"


# ---------------------------------------------------------------------------
# Abandoned games (FINISHED + winner_id=None) must be excluded
# from win/loss/draw counts, streak, and avg duration
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_stats_abandoned_game_not_counted_as_loss() -> None:
    """A game marked FINISHED with no winner should not inflate the loss count."""
    from datetime import datetime, timedelta

    from app.db_models import GameStatus
    from app.repository import get_player_stats

    player_id = uuid.uuid4()
    opp_id = uuid.uuid4()

    # One real win + one abandoned game (server-restart cleanup artifact)
    won_game = SimpleNamespace(
        id=uuid.uuid4(),
        player1_id=player_id,
        player2_id=opp_id,
        status=GameStatus.FINISHED,
        winner_id=player_id,
        created_at=datetime.now(UTC) - timedelta(minutes=10),
        finished_at=datetime.now(UTC) - timedelta(minutes=5),
    )
    abandoned_game = SimpleNamespace(
        id=uuid.uuid4(),
        player1_id=player_id,
        player2_id=opp_id,
        status=GameStatus.FINISHED,
        winner_id=None,  # cleanup sets no winner
        created_at=datetime.now(UTC) - timedelta(hours=2),
        finished_at=datetime.now(UTC) - timedelta(hours=1, minutes=55),
    )

    from unittest.mock import MagicMock

    scalars_mock = MagicMock()
    scalars_mock.scalars.return_value.all.return_value = [won_game, abandoned_game]
    session = AsyncMock()
    session.execute = AsyncMock(return_value=scalars_mock)

    stats = await get_player_stats(session, player_id)

    assert stats["wins"] == 1, "Should count the real win"
    assert stats["losses"] == 0, "Abandoned game must NOT count as a loss"
    assert stats["total_games"] == 1, "Abandoned game must NOT appear in total_games"


@pytest.mark.anyio
async def test_stats_abandoned_game_not_counted_as_loss_only_abandoned() -> None:
    """A player with only abandoned games should show zero stats."""
    from datetime import datetime, timedelta

    from app.db_models import GameStatus
    from app.repository import get_player_stats

    player_id = uuid.uuid4()

    abandoned = SimpleNamespace(
        id=uuid.uuid4(),
        player1_id=player_id,
        player2_id=uuid.uuid4(),
        status=GameStatus.FINISHED,
        winner_id=None,
        created_at=datetime.now(UTC) - timedelta(hours=1),
        finished_at=datetime.now(UTC) - timedelta(minutes=55),
    )

    from unittest.mock import MagicMock

    scalars_mock = MagicMock()
    scalars_mock.scalars.return_value.all.return_value = [abandoned]
    session = AsyncMock()
    session.execute = AsyncMock(return_value=scalars_mock)

    stats = await get_player_stats(session, player_id)

    assert stats["total_games"] == 0
    assert stats["wins"] == 0
    assert stats["losses"] == 0
    assert stats["draws"] == 0
    assert stats["win_rate"] == 0


@pytest.mark.anyio
async def test_stats_abandoned_game_does_not_break_win_streak() -> None:
    """An abandoned game must not interrupt a player's current win streak."""
    from datetime import datetime, timedelta

    from app.db_models import GameStatus
    from app.repository import get_player_stats

    player_id = uuid.uuid4()
    opp_id = uuid.uuid4()
    now = datetime.now(UTC)

    def make_win(minutes_ago: int) -> SimpleNamespace:
        return SimpleNamespace(
            id=uuid.uuid4(),
            player1_id=player_id,
            player2_id=opp_id,
            status=GameStatus.FINISHED,
            winner_id=player_id,
            created_at=now - timedelta(minutes=minutes_ago + 5),
            finished_at=now - timedelta(minutes=minutes_ago),
        )

    # Ordering: most-recent first (matches ORDER BY finished_at DESC in repository)
    wins = [make_win(i * 10) for i in range(3)]  # 3 wins
    abandoned = SimpleNamespace(
        id=uuid.uuid4(),
        player1_id=player_id,
        player2_id=opp_id,
        status=GameStatus.FINISHED,
        winner_id=None,
        created_at=now - timedelta(hours=5),
        finished_at=now - timedelta(hours=4, minutes=55),
    )
    games = wins + [abandoned]

    from unittest.mock import MagicMock

    scalars_mock = MagicMock()
    scalars_mock.scalars.return_value.all.return_value = games
    session = AsyncMock()
    session.execute = AsyncMock(return_value=scalars_mock)

    stats = await get_player_stats(session, player_id)

    assert stats["current_streak"] == 3, "Win streak must not be broken by abandoned game"
    assert stats["streak_type"] == "win"
