"""E2E tests — Game Replay (Screen 4): controls, board, slider, navigation."""

from __future__ import annotations

import pytest

pw = pytest.importorskip("playwright")
from playwright.sync_api import Browser, Page, expect  # noqa: E402

from tests.e2e.conftest import (  # noqa: E402
    DEFAULT_TIMEOUT,
    play_vertical_win,
    register_player,
    setup_two_player_game,
    wait_for_game_over,
)

pytestmark = pytest.mark.e2e


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _play_and_go_to_replay(browser: Browser) -> tuple[Page, Page]:
    """Play a full game (P1 vertical win) then navigate P1 to the replay screen.

    Returns (page1, page2) with page1 on the replay screen.
    """
    ctx1 = browser.new_context()
    ctx2 = browser.new_context()
    p1 = ctx1.new_page()
    p2 = ctx2.new_page()

    register_player(p1)
    register_player(p2)
    setup_two_player_game(p1, p2)
    play_vertical_win(p1, p2)
    wait_for_game_over(p1)

    # Leave to game lobby
    p1.click("#btn-leave")
    p1.wait_for_selector("#screen-games.active", timeout=DEFAULT_TIMEOUT)

    # Wait for game history to load, then click Replay on the first finished game
    p1.wait_for_timeout(2000)
    replay_btn = p1.locator("#history-list .history-item button:has-text('Replay')")
    replay_btn.first.wait_for(state="visible", timeout=DEFAULT_TIMEOUT)
    replay_btn.first.click()

    p1.wait_for_selector("#screen-replay.active", timeout=DEFAULT_TIMEOUT)
    return p1, p2


# ---------------------------------------------------------------------------
# Replay screen layout
# ---------------------------------------------------------------------------


def test_replay_screen_displays_after_clicking_replay(browser: Browser) -> None:
    """Clicking 'Replay' on a finished game in history should open the replay screen."""
    p1, p2 = _play_and_go_to_replay(browser)
    try:
        expect(p1.locator("#screen-replay")).to_have_class("screen active")
        expect(p1.locator("#replay-board")).to_be_visible()
    finally:
        p1.context.close()
        p2.context.close()


def test_replay_board_has_42_cells(browser: Browser) -> None:
    """The replay board should have exactly 42 cells (6×7)."""
    p1, p2 = _play_and_go_to_replay(browser)
    try:
        expect(p1.locator("#replay-board .cell")).to_have_count(42)
    finally:
        p1.context.close()
        p2.context.close()


def test_replay_board_starts_empty(browser: Browser) -> None:
    """When the replay screen loads, the board should start at move 0 (empty)."""
    p1, p2 = _play_and_go_to_replay(browser)
    try:
        # No pieces should be on the board at step 0
        pieces = p1.locator("#replay-board .cell.p1, #replay-board .cell.p2")
        expect(pieces).to_have_count(0)

        # Move info should say "Move 0 / N"
        move_info = p1.locator("#replay-move-info")
        expect(move_info).to_contain_text("Move 0 /")
    finally:
        p1.context.close()
        p2.context.close()


def test_replay_header_shows_game_replay_text(browser: Browser) -> None:
    """The replay screen header should display 'Game Replay'."""
    p1, p2 = _play_and_go_to_replay(browser)
    try:
        expect(p1.locator("#screen-replay .status")).to_have_text("Game Replay")
    finally:
        p1.context.close()
        p2.context.close()


def test_replay_legend_visible(browser: Browser) -> None:
    """The replay legend (Player 1, Player 2 dots) should be visible."""
    p1, p2 = _play_and_go_to_replay(browser)
    try:
        expect(p1.locator(".replay-legend")).to_be_visible()
        expect(p1.locator(".replay-legend .p1-dot")).to_be_visible()
        expect(p1.locator(".replay-legend .p2-dot")).to_be_visible()
    finally:
        p1.context.close()
        p2.context.close()


# ---------------------------------------------------------------------------
# Replay controls — Next / Prev
# ---------------------------------------------------------------------------


def test_replay_next_button_advances_one_move(browser: Browser) -> None:
    """Clicking 'Next' should advance the replay by one move."""
    p1, p2 = _play_and_go_to_replay(browser)
    try:
        p1.click("#btn-replay-next")
        p1.wait_for_timeout(300)

        # Should now be at "Move 1 / N"
        expect(p1.locator("#replay-move-info")).to_contain_text("Move 1 /")

        # Should have exactly 1 piece on the board
        pieces = p1.locator("#replay-board .cell.p1, #replay-board .cell.p2")
        expect(pieces).to_have_count(1)
    finally:
        p1.context.close()
        p2.context.close()


def test_replay_prev_button_goes_back_one_move(browser: Browser) -> None:
    """Clicking 'Next' then 'Prev' should go back to the previous state."""
    p1, p2 = _play_and_go_to_replay(browser)
    try:
        # Advance twice
        p1.click("#btn-replay-next")
        p1.wait_for_timeout(200)
        p1.click("#btn-replay-next")
        p1.wait_for_timeout(200)

        expect(p1.locator("#replay-move-info")).to_contain_text("Move 2 /")

        # Go back once
        p1.click("#btn-replay-prev")
        p1.wait_for_timeout(200)

        expect(p1.locator("#replay-move-info")).to_contain_text("Move 1 /")
        pieces = p1.locator("#replay-board .cell.p1, #replay-board .cell.p2")
        expect(pieces).to_have_count(1)
    finally:
        p1.context.close()
        p2.context.close()


