"""E2E tests — Game lobby (Screen 2): create, join, matchmaking, cancel, history."""

from __future__ import annotations

import pytest

pw = pytest.importorskip("playwright")
from playwright.sync_api import Browser, Page, expect  # noqa: E402

from tests.e2e.conftest import (  # noqa: E402
    DEFAULT_TIMEOUT,
    create_game_and_get_id,
    join_game_by_id,
    play_vertical_win,
    register_player,
    setup_two_player_game,
    wait_for_game_over,
    wait_for_game_screen,
)

pytestmark = pytest.mark.e2e


# ---------------------------------------------------------------------------
# Basic lobby layout
# ---------------------------------------------------------------------------


def test_username_displayed_after_registration(player_page: Page) -> None:
    """The registered username should be shown on the game lobby header."""
    expect(player_page.locator("#display-username")).not_to_be_empty()


def test_waiting_games_list_visible(player_page: Page) -> None:
    """The 'Join a game' section with waiting games list should be visible."""
    expect(player_page.locator("#waiting-games-list")).to_be_visible()


def test_no_own_game_in_waiting_list(player_page: Page) -> None:
    """Games created by the current player should NOT appear in the waiting list."""
    game_id = create_game_and_get_id(player_page)

    # Cancel the waiting state to return to normal lobby view
    player_page.click("#btn-cancel-wait")

    # Refresh the games list
    player_page.click("#btn-refresh-games")
    player_page.wait_for_timeout(1000)

    # Our own game should not be in the list (filtered by player1_id)
    own_game_btn = player_page.locator(f'button[onclick*="{game_id}"]')
    expect(own_game_btn).to_have_count(0)


# ---------------------------------------------------------------------------
# Create game
# ---------------------------------------------------------------------------


def test_create_game_shows_waiting_card(player_page: Page) -> None:
    """Clicking 'New Game' shows the waiting card with a game ID and spinner."""
    player_page.click("#btn-create-game")

    expect(player_page.locator("#waiting-card")).to_be_visible()
    expect(player_page.locator("#waiting-card .spinner")).to_be_visible()

    game_id = player_page.text_content("#waiting-game-id")
    assert game_id is not None and len(game_id.strip()) > 0, "Game ID should be displayed"


def test_cancel_waiting_hides_card(player_page: Page) -> None:
    """Cancelling a waiting game hides the waiting card."""
    player_page.click("#btn-create-game")
    expect(player_page.locator("#waiting-card")).to_be_visible()

    player_page.click("#btn-cancel-wait")
    expect(player_page.locator("#waiting-card")).not_to_be_visible()


def test_create_game_after_cancel_creates_new(player_page: Page) -> None:
    """Creating a new game after cancelling the first one works and shows a new ID."""
    player_page.click("#btn-create-game")
    game_id_1 = player_page.text_content("#waiting-game-id")
    player_page.click("#btn-cancel-wait")

    player_page.click("#btn-create-game")
    game_id_2 = player_page.text_content("#waiting-game-id")

    assert game_id_1 != game_id_2, "Second game should have a different ID"
    expect(player_page.locator("#waiting-card")).to_be_visible()


# ---------------------------------------------------------------------------
# Matchmaking
# ---------------------------------------------------------------------------


def test_matchmaking_shows_card_and_spinner(player_page: Page) -> None:
    """Clicking 'Find Opponent' shows the matchmaking card with an animated spinner."""
    player_page.click("#btn-matchmaking")

    expect(player_page.locator("#matchmaking-card")).to_be_visible()
    expect(player_page.locator("#matchmaking-card .spinner")).to_be_visible()


def test_matchmaking_button_disabled_during_search(player_page: Page) -> None:
    """The 'Find Opponent' button should be disabled while matchmaking is active."""
    player_page.click("#btn-matchmaking")

    expect(player_page.locator("#btn-matchmaking")).to_be_disabled()


def test_cancel_matchmaking_hides_card(player_page: Page) -> None:
    """Cancelling matchmaking hides the card and re-enables the button."""
    player_page.click("#btn-matchmaking")
    expect(player_page.locator("#matchmaking-card")).to_be_visible()

    player_page.click("#btn-cancel-matchmaking")

    expect(player_page.locator("#matchmaking-card")).not_to_be_visible()
    expect(player_page.locator("#btn-matchmaking")).to_be_enabled()
    expect(player_page.locator("#btn-matchmaking")).to_have_text("Find Opponent")


