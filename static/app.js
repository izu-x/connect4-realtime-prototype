/* ================================================================== */
/* Connect 4 — Frontend logic                                         */
/* ================================================================== */

const API = window.location.origin;
const WS_BASE = `${window.location.protocol === "https:" ? "wss" : "ws"}://${window.location.host}`;

const ROWS = 6;
const COLS = 7;

/* ------------------------------------------------------------------ */
/* State                                                              */
/* ------------------------------------------------------------------ */

const state = {
    playerId: null,
    username: null,
    gameId: null,
    myPlayer: null,       // 1 or 2
    currentTurn: 1,       // whose turn it is
    board: null,
    ws: null,
    gameOver: false,
    pollTimer: null,
    statsTimer: null,
    matchmakingTimer: null,
    gamesRefreshTimer: null,
    gameHistory: [],
    connectedPlayers: [],
    playerUsernames: {},  // {1: "Alice", 2: "Bob"}
    wsReconnectAttempts: 0,
};

/* Persist key state fields across page reloads */
function saveState() {
    sessionStorage.setItem("c4state", JSON.stringify({
        playerId: state.playerId,
        username: state.username,
        gameId: state.gameId,
        myPlayer: state.myPlayer,
    }));
}

function loadState() {
    try {
        const saved = JSON.parse(sessionStorage.getItem("c4state"));
        if (saved) {
            state.playerId = saved.playerId;
            state.username = saved.username;
            state.gameId = saved.gameId;
            state.myPlayer = saved.myPlayer;
        }
    } catch { /* ignore corrupt data */ }
}

/* ------------------------------------------------------------------ */
/* DOM references                                                     */
/* ------------------------------------------------------------------ */

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

const screenLobby = $("#screen-lobby");
const screenGames = $("#screen-games");
const screenGame = $("#screen-game");
const screenReplay = $("#screen-replay");

/* ------------------------------------------------------------------ */
/* Screen navigation                                                  */
/* ------------------------------------------------------------------ */

function showScreen(screen) {
    $$(".screen").forEach((s) => s.classList.remove("active"));
    screen.classList.add("active");
}

/* ------------------------------------------------------------------ */
/* API helpers                                                        */
/* ------------------------------------------------------------------ */

async function api(method, path, body) {
    const opts = {
        method,
        headers: { "Content-Type": "application/json" },
    };
    if (body) opts.body = JSON.stringify(body);

    const res = await fetch(`${API}${path}`, opts);
    if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(err.detail || "Request failed");
    }
    /* 204 No Content — valid success with no body; return null instead of crashing */
    if (res.status === 204) return null;
    return res.json();
}

/* ================================================================== */
/* Live Stats                                                         */
/* ================================================================== */

async function refreshStats() {
    try {
        /* Send heartbeat so the server counts us as "online" */
        if (state.playerId) {
            fetch(`${API}/heartbeat`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ player_id: state.playerId }),
            }).catch(() => { });
        }
        const stats = await api("GET", "/stats");
        $("#stat-games").textContent = stats.active_games;
        $("#stat-players").textContent = stats.online_players;
        /* Also update game-lobby stats card if present */
        const glGames = document.getElementById("gl-stat-games");
        const glPlayers = document.getElementById("gl-stat-players");
        if (glGames) glGames.textContent = stats.active_games;
        if (glPlayers) glPlayers.textContent = stats.online_players;
    } catch { /* non-critical */ }
}

function startStatsPolling() {
    refreshStats();
    clearInterval(state.statsTimer);
    state.statsTimer = setInterval(refreshStats, 5000);
}

function stopStatsPolling() {
    clearInterval(state.statsTimer);
}

/* ================================================================== */
/* SCREEN 1 — Lobby                                                   */
/* ================================================================== */

async function loadLeaderboard() {
    try {
        const entries = await api("GET", "/leaderboard?limit=10");
        const list = $("#leaderboard-list");
        list.innerHTML = entries.length
            ? entries
                .map(
                    (e, i) =>
                        `<li><span>${escapeHtml(e.username)}</span><span class="lb-elo">${e.elo_rating}</span><span class="lb-games">${e.total_games}g</span></li>`
                )
                .join("")
            : '<li class="muted">No players yet</li>';
    } catch {
        /* ignore — leaderboard is non-critical */
    }
}

$("#form-register").addEventListener("submit", async (e) => {
    e.preventDefault();
    const username = $("#input-username").value.trim();
    const errorEl = $("#lobby-error");
    errorEl.textContent = "";
    if (!username) {
        errorEl.textContent = "Username cannot be empty.";
        return;
    }

    try {
        const player = await api("POST", "/players", { username });
        state.playerId = player.id;
        state.username = player.username;
        state.gameHistory = player.games || [];
        saveState();
        stopStatsPolling();
        enterGameLobby();
    } catch (err) {
        errorEl.textContent = err.message;
    }
});

/* ================================================================== */
/* SCREEN 2 — Game lobby                                              */
/* ================================================================== */

function enterGameLobby() {
    /* Cancel any active lobby / matchmaking polls from previous screens */
    clearInterval(state.pollTimer);
    clearInterval(state.matchmakingTimer);
    state.pollTimer = null;
    state.matchmakingTimer = null;

    /* Hide cards that may have been left visible from a previous lobbying session */
    $("#waiting-card").style.display = "none";
    $("#matchmaking-card").style.display = "none";

    /* Re-enable the Find Opponent button in case it was disabled */
    const mmBtn = $("#btn-matchmaking");
    if (mmBtn) { mmBtn.disabled = false; mmBtn.textContent = "Find Opponent"; }

    /* Restart live stats polling (stopped whenever we leave the lobby) */
    startStatsPolling();

    $("#display-username").textContent = state.username;
    showScreen(screenGames);
    refreshWaitingGames();
    startGamesAutoRefresh();
    loadPlayerStats();
    /* Hide leaderboard on re-entry so it starts collapsed */
    $("#lobby-leaderboard-card").style.display = "none";

    /* Always try to load game history from server */
    loadGameHistory();

    /* Check for an active in-progress game the player can rejoin */
    checkActiveGame();
}

async function loadPlayerStats() {
    const card = $("#player-stats-card");
    if (!state.playerId) {
        card.style.display = "none";
        return;
    }
    try {
        const stats = await api("GET", `/players/${state.playerId}/stats`);
        if (stats.total_games === 0) {
            card.style.display = "none";
            return;
        }
        card.style.display = "";
        const grid = $("#stats-grid");

        const streakIcon = stats.streak_type === "win" ? "\u{1F525}" :
            stats.streak_type === "loss" ? "\u{2744}\u{FE0F}" : "";
        const avgMins = stats.avg_game_duration_seconds > 0
            ? `${Math.floor(stats.avg_game_duration_seconds / 60)}m ${stats.avg_game_duration_seconds % 60}s`
            : "—";

        grid.innerHTML = `
            <div class="stat-item">
                <span class="stat-value">${stats.total_games}</span>
                <span class="stat-label">Games</span>
            </div>
            <div class="stat-item">
                <span class="stat-value stat-win">${stats.wins}</span>
                <span class="stat-label">Wins</span>
            </div>
            <div class="stat-item">
                <span class="stat-value stat-loss">${stats.losses}</span>
                <span class="stat-label">Losses</span>
            </div>
            <div class="stat-item">
                <span class="stat-value stat-draw">${stats.draws}</span>
                <span class="stat-label">Draws</span>
            </div>
            <div class="stat-item">
                <span class="stat-value">${stats.win_rate}%</span>
                <span class="stat-label">Win Rate</span>
            </div>
            <div class="stat-item">
                <span class="stat-value">${stats.elo_rating}</span>
                <span class="stat-label">ELO</span>
            </div>
            <div class="stat-item">
                <span class="stat-value">${avgMins}</span>
                <span class="stat-label">Avg Duration</span>
            </div>
            <div class="stat-item">
                <span class="stat-value">${stats.current_streak} ${streakIcon}</span>
                <span class="stat-label">Streak</span>
            </div>
        `;
    } catch {
        card.style.display = "none";
    }
}

