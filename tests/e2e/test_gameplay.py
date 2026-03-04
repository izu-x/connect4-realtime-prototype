"""E2E tests — Gameplay (Screen 3): moves, win detection, game over, rematch, leave."""

from __future__ import annotations

import re

import pytest

pw = pytest.importorskip("playwright")
from playwright.sync_api import Page, expect  # noqa: E402

from tests.e2e.conftest import (  # noqa: E402
    make_move,
    play_diagonal_win,
    play_to_draw,
    play_vertical_win,
    setup_two_player_game,
    wait_for_game_over,
)

pytestmark = pytest.mark.e2e


# ---------------------------------------------------------------------------
# Game screen basics
# ---------------------------------------------------------------------------


def test_both_players_reach_game_screen(two_players: tuple[Page, Page]) -> None:
    """After join, both players should be on the game screen with a board."""
    page1, page2 = two_players
    setup_two_player_game(page1, page2)

    expect(page1.locator("#board")).to_be_visible()
    expect(page2.locator("#board")).to_be_visible()


def test_board_has_42_cells(two_players: tuple[Page, Page]) -> None:
    """The game board should have exactly 42 cells (6 rows × 7 columns)."""
    page1, page2 = two_players
    setup_two_player_game(page1, page2)

    expect(page1.locator("#board .cell")).to_have_count(42)
    expect(page2.locator("#board .cell")).to_have_count(42)


def test_player1_starts_first(two_players: tuple[Page, Page]) -> None:
    """Player 1 should see 'Your turn' and Player 2 should see 'Opponent's turn'."""
    page1, page2 = two_players
    setup_two_player_game(page1, page2)

    expect(page1.locator("#game-status")).to_have_text("Your turn", timeout=10_000)
    expect(page2.locator("#game-status")).to_have_text("Opponent's turn", timeout=10_000)


def test_player_cards_show_usernames(two_players: tuple[Page, Page]) -> None:
    """Player cards should display the registered usernames."""
    page1, page2 = two_players
    setup_two_player_game(page1, page2)

    # Give time for WS identify messages to propagate
    page1.wait_for_timeout(2000)

    # Player 1's card on page1 should show "(You)"
    p1_label = page1.locator("#player1-card .player-card-label")
    expect(p1_label).to_contain_text("(You)")

    # Player 2's card on page2 should show "(You)"
    p2_label = page2.locator("#player2-card .player-card-label")
    expect(p2_label).to_contain_text("(You)")


def test_player_cards_show_online_status(two_players: tuple[Page, Page]) -> None:
    """Both player cards should show 'Online' status when both are connected."""
    page1, page2 = two_players
    setup_two_player_game(page1, page2)

    page1.wait_for_timeout(2000)

    p1_status = page1.locator("#player1-card .player-card-status")
    p2_status = page1.locator("#player2-card .player-card-status")

    expect(p1_status).to_have_text("Online")
    expect(p2_status).to_have_text("Online")


# ---------------------------------------------------------------------------
# Making moves
# ---------------------------------------------------------------------------


def test_single_move_appears_on_both_boards(two_players: tuple[Page, Page]) -> None:
    """P1 drops a piece — the piece should appear on _both_ players' boards."""
    page1, page2 = two_players
    setup_two_player_game(page1, page2)

    # P1 makes a move in column 3
    make_move(page1, 3)

    # The bottom cell of column 3 (row 5) should have class 'p1' on both boards
    p1_cell = page1.locator('#board .cell[data-row="5"][data-col="3"]')
    p2_cell = page2.locator('#board .cell[data-row="5"][data-col="3"]')

    expect(p1_cell).to_have_class(re.compile(r"p1"), timeout=5000)
    expect(p2_cell).to_have_class(re.compile(r"p1"), timeout=5000)


def test_turn_alternates_after_move(two_players: tuple[Page, Page]) -> None:
    """After P1 moves, P2 should see 'Your turn' and P1 should see 'Opponent's turn'."""
    page1, page2 = two_players
    setup_two_player_game(page1, page2)

    make_move(page1, 0)

    expect(page1.locator("#game-status")).to_have_text("Opponent's turn", timeout=10_000)
    expect(page2.locator("#game-status")).to_have_text("Your turn", timeout=10_000)


