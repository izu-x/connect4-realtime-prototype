"""Create players, games, and moves tables.

Revision ID: 001
Revises:
Create Date: 2026-02-27
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision: str = "001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "players",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("username", sa.String(64), unique=True, nullable=False, index=True),
        sa.Column("elo_rating", sa.Integer, nullable=False, server_default="1000"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "games",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("player1_id", UUID(as_uuid=True), sa.ForeignKey("players.id"), nullable=False),
        sa.Column("player2_id", UUID(as_uuid=True), sa.ForeignKey("players.id"), nullable=True),
        sa.Column(
            "status",
            sa.Enum("WAITING", "PLAYING", "FINISHED", "DRAW", name="game_status"),
            nullable=False,
            server_default="WAITING",
        ),
        sa.Column("winner_id", UUID(as_uuid=True), sa.ForeignKey("players.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "moves",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("game_id", UUID(as_uuid=True), sa.ForeignKey("games.id"), nullable=False, index=True),
        sa.Column("player", sa.Integer, nullable=False),
        sa.Column("column", sa.Integer, nullable=False),
        sa.Column("row", sa.Integer, nullable=False),
        sa.Column("move_number", sa.Integer, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("moves")
    op.drop_table("games")
    op.drop_table("players")
    op.execute("DROP TYPE IF EXISTS game_status")