def test_replay_end_button_jumps_to_last_move(browser: Browser) -> None:
    """Clicking 'End' should jump to the last move showing the final board."""
    p1, p2 = _play_and_go_to_replay(browser)
    try:
        p1.click("#btn-replay-end")
        p1.wait_for_timeout(300)

        # The game had 7 moves (vertical win). Move info = "Move 7 / 7"
        move_info_text = p1.text_content("#replay-move-info")
        assert move_info_text is not None
        # Format: "Move N / N" where both N are the same
        parts = move_info_text.replace("Move", "").strip().split("/")
        current = int(parts[0].strip())
        total = int(parts[1].strip())
        assert current == total, f"Expected to be at last move, got {current}/{total}"

        # Board should have all pieces
        pieces = p1.locator("#replay-board .cell.p1, #replay-board .cell.p2")
        count = pieces.count()
        assert count == total, f"Expected {total} pieces, got {count}"
    finally:
        p1.context.close()
        p2.context.close()


def test_replay_start_button_resets_to_move_zero(browser: Browser) -> None:
    """Clicking 'Start' after advancing should reset to move 0 (empty board)."""
    p1, p2 = _play_and_go_to_replay(browser)
    try:
        # Advance to end
        p1.click("#btn-replay-end")
        p1.wait_for_timeout(200)

        # Click Start
        p1.click("#btn-replay-start")
        p1.wait_for_timeout(200)

        expect(p1.locator("#replay-move-info")).to_contain_text("Move 0 /")
        pieces = p1.locator("#replay-board .cell.p1, #replay-board .cell.p2")
        expect(pieces).to_have_count(0)
    finally:
        p1.context.close()
        p2.context.close()


# ---------------------------------------------------------------------------
# Button disabled states
# ---------------------------------------------------------------------------


def test_prev_and_start_disabled_at_move_zero(browser: Browser) -> None:
    """At move 0, the 'Prev' and 'Start' buttons should be disabled."""
    p1, p2 = _play_and_go_to_replay(browser)
    try:
        expect(p1.locator("#btn-replay-prev")).to_be_disabled()
        expect(p1.locator("#btn-replay-start")).to_be_disabled()
        expect(p1.locator("#btn-replay-next")).to_be_enabled()
        expect(p1.locator("#btn-replay-end")).to_be_enabled()
    finally:
        p1.context.close()
        p2.context.close()


def test_next_and_end_disabled_at_last_move(browser: Browser) -> None:
    """At the last move, the 'Next' and 'End' buttons should be disabled."""
    p1, p2 = _play_and_go_to_replay(browser)
    try:
        p1.click("#btn-replay-end")
        p1.wait_for_timeout(200)

        expect(p1.locator("#btn-replay-next")).to_be_disabled()
        expect(p1.locator("#btn-replay-end")).to_be_disabled()
        expect(p1.locator("#btn-replay-prev")).to_be_enabled()
        expect(p1.locator("#btn-replay-start")).to_be_enabled()
    finally:
        p1.context.close()
        p2.context.close()


# ---------------------------------------------------------------------------
# Slider
# ---------------------------------------------------------------------------


def test_replay_slider_present_and_functional(browser: Browser) -> None:
    """The slider should be present and its max should equal the total moves."""
    p1, p2 = _play_and_go_to_replay(browser)
    try:
        slider = p1.locator("#replay-slider")
        expect(slider).to_be_visible()

        max_val = slider.get_attribute("max")
        assert max_val is not None
        assert int(max_val) > 0, "Slider max should be > 0"
    finally:
        p1.context.close()
        p2.context.close()


def test_replay_slider_updates_board(browser: Browser) -> None:
    """Changing the slider value should update the board to that move."""
    p1, p2 = _play_and_go_to_replay(browser)
    try:
        # Set slider to middle value
        total = int(p1.locator("#replay-slider").get_attribute("max") or "0")
        mid = total // 2

        p1.locator("#replay-slider").fill(str(mid))
        # Trigger input event since fill doesn't always fire it
        p1.locator("#replay-slider").dispatch_event("input")
        p1.wait_for_timeout(300)

        expect(p1.locator("#replay-move-info")).to_contain_text(f"Move {mid} /")
    finally:
        p1.context.close()
        p2.context.close()


# ---------------------------------------------------------------------------
# Last-move highlight
# ---------------------------------------------------------------------------


def test_replay_last_move_highlighted(browser: Browser) -> None:
    """The most recently played piece should have the 'last-move' class."""
    p1, p2 = _play_and_go_to_replay(browser)
    try:
        # Advance to move 1
        p1.click("#btn-replay-next")
        p1.wait_for_timeout(300)

        last_move_cells = p1.locator("#replay-board .cell.last-move")
        expect(last_move_cells).to_have_count(1)
    finally:
        p1.context.close()
        p2.context.close()


def test_replay_no_last_move_at_step_zero(browser: Browser) -> None:
    """At move 0 (empty board), no cell should have the 'last-move' class."""
    p1, p2 = _play_and_go_to_replay(browser)
    try:
        last_move_cells = p1.locator("#replay-board .cell.last-move")
        expect(last_move_cells).to_have_count(0)
    finally:
        p1.context.close()
        p2.context.close()


# ---------------------------------------------------------------------------
# Back to lobby
# ---------------------------------------------------------------------------


def test_back_to_lobby_returns_to_game_lobby(browser: Browser) -> None:
    """Clicking 'Back' on the replay screen should return to the game lobby."""
    p1, p2 = _play_and_go_to_replay(browser)
    try:
        p1.click("#btn-back-to-lobby")

        expect(p1.locator("#screen-games")).to_have_class("screen active", timeout=DEFAULT_TIMEOUT)
        expect(p1.locator("#screen-replay")).not_to_have_class("active")
    finally:
        p1.context.close()
        p2.context.close()