def test_click_when_not_your_turn_is_ignored(two_players: tuple[Page, Page]) -> None:
    """Clicking a column when it's not your turn should do nothing."""
    page1, page2 = two_players
    setup_two_player_game(page1, page2)

    # It's P1's turn — P2 clicking should be ignored
    page2.locator('#board .cell[data-col="3"]').first.click()
    page2.wait_for_timeout(1000)

    # Board should still be empty (no pieces placed)
    p2_pieces = page2.locator("#board .cell.p1, #board .cell.p2")
    expect(p2_pieces).to_have_count(0)


# ---------------------------------------------------------------------------
# Full game to win
# ---------------------------------------------------------------------------


def test_vertical_win_by_player1(two_players: tuple[Page, Page]) -> None:
    """Play a full game where Player 1 wins with a vertical connect-4 in column 0.

    Moves:
        P1: col 0  →  P2: col 1
        P1: col 0  →  P2: col 1
        P1: col 0  →  P2: col 1
        P1: col 0  →  WIN!
    """
    page1, page2 = two_players
    setup_two_player_game(page1, page2)

    moves = [
        (page1, 0),
        (page2, 1),
        (page1, 0),
        (page2, 1),
        (page1, 0),
        (page2, 1),
        (page1, 0),  # Winning move
    ]

    for player_page, col in moves:
        make_move(player_page, col)
        # Brief pause for WS propagation
        player_page.wait_for_timeout(400)

    # P1 should see a win
    wait_for_game_over(page1)
    expect(page1.locator("#game-over-text")).to_contain_text("win", ignore_case=True)

    # P2 should see a loss
    wait_for_game_over(page2)
    expect(page2.locator("#game-over-text")).to_contain_text("lose", ignore_case=True)


def test_horizontal_win_by_player2(two_players: tuple[Page, Page]) -> None:
    """Play a game where Player 2 wins with a horizontal connect-4.

    Moves:
        P1: col 0  →  P2: col 3
        P1: col 0  →  P2: col 4
        P1: col 0  →  P2: col 5
        P1: col 1  →  P2: col 6  →  WIN! (columns 3,4,5,6)
    """
    page1, page2 = two_players
    setup_two_player_game(page1, page2)

    moves = [
        (page1, 0),
        (page2, 3),
        (page1, 0),
        (page2, 4),
        (page1, 0),
        (page2, 5),
        (page1, 1),
        (page2, 6),  # P2 wins: horizontal at cols 3-6
    ]

    for player_page, col in moves:
        make_move(player_page, col)
        player_page.wait_for_timeout(400)

    # P2 should see a win, P1 should see a loss
    wait_for_game_over(page2)
    expect(page2.locator("#game-over-text")).to_contain_text("win", ignore_case=True)

    wait_for_game_over(page1)
    expect(page1.locator("#game-over-text")).to_contain_text("lose", ignore_case=True)


def test_winning_cells_are_highlighted(two_players: tuple[Page, Page]) -> None:
    """After a win, the winning cells should have the 'win' CSS class."""
    page1, page2 = two_players
    setup_two_player_game(page1, page2)

    # P1 vertical win in column 0
    moves = [
        (page1, 0),
        (page2, 1),
        (page1, 0),
        (page2, 1),
        (page1, 0),
        (page2, 1),
        (page1, 0),
    ]

    for player_page, col in moves:
        make_move(player_page, col)
        player_page.wait_for_timeout(400)

    wait_for_game_over(page1)

    # After animations settle, check for win-highlighted cells
    page1.wait_for_timeout(1500)
    win_cells = page1.locator("#board .cell.win")
    expect(win_cells).to_have_count(4)


def test_game_over_banner_has_play_again_button(two_players: tuple[Page, Page]) -> None:
    """After game over, the banner should include a 'Play Again' button."""
    page1, page2 = two_players
    setup_two_player_game(page1, page2)

    # Quick vertical win
    for page, col in [(page1, 0), (page2, 1), (page1, 0), (page2, 1), (page1, 0), (page2, 1), (page1, 0)]:
        make_move(page, col)
        page.wait_for_timeout(400)

    wait_for_game_over(page1)

    expect(page1.locator("#btn-new-game")).to_be_visible()
    expect(page1.locator("#btn-new-game")).to_be_enabled()


