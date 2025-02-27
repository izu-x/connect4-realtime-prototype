# Connect 4 Real-Time Prototype

> A prototype exploring the architectural gap between real-time state management (hot layer) and traceable event history (audit layer) — two concerns that are usually treated separately but interact in every move-based game.

Demonstrates production-grade patterns for real-time stateful services.

---

## Architecture

```mermaid
flowchart LR
    Client["🌐 Browser"]

    subgraph Compute["FastAPI · Uvicorn"]
        API["REST + WebSocket\nPydantic validation\nSETNX locks"]
    end

    subgraph Hot["🔴 Hot Layer"]
        Redis[("Redis\nBoard state\nTTL: 24 h")]
    end

    subgraph Cold["🔵 Cold Layer"]
        PG[("PostgreSQL\nPlayers · Games\nLeaderboard · ELO")]
    end

    subgraph Audit["🟢 Audit Layer"]
        Log["JSONL\nAppend-only\nevents.log"]
    end

    Client -- "REST / WebSocket" --> API
    API -- "read · write · lock" --> Redis
    API -- "players · games · moves" --> PG
    API -- "fire-and-forget append" --> Log
```

Each layer is optimised for its access pattern: Redis for sub-millisecond reads during active play, PostgreSQL for relational queries and leaderboard, and an append-only log as the immutable event record.

---

## Stack

| Layer | Technology | Why |
|---|---|---|
| Runtime | Python 3.13 | Async-native (`asyncio`); rich ecosystem for web and data tooling |
| API | FastAPI + Uvicorn | WebSocket support, auto-OpenAPI, Pydantic v2, non-blocking I/O |
| Hot state | Redis 7 | Sub-ms get/set; atomic `SETNX` distributed lock |
| Cold data | PostgreSQL 17 | FK constraints, ACID transactions, complex leaderboard queries |
| Audit | JSONL file | Immutable append; directly ingestible by Spark / EMR / Flink |
| Infra | Docker Compose | One-command reproducible environment |
| Quality | Ruff + pytest | Linting, formatting, critical-path test coverage |

---

## Project Layout

```
app/
├── game.py               # Connect 4 logic — pure Python, no I/O
├── models.py             # Pydantic request/response schemas
├── store.py              # Redis persistence + SETNX locking
├── audit.py              # JSONL event logger
├── connection_manager.py # WebSocket room tracking + presence
├── websocket.py          # WebSocket route + broadcast handler
├── db_models.py          # SQLAlchemy ORM models
├── repository.py         # Database query layer
├── database.py           # Async session factory
└── routes/
    ├── games.py          # REST game endpoints
    ├── players.py        # Player registration + ELO
    └── matchmaking.py    # Matchmaking queue

tests/     # pytest suite — no live services required (in-process fakes)
infra/     # AWS CDK stack (Python) — see docs/AWS_DEPLOYMENT.md
docs/      # Architecture rationale and deployment guide
```

---

## Quick Start

```bash
git clone <repo-url>
cd connect4-realtime-prototype
./setup.sh
```

| Mode | What it does |
|------|-------------|
| `./setup.sh docker` | Full stack in containers — zero local dependencies |
| `./setup.sh native` | Python locally with hot-reload; Redis + PostgreSQL in Docker |
| `./setup.sh clean` | Stop containers, remove volumes and `.venv` |

Open **http://localhost:8000** to play, or **http://localhost:8000/docs** for the interactive API explorer.

<details>
<summary>Manual (Docker Compose only)</summary>

```bash
docker compose up --build

# Make a move — player 1 drops into column 3
curl -X POST http://localhost:8000/games/my-game/move \
  -H "Content-Type: application/json" \
  -d '{"game_id": "my-game", "player": 1, "column": 3}'

# Current board state
curl http://localhost:8000/games/my-game

# Real-time WebSocket (send JSON: {"player": 2, "column": 4})
wscat -c ws://localhost:8000/ws/my-game
```

</details>
---

## Environment Variables

Copy `.env.example` to `.env` and adjust values for local development:

```bash
cp .env.example .env
```

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://user:password@localhost:5432/connect4` | PostgreSQL async connection string |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection string |
| `GAME_TTL_SECONDS` | `86400` | Board TTL in Redis (seconds); default = 24 h |

