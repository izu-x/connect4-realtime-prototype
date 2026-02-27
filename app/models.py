"""Pydantic models for request/response validation."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Final

from pydantic import BaseModel, Field, field_validator

if TYPE_CHECKING:
    from app.db_models import GameModel

ROWS: Final[int] = 6
COLS: Final[int] = 7


# ---------------------------------------------------------------------------
# Move schemas (existing)
# ---------------------------------------------------------------------------


class MoveRequest(BaseModel):
    """Represents a player's move: drop a piece into a column."""

    game_id: str = Field(..., min_length=1, max_length=64)
    player: int = Field(..., ge=1, le=2)
    column: int = Field(..., ge=0, lt=COLS)

    @field_validator("game_id")
    @classmethod
    def game_id_alphanumeric(cls, v: str) -> str:
        if not v.replace("-", "").replace("_", "").isalnum():
            raise ValueError("game_id must be alphanumeric (hyphens and underscores allowed)")
        return v


class MoveResponse(BaseModel):
    """Result of processing a move."""

    game_id: str
    player: int
    column: int
    row: int
    winner: int | None = None
    draw: bool = False
    board: list[list[int]]
    winning_cells: list[list[int]] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Game schemas
# ---------------------------------------------------------------------------


class GameCreate(BaseModel):
    """Request body to create a new game."""

    player1_id: uuid.UUID


class GameJoin(BaseModel):
    """Request body to join an existing game."""

    player2_id: uuid.UUID


class GameResponse(BaseModel):
    """Public representation of a game."""

    model_config = {"from_attributes": True}

    id: uuid.UUID
    player1_id: uuid.UUID
    player2_id: uuid.UUID | None = None
    status: str
    winner_id: uuid.UUID | None = None
    player1_name: str | None = None
    player2_name: str | None = None


# ---------------------------------------------------------------------------
# Player schemas
# ---------------------------------------------------------------------------


class PlayerCreate(BaseModel):
    """Request body to register a new player."""

    username: str = Field(..., min_length=1, max_length=64)

    @field_validator("username")
    @classmethod
    def username_not_blank(cls, v: str) -> str:
        """Strip surrounding whitespace and reject usernames that are entirely whitespace."""
        stripped = v.strip()
        if not stripped:
            raise ValueError("Username cannot be blank or whitespace-only.")
        return stripped


class PlayerResponse(BaseModel):
    """Public representation of a player."""

    model_config = {"from_attributes": True}

    id: uuid.UUID
    username: str
    elo_rating: int
    is_returning: bool = False
    games: list[GameResponse] = Field(default_factory=list)


class MoveRecord(BaseModel):
    """A single recorded move for replay / history."""

    model_config = {"from_attributes": True}

    player: int
    column: int
    row: int
    move_number: int


# ---------------------------------------------------------------------------
# Leaderboard / history
# ---------------------------------------------------------------------------


class LeaderboardEntry(BaseModel):
    """A single row in the leaderboard."""

    model_config = {"from_attributes": True}

    username: str
    elo_rating: int
    total_games: int = 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def game_to_response(
    game: GameModel,
    *,
    player1_name: str | None = None,
    player2_name: str | None = None,
) -> GameResponse:
    """Build a ``GameResponse`` from an ORM ``GameModel``.

    Args:
        game: SQLAlchemy GameModel instance.
        player1_name: Optional display name for player 1.
        player2_name: Optional display name for player 2.

    Returns:
        Populated GameResponse.
    """
    return GameResponse(
        id=game.id,
        player1_id=game.player1_id,
        player2_id=game.player2_id,
        status=game.status.value,
        winner_id=game.winner_id,
        player1_name=player1_name,
        player2_name=player2_name,
    )