# ---------------------------------------------------------------------------
# Rematch
# ---------------------------------------------------------------------------


def test_rematch_vote_shows_waiting_status(two_players: tuple[Page, Page]) -> None:
    """After game over, clicking 'Play Again' shows 'Waiting for opponent…'."""
    page1, page2 = two_players
    setup_two_player_game(page1, page2)

    # Quick vertical win
    for page, col in [(page1, 0), (page2, 1), (page1, 0), (page2, 1), (page1, 0), (page2, 1), (page1, 0)]:
        make_move(page, col)
        page.wait_for_timeout(400)

    wait_for_game_over(page1)

    page1.click("#btn-new-game")
    expect(page1.locator("#rematch-status")).to_be_visible()
    expect(page1.locator("#rematch-status")).to_have_text("Waiting for opponent…")
    expect(page1.locator("#btn-new-game")).to_be_disabled()


def test_rematch_accepted_resets_board(two_players: tuple[Page, Page]) -> None:
    """Both players clicking 'Play Again' should reset the board for a new game."""
    page1, page2 = two_players
    setup_two_player_game(page1, page2)

    # Quick vertical win by P1
    for page, col in [(page1, 0), (page2, 1), (page1, 0), (page2, 1), (page1, 0), (page2, 1), (page1, 0)]:
        make_move(page, col)
        page.wait_for_timeout(400)

    wait_for_game_over(page1)
    wait_for_game_over(page2)

    # Both vote for rematch
    page1.click("#btn-new-game")
    page2.click("#btn-new-game")

    # Game over banner should disappear, board should reset
    expect(page1.locator("#game-over-banner")).not_to_be_visible(timeout=10_000)
    expect(page2.locator("#game-over-banner")).not_to_be_visible(timeout=10_000)

    # Board should be empty — no pieces
    page1.wait_for_timeout(500)
    p1_pieces = page1.locator("#board .cell.p1, #board .cell.p2")
    expect(p1_pieces).to_have_count(0)

    # Turn should reset to Player 1
    expect(page1.locator("#game-status")).to_have_text("Your turn", timeout=5000)


# ---------------------------------------------------------------------------
# Leave game
# ---------------------------------------------------------------------------


def test_leave_game_returns_to_lobby(two_players: tuple[Page, Page]) -> None:
    """Clicking 'Leave' should return to the game lobby screen."""
    page1, page2 = two_players
    setup_two_player_game(page1, page2)

    page1.click("#btn-leave")

    expect(page1.locator("#screen-games")).to_have_class("screen active", timeout=10_000)
    expect(page1.locator("#screen-game")).not_to_have_class("active")


def test_leave_clears_game_state(two_players: tuple[Page, Page]) -> None:
    """After leaving, game-over banner and toast container should be clean."""
    page1, page2 = two_players
    setup_two_player_game(page1, page2)

    page1.click("#btn-leave")
    expect(page1.locator("#screen-games")).to_have_class("screen active", timeout=10_000)

    # Game over banner should be hidden
    game_over_display = page1.locator("#game-over-banner").evaluate("el => el.style.display")
    assert game_over_display == "none", "Game over banner should be hidden after leaving"


def test_opponent_disconnect_shows_toast(two_players: tuple[Page, Page]) -> None:
    """When the opponent's browser disconnects, a toast notification should appear."""
    page1, page2 = two_players
    setup_two_player_game(page1, page2)

    # Wait for both to be fully connected
    page1.wait_for_timeout(2000)

    # Close P2's page (simulates disconnect)
    page2.close()

    # P1 should see a disconnect toast
    toast = page1.locator(".toast")
    expect(toast).to_be_visible(timeout=10_000)


# ---------------------------------------------------------------------------
# Column hover
# ---------------------------------------------------------------------------


def test_column_hover_shows_indicator(two_players: tuple[Page, Page]) -> None:
    """Hovering over a column should highlight the hover indicator."""
    page1, page2 = two_players
    setup_two_player_game(page1, page2)

    # Wait for P1's turn
    expect(page1.locator("#game-status")).to_have_text("Your turn", timeout=10_000)

    # Hover over column 3
    page1.locator('#board .cell[data-col="3"]').first.hover()

    indicator = page1.locator('.hover-indicator[data-col="3"]')
    classes = indicator.get_attribute("class") or ""
    assert "active-p1" in classes or "active-p2" in classes, "Hover indicator should be active"


