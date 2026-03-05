# FinAlly — AI Trading Workstation

## Project Specification

## 1. Vision

FinAlly (Finance Ally) is a visually stunning AI-powered trading workstation that streams live market data, lets users trade a simulated portfolio, and integrates an LLM chat assistant that can analyze positions and execute trades on the user's behalf. It looks and feels like a modern Bloomberg terminal with an AI copilot.

This is the capstone project for an agentic AI coding course. It is built entirely by Coding Agents demonstrating how orchestrated AI agents can produce a production-quality full-stack application. Agents interact through files in `planning/`.

## 2. User Experience

### First Launch

The user runs a single Docker command (or a provided start script). A browser opens to `http://localhost:8000`. No login, no signup. They immediately see:

- A watchlist of 10 default tickers with live-updating prices in a grid
- $10,000 in virtual cash
- A dark, data-rich trading terminal aesthetic
- An AI chat panel ready to assist

### What the User Can Do

- **Watch prices stream** — prices flash green (uptick) or red (downtick) with subtle CSS animations that fade
- **View sparkline mini-charts** — price action beside each ticker in the watchlist, accumulated on the frontend from the SSE stream since page load (sparklines fill in progressively)
- **Click a ticker** to see a larger detailed chart in the main chart area
- **Buy and sell shares** — market orders only, instant fill at current price, no fees, no confirmation dialog
- **Monitor their portfolio** — a heatmap (treemap) showing positions sized by weight and colored by P&L, plus a P&L chart tracking total portfolio value over time
- **View a positions table** — ticker, quantity, average cost, current price, unrealized P&L, % change
- **Chat with the AI assistant** — ask about their portfolio, get analysis, and have the AI execute trades and manage the watchlist through natural language
- **Manage the watchlist** — add/remove tickers manually or via the AI chat

### Visual Design

- **Dark theme**: backgrounds around `#0d1117` or `#1a1a2e`, muted gray borders, no pure black
- **Price flash animations**: brief green/red background highlight on price change, fading over ~500ms via CSS transitions
- **Connection status indicator**: a small colored dot (green = connected, yellow = reconnecting, red = disconnected) visible in the header
- **Professional, data-dense layout**: inspired by Bloomberg/trading terminals — every pixel earns its place
- **Responsive but desktop-first**: optimized for wide screens, functional on tablet

### Color Scheme
- Accent Yellow: `#ecad0a`
- Blue Primary: `#209dd7`
- Purple Secondary: `#753991` (submit buttons)

## 3. Architecture Overview

### Single Container, Single Port

```
┌─────────────────────────────────────────────────┐
│  Docker Container (port 8000)                   │
│                                                 │
│  FastAPI (Python/uv)                            │
│  ├── /api/*          REST endpoints             │
│  ├── /api/stream/*   SSE streaming              │
│  └── /*              Static file serving         │
│                      (Next.js export)            │
│                                                 │
│  SQLite database (volume-mounted)               │
│  Background task: market data polling/sim        │
└─────────────────────────────────────────────────┘
```

- **Frontend**: Next.js with TypeScript, built as a static export (`output: 'export'`), served by FastAPI as static files
- **Backend**: FastAPI (Python), managed as a `uv` project
- **Database**: SQLite, single file at `db/finally.db`, volume-mounted for persistence
- **Real-time data**: Server-Sent Events (SSE) — simpler than WebSockets, one-way server→client push, works everywhere
- **AI integration**: LiteLLM → OpenRouter (Cerebras for fast inference), with structured outputs for trade execution
- **Market data**: Environment-variable driven — simulator by default, real data via Massive API if key provided

### Why These Choices

| Decision | Rationale |
|---|---|
| SSE over WebSockets | One-way push is all we need; simpler, no bidirectional complexity, universal browser support |
| Static Next.js export | Single origin, no CORS issues, one port, one container, simple deployment |
| SQLite over Postgres | No auth = no multi-user = no need for a database server; self-contained, zero config |
| Single Docker container | Students run one command; no docker-compose for production, no service orchestration |
| uv for Python | Fast, modern Python project management; reproducible lockfile; what students should learn |
| Market orders only | Eliminates order book, limit order logic, partial fills — dramatically simpler portfolio math |

---

## 4. Directory Structure

