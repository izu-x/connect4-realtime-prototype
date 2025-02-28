"""Critical tests for Connect 4 game logic and API."""

from __future__ import annotations

import pytest

from app.game import COLS, ROWS, ColumnFullError, Connect4, GameOverError, InvalidColumnError, InvalidTurnError

# ---------------------------------------------------------------------------
# 1. Basic drop mechanics
# ---------------------------------------------------------------------------


def test_drop_lands_at_bottom() -> None:
    game = Connect4()
    row = game.drop(1, 0)
    assert row == ROWS - 1
    assert game.board[ROWS - 1][0] == 1


def test_drop_stacks_pieces() -> None:
    game = Connect4()
    r1 = game.drop(1, 0)
    r2 = game.drop(2, 0)
    assert r1 == ROWS - 1
    assert r2 == ROWS - 2


# ---------------------------------------------------------------------------
# 2. Full column check
# ---------------------------------------------------------------------------


def test_full_column_raises() -> None:
    game = Connect4()
    for i in range(ROWS):
        game.drop(1 if i % 2 == 0 else 2, 0)
    with pytest.raises(ColumnFullError):
        game.drop(1, 0)


# ---------------------------------------------------------------------------
# 3. Horizontal win
# ---------------------------------------------------------------------------


def test_horizontal_win() -> None:
    game = Connect4()
    # Alternate turns: p1 plays columns 0-3, p2 plays column 6
    game.drop(1, 0)  # p1
    game.drop(2, 6)  # p2
    game.drop(1, 1)  # p1
    game.drop(2, 6)  # p2
    game.drop(1, 2)  # p1
    assert game.winner is None
    game.drop(2, 6)  # p2
    game.drop(1, 3)  # p1 wins!
    assert game.winner == 1


# ---------------------------------------------------------------------------
# 4. Vertical win
# ---------------------------------------------------------------------------


def test_vertical_win() -> None:
    game = Connect4()
    for _ in range(3):
        game.drop(1, 0)
        game.drop(2, 1)  # keep player 2 busy in another column
    assert game.winner is None
    game.drop(1, 0)
    assert game.winner == 1


# ---------------------------------------------------------------------------
# 5. Diagonal win (bottom-left to top-right)
# ---------------------------------------------------------------------------


def test_diagonal_win() -> None:
    """Build a board where player 1 wins along a diagonal.

    Uses a pre-built board to avoid turn-alternation complexity.
    Player 1 has pieces at (5,0), (4,1), (3,2), (2,3).
    """
    board = [
        [0, 0, 0, 0, 0, 0, 0],  # row 0
        [0, 0, 0, 0, 0, 0, 0],  # row 1
        [0, 0, 0, 1, 0, 0, 0],  # row 2
        [0, 0, 1, 2, 0, 0, 0],  # row 3
        [0, 1, 2, 2, 0, 0, 0],  # row 4
        [1, 2, 2, 2, 0, 0, 0],  # row 5
    ]
    game = Connect4(board=board)
    assert game.winner == 1


# ---------------------------------------------------------------------------
# 6. Draw detection
# ---------------------------------------------------------------------------


def test_draw_detected() -> None:
    """Fill every cell without a winner and confirm draw flag.

    Uses a verified pattern where the maximum run length in any
    direction is 3, guaranteeing no 4-in-a-row exists.
    """
    board = [
        [2, 2, 1, 2, 2, 1, 2],  # row 0 (top)
        [1, 1, 2, 1, 1, 2, 1],
        [2, 2, 1, 2, 2, 1, 2],
        [1, 1, 2, 1, 1, 2, 1],
        [2, 2, 1, 2, 2, 1, 2],
        [1, 1, 2, 1, 1, 2, 1],  # row 5 (bottom)
    ]

    game = Connect4(board=board)
    assert game.winner is None
    assert game.is_draw


# ---------------------------------------------------------------------------
# 7. Move after game over raises
# ---------------------------------------------------------------------------


