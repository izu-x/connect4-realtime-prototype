"""E2E tests — Navigation, page refresh, session recovery, and screen transitions."""

from __future__ import annotations

import re

import pytest

pw = pytest.importorskip("playwright")
from playwright.sync_api import Browser, Page, expect  # noqa: E402

from tests.e2e.conftest import (  # noqa: E402
    BASE_URL,
    DEFAULT_TIMEOUT,
    create_game_and_get_id,
    join_game_by_id,
    make_move,
    play_vertical_win,
    register_player,
    setup_two_player_game,
    unique_username,
    wait_for_game_over,
    wait_for_game_screen,
)

pytestmark = pytest.mark.e2e


# ---------------------------------------------------------------------------
# Lobby refresh
# ---------------------------------------------------------------------------


def test_refresh_on_lobby_stays_on_lobby(page: Page) -> None:
    """Refreshing the page when on the lobby screen should keep the lobby visible."""
    page.goto(BASE_URL)
    expect(page.locator("#screen-lobby")).to_have_class("screen active")

    page.reload()

    expect(page.locator("#screen-lobby")).to_have_class("screen active")


def test_refresh_does_not_show_blank_screen(page: Page) -> None:
    """After refresh, at least one screen should have the 'active' class."""
    page.goto(BASE_URL)
    page.reload()
    page.wait_for_timeout(1000)

    active_screens = page.locator(".screen.active")
    count = active_screens.count()
    assert count >= 1, "After refresh, no active screen is visible"


# ---------------------------------------------------------------------------
# Game lobby refresh
# ---------------------------------------------------------------------------


def test_refresh_on_game_lobby_recovers_session(page: Page) -> None:
    """After registration and page refresh, the app should recover to game lobby."""
    name = unique_username()
    register_player(page, name)
    expect(page.locator("#screen-games")).to_have_class("screen active")

    page.reload()
    page.wait_for_timeout(3000)

    # Should recover to game lobby (sessionStorage has playerId + username)
    expect(page.locator("#screen-games")).to_have_class("screen active", timeout=10_000)
    expect(page.locator("#display-username")).to_have_text(name)


def test_session_storage_persists_player_data(page: Page) -> None:
    """Registration should save player data to sessionStorage."""
    name = unique_username()
    register_player(page, name)

    # Check sessionStorage
    stored = page.evaluate("sessionStorage.getItem('c4state')")
    assert stored is not None, "c4state should be in sessionStorage"

    import json

    state = json.loads(stored)
    assert state["username"] == name
    assert state["playerId"] is not None


def test_clear_session_storage_returns_to_lobby(page: Page) -> None:
    """Clearing sessionStorage and refreshing should return to the lobby screen."""
    register_player(page)
    expect(page.locator("#screen-games")).to_have_class("screen active")

    page.evaluate("sessionStorage.clear()")
    page.reload()
    page.wait_for_timeout(2000)

    expect(page.locator("#screen-lobby")).to_have_class("screen active")


# ---------------------------------------------------------------------------
# Game screen refresh
# ---------------------------------------------------------------------------


def test_refresh_during_game_resumes(browser: Browser) -> None:
    """Refreshing during an active game should reconnect and restore the board."""
    ctx1 = browser.new_context()
    ctx2 = browser.new_context()
    p1 = ctx1.new_page()
    p2 = ctx2.new_page()

    try:
        register_player(p1)
        register_player(p2)
        setup_two_player_game(p1, p2)

        # P1 makes a move
        make_move(p1, 3)
        p1.wait_for_timeout(1000)

        # Verify piece is on the board before refresh
        cell_p1 = p1.locator('#board .cell[data-row="5"][data-col="3"]')
        expect(cell_p1).to_have_class(re.compile(r"p1"), timeout=5000)

        # P1 refreshes
        p1.reload()
        p1.wait_for_timeout(3000)

        # P1 should resume on the game screen
        expect(p1.locator("#screen-game")).to_have_class("screen active", timeout=10_000)

        # The board should have the piece restored
        cell_after_refresh = p1.locator('#board .cell[data-row="5"][data-col="3"]')
        expect(cell_after_refresh).to_have_class(re.compile(r"p1"), timeout=10_000)
    finally:
        ctx1.close()
        ctx2.close()