```
finally/
├── frontend/                 # Next.js TypeScript project (static export)
├── backend/                  # FastAPI uv project (Python)
│   └── db/                   # Schema definitions, seed data, migration logic
├── planning/                 # Project-wide documentation for agents
│   ├── PLAN.md               # This document
│   └── ...                   # Additional agent reference docs
├── scripts/
│   ├── start_mac.sh          # Launch Docker container (macOS/Linux)
│   ├── stop_mac.sh           # Stop Docker container (macOS/Linux)
│   ├── start_windows.ps1     # Launch Docker container (Windows PowerShell)
│   └── stop_windows.ps1      # Stop Docker container (Windows PowerShell)
├── test/                     # Playwright E2E tests + docker-compose.test.yml
├── db/                       # Volume mount target (SQLite file lives here at runtime)
│   └── .gitkeep              # Directory exists in repo; finally.db is gitignored
├── Dockerfile                # Multi-stage build (Node → Python)
├── docker-compose.yml        # Optional convenience wrapper
├── .env                      # Environment variables (gitignored, .env.example committed)
└── .gitignore
```

### Key Boundaries

- **Contract precedence rule**: if any frontend requirement (Section 10 or frontend implementation details) conflicts with backend/API definitions (Sections 6, 7, 8, and 9), the backend definitions are authoritative. Frontend behavior must adapt to backend contracts, not redefine them.
- **`frontend/`** is a self-contained Next.js project. It knows nothing about Python. It talks to the backend via `/api/*` endpoints and `/api/stream/*` SSE endpoints. Internal structure is up to the Frontend Engineer agent.
- **`backend/`** is a self-contained uv project with its own `pyproject.toml`. It owns all server logic including database initialization, schema, seed data, API routes, SSE streaming, market data, and LLM integration. Internal structure is up to the Backend/Market Data agents.
- **`backend/db/`** contains schema SQL definitions and seed logic. The backend lazily initializes the database on first request — creating tables and seeding default data if the SQLite file doesn't exist or is empty.
- **`db/`** at the top level is the runtime volume mount point. The SQLite file (`db/finally.db`) is created here by the backend and persists across container restarts via Docker volume.
- **`planning/`** contains project-wide documentation, including this plan. All agents reference files here as the shared contract.
- **`test/`** contains Playwright E2E tests and supporting infrastructure (e.g., `docker-compose.test.yml`). Unit tests live within `frontend/` and `backend/` respectively, following each framework's conventions.
- **`scripts/`** contains start/stop scripts that wrap Docker commands.

---

## 5. Environment Variables

```bash
# Required: OpenRouter API key for LLM chat functionality
OPENROUTER_API_KEY=your-openrouter-api-key-here

# Optional: Massive (Polygon.io) API key for real market data
# If not set, the built-in market simulator is used (recommended for most users)
MASSIVE_API_KEY=

# Optional: Set to "true" for deterministic mock LLM responses (testing)
LLM_MOCK=false
```

### Behavior

- If `MASSIVE_API_KEY` is set and non-empty → backend uses Massive REST API for market data
- If `MASSIVE_API_KEY` is absent or empty → backend uses the built-in market simulator
- If `LLM_MOCK=true` → backend returns deterministic mock LLM responses (for E2E tests)
- The backend reads `.env` from the project root (mounted into the container or read via docker `--env-file`)

---

## 6. Market Data

### Two Implementations, One Interface

Both the simulator and the Massive client implement the same abstract interface. The backend selects which to use based on the environment variable. All downstream code (SSE streaming, price cache, frontend) is agnostic to the source.

### Simulator (Default)

- Generates prices using geometric Brownian motion (GBM) with configurable drift and volatility per ticker
- Updates at ~500ms intervals
- Correlated moves across tickers (e.g., tech stocks move together)
- Occasional random "events" — sudden 2-5% moves on a ticker for drama
- Starts from realistic seed prices (e.g., AAPL ~$190, GOOGL ~$175, etc.)
- Runs as an in-process background task — no external dependencies

### Massive API (Optional)

- REST API polling (not WebSocket) — simpler, works on all tiers
- Polls for the union of all watched tickers on a configurable interval
- Free tier (5 calls/min): poll every 15 seconds
- Paid tiers: poll every 2-15 seconds depending on tier
- Parses REST response into the same format as the simulator

### Shared Price Cache