async function loadGameHistory() {
    const historyCard = $("#history-card");
    if (!state.playerId) {
        historyCard.style.display = "none";
        return;
    }
    try {
        const games = await api("GET", `/players/${state.playerId}/games?limit=20`);
        state.gameHistory = games;
        if (games.length > 0) {
            historyCard.style.display = "";
            renderGameHistory(games);
        } else {
            historyCard.style.display = "none";
        }
    } catch {
        /* Use cached history if server request fails */
        if (state.gameHistory && state.gameHistory.length > 0) {
            historyCard.style.display = "";
            renderGameHistory(state.gameHistory);
        } else {
            historyCard.style.display = "none";
        }
    }
}

function renderGameHistory(games) {
    const container = $("#history-list");
    if (games.length === 0) {
        container.innerHTML = '<p class="muted">No games played yet</p>';
        return;
    }

    container.innerHTML = games
        .map((game) => {
            const isPlayer1 = game.player1_id === state.playerId;
            let result = "In progress";
            let resultClass = "";

            if (game.status === "finished") {
                if (game.winner_id === null || game.winner_id === undefined) {
                    /* Server-side cleanup set FINISHED with no winner — abandoned game */
                    result = "Abandoned";
                    resultClass = "";
                } else if (game.winner_id === state.playerId) {
                    result = "Won";
                    resultClass = "result-win";
                } else {
                    result = "Lost";
                    resultClass = "result-loss";
                }
            } else if (game.status === "draw") {
                result = "Draw";
                resultClass = "result-draw";
            } else if (game.status === "waiting") {
                result = "Waiting";
                resultClass = "";
            } else if (game.status === "playing") {
                /* Only label as "Abandoned" if it is NOT the current player's own active game.
                   isMyActiveGame is computed below; guard against showing "Abandoned" + Resume. */
                result = "Abandoned";
                resultClass = "result-loss";
            }

            /* Only allow resuming a game if it's actively waiting or in-progress
               AND it's the current player's game (not just any playing game) */
            const isMyActiveGame = (game.status === "playing" || game.status === "waiting") &&
                (game.player1_id === state.playerId || game.player2_id === state.playerId);

            /* Override the "Abandoned" label for the player's own still-active games */
            if (isMyActiveGame && game.status === "playing") {
                result = "In progress";
                resultClass = "";
            }
            const hasEnded = game.status === "finished" || game.status === "draw";
            const actionButton = isMyActiveGame
                ? `<button onclick="rejoinGame('${game.id}', ${isPlayer1 ? 1 : 2})" class="small rejoin-btn">▶ Resume</button>`
                : hasEnded
                    ? `<button onclick="replayGame('${game.id}')" class="small">Replay</button>`
                    : `<span class="muted">—</span>`;

            return `
                <div class="game-list-item history-item">
                    <span class="muted">${game.id.slice(0, 8)}…</span>
                    <span class="game-result ${resultClass}">${result}</span>
                    <span class="muted">P${isPlayer1 ? "1" : "2"}</span>
                    ${actionButton}
                </div>`;
        })
        .join("");
}

async function refreshWaitingGames() {
    const container = $("#waiting-games-list");
    try {
        const allWaiting = await api("GET", "/games/waiting?limit=50");
        const waiting = allWaiting.filter((g) => g.player1_id !== state.playerId);

        if (waiting.length === 0) {
            container.innerHTML = '<p class="muted">No open games. Create one!</p>';
            return;
        }

        container.innerHTML = waiting
            .map(
                (g) => `
      <div class="game-list-item">
        <span class="muted">${g.id.slice(0, 8)}…</span>
        <button onclick="joinGame('${g.id}')">Join</button>
      </div>`
            )
            .join("");
    } catch {
        container.innerHTML = '<p class="muted">Could not load games</p>';
    }
}

function startGamesAutoRefresh() {
    stopGamesAutoRefresh();
    state.gamesRefreshTimer = setInterval(refreshWaitingGames, 2000);
}

function stopGamesAutoRefresh() {
    clearInterval(state.gamesRefreshTimer);
    state.gamesRefreshTimer = null;
}

$("#btn-refresh-games").addEventListener("click", refreshWaitingGames);

/* Toggle leaderboard visibility in game lobby */
$("#btn-toggle-leaderboard").addEventListener("click", async () => {
    const card = $("#lobby-leaderboard-card");
    const arrow = $("#leaderboard-arrow");
    if (card.style.display === "none") {
        card.style.display = "";
        arrow.textContent = "▼";
        await loadLobbyLeaderboard();
    } else {
        card.style.display = "none";
        arrow.textContent = "▶";
    }
});

async function loadLobbyLeaderboard() {
    try {
        const entries = await api("GET", "/leaderboard?limit=10");
        const list = $("#lobby-leaderboard-list");
        list.innerHTML = entries.length
            ? entries
                .map(
                    (e, i) =>
                        `<li><span>${escapeHtml(e.username)}</span><span class="lb-elo">${e.elo_rating}</span><span class="lb-games">${e.total_games}g</span></li>`
                )
                .join("")
            : '<li class="muted">No players yet</li>';
    } catch {
        /* ignore — leaderboard is non-critical */
    }
}

/* Create game */
$("#btn-create-game").addEventListener("click", async () => {
    /* Cancel any pending matchmaking before creating a manual game */
    clearInterval(state.matchmakingTimer);
    state.matchmakingTimer = null;
    $("#matchmaking-card").style.display = "none";
    if (state.playerId) {
        api("DELETE", `/matchmaking/leave/${state.playerId}`).catch(() => { });
    }

    try {
        const game = await api("POST", "/games", { player1_id: state.playerId });
        state.gameId = game.id;
        state.myPlayer = 1;
        saveState();

        /* Show waiting state */
        $("#waiting-card").style.display = "";
        $("#waiting-game-id").textContent = game.id;

        /* Re-enable Find Opponent button if it was disabled */
        const mmBtn = $("#btn-matchmaking");
        if (mmBtn) { mmBtn.disabled = false; mmBtn.textContent = "Find Opponent"; }

        /* Poll until someone joins */
        pollForOpponent(game.id);
    } catch (err) {
        showToast("Error", "Failed to create game: " + err.message, "error");
    }
});

