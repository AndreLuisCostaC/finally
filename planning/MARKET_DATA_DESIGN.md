# Market Data Backend — Design

Implementation-ready design for the FinAlly market data subsystem. Covers the unified interface, in-memory price cache, GBM simulator, Massive API client, SSE streaming endpoint, and FastAPI lifecycle integration.

Everything in this document lives under `backend/app/market/`.

> **Status**: Component implemented and reviewed. See `planning/archive/MARKET_DATA_REVIEW.md` for the post-implementation code review. Archived pre-implementation design is in `planning/archive/MARKET_DATA_DESIGN.md`.

---

## Table of Contents

1. [File Structure](#1-file-structure)
2. [Data Model — `models.py`](#2-data-model)
3. [Price Cache — `cache.py`](#3-price-cache)
4. [Abstract Interface — `interface.py`](#4-abstract-interface)
5. [Seed Prices & Ticker Parameters — `seed_prices.py`](#5-seed-prices--ticker-parameters)
6. [GBM Simulator — `simulator.py`](#6-gbm-simulator)
7. [Massive API Client — `massive_client.py`](#7-massive-api-client)
8. [Factory — `factory.py`](#8-factory)
9. [SSE Streaming Endpoint — `stream.py`](#9-sse-streaming-endpoint)
10. [FastAPI Lifecycle Integration](#10-fastapi-lifecycle-integration)
11. [Watchlist Coordination](#11-watchlist-coordination)
12. [Error Handling & Edge Cases](#12-error-handling--edge-cases)
13. [Configuration Summary](#13-configuration-summary)

---

## 1. File Structure

```
backend/
  app/
    market/
      __init__.py             # Re-exports: PriceUpdate, PriceCache, MarketDataSource, create_market_data_source
      models.py               # PriceUpdate dataclass
      cache.py                # PriceCache (thread-safe in-memory store)
      interface.py            # MarketDataSource ABC
      seed_prices.py          # SEED_PRICES, TICKER_PARAMS, DEFAULT_PARAMS, CORRELATION_GROUPS
      simulator.py            # GBMSimulator + SimulatorDataSource
      massive_client.py       # MassiveDataSource
      factory.py              # create_market_data_source()
      stream.py               # SSE endpoint (FastAPI router)
```

Each file has a single responsibility. The `__init__.py` re-exports the public API so the rest of the backend imports from `app.market` without reaching into submodules.

---

## 2. Data Model

**File: `backend/app/market/models.py`**

`PriceUpdate` is the only data structure that leaves the market data layer. Every downstream consumer — SSE streaming, portfolio valuation, trade execution — works exclusively with this type.

```python
from __future__ import annotations
import time
from dataclasses import dataclass, field

@dataclass(frozen=True, slots=True)
class PriceUpdate:
    """Immutable snapshot of a single ticker's price at a point in time."""

    ticker: str
    price: float
    previous_price: float
    timestamp: float = field(default_factory=time.time)  # Unix seconds

    @property
    def change(self) -> float:
        return round(self.price - self.previous_price, 4)

    @property
    def change_percent(self) -> float:
        if self.previous_price == 0:
            return 0.0
        return round((self.price - self.previous_price) / self.previous_price * 100, 4)

    @property
    def direction(self) -> str:
        if self.price > self.previous_price:
            return "up"
        elif self.price < self.previous_price:
            return "down"
        return "flat"

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "price": self.price,
            "previous_price": self.previous_price,
            "timestamp": self.timestamp,
            "change": self.change,
            "change_percent": self.change_percent,
            "direction": self.direction,
        }
```

### Design decisions

- **`frozen=True`**: Price updates are immutable value objects — safe to share across async tasks without copying.
- **`slots=True`**: Minor memory optimization; we create many of these per second.
- **Computed properties** (`change`, `change_percent`, `direction`): Derived on access so they can never be inconsistent with the raw prices.
- **`to_dict()`**: Single serialization point used by both SSE and REST responses.

---

## 3. Price Cache

**File: `backend/app/market/cache.py`**

The price cache is the central data hub. Data sources write to it; SSE streaming and portfolio valuation read from it. It must be thread-safe because the Massive poller runs via `asyncio.to_thread()` (a real OS thread) while SSE reads happen on the async event loop.

```python
from __future__ import annotations
import time
from threading import Lock
from .models import PriceUpdate

class PriceCache:
    """Thread-safe in-memory cache of the latest price for each ticker."""

    def __init__(self) -> None:
        self._prices: dict[str, PriceUpdate] = {}
        self._lock = Lock()
        self._version: int = 0  # Monotonically increasing; bumped on every update

    def update(self, ticker: str, price: float, timestamp: float | None = None) -> PriceUpdate:
        with self._lock:
            ts = timestamp or time.time()
            prev = self._prices.get(ticker)
            previous_price = prev.price if prev else price
            update = PriceUpdate(
                ticker=ticker,
                price=round(price, 2),
                previous_price=round(previous_price, 2),
                timestamp=ts,
            )
            self._prices[ticker] = update
            self._version += 1
            return update

    def get(self, ticker: str) -> PriceUpdate | None:
        with self._lock:
            return self._prices.get(ticker)

    def get_all(self) -> dict[str, PriceUpdate]:
        with self._lock:
            return dict(self._prices)

    def get_price(self, ticker: str) -> float | None:
        update = self.get(ticker)
        return update.price if update else None

    def remove(self, ticker: str) -> None:
        with self._lock:
            self._prices.pop(ticker, None)

    @property
    def version(self) -> int:
        return self._version

    def __len__(self) -> int:
        with self._lock:
            return len(self._prices)

    def __contains__(self, ticker: str) -> bool:
        with self._lock:
            return ticker in self._prices
```

### Version counter

The SSE streaming loop polls the cache every ~500ms. The version counter lets it skip emission when nothing changed (Massive API only updates every 15s):

```python
last_version = -1
while True:
    if price_cache.version != last_version:
        last_version = price_cache.version
        # emit all current prices
    await asyncio.sleep(0.5)
```

### Thread safety rationale

`threading.Lock` is used instead of `asyncio.Lock` because:
- The Massive client's synchronous `get_snapshot_all()` runs in `asyncio.to_thread()`, which is a real OS thread — `asyncio.Lock` would not protect against that.
- `threading.Lock` works correctly from both sync threads and the async event loop.

---

## 4. Abstract Interface

**File: `backend/app/market/interface.py`**

```python
from __future__ import annotations
from abc import ABC, abstractmethod

class MarketDataSource(ABC):
    """Contract for market data providers.

    Implementations push price updates into a shared PriceCache on their own
    schedule. Downstream code never calls the data source directly for prices —
    it reads from the cache.
    """

    @abstractmethod
    async def start(self, tickers: list[str]) -> None:
        """Begin producing price updates. Called exactly once at app startup."""

    @abstractmethod
    async def stop(self) -> None:
        """Stop the background task and release resources. Safe to call multiple times."""

    @abstractmethod
    async def add_ticker(self, ticker: str) -> None:
        """Add a ticker to the active set. No-op if already present."""

    @abstractmethod
    async def remove_ticker(self, ticker: str) -> None:
        """Remove a ticker. Also removes it from the PriceCache."""

    @abstractmethod
    def get_tickers(self) -> list[str]:
        """Return the current list of actively tracked tickers."""
```

### Push vs pull model

The source pushes updates into the cache on its own schedule rather than returning prices on demand. This decouples timing: the simulator ticks at 500ms, Massive polls at 15s, but SSE always reads from the cache at its own 500ms cadence. The SSE layer is completely agnostic to which data source is active.

---

## 5. Seed Prices & Ticker Parameters

**File: `backend/app/market/seed_prices.py`**

Constants only — no logic, no imports beyond stdlib. Shared by the simulator (initial prices and GBM parameters) and potentially by the Massive client as fallback prices before the first API response.

```python
SEED_PRICES: dict[str, float] = {
    "AAPL": 190.00,
    "GOOGL": 175.00,
    "MSFT": 420.00,
    "AMZN": 185.00,
    "TSLA": 250.00,
    "NVDA": 800.00,
    "META": 500.00,
    "JPM":  195.00,
    "V":    280.00,
    "NFLX": 600.00,
}

TICKER_PARAMS: dict[str, dict[str, float]] = {
    "AAPL":  {"sigma": 0.22, "mu": 0.05},
    "GOOGL": {"sigma": 0.25, "mu": 0.05},
    "MSFT":  {"sigma": 0.20, "mu": 0.05},
    "AMZN":  {"sigma": 0.28, "mu": 0.05},
    "TSLA":  {"sigma": 0.50, "mu": 0.03},   # High volatility
    "NVDA":  {"sigma": 0.40, "mu": 0.08},   # High vol, strong drift
    "META":  {"sigma": 0.30, "mu": 0.05},
    "JPM":   {"sigma": 0.18, "mu": 0.04},   # Lower vol (bank)
    "V":     {"sigma": 0.17, "mu": 0.04},   # Lower vol (payments)
    "NFLX":  {"sigma": 0.35, "mu": 0.05},
}

DEFAULT_PARAMS: dict[str, float] = {"sigma": 0.25, "mu": 0.05}

CORRELATION_GROUPS: dict[str, set[str]] = {
    "tech":    {"AAPL", "GOOGL", "MSFT", "AMZN", "META", "NVDA", "NFLX"},
    "finance": {"JPM", "V"},
}

INTRA_TECH_CORR    = 0.6
INTRA_FINANCE_CORR = 0.5
CROSS_GROUP_CORR   = 0.3
TSLA_CORR          = 0.3   # TSLA does its own thing
DEFAULT_CORR       = 0.3   # Unknown tickers
```

---

## 6. GBM Simulator

**File: `backend/app/market/simulator.py`**

Two classes in one file:
- `GBMSimulator` — pure math engine; stateful, holds current prices, advances one step at a time.
- `SimulatorDataSource` — `MarketDataSource` implementation wrapping `GBMSimulator` in an async loop that writes to `PriceCache`.

### 6.1 GBM Math

At each time step, prices evolve as:

```
S(t+dt) = S(t) * exp((mu - sigma²/2) * dt + sigma * sqrt(dt) * Z)
```

Where:
- `S(t)` = current price
- `mu` = annualized drift (expected return), e.g. `0.05`
- `sigma` = annualized volatility, e.g. `0.22`
- `dt` = time step as fraction of a trading year
- `Z` = standard normal random variable

For 500ms updates:
```
dt = 0.5 / (252 * 6.5 * 3600) ≈ 8.48e-8
```

This tiny `dt` produces sub-cent moves per tick that accumulate naturally into realistic price paths.

### 6.2 Correlated Moves

Real stocks move together within sectors. GBM uses **Cholesky decomposition** of a correlation matrix:

```
Z_correlated = L @ Z_independent
```

Where `L = cholesky(C)` and `C` is the correlation matrix built from sector groupings.

Correlation structure:
- Tech stocks (AAPL, GOOGL, MSFT, AMZN, META, NVDA, NFLX): **0.6** intra-group
- Finance stocks (JPM, V): **0.5** intra-group
- TSLA with anything: **0.3** (high volatility, does its own thing)
- Cross-sector / unknown: **0.3** baseline

### 6.3 Random Events

Every step, each ticker has a `~0.1%` probability of a sudden 2–5% move in either direction. With 10 tickers at 500ms intervals, this produces roughly one event somewhere on the watchlist every 50 seconds — enough visual drama without destabilizing prices.

### 6.4 GBMSimulator Class

```python
class GBMSimulator:
    TRADING_SECONDS_PER_YEAR = 252 * 6.5 * 3600  # 5,896,800
    DEFAULT_DT = 0.5 / TRADING_SECONDS_PER_YEAR   # ~8.48e-8

    def __init__(self, tickers: list[str], dt: float = DEFAULT_DT,
                 event_probability: float = 0.001) -> None: ...

    def step(self) -> dict[str, float]:
        """Advance all tickers one time step. Returns {ticker: new_price}."""
        # 1. Draw independent N(0,1) for each ticker
        # 2. Apply Cholesky: z_correlated = L @ z_independent
        # 3. For each ticker: price *= exp(drift + diffusion * z)
        # 4. Apply random event shock with EVENT_PROB probability
        # 5. Return rounded prices

    def add_ticker(self, ticker: str) -> None:
        """Add a ticker; rebuilds Cholesky matrix."""

    def remove_ticker(self, ticker: str) -> None:
        """Remove a ticker; rebuilds Cholesky matrix."""

    def get_price(self, ticker: str) -> float | None: ...

    def _rebuild_cholesky(self) -> None:
        """O(n²) build. n < 50, so negligible cost."""

    @staticmethod
    def _pairwise_correlation(t1: str, t2: str) -> float: ...
```

### 6.5 SimulatorDataSource Class

```python
class SimulatorDataSource(MarketDataSource):
    def __init__(self, price_cache: PriceCache, update_interval: float = 0.5,
                 event_probability: float = 0.001) -> None: ...

    async def start(self, tickers: list[str]) -> None:
        # 1. Create GBMSimulator with initial tickers
        # 2. Seed PriceCache with initial prices (SSE gets data immediately on connect)
        # 3. Launch asyncio background task _run_loop()

    async def stop(self) -> None:
        # Cancel and await the background task

    async def add_ticker(self, ticker: str) -> None:
        # Delegates to self._sim.add_ticker()

    async def remove_ticker(self, ticker: str) -> None:
        # Delegates to self._sim.remove_ticker() + self._cache.remove()

    def get_tickers(self) -> list[str]: ...

    async def _run_loop(self) -> None:
        while True:
            prices = self._sim.step()
            for ticker, price in prices.items():
                self._cache.update(ticker=ticker, price=price)
            await asyncio.sleep(self._interval)
```

**Behavior notes:**
- Prices never go negative — GBM uses `exp()`, always positive.
- Cholesky rebuild is O(n²) but n < 50, so cost is negligible.
- Dynamic ticker addition takes effect on the next `step()` call.

---

## 7. Massive API Client

**File: `backend/app/market/massive_client.py`**

For full Massive API reference (formerly Polygon.io), see `planning/MASSIVE_API.md`.

```python
class MassiveDataSource(MarketDataSource):
    def __init__(self, api_key: str, price_cache: PriceCache,
                 poll_interval: float = 15.0) -> None:
        # RESTClient is lazily imported inside start() to keep massive optional
        self._api_key = api_key
        self._cache = price_cache
        self._interval = poll_interval
        self._tickers: list[str] = []
        self._task: asyncio.Task | None = None

    async def start(self, tickers: list[str]) -> None:
        from massive import RESTClient
        self._client = RESTClient(api_key=self._api_key)
        self._tickers = list(tickers)
        self._task = asyncio.create_task(self._poll_loop(), name="massive-poller")

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def add_ticker(self, ticker: str) -> None:
        if ticker not in self._tickers:
            self._tickers.append(ticker)

    async def remove_ticker(self, ticker: str) -> None:
        self._tickers = [t for t in self._tickers if t != ticker]
        self._cache.remove(ticker)

    def get_tickers(self) -> list[str]:
        return list(self._tickers)

    async def _poll_loop(self) -> None:
        while True:
            await self._poll_once()
            await asyncio.sleep(self._interval)

    async def _poll_once(self) -> None:
        if not self._tickers:
            return
        # Run synchronous Massive client in thread pool to avoid blocking event loop
        snapshots = await asyncio.to_thread(
            self._fetch_snapshots, list(self._tickers)
        )
        for snap in snapshots:
            self._cache.update(
                ticker=snap.ticker,
                price=snap.last_trade.price,
                timestamp=snap.last_trade.timestamp / 1000,  # ms → seconds
            )

    def _fetch_snapshots(self, tickers: list[str]):
        return self._client.get_snapshot_all("stocks", tickers=tickers)
```

**Key fields extracted per snapshot:**
- `snap.ticker` — ticker symbol
- `snap.last_trade.price` — current price
- `snap.last_trade.timestamp` — Unix milliseconds (divide by 1000 for seconds)

**Rate limits:**
- Free tier: 5 req/min → `poll_interval = 15.0`
- Paid tiers: unlimited → `poll_interval` can be as low as `2.0`

---

## 8. Factory

**File: `backend/app/market/factory.py`**

Selects the data source at startup based on environment variables. The `massive` package is only imported when needed — the simulator works without it installed.

```python
import os
from .cache import PriceCache
from .interface import MarketDataSource

def create_market_data_source(price_cache: PriceCache) -> MarketDataSource:
    """Create the appropriate market data source based on environment."""
    api_key = os.environ.get("MASSIVE_API_KEY", "").strip()

    if api_key:
        from .massive_client import MassiveDataSource
        return MassiveDataSource(api_key=api_key, price_cache=price_cache)
    else:
        from .simulator import SimulatorDataSource
        return SimulatorDataSource(price_cache=price_cache)
```

---

## 9. SSE Streaming Endpoint

**File: `backend/app/market/stream.py`**

Implements `GET /api/stream/prices`. Each SSE event carries exactly one ticker per the wire contract in `PLAN.md` Section 6.

```python
import asyncio
import json
import logging
from datetime import datetime, timezone

from fastapi import Request
from fastapi.responses import StreamingResponse

from .cache import PriceCache

logger = logging.getLogger(__name__)


def create_stream_router(price_cache: PriceCache):
    from fastapi import APIRouter
    router = APIRouter()

    @router.get("/stream/prices")
    async def stream_prices(request: Request):
        return StreamingResponse(
            _generate_events(request, price_cache),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",    # Disable Nginx buffering
                "Connection": "keep-alive",
            },
        )

    return router


async def _generate_events(request: Request, price_cache: PriceCache):
    """Async generator: emits one SSE event per ticker every 500ms."""
    yield "retry: 3000\n\n"   # Tell browser to reconnect after 3s on disconnect

    heartbeat_counter = 0
    HEARTBEAT_EVERY = 30  # ticks × 500ms = 15 seconds

    while True:
        if await request.is_disconnected():
            break

        prices = price_cache.get_all()
        for ticker, p in prices.items():
            ts_iso = (
                datetime.fromtimestamp(p.timestamp, tz=timezone.utc)
                .isoformat()
                .replace("+00:00", "Z")
            )
            payload = {
                "type": "price_update",
                "data": {
                    "ticker": p.ticker,
                    "price": p.price,
                    "previous_price": p.previous_price,
                    "change_pct": round(p.change_percent, 4),
                    "timestamp": ts_iso,
                },
            }
            yield f"event: price_update\ndata: {json.dumps(payload)}\n\n"

        heartbeat_counter += 1
        if heartbeat_counter >= HEARTBEAT_EVERY:
            yield ":\n\n"   # SSE comment heartbeat keeps connection alive through proxies
            heartbeat_counter = 0

        await asyncio.sleep(0.5)
```

### SSE Wire Contract (normative)

Transport: `Content-Type: text/event-stream`, UTF-8.

Each price update:
```
event: price_update
data: {"type":"price_update","data":{"ticker":"AAPL","price":192.31,"previous_price":191.94,"change_pct":0.1929,"timestamp":"2026-03-05T14:21:11.100Z"}}

```

Rules:
- Exactly one ticker per `data:` line. Never batch multiple tickers.
- `change_pct` is tick-to-tick: `(price - previous_price) / previous_price * 100`.
- `timestamp` is ISO 8601 UTC.
- Heartbeat comment (`:\n\n`) every 15 seconds.
- `retry: 3000` directive on connection start.

---

## 10. FastAPI Lifecycle Integration

Market data starts and stops with the FastAPI application via the `lifespan` context manager.

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.market import PriceCache, create_market_data_source

price_cache = PriceCache()
market_source = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global market_source

    # Read initial tickers from the database
    from app.db import get_watchlist_tickers
    initial_tickers = await get_watchlist_tickers()

    market_source = create_market_data_source(price_cache)
    await market_source.start(initial_tickers)

    yield  # Application runs here

    await market_source.stop()


app = FastAPI(lifespan=lifespan)
```

---

## 11. Watchlist Coordination

When a ticker is added or removed from the watchlist via the REST API, the market data source and price cache are updated immediately — no restart required.

```python
# In the watchlist API route handler:

async def add_ticker_to_watchlist(ticker: str):
    # 1. Insert into watchlist table (raises 409 if duplicate)
    await db.insert_watchlist(ticker)
    # 2. Add to the live market data source
    await market_source.add_ticker(ticker)
    # Cache update happens automatically on the next simulator step / Massive poll

async def remove_ticker_from_watchlist(ticker: str):
    # 1. Delete from watchlist table (raises 404 if not found)
    await db.delete_watchlist(ticker)
    # 2. Remove from the live source and price cache immediately
    await market_source.remove_ticker(ticker)
```

The `price_cache` and `market_source` are application-level singletons, injected as FastAPI dependencies or accessed via app state.

---

## 12. Error Handling & Edge Cases

| Scenario | Behavior |
|---|---|
| Simulator step raises exception | Logged at ERROR; loop continues with `await asyncio.sleep(interval)` |
| Massive `get_snapshot_all` raises exception | Logged at ERROR; poll continues on next interval |
| Massive HTTP 429 (rate limit) | Logged at WARNING; treated as a poll failure; next poll after interval |
| Massive HTTP 401 (bad key) | Logged at CRITICAL; loop continues (may be transient on startup) |
| Empty watchlist | Both sources return `{}` from step/poll; SSE emits no events that tick |
| Unknown ticker added dynamically | Simulator assigns `DEFAULT_PARAMS` + random seed price $50–$300; Massive fetches real data |
| Duplicate `add_ticker` call | No-op in both implementations; Cholesky not needlessly rebuilt |
| `stop()` called before `start()` | No-op (task is None) |
| SSE client disconnects | Generator detects `request.is_disconnected()` and exits cleanly |

---

## 13. Configuration Summary

| Parameter | Default | Env var / source |
|---|---|---|
| Data source | Simulator | `MASSIVE_API_KEY` absent or empty |
| Simulator tick interval | `0.5s` | Hard-coded; configurable in `SimulatorDataSource.__init__` |
| Simulator event probability | `0.001` | Hard-coded; configurable in `SimulatorDataSource.__init__` |
| Massive poll interval | `15.0s` | Hard-coded; configurable in `MassiveDataSource.__init__` |
| SSE emit interval | `0.5s` | Hard-coded in `_generate_events` |
| SSE heartbeat interval | `15s` | 30 ticks × 0.5s |
| SSE reconnect hint | `3000ms` | `retry: 3000` directive |
| Default ticker seed price | `$50–$300` random | `seed_prices.SEED_PRICES` for known tickers |