- A single background task (simulator or Massive poller) writes to an in-memory price cache
- The cache holds the latest price, previous price, and timestamp for each ticker
- SSE streams read from this cache and push updates to connected clients
- This architecture supports future multi-user scenarios without changes to the data layer

### SSE Streaming

- Endpoint: `GET /api/stream/prices`
- Long-lived SSE connection; client uses native `EventSource` API
- Server pushes price updates for all tickers in the `watchlist` table at a fixed cadence of **500ms**
- Each SSE message carries exactly **one ticker** — events are never batched
- When a ticker is added to the watchlist mid-session, the price simulator immediately begins generating prices for it — no restart required
- Client handles reconnection automatically (EventSource has built-in retry)

#### SSE Wire Contract (normative)

Transport: `Content-Type: text/event-stream`, UTF-8.

Each price update is framed as:
```
event: price_update
data: {"type":"price_update","data":{"ticker":"AAPL","price":192.31,"previous_price":191.94,"change_pct":0.19,"timestamp":"2026-03-05T14:21:11.100Z"}}

```
Rules:
- Exactly one ticker per `data:` line. Never send multiple tickers in one event.
- `change_pct` is tick-to-tick: `(price - previous_price) / previous_price * 100`.
- `timestamp` is ISO 8601 UTC.
- The server sends a heartbeat comment (`:\n\n`) every 15 seconds to keep the connection alive through proxies and load balancers.
- On initial connection, the server sends `retry: 3000` to hint the client to reconnect after 3 seconds on disconnect.

---

## 7. Database

### SQLite with Lazy Initialization

The backend checks for the SQLite database on startup (or first request). If the file doesn't exist or tables are missing, it creates the schema and seeds default data. This means:

- No separate migration step
- No manual database setup
- Fresh Docker volumes start with a clean, seeded database automatically

### Schema

All tables include a `user_id` column defaulting to `"default"`. This is hardcoded for now (single-user) but enables future multi-user support without schema migration.

**users_profile** — User state (cash balance)
- `id` TEXT PRIMARY KEY (default: `"default"`)
- `cash_balance` REAL (default: `10000.0`)
- `created_at` TEXT (ISO timestamp)

**watchlist** — Tickers the user is watching
- `id` INTEGER PRIMARY KEY
- `user_id` TEXT (default: `"default"`)
- `ticker` TEXT
- `added_at` TEXT (ISO timestamp)
- UNIQUE constraint on `(user_id, ticker)`

**positions** — Current holdings (one row per ticker per user)
- `id` INTEGER PRIMARY KEY
- `user_id` TEXT (default: `"default"`)
- `ticker` TEXT
- `quantity` REAL (fractional shares supported)
- `avg_cost` REAL
- `updated_at` TEXT (ISO timestamp)
- UNIQUE constraint on `(user_id, ticker)`

**trades** — Trade history (append-only log)
- `id` INTEGER PRIMARY KEY
- `user_id` TEXT (default: `"default"`)
- `ticker` TEXT
- `side` TEXT (`"buy"` or `"sell"`)
- `quantity` REAL (fractional shares supported)
- `price` REAL
- `executed_at` TEXT (ISO timestamp)

**portfolio_snapshots** — Portfolio value over time (for P&L chart). Recorded after each trade execution only (no background polling task). Pruned to retain the most recent 2,764,800 rows.
- `id` INTEGER PRIMARY KEY
- `user_id` TEXT (default: `"default"`)
- `total_value` REAL
- `recorded_at` TEXT (ISO timestamp)

**chat_messages** — Conversation history with LLM
- `id` INTEGER PRIMARY KEY
- `user_id` TEXT (default: `"default"`)
- `role` TEXT (`"user"` or `"assistant"`)
- `content` TEXT
- `actions` TEXT (JSON post-execution summary: each trade and watchlist change with its success/failure status; null for user messages)
- `created_at` TEXT (ISO timestamp)

### Default Seed Data

- One user profile: `id="default"`, `cash_balance=10000.0`
- Ten watchlist entries: AAPL, GOOGL, MSFT, AMZN, TSLA, NVDA, META, JPM, V, NFLX

---

## 8. API Endpoints

Contract-first rules from `planning/api_lifecycle_and_validation.md` apply here:
- Backend routes MUST declare `response_model=...` and use Pydantic request models.
- Frontend MUST consume exactly these JSON signatures (no ad-hoc shape changes).
- FastAPI-generated OpenAPI from these Pydantic schemas is the source for frontend types.

