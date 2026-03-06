# Market Data Backend — Design

Implementation-ready design for the FinAlly market data subsystem. Covers the unified interface, in-memory price cache, GBM simulator, Massive API client, SSE streaming endpoint, and FastAPI lifecycle integration.

Everything in this document lives under `backend/app/market/`.

> **Status**: Component implemented and reviewed. See `planning/archive/MARKET_DATA_REVIEW.md` for the post-implementation code review. Archived pre-implementation design is in `planning/archive/MARKET_DATA_DESIGN.md`.

# Market Data Backend — Detailed Design

Implementation-ready design for the FinAlly market data subsystem. Covers the
unified interface, in-memory price cache, GBM simulator, Massive API client,
SSE streaming endpoint, and FastAPI lifecycle integration.

Everything in this document lives under `backend/app/market/`.

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
12. [Testing Strategy](#12-testing-strategy)
13. [Error Handling & Edge Cases](#13-error-handling--edge-cases)
14. [Configuration Summary](#14-configuration-summary)

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

Each file has a single responsibility. The `__init__.py` re-exports the public
API so that the rest of the backend imports from `app.market` without reaching
into submodules.

```python
# backend/app/market/__init__.py
from .cache import PriceCache
from .factory import create_market_data_source
from .interface import MarketDataSource
from .models import PriceUpdate
from .stream import create_stream_router

__all__ = [
    "PriceUpdate",
    "PriceCache",
    "MarketDataSource",
    "create_market_data_source",
    "create_stream_router",
]
```

---

## 2. Data Model

**File: `backend/app/market/models.py`**

`PriceUpdate` is the only data structure that leaves the market data layer.
Every downstream consumer — SSE streaming, portfolio valuation, trade execution
— works exclusively with this type.

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
        """Absolute price change from previous update."""
        return round(self.price - self.previous_price, 4)

    @property
    def change_percent(self) -> float:
        """Percentage change from previous update (tick-to-tick)."""
        if self.previous_price == 0:
            return 0.0
        return round((self.price - self.previous_price) / self.previous_price * 100, 4)

    @property
    def direction(self) -> str:
        """'up', 'down', or 'flat'."""
        if self.price > self.previous_price:
            return "up"
        elif self.price < self.previous_price:
            return "down"
        return "flat"

    def to_sse_dict(self) -> dict:
        """Serialize to the SSE wire format defined in PLAN.md Section 6."""
        from datetime import datetime, timezone

        iso_ts = (
            datetime.fromtimestamp(self.timestamp, tz=timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        )
        return {
            "type": "price_update",
            "data": {
                "ticker": self.ticker,
                "price": self.price,
                "previous_price": self.previous_price,
                "change_pct": round(self.change_percent, 4),
                "timestamp": iso_ts,
            },
        }
```

### Design decisions

- **`frozen=True`**: Price updates are immutable value objects. Once created
  they never change, making them safe to share across async tasks without
  copying.
- **`slots=True`**: Minor memory optimization — many instances are created per
  second.
- **Computed properties** (`change`, `direction`, `change_percent`): Derived
  from `price` and `previous_price` so they can never be inconsistent.
- **`to_sse_dict()`**: Produces exactly the JSON shape required by the SSE Wire
  Contract in PLAN.md §6. The `change_pct` field name (not `change_percent`)
  matches the contract.

---

## 3. Price Cache

**File: `backend/app/market/cache.py`**

The price cache is the central data hub. Data sources write to it; SSE
streaming and portfolio valuation read from it. It must be thread-safe because
the simulator/poller may run in a thread pool executor while SSE reads happen
on the async event loop.

```python
from __future__ import annotations

import time
from threading import Lock

from .models import PriceUpdate


class PriceCache:
    """Thread-safe in-memory cache of the latest price for each ticker.

    Writers: SimulatorDataSource or MassiveDataSource (exactly one at a time).
    Readers: SSE streaming endpoint, portfolio valuation, trade execution.
    """

    def __init__(self) -> None:
        self._prices: dict[str, PriceUpdate] = {}
        self._lock = Lock()
        self._version: int = 0  # Bumped on every write

    def update(self, ticker: str, price: float, timestamp: float | None = None) -> PriceUpdate:
        """Record a new price for a ticker. Returns the new PriceUpdate.

        On the first update for a ticker, previous_price == price (direction='flat').
        """
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
        """Get the latest price for a single ticker, or None if unknown."""
        with self._lock:
            return self._prices.get(ticker)

    def get_all(self) -> dict[str, PriceUpdate]:
        """Snapshot of all current prices. Returns a shallow copy."""
        with self._lock:
            return dict(self._prices)

    def get_price(self, ticker: str) -> float | None:
        """Convenience: get just the price float, or None."""
        update = self.get(ticker)
        return update.price if update else None

    def remove(self, ticker: str) -> None:
        """Remove a ticker from the cache (called on watchlist removal)."""
        with self._lock:
            self._prices.pop(ticker, None)

    @property
    def version(self) -> int:
        """Monotonically increasing counter. Bumped on every update()."""
        return self._version

    def __len__(self) -> int:
        with self._lock:
            return len(self._prices)

    def __contains__(self, ticker: str) -> bool:
        with self._lock:
            return ticker in self._prices
```

### Why a version counter?

The SSE loop polls the cache every 500ms. The version counter lets the SSE
loop detect whether anything has changed since the last send and avoid
transmitting stale data. This matters most with the Massive client, which only
updates every 15 seconds.

```python
# SSE loop: only send when data has changed
last_version = -1
while True:
    current_version = price_cache.version
    if current_version != last_version:
        last_version = current_version
        # ... send events ...
    await asyncio.sleep(0.5)
```

---

## 4. Abstract Interface

**File: `backend/app/market/interface.py`**

```python
from __future__ import annotations

from abc import ABC, abstractmethod


class MarketDataSource(ABC):
    """Contract for market data providers.

    Implementations push price updates into a shared PriceCache on their own
    schedule. Downstream code reads from the cache — never from the source
    directly.

    Lifecycle:
        cache = PriceCache()
        source = create_market_data_source(cache)
        await source.start(["AAPL", "GOOGL", ...])   # starts background task
        await source.add_ticker("TSLA")              # immediate effect
        await source.remove_ticker("GOOGL")          # removed from cache too
        await source.stop()                          # graceful shutdown
    """

    @abstractmethod
    async def start(self, tickers: list[str]) -> None:
        """Begin producing price updates for the given tickers.

        Starts a background task that periodically writes to PriceCache.
        Must be called exactly once. The initial tickers are taken from the
        watchlist table at startup.
        """

    @abstractmethod
    async def stop(self) -> None:
        """Stop the background task and release resources.

        Safe to call multiple times. After stop(), no further writes to cache.
        """

    @abstractmethod
    async def add_ticker(self, ticker: str) -> None:
        """Add a ticker to the active set. No-op if already present.

        Takes effect on the next poll/step cycle. The watchlist API calls this
        after inserting a ticker into the DB.
        """

    @abstractmethod
    async def remove_ticker(self, ticker: str) -> None:
        """Remove a ticker from the active set. No-op if not present.

        Also removes the ticker from the PriceCache immediately.
        """

    @abstractmethod
    def get_tickers(self) -> list[str]:
        """Return the current list of actively tracked tickers."""
```

### Separation of concerns

The interface deliberately has **no price-reading methods**. Downstream code
reads prices from `PriceCache`, not from the source. This keeps the interface
minimal and means callers can't accidentally bypass the cache.

```
MarketDataSource ──writes──▶ PriceCache ──reads──▶ SSE / Portfolio / Trades
```

---

## 5. Seed Prices & Ticker Parameters

**File: `backend/app/market/seed_prices.py`**

```python
# Realistic starting prices for the default watchlist
SEED_PRICES: dict[str, float] = {
    "AAPL":  190.00,
    "GOOGL": 175.00,
    "MSFT":  420.00,
    "AMZN":  185.00,
    "TSLA":  250.00,
    "NVDA":  800.00,
    "META":  500.00,
    "JPM":   195.00,
    "V":     280.00,
    "NFLX":  600.00,
}

# Per-ticker GBM parameters
# sigma: annualized volatility   mu: annualized drift (expected return)
TICKER_PARAMS: dict[str, dict[str, float]] = {
    "AAPL":  {"sigma": 0.22, "mu": 0.05},
    "GOOGL": {"sigma": 0.25, "mu": 0.05},
    "MSFT":  {"sigma": 0.20, "mu": 0.05},
    "AMZN":  {"sigma": 0.28, "mu": 0.05},
    "TSLA":  {"sigma": 0.50, "mu": 0.03},  # High volatility
    "NVDA":  {"sigma": 0.40, "mu": 0.08},  # High vol, strong drift
    "META":  {"sigma": 0.30, "mu": 0.05},
    "JPM":   {"sigma": 0.18, "mu": 0.04},  # Low vol (bank)
    "V":     {"sigma": 0.17, "mu": 0.04},  # Low vol (payments)
    "NFLX":  {"sigma": 0.35, "mu": 0.05},
}

# Fallback for dynamically added tickers not in the list above
DEFAULT_PARAMS: dict[str, float] = {"sigma": 0.25, "mu": 0.05}

# Sector groupings for the Cholesky correlation matrix
CORRELATION_GROUPS: dict[str, set[str]] = {
    "tech":    {"AAPL", "GOOGL", "MSFT", "AMZN", "META", "NVDA", "NFLX"},
    "finance": {"JPM", "V"},
}

# Pairwise correlation coefficients
INTRA_TECH_CORR    = 0.6   # Within tech sector
INTRA_FINANCE_CORR = 0.5   # Within finance sector
CROSS_GROUP_CORR   = 0.3   # Across sectors / unknown
TSLA_CORR          = 0.3   # TSLA does its own thing
```

---

## 6. GBM Simulator

**File: `backend/app/market/simulator.py`**

### Math background

Geometric Brownian Motion (GBM) is the standard Black-Scholes price model.
Prices evolve as:

```
S(t+dt) = S(t) * exp((mu - sigma²/2) * dt + sigma * sqrt(dt) * Z)
```

Where:
- `mu` = annualized drift (e.g. `0.05` = 5% expected annual return)
- `sigma` = annualized volatility (e.g. `0.22` = 22% annual vol)
- `dt` = time step as fraction of a trading year
- `Z` ~ N(0, 1) standard normal

For 500ms ticks over 252 trading days × 6.5 hours/day:
```
dt = 0.5 / (252 × 6.5 × 3600) ≈ 8.48e-8
```

This produces sub-cent moves per tick that accumulate naturally over time.

### Correlated moves via Cholesky decomposition

Real stocks don't move independently — tech stocks tend to move together. To
generate correlated draws:

1. Build correlation matrix `C` (size n×n) from pairwise coefficients
2. Compute lower-triangular `L = cholesky(C)` once at startup
3. Each step: draw `Z_independent ~ N(0,I)` then `Z_correlated = L @ Z_independent`

The Cholesky matrix is rebuilt whenever tickers are added or removed. This is
O(n²) but n < 50 tickers, so it takes < 1ms.

### Implementation

```python
import asyncio
import logging
import math
import random

import numpy as np

from .cache import PriceCache
from .interface import MarketDataSource
from .seed_prices import (
    CORRELATION_GROUPS, CROSS_GROUP_CORR, DEFAULT_PARAMS,
    INTRA_FINANCE_CORR, INTRA_TECH_CORR, SEED_PRICES,
    TICKER_PARAMS, TSLA_CORR,
)

logger = logging.getLogger(__name__)


class GBMSimulator:
    """Generates correlated GBM price paths for multiple tickers."""

    # 500ms as a fraction of a trading year
    TRADING_SECONDS_PER_YEAR = 252 * 6.5 * 3600  # 5,896,800
    DEFAULT_DT = 0.5 / TRADING_SECONDS_PER_YEAR   # ~8.48e-8

    def __init__(
        self,
        tickers: list[str],
        dt: float = DEFAULT_DT,
        event_probability: float = 0.001,
    ) -> None:
        self._dt = dt
        self._event_prob = event_probability
        self._tickers: list[str] = []
        self._prices: dict[str, float] = {}
        self._params: dict[str, dict[str, float]] = {}
        self._cholesky: np.ndarray | None = None

        for ticker in tickers:
            self._add_ticker_internal(ticker)
        self._rebuild_cholesky()

    def step(self) -> dict[str, float]:
        """Advance all tickers one time step. Returns {ticker: new_price}.

        Hot path — called every 500ms. Designed to be fast.
        """
        n = len(self._tickers)
        if n == 0:
            return {}

        z = np.random.standard_normal(n)
        if self._cholesky is not None:
            z = self._cholesky @ z  # Apply correlation structure

        result: dict[str, float] = {}
        for i, ticker in enumerate(self._tickers):
            mu = self._params[ticker]["mu"]
            sigma = self._params[ticker]["sigma"]

            # GBM step
            drift = (mu - 0.5 * sigma**2) * self._dt
            diffusion = sigma * math.sqrt(self._dt) * z[i]
            self._prices[ticker] *= math.exp(drift + diffusion)

            # Random event: ~0.1% per tick ≈ one event per 50s across 10 tickers
            if random.random() < self._event_prob:
                shock = random.uniform(0.02, 0.05) * random.choice([-1, 1])
                self._prices[ticker] *= (1 + shock)

            result[ticker] = round(self._prices[ticker], 2)

        return result

    def add_ticker(self, ticker: str) -> None:
        """Add a ticker and rebuild the Cholesky matrix."""
        if ticker in self._prices:
            return
        self._add_ticker_internal(ticker)
        self._rebuild_cholesky()

    def remove_ticker(self, ticker: str) -> None:
        """Remove a ticker and rebuild the Cholesky matrix."""
        if ticker not in self._prices:
            return
        self._tickers.remove(ticker)
        del self._prices[ticker]
        del self._params[ticker]
        self._rebuild_cholesky()

    def get_price(self, ticker: str) -> float | None:
        return self._prices.get(ticker)

    def get_tickers(self) -> list[str]:
        return list(self._tickers)

    def _add_ticker_internal(self, ticker: str) -> None:
        """Add without rebuilding Cholesky (batch initialization)."""
        self._tickers.append(ticker)
        self._prices[ticker] = SEED_PRICES.get(ticker, random.uniform(50.0, 300.0))
        self._params[ticker] = dict(TICKER_PARAMS.get(ticker, DEFAULT_PARAMS))

    def _rebuild_cholesky(self) -> None:
        """Rebuild correlation matrix Cholesky decomposition."""
        n = len(self._tickers)
        if n <= 1:
            self._cholesky = None
            return

        corr = np.eye(n)
        for i in range(n):
            for j in range(i + 1, n):
                rho = self._pairwise_correlation(self._tickers[i], self._tickers[j])
                corr[i, j] = rho
                corr[j, i] = rho

        self._cholesky = np.linalg.cholesky(corr)

    @staticmethod
    def _pairwise_correlation(t1: str, t2: str) -> float:
        """Correlation between two tickers based on sector membership."""
        tech = CORRELATION_GROUPS["tech"]
        finance = CORRELATION_GROUPS["finance"]

        if t1 == "TSLA" or t2 == "TSLA":
            return TSLA_CORR
        if t1 in tech and t2 in tech:
            return INTRA_TECH_CORR
        if t1 in finance and t2 in finance:
            return INTRA_FINANCE_CORR
        return CROSS_GROUP_CORR


class SimulatorDataSource(MarketDataSource):
    """MarketDataSource that wraps GBMSimulator in an async loop."""

    def __init__(
        self,
        price_cache: PriceCache,
        update_interval: float = 0.5,
        event_probability: float = 0.001,
    ) -> None:
        self._cache = price_cache
        self._interval = update_interval
        self._event_prob = event_probability
        self._sim: GBMSimulator | None = None
        self._task: asyncio.Task | None = None

    async def start(self, tickers: list[str]) -> None:
        self._sim = GBMSimulator(tickers=tickers, event_probability=self._event_prob)
        # Seed cache so SSE has data immediately on first connect
        for ticker in tickers:
            price = self._sim.get_price(ticker)
            if price is not None:
                self._cache.update(ticker=ticker, price=price)
        self._task = asyncio.create_task(self._run_loop(), name="simulator-loop")
        logger.info("Simulator started: %d tickers", len(tickers))

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    async def add_ticker(self, ticker: str) -> None:
        if self._sim:
            self._sim.add_ticker(ticker)
            price = self._sim.get_price(ticker)
            if price is not None:
                self._cache.update(ticker=ticker, price=price)

    async def remove_ticker(self, ticker: str) -> None:
        if self._sim:
            self._sim.remove_ticker(ticker)
        self._cache.remove(ticker)

    def get_tickers(self) -> list[str]:
        return self._sim.get_tickers() if self._sim else []

    async def _run_loop(self) -> None:
        while True:
            try:
                if self._sim:
                    prices = self._sim.step()
                    for ticker, price in prices.items():
                        self._cache.update(ticker=ticker, price=price)
            except Exception:
                logger.exception("Simulator step failed")
            await asyncio.sleep(self._interval)
```

### Behavioral guarantees

| Property | Guarantee |
|---|---|
| Prices never go negative | `exp()` is always positive |
| Per-tick moves are small | With `sigma=0.22` and `dt=8.5e-8`, average move is ~0.05 cents |
| Correlation matrix validity | All coefficients 0 < ρ < 1 with 1 on diagonal → positive definite |
| Dynamic ticker addition | `add_ticker()` safe during simulation; Cholesky rebuilt in < 1ms |
| Random events | ~0.1% per tick per ticker → ~1 event/50s across 10 tickers |

---

## 7. Massive API Client

**File: `backend/app/market/massive_client.py`**

The Massive (formerly Polygon.io) client polls the REST snapshot endpoint for
all watched tickers in a single API call, then writes to the PriceCache.

```python
import asyncio
import logging

from massive import RESTClient

from .cache import PriceCache
from .interface import MarketDataSource

logger = logging.getLogger(__name__)


class MassiveDataSource(MarketDataSource):
    """MarketDataSource backed by the Massive (Polygon.io) REST API.

    Uses GET /v2/snapshot/locale/us/markets/stocks/tickers for all tickers
    in one call. Poll interval must respect the rate limit:
      - Free tier: 5 req/min → poll every 15s (default)
      - Paid tiers: can poll every 2-5s
    """

    def __init__(
        self,
        api_key: str,
        price_cache: PriceCache,
        poll_interval: float = 15.0,
    ) -> None:
        self._api_key = api_key
        self._cache = price_cache
        self._interval = poll_interval
        self._tickers: list[str] = []
        self._task: asyncio.Task | None = None
        self._client: RESTClient | None = None

    async def start(self, tickers: list[str]) -> None:
        self._client = RESTClient(api_key=self._api_key)
        self._tickers = list(tickers)

        # Immediate first poll so the cache is populated before SSE clients connect
        await self._poll_once()

        self._task = asyncio.create_task(self._poll_loop(), name="massive-poller")
        logger.info(
            "Massive poller started: %d tickers, %.1fs interval",
            len(tickers),
            self._interval,
        )

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        self._client = None

    async def add_ticker(self, ticker: str) -> None:
        ticker = ticker.upper().strip()
        if ticker not in self._tickers:
            self._tickers.append(ticker)
            # Ticker will appear in the cache on the next poll cycle

    async def remove_ticker(self, ticker: str) -> None:
        ticker = ticker.upper().strip()
        self._tickers = [t for t in self._tickers if t != ticker]
        self._cache.remove(ticker)

    def get_tickers(self) -> list[str]:
        return list(self._tickers)

    async def _poll_loop(self) -> None:
        """Sleep then poll, forever. First poll already happened in start()."""
        while True:
            await asyncio.sleep(self._interval)
            await self._poll_once()

    async def _poll_once(self) -> None:
        """Fetch snapshots for all tickers and write to cache."""
        if not self._tickers or not self._client:
            return

        try:
            # The RESTClient is synchronous — run it in a thread pool to avoid
            # blocking the event loop during the HTTP request.
            snapshots = await asyncio.to_thread(self._fetch_snapshots)

            for snap in snapshots:
                try:
                    self._cache.update(
                        ticker=snap.ticker,
                        price=snap.last_trade.price,
                        # Massive timestamps are Unix milliseconds → convert to seconds
                        timestamp=snap.last_trade.timestamp / 1000.0,
                    )
                except (AttributeError, TypeError) as e:
                    logger.warning("Skipping bad snapshot for %s: %s",
                                   getattr(snap, "ticker", "???"), e)

        except Exception as e:
            # Log but don't re-raise — the loop retries on the next interval.
            # Common failures: 401 (bad key), 429 (rate limit), network error.
            logger.error("Massive poll failed: %s", e)

    def _fetch_snapshots(self) -> list:
        """Synchronous snapshot call. Runs in a thread via asyncio.to_thread."""
        return self._client.get_snapshot_all("stocks", tickers=self._tickers)
```

### Why `asyncio.to_thread`?

The `massive` (Polygon.io) `RESTClient` makes synchronous blocking HTTP calls.
If called directly on the event loop it would block all other coroutines
(including SSE streaming) for the duration of the HTTP round-trip. Running it
in a thread via `asyncio.to_thread` keeps the event loop free.

### Snapshot response field mapping

```
snap.ticker                 → ticker symbol (str)
snap.last_trade.price       → current price (float)
snap.last_trade.timestamp   → Unix milliseconds (int) → divide by 1000 for seconds
snap.prev_day.close         → previous day's close (available but not used by cache)
snap.today_change_percent   → day-change % (available but not used by cache)
```

---

## 8. Factory

**File: `backend/app/market/factory.py`**

```python
import logging
import os

from .cache import PriceCache
from .interface import MarketDataSource

logger = logging.getLogger(__name__)


def create_market_data_source(price_cache: PriceCache) -> MarketDataSource:
    """Select market data implementation from environment.

    MASSIVE_API_KEY set and non-empty → MassiveDataSource (real data)
    Otherwise                         → SimulatorDataSource (GBM)

    Returns an unstarted source. Caller must await source.start(tickers).
    """
    api_key = os.environ.get("MASSIVE_API_KEY", "").strip()

    if api_key:
        from .massive_client import MassiveDataSource
        logger.info("Market data source: Massive API")
        return MassiveDataSource(api_key=api_key, price_cache=price_cache)
    else:
        from .simulator import SimulatorDataSource
        logger.info("Market data source: GBM Simulator")
        return SimulatorDataSource(price_cache=price_cache)
```

The factory uses **lazy imports** (`from .massive_client import ...` inside the
branch) so `massive` is only imported when an API key is present. This prevents
an `ImportError` on startup if the package isn't installed for pure-simulator
deployments.

---

## 9. SSE Streaming Endpoint

**File: `backend/app/market/stream.py`**

### Wire contract (from PLAN.md §6)

- Each SSE message carries **exactly one ticker** — events are never batched
- `event:` field is always `price_update`
- `change_pct` is tick-to-tick (not daily)
- `timestamp` is ISO 8601 UTC
- Heartbeat comment (`:\n\n`) every 15 seconds
- `retry: 3000` on initial connection

### Correct SSE format

```
retry: 3000

event: price_update
data: {"type":"price_update","data":{"ticker":"AAPL","price":192.31,"previous_price":191.94,"change_pct":0.19,"timestamp":"2026-03-05T14:21:11.100Z"}}

event: price_update
data: {"type":"price_update","data":{"ticker":"GOOGL","price":175.82,"previous_price":175.70,"change_pct":0.07,"timestamp":"2026-03-05T14:21:11.100Z"}}

```

### Implementation

```python
import asyncio
import json
import logging
import time
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from .cache import PriceCache

logger = logging.getLogger(__name__)

HEARTBEAT_INTERVAL = 15.0   # seconds between keepalive comments
SSE_TICK_INTERVAL  = 0.5    # seconds between price emission cycles


def create_stream_router(price_cache: PriceCache) -> APIRouter:
    """Factory that returns a router with the price_cache injected via closure."""

    router = APIRouter(prefix="/api/stream", tags=["streaming"])

    @router.get("/prices")
    async def stream_prices(request: Request) -> StreamingResponse:
        """SSE endpoint: streams one event per ticker per 500ms tick."""
        return StreamingResponse(
            _generate_events(price_cache, request),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",   # Disable nginx buffering if proxied
            },
        )

    return router


async def _generate_events(
    price_cache: PriceCache,
    request: Request,
) -> AsyncGenerator[str, None]:
    """Async generator: one SSE event per ticker per 500ms.

    Heartbeat comment every 15s keeps the connection alive through proxies.
    Stops when the client disconnects.
    """
    # Reconnect hint: client retries after 3 seconds on disconnection
    yield "retry: 3000\n\n"

    last_heartbeat = time.monotonic()
    client_ip = request.client.host if request.client else "unknown"
    logger.info("SSE client connected: %s", client_ip)

    try:
        while True:
            if await request.is_disconnected():
                logger.info("SSE client disconnected: %s", client_ip)
                break

            # Heartbeat to keep connection alive through proxies / load balancers
            now = time.monotonic()
            if now - last_heartbeat >= HEARTBEAT_INTERVAL:
                yield ":\n\n"
                last_heartbeat = now

            prices = price_cache.get_all()
            for update in prices.values():
                payload = json.dumps(update.to_sse_dict())
                yield f"event: price_update\ndata: {payload}\n\n"

            await asyncio.sleep(SSE_TICK_INTERVAL)

    except asyncio.CancelledError:
        logger.info("SSE stream cancelled: %s", client_ip)
```

### Key design decisions

| Decision | Rationale |
|---|---|
| One event per ticker | Plan §6 contract: "events are never batched" |
| `retry: 3000` | Plan §6 contract; browser auto-reconnects after 3s |
| Heartbeat every 15s | Keeps TCP connection alive through proxies / ELBs |
| `X-Accel-Buffering: no` | Nginx (common in Docker) buffers SSE by default; this disables it |
| `is_disconnected()` | Clean shutdown when client navigates away |
| `create_stream_router` factory | Injects PriceCache via closure — no global state |

### What the client sees on connection

```
retry: 3000\n\n
event: price_update\ndata: {"type":"price_update","data":{"ticker":"AAPL",...}}\n\n
event: price_update\ndata: {"type":"price_update","data":{"ticker":"GOOGL",...}}\n\n
... (one event per watched ticker, every 500ms) ...
:\n\n  ← heartbeat comment after 15s of silence
```

---

## 10. FastAPI Lifecycle Integration

The market data subsystem is started/stopped via FastAPI's lifespan context
manager. This ensures resources are properly initialized before the first
request and cleaned up on shutdown.

```python
# backend/app/main.py

from contextlib import asynccontextmanager

from fastapi import FastAPI

from .database import get_watchlist_tickers, init_db
from .market import PriceCache, create_market_data_source, create_stream_router

# Global singletons — shared between startup and routes via app.state
price_cache = PriceCache()
market_source = create_market_data_source(price_cache)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan: start market data on startup, stop on shutdown."""
    # Initialize database (create tables + seed default data if needed)
    init_db()

    # Load initial watchlist tickers from the database
    initial_tickers = get_watchlist_tickers(user_id="default")

    # Start market data (simulator or Massive API)
    await market_source.start(initial_tickers)

    # Attach to app state so routes can access them
    app.state.price_cache = price_cache
    app.state.market_source = market_source

    yield  # App is running

    # Graceful shutdown
    await market_source.stop()


app = FastAPI(title="FinAlly", lifespan=lifespan)

# Mount the SSE router (uses closure over price_cache)
app.include_router(create_stream_router(price_cache))

# Mount other API routers
# app.include_router(portfolio_router)
# app.include_router(watchlist_router)
# app.include_router(chat_router)
```

### Accessing the market source from API routes

```python
# In a route handler (e.g., watchlist endpoint)
from fastapi import Request

@router.post("/api/watchlist")
async def add_ticker(body: WatchlistAddRequest, request: Request):
    market_source = request.app.state.market_source
    price_cache   = request.app.state.price_cache

    # 1. Persist to database
    insert_watchlist_ticker(user_id="default", ticker=body.ticker)

    # 2. Start generating prices immediately
    await market_source.add_ticker(body.ticker)

    return WatchlistMutationResponse(
        ticker=body.ticker,
        action="add",
        watchlist_size=get_watchlist_size(),
    )
```

### Accessing current price for trade execution

```python
@router.post("/api/portfolio/trade")
async def execute_trade(body: TradeRequest, request: Request):
    price_cache = request.app.state.price_cache

    # Get current price from cache
    update = price_cache.get(body.ticker)
    if update is None:
        raise HTTPException(400, "No price data available for ticker")

    current_price = update.price
    # ... validate and execute trade ...
```

---

## 11. Watchlist Coordination

When a user adds or removes a ticker from the watchlist, the market data
subsystem must be updated in lock-step with the database change. Coordination
is the responsibility of the watchlist API route handlers.

```
User adds ticker
      │
      ▼
POST /api/watchlist
      │
      ├─ 1. Validate ticker format
      ├─ 2. Check for duplicate (409 if already in watchlist)
      ├─ 3. INSERT into watchlist table
      ├─ 4. await market_source.add_ticker(ticker)   ← starts price generation
      └─ 5. Return WatchlistMutationResponse

User removes ticker
      │
      ▼
DELETE /api/watchlist/{ticker}
      │
      ├─ 1. Check ticker exists in watchlist (404 if not)
      ├─ 2. DELETE from watchlist table
      ├─ 3. await market_source.remove_ticker(ticker)  ← stops generation + clears cache
      └─ 4. Return WatchlistMutationResponse
```

**Critical ordering**: always update the database **before** calling
`add_ticker` / `remove_ticker`. If the DB write fails, we want to roll back
without having modified the in-memory state. If the in-memory update fails
(shouldn't happen under normal conditions), the DB is still consistent and the
discrepancy will self-heal on restart.

---

## 12. Testing Strategy

### Unit tests for GBMSimulator

```python
# backend/tests/test_simulator.py

import pytest
from app.market.simulator import GBMSimulator


def test_prices_always_positive():
    """GBM prices must never go negative (exp() guarantee)."""
    sim = GBMSimulator(["AAPL", "TSLA"])
    for _ in range(1000):
        prices = sim.step()
        for ticker, price in prices.items():
            assert price > 0, f"{ticker} price went non-positive: {price}"


def test_single_ticker_no_correlation():
    """Single-ticker simulator should run without a Cholesky matrix."""
    sim = GBMSimulator(["AAPL"])
    assert sim._cholesky is None
    prices = sim.step()
    assert "AAPL" in prices
    assert prices["AAPL"] > 0


def test_add_remove_ticker():
    """Dynamic add/remove should update the ticker set and Cholesky."""
    sim = GBMSimulator(["AAPL"])
    sim.add_ticker("GOOGL")
    assert "GOOGL" in sim.get_tickers()
    assert sim._cholesky is not None  # 2 tickers → Cholesky exists

    sim.remove_ticker("GOOGL")
    assert "GOOGL" not in sim.get_tickers()
    assert sim._cholesky is None  # Back to 1 ticker


def test_seed_prices_used():
    """Known tickers should start at their seed price."""
    from app.market.seed_prices import SEED_PRICES
    sim = GBMSimulator(["AAPL"])
    assert sim.get_price("AAPL") == SEED_PRICES["AAPL"]


def test_unknown_ticker_random_price():
    """Unknown tickers should start at a price between $50 and $300."""
    sim = GBMSimulator(["ZZZZ"])
    price = sim.get_price("ZZZZ")
    assert 50.0 <= price <= 300.0


def test_step_returns_all_tickers():
    """step() must return a price for every tracked ticker."""
    tickers = ["AAPL", "GOOGL", "MSFT"]
    sim = GBMSimulator(tickers)
    prices = sim.step()
    assert set(prices.keys()) == set(tickers)
```

### Unit tests for PriceCache

```python
# backend/tests/test_cache.py

import time
import pytest
from app.market.cache import PriceCache


def test_first_update_is_flat():
    """First update for a ticker: previous_price == price, direction == 'flat'."""
    cache = PriceCache()
    update = cache.update("AAPL", 190.0)
    assert update.price == 190.0
    assert update.previous_price == 190.0
    assert update.direction == "flat"
    assert update.change_percent == 0.0


def test_uptick_direction():
    cache = PriceCache()
    cache.update("AAPL", 190.0)
    update = cache.update("AAPL", 191.0)
    assert update.direction == "up"
    assert update.change_percent > 0


def test_downtick_direction():
    cache = PriceCache()
    cache.update("AAPL", 190.0)
    update = cache.update("AAPL", 189.0)
    assert update.direction == "down"
    assert update.change_percent < 0


def test_remove_clears_ticker():
    cache = PriceCache()
    cache.update("AAPL", 190.0)
    cache.remove("AAPL")
    assert cache.get("AAPL") is None
    assert "AAPL" not in cache


def test_version_increments():
    cache = PriceCache()
    v0 = cache.version
    cache.update("AAPL", 190.0)
    assert cache.version == v0 + 1
    cache.update("AAPL", 191.0)
    assert cache.version == v0 + 2


def test_get_all_returns_copy():
    """Mutations to the returned dict must not affect the cache."""
    cache = PriceCache()
    cache.update("AAPL", 190.0)
    snapshot = cache.get_all()
    snapshot["AAPL"] = None  # mutate the copy
    assert cache.get("AAPL") is not None  # cache unaffected
```

### Integration tests for the SSE endpoint

```python
# backend/tests/test_stream.py

import json
import asyncio
import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.market import PriceCache


@pytest.mark.asyncio
async def test_sse_emits_price_update_events():
    """SSE stream should emit one event per ticker."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        lines = []
        async with client.stream("GET", "/api/stream/prices") as response:
            assert response.status_code == 200
            assert "text/event-stream" in response.headers["content-type"]

            async for line in response.aiter_lines():
                lines.append(line)
                if len(lines) >= 10:  # Read a few events
                    break

    event_data_lines = [l for l in lines if l.startswith("data:")]
    assert len(event_data_lines) > 0

    # Parse first event
    payload = json.loads(event_data_lines[0][len("data:"):].strip())
    assert payload["type"] == "price_update"
    assert "ticker" in payload["data"]
    assert "price" in payload["data"]
    assert "change_pct" in payload["data"]
    assert "timestamp" in payload["data"]


@pytest.mark.asyncio
async def test_sse_sends_retry_directive():
    """SSE stream must open with retry: 3000."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        async with client.stream("GET", "/api/stream/prices") as response:
            first_line = await response.aiter_lines().__anext__()
            assert first_line == "retry: 3000"
```

### Async integration test for SimulatorDataSource lifecycle

```python
# backend/tests/test_simulator_lifecycle.py

import asyncio
import pytest
from app.market.cache import PriceCache
from app.market.simulator import SimulatorDataSource


@pytest.mark.asyncio
async def test_simulator_populates_cache_on_start():
    """After start(), cache should have a price for every initial ticker."""
    cache = PriceCache()
    source = SimulatorDataSource(price_cache=cache, update_interval=0.1)
    await source.start(["AAPL", "GOOGL"])

    assert cache.get("AAPL") is not None
    assert cache.get("GOOGL") is not None

    await source.stop()


@pytest.mark.asyncio
async def test_simulator_updates_cache_over_time():
    """Cache version should increase as the simulator runs."""
    cache = PriceCache()
    source = SimulatorDataSource(price_cache=cache, update_interval=0.05)
    await source.start(["AAPL"])

    v_before = cache.version
    await asyncio.sleep(0.2)  # Let 2-4 ticks run
    v_after = cache.version

    assert v_after > v_before
    await source.stop()


@pytest.mark.asyncio
async def test_add_remove_ticker_live():
    """add_ticker and remove_ticker should work while simulator is running."""
    cache = PriceCache()
    source = SimulatorDataSource(price_cache=cache, update_interval=0.05)
    await source.start(["AAPL"])

    await source.add_ticker("TSLA")
    await asyncio.sleep(0.1)
    assert cache.get("TSLA") is not None

    await source.remove_ticker("TSLA")
    assert cache.get("TSLA") is None

    await source.stop()
```

---

## 13. Error Handling & Edge Cases

### Empty watchlist on startup

If the watchlist table is empty (e.g., clean database), `start([])` is called
with an empty list. Both implementations handle this gracefully:

- `GBMSimulator(tickers=[])` initializes with no tickers; `step()` returns `{}`
- `MassiveDataSource._poll_once()` short-circuits on empty tickers list

### Massive API errors

Errors from the Massive poller are **logged but not raised**, so the SSE stream
remains alive and prices simply don't update until the next successful poll:

```python
except Exception as e:
    logger.error("Massive poll failed: %s", e)
    # Don't re-raise — loop retries at next interval
```

Common errors and expected behavior:

| Error | HTTP Code | Recovery |
|---|---|---|
| Invalid API key | 401 | Logged; no prices update |
| Rate limit exceeded | 429 | Logged; retries after interval |
| Ticker not found | Snap returned without ticker | Silently skipped |
| Network timeout | OSError | Logged; retries after interval |

### Simulator step exceptions

The simulator loop catches exceptions at the step level, logs them, and
continues. This prevents a one-off numpy error from killing the streaming loop:

```python
async def _run_loop(self) -> None:
    while True:
        try:
            prices = self._sim.step()
            for ticker, price in prices.items():
                self._cache.update(ticker=ticker, price=price)
        except Exception:
            logger.exception("Simulator step failed")  # Don't re-raise
        await asyncio.sleep(self._interval)
```

### SSE client disconnection

When the browser tab closes or navigates away, `request.is_disconnected()`
returns `True` on the next check and the generator exits cleanly. FastAPI
cancels the underlying task, triggering `asyncio.CancelledError` which is
caught and logged.

### Ticker not in cache at trade time

If a user tries to trade a ticker that has no price (e.g., just added, Massive
not polled yet), `price_cache.get(ticker)` returns `None`. The trade endpoint
must handle this:

```python
update = price_cache.get(body.ticker)
if update is None:
    raise HTTPException(
        status_code=400,
        detail={"code": "NO_PRICE_DATA", "message": "Price not yet available for ticker"}
    )
```

---

## 14. Configuration Summary

| Variable | Default | Effect |
|---|---|---|
| `MASSIVE_API_KEY` | (empty) | If set: uses Massive REST API; if empty: uses GBM simulator |
| `LLM_MOCK` | `false` | Not used by market data layer |

### Simulator tuning (code constants)

| Constant | Default | File | Effect |
|---|---|---|---|
| `update_interval` | `0.5` s | `simulator.py` | Tick frequency |
| `event_probability` | `0.001` | `simulator.py` | Random event probability per tick |
| `GBMSimulator.DEFAULT_DT` | `8.48e-8` | `simulator.py` | Time step (fraction of trading year) |
| `INTRA_TECH_CORR` | `0.6` | `seed_prices.py` | Correlation between tech stocks |
| `INTRA_FINANCE_CORR` | `0.5` | `seed_prices.py` | Correlation between finance stocks |
| `CROSS_GROUP_CORR` | `0.3` | `seed_prices.py` | Cross-sector correlation |

### Massive tuning (constructor parameters)

| Parameter | Default | Effect |
|---|---|---|
| `poll_interval` | `15.0` s | How often to poll (free tier: 15s; paid: 2-5s) |

---

## Appendix: Data Flow Diagram

```
┌─────────────────────────────────────────────────────────┐
│  MARKET DATA LAYER                                      │
│                                                         │
│  ┌─────────────────────────────────────────────────┐   │
│  │  MarketDataSource (ABC)                         │   │
│  │                                                 │   │
│  │  ┌───────────────────┐  ┌─────────────────────┐│   │
│  │  │  SimulatorData    │  │  MassiveDataSource  ││   │
│  │  │  Source           │  │                     ││   │
│  │  │  ┌─────────────┐  │  │  ┌───────────────┐  ││   │
│  │  │  │ GBMSimulator│  │  │  │  RESTClient   │  ││   │
│  │  │  │ .step()     │  │  │  │  .get_        │  ││   │
│  │  │  │   every 500ms  │  │  │  snapshot_all │  ││   │
│  │  │  └─────────────┘  │  │  └───────────────┘  ││   │
│  │  └────────┬──────────┘  └──────────┬──────────┘│   │
│  │           │                        │            │   │
│  │           └────────────┬───────────┘            │   │
│  │                        │ cache.update()          │   │
│  │                        ▼                        │   │
│  │              ┌──────────────────┐               │   │
│  │              │   PriceCache     │               │   │
│  │              │  {ticker: Price  │               │   │
│  │              │    Update}       │               │   │
│  │              └──────┬───────────┘               │   │
│  └─────────────────────┼───────────────────────────┘   │
│                        │                               │
│            ┌───────────┼────────────┐                  │
│            │           │            │                  │
│            ▼           ▼            ▼                  │
│       SSE Stream   Portfolio     Trade                  │
│      /api/stream   Valuation   Execution               │
│       /prices      /api/       /api/portfolio          │
│                    portfolio   /trade                  │
└─────────────────────────────────────────────────────────┘
```

One `PriceCache` instance. One `MarketDataSource` instance. All routes read
from the cache; none talk to the data source directly.