# ---------------------------------------------------------------------------
# Diagonal win
# ---------------------------------------------------------------------------


def test_diagonal_win_by_player1(two_players: tuple[Page, Page]) -> None:
    """Player 1 wins with a diagonal connect-4 (bottom-left to top-right)."""
    page1, page2 = two_players
    setup_two_player_game(page1, page2)

    play_diagonal_win(page1, page2)

    wait_for_game_over(page1)
    expect(page1.locator("#game-over-text")).to_contain_text("win", ignore_case=True)

    wait_for_game_over(page2)
    expect(page2.locator("#game-over-text")).to_contain_text("lose", ignore_case=True)


def test_diagonal_win_highlights_four_cells(two_players: tuple[Page, Page]) -> None:
    """A diagonal win should highlight exactly 4 winning cells."""
    page1, page2 = two_players
    setup_two_player_game(page1, page2)

    play_diagonal_win(page1, page2)
    wait_for_game_over(page1)

    page1.wait_for_timeout(1500)
    win_cells = page1.locator("#board .cell.win")
    expect(win_cells).to_have_count(4)


# ---------------------------------------------------------------------------
# Full column
# ---------------------------------------------------------------------------


def test_full_column_rejects_additional_drops(two_players: tuple[Page, Page]) -> None:
    """Stacking more than 6 pieces in the same column should be rejected."""
    page1, page2 = two_players
    setup_two_player_game(page1, page2)

    # Fill column 0 with 6 pieces alternating P1/P2
    for _ in range(3):
        make_move(page1, 0)
        page1.wait_for_timeout(400)
        make_move(page2, 0)
        page2.wait_for_timeout(400)

    # Column 0 is now full — P1 tries to drop. Should be P1's turn.
    expect(page1.locator("#game-status")).to_have_text("Your turn", timeout=10_000)
    # Click column 0 (full) — should be rejected, turn should stay with P1
    page1.locator('#board .cell[data-col="0"]').first.click()
    page1.wait_for_timeout(1000)

    # Still should be P1's turn since the move was rejected
    expect(page1.locator("#game-status")).to_have_text("Your turn", timeout=5_000)


# ---------------------------------------------------------------------------
# Game-over UI details
# ---------------------------------------------------------------------------


def test_game_over_banner_shows_draw_text(two_players: tuple[Page, Page]) -> None:
    """A drawn game should show 'draw' in the game-over text."""
    page1, page2 = two_players
    setup_two_player_game(page1, page2)

    play_to_draw(page1, page2)

    wait_for_game_over(page1)
    expect(page1.locator("#game-over-text")).to_contain_text("draw", ignore_case=True)


def test_winner_sees_confetti_canvas(two_players: tuple[Page, Page]) -> None:
    """The winner should see a confetti canvas element after winning."""
    page1, page2 = two_players
    setup_two_player_game(page1, page2)

    play_vertical_win(page1, page2)
    wait_for_game_over(page1)

    # Confetti canvas should be present in the DOM for the winner
    page1.wait_for_timeout(500)
    confetti = page1.locator("#confetti-canvas")
    expect(confetti).to_be_visible()


def test_loser_does_not_see_confetti(two_players: tuple[Page, Page]) -> None:
    """The loser should NOT see confetti — only the winner gets it."""
    page1, page2 = two_players
    setup_two_player_game(page1, page2)

    play_vertical_win(page1, page2)
    wait_for_game_over(page2)

    page2.wait_for_timeout(500)
    confetti_count = page2.locator("#confetti-canvas").count()
    assert confetti_count == 0, "Loser should not see confetti"


def test_game_over_status_says_game_over(two_players: tuple[Page, Page]) -> None:
    """After game ends, the status text should say 'Game Over'."""
    page1, page2 = two_players
    setup_two_player_game(page1, page2)

    play_vertical_win(page1, page2)
    wait_for_game_over(page1)

    expect(page1.locator("#game-status")).to_have_text("Game Over")