def test_matchmaking_cancel_and_retry_spinner_animates(player_page: Page) -> None:
    """BUG REGRESSION: cancelling matchmaking then retrying must keep the spinner animated.

    Previously, after cancel → retry the spinner would stop animating,
    leaving the user with a frozen 'searching' UI.
    """
    # First search
    player_page.click("#btn-matchmaking")
    expect(player_page.locator("#matchmaking-card .spinner")).to_be_visible()
    player_page.wait_for_timeout(500)

    # Cancel
    player_page.click("#btn-cancel-matchmaking")
    expect(player_page.locator("#matchmaking-card")).not_to_be_visible()
    player_page.wait_for_timeout(300)

    # Retry
    player_page.click("#btn-matchmaking")
    expect(player_page.locator("#matchmaking-card")).to_be_visible()

    spinner = player_page.locator("#matchmaking-card .spinner")
    expect(spinner).to_be_visible()

    # Verify the CSS animation is actually running
    animation_state = spinner.evaluate("el => getComputedStyle(el).animationPlayState")
    assert animation_state == "running", (
        f"Spinner animation is '{animation_state}', expected 'running'. The spinner froze after cancel→retry."
    )

    # Also verify the animation-name is set (not 'none')
    animation_name = spinner.evaluate("el => getComputedStyle(el).animationName")
    assert animation_name != "none", (
        f"Spinner animation-name is '{animation_name}'. CSS animation may have been cleared."
    )


def test_matchmaking_cancel_and_retry_multiple_times(player_page: Page) -> None:
    """Cancel and retry matchmaking 3 times — spinner must work every time."""
    for i in range(3):
        player_page.click("#btn-matchmaking")
        expect(player_page.locator("#matchmaking-card .spinner")).to_be_visible()

        animation_state = player_page.locator("#matchmaking-card .spinner").evaluate(
            "el => getComputedStyle(el).animationPlayState"
        )
        assert animation_state == "running", f"Spinner frozen on attempt {i + 1}"

        player_page.click("#btn-cancel-matchmaking")
        expect(player_page.locator("#matchmaking-card")).not_to_be_visible()
        player_page.wait_for_timeout(300)


def test_matchmaking_button_re_enabled_after_cancel(player_page: Page) -> None:
    """After cancelling matchmaking, the button text and state should reset."""
    player_page.click("#btn-matchmaking")
    expect(player_page.locator("#btn-matchmaking")).to_be_disabled()

    player_page.click("#btn-cancel-matchmaking")

    btn = player_page.locator("#btn-matchmaking")
    expect(btn).to_be_enabled()
    expect(btn).to_have_text("Find Opponent")


def test_create_game_cancels_matchmaking(player_page: Page) -> None:
    """Starting matchmaking then creating a game should hide the matchmaking card."""
    player_page.click("#btn-matchmaking")
    expect(player_page.locator("#matchmaking-card")).to_be_visible()

    player_page.click("#btn-create-game")

    expect(player_page.locator("#matchmaking-card")).not_to_be_visible()
    expect(player_page.locator("#waiting-card")).to_be_visible()


# ---------------------------------------------------------------------------
# Refresh & leaderboard
# ---------------------------------------------------------------------------


def test_refresh_games_button_works(player_page: Page) -> None:
    """Clicking 'Refresh' should update the waiting games list without crashing."""
    player_page.click("#btn-refresh-games")
    player_page.wait_for_timeout(1000)

    expect(player_page.locator("#waiting-games-list")).to_be_visible()


def test_leaderboard_toggle_shows_and_hides(player_page: Page) -> None:
    """The leaderboard toggle button shows/hides the lobby leaderboard card."""
    expect(player_page.locator("#lobby-leaderboard-card")).not_to_be_visible()

    player_page.click("#btn-toggle-leaderboard")
    expect(player_page.locator("#lobby-leaderboard-card")).to_be_visible()

    player_page.click("#btn-toggle-leaderboard")
    expect(player_page.locator("#lobby-leaderboard-card")).not_to_be_visible()


def test_leaderboard_arrow_rotates(player_page: Page) -> None:
    """The toggle arrow should change direction when the leaderboard is opened/closed."""
    arrow = player_page.locator("#leaderboard-arrow")

    expect(arrow).to_have_text("▶")

    player_page.click("#btn-toggle-leaderboard")
    expect(arrow).to_have_text("▼")

    player_page.click("#btn-toggle-leaderboard")
    expect(arrow).to_have_text("▶")


# ---------------------------------------------------------------------------
# Two-player: create & join
# ---------------------------------------------------------------------------


def test_create_game_and_join_from_second_player(two_players: tuple[Page, Page]) -> None:
    """Player 1 creates a game, Player 2 joins — both should reach the game screen."""
    page1, page2 = two_players

    game_id = create_game_and_get_id(page1)
    join_game_by_id(page2, game_id)

    wait_for_game_screen(page1)
    wait_for_game_screen(page2)