### Authentication (all endpoints in v1)

- No user login in v1; backend resolves a single hardcoded user id (`"default"`).
- No bearer token required for `/api/*` in v1.
- If auth is added later, it must be introduced as `/api/v2/*` to avoid breaking v1 clients.

### Error Handling (all endpoints in v1)

All error responses — business failures, validation failures, and server errors — use the same `ApiErrorResponse` envelope. There is one error shape; frontend never needs to special-case validation vs. business errors.

- Domain/business failures return HTTP `400` with:
```json
{
  "error": {
    "code": "INSUFFICIENT_CASH",
    "message": "Not enough cash to execute buy order",
    "details": {
      "cash_balance": 120.5,
      "required": 350.0
    }
  }
}
```
- Request schema validation failures return HTTP `422` with the same envelope:
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Request validation failed",
    "details": {
      "field": "quantity",
      "issue": "must be greater than 0"
    }
  }
}
```
FastAPI's default `422` handler MUST be overridden with a custom exception handler that maps Pydantic `RequestValidationError` to this envelope.

- Unknown server failures return HTTP `500` with:
```json
{
  "error": {
    "code": "INTERNAL_ERROR",
    "message": "Unexpected server error",
    "details": null
  }
}
```

### Shared Pydantic Contracts

Backend module to create: `backend/app/api/schemas.py` (single source of API truth).

```python
from datetime import datetime
from enum import Enum
from pydantic import BaseModel, ConfigDict, Field

class TradeSide(str, Enum):
    buy = "buy"
    sell = "sell"

class WatchlistAction(str, Enum):
    add = "add"
    remove = "remove"

class ApiErrorBody(BaseModel):
    code: str
    message: str
    details: dict | None = None

class ApiErrorResponse(BaseModel):
    error: ApiErrorBody

class PositionDTO(BaseModel):
    ticker: str
    quantity: float
    avg_cost: float
    current_price: float
    unrealized_pnl: float
    pnl_pct: float

class PortfolioResponse(BaseModel):
    cash_balance: float
    total_value: float
    total_unrealized_pnl: float
    positions: list[PositionDTO]

class TradeRequest(BaseModel):
    ticker: str = Field(min_length=1, max_length=10)
    quantity: float = Field(gt=0)
    side: TradeSide

class TradeResponse(BaseModel):
    trade_id: int
    ticker: str
    side: TradeSide
    quantity: float
    price: float
    executed_at: datetime
    cash_balance: float
    total_value: float

class PortfolioHistoryPoint(BaseModel):
    total_value: float
    recorded_at: datetime

class PortfolioHistoryResponse(BaseModel):
    points: list[PortfolioHistoryPoint]

class WatchlistItem(BaseModel):
    ticker: str
    price: float | None = None
    previous_price: float | None = None
    change_pct: float | None = None
    updated_at: datetime | None = None

class WatchlistResponse(BaseModel):
    items: list[WatchlistItem]

class WatchlistAddRequest(BaseModel):
    ticker: str = Field(min_length=1, max_length=10)

class WatchlistMutationResponse(BaseModel):
    ticker: str
    action: WatchlistAction
    watchlist_size: int

class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)

class ChatTradeInstruction(BaseModel):
    ticker: str
    side: TradeSide
    quantity: float = Field(gt=0)

class ChatWatchlistInstruction(BaseModel):
    ticker: str
    action: WatchlistAction

class ChatActionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str
    ticker: str
    action: str
    success: bool
    message: str

class ChatResponse(BaseModel):
    message: str
    trades: list[ChatTradeInstruction] = Field(default_factory=list)
    watchlist_changes: list[ChatWatchlistInstruction] = Field(default_factory=list)
    execution_results: list[ChatActionResult] = Field(default_factory=list)

class HealthResponse(BaseModel):
    status: str
    version: str
    market_data_source: str
    timestamp: datetime