> In AWS deployments, `DB_HOST`, `DB_USERNAME`, `DB_PASSWORD`, `DB_NAME`, `DB_PORT`, and `REDIS_URL` are injected by CDK from Secrets Manager at runtime — no manual configuration needed.

---

## API Reference

### Games

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/games` | Create a new game (returns `WAITING` status) |
| `POST` | `/games/{game_id}/join` | Join a waiting game as player 2 |
| `POST` | `/games/{game_id}/move` | Make a move; returns board state, winner, and winning cells |
| `GET` | `/games/{game_id}` | Current board state from Redis |
| `GET` | `/games/{game_id}/status` | Game + player metadata from PostgreSQL |
| `GET` | `/games/{game_id}/moves` | Ordered move history for full replay |
| `GET` | `/games/recent` | Recently finished games (default 10, max 100) |
| `GET` | `/games/waiting` | Games waiting for a second player |
| `DELETE` | `/games/{game_id}/cancel` | Cancel a waiting game (creator only) |

### Players

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/players` | Register a new player |
| `GET` | `/players/{player_id}/stats` | ELO rating + win/loss/draw record |
| `GET` | `/players/{player_id}/active-game` | Currently active game for a player |
| `GET` | `/players/{player_id}/games` | Full game history for a player |
| `GET` | `/leaderboard` | Top players ranked by ELO |

### Matchmaking

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/matchmaking/join` | Enter the matchmaking queue |
| `DELETE` | `/matchmaking/leave/{player_id}` | Leave the matchmaking queue |
| `GET` | `/matchmaking/status/{player_id}` | Your current queue position and size |

### Real-time

| Protocol | Endpoint | Description |
|----------|----------|-------------|
| `WS` | `/ws/{game_id}` | Game room — broadcasts every move to all participants |

### Platform

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/stats` | Live active game count and online player count |
| `POST` | `/heartbeat` | Record a presence heartbeat for a player |
| `GET` | `/docs` | Interactive OpenAPI reference |

### Payloads

**Make a move:**
```json
{ "game_id": "abc-123", "player": 1, "column": 3 }
```
- `player`: `1` or `2`
- `column`: `0` – `6` (left to right)

**WebSocket — make a move:**
```json
{ "player": 1, "column": 3 }
```

**WebSocket — request rematch:**
```json
{ "action": "rematch", "player": 1 }
```
Once both players send this, the server creates a new game, keeps the same WebSocket `game_id` (room), and broadcasts `{ "rematch": true }` so clients can reset their local state.

**Move response:**
```json
{
  "game_id": "abc-123",
  "player": 1,
  "column": 3,
  "row": 5,
  "board": [[0,0,...], ...],
  "winner": null,
  "draw": false,
  "winning_cells": []
}
```

---

## Development

```bash
pip install -e ".[dev]"

ruff check app/ tests/   # lint
ruff format app/ tests/  # format

pytest -v  # no Redis or PostgreSQL required
```

---

## API

| Endpoint | Method | Description |
|---|---|---|
| `/games/{game_id}/move` | `POST` | Make a move; returns full board + win/draw flags |
| `/games/{game_id}` | `GET` | Current board state |
| `/ws/{game_id}` | `WS` | Real-time game room — broadcasts every move to all participants |
| `/players` | `POST` | Register a player |
| `/players/{id}/stats` | `GET` | ELO rating + win/loss record |
| `/matchmaking/join` | `POST` | Enter the matchmaking queue |
| `/docs` | `GET` | Interactive OpenAPI reference |

**Move payload**: `{"player": 1, "column": 3}` — column `0–6`, player `1 or 2`.

**WebSocket rematch**: send `{"action": "rematch", "player": 1}` (both players must vote).

---

## Trade-offs

| Decision | Trade-off | Production path |
|---|---|---|
| File-based audit log | Not durable across container restarts | Kafka / Kinesis producer; same event schema |
| SETNX lock (single Redis node) | Correct for single-node only | Redlock for multi-node HA |
| JSON board serialisation | Human-readable, ~120 B | MessagePack for ~5× smaller payload |
| In-memory `ConnectionManager` | Breaks with multiple app instances | Redis Pub/Sub fan-out per game channel |

For full architecture rationale see [`docs/TECHNICAL_DECISIONS.md`](docs/TECHNICAL_DECISIONS.md).
For AWS deployment see [`docs/AWS_DEPLOYMENT.md`](docs/AWS_DEPLOYMENT.md).