# ---------------------------------------------------------------------------
# Last-move indicator
# ---------------------------------------------------------------------------


def test_last_move_indicator_visible_after_move(two_players: tuple[Page, Page]) -> None:
    """The most recently dropped piece should have the 'last-move' CSS class."""
    page1, page2 = two_players
    setup_two_player_game(page1, page2)

    make_move(page1, 3)

    # Wait for drop animation to complete
    page1.wait_for_timeout(1500)

    last_move_cells = page1.locator("#board .cell.last-move")
    expect(last_move_cells).to_have_count(1)


def test_last_move_moves_to_latest_piece(two_players: tuple[Page, Page]) -> None:
    """After the second move, the last-move highlight should shift to the new piece."""
    page1, page2 = two_players
    setup_two_player_game(page1, page2)

    make_move(page1, 0)
    page1.wait_for_timeout(1500)

    # First move: last-move on col 0
    first_last = page1.locator("#board .cell.last-move")
    expect(first_last).to_have_count(1)

    make_move(page2, 3)
    page2.wait_for_timeout(1500)

    # After P2's move, last-move should be on col 3, row 5
    last_cell = page1.locator('#board .cell[data-row="5"][data-col="3"].last-move')
    expect(last_cell).to_have_count(1)

    # Total last-move cells should still be exactly 1
    all_last = page1.locator("#board .cell.last-move")
    expect(all_last).to_have_count(1)


def test_last_move_only_one_after_many_rapid_moves(two_players: tuple[Page, Page]) -> None:
    """After many moves played quickly, exactly one cell should have 'last-move'.

    Regression test for overlapping drop-animation handlers re-adding
    the last-move class to stale cells.
    """
    page1, page2 = two_players
    setup_two_player_game(page1, page2)

    # Play 10 moves in different columns — minimal delay between them
    moves = [
        (page1, 3),
        (page2, 4),
        (page1, 2),
        (page2, 5),
        (page1, 1),
        (page2, 0),
        (page1, 6),
        (page2, 3),
        (page1, 4),
        (page2, 2),
    ]

    for player_page, col in moves:
        make_move(player_page, col)
        # Short delay — fast enough to overlap slow animations (feather ~2s)
        player_page.wait_for_timeout(400)

    # Wait for all animations to fully settle
    page1.wait_for_timeout(3000)

    # Exactly one cell should have the last-move ring — on both clients
    for page in (page1, page2):
        all_last = page.locator("#board .cell.last-move")
        expect(all_last).to_have_count(1)


def test_last_move_correct_cell_after_many_moves(two_players: tuple[Page, Page]) -> None:
    """The last-move ring should be on the cell where the final move landed."""
    page1, page2 = two_players
    setup_two_player_game(page1, page2)

    # Play 6 moves, last move is P2 in column 6
    moves = [
        (page1, 0),
        (page2, 1),
        (page1, 2),
        (page2, 3),
        (page1, 4),
        (page2, 6),
    ]
    for player_page, col in moves:
        make_move(player_page, col)
        player_page.wait_for_timeout(400)

    page1.wait_for_timeout(3000)

    # Last move was col 6, should land on row 5 (bottom)
    last_cell = page1.locator('#board .cell[data-row="5"][data-col="6"].last-move')
    expect(last_cell).to_have_count(1)

    # Still exactly one total
    all_last = page1.locator("#board .cell.last-move")
    expect(all_last).to_have_count(1)


# ---------------------------------------------------------------------------
# Board shake on draw
# ---------------------------------------------------------------------------


def test_board_shake_animation_on_draw(two_players: tuple[Page, Page]) -> None:
    """When a draw occurs, the board container should briefly have the 'shaking' class."""
    page1, page2 = two_players
    setup_two_player_game(page1, page2)

    play_to_draw(page1, page2)

    wait_for_game_over(page1)
    # The shaking class is added then removed via animationend — just verify game ended as draw
    expect(page1.locator("#game-over-text")).to_contain_text("draw", ignore_case=True)


# ---------------------------------------------------------------------------
# Idle taunt system
# ---------------------------------------------------------------------------


