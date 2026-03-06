# Market Data — Component Summary

**Status: Complete and production-ready.**
92 tests passing, 96% coverage, lint clean, all review items resolved.

---

## What Was Built

The market data subsystem is a self-contained Python package at `backend/app/market/`. It provides real-time price streaming for the FinAlly trading workstation via a two-implementation strategy pattern: a built-in GBM simulator (default) and a Massive (Polygon.io) REST API client (optional). All downstream code — SSE streaming, portfolio valuation, trade execution — is source-agnostic.

---

## Key Files

| File | Purpose |
|---|---|
| `app/market/models.py` | `PriceUpdate` — immutable frozen dataclass, the only type that leaves this layer |
| `app/market/cache.py` | `PriceCache` — thread-safe in-memory store; writers update it, SSE reads it |
| `app/market/interface.py` | `MarketDataSource` — abstract base class both implementations satisfy |
| `app/market/simulator.py` | `GBMSimulator` + `SimulatorDataSource` — GBM math, correlated moves via Cholesky |
| `app/market/seed_prices.py` | Seed prices, per-ticker volatility/drift params, correlation group definitions |
| `app/market/massive_client.py` | `MassiveDataSource` — Polygon.io REST poller via `asyncio.to_thread` |
| `app/market/factory.py` | `create_market_data_source()` — picks simulator or Massive based on env var |
| `app/market/stream.py` | `create_stream_router()` — FastAPI SSE endpoint (`GET /api/stream/prices`) |

---

## How It Works

```
GBMSimulator (or Massive REST poller)
        │  writes every 500ms
        ▼
   PriceCache (thread-safe, single source of truth)
        │  reads every 500ms
        ▼
   SSE stream → Frontend EventSource
```

1. On startup, `create_market_data_source(cache)` returns the right source based on `MASSIVE_API_KEY`.
2. `await source.start(tickers)` seeds the cache and launches the background task.
3. The SSE endpoint reads `cache.get_all()` every 500ms and emits one `event: price_update` per ticker.
4. Adding/removing tickers mid-session calls `await source.add_ticker()` / `remove_ticker()` — no restart needed.
5. On shutdown, `await source.stop()` cancels the background task and awaits cleanup.

---

## Simulator Details

- **Model**: Geometric Brownian Motion — `S(t+dt) = S(t) * exp((mu - σ²/2)·dt + σ·√dt·Z)`
- **dt**: `0.5 / (252 × 6.5 × 3600) ≈ 8.48e-8` (500ms as fraction of a trading year)
- **Prices never go negative**: `exp()` is always positive
- **Correlated moves**: Cholesky decomposition of a sector correlation matrix
  - Tech (AAPL, GOOGL, MSFT, AMZN, META, NVDA, NFLX): ρ = 0.6
  - Finance (JPM, V): ρ = 0.5
  - Cross-sector / TSLA: ρ = 0.3
- **Random events**: ~0.1% probability per tick → sudden 2–5% move for drama

---

## SSE Wire Contract

```
event: price_update
data: {"type":"price_update","data":{"ticker":"AAPL","price":192.31,"previous_price":191.94,"change_pct":0.1928,"timestamp":"2026-03-06T14:21:11.100Z"}}

```

- One event per ticker, never batched
- `change_pct` is tick-to-tick
- `timestamp` is ISO 8601 UTC
- Heartbeat comment (`:\n\n`) every 15s
- `retry: 3000` on connection

---

## Test Coverage

```
92 tests, 0 failures  |  96% overall coverage  |  ruff lint: clean

tests/market/test_cache.py              14 tests  — thread-safety, CRUD, version
tests/market/test_factory.py             7 tests  — env-var source selection
tests/market/test_massive.py            13 tests  — poll logic, normalization, errors
tests/market/test_models.py             11 tests  — PriceUpdate math and immutability
tests/market/test_simulator.py          20 tests  — GBM math, Cholesky, 10-ticker set
tests/market/test_simulator_source.py  10 tests  — lifecycle, dynamic ticker add/remove
tests/market/test_stream.py            17 tests  — SSE wire format, event generation
```

---

## Running the Demo

A live terminal dashboard lets you see the simulator in action:

```bash
cd backend
uv run market_data_demo.py
```

Displays a Rich UI with live prices, sparklines, tick-to-tick changes, and an event log for notable moves (>1%). Runs for 60 seconds then shows a session summary. Press `Ctrl+C` to exit early.

---

## Detailed Documentation

| Document | Contents |
|---|---|
| `planning/archive/MARKET_DATA_DESIGN.md` | Full implementation design — all modules, lifecycle, error handling |
| `planning/archive/MARKET_SIMULATOR.md` | GBM math, Cholesky, seed prices, per-ticker params |
| `planning/archive/MARKET_INTERFACE.md` | Abstract interface, PriceCache, factory, SSE integration |
| `planning/archive/MASSIVE_API.md` | Polygon.io/Massive REST API reference |
| `planning/MARKET_DATA_REVIEW.md` | Latest code review — 92 tests, issues found and resolved |
| `planning/archive/MARKET_DATA_REVIEW.md` | Prior code review (2026-02-10, 73 tests) |
