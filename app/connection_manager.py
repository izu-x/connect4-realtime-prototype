"""WebSocket connection manager — tracks rooms, players, presence, and rematch votes."""

from __future__ import annotations

import json
import time
from contextlib import suppress
from typing import Any, Final

from fastapi import WebSocket

HEARTBEAT_TIMEOUT_SECONDS: Final[int] = 15


class ConnectionManager:
    """Track active WebSocket connections per game with player identity."""

    def __init__(self) -> None:
        self._rooms: dict[str, list[WebSocket]] = {}
        self._rematch_votes: dict[str, set[int]] = {}
        self._player_map: dict[str, dict[WebSocket, int]] = {}
        self._usernames: dict[str, dict[int, str]] = {}
        self._presence: dict[str, float] = {}  # player_id → last heartbeat timestamp
        self._db_game_id: dict[str, str] = {}  # ws game_id → current DB UUID (updated on rematch)

    # ------------------------------------------------------------------
    # Presence tracking
    # ------------------------------------------------------------------

    def heartbeat(self, player_id: str) -> None:
        """Record a heartbeat for presence tracking.

        Args:
            player_id: UUID string of the player.
        """
        self._presence[player_id] = time.monotonic()

    def online_count(self) -> int:
        """Return the number of players seen within the heartbeat window."""
        cutoff = time.monotonic() - HEARTBEAT_TIMEOUT_SECONDS
        # Prune expired entries while counting
        expired = [pid for pid, ts in self._presence.items() if ts < cutoff]
        for pid in expired:
            del self._presence[pid]
        return len(self._presence)

    # ------------------------------------------------------------------
    # Room management
    # ------------------------------------------------------------------

    async def connect(self, game_id: str, ws: WebSocket, player_number: int | None = None) -> None:
        """Accept and register a WebSocket connection for a game room.

        Args:
            game_id: Unique identifier of the game room.
            ws: WebSocket connection to register.
            player_number: Player identifier (1 or 2), if known at connect time.
        """
        await ws.accept()
        self._rooms.setdefault(game_id, []).append(ws)
        if player_number is not None:
            self._player_map.setdefault(game_id, {})[ws] = player_number
            await self.broadcast(
                game_id,
                {
                    "type": "player_status",
                    "player": player_number,
                    "status": "connected",
                    "connected_players": self._connected_player_numbers(game_id),
                    "usernames": self._usernames.get(game_id, {}),
                },
            )

    def _connected_player_numbers(self, game_id: str) -> list[int]:
        """Return sorted list of currently connected player numbers for a game.

        Args:
            game_id: Unique identifier of the game room.

        Returns:
            Sorted list of connected player numbers.
        """
        if game_id not in self._player_map:
            return []
        return sorted(set(self._player_map[game_id].values()))

    def disconnect(self, game_id: str, ws: WebSocket) -> None:
        """Remove a WebSocket connection from a game room.

        Args:
            game_id: Unique identifier of the game room.
            ws: WebSocket connection to remove.
        """
        if game_id in self._rooms:
            with suppress(ValueError):
                self._rooms[game_id].remove(ws)
            # When the room is fully empty, purge all per-room state to prevent
            # unbounded memory growth over many completed games.
            if not self._rooms[game_id]:
                del self._rooms[game_id]
                self._player_map.pop(game_id, None)
                self._usernames.pop(game_id, None)
                self._rematch_votes.pop(game_id, None)
                self._db_game_id.pop(game_id, None)
                return
        if game_id in self._player_map:
            self._player_map[game_id].pop(ws, None)

    async def disconnect_and_notify(self, game_id: str, ws: WebSocket) -> None:
        """Remove a connection and broadcast departure to remaining players.

        Args:
            game_id: Unique identifier of the game room.
            ws: WebSocket connection to remove.
        """
        player_number = self._player_map.get(game_id, {}).get(ws)
        self.disconnect(game_id, ws)
        if player_number is not None:
            await self.broadcast(
                game_id,
                {
                    "type": "player_status",
                    "player": player_number,
                    "status": "disconnected",
                    "connected_players": self._connected_player_numbers(game_id),
                    "usernames": self._usernames.get(game_id, {}),
                },
            )

    async def broadcast(self, game_id: str, message: dict[str, Any]) -> None:
        """Send a message to all active WebSocket connections in a game room.

        Args:
            game_id: Unique identifier of the game room.
            message: JSON-serialisable dictionary to broadcast.
        """
        dead: list[WebSocket] = []
        for ws in list(self._rooms.get(game_id, [])):
            try:
                await ws.send_text(json.dumps(message))
            except Exception:  # noqa: BLE001
                dead.append(ws)
        for ws in dead:
            self.disconnect(game_id, ws)


manager: ConnectionManager = ConnectionManager()