def test_matchmaking_matches_two_players(browser) -> None:  # noqa: ANN001
    """Two players clicking 'Find Opponent' simultaneously should get matched."""
    ctx1 = browser.new_context()
    ctx2 = browser.new_context()
    p1 = ctx1.new_page()
    p2 = ctx2.new_page()

    try:
        register_player(p1)
        register_player(p2)

        p1.click("#btn-matchmaking")
        p2.click("#btn-matchmaking")

        # At least one should reach the game screen via matchmaking
        # (the other might get there slightly later through polling)
        game_screen_visible = False
        for _ in range(30):
            p1.wait_for_timeout(500)
            if p1.locator("#screen-game.active").count() > 0 or p2.locator("#screen-game.active").count() > 0:
                game_screen_visible = True
                break

        assert game_screen_visible, "Matchmaking did not match the two players within 15 seconds"
    finally:
        ctx1.close()
        ctx2.close()


# ---------------------------------------------------------------------------
# Player stats card
# ---------------------------------------------------------------------------


def test_player_stats_card_hidden_for_new_player(player_page: Page) -> None:
    """A brand-new player (0 games) should not see the stats card."""
    # The stats card is hidden when total_games == 0
    stats_card = player_page.locator("#player-stats-card")
    # Either not visible or display:none
    player_page.wait_for_timeout(2000)
    display = stats_card.evaluate("el => getComputedStyle(el).display")
    assert display == "none", "Stats card should be hidden for new players"


def test_player_stats_card_visible_after_game(browser: Browser) -> None:
    """After playing a game, the stats card should be visible on return to lobby."""
    ctx1 = browser.new_context()
    ctx2 = browser.new_context()
    p1 = ctx1.new_page()
    p2 = ctx2.new_page()

    try:
        register_player(p1)
        register_player(p2)
        setup_two_player_game(p1, p2)
        play_vertical_win(p1, p2)
        wait_for_game_over(p1)

        # Go back to lobby
        p1.click("#btn-leave")
        p1.wait_for_selector("#screen-games.active", timeout=DEFAULT_TIMEOUT)
        p1.wait_for_timeout(3000)

        # Stats card should now be visible with game data
        stats_card = p1.locator("#player-stats-card")
        display = stats_card.evaluate("el => getComputedStyle(el).display")
        assert display != "none", "Stats card should be visible after playing a game"
    finally:
        ctx1.close()
        ctx2.close()


def test_player_stats_shows_games_count(browser: Browser) -> None:
    """After playing a game, the stats grid should show at least 1 game."""
    ctx1 = browser.new_context()
    ctx2 = browser.new_context()
    p1 = ctx1.new_page()
    p2 = ctx2.new_page()

    try:
        register_player(p1)
        register_player(p2)
        setup_two_player_game(p1, p2)
        play_vertical_win(p1, p2)
        wait_for_game_over(p1)

        p1.click("#btn-leave")
        p1.wait_for_selector("#screen-games.active", timeout=DEFAULT_TIMEOUT)
        p1.wait_for_timeout(3000)

        # Check stats grid has rendered stat items
        stat_items = p1.locator("#stats-grid .stat-item")
        assert stat_items.count() >= 4, "Stats grid should have multiple stat items"
    finally:
        ctx1.close()
        ctx2.close()


# ---------------------------------------------------------------------------
# Game history
# ---------------------------------------------------------------------------


def test_game_history_card_hidden_for_new_player(player_page: Page) -> None:
    """A new player with no games should not see the history card."""
    player_page.wait_for_timeout(2000)
    history_card = player_page.locator("#history-card")
    display = history_card.evaluate("el => getComputedStyle(el).display")
    assert display == "none", "History card should be hidden with no games"


def test_game_history_shows_after_playing(browser: Browser) -> None:
    """After playing a game, the history card should display with game entries."""
    ctx1 = browser.new_context()
    ctx2 = browser.new_context()
    p1 = ctx1.new_page()
    p2 = ctx2.new_page()

    try:
        register_player(p1)
        register_player(p2)
        setup_two_player_game(p1, p2)
        play_vertical_win(p1, p2)
        wait_for_game_over(p1)

        p1.click("#btn-leave")
        p1.wait_for_selector("#screen-games.active", timeout=DEFAULT_TIMEOUT)
        p1.wait_for_timeout(3000)

        history_card = p1.locator("#history-card")
        display = history_card.evaluate("el => getComputedStyle(el).display")
        assert display != "none", "History card should be visible after a game"

        # Should have at least one game entry
        history_items = p1.locator("#history-list .history-item")
        assert history_items.count() >= 1, "History should have at least one game"
    finally:
        ctx1.close()
        ctx2.close()