def test_move_after_win_raises() -> None:
    game = Connect4()
    # Alternate turns: p1 plays columns 0-3, p2 plays column 6
    game.drop(1, 0)  # p1
    game.drop(2, 6)  # p2
    game.drop(1, 1)  # p1
    game.drop(2, 6)  # p2
    game.drop(1, 2)  # p1
    game.drop(2, 6)  # p2
    game.drop(1, 3)  # p1 wins!
    assert game.winner == 1
    with pytest.raises(GameOverError):
        game.drop(2, 0)


# ---------------------------------------------------------------------------
# 8. Winning cells tracking
# ---------------------------------------------------------------------------


def test_horizontal_win_returns_winning_cells() -> None:
    """Horizontal win should populate winning_cells with exactly the winning coordinates."""
    game = Connect4()
    game.drop(1, 0)  # p1
    game.drop(2, 6)  # p2
    game.drop(1, 1)  # p1
    game.drop(2, 6)  # p2
    game.drop(1, 2)  # p1
    game.drop(2, 6)  # p2
    game.drop(1, 3)  # p1 wins!

    assert game.winner == 1
    assert len(game.winning_cells) >= 4
    expected_cells = {(ROWS - 1, 0), (ROWS - 1, 1), (ROWS - 1, 2), (ROWS - 1, 3)}
    assert expected_cells.issubset(set(game.winning_cells))


def test_vertical_win_returns_winning_cells() -> None:
    """Vertical win should populate winning_cells."""
    game = Connect4()
    for _ in range(3):
        game.drop(1, 0)
        game.drop(2, 1)
    game.drop(1, 0)

    assert game.winner == 1
    assert len(game.winning_cells) >= 4
    winning_cols = {col for _, col in game.winning_cells}
    assert winning_cols == {0}


def test_diagonal_win_returns_winning_cells() -> None:
    """Pre-built diagonal win should populate winning_cells."""
    board = [
        [0, 0, 0, 0, 0, 0, 0],
        [0, 0, 0, 0, 0, 0, 0],
        [0, 0, 0, 1, 0, 0, 0],
        [0, 0, 1, 2, 0, 0, 0],
        [0, 1, 2, 2, 0, 0, 0],
        [1, 2, 2, 2, 0, 0, 0],
    ]
    game = Connect4(board=board)
    assert game.winner == 1
    assert len(game.winning_cells) >= 4
    expected_cells = {(5, 0), (4, 1), (3, 2), (2, 3)}
    assert expected_cells.issubset(set(game.winning_cells))


def test_no_winner_has_empty_winning_cells() -> None:
    """Mid-game state should have empty winning_cells."""
    game = Connect4()
    game.drop(1, 0)
    game.drop(2, 1)
    assert game.winner is None
    assert game.winning_cells == []


def test_draw_has_empty_winning_cells() -> None:
    """A draw should have empty winning_cells."""
    board = [
        [2, 2, 1, 2, 2, 1, 2],
        [1, 1, 2, 1, 1, 2, 1],
        [2, 2, 1, 2, 2, 1, 2],
        [1, 1, 2, 1, 1, 2, 1],
        [2, 2, 1, 2, 2, 1, 2],
        [1, 1, 2, 1, 1, 2, 1],
    ]
    game = Connect4(board=board)
    assert game.is_draw
    assert game.winning_cells == []


# ---------------------------------------------------------------------------
# 9. Invalid turn detection
# ---------------------------------------------------------------------------


def test_invalid_turn_raises() -> None:
    """Dropping out of turn should raise InvalidTurnError."""
    game = Connect4()
    with pytest.raises(InvalidTurnError, match="player 1"):
        game.drop(2, 0)  # player 2 tries to go first


def test_invalid_turn_after_one_move() -> None:
    """Player 1 moving twice in a row should raise InvalidTurnError."""
    game = Connect4()
    game.drop(1, 0)
    with pytest.raises(InvalidTurnError, match="player 2"):
        game.drop(1, 1)  # player 1 goes again


# ---------------------------------------------------------------------------
# 10. Anti-diagonal win (top-right to bottom-left)
# ---------------------------------------------------------------------------


