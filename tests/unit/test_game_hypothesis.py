"""Property-based tests for Connect4 game logic using Hypothesis.

These tests complement the example-based tests in test_game.py by verifying
invariants that must hold for *any* valid sequence of moves — not just the
hand-picked cases that example tests cover.

Each property is concisely named so that Hypothesis failure output immediately
tells you which invariant was broken.
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from app.game import COLS, ROWS, WIN_LENGTH, ColumnFullError, Connect4, GameOverError, InvalidColumnError

# ---------------------------------------------------------------------------
# Strategy helpers
# ---------------------------------------------------------------------------

_col = st.integers(min_value=0, max_value=COLS - 1)
_col_list = st.lists(_col, max_size=ROWS * COLS + 5)


def _play_sequence(columns: list[int]) -> tuple[Connect4, int]:
    """Play a sequence of column choices, skipping full columns, until game over.

    Alternates players automatically (P1 first). Columns that are full are
    silently skipped so the caller does not need to pre-filter them.

    Args:
        columns: Zero-based column indices to attempt in order.

    Returns:
        A tuple of (game, moves_made) where moves_made is the count of drops
        that actually landed a piece (i.e. did not raise ColumnFullError).
    """
    game = Connect4()
    moves_made = 0
    for col in columns:
        if game.winner is not None or game.is_draw:
            break
        try:
            game.drop(game.next_player, col)
            moves_made += 1
        except ColumnFullError:
            pass  # column full — skip and continue sequence
    return game, moves_made


# ---------------------------------------------------------------------------
# Property 1: Board cells are always 0, 1, or 2
# ---------------------------------------------------------------------------


@given(_col_list)
def test_board_cells_are_always_valid(columns: list[int]) -> None:
    """Every cell must be 0 (empty), 1, or 2 regardless of move sequence."""
    game, _ = _play_sequence(columns)
    for row in game.board:
        for cell in row:
            assert cell in {0, 1, 2}, f"Unexpected cell value {cell!r}"


# ---------------------------------------------------------------------------
# Property 2: Piece count equals the number of successful drops
# ---------------------------------------------------------------------------


@given(_col_list)
def test_piece_count_equals_moves_made(columns: list[int]) -> None:
    """Total non-zero cells on the board must equal moves_made at all times."""
    game, moves_made = _play_sequence(columns)
    total = sum(cell != 0 for row in game.board for cell in row)
    assert total == moves_made, f"Piece count {total} != moves_made {moves_made}"


# ---------------------------------------------------------------------------
# Property 3: Winner and draw are mutually exclusive
# ---------------------------------------------------------------------------


@given(_col_list)
def test_winner_and_draw_mutually_exclusive(columns: list[int]) -> None:
    """winner is not None and is_draw == True can never both be true simultaneously."""
    game, _ = _play_sequence(columns)
    assert not (game.winner is not None and game.is_draw), (
        f"Both winner={game.winner} and is_draw=True set at the same time"
    )


# ---------------------------------------------------------------------------
# Property 4: Winner is always player 1 or player 2
# ---------------------------------------------------------------------------


@given(_col_list)
def test_winner_is_player_1_or_2(columns: list[int]) -> None:
    """If a winner exists it must be 1 or 2 — never 0 or any other value."""
    game, _ = _play_sequence(columns)
    if game.winner is not None:
        assert game.winner in {1, 2}, f"Invalid winner value: {game.winner!r}"


# ---------------------------------------------------------------------------
# Property 5: Every cell in winning_cells contains the winner's value
# ---------------------------------------------------------------------------


@given(_col_list)
def test_winning_cells_contain_winner_value(columns: list[int]) -> None:
    """winning_cells must only reference cells holding the winner's player number."""
    game, _ = _play_sequence(columns)
    if game.winner is not None:
        for r, c in game.winning_cells:
            assert game.board[r][c] == game.winner, (
                f"winning_cells entry ({r},{c}) has value {game.board[r][c]}, expected {game.winner}"
            )


# ---------------------------------------------------------------------------
# Property 6: Winning line contains at least WIN_LENGTH cells
# ---------------------------------------------------------------------------


@given(_col_list)
def test_winning_cells_minimum_length(columns: list[int]) -> None:
    """winning_cells must hold at least WIN_LENGTH entries when there is a winner."""
    game, _ = _play_sequence(columns)
    if game.winner is not None:
        assert len(game.winning_cells) >= WIN_LENGTH, (
            f"Winning line has only {len(game.winning_cells)} cells (need {WIN_LENGTH})"
        )


