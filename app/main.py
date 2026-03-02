"""FastAPI application — lifespan, app factory, and router wiring."""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import Body, FastAPI, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import database as _db
from app.connection_manager import manager
from app.repository import cleanup_stale_games
from app.routes import games as games_router
from app.routes import matchmaking as matchmaking_router
from app.routes import players as players_router
from app.store import close_redis, get_redis
from app.websocket import router as ws_router

logger: logging.Logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:  # noqa: ANN001
    """Application lifespan handler — cleans stale state on startup, closes connections on shutdown."""
    async with _db.async_session_factory() as session:
        await cleanup_stale_games(session)
        await session.commit()

    # Clear stale matchmaking queue from previous session — those players are gone
    redis = await get_redis()
    stale_queue = await redis.zcard("matchmaking:queue")
    if stale_queue > 0:
        logger.info("Clearing %d stale player(s) from matchmaking queue", stale_queue)
    await redis.delete("matchmaking:queue")

    yield
    await close_redis()
    await _db.close_db()


app: FastAPI = FastAPI(
    title="Connect 4 Real-Time Prototype",
    description="Demonstrates real-time state management, event sourcing, and audit logging.",
    version="1.0.0",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# Static files & root page
# ---------------------------------------------------------------------------

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/", include_in_schema=False)
async def root() -> FileResponse:
    """Serve the single-page frontend."""
    return FileResponse("static/index.html")


# ---------------------------------------------------------------------------
# Lightweight top-level endpoints (no router needed)
# ---------------------------------------------------------------------------


@app.get("/stats", status_code=status.HTTP_200_OK)
async def live_stats() -> dict[str, int]:
    """Return live platform statistics: active games and online players.

    Active games come from WebSocket room tracking (source of truth).
    Online players come from heartbeat-based presence tracking —
    any player whose client has polled within the last 15 seconds.

    Returns:
        Dictionary with active_games and online_players counts.
    """
    active_games = sum(1 for conns in manager._rooms.values() if conns)
    online_players = manager.online_count()

    return {
        "active_games": active_games,
        "online_players": online_players,
    }


@app.post("/heartbeat", status_code=status.HTTP_204_NO_CONTENT)
async def heartbeat(player_id: str | None = Body(None, embed=True)) -> None:
    """Record a presence heartbeat for the given player.

    Called by the frontend stats poller every 5 seconds so the server
    knows how many players are actively using the app (not just in a game).

    Args:
        player_id: UUID string of the player (from request body).
    """
    if player_id:
        try:
            uuid.UUID(player_id)
        except ValueError:
            return
        manager.heartbeat(player_id)


# ---------------------------------------------------------------------------
# Include routers
# ---------------------------------------------------------------------------

app.include_router(games_router.router)
app.include_router(players_router.router)
app.include_router(matchmaking_router.router)
app.include_router(ws_router)