function pollForOpponent(gameId) {
    clearInterval(state.pollTimer);
    state.pollTimer = setInterval(async () => {
        try {
            const game = await api("GET", `/games/${gameId}/status`);
            if (game.status === "playing") {
                clearInterval(state.pollTimer);
                startGame(gameId);
            } else if (game.status === "finished" || game.status === "draw") {
                /* Game was cleaned up (server restart) before an opponent joined */
                clearInterval(state.pollTimer);
                state.pollTimer = null;
                state.gameId = null;
                state.myPlayer = null;
                saveState();
                $("#waiting-card").style.display = "none";
                showToast("Game Expired", "The waiting game no longer exists. Please create a new one.", "warning");
            }
        } catch (err) {
            /* Stop polling on 404 — the game was deleted (cancelled or cleaned up) */
            if (err.message && (err.message.includes("404") || err.message.toLowerCase().includes("not found"))) {
                clearInterval(state.pollTimer);
                state.pollTimer = null;
                state.gameId = null;
                state.myPlayer = null;
                saveState();
                $("#waiting-card").style.display = "none";
                showToast("Game Not Found", "The waiting game no longer exists. Please create a new one.", "warning");
            }
            /* Other errors (network blip) — keep polling */
        }
    }, 2000);
}

/* ================================================================== */
/* Matchmaking                                                        */
/* ================================================================== */

$("#btn-matchmaking").addEventListener("click", async () => {
    /* Cancel any pending opponent-wait poll before queuing */
    clearInterval(state.pollTimer);
    state.pollTimer = null;
    $("#waiting-card").style.display = "none";
    /* Cancel the orphaned WAITING game so it doesn't linger in the lobby */
    if (state.gameId && state.playerId) {
        api("DELETE", `/games/${state.gameId}/cancel?player_id=${state.playerId}`).catch(() => { });
    }
    state.gameId = null;
    state.myPlayer = null;
    saveState();

    /* Prevent re-click while the request is in flight */
    const btn = $("#btn-matchmaking");
    btn.disabled = true;
    btn.textContent = "Searching…";

    try {
        const result = await api("POST", "/matchmaking/join", { player1_id: state.playerId });

        if (result.status === "matched") {
            state.gameId = result.game_id;
            state.myPlayer = result.my_player;
            saveState();
            startGame(result.game_id);
            return;
        }

        /* Queued — show matchmaking card and poll */
        $("#matchmaking-card").style.display = "";
        pollMatchmaking();
    } catch (err) {
        btn.disabled = false;
        btn.textContent = "Find Opponent";
        showToast("Error", "Matchmaking failed: " + err.message, "error");
    }
});

function pollMatchmaking() {
    clearInterval(state.matchmakingTimer);
    const timerId = setInterval(async () => {
        /* Guard: if this timer was replaced or cancelled, bail out */
        if (state.matchmakingTimer !== timerId) return;
        try {
            const matchStatus = await api("GET", `/matchmaking/status/${state.playerId}`);
            /* Re-check after await — cancel or new session may have started */
            if (state.matchmakingTimer !== timerId) return;
            if (matchStatus.status === "matched") {
                /* Server found us a match */
                clearInterval(state.matchmakingTimer);
                state.matchmakingTimer = null;
                $("#matchmaking-card").style.display = "none";
                state.gameId = matchStatus.game_id;
                state.myPlayer = matchStatus.my_player;
                saveState();
                startGame(matchStatus.game_id);
            } else if (matchStatus.status === "queued") {
                $("#matchmaking-status").textContent =
                    `Searching\u2026 (${matchStatus.queue_size} player${matchStatus.queue_size !== 1 ? "s" : ""} in queue)`;
            } else {
                /* not_queued and no match — something went wrong, re-queue */
                clearInterval(state.matchmakingTimer);
                state.matchmakingTimer = null;
                $("#matchmaking-card").style.display = "none";
                /* Re-enable the button so the user can try again without reloading */
                const btn = $("#btn-matchmaking");
                if (btn) { btn.disabled = false; btn.textContent = "Find Opponent"; }
                showToast("Matchmaking", "Lost queue position. Please try again.", "warning");
            }
        } catch { /* keep polling */ }
    }, 2000);
    state.matchmakingTimer = timerId;
}

$("#btn-cancel-wait").addEventListener("click", async () => {
    clearInterval(state.pollTimer);
    state.pollTimer = null;
    const gameIdToCancel = state.gameId;
    state.gameId = null;
    state.myPlayer = null;
    saveState();
    $("#waiting-card").style.display = "none";
    if (gameIdToCancel && state.playerId) {
        try {
            await api("DELETE", `/games/${gameIdToCancel}/cancel?player_id=${state.playerId}`);
        } catch { /* best-effort — game will be cleaned up on server restart */ }
    }
});

$("#btn-cancel-matchmaking").addEventListener("click", async () => {
    clearInterval(state.matchmakingTimer);
    state.matchmakingTimer = null;
    /* Hide card immediately to prevent flash if a new session starts */
    $("#matchmaking-card").style.display = "none";
    /* Complete the server-side leave BEFORE re-enabling the button. This
       prevents a race where a stale DELETE removes a freshly-queued entry
       created by the next join click. */
    try {
        await api("DELETE", `/matchmaking/leave/${state.playerId}`);
    } catch { /* ignore */ }
    const btn = $("#btn-matchmaking");
    if (btn) { btn.disabled = false; btn.textContent = "Find Opponent"; }
});

/* Join game */
window.joinGame = async function (gameId) {
    try {
        await api("POST", `/games/${gameId}/join`, {
            player2_id: state.playerId,
        });
        state.gameId = gameId;
        state.myPlayer = 2;
        saveState();
        startGame(gameId);
    } catch (err) {
        showToast("Join failed", err.message || "Could not join game — it may have already started or been taken.", "error");
        refreshWaitingGames();
    }
};

/* ================================================================== */
/* SCREEN 3 — Game board                                              */
/* ================================================================== */

function startGame(gameId) {
    /* Kill any lobby polls before entering the game */
    clearInterval(state.pollTimer);
    clearInterval(state.matchmakingTimer);
    state.pollTimer = null;
    state.matchmakingTimer = null;

    stopGamesAutoRefresh();
    stopStatsPolling();
    state.gameId = gameId;
    state.currentTurn = 1;
    state.gameOver = false;
    state.moveCount = 0;
    state.lastMoveCell = null;
    /* Invalidate drop generation so in-flight animationend handlers
       from a previous game cannot affect the new board. */
    state.dropGeneration = (state.dropGeneration || 0) + 1;
    state.connectedPlayers = [];
    state.board = Array.from({ length: ROWS }, () => Array(COLS).fill(0));

    $("#waiting-card").style.display = "none";
    if ($("#matchmaking-card")) {
        $("#matchmaking-card").style.display = "none";
    }
    showScreen(screenGame);
    buildBoard();
    connectWebSocket(gameId);
    updateStatus();

    /* Pre-populate own name immediately so it never shows "Player X" */
    state.playerUsernames = {};
    if (state.username) {
        state.playerUsernames[state.myPlayer] = state.username;
    }
    updatePlayerCards([], state.playerUsernames);

    /* Fetch current board state (handles rejoin after disconnect) */
    restoreGameState(gameId);
}