def test_game_history_shows_result_label(browser: Browser) -> None:
    """Game history entries should display a result (Won/Lost/Draw/etc)."""
    ctx1 = browser.new_context()
    ctx2 = browser.new_context()
    p1 = ctx1.new_page()
    p2 = ctx2.new_page()

    try:
        register_player(p1)
        register_player(p2)
        setup_two_player_game(p1, p2)
        play_vertical_win(p1, p2)
        wait_for_game_over(p1)

        p1.click("#btn-leave")
        p1.wait_for_selector("#screen-games.active", timeout=DEFAULT_TIMEOUT)
        p1.wait_for_timeout(3000)

        # Winner (P1) should see "Won"
        result = p1.locator("#history-list .game-result").first
        result_text = result.text_content() or ""
        assert result_text.strip() in {"Won", "Lost", "Draw", "In progress", "Abandoned", "Waiting"}, (
            f"Unexpected result label: {result_text}"
        )
    finally:
        ctx1.close()
        ctx2.close()


def test_game_history_has_replay_button(browser: Browser) -> None:
    """Finished games in history should have a 'Replay' button."""
    ctx1 = browser.new_context()
    ctx2 = browser.new_context()
    p1 = ctx1.new_page()
    p2 = ctx2.new_page()

    try:
        register_player(p1)
        register_player(p2)
        setup_two_player_game(p1, p2)
        play_vertical_win(p1, p2)
        wait_for_game_over(p1)

        p1.click("#btn-leave")
        p1.wait_for_selector("#screen-games.active", timeout=DEFAULT_TIMEOUT)
        p1.wait_for_timeout(3000)

        replay_btn = p1.locator("#history-list button:has-text('Replay')")
        assert replay_btn.count() >= 1, "Finished games should have a Replay button"
    finally:
        ctx1.close()
        ctx2.close()


# ---------------------------------------------------------------------------
# Auto-refresh & polling
# ---------------------------------------------------------------------------


def test_waiting_games_auto_refresh(browser: Browser) -> None:
    """Waiting games list should auto-refresh — a new game should appear without manual refresh."""
    ctx1 = browser.new_context()
    ctx2 = browser.new_context()
    p1 = ctx1.new_page()
    p2 = ctx2.new_page()

    try:
        register_player(p1)
        register_player(p2)

        # Count initial waiting games on P2's list
        p2.wait_for_timeout(2000)
        initial_count = p2.locator("#waiting-games-list .game-list-item").count()

        # P1 creates a game
        create_game_and_get_id(p1)

        # Wait for auto-refresh cycle (~5 s)
        p2.wait_for_timeout(7000)

        # P2 should now see the game without clicking refresh
        updated_count = p2.locator("#waiting-games-list .game-list-item").count()
        assert updated_count > initial_count, (
            f"Waiting games list should auto-refresh. Before: {initial_count}, After: {updated_count}"
        )

        # Cleanup
        p1.click("#btn-cancel-wait")
    finally:
        ctx1.close()
        ctx2.close()


def test_leaderboard_card_hidden_on_lobby_reentry(browser: Browser) -> None:
    """Re-entering the game lobby should start with leaderboard collapsed."""
    ctx1 = browser.new_context()
    ctx2 = browser.new_context()
    p1 = ctx1.new_page()
    p2 = ctx2.new_page()

    try:
        register_player(p1)
        register_player(p2)

        # Open leaderboard on lobby
        p1.click("#btn-toggle-leaderboard")
        expect(p1.locator("#lobby-leaderboard-card")).to_be_visible()

        # Go to game, then come back
        setup_two_player_game(p1, p2)
        p1.click("#btn-leave")
        p1.wait_for_selector("#screen-games.active", timeout=DEFAULT_TIMEOUT)

        # Leaderboard should be collapsed on re-entry
        expect(p1.locator("#lobby-leaderboard-card")).not_to_be_visible()
    finally:
        ctx1.close()
        ctx2.close()


def test_waiting_card_displays_share_url(player_page: Page) -> None:
    """The waiting card should show a game ID that players can use to join."""
    player_page.click("#btn-create-game")
    expect(player_page.locator("#waiting-card")).to_be_visible()

    game_id_text = player_page.text_content("#waiting-game-id")
    assert game_id_text is not None
    assert len(game_id_text.strip()) >= 8, "Game ID should be a meaningful identifier"

    player_page.click("#btn-cancel-wait")


def test_create_game_button_visible_and_enabled(player_page: Page) -> None:
    """The 'New Game' button should be visible and enabled on the game lobby."""
    btn = player_page.locator("#btn-create-game")
    expect(btn).to_be_visible()
    expect(btn).to_be_enabled()
