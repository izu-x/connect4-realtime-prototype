#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# Connect 4 Real-Time Prototype — Local Setup Script
# ──────────────────────────────────────────────────────────────────────────────
#
# Usage:
#   ./setup.sh          → Interactive mode (choose Docker or native)
#   ./setup.sh docker   → Docker Compose (recommended — zero local deps)
#   ./setup.sh native   → Native Python with local/Docker Redis & PostgreSQL
#   ./setup.sh clean    → Stop containers and remove volumes
#
# Prerequisites:
#   Docker mode  → Docker Engine + Docker Compose v2
#   Native mode  → Python 3.13+, pip
# ──────────────────────────────────────────────────────────────────────────────

set -euo pipefail

# ── Colours & symbols ────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
DIM='\033[2m'
RESET='\033[0m'

ok()   { printf "${GREEN}✔${RESET} %s\n" "$1"; }
warn() { printf "${YELLOW}⚠${RESET} %s\n" "$1"; }
fail() { printf "${RED}✖${RESET} %s\n" "$1"; }
info() { printf "${BLUE}ℹ${RESET} %s\n" "$1"; }
step() { printf "\n${BOLD}${CYAN}── %s${RESET}\n" "$1"; }

# ── Banner ───────────────────────────────────────────────────────────────────
banner() {
    printf "${BOLD}${CYAN}"
    cat << 'EOF'

    ╔═══════════════════════════════════════════════════════╗
    ║                                                       ║
    ║     ██████╗ ██████╗ ███╗   ██╗███╗   ██╗             ║
    ║    ██╔════╝██╔═══██╗████╗  ██║████╗  ██║             ║
    ║    ██║     ██║   ██║██╔██╗ ██║██╔██╗ ██║             ║
    ║    ██║     ██║   ██║██║╚██╗██║██║╚██╗██║             ║
    ║    ╚██████╗╚██████╔╝██║ ╚████║██║ ╚████║             ║
    ║     ╚═════╝ ╚═════╝ ╚═╝  ╚═══╝╚═╝  ╚═══╝             ║
    ║          Connect 4 — Real-Time Prototype              ║
    ║                                                       ║
    ╚═══════════════════════════════════════════════════════╝

EOF
    printf "${RESET}"
}

# ── Prerequisite checks ─────────────────────────────────────────────────────
has_command() { command -v "$1" &> /dev/null; }

check_docker() {
    if ! has_command docker; then
        fail "Docker not found. Install it from https://docs.docker.com/get-docker/"
        return 1
    fi

    if ! docker info &> /dev/null; then
        fail "Docker daemon is not running. Start Docker and try again."
        return 1
    fi

    if ! docker compose version &> /dev/null; then
        fail "Docker Compose v2 not found. Update Docker or install the compose plugin."
        return 1
    fi

    ok "Docker $(docker --version | grep -oP '\d+\.\d+\.\d+')"
    ok "Docker Compose $(docker compose version --short)"
    return 0
}

check_python() {
    local python_cmd=""

    for cmd in python3.13 python3 python; do
        if has_command "$cmd"; then
            local version
            version=$("$cmd" --version 2>&1 | grep -oP '\d+\.\d+')
            local major minor
            major=$(echo "$version" | cut -d. -f1)
            minor=$(echo "$version" | cut -d. -f2)
            if [[ "$major" -ge 3 && "$minor" -ge 13 ]]; then
                python_cmd="$cmd"
                break
            fi
        fi
    done

    if [[ -z "$python_cmd" ]]; then
        fail "Python 3.13+ not found. Install it from https://www.python.org/downloads/"
        return 1
    fi

    ok "Python $($python_cmd --version 2>&1 | grep -oP '\d+\.\d+\.\d+')"
    echo "$python_cmd"
    return 0
}

# ── Wait for a service to be ready ──────────────────────────────────────────
wait_for_service() {
    local name="$1"
    local check_cmd="$2"
    local max_attempts="${3:-30}"
    local attempt=0

    printf "  Waiting for ${BOLD}%s${RESET}..." "$name"
    while ! eval "$check_cmd" &> /dev/null; do
        attempt=$((attempt + 1))
        if [[ $attempt -ge $max_attempts ]]; then
            printf " ${RED}timeout${RESET}\n"
            fail "$name did not become ready after $max_attempts attempts."
            return 1
        fi
        printf "."
        sleep 1
    done
    printf " ${GREEN}ready${RESET}\n"
}