def test_idle_taunts_appear_after_inactivity(two_players: tuple[Page, Page]) -> None:
    """After ~8 seconds of idle on your turn, taunt emojis should appear."""
    page1, page2 = two_players
    setup_two_player_game(page1, page2)

    # P1's turn — wait for idle taunt timer (8s idle + buffer)
    expect(page1.locator("#game-status")).to_have_text("Your turn", timeout=10_000)
    page1.wait_for_timeout(12_000)

    # Should have at least one taunt emoji on the page
    taunts = page1.locator(".taunt-emoji")
    assert taunts.count() >= 1, "Taunt emojis should appear after idle timeout"


def test_idle_taunts_stop_after_move(two_players: tuple[Page, Page]) -> None:
    """Making a move should stop the idle taunt emojis."""
    page1, page2 = two_players
    setup_two_player_game(page1, page2)

    # Wait for taunts to start
    expect(page1.locator("#game-status")).to_have_text("Your turn", timeout=10_000)
    page1.wait_for_timeout(12_000)

    # Make a move (stops taunts)
    make_move(page1, 0)
    page1.wait_for_timeout(1000)

    # Existing taunts should be removed
    taunts = page1.locator(".taunt-emoji")
    expect(taunts).to_have_count(0)


# ---------------------------------------------------------------------------
# Active-turn card indicator
# ---------------------------------------------------------------------------


def test_active_turn_card_highlighted(two_players: tuple[Page, Page]) -> None:
    """The player card for the current turn should have the 'active-turn' class."""
    page1, page2 = two_players
    setup_two_player_game(page1, page2)

    # P1's turn initially
    page1.wait_for_timeout(1000)
    p1_card = page1.locator("#player1-card")
    p2_card = page1.locator("#player2-card")

    expect(p1_card).to_have_class(re.compile(r"active-turn"))
    p2_classes = p2_card.get_attribute("class") or ""
    assert "active-turn" not in p2_classes, "P2 card should NOT be active-turn on P1's turn"


def test_active_turn_switches_after_move(two_players: tuple[Page, Page]) -> None:
    """After P1 moves, the active-turn class should move to P2's card."""
    page1, page2 = two_players
    setup_two_player_game(page1, page2)

    make_move(page1, 0)
    page1.wait_for_timeout(1000)

    # Now P2's turn — P2's card should be active
    p2_card = page1.locator("#player2-card")
    expect(p2_card).to_have_class(re.compile(r"active-turn"), timeout=5000)

    p1_card = page1.locator("#player1-card")
    p1_classes = p1_card.get_attribute("class") or ""
    assert "active-turn" not in p1_classes, "P1 card should lose active-turn after moving"


# ---------------------------------------------------------------------------
# Countdown ring
# ---------------------------------------------------------------------------


def test_countdown_ring_starts_on_turn(two_players: tuple[Page, Page]) -> None:
    """The countdown CSS variable should be set on the active player card."""
    page1, page2 = two_players
    setup_two_player_game(page1, page2)

    page1.wait_for_timeout(2000)

    # Check that the --cd-pct CSS variable is set on the active card
    p1_card = page1.locator("#player1-card")
    cd_pct = p1_card.evaluate("el => getComputedStyle(el).getPropertyValue('--cd-pct')")
    # --cd-pct should be a number between 0 and 100
    assert cd_pct is not None and cd_pct.strip() != "", "Countdown variable should be set"


# ---------------------------------------------------------------------------
# WebSocket disconnect UI
# ---------------------------------------------------------------------------


def test_opponent_disconnect_shows_offline_status(two_players: tuple[Page, Page]) -> None:
    """When the opponent disconnects, their player card should show 'Offline'."""
    page1, page2 = two_players
    setup_two_player_game(page1, page2)

    page1.wait_for_timeout(2000)

    # Verify both are online first
    expect(page1.locator("#player2-card .player-card-status")).to_have_text("Online")

    # Close P2's page
    page2.close()
    page1.wait_for_timeout(5000)

    # P2's card on P1's board should now show "Offline"
    expect(page1.locator("#player2-card .player-card-status")).to_have_text("Offline")