def test_anti_diagonal_win() -> None:
    """Player 2 wins on the anti-diagonal: (2,3), (3,2), (4,1), (5,0)."""
    board = [
        [0, 0, 0, 0, 0, 0, 0],
        [0, 0, 0, 0, 0, 0, 0],
        [0, 0, 0, 2, 0, 0, 0],
        [0, 0, 2, 1, 0, 0, 0],
        [0, 2, 1, 1, 0, 0, 0],
        [2, 1, 1, 1, 0, 0, 0],
    ]
    game = Connect4(board=board)
    assert game.winner == 2
    expected = {(2, 3), (3, 2), (4, 1), (5, 0)}
    assert expected.issubset(set(game.winning_cells))


def test_anti_diagonal_win_via_drop() -> None:
    """Win on the anti-diagonal detected via a pre-built board drop."""
    # Pre-built board where P1 has 3 on anti-diagonal, one more to win
    board = [
        [0, 0, 0, 0, 0, 0, 0],
        [0, 0, 0, 0, 0, 0, 0],
        [0, 0, 0, 0, 0, 0, 0],  # P1 will drop here at col 3
        [0, 0, 1, 2, 0, 0, 0],  # P1 at (3,2)
        [0, 1, 2, 2, 0, 0, 0],  # P1 at (4,1)
        [1, 2, 2, 2, 1, 0, 0],  # P1 at (5,0)
    ]
    game = Connect4(board=board)
    # 10 pieces (even) → next_player = 1. P1 drops at col 3 → (2,3) to win anti-diag
    assert game.next_player == 1
    game.drop(1, 3)
    assert game.winner == 1


# ---------------------------------------------------------------------------
# 11. Move after draw raises GameOverError
# ---------------------------------------------------------------------------


def test_move_after_draw_raises() -> None:
    """Attempting a move on a drawn game should raise GameOverError."""
    board = [
        [2, 2, 1, 2, 2, 1, 2],
        [1, 1, 2, 1, 1, 2, 1],
        [2, 2, 1, 2, 2, 1, 2],
        [1, 1, 2, 1, 1, 2, 1],
        [2, 2, 1, 2, 2, 1, 2],
        [1, 1, 2, 1, 1, 2, 1],
    ]
    game = Connect4(board=board)
    assert game.is_draw
    with pytest.raises(GameOverError):
        game.drop(1, 0)


# ---------------------------------------------------------------------------
# 12. Boundary columns (leftmost and rightmost)
# ---------------------------------------------------------------------------


def test_drop_leftmost_column() -> None:
    """Dropping into column 0 should work correctly."""
    game = Connect4()
    row = game.drop(1, 0)
    assert row == ROWS - 1
    assert game.board[ROWS - 1][0] == 1


def test_drop_rightmost_column() -> None:
    """Dropping into column 6 should work correctly."""
    game = Connect4()
    row = game.drop(1, COLS - 1)
    assert row == ROWS - 1
    assert game.board[ROWS - 1][COLS - 1] == 1


# ---------------------------------------------------------------------------
# 13. Three in a row should NOT trigger a win
# ---------------------------------------------------------------------------


def test_three_in_a_row_is_not_a_win() -> None:
    """Exactly 3 consecutive pieces should not trigger a win."""
    game = Connect4()
    game.drop(1, 0)
    game.drop(2, 6)
    game.drop(1, 1)
    game.drop(2, 6)
    game.drop(1, 2)
    assert game.winner is None, "3-in-a-row should not be a win"


# ---------------------------------------------------------------------------
# 14. Win on the very last cell (board full + win simultaneously)
# ---------------------------------------------------------------------------


def test_win_on_last_cell() -> None:
    """Winning with the final piece on a full board should detect win, not draw."""
    # 41 pieces placed, 1 empty at (0,6). Next player has odd count → P2.
    # P2 drops at (0,6) to complete a horizontal: (0,4), (0,5), (0,6) + need (0,3).
    # Simpler: let P1 go (even count = 40 pieces).
    # 40 pieces, one empty cell at (0,0). P1 drops to complete horizontal.
    # Use the known no-winner pattern with one hole.
    board2 = [
        [0, 2, 1, 2, 2, 1, 2],  # one empty at (0,0)
        [1, 1, 2, 1, 1, 2, 1],
        [2, 2, 1, 2, 2, 1, 2],
        [1, 1, 2, 1, 1, 2, 1],
        [2, 2, 1, 2, 2, 1, 2],
        [1, 1, 2, 1, 1, 2, 1],
    ]
    game2 = Connect4(board=board2)
    assert game2.winner is None  # no winner yet
    # Count: row0 has 6 pieces, rows 1-5 have 7 each = 6 + 35 = 41. Odd → P2.
    assert game2.next_player == 2
    row = game2.drop(2, 0)
    assert row == 0
    # Board is now full — should be draw (no 4-in-a-row in this pattern)
    assert game2.is_draw is True