# ── Fix stale PostgreSQL volumes ─────────────────────────────────────────────
check_postgres_volume() {
    # If a pgdata volume exists from an older PG major version, the new
    # container will refuse to start ("database files are incompatible").
    # Detect and offer to recreate it.
    local volume_name
    volume_name=$(docker compose config --volumes 2>/dev/null | grep -m1 pgdata || true)

    if [[ -z "$volume_name" ]]; then
        return 0
    fi

    local full_volume
    full_volume=$(docker volume ls -q | grep pgdata || true)

    if [[ -z "$full_volume" ]]; then
        return 0
    fi

    # Quick check: start postgres and see if it crashes immediately
    docker compose up -d postgres 2>/dev/null
    sleep 3

    local pg_status
    pg_status=$(docker compose ps postgres --format '{{.Status}}' 2>/dev/null || true)

    if echo "$pg_status" | grep -qi "exited\|dead"; then
        local logs
        logs=$(docker compose logs postgres --tail 5 2>/dev/null)
        if echo "$logs" | grep -qi "incompatible"; then
            warn "PostgreSQL data volume was created by an older version and is incompatible."
            info "Recreating volume (existing dev data will be lost)..."
            docker compose down -v --remove-orphans 2>/dev/null
            ok "Stale volume removed — will be recreated on next start"
        fi
    else
        # Already running fine, stop it so the caller can start cleanly
        docker compose stop postgres 2>/dev/null
    fi
}

# ── Docker mode ──────────────────────────────────────────────────────────────
run_docker() {
    step "Checking prerequisites"
    check_docker || exit 1
    check_postgres_volume

    step "Building and starting containers"
    info "Running: docker compose up --build -d"
    docker compose up --build -d

    step "Waiting for services to be healthy"
    wait_for_service "PostgreSQL" "docker compose exec -T postgres pg_isready -U connect4"
    wait_for_service "Redis" "docker compose exec -T redis redis-cli ping"
    wait_for_service "API" "curl -sf http://localhost:8000/docs > /dev/null"

    print_success
}

# ── Native mode ──────────────────────────────────────────────────────────────
run_native() {
    step "Checking prerequisites"

    local python_output
    python_output=$(check_python) || exit 1
    local python_cmd
    python_cmd=$(echo "$python_output" | tail -1)

    # Check if Redis and PostgreSQL are available (via Docker or locally)
    local need_docker_services=false
    local redis_running=false
    local postgres_running=false

    if has_command redis-cli && redis-cli ping &> /dev/null; then
        ok "Redis (local, already running)"
        redis_running=true
    fi

    if has_command pg_isready && pg_isready -U connect4 &> /dev/null; then
        ok "PostgreSQL (local, already running)"
        postgres_running=true
    fi

    if ! $redis_running || ! $postgres_running; then
        info "Redis and/or PostgreSQL not running locally — starting via Docker..."
        check_docker || {
            fail "Docker is needed to run Redis and PostgreSQL. Install them locally or install Docker."
            exit 1
        }
        check_postgres_volume
        need_docker_services=true
    fi

    # Start backing services via Docker if needed
    if $need_docker_services; then
        step "Starting backing services (Redis + PostgreSQL)"
        docker compose up -d postgres redis
        wait_for_service "PostgreSQL" "docker compose exec -T postgres pg_isready -U connect4"
        wait_for_service "Redis" "docker compose exec -T redis redis-cli ping"
    fi

    # Create virtual environment
    step "Setting up Python virtual environment"
    if [[ ! -d ".venv" ]]; then
        info "Creating virtual environment (.venv)..."
        "$python_cmd" -m venv .venv
        ok "Virtual environment created"
    else
        ok "Virtual environment already exists"
    fi

    # shellcheck disable=SC1091
    source .venv/bin/activate
    ok "Activated .venv ($(python --version))"

    # Install dependencies
    step "Installing dependencies"
    pip install --quiet --upgrade pip
    pip install --quiet -e ".[dev]"
    ok "All dependencies installed"

    # Run migrations
    step "Running database migrations"
    alembic upgrade head
    ok "Migrations applied"

    # Run linter
    step "Running code quality checks"
    if ruff check app/ tests/ --quiet; then
        ok "Ruff lint passed"
    else
        warn "Ruff found issues (non-blocking)"
    fi

    # Run tests
    step "Running tests"
    if pytest -v --tb=short 2>&1; then
        ok "All tests passed"
    else
        warn "Some tests failed (non-blocking — the app will still start)"
    fi

    # Start the app
    step "Starting the application"
    info "Running: uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload"
    echo ""

    print_success

    echo ""
    info "Starting Uvicorn with hot-reload enabled..."
    info "Press ${BOLD}Ctrl+C${RESET} to stop."
    echo ""
    uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
}

