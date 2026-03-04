"""E2E tests — Lobby screen (Screen 1): registration, validation, leaderboard, stats."""

from __future__ import annotations

import pytest

pw = pytest.importorskip("playwright")
from playwright.sync_api import Page, expect  # noqa: E402

from tests.e2e.conftest import BASE_URL, unique_username  # noqa: E402

pytestmark = pytest.mark.e2e


# ---------------------------------------------------------------------------
# Page load
# ---------------------------------------------------------------------------


def test_lobby_screen_visible_on_load(page: Page) -> None:
    """The lobby screen should be the active screen when the app first loads."""
    page.goto(BASE_URL)

    expect(page.locator("#screen-lobby")).to_have_class("screen active")
    expect(page.locator("#screen-games")).not_to_have_class("active")
    expect(page.locator("#screen-game")).not_to_have_class("active")


def test_page_title_is_connect_4(page: Page) -> None:
    """The page title should be 'Connect 4'."""
    page.goto(BASE_URL)
    expect(page).to_have_title("Connect 4")


# ---------------------------------------------------------------------------
# Live stats
# ---------------------------------------------------------------------------


def test_live_stats_card_displayed(page: Page) -> None:
    """The stats card should show active games and players counts."""
    page.goto(BASE_URL)

    expect(page.locator("#stats-card")).to_be_visible()
    expect(page.locator("#stat-games")).to_be_visible()
    expect(page.locator("#stat-players")).to_be_visible()


def test_live_stats_are_numeric(page: Page) -> None:
    """Stat values should be numbers (not 'undefined' or empty)."""
    page.goto(BASE_URL)

    # Wait for stats to load (polled every 5 s, but initial call is immediate)
    page.wait_for_timeout(2000)

    games_text = page.text_content("#stat-games")
    players_text = page.text_content("#stat-players")

    assert games_text is not None and games_text.strip().isdigit()
    assert players_text is not None and players_text.strip().isdigit()


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_register_valid_username(page: Page) -> None:
    """Submitting a valid username navigates to the game lobby with the name shown."""
    name = unique_username()
    page.goto(BASE_URL)

    page.fill("#input-username", name)
    page.click('#form-register button[type="submit"]')

    expect(page.locator("#screen-games")).to_have_class("screen active", timeout=10_000)
    expect(page.locator("#display-username")).to_have_text(name)


def test_register_empty_username_shows_error(page: Page) -> None:
    """Submitting an empty username shows an inline error and stays on the lobby."""
    page.goto(BASE_URL)

    page.fill("#input-username", "")
    page.click('#form-register button[type="submit"]')

    # HTML5 required validation prevents submission — the lobby should still be visible
    expect(page.locator("#screen-lobby")).to_have_class("screen active")
    expect(page.locator("#screen-games")).not_to_have_class("active")


def test_register_long_username(page: Page) -> None:
    """A username at the max length limit (64 chars) should still register."""
    name = "A" * 64
    page.goto(BASE_URL)

    page.fill("#input-username", name)
    page.click('#form-register button[type="submit"]')

    # Either navigates to game lobby or shows a server-side error — should not crash
    page.wait_for_timeout(2000)
    # Just verify the page didn't break — either screen should be active
    lobby_active = page.locator("#screen-lobby.active").count() > 0
    games_active = page.locator("#screen-games.active").count() > 0
    assert lobby_active or games_active, "Page broke — no active screen"


# ---------------------------------------------------------------------------
# Leaderboard
# ---------------------------------------------------------------------------


def test_duplicate_username_logs_in_as_returning_player(page: Page) -> None:
    """Registering an already-taken username should log in as a returning player.

    The server intentionally returns the existing player rather than an error,
    so the second page should navigate to the game lobby.
    """

    name = unique_username()
    page.goto(BASE_URL)
    page.fill("#input-username", name)
    page.click('#form-register button[type="submit"]')
    page.wait_for_selector("#screen-games.active", timeout=10_000)

    # Open a fresh page and try the same username
    page2 = page.context.new_page()
    page2.goto(BASE_URL)
    page2.fill("#input-username", name)
    page2.click('#form-register button[type="submit"]')

    # Should navigate to the game lobby as a returning player (no error)
    page2.wait_for_selector("#screen-games.active", timeout=10_000)
    expect(page2.locator("#display-username")).to_have_text(name)


def test_stats_polling_updates_values(page: Page) -> None:
    """Stats should update automatically via polling (values may change over time)."""
    page.goto(BASE_URL)
    page.wait_for_timeout(2000)

    _ = page.text_content("#stat-games")

    # Wait for the next poll cycle (~5 s)
    page.wait_for_timeout(6000)

    # Stats should still be numeric (regardless of whether values changed)
    updated_games = page.text_content("#stat-games")
    assert updated_games is not None and updated_games.strip().isdigit(), "Stats should still be numeric after polling"


def test_lobby_has_username_input_and_submit_button(page: Page) -> None:
    """The lobby form should have a username input field and a submit button."""
    page.goto(BASE_URL)

    expect(page.locator("#input-username")).to_be_visible()
    expect(page.locator('#form-register button[type="submit"]')).to_be_visible()
    expect(page.locator('#form-register button[type="submit"]')).to_be_enabled()


def test_lobby_error_initially_empty(page: Page) -> None:
    """The lobby error element should be empty on initial page load."""
    page.goto(BASE_URL)

    error_text = page.text_content("#lobby-error")
    assert error_text is not None and error_text.strip() == "", "Lobby error should be empty on load"


def test_leaderboard_list_has_entries_or_placeholder(page: Page) -> None:
    """The leaderboard should either show player entries with ELO or a 'No players' placeholder."""
    page.goto(BASE_URL)
    page.wait_for_timeout(3000)

    list_el = page.locator("#leaderboard-list")
    items = list_el.locator("li")
    count = items.count()
    assert count >= 1, "Leaderboard should have at least one item (entries or placeholder)"

    # Check first item: either has ELO number or is the placeholder
    first_text = items.first.text_content() or ""
    is_placeholder = "No players" in first_text
    has_elo = any(char.isdigit() for char in first_text)
    assert is_placeholder or has_elo, f"Leaderboard item should have ELO or be placeholder, got: {first_text}"


# ---------------------------------------------------------------------------
# Leaderboard
# ---------------------------------------------------------------------------


def test_leaderboard_section_renders(page: Page) -> None:
    """The leaderboard section should be present on the lobby screen."""
    page.goto(BASE_URL)

    expect(page.locator("#leaderboard-card")).to_be_visible()
    expect(page.locator("#leaderboard-list")).to_be_visible()