async function restoreGameState(gameId) {
    try {
        const data = await api("GET", `/games/${gameId}`);
        if (data.board) {
            state.board = data.board;
            renderBoard(data.board);
            /* Figure out whose turn it is by counting pieces */
            const pieceCount = data.board.flat().filter((v) => v !== 0).length;
            state.currentTurn = pieceCount % 2 === 0 ? 1 : 2;
            if (data.winner) {
                state.gameOver = true;
                showGameOver(data.winner, data.winning_cells);
            } else if (data.draw) {
                state.gameOver = true;
                showGameOver(null, null);
            }
            updateStatus();
        }
    } catch { /* fresh game — no state yet */ }

    /* Fetch player names from the DB-backed status endpoint */
    try {
        const info = await api("GET", `/games/${gameId}/status`);
        const names = {};
        if (info.player1_name) names[1] = info.player1_name;
        if (info.player2_name) names[2] = info.player2_name;
        if (Object.keys(names).length > 0) {
            Object.assign(state.playerUsernames, names);
            updatePlayerCards(state.connectedPlayers, state.playerUsernames);
        }
    } catch { /* status endpoint unavailable — names will arrive via WS */ }
}

/* Build the board grid */
function buildBoard() {
    const boardEl = $("#board");
    const hoverEl = $("#hover-row");

    boardEl.innerHTML = "";
    hoverEl.innerHTML = "";

    /* Hover indicators */
    for (let c = 0; c < COLS; c++) {
        const ind = document.createElement("div");
        ind.className = "hover-indicator";
        ind.dataset.col = c;
        hoverEl.appendChild(ind);
    }

    /* Board cells — with diagonal stagger entrance */
    for (let r = 0; r < ROWS; r++) {
        for (let c = 0; c < COLS; c++) {
            const cell = document.createElement("div");
            cell.className = "cell";
            cell.dataset.row = r;
            cell.dataset.col = c;

            /* Stagger delay increases diagonally from top-left */
            const delayMs = (r + c) * 22;
            cell.style.animation = `cellEnter 0.35s cubic-bezier(0.34, 1.56, 0.64, 1) ${delayMs}ms backwards`;

            cell.addEventListener("click", () => handleCellClick(c));
            cell.addEventListener("mouseenter", () => highlightColumn(c));
            cell.addEventListener("mouseleave", clearColumnHighlight);

            boardEl.appendChild(cell);
        }
    }
}

/* Render current board state onto existing cells */
function renderBoard(board) {
    const cells = $$("#board .cell");
    for (let r = 0; r < ROWS; r++) {
        for (let c = 0; c < COLS; c++) {
            const idx = r * COLS + c;
            const cell = cells[idx];
            const val = board[r][c];

            cell.classList.remove("p1", "p2", "drop", "win", "dimmed", "last-move");

            if (val === 1) cell.classList.add("p1");
            else if (val === 2) cell.classList.add("p2");
        }
    }
}


/* Animate a newly dropped piece — randomly picks one of 5 personalities */
function animateDrop(row, col, player) {
    const idx = row * COLS + col;
    const cells = $$("#board .cell");
    const cell = cells[idx];

    /* Remove last-move ring from the previous piece immediately via state, not DOM query */
    if (state.lastMoveCell) {
        state.lastMoveCell.classList.remove("last-move");
        state.lastMoveCell = null;
    }

    /* Bump generation so stale animationend handlers from overlapping
       animations become no-ops — fixes multiple last-move rings when
       moves arrive faster than an in-flight animation (e.g. feather). */
    state.dropGeneration = (state.dropGeneration || 0) + 1;
    const gen = state.dropGeneration;

    const fallDistance = row + 1;
    const effect = pickDropEffect(state.moveCount);
    state.moveCount = (state.moveCount || 0) + 1;

    cell.classList.add(player === 1 ? "p1" : "p2");
    cell.style.animation = "none";
    cell.offsetHeight; /* force reflow */

    switch (effect) {
        case "slam": {
            const dur = (0.28 + fallDistance * 0.04).toFixed(2);
            cell.style.animation = `dropSlam ${dur}s cubic-bezier(0.55, 0, 1, 0.45) forwards`;
            /* Board container quakes on impact */
            cell.addEventListener("animationend", () => {
                const bc = $("#board-container");
                bc.classList.add("shaking");
                bc.addEventListener("animationend", () => bc.classList.remove("shaking"), { once: true });
            }, { once: true });
            break;
        }
        case "feather": {
            const dur = (1.2 + fallDistance * 0.15).toFixed(2);
            cell.style.animation = `dropFeather ${dur}s ease-in-out forwards`;
            break;
        }
        case "slam_ultra": {
            const dur = (0.24 + fallDistance * 0.032).toFixed(2);
            cell.style.animation = `dropSlam ${dur}s cubic-bezier(0.55, 0, 1, 0.45) forwards`;
            cell.addEventListener("animationend", () => {
                const bc = $("#board-container");
                bc.classList.add("vibrating");
                bc.addEventListener("animationend", () => bc.classList.remove("vibrating"), { once: true });
            }, { once: true });
            break;
        }
        case "bowling": {
            const dur = (0.22 + fallDistance * 0.03).toFixed(2);
            cell.style.animation = `dropSlam ${dur}s cubic-bezier(0.55, 0, 1, 0.45) forwards`;
            /* Cells above in same column bounce up */
            cell.addEventListener("animationend", () => {
                for (let r2 = 0; r2 < row; r2++) {
                    const above = cells[r2 * COLS + col];
                    above.classList.add("colquake");
                    above.addEventListener("animationend", () => above.classList.remove("colquake"), { once: true });
                }
            }, { once: true });
            break;
        }
        default: { /* soft — standard bounce */
            const dur = (0.25 + fallDistance * 0.06).toFixed(2);
            cell.style.animation = `dropBounce ${dur}s cubic-bezier(0.22, 0.61, 0.36, 1) forwards`;
            break;
        }
    }

    /* After any animation settles, mark this cell as the last move —
       but only if no newer animateDrop call has run since (gen check).
       Return early when stale so the handler is a true no-op and cannot
       clear/cancel an animation that belongs to a newer drop or a new game. */
    cell.addEventListener("animationend", () => {
        if (gen !== state.dropGeneration) return;
        cell.style.animation = "";
        cell.classList.add("last-move");
        state.lastMoveCell = cell;
    }, { once: true });
}

/* Weighted random drop personality — more dramatic as game goes on */
function pickDropEffect(moveCount) {
    const late = moveCount > 14;
    const w = late
        ? { soft: 5, slam: 30, slam_ultra: 30, feather: 20, bowling: 15 }
        : { soft: 5, slam: 35, slam_ultra: 20, feather: 25, bowling: 15 };
    const total = Object.values(w).reduce((a, b) => a + b, 0);
    let rand = Math.random() * total;
    for (const [k, v] of Object.entries(w)) {
        rand -= v;
        if (rand <= 0) return k;
    }
    return "soft";
}

/* Spawn a floating effect label near a cell (e.g. "SLAM", "EARTHQUAKE") */
/* Column hover highlight */
function highlightColumn(col) {
    if (state.gameOver) return;
    const indicators = $$("#hover-row .hover-indicator");
    indicators.forEach((ind) =>
        ind.classList.remove("active-p1", "active-p2")
    );
    const cls = state.myPlayer === 1 ? "active-p1" : "active-p2";
    indicators[col].classList.add(cls);
}

function clearColumnHighlight() {
    $$("#hover-row .hover-indicator").forEach((ind) =>
        ind.classList.remove("active-p1", "active-p2")
    );
}

