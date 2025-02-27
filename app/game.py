"""Connect 4 core game logic with O(1) localised win detection."""

from __future__ import annotations

from typing import Final

ROWS: Final[int] = 6
COLS: Final[int] = 7
WIN_LENGTH: Final[int] = 4

_DIRECTIONS: Final[list[tuple[int, int]]] = [(0, 1), (1, 0), (1, 1), (1, -1)]


class ColumnFullError(Exception):
    """Raised when a move is attempted on a full column."""


class GameOverError(Exception):
    """Raised when a move is attempted on a finished game."""


class InvalidTurnError(Exception):
    """Raised when a player moves out of turn."""


class InvalidColumnError(Exception):
    """Raised when a column index is out of the valid range."""


class Connect4:
    """In-memory Connect 4 board."""

    def __init__(self, board: list[list[int]] | None = None) -> None:
        self.board: list[list[int]] = board if board is not None else [[0] * COLS for _ in range(ROWS)]
        self.winner: int | None = None
        self.winning_cells: list[tuple[int, int]] = []
        self.is_draw: bool = False
        self.next_player: int = 1
        if board is not None:
            self._recompute_terminal()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def drop(self, player: int, column: int) -> int:
        """Drop a piece for *player* into *column*.

        Args:
            player: Player identifier (1 or 2).
            column: Zero-based column index where the piece is dropped.

        Returns:
            Row index where the piece landed (0 = top, ROWS-1 = bottom).

        Raises:
            GameOverError: If the game is already finished.
            InvalidTurnError: If it is not this player's turn.
            ColumnFullError: If the target column has no empty cells.
        """
        if self.winner is not None or self.is_draw:
            raise GameOverError("The game is already over.")
        if player != self.next_player:
            raise InvalidTurnError(f"It is player {self.next_player}'s turn, not player {player}'s.")
        if not (0 <= column < COLS):
            raise InvalidColumnError(f"Column {column} is out of range (0-{COLS - 1}).")
        row = self._lowest_empty_row(column)
        self.board[row][column] = player
        winning_cells = self._find_winning_cells(row, column, player)
        if winning_cells:
            self.winner = player
            self.winning_cells = winning_cells
        elif self._check_draw():
            self.is_draw = True
        self.next_player = 2 if player == 1 else 1
        return row

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _lowest_empty_row(self, column: int) -> int:
        """Return the lowest empty row in *column* (bottom-up gravity)."""
        for row in range(ROWS - 1, -1, -1):
            if self.board[row][column] == 0:
                return row
        raise ColumnFullError(f"Column {column} is full.")

    def _find_winning_cells(self, row: int, col: int, player: int) -> list[tuple[int, int]]:
        """Localised win check that returns the winning cells — O(WIN_LENGTH) per direction.

        Args:
            row: Row of the last placed piece.
            col: Column of the last placed piece.
            player: Player identifier (1 or 2).

        Returns:
            List of (row, col) tuples forming the winning line, or empty list if no win.
        """
        for dr, dc in _DIRECTIONS:
            cells: list[tuple[int, int]] = [(row, col)]
            # Look in the positive direction
            r, c = row + dr, col + dc
            while 0 <= r < ROWS and 0 <= c < COLS and self.board[r][c] == player:
                cells.append((r, c))
                r += dr
                c += dc
            # Look in the negative direction
            r, c = row - dr, col - dc
            while 0 <= r < ROWS and 0 <= c < COLS and self.board[r][c] == player:
                cells.append((r, c))
                r -= dr
                c -= dc
            if len(cells) >= WIN_LENGTH:
                return cells
        return []

    def _check_draw(self) -> bool:
        """Draw if every cell in the top row is occupied and there is no winner."""
        return all(self.board[0][c] != 0 for c in range(COLS))

    def _recompute_terminal(self) -> None:
        """After loading a persisted board, recompute terminal state and next player."""
        piece_count: int = 0
        for r in range(ROWS):
            for c in range(COLS):
                p = self.board[r][c]
                if p != 0:
                    piece_count += 1
                    if self.winner is None:
                        winning_cells = self._find_winning_cells(r, c, p)
                        if winning_cells:
                            self.winner = p
                            self.winning_cells = winning_cells
        if self.winner is None and self._check_draw():
            self.is_draw = True
        self.next_player = 1 if piece_count % 2 == 0 else 2
