# Connect 4 Real-Time Prototype

[![CI](https://github.com/izu-x/connect4-realtime-prototype/actions/workflows/ci.yml/badge.svg)](https://github.com/izu-x/connect4-realtime-prototype/actions/workflows/ci.yml)
![Python 3.13](https://img.shields.io/badge/python-3.13-blue)
![Tests](https://img.shields.io/badge/tests-267%20passed%20%2B%20123%20e2e-brightgreen)
![Ruff](https://img.shields.io/badge/linter-ruff%200.15.4-purple)
![License](https://img.shields.io/badge/license-MIT-green)

> A prototype exploring the architectural gap between real-time state management
> (hot layer) and traceable event history (audit layer) — two concerns that are
> usually treated separately but interact in every move-based game.

<p align="center">
  <img src="docs/screenshot.png" alt="Connect 4 game in progress" width="720">
</p>

---

## Table of Contents

- [Connect 4 Real-Time Prototype](#connect-4-real-time-prototype)
  - [Table of Contents](#table-of-contents)
  - [Architecture](#architecture)
  - [Stack](#stack)
  - [Project Layout](#project-layout)
  - [Quick Start](#quick-start)
  - [Environment Variables](#environment-variables)
  - [API](#api)
    - [WebSocket Protocol](#websocket-protocol)
  - [Testing](#testing)
    - [E2E Browser Tests (Playwright)](#e2e-browser-tests-playwright)
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

Each layer is optimised for its access pattern: Redis for sub-millisecond reads during active play,
PostgreSQL for relational queries and leaderboard, and an append-only log as the immutable event record.

---

## Stack

| Layer     | Technology                        | Why                                                               |
| --------- | --------------------------------- | ----------------------------------------------------------------- |
| Runtime   | Python 3.13                       | Async-native (`asyncio`); rich ecosystem for web and data tooling |
| API       | FastAPI + Uvicorn                 | WebSocket support, auto-OpenAPI, Pydantic v2, non-blocking I/O    |
| Hot state | Redis 8                           | Sub-ms get/set; atomic `SETNX` distributed lock                   |
| Cold data | PostgreSQL 17                     | FK constraints, ACID transactions, complex leaderboard queries    |
| Audit     | JSONL file                        | Immutable append; directly ingestible by Spark / EMR / Flink      |
| Frontend  | Vanilla JS + CSS                  | Single-page app, WebSocket-driven, zero build step                |
| Infra     | Docker Compose / AWS CDK          | One-command local dev; Fargate + RDS + ElastiCache in production  |
| Quality   | Ruff 0.15.4 + pytest + Hypothesis | Linting, formatting, example + property-based test coverage       |
| CI/CD     | GitHub Actions                    | Lint, format check, and full test suite on every push             |

---

## Project Layout

```text
app/              # FastAPI backend — routes, game logic, Redis/DB layers
  routes/         #   REST endpoints (games, players, matchmaking)
static/           # Single-page frontend (vanilla JS + CSS, zero build step)
tests/
  unit/           # Pure logic: game, models, store, audit, ELO (incl. Hypothesis)
  integration/    # Full HTTP/WS journeys with FakeRedis + mock DB
  e2e/            # Playwright browser tests (opt-in via --e2e)
infra/            # AWS CDK stack (Fargate + RDS + ElastiCache)
alembic/          # Database migrations
docs/             # Architecture rationale + deployment guide
```

---

## Quick Start

```bash
git clone https://github.com/izu-x/connect4-realtime-prototype.git
cd connect4-realtime-prototype
./setup.sh
```

| Mode                | What it does                                                 |
| ------------------- | ------------------------------------------------------------ |
| `./setup.sh docker` | Full stack in containers — zero local dependencies           |
| `./setup.sh native` | Python locally with hot-reload; Redis + PostgreSQL in Docker |
| `./setup.sh clean`  | Stop containers, remove volumes and `.venv`                  |

Open **<http://localhost:8000>** to play, or **<http://localhost:8000/docs>** for the interactive API explorer.

> **Mise users:** The project includes a `.mise.toml` that auto-selects Python 3.13 and activates `.venv`.
> If `mise` is installed, `./setup.sh native` will configure the shell hook automatically.
> For manual setup: `mise trust && eval "$(mise activate zsh)"` (or replace `zsh` with your shell).

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

| Variable           | Default                                                      | Description                                  |
| ------------------ | ------------------------------------------------------------ | -------------------------------------------- |
| `DATABASE_URL`     | `postgresql+asyncpg://user:password@localhost:5432/connect4` | PostgreSQL async connection string           |
| `REDIS_URL`        | `redis://localhost:6379`                                     | Redis connection string                      |
| `GAME_TTL_SECONDS` | `86400`                                                      | Board TTL in Redis (seconds); default = 24 h |

> In AWS deployments, database and Redis credentials are injected by CDK from Secrets Manager at runtime.

---

## API

Full REST API documentation is auto-generated — run the server and visit [`/docs`](http://localhost:8000/docs) for the interactive OpenAPI reference.

### WebSocket Protocol

Connect to `ws://host/ws/{game_id}` and exchange JSON messages:

| Direction       | Message                                                     | Description                               |
| --------------- | ----------------------------------------------------------- | ----------------------------------------- |
| Client → Server | `{"player": 1, "column": 3}`                                | Make a move                               |
| Client → Server | `{"action": "identify", "player": 1, "username": "alice"}`  | Bind identity to connection               |
| Client → Server | `{"action": "rematch", "player": 1}`                        | Vote for rematch (2 votes triggers reset) |
| Server → Client | `{"player": 1, "column": 3, "row": 5, "board": [...], ...}` | Move broadcast                            |
| Server → Client | `{"rematch": true}`                                         | Rematch accepted — both clients reset     |
| Server → Client | `{"rematch_waiting": true}`                                 | Opponent voted, waiting for your vote     |
| Server → Client | `{"type": "player_status", ...}`                            | Presence notification                     |

> The first move from a WebSocket connection permanently binds that socket to the sending player number. Use separate connections for each player.

---

## Testing

```bash
pytest -v                  # 267 unit + integration tests, ~2 s, no external deps
pytest tests/unit/         # Unit tests only (includes property-based / Hypothesis)
pytest tests/integration/  # Integration tests only
pytest --e2e -v tests/e2e/ # 123 E2E browser tests (requires docker compose up)
```

All unit and integration tests run against an **in-process FakeRedis** and **mock DB sessions** — no external services required.

| Suite         | Tests | What it covers                                                           |
| ------------- | ----- | ------------------------------------------------------------------------ |
| Unit          | 127   | Game logic (incl. Hypothesis), models, store, audit, ELO, connection mgr |
| Integration   | 140   | Full HTTP/WS journeys, matchmaking pipelines, concurrent moves, recovery |
| E2E (browser) | 123   | Playwright browser tests: real clicks, screen flows, refresh recovery    |

### E2E Browser Tests (Playwright)

End-to-end tests drive a real Chromium browser against the running application.
They cover user flows that unit/integration tests cannot: CSS animations,
screen transitions, WebSocket-driven updates across two browser tabs,
page refresh recovery, and session persistence.

```bash
# 1. Install E2E dependencies + browser
pip install -e ".[dev,e2e]"
playwright install chromium

# 2. Start the full stack
docker compose up --build -d

# 3. Run E2E tests
pytest --e2e -v tests/e2e/

# Run a specific E2E test
pytest --e2e -v tests/e2e/test_game_lobby.py::test_matchmaking_cancel_and_retry_spinner_animates
```

E2E tests are **opt-in** — they require the `--e2e` flag and a running server.
Without `--e2e`, they are automatically skipped so `pytest -v` remains fast
and dependency-free.

<details>
<summary>What the E2E suite covers (123 tests)</summary>

- **Lobby (13)** — registration, validation, duplicate username error,
  live stats polling, leaderboard entries, form elements
- **Game lobby (31)** — create, cancel, join, matchmaking cancel/retry (regression),
  leaderboard toggle, player stats card, game history with results/replay buttons,
  auto-refresh, re-entry state reset
- **Gameplay (47)** — two-player moves, vertical/horizontal/diagonal wins,
  draw detection, full-column rejection, winning cell highlights,
  confetti (winner-only), game-over text, last-move indicator,
  active-turn card, countdown ring, idle taunts,
  rematch flow (waiting/accepted/fallback), WS disconnect status,
  board-disabled class, toast notifications,
  hover indicators (clear/disabled on game-over),
  leave cleanup (confetti/toasts),
  player card CSS classes (is-me/online/offline/color)
- **Replay (14)** — board rendering, next/prev/start/end controls,
  button disabled states, slider, last-move highlight, legend, back-to-lobby
- **Navigation (18)** — page refresh on every screen, session recovery,
  corrupt sessionStorage handling, screen-transition invariant (single active screen),
  quick-play URL params, stats polling lifecycle, finished-game resume

</details>

---

## Development

```bash
pip install -e ".[dev]"    # Install with dev dependencies
pip install -e ".[e2e]"    # Install E2E test dependencies (Playwright)

ruff check app/ tests/     # Lint
ruff format app/ tests/    # Format

pytest -v                  # Run unit + integration tests
pytest --e2e -v            # Run E2E browser tests (requires docker compose up)

alembic upgrade head       # Apply database migrations
alembic revision --autogenerate -m "description"  # Generate migration
```

Pre-commit hooks (trailing whitespace, YAML/TOML checks, ruff, pytest) are configured — run `pre-commit install` once after cloning to enable them.

---

## AWS Deployment

```bash
cdk deploy --profile <your-profile> --context free_tier=true
```

Deploys to ECS Fargate with RDS PostgreSQL and ElastiCache Redis. See [`docs/AWS_DEPLOYMENT.md`](docs/AWS_DEPLOYMENT.md) for full details.

---

## Trade-offs

| Decision                       | Trade-off                             | Production path                             |
| ------------------------------ | ------------------------------------- | ------------------------------------------- |
| File-based audit log           | Not durable across container restarts | Kafka / Kinesis producer; same event schema |
| SETNX lock (single Redis node) | Correct for single-node only          | Redlock for multi-node HA                   |
| JSON board serialisation       | Human-readable, ~120 B                | MessagePack for ~5x smaller payload         |
| In-memory `ConnectionManager`  | Breaks with multiple app instances    | Redis Pub/Sub fan-out per game channel      |
| Vanilla JS frontend            | No build step, fast iteration         | React/Vue for complex UI state              |

For full architecture rationale see [`docs/TECHNICAL_DECISIONS.md`](docs/TECHNICAL_DECISIONS.md).