/* ---- IDLE TAUNT SYSTEM ---- */
const TAUNT_EMOJIS = ['\u{1F634}', '\u{1F971}', '\u{1F4A4}', '\u23F3', '\u{1F40C}', '\u{1F9A5}', '\u231B', '\u{1F611}', '\u{1F644}', '\u{1F440}', '\u{1FAE0}', '\u{1F9F1}', '\u{1F570}\uFE0F', '\u{1F62A}'];
let _tauntIdleTimer = null;
let _tauntSpawnTimer = null;

function spawnTauntEmoji() {
    const emoji = TAUNT_EMOJIS[Math.floor(Math.random() * TAUNT_EMOJIS.length)];
    const el = document.createElement('span');
    el.className = 'taunt-emoji';
    el.textContent = emoji;
    el.style.left = (5 + Math.random() * 85) + 'vw';
    el.style.top = (8 + Math.random() * 76) + 'vh';
    document.body.appendChild(el);
    el.addEventListener('animationend', () => el.remove(), { once: true });
}

function startIdleTaunt() {
    stopIdleTaunt();
    _tauntIdleTimer = setTimeout(() => {
        spawnTauntEmoji();
        _tauntSpawnTimer = setInterval(spawnTauntEmoji, 2500);
    }, 8000);
}

function stopIdleTaunt() {
    clearTimeout(_tauntIdleTimer);
    clearInterval(_tauntSpawnTimer);
    _tauntIdleTimer = null;
    _tauntSpawnTimer = null;
    document.querySelectorAll('.taunt-emoji').forEach(el => el.remove());
}

/* Handle click on a column */
function handleCellClick(col) {
    if (state.gameOver) return;
    if (state.currentTurn !== state.myPlayer) return;
    if (!state.ws || state.ws.readyState !== WebSocket.OPEN) return;

    stopIdleTaunt();
    state.ws.send(
        JSON.stringify({ player: state.myPlayer, column: col })
    );
}

/* Update status text */
function updateStatus() {
    const el = $("#game-status");
    el.classList.remove("p1-turn", "p2-turn");

    /* Trigger micro-bounce on every turn change */
    el.classList.remove("pop");
    void el.offsetWidth; /* force reflow so animation re-fires */

    if (state.gameOver) {
        el.textContent = "Game Over";
        stopIdleTaunt();
        updateTurnIndicator();
        return;
    }

    if (state.currentTurn === state.myPlayer) {
        el.textContent = "Your turn";
        el.classList.add(state.myPlayer === 1 ? "p1-turn" : "p2-turn");
        if (state.myPlayer) startIdleTaunt(); /* YOU are slow — taunt yourself */
    } else {
        el.textContent = "Opponent's turn";
        el.classList.add(state.currentTurn === 1 ? "p1-turn" : "p2-turn");
        stopIdleTaunt(); /* opponent's problem now — clean up */
    }

    el.classList.add("pop");
    updateTurnIndicator();
}

/* ---- TURN COUNTDOWN RING ---- */
let _cdRafId = null;
let _cdStartTime = null;
const CD_DURATION = 30000; /* 30 seconds per turn */

function startCountdown(card, playerNum) {
    stopCountdown();
    _cdStartTime = performance.now();
    const color = playerNum === 1 ? "var(--p1)" : "var(--p2)";
    card.style.setProperty("--cd-color", color);
    function tick(now) {
        const pct = Math.max(0, 100 - ((now - _cdStartTime) / CD_DURATION) * 100);
        card.style.setProperty("--cd-pct", pct.toFixed(2));
        if (pct > 0) _cdRafId = requestAnimationFrame(tick);
    }
    _cdRafId = requestAnimationFrame(tick);
}

function stopCountdown() {
    if (_cdRafId) { cancelAnimationFrame(_cdRafId); _cdRafId = null; }
    _cdStartTime = null;
    [$("#player1-card"), $("#player2-card")].forEach(c => {
        if (c) c.style.setProperty("--cd-pct", 0);
    });
}

/* Highlight the player card whose turn it is */
function updateTurnIndicator() {
    const card1 = $("#player1-card");
    const card2 = $("#player2-card");
    if (!card1 || !card2) return;

    card1.classList.remove("active-turn");
    card2.classList.remove("active-turn");

    if (state.gameOver) { stopCountdown(); return; }

    if (state.currentTurn === 1) {
        card1.classList.add("active-turn");
        startCountdown(card1, 1);
    } else {
        card2.classList.add("active-turn");
        startCountdown(card2, 2);
    }
}

/* Update player cards with connection status and usernames */
function updatePlayerCards(connectedPlayers, usernames) {
    const prev = state.connectedPlayers;
    state.connectedPlayers = connectedPlayers;
    if (usernames) state.playerUsernames = usernames;

    for (let pn = 1; pn <= 2; pn++) {
        const card = $(`#player${pn}-card`);
        const dot = card.querySelector(".player-card-dot");
        const statusText = card.querySelector(".player-card-status");
        const label = card.querySelector(".player-card-label");
        const isConnected = connectedPlayers.includes(pn);
        const isMe = pn === state.myPlayer;

        /* Use real username if available, fall back to player number */
        const nameMap = state.playerUsernames || {};
        const displayName = nameMap[pn] || `Player ${pn}`;
        label.textContent = isMe ? `${displayName} (You)` : displayName;

        /* Color the dot to match the player's piece color */
        card.classList.toggle("online", isConnected);
        card.classList.toggle("offline", !isConnected);
        card.classList.toggle("is-me", isMe);
        card.classList.toggle("player1-color", pn === 1);
        card.classList.toggle("player2-color", pn === 2);
        dot.className = `player-card-dot ${isConnected ? "connected" : "disconnected"} p${pn}-dot`;
        statusText.textContent = isConnected ? "Online" : "Offline";
    }

    /* Toast notification when opponent disconnects mid-game */
    const opponentNumber = state.myPlayer === 1 ? 2 : 1;
    const wasConnected = prev.includes(opponentNumber);
    const isNowConnected = connectedPlayers.includes(opponentNumber);

    if (wasConnected && !isNowConnected) {
        if (!state.gameOver) {
            showToast("Opponent disconnected", "Your opponent has left the game.", "warning");
        }
        /* If we were waiting for the opponent to accept a rematch, cancel that wait */
        const rematchStatusEl = $("#rematch-status");
        const newGameBtn = $("#btn-new-game");
        if (
            newGameBtn &&
            newGameBtn.disabled &&
            rematchStatusEl &&
            rematchStatusEl.textContent === "Waiting for opponent…"
        ) {
            newGameBtn.disabled = false;
            rematchStatusEl.textContent = "Opponent left — rematch cancelled.";
        }
    } else if (!wasConnected && isNowConnected && prev.length > 0) {
        showToast("Opponent reconnected", "Your opponent is back!", "success");
    }
}

/* Toast notification system */
function showToast(title, message, type) {
    const container = $("#toast-container");
    if (!container) return;

    const toast = document.createElement("div");
    toast.className = `toast toast-${type}`;
    toast.innerHTML = `<strong>${escapeHtml(title)}</strong><span>${escapeHtml(message)}</span>`;
    container.appendChild(toast);

    /* Trigger enter animation */
    requestAnimationFrame(() => toast.classList.add("show"));

    /* Auto-dismiss after 5 seconds */
    setTimeout(() => {
        toast.classList.remove("show");
        toast.addEventListener("transitionend", () => toast.remove(), { once: true });
    }, 5000);
}