# ---------------------------------------------------------------------------
# Property 7: winning_cells is empty when there is no winner
# ---------------------------------------------------------------------------


@given(_col_list)
def test_no_winning_cells_without_winner(columns: list[int]) -> None:
    """winning_cells must be [] whenever winner is None."""
    game, _ = _play_sequence(columns)
    if game.winner is None:
        assert game.winning_cells == [], f"Expected empty winning_cells but got {game.winning_cells}"


# ---------------------------------------------------------------------------
# Property 8: next_player parity matches piece count
# ---------------------------------------------------------------------------


@given(_col_list)
def test_next_player_parity_matches_piece_count(columns: list[int]) -> None:
    """next_player must be 1 after an even number of moves, 2 after an odd number."""
    game, moves_made = _play_sequence(columns)
    if game.winner is None and not game.is_draw:
        expected = 1 if moves_made % 2 == 0 else 2
        assert game.next_player == expected, (
            f"After {moves_made} moves expected next_player={expected}, got {game.next_player}"
        )


# ---------------------------------------------------------------------------
# Property 9: Any drop after a terminal state raises GameOverError
# ---------------------------------------------------------------------------


@given(_col_list, _col)
def test_move_after_terminal_raises_game_over(columns: list[int], extra_col: int) -> None:
    """Once the game is terminal, every further drop must raise GameOverError.

    GameOverError is checked first in drop(), before turn or column validation,
    so it is always the exception that surfaces.
    """
    game, _ = _play_sequence(columns)
    if game.winner is not None or game.is_draw:
        try:
            game.drop(game.next_player, extra_col)
            raise AssertionError(
                f"Expected GameOverError after terminal state (winner={game.winner}, draw={game.is_draw})"
            )
        except GameOverError:
            pass  # correct


# ---------------------------------------------------------------------------
# Property 10: Out-of-range columns always raise InvalidColumnError
# ---------------------------------------------------------------------------


@given(
    _col_list,
    st.one_of(st.integers(max_value=-1), st.integers(min_value=COLS)),
)
def test_invalid_column_always_raises(columns: list[int], bad_col: int) -> None:
    """Dropping into a column outside [0, COLS) must raise InvalidColumnError.

    Only tested on non-terminal games — on terminal games GameOverError fires
    first, which is a separate invariant tested above.
    """
    game, _ = _play_sequence(columns)
    if game.winner is not None or game.is_draw:
        return
    try:
        game.drop(game.next_player, bad_col)
        raise AssertionError(f"Expected InvalidColumnError for column {bad_col!r}")
    except InvalidColumnError:
        pass  # correct


# ---------------------------------------------------------------------------
# Property 11: Gravity — piece lands at the lowest empty row in its column
# ---------------------------------------------------------------------------


@settings(max_examples=200)
@given(_col_list, _col)
def test_gravity_piece_lands_at_lowest_empty_row(columns: list[int], target_col: int) -> None:
    """A successful drop must place the piece at the lowest unoccupied row."""
    game, _ = _play_sequence(columns)
    if game.winner is not None or game.is_draw:
        return

    # Find the expected lowest empty row in target_col
    expected_row = next(
        (row for row in range(ROWS - 1, -1, -1) if game.board[row][target_col] == 0),
        None,
    )
    if expected_row is None:
        return  # column is full — gravity invariant doesn't apply

    actual_row = game.drop(game.next_player, target_col)
    assert actual_row == expected_row, f"Piece landed at row {actual_row}, expected lowest empty row {expected_row}"
    assert game.board[expected_row][target_col] != 0, f"Cell ({expected_row},{target_col}) is still empty after drop"


# ---------------------------------------------------------------------------
# Property 12: Board loaded from a played game is consistent with its state
# ---------------------------------------------------------------------------


@given(_col_list)
def test_board_reload_preserves_terminal_state(columns: list[int]) -> None:
    """Loading a played board via Connect4(board=...) must detect the same terminal state.

    This validates _recompute_terminal() against the live drop() path.
    """
    original, _ = _play_sequence(columns)
    # Deep-copy the board (list of lists)
    board_snapshot = [row[:] for row in original.board]
    reloaded = Connect4(board=board_snapshot)

    assert reloaded.winner == original.winner, f"Reloaded winner {reloaded.winner!r} != original {original.winner!r}"
    assert reloaded.is_draw == original.is_draw, f"Reloaded is_draw={reloaded.is_draw} != original {original.is_draw}"
    if original.winner is not None:
        assert len(reloaded.winning_cells) >= WIN_LENGTH