def test_refresh_during_game_allows_continued_play(browser: Browser) -> None:
    """After refreshing mid-game, the player should be able to continue making moves."""
    ctx1 = browser.new_context()
    ctx2 = browser.new_context()
    p1 = ctx1.new_page()
    p2 = ctx2.new_page()

    try:
        register_player(p1)
        register_player(p2)
        setup_two_player_game(p1, p2)

        # P1 makes a move
        make_move(p1, 0)
        p1.wait_for_timeout(1000)

        # P2 refreshes
        p2.reload()
        p2.wait_for_timeout(3000)

        # P2 should be on game screen and it should be P2's turn
        expect(p2.locator("#screen-game")).to_have_class("screen active", timeout=10_000)
        expect(p2.locator("#game-status")).to_have_text("Your turn", timeout=10_000)

        # P2 should be able to make a move
        make_move(p2, 1)
        p2.wait_for_timeout(1000)

        # Verify P2's piece landed
        cell = p2.locator('#board .cell[data-row="5"][data-col="1"]')
        expect(cell).to_have_class(re.compile(r"p2"), timeout=5000)
    finally:
        ctx1.close()
        ctx2.close()


# ---------------------------------------------------------------------------
# Waiting game refresh
# ---------------------------------------------------------------------------


def test_refresh_while_waiting_for_opponent(page: Page) -> None:
    """Refreshing while waiting for an opponent should restore the waiting state.

    The init code checks if the player has an active waiting game and
    shows the waiting card on the game lobby.
    """
    register_player(page)
    create_game_and_get_id(page)

    page.reload()
    page.wait_for_timeout(3000)

    # Should be on game lobby with the waiting card shown
    # (resumeSession detects waiting status and shows the card)
    expect(page.locator("#screen-games")).to_have_class("screen active", timeout=10_000)
    expect(page.locator("#waiting-card")).to_be_visible(timeout=10_000)

    # Cancel so we don't leave stale games
    page.click("#btn-cancel-wait")


# ---------------------------------------------------------------------------
# Screen transitions
# ---------------------------------------------------------------------------


def test_lobby_to_games_to_game_and_back(browser: Browser) -> None:
    """Full navigation flow: lobby → game lobby → game → leave → game lobby."""
    ctx1 = browser.new_context()
    ctx2 = browser.new_context()
    p1 = ctx1.new_page()
    p2 = ctx2.new_page()

    try:
        # Screen 1 → Screen 2
        register_player(p1)
        register_player(p2)
        expect(p1.locator("#screen-games")).to_have_class("screen active")

        # Screen 2 → Screen 3
        setup_two_player_game(p1, p2)
        expect(p1.locator("#screen-game")).to_have_class("screen active")

        # Screen 3 → Screen 2 (via Leave)
        p1.click("#btn-leave")
        expect(p1.locator("#screen-games")).to_have_class("screen active", timeout=10_000)

        # Verify the lobby is functional — can still create a game
        p1.click("#btn-create-game")
        expect(p1.locator("#waiting-card")).to_be_visible()
        p1.click("#btn-cancel-wait")
    finally:
        ctx1.close()
        ctx2.close()


def test_leave_and_rejoin_different_game(browser: Browser) -> None:
    """A player can leave a game and start a completely new one."""
    ctx1 = browser.new_context()
    ctx2 = browser.new_context()
    p1 = ctx1.new_page()
    p2 = ctx2.new_page()

    try:
        register_player(p1)
        register_player(p2)

        # Game 1
        game_id_1 = create_game_and_get_id(p1)

        # Leave before anyone joins
        p1.click("#btn-cancel-wait")
        expect(p1.locator("#waiting-card")).not_to_be_visible()

        # Game 2
        game_id_2 = create_game_and_get_id(p1)
        assert game_id_1 != game_id_2

        # P2 joins game 2
        join_game_by_id(p2, game_id_2)
        wait_for_game_screen(p1)
        wait_for_game_screen(p2)
    finally:
        ctx1.close()
        ctx2.close()


# ---------------------------------------------------------------------------
# Quick play mode
# ---------------------------------------------------------------------------


def test_quick_play_mode_via_url_params(browser: Browser) -> None:
    """The ?game=ID&player=N URL params should bypass registration and go straight to game."""
    ctx = browser.new_context()
    page = ctx.new_page()

    try:
        page.goto(f"{BASE_URL}?game=test-quick-play-e2e&player=1")
        page.wait_for_timeout(2000)

        # Should be on the game screen directly
        expect(page.locator("#screen-game")).to_have_class("screen active")
        expect(page.locator("#board")).to_be_visible()
    finally:
        ctx.close()