/* Highlight only the winning cells — sequential pop-in for extra satisfaction */
function highlightWinningCells(winningCells) {
    if (!winningCells || winningCells.length === 0) return;
    const cells = $$("#board .cell");

    /* Remove last-move ring so it doesn't clash with win highlight */
    if (state.lastMoveCell) {
        state.lastMoveCell.classList.remove("last-move");
        state.lastMoveCell = null;
    }

    /* Dim all non-winning pieces first */
    cells.forEach((cell) => {
        if (cell.classList.contains("p1") || cell.classList.contains("p2")) {
            cell.classList.add("dimmed");
        }
    });

    /* Each winning cell pops in with a staggered delay via CSS custom property */
    winningCells.forEach(([row, col], i) => {
        const idx = row * COLS + col;
        const cell = cells[idx];
        cell.classList.remove("dimmed");
        cell.style.setProperty("--win-delay", `${i * 95}ms`);
        cell.classList.add("win");
    });
}

/* ------------------------------------------------------------------ */
/* WebSocket                                                          */
/* ------------------------------------------------------------------ */

function connectWebSocket(gameId) {
    if (state.ws) {
        state.ws.close();
    }

    const ws = new WebSocket(`${WS_BASE}/ws/${gameId}`);
    state.ws = ws;

    ws.addEventListener("open", () => {
        /* Remove the disconnected overlay if we're reconnecting */
        const boardEl = $("#board");
        if (boardEl) boardEl.classList.remove("board-disabled");

        /* Send an identify message so server knows our player number + name */
        ws.send(JSON.stringify({ action: "identify", player: state.myPlayer, username: state.username }));
        $("#game-status").textContent =
            state.currentTurn === state.myPlayer ? "Your turn" : "Opponent's turn";
    });

    ws.addEventListener("error", () => {
        console.warn("WebSocket error on game", gameId);
    });

    ws.addEventListener("message", (event) => {
        let data;
        try {
            data = JSON.parse(event.data);
        } catch (err) {
            console.warn("Malformed WS message:", err);
            return;
        }

        /* Error messages */
        if (data.error) {
            console.warn("Server error:", data.error);
            showToast("Move rejected", data.error, "warning");
            /* Restart idle taunts — handleCellClick stopped them before the move failed */
            if (!state.gameOver && state.currentTurn === state.myPlayer) {
                startIdleTaunt();
            }
            return;
        }

        /* Player status update */
        if (data.type === "player_status") {
            const connectedPlayers = data.connected_players || [];
            const usernames = data.usernames || {};
            updatePlayerCards(connectedPlayers, usernames);
            return;
        }

        /* Rematch accepted — both players ready, reset the board */
        if (data.rematch) {
            state.gameOver = false;
            state.currentTurn = 1;
            state.moveCount = 0;
            /* Invalidate drop generation so any in-flight animationend
               handlers from the previous game become true no-ops. */
            state.dropGeneration = (state.dropGeneration || 0) + 1;
            state.lastMoveCell = null;
            state.board = Array.from({ length: ROWS }, () => Array(COLS).fill(0));
            stopCountdown();
            $("#game-over-banner").style.display = "none";
            $("#rematch-status").style.display = "none";
            $("#btn-new-game").disabled = false;
            stopConfetti();
            renderBoard(state.board);
            updateStatus();
            return;
        }

        /* Rematch vote — opponent wants to play again */
        if (data.rematch_waiting) {
            $("#rematch-status").textContent = "Opponent wants a rematch!";
            $("#rematch-status").style.display = "";
            return;
        }

        /* Reset reconnect counter on first valid game message */
        if (state.wsReconnectAttempts > 0) state.wsReconnectAttempts = 0;

        /* Regular move — guard against messages missing required fields */
        if (data.board == null || data.row == null || data.column == null || data.player == null) return;
        state.board = data.board;
        renderBoard(data.board);
        animateDrop(data.row, data.column, data.player);

        /* Advance turn */
        state.currentTurn = data.player === 1 ? 2 : 1;

        /* Check end conditions */
        if (data.winner) {
            state.gameOver = true;
            showGameOver(data.winner, data.winning_cells);
        } else if (data.draw) {
            state.gameOver = true;
            showGameOver(null, null);
        }

        updateStatus();
    });

    ws.addEventListener("close", () => {
        if (state.gameOver) return;

        $("#game-status").textContent = "Disconnected — attempting to reconnect…";
        /* Disable the board so stale clicks don't queue up */
        const boardEl = $("#board");
        if (boardEl) boardEl.classList.add("board-disabled");

        /* Exponential backoff: 2s → 4s → 8s, then give up */
        const MAX_RECONNECT_ATTEMPTS = 3;
        if (state.wsReconnectAttempts < MAX_RECONNECT_ATTEMPTS) {
            const delay = 2000 * Math.pow(2, state.wsReconnectAttempts);
            state.wsReconnectAttempts++;
            setTimeout(() => {
                if (!state.gameOver && state.gameId && state.ws === ws) {
                    connectWebSocket(state.gameId);
                    restoreGameState(state.gameId);
                }
            }, delay);
        } else {
            $("#game-status").textContent = "Connection lost — please rejoin the game.";
            showToast("Disconnected", "Could not reconnect after multiple attempts.", "error");
        }
    });
}

/* Game over UI */
function showGameOver(winner, winningCells) {
    const banner = $("#game-over-banner");
    const text = $("#game-over-text");

    banner.style.display = "";

    stopCountdown();

    if (winner === null) {
        text.textContent = "It's a draw!";
        text.style.color = "var(--accent)";
        /* Frustrated board shake */
        const bc = $("#board-container");
        bc.classList.add("shaking");
        bc.addEventListener("animationend", () => bc.classList.remove("shaking"), { once: true });
    } else if (winner === state.myPlayer) {
        text.textContent = "You win! \u{1F389}";
        text.style.color =
            state.myPlayer === 1 ? "var(--p1)" : "var(--p2)";
        launchConfetti();
    } else {
        text.textContent = "You lose \u{1F622}";
        text.style.color =
            winner === 1 ? "var(--p1)" : "var(--p2)";
    }

    /* Highlight only the winning combination */
    if (winner && winningCells && winningCells.length > 0) {
        highlightWinningCells(winningCells);
    }
}

/* ------------------------------------------------------------------ */
/* Navigation buttons                                                 */
/* ------------------------------------------------------------------ */

$("#btn-leave").addEventListener("click", () => {
    if (state.ws) { state.ws.close(); state.ws = null; }
    clearInterval(state.pollTimer);
    clearInterval(state.matchmakingTimer);
    state.pollTimer = null;
    state.matchmakingTimer = null;
    state.gameOver = false;
    state.gameId = null;
    state.myPlayer = null;
    saveState();
    $("#game-over-banner").style.display = "none";
    $("#btn-new-game").disabled = false;
    const toasts = $("#toast-container");
    if (toasts) toasts.innerHTML = "";
    stopConfetti();
    stopIdleTaunt();
    stopCountdown();
    enterGameLobby();
});