```

Frontend alignment rule:
- Frontend TypeScript types MUST be generated from backend OpenAPI (`schemas.py` + route signatures), not manually duplicated.
- Endpoint payload names and field names above are canonical for both backend and frontend.

### Market Data

#### `GET /api/stream/prices` (SSE)
- Purpose: stream latest prices for all tickers currently in watchlist.
- Auth: none (v1).
- Request format: no body.
- Success status: `200` (`text/event-stream`).
- SSE event payload format:
```json
{
  "type": "price_update",
  "data": {
    "ticker": "AAPL",
    "price": 192.31,
    "previous_price": 191.94,
    "change_pct": 0.19,
    "timestamp": "2026-03-05T14:21:11.100Z"
  }
}
```
- Error handling:
  - Stream disconnects are handled by client auto-retry (`EventSource`).
  - On backend failure while streaming, server closes connection; client reconnects.

### Portfolio

| Method | Path | Request Model | Response Model |
|---|---|---|---|
| GET | `/api/portfolio` | none | `PortfolioResponse` |
| POST | `/api/portfolio/trade` | `TradeRequest` | `TradeResponse` |
| GET | `/api/portfolio/history` | query params (`limit`, `from`, `to`) | `PortfolioHistoryResponse` |

#### `GET /api/portfolio`
- Status codes: `200`, `500`.

#### `POST /api/portfolio/trade`
- Rules:
  - `side` is `buy` or `sell`.
  - Buys require sufficient cash.
  - Sells require sufficient shares (shorting disallowed).
- Transaction (ACID): all DB writes for a trade — cash deduction/credit on `users_profile`, position upsert on `positions`, trade record insert on `trades`, and snapshot insert on `portfolio_snapshots` — execute within a single SQLite transaction. If any write fails, the entire transaction rolls back and no state changes. Half-done trades are never acceptable.
- Status codes:
  - `200` trade executed.
  - `400` business rule violation (`ApiErrorResponse`).
  - `422` invalid request schema (`ApiErrorResponse`).
  - `500` server error (`ApiErrorResponse`).

#### `GET /api/portfolio/history`
- Query parameters:
  - `limit` (optional int, default `200`, max `5000`)
  - `from` (optional ISO timestamp, inclusive)
  - `to` (optional ISO timestamp, inclusive)
- Status codes: `200`, `422`, `500`.

### Watchlist

| Method | Path | Request Model | Response Model |
|---|---|---|---|
| GET | `/api/watchlist` | none | `WatchlistResponse` |
| POST | `/api/watchlist` | `WatchlistAddRequest` | `WatchlistMutationResponse` |
| DELETE | `/api/watchlist/{ticker}` | path param `ticker` | `WatchlistMutationResponse` |

#### Status codes
- `GET /api/watchlist`: `200`, `500`
- `POST /api/watchlist`: `201`, `400`, `409`, `422`, `500`
- `DELETE /api/watchlist/{ticker}`: `200`, `404`, `422`, `500`

### Chat

| Method | Path | Request Model | Response Model |
|---|---|---|---|
| POST | `/api/chat` | `ChatRequest` | `ChatResponse` |

#### `POST /api/chat`
- Behavior:
  - Returns assistant message and proposed actions.
  - Backend executes returned actions and includes per-action outcome in `execution_results`.
- Status codes:
  - `200` success (even when some actions fail; failures appear in `execution_results`).
  - `422` invalid request schema.
  - `500` LLM/provider/internal error.

### System

| Method | Path | Request Model | Response Model |
|---|---|---|---|
| GET | `/api/health` | none | `HealthResponse` |

#### `GET /api/health`
- Purpose: health probe for local Docker and deployment platforms.
- Status codes:
  - `200` service healthy.
  - `503` dependencies unhealthy (optional if health checks are deep).

---

## 9. LLM Integration

When writing code to make calls to LLMs, use cerebras-inference skill to use LiteLLM via OpenRouter to the `openrouter/openai/gpt-4o` model with Cerebras as the inference provider. Structured Outputs should be used to interpret the results.

There is an OPENROUTER_API_KEY in the .env file in the project root.

### How It Works

When the user sends a chat message, the backend:

1. Loads the user's current portfolio context (cash, positions with P&L, watchlist with live prices, total portfolio value)
2. Loads recent conversation history from the `chat_messages` table
3. Constructs a prompt with a system message, portfolio context, conversation history, and the user's new message
4. Calls the LLM via LiteLLM → OpenRouter, requesting structured output, using the cerebras-inference skill
5. Parses the complete structured JSON response
6. Auto-executes any trades or watchlist changes specified in the response
7. Stores the message and executed actions in `chat_messages`
8. Returns the complete JSON response to the frontend (no token-by-token streaming — Cerebras inference is fast enough that a loading indicator is sufficient)

### Structured Output Schema

The LLM is instructed to respond with JSON matching this schema:

```json
{
  "message": "Your conversational response to the user",
  "trades": [
    {"ticker": "AAPL", "side": "buy", "quantity": 10}
  ],
  "watchlist_changes": [
    {"ticker": "PYPL", "action": "add"}
  ]
}
```

- `message` (required): The conversational text shown to the user
- `trades` (optional): Array of trades to auto-execute. Each trade goes through the same validation as manual trades (sufficient cash for buys, sufficient shares for sells)
- `watchlist_changes` (optional): Array of watchlist modifications

### Auto-Execution

Trades specified by the LLM execute automatically — no confirmation dialog. This is a deliberate design choice:
- It's a simulated environment with fake money, so the stakes are zero
- It creates an impressive, fluid demo experience
- It demonstrates agentic AI capabilities — the core theme of the course

If a trade fails validation (e.g., insufficient cash), the error is included in the chat response so the LLM can inform the user.

### System Prompt Guidance

The LLM should be prompted as "FinAlly, an AI trading assistant" with instructions to:
- Analyze portfolio composition, risk concentration, and P&L
- Suggest trades with reasoning
- Execute trades when the user asks or agrees
- Manage the watchlist proactively
- Be concise and data-driven in responses
- Always respond with valid structured JSON

### LLM Mock Mode

When `LLM_MOCK=true`, the backend returns deterministic mock responses instead of calling OpenRouter. This enables:
- Fast, free, reproducible E2E tests
- Development without an API key
- CI/CD pipelines

---

## 10. Frontend Design

### Layout

The frontend is a single-page application with a dense, terminal-inspired layout. The specific component architecture and layout system is up to the Frontend Engineer, but the UI should include these elements:

- **Watchlist panel** — grid/table of watched tickers with: ticker symbol, current price (flashing green/red on change), tick-to-tick change % (derived from `change_pct` in the SSE event — previous tick to current tick), and a sparkline mini-chart (accumulated from SSE since page load)
- **Main chart area** — larger chart for the currently selected ticker, with at minimum price over time. Clicking a ticker in the watchlist selects it here.
- **Portfolio heatmap** — treemap visualization where each rectangle is a position, sized by portfolio weight, colored by P&L (green = profit, red = loss)
- **P&L chart** — line chart showing total portfolio value over time, using data from `portfolio_snapshots`
- **Positions table** — tabular view of all positions: ticker, quantity, avg cost, current price, unrealized P&L, % change
- **Trade bar** — simple input area: ticker field, quantity field, buy button, sell button. Market orders, instant fill.
- **AI chat panel** — docked/collapsible sidebar. Message input, scrolling conversation history, loading indicator while waiting for LLM response. Trade executions and watchlist changes shown inline as confirmations.
- **Header** — portfolio total value (updating live), connection status indicator, cash balance

### Technical Notes

- Use `EventSource` for SSE connection to `/api/stream/prices`
- Canvas-based charting library preferred (Lightweight Charts or Recharts) for performance
- Price flash effect: on receiving a new price, briefly apply a CSS class with background color transition, then remove it
- All API calls go to the same origin (`/api/*`) — no CORS configuration needed
- Tailwind CSS for styling with a custom dark theme
- API payloads, validation rules, and execution semantics are defined by backend sections of this plan; frontend must treat them as the source of truth.

---

## 11. Docker & Deployment

### Multi-Stage Dockerfile

```
Stage 1: Node 20 slim
  - Copy frontend/
  - npm install && npm run build (produces static export)

Stage 2: Python 3.12 slim
  - Install uv
  - Copy backend/
  - uv sync (install Python dependencies from lockfile)
  - Copy frontend build output into a static/ directory
  - Expose port 8000
  - CMD: uvicorn serving FastAPI app
```

FastAPI serves the static frontend files and all API routes on port 8000.

### Docker Volume

The SQLite database persists via a named Docker volume:

```bash
docker run -v finally-data:/app/db -p 8000:8000 --env-file .env finally
```

The `db/` directory in the project root maps to `/app/db` in the container. The backend writes `finally.db` to this path.

### Start/Stop Scripts

**`scripts/start_mac.sh`** (macOS/Linux):
- Builds the Docker image if not already built (or if `--build` flag passed)
- Runs the container with the volume mount, port mapping, and `.env` file
- Prints the URL to access the app
- Optionally opens the browser

**`scripts/stop_mac.sh`** (macOS/Linux):
- Stops and removes the running container
- Does NOT remove the volume (data persists)

**`scripts/start_windows.ps1`** / **`scripts/stop_windows.ps1`**: PowerShell equivalents for Windows.

All scripts should be idempotent — safe to run multiple times.

### Optional Cloud Deployment

The container is designed to deploy to AWS App Runner, Render, or any container platform. A Terraform configuration for App Runner may be provided in a `deploy/` directory as a stretch goal, but is not part of the core build.

---

## 12. Testing Strategy

### TDD Definition (Red-Green-Refactor)

All implementation work follows strict TDD:
- **Red**: write a failing automated test that expresses one behavior from this plan
- **Green**: write the minimum production code to pass that test
- **Refactor**: improve design with tests still green
- Keep test increments small: one behavior at a time
- Do not merge feature code without tests that were written first for the behavior

### Test Layers and Scope

**Unit tests (primary, fast feedback)**:
- Backend (`pytest`): market simulator math and constraints, Massive parsing, portfolio/trade rules, LLM schema parsing and validation helpers
- Frontend (React Testing Library/Vitest): rendering, SSE state updates, price-flash behavior, watchlist interactions, chat loading/render states

**API integration tests (backend)**:
- Validate route contracts in Section 8: status codes, response shape, validation errors
- Validate trade and watchlist side effects against SQLite state
- Validate `LLM_MOCK=true` chat flow including execution summaries

**E2E tests (`test/` with Playwright)**:
- Run against the containerized app via `docker-compose.test.yml` for parity with runtime packaging
- Cover critical user journeys only: first launch, watchlist CRUD, buy/sell lifecycle, portfolio visuals present, mocked AI chat action execution, SSE reconnect behavior

### Environment and Determinism

- Default test mode uses `LLM_MOCK=true`
- Use deterministic simulator seeds in test runs where possible
- Each test suite starts from a clean database state

### Quality Gates

- Required before merge: unit + API integration tests passing
- Required before release/capstone demo: Playwright E2E suite passing
- Bug fixes must start with a failing regression test (Red) before applying code changes

---

## 13. Design Decisions (resolved)

The following decisions were made during planning and are already reflected in the sections above. Listed here for traceability.

- **LLM model**: `openrouter/openai/gpt-4o` via OpenRouter with Cerebras inference.
- **SSE ticker scope**: stream covers exactly the tickers in the `watchlist` table. Adding a ticker mid-session immediately adds it to the simulator — no restart needed.
- **SSE granularity**: one event per ticker per 500ms tick. Events are never batched. See Section 6 SSE Wire Contract for the normative framing.
- **SSE change % metric**: tick-to-tick only (`(price - previous_price) / previous_price * 100`). There is no daily-change baseline; the frontend displays the tick-to-tick `change_pct` from the SSE event.
- **Error envelope**: all error responses (business, validation, server) use `ApiErrorResponse`. FastAPI's default 422 handler is overridden to emit the same envelope.
- **Trade atomicity**: every trade executes in a single ACID SQLite transaction covering all four writes (cash, position, trade log, snapshot). Partial state changes are never acceptable.
- **`portfolio_snapshots` recording**: after trades only (no 30-second background task). Retain the most recent 2,764,800 rows.
- **Portfolio P&L chart intent**: the chart reflects trade checkpoints only. It does not attempt to show market movement between trades.
- **`chat_messages.actions`**: post-execution summary JSON recording the outcome (success/failure) of each trade and watchlist change the LLM requested.
- **Shorting**: explicitly disallowed — sells validate that `quantity <= held shares`.
- **`/api/portfolio` response shape**: defined in Section 8.
- **Primary keys**: `INTEGER PRIMARY KEY` (SQLite autoincrement) for all tables except `users_profile` (which keeps `id TEXT PRIMARY KEY = "default"`).
- **`users_profile` table**: retained as-is for future multi-user extensibility.
- **Chat history**: persisted to `chat_messages` table.
- **E2E test infrastructure**: Playwright runs against the containerized app via `docker-compose.test.yml` for environment parity.