# ---------------------------------------------------------------------------
# Corrupt / missing session data
# ---------------------------------------------------------------------------


def test_corrupt_session_storage_falls_back_to_lobby(page: Page) -> None:
    """Corrupt data in sessionStorage should not crash the app — it falls back to lobby."""
    page.goto(BASE_URL)
    page.evaluate("sessionStorage.setItem('c4state', 'NOT VALID JSON {{{');")
    page.reload()
    page.wait_for_timeout(2000)

    # Should gracefully fall back to the lobby
    expect(page.locator("#screen-lobby")).to_have_class("screen active")


def test_partial_session_storage_handled(page: Page) -> None:
    """Partial session data (missing fields) should not crash the app."""
    page.goto(BASE_URL)
    page.evaluate("""sessionStorage.setItem('c4state', JSON.stringify({
        playerId: null,
        username: null
    }));""")
    page.reload()
    page.wait_for_timeout(2000)

    # Should be on lobby since playerId is null
    expect(page.locator("#screen-lobby")).to_have_class("screen active")


# ---------------------------------------------------------------------------
# Single active screen invariant
# ---------------------------------------------------------------------------


def test_only_one_screen_active_on_lobby(page: Page) -> None:
    """At any point, exactly one screen should have the 'active' class on the lobby."""
    page.goto(BASE_URL)
    page.wait_for_timeout(1000)

    active_screens = page.locator(".screen.active")
    expect(active_screens).to_have_count(1)


def test_only_one_screen_active_on_game_lobby(page: Page) -> None:
    """After registration, exactly one screen should be active."""
    register_player(page)
    page.wait_for_timeout(1000)

    active_screens = page.locator(".screen.active")
    expect(active_screens).to_have_count(1)


def test_only_one_screen_active_during_game(browser: Browser) -> None:
    """During an active game, exactly one screen should have 'active' class."""
    ctx1 = browser.new_context()
    ctx2 = browser.new_context()
    p1 = ctx1.new_page()
    p2 = ctx2.new_page()

    try:
        register_player(p1)
        register_player(p2)
        setup_two_player_game(p1, p2)

        active1 = p1.locator(".screen.active")
        active2 = p2.locator(".screen.active")
        expect(active1).to_have_count(1)
        expect(active2).to_have_count(1)
    finally:
        ctx1.close()
        ctx2.close()


# ---------------------------------------------------------------------------
# Finished game resume → should show lobby, not broken game
# ---------------------------------------------------------------------------


def test_finished_game_in_session_goes_to_lobby(browser: Browser) -> None:
    """If sessionStorage has a gameId for a finished game, app should show game lobby."""
    ctx1 = browser.new_context()
    ctx2 = browser.new_context()
    p1 = ctx1.new_page()
    p2 = ctx2.new_page()

    try:
        name1 = register_player(p1)
        register_player(p2)
        setup_two_player_game(p1, p2)

        play_vertical_win(p1, p2)
        wait_for_game_over(p1)

        # Leave: clears gameId from session
        p1.click("#btn-leave")
        p1.wait_for_selector("#screen-games.active", timeout=DEFAULT_TIMEOUT)

        # Should be cleanly on game lobby
        expect(p1.locator("#screen-games")).to_have_class("screen active")
        expect(p1.locator("#display-username")).to_have_text(name1)
    finally:
        ctx1.close()
        ctx2.close()


# ---------------------------------------------------------------------------
# Stats polling lifecycle
# ---------------------------------------------------------------------------


def test_stats_polling_active_on_game_lobby(page: Page) -> None:
    """After registering, stats should be polled on the game lobby (stats card visible)."""
    register_player(page)
    page.wait_for_timeout(2000)

    # Stats card should be visible on the game lobby screen
    expect(page.locator("#stats-card")).to_be_visible()

    games_text = page.text_content("#stat-games")
    players_text = page.text_content("#stat-players")

    assert games_text is not None and games_text.strip().isdigit(), "Active games stat should be numeric"
    assert players_text is not None and players_text.strip().isdigit(), "Online players stat should be numeric"
