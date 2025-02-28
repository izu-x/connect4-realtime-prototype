"""Tests for Pydantic request/response model validation."""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from app.models import (
    GameCreate,
    GameJoin,
    LeaderboardEntry,
    MoveRecord,
    MoveRequest,
    MoveResponse,
    PlayerCreate,
    PlayerResponse,
)

# ---------------------------------------------------------------------------
# MoveRequest validation
# ---------------------------------------------------------------------------


def test_move_request_valid() -> None:
    """A well-formed MoveRequest should parse without error."""
    req = MoveRequest(game_id="game-1", player=1, column=3)
    assert req.game_id == "game-1"
    assert req.player == 1
    assert req.column == 3


def test_move_request_game_id_special_chars() -> None:
    """game_id with non-alphanumeric chars (except hyphen/underscore) should fail."""
    with pytest.raises(ValidationError, match="alphanumeric"):
        MoveRequest(game_id="ga me!", player=1, column=3)


def test_move_request_game_id_path_traversal() -> None:
    """game_id with path traversal should fail."""
    with pytest.raises(ValidationError, match="alphanumeric"):
        MoveRequest(game_id="../etc", player=1, column=3)


def test_move_request_game_id_with_hyphens_and_underscores() -> None:
    """game_id with hyphens and underscores should be valid."""
    req = MoveRequest(game_id="game_1-abc", player=1, column=3)
    assert req.game_id == "game_1-abc"


def test_move_request_game_id_empty() -> None:
    """Empty game_id should fail min_length=1."""
    with pytest.raises(ValidationError):
        MoveRequest(game_id="", player=1, column=3)


def test_move_request_player_zero() -> None:
    """player=0 should fail (ge=1)."""
    with pytest.raises(ValidationError):
        MoveRequest(game_id="game1", player=0, column=3)


def test_move_request_player_three() -> None:
    """player=3 should fail (le=2)."""
    with pytest.raises(ValidationError):
        MoveRequest(game_id="game1", player=3, column=3)


def test_move_request_column_negative() -> None:
    """column=-1 should fail (ge=0)."""
    with pytest.raises(ValidationError):
        MoveRequest(game_id="game1", player=1, column=-1)


def test_move_request_column_too_large() -> None:
    """column=7 should fail (lt=COLS=7)."""
    with pytest.raises(ValidationError):
        MoveRequest(game_id="game1", player=1, column=7)


def test_move_request_column_boundary_valid() -> None:
    """column=0 and column=6 should both be valid."""
    req0 = MoveRequest(game_id="game1", player=1, column=0)
    req6 = MoveRequest(game_id="game1", player=1, column=6)
    assert req0.column == 0
    assert req6.column == 6


# ---------------------------------------------------------------------------
# PlayerCreate validation
# ---------------------------------------------------------------------------


def test_player_create_valid() -> None:
    """A valid username should parse without error."""
    pc = PlayerCreate(username="alice")
    assert pc.username == "alice"


def test_player_create_empty_username() -> None:
    """Empty username should fail min_length=1."""
    with pytest.raises(ValidationError):
        PlayerCreate(username="")


def test_player_create_too_long_username() -> None:
    """Username longer than 64 chars should fail."""
    with pytest.raises(ValidationError):
        PlayerCreate(username="x" * 65)


def test_player_create_max_length_valid() -> None:
    """Username of exactly 64 chars should be valid."""
    pc = PlayerCreate(username="x" * 64)
    assert len(pc.username) == 64


def test_player_create_strips_leading_trailing_whitespace() -> None:
    """Leading/trailing whitespace must be stripped from usernames."""
    pc = PlayerCreate(username="  alice  ")
    assert pc.username == "alice"


def test_player_create_rejects_whitespace_only_username() -> None:
    """A username consisting only of spaces must raise a validation error."""
    with pytest.raises(ValidationError, match="blank or whitespace"):
        PlayerCreate(username="   ")


# ---------------------------------------------------------------------------
# GameCreate / GameJoin validation
# ---------------------------------------------------------------------------


def test_game_create_valid_uuid() -> None:
    """GameCreate with a valid UUID should parse."""
    pid = uuid.uuid4()
    gc = GameCreate(player1_id=pid)
    assert gc.player1_id == pid


def test_game_create_invalid_uuid() -> None:
    """GameCreate with an invalid UUID string should fail."""
    with pytest.raises(ValidationError):
        GameCreate(player1_id="not-a-uuid")


def test_game_join_valid_uuid() -> None:
    """GameJoin with a valid UUID should parse."""
    pid = uuid.uuid4()
    gj = GameJoin(player2_id=pid)
    assert gj.player2_id == pid


def test_game_join_invalid_uuid() -> None:
    """GameJoin with an invalid UUID string should fail."""
    with pytest.raises(ValidationError):
        GameJoin(player2_id="not-a-uuid")


# ---------------------------------------------------------------------------
# MoveResponse
# ---------------------------------------------------------------------------


def test_move_response_serialization() -> None:
    """MoveResponse should serialize all fields."""
    resp = MoveResponse(
        game_id="g1",
        player=1,
        column=3,
        row=5,
        winner=None,
        draw=False,
        board=[[0] * 7 for _ in range(6)],
        winning_cells=[],
    )
    assert resp.game_id == "g1"
    assert resp.winner is None


def test_move_response_with_winner() -> None:
    """MoveResponse with a winner should serialize correctly."""
    resp = MoveResponse(
        game_id="g1",
        player=1,
        column=3,
        row=5,
        winner=1,
        draw=False,
        board=[[0] * 7 for _ in range(6)],
        winning_cells=[[5, 0], [5, 1], [5, 2], [5, 3]],
    )
    assert resp.winner == 1
    assert len(resp.winning_cells) == 4


# ---------------------------------------------------------------------------
# LeaderboardEntry / MoveRecord
# ---------------------------------------------------------------------------


def test_leaderboard_entry() -> None:
    """LeaderboardEntry should hold username and elo."""
    entry = LeaderboardEntry(username="alice", elo_rating=1200)
    assert entry.username == "alice"
    assert entry.elo_rating == 1200


def test_move_record() -> None:
    """MoveRecord should hold move data."""
    record = MoveRecord(player=1, column=3, row=5, move_number=1)
    assert record.player == 1
    assert record.move_number == 1


# ---------------------------------------------------------------------------
# PlayerResponse
# ---------------------------------------------------------------------------


def test_player_response() -> None:
    """PlayerResponse should hold all player fields."""
    pid = uuid.uuid4()
    resp = PlayerResponse(id=pid, username="alice", elo_rating=1000, is_returning=False, games=[])
    assert resp.id == pid
    assert resp.is_returning is False
    assert resp.games == []
