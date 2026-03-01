# Connect 4 Real-Time Prototype

[![CI](https://github.com/izu-x/connect4-realtime-prototype/actions/workflows/ci.yml/badge.svg)](https://github.com/izu-x/connect4-realtime-prototype/actions/workflows/ci.yml)
![Python 3.13](https://img.shields.io/badge/python-3.13-blue)
![Tests](https://img.shields.io/badge/tests-267%20passed-brightgreen)
![Ruff](https://img.shields.io/badge/linter-ruff%200.15.4-purple)
![License](https://img.shields.io/badge/license-MIT-green)

> A prototype exploring the architectural gap between real-time state management (hot layer) and traceable event history (audit layer) — two concerns that are usually treated separately but interact in every move-based game.

---

## Table of Contents

- [Architecture](#architecture)
- [Stack](#stack)
- [Project Layout](#project-layout)
- [Quick Start](#quick-start)
- [Environment Variables](#environment-variables)
- [API Reference](#api-reference)
- [WebSocket Protocol](#websocket-protocol)
- [Testing](#testing)
- [Development](#development)
- [AWS Deployment](#aws-deployment)
- [Trade-offs](#trade-offs)

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
|-------|------------|-----|
| Runtime | Python 3.13 | Async-native (`asyncio`); rich ecosystem for web and data tooling |
| API | FastAPI + Uvicorn | WebSocket support, auto-OpenAPI, Pydantic v2, non-blocking I/O |
| Hot state | Redis 7 | Sub-ms get/set; atomic `SETNX` distributed lock |
| Cold data | PostgreSQL 17 | FK constraints, ACID transactions, complex leaderboard queries |
| Audit | JSONL file | Immutable append; directly ingestible by Spark / EMR / Flink |
| Frontend | Vanilla JS + CSS | Single-page app, WebSocket-driven, zero build step |
| Infra | Docker Compose / AWS CDK | One-command local dev; Fargate + RDS + ElastiCache in production |
| Quality | Ruff 0.15.4 + pytest + Hypothesis | Linting, formatting, example + property-based test coverage |
| CI/CD | GitHub Actions | Lint, format check, and full test suite on every push |

---

## Project Layout

```
app/
├── main.py               # FastAPI app creation, router includes, lifespan
├── game.py               # Connect 4 logic — pure Python, zero I/O
├── models.py             # Pydantic request/response schemas
├── store.py              # Redis persistence + SETNX locking
├── audit.py              # JSONL event logger (nanosecond timestamps)
├── connection_manager.py # WebSocket room/player tracking + presence
├── websocket.py          # WebSocket endpoint: move, identify, rematch
├── db_models.py          # SQLAlchemy ORM models (Player, Game, Move)
├── repository.py         # Database query functions (no commits inside)
├── database.py           # Async engine + session factory
└── routes/
    ├── games.py          # Game CRUD + board state REST endpoints
    ├── players.py        # Player registration, stats, ELO
    └── matchmaking.py    # ELO-band matchmaking queue

static/
├── index.html            # Single-page app shell
├── app.js                # All frontend logic (~1 700 lines)
└── style.css             # CSS variables + glassmorphism theme

tests/
├── conftest.py           # FakeRedis, mock DB, shared fixtures
├── unit/                 # Pure logic: game, models, store, audit, ELO
│   ├── test_game.py
│   ├── test_game_hypothesis.py   # Property-based tests (Hypothesis)
│   ├── test_models.py
│   ├── test_store.py
│   ├── test_audit.py
│   ├── test_connection_manager.py
│   └── test_elo_and_stats.py
└── integration/          # Full-stack journeys: HTTP → Redis → DB mock
    ├── test_api.py
    ├── test_integration.py
    ├── test_websocket_persistence.py
    ├── test_matchmaking.py
    ├── test_concurrent_moves.py
    └── test_presence_and_stale_games.py

infra/                    # AWS CDK stack — see docs/AWS_DEPLOYMENT.md
alembic/                  # Database migrations
docs/                     # Architecture rationale + deployment guide
```

---

## Quick Start

```bash
git clone https://github.com/izu-x/connect4-realtime-prototype.git
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

# Real-time WebSocket
wscat -c ws://localhost:8000/ws/my-game
# Send: {"player": 2, "column": 4}
```

</details>

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql+asyncpg://user:password@localhost:5432/connect4` | PostgreSQL async connection string |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection string |
| `GAME_TTL_SECONDS` | `86400` | Board TTL in Redis (seconds); default = 24 h |

> In AWS deployments, database and Redis credentials are injected by CDK from Secrets Manager at runtime.

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
| `POST` | `/matchmaking/join` | Enter the ELO-band matchmaking queue |
| `GET` | `/matchmaking/status/{player_id}` | Queue position or match result |
| `DELETE` | `/matchmaking/leave/{player_id}` | Leave the matchmaking queue |

### Platform

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/stats` | Live active game count and online player count |
| `POST` | `/heartbeat` | Record a presence heartbeat for a player |
| `GET` | `/docs` | Interactive OpenAPI reference |

### Move Payload

```json
{ "game_id": "abc-123", "player": 1, "column": 3 }
```

- `player`: `1` or `2`
- `column`: `0`–`6` (left to right)

---

## WebSocket Protocol

Connect to `ws://host/ws/{game_id}` and exchange JSON messages:

| Direction | Message | Description |
|-----------|---------|-------------|
| Client → Server | `{"player": 1, "column": 3}` | Make a move |
| Client → Server | `{"action": "identify", "player": 1, "username": "alice"}` | Bind identity to connection |
| Client → Server | `{"action": "rematch", "player": 1}` | Vote for rematch (2 votes triggers reset) |
| Server → Client | `{"player": 1, "column": 3, "row": 5, "board": [...], ...}` | Move broadcast |
| Server → Client | `{"rematch": true}` | Rematch accepted — both clients reset |
| Server → Client | `{"rematch_waiting": true}` | Opponent voted, waiting for your vote |
| Server → Client | `{"type": "player_status", ...}` | Presence notification |

> The first move from a WebSocket connection permanently binds that socket to the sending player number. Use separate connections for each player.

---

## Testing

```bash
pytest -v                  # 267 tests, ~2 s, no Redis/PostgreSQL needed
pytest tests/unit/         # Unit tests only
pytest tests/integration/  # Integration tests only
```

All tests run against an **in-process FakeRedis** and **mock DB sessions** — no external services required.

| Suite | Tests | What it covers |
|-------|-------|----------------|
| Unit | 97 | Game logic, Pydantic models, store ops, audit, ELO, connection manager |
| Property-based | 11 | Hypothesis-driven invariants on game logic |
| Integration | 159 | Full HTTP/WS journeys, matchmaking pipelines, concurrent moves, auto-recovery |

---

## Development

```bash
pip install -e ".[dev]"    # Install with dev dependencies

ruff check app/ tests/     # Lint
ruff format app/ tests/    # Format

pytest -v                  # Run all tests

alembic upgrade head       # Apply database migrations
alembic revision --autogenerate -m "description"  # Generate migration
```

Pre-commit hooks run automatically on `git commit`: trailing whitespace, YAML/TOML checks, ruff lint + format, and the full test suite.

---

## AWS Deployment

```bash
cdk deploy --profile <your-profile> --context free_tier=true
```

Deploys to ECS Fargate with RDS PostgreSQL and ElastiCache Redis. See [`docs/AWS_DEPLOYMENT.md`](docs/AWS_DEPLOYMENT.md) for full details.

---

## Trade-offs

| Decision | Trade-off | Production path |
|----------|-----------|-----------------|
| File-based audit log | Not durable across container restarts | Kafka / Kinesis producer; same event schema |
| SETNX lock (single Redis node) | Correct for single-node only | Redlock for multi-node HA |
| JSON board serialisation | Human-readable, ~120 B | MessagePack for ~5x smaller payload |
| In-memory `ConnectionManager` | Breaks with multiple app instances | Redis Pub/Sub fan-out per game channel |
| Vanilla JS frontend | No build step, fast iteration | React/Vue for complex UI state |

For full architecture rationale see [`docs/TECHNICAL_DECISIONS.md`](docs/TECHNICAL_DECISIONS.md).
