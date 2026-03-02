"""E2E test fixtures — Playwright browser tests against a running server.

These tests require:
    1. ``pip install -e ".[dev,e2e]" && playwright install chromium``
    2. ``docker compose up --build`` (running in background)
    3. ``pytest --e2e`` (opt-in flag)
"""

from __future__ import annotations

import uuid

import pytest

# ---------------------------------------------------------------------------
# Conditional playwright imports — prevent breakage when not installed
# ---------------------------------------------------------------------------

try:
    from playwright.sync_api import Browser, Page, expect  # noqa: F401

    _HAS_PLAYWRIGHT = True
except ImportError:
    _HAS_PLAYWRIGHT = False
    Browser = type(None)  # type: ignore[assignment,misc]
    Page = type(None)  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASE_URL: str = "http://localhost:8000"
"""Base URL for the running application server."""

DEFAULT_TIMEOUT: int = 10_000
"""Default timeout (ms) for Playwright waits."""

GAME_SETUP_TIMEOUT: int = 15_000
"""Longer timeout for game setup operations (join, WebSocket connect)."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def unique_username() -> str:
    """Generate a collision-free username for test isolation."""
    return f"e2e_{uuid.uuid4().hex[:8]}"


def register_player(page: Page, username: str | None = None) -> str:
    """Register a player on the lobby screen and navigate to game lobby.

    Args:
        page: Playwright page (freshly loaded or at lobby screen).
        username: Optional username; auto-generated if omitted.

    Returns:
        The username that was registered.
    """
    name = username or unique_username()
    page.goto(BASE_URL)
    page.fill("#input-username", name)
    page.click('#form-register button[type="submit"]')
    page.wait_for_selector("#screen-games.active", timeout=DEFAULT_TIMEOUT)
    return name


def create_game_and_get_id(page: Page) -> str:
    """Click 'New Game' on the game lobby and return the game ID.

    Args:
        page: Playwright page on the game lobby screen.

    Returns:
        The full UUID game ID string.
    """
    page.click("#btn-create-game")
    page.wait_for_selector("#waiting-card", state="visible", timeout=DEFAULT_TIMEOUT)
    game_id = page.text_content("#waiting-game-id")
    assert game_id is not None
    return game_id.strip()


def join_game_by_id(page: Page, game_id: str) -> None:
    """Join a specific waiting game from another player's game lobby.

    Waits for the game to appear in the waiting games list (auto-refreshes
    every 2 s) then clicks the Join button.

    Args:
        page: Playwright page on the game lobby screen.
        game_id: Full UUID of the game to join.
    """
    join_btn = page.locator(f'button[onclick*="{game_id}"]')
    join_btn.wait_for(state="visible", timeout=GAME_SETUP_TIMEOUT)
    join_btn.click()


def wait_for_game_screen(page: Page) -> None:
    """Wait until the game board screen is active."""
    page.wait_for_selector("#screen-game.active", timeout=GAME_SETUP_TIMEOUT)


def setup_two_player_game(page1: Page, page2: Page) -> str:
    """Create a game on page1, join from page2, wait for both to be on game screen.

    Args:
        page1: Player 1's page (on game lobby).
        page2: Player 2's page (on game lobby).

    Returns:
        The game ID.
    """
    game_id = create_game_and_get_id(page1)
    join_game_by_id(page2, game_id)

    wait_for_game_screen(page1)
    wait_for_game_screen(page2)
    return game_id


def make_move(page: Page, column: int) -> None:
    """Wait for 'Your turn' then click a column on the game board.

    Args:
        page: Playwright page on the active game screen.
        column: Column index (0–6) to drop a piece into.
    """
    if _HAS_PLAYWRIGHT:
        expect(page.locator("#game-status")).to_have_text("Your turn", timeout=DEFAULT_TIMEOUT)
    page.locator(f'#board .cell[data-col="{column}"]').first.click()


def wait_for_game_over(page: Page) -> None:
    """Wait until the game-over banner is visible."""
    page.wait_for_selector("#game-over-banner", state="visible", timeout=DEFAULT_TIMEOUT)


def play_vertical_win(page1: Page, page2: Page) -> None:
    """Play a vertical win for Player 1 in column 0. P2 plays column 1.

    Sequence: P1:0, P2:1, P1:0, P2:1, P1:0, P2:1, P1:0 (win).

    Args:
        page1: Player 1's page (on game screen).
        page2: Player 2's page (on game screen).
    """
    moves = [
        (page1, 0),
        (page2, 1),
        (page1, 0),
        (page2, 1),
        (page1, 0),
        (page2, 1),
        (page1, 0),  # winning move
    ]
    for player_page, col in moves:
        make_move(player_page, col)
        player_page.wait_for_timeout(400)


def play_diagonal_win(page1: Page, page2: Page) -> None:
    r"""Play a diagonal win for Player 1 (bottom-left to top-right).

    Sequence builds a staircase so P1 gets 4 in a diagonal::

        Col:  0  1  2  3
        Row5: P1
        Row5:    P2
        Row4:    P1
        Row5:       P2
        Row4:       P2
        Row3:       P1
        Row5:          P2
        Row4:          P1  (waste — keep turn alternating)
        Row3:          P2  (waste)

    Actual move sequence:
        P1:0, P2:1, P1:1, P2:2, P1:2, P2:3, P1:2, P2:3, P1:3, P2:6, P1:3 → WIN diagonal

    Args:
        page1: Player 1's page.
        page2: Player 2's page.
    """
    moves = [
        (page1, 0),
        (page2, 1),  # P1@(5,0) P2@(5,1)
        (page1, 1),
        (page2, 2),  # P1@(4,1) P2@(5,2)
        (page1, 2),
        (page2, 3),  # P1@(4,2) P2@(5,3)
        (page1, 2),
        (page2, 3),  # P1@(3,2) P2@(4,3)
        (page1, 3),
        (page2, 6),  # P1@(3,3) P2@(5,6)
        (page1, 3),  # P1@(2,3) → WIN diagonal (5,0)(4,1)(3,2)(2,3)
    ]
    for player_page, col in moves:
        make_move(player_page, col)
        player_page.wait_for_timeout(400)


def play_to_draw(page1: Page, page2: Page) -> None:
    """Play a full 42-move game that ends in a draw.

    Uses a column-fill pattern that avoids any 4-in-a-row:
        Cols 0,2,4,6 filled P1-first (P1,P2,P1,P2,P1,P2 bottom-to-top)
        Cols 1,3,5   filled P2-first (P2,P1,P2,P1,P2,P1 bottom-to-top)

    This creates a vertical pattern 121212/212121 which never forms horizontal
    or diagonal connect-4 because adjacent columns alternate starting players.

    Args:
        page1: Player 1's page.
        page2: Player 2's page.
    """
    # Build column order: fill column-by-column
    # P1-first columns: 0,2,4,6 → each column gets 6 cells: P1,P2,P1,P2,P1,P2
    # P2-first columns: 1,3,5   → each column gets 6 cells: P2,P1,P2,P1,P2,P1
    move_sequence: list[tuple[Page, int]] = []

    for col in [0, 2, 4, 6]:  # P1-first columns
        for i in range(6):
            page = page1 if i % 2 == 0 else page2
            move_sequence.append((page, col))

    for col in [1, 3, 5]:  # P2-first columns
        for i in range(6):
            page = page2 if i % 2 == 0 else page1
            move_sequence.append((page, col))

    for player_page, col in move_sequence:
        make_move(player_page, col)
        player_page.wait_for_timeout(350)


# ---------------------------------------------------------------------------
# Session-scoped: verify the server is reachable
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def _assert_server_running() -> None:
    """Fail fast if the application server is not running."""
    import httpx

    try:
        resp = httpx.get(f"{BASE_URL}/stats", timeout=5)
        resp.raise_for_status()
    except (httpx.HTTPError, OSError):
        pytest.skip(f"Application server not running at {BASE_URL}. Start with: docker compose up --build")


@pytest.fixture(autouse=True)
def _require_server(_assert_server_running: None) -> None:
    """Ensure every E2E test skips when the server is unavailable."""


# ---------------------------------------------------------------------------
# Player page fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def player_page(page: Page) -> Page:
    """A Playwright page with a freshly registered player on the game lobby.

    Uses the ``page`` fixture provided by pytest-playwright.
    """
    register_player(page)
    return page


@pytest.fixture
def two_players(browser: Browser) -> tuple[Page, Page]:
    """Two isolated browser contexts, each with a registered player on the game lobby.

    Yields (page1, page2). Both contexts are closed after the test.
    """
    ctx1 = browser.new_context()
    ctx2 = browser.new_context()
    p1 = ctx1.new_page()
    p2 = ctx2.new_page()

    register_player(p1)
    register_player(p2)

    yield p1, p2

    ctx1.close()
    ctx2.close()
