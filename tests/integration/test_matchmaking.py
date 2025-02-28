"""Tests for matchmaking endpoints — queue, match, status, and leave flows."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

PLAYER_ALICE_ID: uuid.UUID = uuid.uuid4()
PLAYER_BOB_ID: uuid.UUID = uuid.uuid4()
GAME_ID: uuid.UUID = uuid.uuid4()


def _mock_player(
    player_id: uuid.UUID,
    username: str,
    elo_rating: int = 1000,
) -> SimpleNamespace:
    """Create a lightweight mock player object."""
    return SimpleNamespace(id=player_id, username=username, elo_rating=elo_rating)


def _mock_game(
    game_id: uuid.UUID,
    player1_id: uuid.UUID,
    player2_id: uuid.UUID | None = None,
    status_value: str = "waiting",
) -> SimpleNamespace:
    """Create a lightweight mock game object."""
    return SimpleNamespace(
        id=game_id,
        player1_id=player1_id,
        player2_id=player2_id,
        status=SimpleNamespace(value=status_value),
        winner_id=None,
    )


# ---------------------------------------------------------------------------
# Queue lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_matchmaking_join_queues_player(client: AsyncClient) -> None:
    """First player to join matchmaking should be added to the queue."""
    alice = _mock_player(PLAYER_ALICE_ID, "alice")

    with patch("app.routes.matchmaking.get_player_by_id", new=AsyncMock(return_value=alice)):
        resp = await client.post(
            "/matchmaking/join",
            json={"player1_id": str(PLAYER_ALICE_ID)},
        )

    assert resp.status_code == 200
    assert resp.json()["status"] == "queued"


@pytest.mark.anyio
async def test_matchmaking_status_shows_position(client: AsyncClient) -> None:
    """A queued player should see their position and queue size."""
    alice = _mock_player(PLAYER_ALICE_ID, "alice")

    with patch("app.routes.matchmaking.get_player_by_id", new=AsyncMock(return_value=alice)):
        await client.post(
            "/matchmaking/join",
            json={"player1_id": str(PLAYER_ALICE_ID)},
        )

    resp = await client.get(f"/matchmaking/status/{PLAYER_ALICE_ID}")
    body = resp.json()
    assert body["status"] == "queued"
    assert body["position"] == 1
    assert body["queue_size"] == 1


@pytest.mark.anyio
async def test_matchmaking_leave_removes_from_queue(client: AsyncClient) -> None:
    """Player should be removed from queue after leaving."""
    alice = _mock_player(PLAYER_ALICE_ID, "alice")

    with patch("app.routes.matchmaking.get_player_by_id", new=AsyncMock(return_value=alice)):
        await client.post(
            "/matchmaking/join",
            json={"player1_id": str(PLAYER_ALICE_ID)},
        )

    resp = await client.delete(f"/matchmaking/leave/{PLAYER_ALICE_ID}")
    assert resp.json()["status"] == "left"

    resp = await client.get(f"/matchmaking/status/{PLAYER_ALICE_ID}")
    assert resp.json()["status"] == "not_queued"


@pytest.mark.anyio
async def test_matchmaking_status_not_queued(client: AsyncClient) -> None:
    """Player not in queue should get not_queued status."""
    random_id = uuid.uuid4()
    resp = await client.get(f"/matchmaking/status/{random_id}")
    assert resp.json()["status"] == "not_queued"


# ---------------------------------------------------------------------------
# Matching within ELO band
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_matchmaking_match_within_elo_band(client: AsyncClient) -> None:
    """Two players within 200 ELO should be matched and a game created."""
    alice = _mock_player(PLAYER_ALICE_ID, "alice", elo_rating=1000)
    bob = _mock_player(PLAYER_BOB_ID, "bob", elo_rating=1100)
    game = _mock_game(GAME_ID, PLAYER_ALICE_ID, PLAYER_BOB_ID, "playing")

    # Alice joins → queued
    with patch("app.routes.matchmaking.get_player_by_id", new=AsyncMock(return_value=alice)):
        resp = await client.post(
            "/matchmaking/join",
            json={"player1_id": str(PLAYER_ALICE_ID)},
        )
    assert resp.json()["status"] == "queued"

    # Bob joins → matches with Alice
    with (
        patch("app.routes.matchmaking.get_player_by_id", new=AsyncMock(return_value=bob)),
        patch("app.routes.matchmaking.create_game", new=AsyncMock(return_value=_mock_game(GAME_ID, PLAYER_ALICE_ID))),
        patch("app.routes.matchmaking.join_game", new=AsyncMock(return_value=game)),
    ):
        resp = await client.post(
            "/matchmaking/join",
            json={"player1_id": str(PLAYER_BOB_ID)},
        )

    body = resp.json()
    assert body["status"] == "matched"
    assert body["game_id"] == str(GAME_ID)
    assert body["my_player"] == 2


@pytest.mark.anyio
async def test_matchmaking_no_match_outside_elo_band(client: AsyncClient) -> None:
    """Players with ELO difference > 200 should NOT be matched."""
    alice = _mock_player(PLAYER_ALICE_ID, "alice", elo_rating=1000)
    bob = _mock_player(PLAYER_BOB_ID, "bob", elo_rating=1500)

    # Alice joins → queued
    with patch("app.routes.matchmaking.get_player_by_id", new=AsyncMock(return_value=alice)):
        await client.post(
            "/matchmaking/join",
            json={"player1_id": str(PLAYER_ALICE_ID)},
        )

    # Bob joins → no match (500 ELO apart, band is 200), also queued
    with patch("app.routes.matchmaking.get_player_by_id", new=AsyncMock(return_value=bob)):
        resp = await client.post(
            "/matchmaking/join",
            json={"player1_id": str(PLAYER_BOB_ID)},
        )

    assert resp.json()["status"] == "queued"


# ---------------------------------------------------------------------------
# Race-condition regression: waiting player discovers match via status
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_waiting_player_discovers_match_via_status(client: AsyncClient) -> None:
    """Player 1 queued, Player 2 matches them, Player 1 discovers match via status.

    This is a regression test for the matchmaking race condition:
    previously, the waiting player had no mechanism to discover
    that another player had matched them.
    """
    alice = _mock_player(PLAYER_ALICE_ID, "alice", elo_rating=1000)
    bob = _mock_player(PLAYER_BOB_ID, "bob", elo_rating=1050)
    game = _mock_game(GAME_ID, PLAYER_ALICE_ID, PLAYER_BOB_ID, "playing")

    # Alice joins → queued
    with patch("app.routes.matchmaking.get_player_by_id", new=AsyncMock(return_value=alice)):
        await client.post(
            "/matchmaking/join",
            json={"player1_id": str(PLAYER_ALICE_ID)},
        )

    # Bob joins → matches with Alice
    with (
        patch("app.routes.matchmaking.get_player_by_id", new=AsyncMock(return_value=bob)),
        patch("app.routes.matchmaking.create_game", new=AsyncMock(return_value=_mock_game(GAME_ID, PLAYER_ALICE_ID))),
        patch("app.routes.matchmaking.join_game", new=AsyncMock(return_value=game)),
    ):
        bob_resp = await client.post(
            "/matchmaking/join",
            json={"player1_id": str(PLAYER_BOB_ID)},
        )
    assert bob_resp.json()["status"] == "matched"

    # Alice polls status → should discover the match
    resp = await client.get(f"/matchmaking/status/{PLAYER_ALICE_ID}")
    body = resp.json()
    assert body["status"] == "matched"
    assert body["game_id"] == str(GAME_ID)
    assert body["my_player"] == 1


@pytest.mark.anyio
async def test_match_result_consumed_once(client: AsyncClient) -> None:
    """After discovering a match via status, polling again should return not_queued."""
    alice = _mock_player(PLAYER_ALICE_ID, "alice", elo_rating=1000)
    bob = _mock_player(PLAYER_BOB_ID, "bob", elo_rating=1050)
    game = _mock_game(GAME_ID, PLAYER_ALICE_ID, PLAYER_BOB_ID, "playing")

    # Alice queues, Bob matches
    with patch("app.routes.matchmaking.get_player_by_id", new=AsyncMock(return_value=alice)):
        await client.post("/matchmaking/join", json={"player1_id": str(PLAYER_ALICE_ID)})

    with (
        patch("app.routes.matchmaking.get_player_by_id", new=AsyncMock(return_value=bob)),
        patch("app.routes.matchmaking.create_game", new=AsyncMock(return_value=_mock_game(GAME_ID, PLAYER_ALICE_ID))),
        patch("app.routes.matchmaking.join_game", new=AsyncMock(return_value=game)),
    ):
        await client.post("/matchmaking/join", json={"player1_id": str(PLAYER_BOB_ID)})

    # First poll consumes the result
    resp1 = await client.get(f"/matchmaking/status/{PLAYER_ALICE_ID}")
    assert resp1.json()["status"] == "matched"

    # Second poll — result already consumed, no longer queued
    resp2 = await client.get(f"/matchmaking/status/{PLAYER_ALICE_ID}")
    assert resp2.json()["status"] == "not_queued"


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_matchmaking_unknown_player_returns_404(client: AsyncClient) -> None:
    """Matchmaking join with non-existent player should return 404."""
    with patch("app.routes.matchmaking.get_player_by_id", new=AsyncMock(return_value=None)):
        resp = await client.post(
            "/matchmaking/join",
            json={"player1_id": str(uuid.uuid4())},
        )

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Stale queue regression: ghost players from previous sessions
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_stale_queue_entry_causes_ghost_match(client: AsyncClient) -> None:
    """Stale queue entries (no expiry sentinel) are evicted, not ghost-matched.

    Regression/fix test: leftover matchmaking queue entries from a previous
    session have no MATCHMAKING_EXPIRY_PREFIX key in Redis.  The server must
    evict them silently instead of creating a one-player "ghost" game.
    """
    ghost_id = uuid.uuid4()
    real_player_id = uuid.uuid4()

    # Simulate a stale queue entry: zadd only, no expiry sentinel
    from tests.conftest import fake_redis_instance

    await fake_redis_instance.zadd("matchmaking:queue", {str(ghost_id): 1000.0})

    real_player = _mock_player(real_player_id, "realplayer", elo_rating=1050)

    # Real player joins → ghost has no expiry key → ghost is evicted → real player is queued
    with patch("app.routes.matchmaking.get_player_by_id", new=AsyncMock(return_value=real_player)):
        resp = await client.post(
            "/matchmaking/join",
            json={"player1_id": str(real_player_id)},
        )

    body = resp.json()
    assert (
        body["status"] == "queued"
    ), "Ghost entry without expiry sentinel must be evicted; real player should be queued, not ghost-matched"


@pytest.mark.anyio
async def test_solo_player_queued_not_matched(client: AsyncClient) -> None:
    """A single player joining matchmaking with an empty queue should be queued, not matched.

    This is the core scenario: one player clicks 'Find Opponent' with nobody
    else in the queue. They should wait, not start a game.
    """
    alice = _mock_player(PLAYER_ALICE_ID, "alice", elo_rating=1000)

    with patch("app.routes.matchmaking.get_player_by_id", new=AsyncMock(return_value=alice)):
        resp = await client.post(
            "/matchmaking/join",
            json={"player1_id": str(PLAYER_ALICE_ID)},
        )

    body = resp.json()
    assert body["status"] == "queued", "Solo player should be queued, NOT matched"

    # Verify the queue actually has the player
    status_resp = await client.get(f"/matchmaking/status/{PLAYER_ALICE_ID}")
    status_body = status_resp.json()
    assert status_body["status"] == "queued"
    assert status_body["queue_size"] == 1


@pytest.mark.anyio
async def test_player_not_matched_against_self(client: AsyncClient) -> None:
    """A player already in the queue who re-joins should NOT match themselves."""
    alice = _mock_player(PLAYER_ALICE_ID, "alice", elo_rating=1000)

    # Alice joins once
    with patch("app.routes.matchmaking.get_player_by_id", new=AsyncMock(return_value=alice)):
        resp1 = await client.post(
            "/matchmaking/join",
            json={"player1_id": str(PLAYER_ALICE_ID)},
        )
    assert resp1.json()["status"] == "queued"

    # Alice joins again (e.g., page refresh + re-click)
    with patch("app.routes.matchmaking.get_player_by_id", new=AsyncMock(return_value=alice)):
        resp2 = await client.post(
            "/matchmaking/join",
            json={"player1_id": str(PLAYER_ALICE_ID)},
        )

    assert resp2.json()["status"] == "queued", "Player should NOT match against self"


# ---------------------------------------------------------------------------
# ELO band boundary (difference = 200 should match)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_matchmaking_exact_elo_boundary_matches(client: AsyncClient) -> None:
    """Players with ELO difference of exactly 200 should still be matched (<=)."""
    alice = _mock_player(PLAYER_ALICE_ID, "alice", elo_rating=1000)
    bob = _mock_player(PLAYER_BOB_ID, "bob", elo_rating=1200)  # exactly 200 apart
    game = _mock_game(GAME_ID, PLAYER_ALICE_ID, PLAYER_BOB_ID, "playing")

    with patch("app.routes.matchmaking.get_player_by_id", new=AsyncMock(return_value=alice)):
        await client.post("/matchmaking/join", json={"player1_id": str(PLAYER_ALICE_ID)})

    with (
        patch("app.routes.matchmaking.get_player_by_id", new=AsyncMock(return_value=bob)),
        patch("app.routes.matchmaking.create_game", new=AsyncMock(return_value=_mock_game(GAME_ID, PLAYER_ALICE_ID))),
        patch("app.routes.matchmaking.join_game", new=AsyncMock(return_value=game)),
    ):
        resp = await client.post("/matchmaking/join", json={"player1_id": str(PLAYER_BOB_ID)})

    assert resp.json()["status"] == "matched", "ELO diff of exactly 200 should match"


@pytest.mark.anyio
async def test_matchmaking_just_outside_elo_band(client: AsyncClient) -> None:
    """Players with ELO difference of 201 should NOT be matched."""
    alice = _mock_player(PLAYER_ALICE_ID, "alice", elo_rating=1000)
    bob = _mock_player(PLAYER_BOB_ID, "bob", elo_rating=1201)  # 201 apart

    with patch("app.routes.matchmaking.get_player_by_id", new=AsyncMock(return_value=alice)):
        await client.post("/matchmaking/join", json={"player1_id": str(PLAYER_ALICE_ID)})

    with patch("app.routes.matchmaking.get_player_by_id", new=AsyncMock(return_value=bob)):
        resp = await client.post("/matchmaking/join", json={"player1_id": str(PLAYER_BOB_ID)})

    assert resp.json()["status"] == "queued", "ELO diff of 201 should NOT match"


# ---------------------------------------------------------------------------
# Three+ players in queue — correct pairing
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_three_players_correct_pairing(client: AsyncClient) -> None:
    """With 2 players in queue, a 3rd within ELO band should match the first in score order."""
    player_c_id = uuid.uuid4()
    alice = _mock_player(PLAYER_ALICE_ID, "alice", elo_rating=1000)
    bob = _mock_player(PLAYER_BOB_ID, "bob", elo_rating=1100)
    charlie = _mock_player(player_c_id, "charlie", elo_rating=1050)

    # Alice joins → queued (no match)
    with patch("app.routes.matchmaking.get_player_by_id", new=AsyncMock(return_value=alice)):
        r1 = await client.post("/matchmaking/join", json={"player1_id": str(PLAYER_ALICE_ID)})
    assert r1.json()["status"] == "queued"

    # Bob joins → matches Alice (both within 200 ELO band, Alice first in queue)
    game_ab = _mock_game(GAME_ID, PLAYER_ALICE_ID, PLAYER_BOB_ID, "playing")
    with (
        patch("app.routes.matchmaking.get_player_by_id", new=AsyncMock(return_value=bob)),
        patch("app.routes.matchmaking.create_game", new=AsyncMock(return_value=_mock_game(GAME_ID, PLAYER_ALICE_ID))),
        patch("app.routes.matchmaking.join_game", new=AsyncMock(return_value=game_ab)),
    ):
        r2 = await client.post("/matchmaking/join", json={"player1_id": str(PLAYER_BOB_ID)})
    assert r2.json()["status"] == "matched"
    assert r2.json()["opponent_id"] == str(PLAYER_ALICE_ID)

    # Charlie joins → queue is now empty (Alice+Bob matched), so Charlie is queued
    with patch("app.routes.matchmaking.get_player_by_id", new=AsyncMock(return_value=charlie)):
        r3 = await client.post("/matchmaking/join", json={"player1_id": str(player_c_id)})
    assert r3.json()["status"] == "queued"


# ---------------------------------------------------------------------------
# Leave idempotence
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_matchmaking_leave_idempotent(client: AsyncClient) -> None:
    """Leaving when not in queue should succeed without error."""
    resp = await client.delete(f"/matchmaking/leave/{uuid.uuid4()}")
    assert resp.json()["status"] == "left"