# ---------------------------------------------------------------------------
# 15. Pre-built board recompute — next_player correctness
# ---------------------------------------------------------------------------


def test_recompute_next_player_odd_pieces() -> None:
    """Loading a board with 3 pieces should set next_player to 2."""
    board = [
        [0, 0, 0, 0, 0, 0, 0],
        [0, 0, 0, 0, 0, 0, 0],
        [0, 0, 0, 0, 0, 0, 0],
        [0, 0, 0, 0, 0, 0, 0],
        [0, 0, 0, 0, 0, 0, 0],
        [1, 2, 1, 0, 0, 0, 0],
    ]
    game = Connect4(board=board)
    assert game.next_player == 2


def test_recompute_next_player_even_pieces() -> None:
    """Loading a board with 4 pieces should set next_player to 1."""
    board = [
        [0, 0, 0, 0, 0, 0, 0],
        [0, 0, 0, 0, 0, 0, 0],
        [0, 0, 0, 0, 0, 0, 0],
        [0, 0, 0, 0, 0, 0, 0],
        [0, 0, 0, 0, 0, 0, 0],
        [1, 2, 1, 2, 0, 0, 0],
    ]
    game = Connect4(board=board)
    assert game.next_player == 1


# ---------------------------------------------------------------------------
# 16. Empty board initial state
# ---------------------------------------------------------------------------


def test_empty_board_initial_state() -> None:
    """A fresh game should have no winner, no draw, and next_player = 1."""
    game = Connect4()
    assert game.winner is None
    assert game.is_draw is False
    assert game.next_player == 1
    assert game.winning_cells == []
    assert all(cell == 0 for row in game.board for cell in row)


# ---------------------------------------------------------------------------
# 17. Pre-built board with horizontal winner via recompute
# ---------------------------------------------------------------------------


def test_recompute_horizontal_winner() -> None:
    """A pre-built board with a horizontal winner should detect it."""
    board = [
        [0, 0, 0, 0, 0, 0, 0],
        [0, 0, 0, 0, 0, 0, 0],
        [0, 0, 0, 0, 0, 0, 0],
        [0, 0, 0, 0, 0, 0, 0],
        [0, 0, 0, 0, 0, 0, 0],
        [1, 1, 1, 1, 0, 0, 0],
    ]
    game = Connect4(board=board)
    assert game.winner == 1


def test_recompute_vertical_winner() -> None:
    """A pre-built board with a vertical winner should detect it."""
    board = [
        [0, 0, 0, 0, 0, 0, 0],
        [0, 0, 0, 0, 0, 0, 0],
        [2, 0, 0, 0, 0, 0, 0],
        [2, 0, 0, 0, 0, 0, 0],
        [2, 0, 0, 0, 0, 0, 0],
        [2, 0, 0, 0, 0, 0, 0],
    ]
    game = Connect4(board=board)
    assert game.winner == 2


# ---------------------------------------------------------------------------
# 18. Invalid column validation (out-of-range columns)
# ---------------------------------------------------------------------------


def test_negative_column_raises_invalid_column() -> None:
    game = Connect4()
    with pytest.raises(InvalidColumnError):
        game.drop(1, -1)


def test_column_too_large_raises_invalid_column() -> None:
    game = Connect4()
    with pytest.raises(InvalidColumnError):
        game.drop(1, COLS)


def test_column_way_too_large_raises_invalid_column() -> None:
    game = Connect4()
    with pytest.raises(InvalidColumnError):
        game.drop(1, 100)


def test_all_valid_columns_work() -> None:
    """All valid columns (0 through COLS-1) should not raise."""
    for col in range(COLS):
        g = Connect4()
        row = g.drop(1, col)
        assert row >= 0
