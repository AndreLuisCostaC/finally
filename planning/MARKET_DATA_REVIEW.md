# Market Data Backend — Code Review

**Date:** 2026-03-06
**Reviewer:** Claude Code
**Scope:** `backend/app/market/` (8 source files, 359 statements) and `backend/tests/market/` (7 test files, 90 tests)
**Prior review:** `planning/archive/MARKET_DATA_REVIEW.md` (2026-02-10, 73 tests, 5 failures)

---

## 1. Test Results

**90 tests collected, 90 passed, 0 failed.**

```
platform linux -- Python 3.13.7, pytest-9.0.2
asyncio: mode=Auto

tests/market/test_cache.py           13 passed
tests/market/test_factory.py          7 passed
tests/market/test_massive.py         13 passed
tests/market/test_models.py          11 passed
tests/market/test_simulator.py       19 passed
tests/market/test_simulator_source.py 10 passed
tests/market/test_stream.py          17 passed
```

**Lint (ruff):** Passes clean. Zero warnings.

### Coverage

```
Name                           Stmts   Miss  Cover   Missing
------------------------------------------------------------
app/market/__init__.py             6      0   100%
app/market/cache.py               39      0   100%
app/market/factory.py             15      0   100%
app/market/interface.py           13      0   100%
app/market/massive_client.py      67      4    94%   85-87, 125
app/market/models.py              26      0   100%
app/market/seed_prices.py          8      0   100%
app/market/simulator.py          139      3    98%   149, 268-269
app/market/stream.py              46      8    83%   32-56, 120-121, 134-135
------------------------------------------------------------
TOTAL                            359     15    96%
```

Coverage gaps are all justifiable:
- `stream.py` lines 32-56: The `stream_prices` FastAPI route handler inside `create_stream_router`. This path creates a `StreamingResponse` and requires a running ASGI server to exercise; the generator it calls (`_generate_events`) is directly tested with 17 tests.
- `stream.py` lines 120-121: Heartbeat branch. Only fires after 15 seconds have elapsed; not worth the test overhead.
- `stream.py` lines 134-135: `CancelledError` handler. Requires a task cancellation scenario in the async generator test harness.
- `simulator.py` line 149: Duplicate-guard in `_add_ticker_internal` (defensive check already covered by `add_ticker`).
- `simulator.py` lines 268-269: Exception log branch in `_run_loop`. Would require injecting a fault mid-simulation.
- `massive_client.py` lines 85-87: The `_poll_loop` body (sleep + poll). Not triggered because tests call `_poll_once` directly.
- `massive_client.py` line 125: The `_fetch_snapshots` synchronous call. Never reached in tests because `_fetch_snapshots` is patched before `_poll_once` invokes it.

96% overall coverage for a backend subsystem with external dependencies is solid.

---

## 2. Progress Since Prior Review

The archive review identified 7 issues. All required and recommended fixes have been applied:

| Issue | Prior Status | Current Status |
|---|---|---|
| Build config bug (hatchling wheel packages) | High — blocked builds | **Fixed** |
| Massive test fragility (5 failing tests) | Medium — 5 failures | **Fixed** — all 90 pass |
| `_generate_events` missing return type | Low | **Fixed** — `-> AsyncGenerator[str, None]` |
| `SimulatorDataSource.get_tickers` accessed private `_sim._tickers` | Low | **Fixed** — now calls `_sim.get_tickers()` |
| Unused imports in test files | Trivial | **Fixed** — lint clean |
| Add `GBMSimulator.get_tickers()` public method | Nice-to-have | **Fixed** |
| Add SSE integration tests | Nice-to-have | **Fixed** — 17 stream tests added |

Two low-severity items from the prior review were noted as acceptable and remain:
- `version` property reads without lock (CPython GIL makes single-int reads atomic)
- Module-level `router` singleton in `stream.py` (only called once in practice)

---

## 3. Architecture Assessment

The market data subsystem correctly implements the strategy pattern described in the design documents:

```
MarketDataSource (ABC)
├── SimulatorDataSource  — GBM + correlated moves via Cholesky decomposition
└── MassiveDataSource    — Polygon.io REST poller via asyncio.to_thread
        │
        ▼
   PriceCache (thread-safe, single source of truth)
        │
        ▼
   SSE stream → Frontend
```

**Strengths:**

- **Clean separation of concerns.** 8 focused modules, each with a single responsibility. The `__init__.py` re-exports form a minimal, stable public API.
- **Correct GBM math.** The log-normal price model `S(t+dt) = S(t) * exp((mu - 0.5*sigma²)*dt + sigma*sqrt(dt)*Z)` ensures prices never go negative. `dt = 0.5 / (252 * 6.5 * 3600) ≈ 8.48e-8` is correctly calibrated for 500ms ticks.
- **Correlated moves via Cholesky decomposition.** The sector-based correlation structure (tech 0.6, finance 0.5, cross 0.3) is mathematically correct and produces realistic co-movement. The matrix is rebuilt in O(n²) on ticker add/remove — negligible for n < 50.
- **Immutable `PriceUpdate` dataclass.** `frozen=True, slots=True` is the right choice for a high-frequency value object shared across async tasks.
- **Thread-safe `PriceCache`.** The `threading.Lock` correctly covers all mutating operations. The cache is the single shared state between the async event loop (SSE reads) and the thread pool (Massive API calls via `asyncio.to_thread`).
- **Resilient background tasks.** Both `_run_loop` (simulator) and `_poll_once` (Massive) catch exceptions and continue. This is essential for production stability — a transient numpy error or network blip should not kill the streaming loop.
- **Graceful shutdown.** Both sources cancel the background task and await its completion in `stop()`, preventing orphaned tasks. `stop()` is idempotent.
- **Immediate cache seeding.** `SimulatorDataSource.start()` seeds the cache before the loop starts; `MassiveDataSource.start()` does an immediate first poll. SSE clients connecting at startup get data right away.
- **SSE wire contract compliance.** `_format_price_event` produces exactly the format specified in PLAN.md §6: `event: price_update`, `data:` JSON with `type` wrapper, `change_pct` (not `change_percent`), ISO 8601 UTC timestamp. The `retry: 3000` directive and 15-second heartbeat are correctly implemented.
- **Factory with lazy imports.** `factory.py` currently uses module-level imports (acceptable since `massive` is a declared required dependency). The clean conditional logic selects the right source based on `MASSIVE_API_KEY`.

---

## 4. Issues Found

### 4.1 `MassiveDataSource.start()` Does Not Normalize Tickers (Severity: Low)

`add_ticker()` normalizes tickers with `.upper().strip()`, but `start()` stores the incoming list directly:

```python
async def start(self, tickers: list[str]) -> None:
    self._client = RESTClient(api_key=self._api_key)
    self._tickers = list(tickers)  # No normalization
```

In practice tickers from the database should already be uppercase (the watchlist route normalizes at insertion), but this is a silent inconsistency. A ticker stored in the DB as lowercase (e.g., due to direct SQL insertion) would be held in `_tickers` as-is, while `add_ticker("aapl")` would normalize to `"AAPL"`, leading to duplicates and two cache entries.

### 4.2 Module-Level `router` Singleton in `stream.py` (Severity: Low)

Line 18 creates a module-level `APIRouter` instance that `create_stream_router()` registers a route on:

```python
router = APIRouter(prefix="/api/stream", tags=["streaming"])

def create_stream_router(price_cache: PriceCache) -> APIRouter:
    @router.get("/prices")
    async def stream_prices(request: Request) -> StreamingResponse:
        ...
    return router
```

If `create_stream_router` is called more than once (e.g., in tests that construct the app multiple times), the route is registered again on the same router, creating duplicate route entries. This is harmless in the current single-app-instance architecture, but is a latent problem for test isolation.

### 4.3 `PriceCache.version` Property Not Under Lock (Severity: Low)