$("#btn-new-game").addEventListener("click", () => {
    if (state.ws && state.ws.readyState === WebSocket.OPEN) {
        state.ws.send(JSON.stringify({ action: "rematch", player: state.myPlayer }));
        $("#rematch-status").textContent = "Waiting for opponent…";
        $("#rematch-status").style.display = "";
        $("#btn-new-game").disabled = true;
    } else {
        /* Fallback: no WS connection, go back to lobby */
        state.gameOver = false;
        state.gameId = null;
        state.myPlayer = null;
        saveState();
        $("#game-over-banner").style.display = "none";
        $("#rematch-status").style.display = "none";
        enterGameLobby();
    }
});

/* ================================================================== */
/* SCREEN 4 — Game Replay                                             */
/* ================================================================== */

const replayState = {
    moves: [],
    currentStep: 0,
    board: null,
};

window.replayGame = async function (gameId) {
    stopGamesAutoRefresh();
    try {
        const moves = await api("GET", `/games/${gameId}/moves`);
        if (moves.length === 0) {
            showToast("Replay", "No moves recorded for this game.", "warning");
            return;
        }

        replayState.moves = moves;
        replayState.currentStep = 0;
        replayState.board = Array.from({ length: ROWS }, () => Array(COLS).fill(0));

        showScreen(screenReplay);
        buildReplayBoard();
        renderReplayBoard();
        updateReplayControls();

        const slider = $("#replay-slider");
        slider.max = moves.length;
        slider.value = 0;
    } catch (err) {
        showToast("Error", "Failed to load game moves: " + err.message, "error");
    }
};

function buildReplayBoard() {
    const boardEl = $("#replay-board");
    boardEl.innerHTML = "";

    for (let r = 0; r < ROWS; r++) {
        for (let c = 0; c < COLS; c++) {
            const cell = document.createElement("div");
            cell.className = "cell";
            cell.dataset.row = r;
            cell.dataset.col = c;
            boardEl.appendChild(cell);
        }
    }
}

function renderReplayBoard() {
    /* Rebuild board from scratch up to currentStep */
    const board = Array.from({ length: ROWS }, () => Array(COLS).fill(0));
    for (let step = 0; step < replayState.currentStep; step++) {
        const move = replayState.moves[step];
        board[move.row][move.column] = move.player;
    }
    replayState.board = board;

    const cells = $$("#replay-board .cell");
    for (let r = 0; r < ROWS; r++) {
        for (let c = 0; c < COLS; c++) {
            const idx = r * COLS + c;
            const cell = cells[idx];
            const val = board[r][c];

            cell.classList.remove("p1", "p2", "win", "last-move");

            if (val === 1) cell.classList.add("p1");
            else if (val === 2) cell.classList.add("p2");
        }
    }

    /* Highlight the last move */
    if (replayState.currentStep > 0) {
        const lastMove = replayState.moves[replayState.currentStep - 1];
        const lastIdx = lastMove.row * COLS + lastMove.column;
        cells[lastIdx].classList.add("last-move");
    }
}

function updateReplayControls() {
    const total = replayState.moves.length;
    const current = replayState.currentStep;
    $("#replay-move-info").textContent = `Move ${current} / ${total}`;
    $("#btn-replay-prev").disabled = current <= 0;
    $("#btn-replay-start").disabled = current <= 0;
    $("#btn-replay-next").disabled = current >= total;
    $("#btn-replay-end").disabled = current >= total;
    $("#replay-slider").value = current;
}

$("#btn-replay-start").addEventListener("click", () => {
    replayState.currentStep = 0;
    renderReplayBoard();
    updateReplayControls();
});

$("#btn-replay-prev").addEventListener("click", () => {
    if (replayState.currentStep > 0) {
        replayState.currentStep--;
        renderReplayBoard();
        updateReplayControls();
    }
});

$("#btn-replay-next").addEventListener("click", () => {
    if (replayState.currentStep < replayState.moves.length) {
        replayState.currentStep++;
        renderReplayBoard();
        updateReplayControls();
    }
});

$("#btn-replay-end").addEventListener("click", () => {
    replayState.currentStep = replayState.moves.length;
    renderReplayBoard();
    updateReplayControls();
});

$("#replay-slider").addEventListener("input", (e) => {
    replayState.currentStep = parseInt(e.target.value, 10);
    renderReplayBoard();
    updateReplayControls();
});

$("#btn-back-to-lobby").addEventListener("click", () => {
    enterGameLobby();
});

/* ------------------------------------------------------------------ */
/* Quick-play mode — for prototype testing                            */
/* ------------------------------------------------------------------ */
/* If the URL has ?game=XYZ&player=1, go straight to the game.        */
/* This is useful for opening two browser tabs to test.                */

function checkQuickPlay() {
    const params = new URLSearchParams(window.location.search);
    const gameId = params.get("game");
    const player = parseInt(params.get("player"), 10);

    if (gameId && (player === 1 || player === 2)) {
        state.username = `Player ${player}`;
        state.playerId = `quick-p${player}`;
        state.myPlayer = player;
        startGame(gameId);
        return true;
    }
    return false;
}

/* ================================================================== */
/* Confetti celebration                                               */
/* ================================================================== */

let confettiCanvas = null;
let confettiCtx = null;
let confettiAnimationId = null;
let confettiParticles = [];

const CONFETTI_EMOJIS = ["\u{1F389}", "\u{1F38A}", "\u{2B50}", "\u{1F525}", "\u{1F451}", "\u{1F3C6}", "\u{1F60E}", "\u{1F4A5}"];
const CONFETTI_COLORS = ["#ef4444", "#3b82f6", "#facc15", "#22c55e", "#f97316", "#a855f7", "#ec4899", "#14b8a6"];