# ── Clean mode ───────────────────────────────────────────────────────────────
run_clean() {
    step "Stopping containers and cleaning up"

    if has_command docker && docker info &> /dev/null; then
        docker compose down -v --remove-orphans 2> /dev/null && ok "Containers stopped and volumes removed" || warn "No containers to stop"
    fi

    if [[ -d ".venv" ]]; then
        rm -rf .venv
        ok "Virtual environment removed"
    fi

    if [[ -f "events.log" ]]; then
        rm -f events.log
        ok "Audit log removed"
    fi

    ok "Clean complete"
}

# ── Success message ──────────────────────────────────────────────────────────
print_success() {
    printf "\n"
    printf "${GREEN}${BOLD}"
    printf "    ┌─────────────────────────────────────────────────┐\n"
    printf "    │         🎮  Connect 4 is ready to play!         │\n"
    printf "    └─────────────────────────────────────────────────┘\n"
    printf "${RESET}\n"
    printf "    ${BOLD}App${RESET}          http://localhost:8000\n"
    printf "    ${BOLD}API Docs${RESET}     http://localhost:8000/docs\n"
    printf "    ${BOLD}WebSocket${RESET}    ws://localhost:8000/ws/{game_id}\n"
    printf "\n"
    printf "    ${DIM}Quick test:${RESET}\n"
    printf "    ${DIM}curl -X POST http://localhost:8000/games/test/move \\${RESET}\n"
    printf "    ${DIM}  -H 'Content-Type: application/json' \\${RESET}\n"
    printf "    ${DIM}  -d '{\"game_id\": \"test\", \"player\": 1, \"column\": 3}'${RESET}\n"
    printf "\n"
}

# ── Interactive mode ─────────────────────────────────────────────────────────
run_interactive() {
    echo ""
    printf "  ${BOLD}How would you like to run the project?${RESET}\n\n"
    printf "    ${CYAN}1)${RESET} ${BOLD}Docker${RESET}  — Full stack in containers ${DIM}(recommended, no local deps)${RESET}\n"
    printf "    ${CYAN}2)${RESET} ${BOLD}Native${RESET}  — Python locally, Redis + PostgreSQL in Docker ${DIM}(hot-reload)${RESET}\n"
    printf "    ${CYAN}3)${RESET} ${BOLD}Clean${RESET}   — Stop everything and remove volumes\n"
    echo ""
    printf "  Enter choice [1/2/3]: "
    read -r choice

    case "$choice" in
        1) run_docker ;;
        2) run_native ;;
        3) run_clean ;;
        *)
            fail "Invalid choice. Usage: ./setup.sh [docker|native|clean]"
            exit 1
            ;;
    esac
}

# ── Main ─────────────────────────────────────────────────────────────────────
main() {
    banner

    local mode="${1:-interactive}"

    case "$mode" in
        docker)      run_docker ;;
        native)      run_native ;;
        clean)       run_clean ;;
        interactive) run_interactive ;;
        -h|--help)
            echo "Usage: ./setup.sh [docker|native|clean]"
            echo ""
            echo "  docker   Full stack in Docker Compose (recommended)"
            echo "  native   Python locally with backing services in Docker"
            echo "  clean    Stop containers, remove volumes and .venv"
            echo ""
            echo "Run without arguments for interactive mode."
            exit 0
            ;;
        *)
            fail "Unknown mode: $mode"
            echo "Usage: ./setup.sh [docker|native|clean]"
            exit 1
            ;;
    esac
}

main "$@"