```python
@property
def version(self) -> int:
    return self._version  # No lock
```

All other `PriceCache` methods acquire `self._lock`. On CPython with the GIL, reading a single `int` attribute is atomic, so this causes no observable issue. Python 3.13 introduced a "free-threaded" (no-GIL) experimental build mode; if the project runs on that, this becomes a real race. A minor concern for future-proofing.

### 4.4 `to_dict()` on `PriceUpdate` Is Not the SSE Wire Format (Severity: Low)

`PriceUpdate.to_dict()` returns an internal format:

```python
{
    "ticker": ...,
    "price": ...,
    "previous_price": ...,
    "timestamp": 1741185671.1,       # Unix float, not ISO 8601
    "change": ...,                   # not in wire contract
    "change_percent": ...,           # field name differs from wire contract
    "direction": ...,                # not in wire contract
}
```

The SSE wire format is produced by `stream._format_price_event()`, not by `to_dict()`. The existence of `to_dict()` on the model may mislead future developers into using it for SSE serialization. The docstring says "Serialize for JSON / SSE transmission" which is actively misleading — it is not the SSE transmission format. The method is uncalled anywhere in the current codebase.

Either rename it to `to_internal_dict()` and clarify the docstring, or remove it if it has no planned use.

### 4.5 Missing Test: Full 10-Ticker Default Set (Severity: Low)

All simulator tests use 1–3 tickers. No test verifies the Cholesky decomposition succeeds for the full default set of 10 tickers (AAPL, GOOGL, MSFT, AMZN, TSLA, NVDA, META, JPM, V, NFLX). While the correlation parameters are well-chosen and the matrix is positive-definite by construction, a regression test against the full default set would catch any future changes to `CORRELATION_GROUPS` or correlation coefficients that break the decomposition.

### 4.6 Missing Test: Thread-Safety Under Concurrent Writes (Severity: Low)

`PriceCache` uses a `threading.Lock` to be thread-safe, but no test verifies this empirically. A test spawning multiple threads calling `update()` simultaneously and checking no data is corrupted (no `None` values, version monotonicity) would verify the lock is working as intended.

---

## 5. Contract Compliance

The implementation correctly follows the normative SSE wire contract in PLAN.md §6:

| Requirement | Status |
|---|---|
| One event per ticker, never batched | ✅ `_generate_events` iterates `prices.values()` yielding one event per iteration |
| `event: price_update` field | ✅ Hardcoded in `_format_price_event` |
| `type: "price_update"` in JSON | ✅ Present |
| `change_pct` (not `change_percent`) | ✅ Correct field name, tested in `test_change_pct_field_name` |
| `timestamp` ISO 8601 UTC ending in `Z` | ✅ `datetime.fromtimestamp(..., tz=UTC).isoformat().replace("+00:00", "Z")` |
| `retry: 3000` on initial connection | ✅ First yield in `_generate_events` |
| Heartbeat comment `:\n\n` every 15s | ✅ Implemented; not fully tested (acceptable) |
| `Cache-Control: no-cache` header | ✅ |
| `X-Accel-Buffering: no` header | ✅ Prevents nginx buffering |

---

## 6. Verdict

The market data backend is in excellent shape. All prior blocking and recommended issues have been resolved. The code is clean, well-tested, and correctly implements the design specification.

**No blockers for proceeding to the next development phase.**

**Should fix before the project is complete:**

1. Fix `to_dict()` docstring — remove "SSE transmission" from the description, or rename the method to avoid confusion with the actual SSE wire format in `stream.py`.
2. Fix `MassiveDataSource.start()` to normalize tickers with `.upper().strip()` for consistency with `add_ticker()`.

**Nice to have:**

3. Add a test covering `GBMSimulator` with all 10 default tickers to guard the correlation matrix.
4. Add a concurrent thread-safety test for `PriceCache`.
5. Move the `router` instance inside `create_stream_router()` (return a fresh `APIRouter` each call) to make the factory truly idempotent for test isolation.