function launchConfetti() {
    stopConfetti();

    confettiCanvas = document.createElement("canvas");
    confettiCanvas.id = "confetti-canvas";
    confettiCanvas.style.cssText =
        "position:fixed;top:0;left:0;width:100%;height:100%;pointer-events:none;z-index:9999";
    document.body.appendChild(confettiCanvas);
    confettiCtx = confettiCanvas.getContext("2d");

    function resize() {
        confettiCanvas.width = window.innerWidth;
        confettiCanvas.height = window.innerHeight;
    }
    resize();
    _confettiResizeHandler = resize;
    window.addEventListener("resize", resize);

    const width = confettiCanvas.width;
    const height = confettiCanvas.height;
    confettiParticles = [];

    /* Burst from the center */
    for (let i = 0; i < 150; i++) {
        const useEmoji = Math.random() < 0.3;
        confettiParticles.push({
            x: width / 2 + (Math.random() - 0.5) * 200,
            y: height * 0.5,
            vx: (Math.random() - 0.5) * 18,
            vy: -Math.random() * 22 - 5,
            size: Math.random() * 8 + 4,
            color: CONFETTI_COLORS[Math.floor(Math.random() * CONFETTI_COLORS.length)],
            rotation: Math.random() * 360,
            rotationSpeed: (Math.random() - 0.5) * 12,
            emoji: useEmoji ? CONFETTI_EMOJIS[Math.floor(Math.random() * CONFETTI_EMOJIS.length)] : null,
            gravity: 0.25 + Math.random() * 0.15,
            wobble: Math.random() * 10,
            wobbleSpeed: 0.05 + Math.random() * 0.1,
            opacity: 1,
        });
    }

    /* Rain from the top */
    for (let i = 0; i < 100; i++) {
        const useEmoji = Math.random() < 0.25;
        confettiParticles.push({
            x: Math.random() * width,
            y: -Math.random() * height * 0.5,
            vx: (Math.random() - 0.5) * 3,
            vy: Math.random() * 3 + 2,
            size: Math.random() * 7 + 3,
            color: CONFETTI_COLORS[Math.floor(Math.random() * CONFETTI_COLORS.length)],
            rotation: Math.random() * 360,
            rotationSpeed: (Math.random() - 0.5) * 8,
            emoji: useEmoji ? CONFETTI_EMOJIS[Math.floor(Math.random() * CONFETTI_EMOJIS.length)] : null,
            gravity: 0.1 + Math.random() * 0.1,
            wobble: Math.random() * 10,
            wobbleSpeed: 0.03 + Math.random() * 0.05,
            opacity: 1,
        });
    }

    let frame = 0;
    function animate() {
        confettiCtx.clearRect(0, 0, confettiCanvas.width, confettiCanvas.height);
        frame++;
        let alive = 0;

        for (const p of confettiParticles) {
            p.vy += p.gravity;
            p.x += p.vx + Math.sin(p.wobble) * 1.5;
            p.y += p.vy;
            p.rotation += p.rotationSpeed;
            p.wobble += p.wobbleSpeed;

            /* Fade out near the bottom */
            if (p.y > confettiCanvas.height * 0.8) {
                p.opacity = Math.max(0, 1 - (p.y - confettiCanvas.height * 0.8) / (confettiCanvas.height * 0.2));
            }

            if (p.opacity <= 0 || p.y > confettiCanvas.height + 50) continue;
            alive++;

            confettiCtx.save();
            confettiCtx.translate(p.x, p.y);
            confettiCtx.rotate((p.rotation * Math.PI) / 180);
            confettiCtx.globalAlpha = p.opacity;

            if (p.emoji) {
                confettiCtx.font = `${p.size * 2.5}px serif`;
                confettiCtx.textAlign = "center";
                confettiCtx.textBaseline = "middle";
                confettiCtx.fillText(p.emoji, 0, 0);
            } else {
                confettiCtx.fillStyle = p.color;
                confettiCtx.fillRect(-p.size / 2, -p.size / 4, p.size, p.size / 2);
            }

            confettiCtx.restore();
        }

        /* Slow damping on horizontal speed */
        for (const p of confettiParticles) {
            p.vx *= 0.99;
        }

        if (alive > 0 && frame < 400) {
            confettiAnimationId = requestAnimationFrame(animate);
        } else {
            stopConfetti();
        }
    }

    confettiAnimationId = requestAnimationFrame(animate);
}

let _confettiResizeHandler = null;

function stopConfetti() {
    if (confettiAnimationId) {
        cancelAnimationFrame(confettiAnimationId);
        confettiAnimationId = null;
    }
    if (_confettiResizeHandler) {
        window.removeEventListener("resize", _confettiResizeHandler);
        _confettiResizeHandler = null;
    }
    if (confettiCanvas) {
        confettiCanvas.remove();
        confettiCanvas = null;
        confettiCtx = null;
    }
    confettiParticles = [];
}

/* ================================================================== */
/* Rejoin active game                                                 */
/* ================================================================== */

window.rejoinGame = function (gameId, myPlayer) {
    state.gameId = gameId;
    state.myPlayer = myPlayer;
    saveState();
    startGame(gameId);
};

async function checkActiveGame() {
    if (!state.playerId) return;
    try {
        const result = await api("GET", `/players/${state.playerId}/active-game`);
        if (result.game) {
            const gameInfo = result.game;
            const statusLabel = gameInfo.status === "waiting" ? "Waiting for opponent" : "In progress";
            const rejoinCard = document.createElement("div");
            rejoinCard.className = "card";
            rejoinCard.id = "rejoin-card";
            rejoinCard.innerHTML = `
            <h2>\u{1F3AE} Active Game Found</h2>
            <p class="muted">${statusLabel} (${gameInfo.id.slice(0, 8)}\u2026)</p>
            <button onclick="rejoinGame('${gameInfo.id}', ${gameInfo.my_player})" class="rejoin-btn">▶ Resume Game</button>
            <button class="secondary small" onclick="this.parentElement.remove()">Dismiss</button>
        `;
            /* Insert at the top of the games screen */
            const existing = $("#rejoin-card");
            if (existing) existing.remove();
            const firstCard = screenGames.querySelector(".card");
            if (firstCard) {
                screenGames.insertBefore(rejoinCard, firstCard);
            } else {
                screenGames.appendChild(rejoinCard);
            }
        } else {
            const existing = $("#rejoin-card");
            if (existing) existing.remove();
        }
    } catch (error) {
        console.warn("checkActiveGame failed:", error);
    }
}

/* ------------------------------------------------------------------ */
/* Utils                                                              */
/* ------------------------------------------------------------------ */

function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
}

/* ================================================================== */
/* Button ripple effect                                               */
/* ================================================================== */
document.addEventListener("pointerdown", (e) => {
    const btn = e.target.closest("button");
    if (!btn || btn.disabled) return;
    const rect = btn.getBoundingClientRect();
    const size = Math.max(rect.width, rect.height) * 2;
    const ripple = document.createElement("span");
    ripple.className = "ripple";
    ripple.style.cssText = [
        `width:${size}px`,
        `height:${size}px`,
        `left:${e.clientX - rect.left - size / 2}px`,
        `top:${e.clientY - rect.top - size / 2}px`,
    ].join(";");
    btn.appendChild(ripple);
    ripple.addEventListener("animationend", () => ripple.remove(), { once: true });
});

/* ------------------------------------------------------------------ */
/* Session resume — smart reconnect after page refresh                 */
/* ------------------------------------------------------------------ */

async function resumeSession() {
    if (!state.gameId || !state.myPlayer) {
        enterGameLobby();
        return;
    }

    try {
        const info = await api("GET", `/games/${state.gameId}/status`);

        if (info.status === "waiting" && state.myPlayer === 1) {
            /* Still waiting for opponent — go back to lobby with waiting card */
            enterGameLobby();
            $("#waiting-card").style.display = "";
            $("#waiting-game-id").textContent = state.gameId;
            pollForOpponent(state.gameId);
        } else if (info.status === "playing") {
            /* Active game — restore it */
            startGame(state.gameId);
        } else if (info.status === "finished" || info.status === "draw") {
            /* Game is already over — don't dump the user back into it.
               Clear stale state and go to the lobby so they can start fresh. */
            state.gameId = null;
            state.myPlayer = null;
            saveState();
            enterGameLobby();
        } else {
            /* Unknown status — clean up and go to lobby */
            state.gameId = null;
            state.myPlayer = null;
            saveState();
            enterGameLobby();
        }
    } catch {
        /* Game not found or server error — clean up stale state */
        state.gameId = null;
        state.myPlayer = null;
        saveState();
        enterGameLobby();
    }
}

/* ------------------------------------------------------------------ */
/* Init                                                               */
/* ------------------------------------------------------------------ */

loadState();

/* Always start stats polling on the lobby screen */
startStatsPolling();

if (!checkQuickPlay()) {
    if (state.playerId && state.username) {
        /* Returning user after page refresh — check game status first */
        resumeSession();
    } else {
        loadLeaderboard();
    }
}
