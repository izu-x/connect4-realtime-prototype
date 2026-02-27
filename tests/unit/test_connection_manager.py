"""Unit tests for ConnectionManager — WebSocket player tracking and broadcast."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from app.connection_manager import ConnectionManager

# ---------------------------------------------------------------------------
# Player tracking
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_connect_tracks_player_number() -> None:
    """Connecting with a player number should register it."""
    manager = ConnectionManager()
    websocket = AsyncMock()

    await manager.connect("game1", websocket, player_number=1)

    assert manager._connected_player_numbers("game1") == [1]


@pytest.mark.anyio
async def test_two_players_both_tracked() -> None:
    """Both players should appear in connected_players after connecting."""
    manager = ConnectionManager()
    websocket_one = AsyncMock()
    websocket_two = AsyncMock()

    await manager.connect("game1", websocket_one, player_number=1)
    await manager.connect("game1", websocket_two, player_number=2)

    assert manager._connected_player_numbers("game1") == [1, 2]


@pytest.mark.anyio
async def test_connected_player_numbers_empty_game() -> None:
    """Unknown game should return empty list."""
    manager = ConnectionManager()

    assert manager._connected_player_numbers("nonexistent") == []


# ---------------------------------------------------------------------------
# Status broadcasts on connect
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_connect_broadcasts_status() -> None:
    """Connecting should broadcast a player_status message to the room."""
    manager = ConnectionManager()
    websocket = AsyncMock()

    await manager.connect("game1", websocket, player_number=1)

    websocket.send_text.assert_called()
    sent = json.loads(websocket.send_text.call_args[0][0])
    assert sent["type"] == "player_status"
    assert sent["player"] == 1
    assert sent["status"] == "connected"
    assert sent["connected_players"] == [1]


@pytest.mark.anyio
async def test_second_connect_broadcasts_both_players() -> None:
    """When player 2 connects, both players should be listed in connected_players."""
    manager = ConnectionManager()
    websocket_one = AsyncMock()
    websocket_two = AsyncMock()

    await manager.connect("game1", websocket_one, player_number=1)
    await manager.connect("game1", websocket_two, player_number=2)

    # Check the last message sent to player 1
    last_call = websocket_one.send_text.call_args_list[-1]
    sent = json.loads(last_call[0][0])
    assert sent["connected_players"] == [1, 2]


# ---------------------------------------------------------------------------
# Disconnect
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_disconnect_removes_player() -> None:
    """Disconnecting should remove the player from tracking."""
    manager = ConnectionManager()
    websocket = AsyncMock()

    await manager.connect("game1", websocket, player_number=1)
    manager.disconnect("game1", websocket)

    assert manager._connected_player_numbers("game1") == []


@pytest.mark.anyio
async def test_disconnect_and_notify_broadcasts_departure() -> None:
    """Disconnecting with notification should broadcast to remaining players."""
    manager = ConnectionManager()
    websocket_one = AsyncMock()
    websocket_two = AsyncMock()

    await manager.connect("game1", websocket_one, player_number=1)
    await manager.connect("game1", websocket_two, player_number=2)

    # Reset call counts so we only check the disconnect broadcast
    websocket_two.send_text.reset_mock()

    await manager.disconnect_and_notify("game1", websocket_one)

    websocket_two.send_text.assert_called()
    sent = json.loads(websocket_two.send_text.call_args[0][0])
    assert sent["type"] == "player_status"
    assert sent["player"] == 1
    assert sent["status"] == "disconnected"
    assert sent["connected_players"] == [2]


# ---------------------------------------------------------------------------
# Broadcast
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_broadcast_sends_to_all_in_room() -> None:
    """Broadcast should send the message to every connection in the room."""
    manager = ConnectionManager()
    websocket_one = AsyncMock()
    websocket_two = AsyncMock()

    await manager.connect("game1", websocket_one, player_number=1)
    await manager.connect("game1", websocket_two, player_number=2)

    websocket_one.send_text.reset_mock()
    websocket_two.send_text.reset_mock()

    await manager.broadcast("game1", {"type": "test", "data": 42})

    assert websocket_one.send_text.call_count == 1
    assert websocket_two.send_text.call_count == 1
    sent = json.loads(websocket_one.send_text.call_args[0][0])
    assert sent["type"] == "test"
    assert sent["data"] == 42


@pytest.mark.anyio
async def test_broadcast_isolates_rooms() -> None:
    """Messages should only reach connections in the same game room."""
    manager = ConnectionManager()
    websocket_game1 = AsyncMock()
    websocket_game2 = AsyncMock()

    await manager.connect("game1", websocket_game1, player_number=1)
    await manager.connect("game2", websocket_game2, player_number=1)

    websocket_game1.send_text.reset_mock()
    websocket_game2.send_text.reset_mock()

    await manager.broadcast("game1", {"type": "move"})

    assert websocket_game1.send_text.call_count == 1
    assert websocket_game2.send_text.call_count == 0


@pytest.mark.anyio
async def test_broadcast_removes_dead_connections() -> None:
    """Dead connections should be cleaned up during broadcast."""
    manager = ConnectionManager()
    websocket_alive = AsyncMock()
    websocket_dead = AsyncMock()
    websocket_dead.send_text.side_effect = RuntimeError("connection closed")

    await manager.connect("game1", websocket_alive, player_number=1)
    await manager.connect("game1", websocket_dead, player_number=2)

    websocket_alive.send_text.reset_mock()

    await manager.broadcast("game1", {"type": "test"})

    # Dead connection should be removed from the room
    assert websocket_dead not in manager._rooms.get("game1", [])
    # Alive connection should still have received the message
    assert websocket_alive.send_text.call_count == 1


# ---------------------------------------------------------------------------
# Username tracking
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_connect_broadcasts_usernames_dict() -> None:
    """Player status broadcasts should include a usernames dictionary."""
    manager = ConnectionManager()
    websocket = AsyncMock()

    await manager.connect("game1", websocket, player_number=1)

    sent = json.loads(websocket.send_text.call_args[0][0])
    assert "usernames" in sent
    assert isinstance(sent["usernames"], dict)


@pytest.mark.anyio
async def test_disconnect_broadcast_includes_usernames() -> None:
    """Disconnect notifications should also include the usernames dictionary."""
    manager = ConnectionManager()
    websocket_one = AsyncMock()
    websocket_two = AsyncMock()

    await manager.connect("game1", websocket_one, player_number=1)
    await manager.connect("game1", websocket_two, player_number=2)
    manager._usernames["game1"] = {1: "Alice", 2: "Bob"}

    websocket_two.send_text.reset_mock()
    await manager.disconnect_and_notify("game1", websocket_one)

    sent = json.loads(websocket_two.send_text.call_args[0][0])
    assert sent["usernames"] == {"1": "Alice", "2": "Bob"}


# ---------------------------------------------------------------------------
# Edge cases: connect without player_number
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_connect_without_player_number() -> None:
    """Connecting without a player number should still add to the room."""
    manager = ConnectionManager()
    websocket = AsyncMock()

    await manager.connect("game1", websocket, player_number=None)

    assert websocket in manager._rooms["game1"]
    assert manager._connected_player_numbers("game1") == []


@pytest.mark.anyio
async def test_disconnect_unknown_game() -> None:
    """Disconnecting from an unknown game should not raise."""
    manager = ConnectionManager()
    websocket = AsyncMock()

    # Should not raise
    manager.disconnect("nonexistent", websocket)


@pytest.mark.anyio
async def test_disconnect_unknown_websocket_in_room() -> None:
    """Disconnecting a websocket not in the room should not raise (suppress ValueError)."""
    manager = ConnectionManager()
    ws_in_room = AsyncMock()
    ws_not_in_room = AsyncMock()

    await manager.connect("game1", ws_in_room, player_number=1)

    # Disconnect a WS that was never connected — should be silently suppressed
    manager.disconnect("game1", ws_not_in_room)
    assert ws_in_room in manager._rooms["game1"]


@pytest.mark.anyio
async def test_broadcast_to_empty_room() -> None:
    """Broadcasting to a nonexistent room should not raise."""
    manager = ConnectionManager()

    # Should not raise
    await manager.broadcast("nonexistent", {"type": "test"})


@pytest.mark.anyio
async def test_broadcast_removes_multiple_dead_connections() -> None:
    """Multiple dead connections should all be cleaned up during broadcast."""
    manager = ConnectionManager()
    ws_alive = AsyncMock()
    ws_dead1 = AsyncMock()
    ws_dead2 = AsyncMock()
    ws_dead1.send_text.side_effect = RuntimeError("closed")
    ws_dead2.send_text.side_effect = RuntimeError("closed")

    await manager.connect("game1", ws_alive, player_number=1)
    await manager.connect("game1", ws_dead1, player_number=2)
    # Add dead2 without player_number
    manager._rooms["game1"].append(ws_dead2)

    ws_alive.send_text.reset_mock()

    await manager.broadcast("game1", {"type": "test"})

    assert ws_dead1 not in manager._rooms.get("game1", [])
    assert ws_dead2 not in manager._rooms.get("game1", [])
    assert ws_alive in manager._rooms["game1"]


@pytest.mark.anyio
async def test_disconnect_and_notify_no_player_number() -> None:
    """disconnect_and_notify for a WS without a player_number should not broadcast status."""
    manager = ConnectionManager()
    ws1 = AsyncMock()  # no player number
    ws2 = AsyncMock()

    await manager.connect("game1", ws1, player_number=None)
    await manager.connect("game1", ws2, player_number=1)

    ws2.send_text.reset_mock()

    await manager.disconnect_and_notify("game1", ws1)

    # ws2 should NOT have received a player_status broadcast (no player_number to report)
    for call in ws2.send_text.call_args_list:
        msg = json.loads(call[0][0])
        if msg.get("type") == "player_status":
            assert msg.get("status") != "disconnected" or msg.get("player") is not None


# ---------------------------------------------------------------------------
# Bug 21 — room cleanup on last disconnect (memory leak fix)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_disconnect_last_player_cleans_up_all_room_state() -> None:
    """When all players leave, per-room dicts must be fully purged."""
    manager = ConnectionManager()
    ws1 = AsyncMock()
    ws2 = AsyncMock()

    await manager.connect("game-leak", ws1, player_number=1)
    await manager.connect("game-leak", ws2, player_number=2)

    # Inject some per-room state that should be cleaned up
    manager._usernames["game-leak"] = {1: "Alice", 2: "Bob"}
    manager._rematch_votes["game-leak"] = {1}
    manager._db_game_id["game-leak"] = "some-db-uuid"

    # First player leaves — room still has one occupant
    manager.disconnect("game-leak", ws1)
    assert "game-leak" in manager._rooms, "Room should persist while ws2 is still connected"

    # Last player leaves — room must be fully purged
    manager.disconnect("game-leak", ws2)

    assert "game-leak" not in manager._rooms, "_rooms must be deleted"
    assert "game-leak" not in manager._player_map, "_player_map must be deleted"
    assert "game-leak" not in manager._usernames, "_usernames must be deleted"
    assert "game-leak" not in manager._rematch_votes, "_rematch_votes must be deleted"
    assert "game-leak" not in manager._db_game_id, "_db_game_id must be deleted"


@pytest.mark.anyio
async def test_disconnect_nonexistent_room_does_not_raise() -> None:
    """Disconnecting a WS not in any room must be a safe no-op."""
    manager = ConnectionManager()
    ws = AsyncMock()

    # Should not raise
    manager.disconnect("no-such-room", ws)