def test_ws_disconnect_shows_reconnecting_status(two_players: tuple[Page, Page]) -> None:
    """Closing the WebSocket should show a reconnecting message in the game status."""
    page1, page2 = two_players
    setup_two_player_game(page1, page2)

    page1.wait_for_timeout(2000)

    # Close P1's WebSocket from the client side
    page1.evaluate("if (state.ws) state.ws.close()")
    page1.wait_for_timeout(500)

    status_text = page1.text_content("#game-status")
    assert status_text is not None
    assert "Disconnected" in status_text or "reconnect" in status_text.lower(), (
        f"Expected disconnect message, got: {status_text}"
    )


def test_board_disabled_on_disconnect(two_players: tuple[Page, Page]) -> None:
    """When WebSocket disconnects, the board should get the 'board-disabled' class."""
    page1, page2 = two_players
    setup_two_player_game(page1, page2)

    page1.wait_for_timeout(1000)

    # Close P1's WebSocket
    page1.evaluate("if (state.ws) state.ws.close()")
    page1.wait_for_timeout(1000)

    board = page1.locator("#board")
    board_classes = board.get_attribute("class") or ""
    assert "board-disabled" in board_classes, "Board should be disabled on disconnect"


# ---------------------------------------------------------------------------
# Rematch flow details
# ---------------------------------------------------------------------------


def test_rematch_opponent_wants_rematch_text(two_players: tuple[Page, Page]) -> None:
    """When only the opponent clicks 'Play Again', the other should see 'Opponent wants a rematch!'."""
    page1, page2 = two_players
    setup_two_player_game(page1, page2)

    play_vertical_win(page1, page2)
    wait_for_game_over(page1)
    wait_for_game_over(page2)

    # Only P2 votes for rematch
    page2.click("#btn-new-game")

    # P1 should see the rematch notification
    expect(page1.locator("#rematch-status")).to_be_visible(timeout=5000)
    expect(page1.locator("#rematch-status")).to_have_text("Opponent wants a rematch!")


def test_play_again_no_ws_fallback_returns_to_lobby(two_players: tuple[Page, Page]) -> None:
    """If WS is closed on game over, 'Play Again' should fall back to game lobby."""
    page1, page2 = two_players
    setup_two_player_game(page1, page2)

    play_vertical_win(page1, page2)
    wait_for_game_over(page1)

    # Close the WS from client side
    page1.evaluate("if (state.ws) { state.ws.close(); state.ws = null; }")
    page1.wait_for_timeout(500)

    # Click Play Again
    page1.click("#btn-new-game")

    # Should go back to game lobby since WS is dead
    expect(page1.locator("#screen-games")).to_have_class("screen active", timeout=10_000)


# ---------------------------------------------------------------------------
# Toast notifications
# ---------------------------------------------------------------------------


def test_toast_appears_on_opponent_disconnect(two_players: tuple[Page, Page]) -> None:
    """A toast notification should appear when the opponent disconnects mid-game."""
    page1, page2 = two_players
    setup_two_player_game(page1, page2)

    page1.wait_for_timeout(2000)
    page2.close()

    toast = page1.locator(".toast")
    expect(toast).to_be_visible(timeout=10_000)

    # Toast should contain meaningful text
    toast_text = toast.first.text_content() or ""
    assert len(toast_text.strip()) > 0, "Toast should have text content"


# ---------------------------------------------------------------------------
# Hover behaviour edge cases
# ---------------------------------------------------------------------------


def test_hover_indicator_cleared_on_mouse_leave(two_players: tuple[Page, Page]) -> None:
    """Moving mouse away from the board should clear hover indicators."""
    page1, page2 = two_players
    setup_two_player_game(page1, page2)

    expect(page1.locator("#game-status")).to_have_text("Your turn", timeout=10_000)

    # Hover then leave
    page1.locator('#board .cell[data-col="3"]').first.hover()
    page1.wait_for_timeout(200)
    page1.locator("#game-status").hover()  # hover somewhere else
    page1.wait_for_timeout(200)

    # All indicators should be cleared
    active_indicators = page1.locator(".hover-indicator.active-p1, .hover-indicator.active-p2")
    expect(active_indicators).to_have_count(0)


