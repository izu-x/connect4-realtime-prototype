"""SQLAlchemy ORM models for persistent game data in PostgreSQL."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Final

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

DEFAULT_ELO: Final[int] = 1000


class GameStatus(enum.Enum):
    """Possible states of a game."""

    WAITING = "waiting"
    PLAYING = "playing"
    FINISHED = "finished"
    DRAW = "draw"


class PlayerModel(Base):
    """Registered player with ELO rating."""

    __tablename__ = "players"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    elo_rating: Mapped[int] = mapped_column(Integer, default=DEFAULT_ELO, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    games_as_player1: Mapped[list[GameModel]] = relationship(
        "GameModel",
        foreign_keys="GameModel.player1_id",
        back_populates="player1",
    )
    games_as_player2: Mapped[list[GameModel]] = relationship(
        "GameModel",
        foreign_keys="GameModel.player2_id",
        back_populates="player2",
    )


class GameModel(Base):
    """A single Connect 4 match between two players."""

    __tablename__ = "games"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    player1_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("players.id"),
        nullable=False,
    )
    player2_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("players.id"),
        nullable=True,
    )
    status: Mapped[GameStatus] = mapped_column(
        Enum(GameStatus, name="game_status"),
        default=GameStatus.WAITING,
        nullable=False,
    )
    winner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("players.id"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    player1: Mapped[PlayerModel] = relationship(
        "PlayerModel",
        foreign_keys=[player1_id],
        back_populates="games_as_player1",
    )
    player2: Mapped[PlayerModel | None] = relationship(
        "PlayerModel",
        foreign_keys=[player2_id],
        back_populates="games_as_player2",
    )
    moves: Mapped[list[MoveModel]] = relationship(
        "MoveModel",
        back_populates="game",
        order_by="MoveModel.move_number",
    )


class MoveModel(Base):
    """A single move in a game — enables full replay."""

    __tablename__ = "moves"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    game_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("games.id"),
        nullable=False,
        index=True,
    )
    player: Mapped[int] = mapped_column(Integer, nullable=False)
    column: Mapped[int] = mapped_column(Integer, nullable=False)
    row: Mapped[int] = mapped_column(Integer, nullable=False)
    move_number: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    game: Mapped[GameModel] = relationship("GameModel", back_populates="moves")