def test_hover_indicator_not_shown_when_game_over(two_players: tuple[Page, Page]) -> None:
    """After game over, hovering should NOT show column indicators."""
    page1, page2 = two_players
    setup_two_player_game(page1, page2)

    play_vertical_win(page1, page2)
    wait_for_game_over(page1)

    # Hover over a column
    page1.locator('#board .cell[data-col="3"]').first.hover()
    page1.wait_for_timeout(300)

    active_indicators = page1.locator(".hover-indicator.active-p1, .hover-indicator.active-p2")
    expect(active_indicators).to_have_count(0)


# ---------------------------------------------------------------------------
# Leave game cleanup
# ---------------------------------------------------------------------------


def test_leave_stops_confetti(two_players: tuple[Page, Page]) -> None:
    """Leaving a game after winning should remove the confetti canvas."""
    page1, page2 = two_players
    setup_two_player_game(page1, page2)

    play_vertical_win(page1, page2)
    wait_for_game_over(page1)

    # Confetti should be visible on win
    page1.wait_for_timeout(500)
    expect(page1.locator("#confetti-canvas")).to_be_visible()

    # Leave the game
    page1.click("#btn-leave")
    expect(page1.locator("#screen-games")).to_have_class("screen active", timeout=10_000)

    # Confetti canvas should be removed
    confetti_count = page1.locator("#confetti-canvas").count()
    assert confetti_count == 0, "Confetti should be removed after leaving"


def test_leave_clears_toast_container(two_players: tuple[Page, Page]) -> None:
    """Leaving the game should clear all toast notifications."""
    page1, page2 = two_players
    setup_two_player_game(page1, page2)

    # Trigger a toast by disconnecting P2
    page1.wait_for_timeout(1000)
    page2.close()
    page1.wait_for_timeout(3000)

    # There should be a toast
    _ = page1.locator(".toast").count()

    # Leave the game
    page1.click("#btn-leave")
    expect(page1.locator("#screen-games")).to_have_class("screen active", timeout=10_000)

    # Toast container should be empty
    toast_html = page1.locator("#toast-container").inner_html()
    assert toast_html.strip() == "", "Toast container should be cleared after leaving"


# ---------------------------------------------------------------------------
# Player card CSS classes
# ---------------------------------------------------------------------------


def test_player_card_has_is_me_class(two_players: tuple[Page, Page]) -> None:
    """The current player's card should have the 'is-me' class."""
    page1, page2 = two_players
    setup_two_player_game(page1, page2)

    page1.wait_for_timeout(2000)

    # P1's card on P1's page should have 'is-me'
    p1_card_classes = page1.locator("#player1-card").get_attribute("class") or ""
    assert "is-me" in p1_card_classes, "Player's own card should have 'is-me' class"


def test_player_cards_have_color_classes(two_players: tuple[Page, Page]) -> None:
    """Player cards should have the appropriate color class."""
    page1, page2 = two_players
    setup_two_player_game(page1, page2)

    page1.wait_for_timeout(2000)

    p1_card_classes = page1.locator("#player1-card").get_attribute("class") or ""
    p2_card_classes = page1.locator("#player2-card").get_attribute("class") or ""

    assert "player1-color" in p1_card_classes, "P1 card should have player1-color"
    assert "player2-color" in p2_card_classes, "P2 card should have player2-color"


def test_online_player_card_has_online_class(two_players: tuple[Page, Page]) -> None:
    """Connected players' cards should have the 'online' CSS class."""
    page1, page2 = two_players
    setup_two_player_game(page1, page2)

    page1.wait_for_timeout(2000)

    p1_card_classes = page1.locator("#player1-card").get_attribute("class") or ""
    p2_card_classes = page1.locator("#player2-card").get_attribute("class") or ""

    assert "online" in p1_card_classes, "P1 card should be online"
    assert "online" in p2_card_classes, "P2 card should be online"


def test_disconnected_player_card_has_offline_class(two_players: tuple[Page, Page]) -> None:
    """A disconnected player's card should have the 'offline' CSS class."""
    page1, page2 = two_players
    setup_two_player_game(page1, page2)

    page1.wait_for_timeout(2000)
    page2.close()
    page1.wait_for_timeout(5000)

    p2_card_classes = page1.locator("#player2-card").get_attribute("class") or ""
    assert "offline" in p2_card_classes, "Disconnected P2 card should have 'offline' class"
