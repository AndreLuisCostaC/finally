# Massive API Reference (formerly Polygon.io)

Reference documentation for the Massive (formerly Polygon.io) REST API as used in FinAlly.

## Overview

- **Base URL**: `https://api.massive.com` (legacy `https://api.polygon.io` still supported)
- **Python package**: `massive` — install via `uv add massive`
- **Min Python version**: 3.9+
- **Auth**: API key via `MASSIVE_API_KEY` env var or passed to `RESTClient(api_key=...)`
- **Auth header**: `Authorization: Bearer <API_KEY>` (the client handles this automatically)

> **Rebrand note**: Polygon.io rebranded as Massive.com on October 30, 2025. Package name changed from `polygon-api-client` → `massive`. Import path and method signatures are identical.

## Rate Limits

| Tier | Limit |
|------|-------|
| Free | 5 requests/minute |
| Paid (all tiers) | Unlimited (recommended: stay under 100 req/s) |

For FinAlly: free tier → poll every 15s. Paid tiers → poll every 2–5s.

## Client Initialization

```python
from massive import RESTClient

# Reads MASSIVE_API_KEY from environment automatically
client = RESTClient()

# Or pass explicitly
client = RESTClient(api_key="your_key_here")
```

## Endpoints Used in FinAlly

### 1. Snapshot — All Tickers (Primary Endpoint)

Gets current prices for multiple tickers in a **single API call**. This is the main endpoint we use for polling.

**REST**: `GET /v2/snapshot/locale/us/markets/stocks/tickers?tickers=AAPL,GOOGL,MSFT`

**Python**:
```python
from massive import RESTClient

client = RESTClient()

# One call fetches all tickers
snapshots = client.get_snapshot_all(
    "stocks",
    tickers=["AAPL", "GOOGL", "MSFT", "AMZN", "TSLA"],
)

for snap in snapshots:
    print(f"{snap.ticker}: ${snap.last_trade.price}")
    print(f"  Day change: {snap.today_change_percent}%")
    print(f"  Day OHLC: O={snap.day.open} H={snap.day.high} L={snap.day.low} C={snap.day.close}")
```

**Response structure** (per ticker):
```json
{
  "ticker": "AAPL",
  "day": {
    "open": 129.61,
    "high": 130.15,
    "low": 125.07,
    "close": 125.07,
    "volume": 111237700,
    "vwap": 127.35
  },
  "last_trade": {
    "price": 125.07,
    "size": 100,
    "exchange": 4,
    "timestamp": 1675190399000
  },
  "last_quote": {
    "bid": 125.06,
    "ask": 125.08,
    "bid_size": 500,
    "ask_size": 1000,
    "timestamp": 1675190399500
  },
  "prev_day": {
    "close": 129.61,
    "open": 128.00,
    "high": 130.00,
    "low": 127.50,
    "volume": 98000000
  },
  "today_change": -4.54,
  "today_change_percent": -3.50
}
```

**Key fields we extract**:
- `last_trade.price` — current price for trading and display
- `prev_day.close` — previous day's close (for daily change reference)
- `today_change_percent` — day change percentage
- `last_trade.timestamp` — Unix milliseconds when the trade occurred

### 2. Single Ticker Snapshot

Detailed data on one ticker (e.g., when user clicks for detail view).

**REST**: `GET /v2/snapshot/locale/us/markets/stocks/tickers/{ticker}`

**Python**:
```python
snapshot = client.get_snapshot_ticker("stocks", "AAPL")

print(f"Price: ${snapshot.last_trade.price}")
print(f"Bid/Ask: ${snapshot.last_quote.bid} / ${snapshot.last_quote.ask}")
print(f"Day range: ${snapshot.day.low} - ${snapshot.day.high}")
```

### 3. Previous Close

Gets the previous day's OHLCV for a ticker. Useful for seeding prices on startup.

**REST**: `GET /v2/aggs/ticker/{ticker}/prev`

**Python**:
```python
results = client.get_previous_close_agg("AAPL")

for agg in results:
    print(f"Previous close: ${agg.close}")
    print(f"OHLC: O={agg.open} H={agg.high} L={agg.low} C={agg.close}")
    print(f"Volume: {agg.volume}")
```

**Response** (each bar):
```json
{
  "o": 150.0,
  "h": 155.0,
  "l": 149.0,
  "c": 154.5,
  "v": 1000000,
  "t": 1672531200000
}
```

### 4. Aggregates (Bars)

Historical OHLCV bars over a date range. Not needed for live polling but useful for historical charts.

**REST**: `GET /v2/aggs/ticker/{ticker}/range/{multiplier}/{timespan}/{from}/{to}`

**Python**:
```python
aggs = list(client.list_aggs(
    ticker="AAPL",
    multiplier=1,
    timespan="day",
    from_="2024-01-01",
    to="2024-01-31",
    limit=50000,
))

for a in aggs:
    print(f"O={a.open} H={a.high} L={a.low} C={a.close} V={a.volume} t={a.timestamp}")
```

Note: `list_aggs` auto-paginates — it returns all results, not just one page.

### 5. Last Trade / Last Quote

Individual endpoints for the most recent trade or NBBO quote. Prefer the snapshot endpoint (covers multiple tickers in one call) for polling; use these only when you need one ticker.

```python
# Last trade
trade = client.get_last_trade("AAPL")
print(f"Last trade: ${trade.price} x {trade.size}")

# Last NBBO quote
quote = client.get_last_quote("AAPL")
print(f"Bid: ${quote.bid} x {quote.bid_size}")
print(f"Ask: ${quote.ask} x {quote.ask_size}")
```

## How FinAlly Uses the API

The Massive poller runs as a background async task:

1. Reads all tickers from the watchlist
2. Calls `get_snapshot_all()` with those tickers (one API call for all)
3. Extracts `last_trade.price` and `last_trade.timestamp` from each snapshot
4. Writes to the shared in-memory `PriceCache`
5. Sleeps for the configured poll interval, then repeats

```python
import asyncio
from massive import RESTClient

async def poll_massive(api_key: str, get_tickers, price_cache, interval: float = 15.0):
    """Poll Massive API and update the price cache."""
    client = RESTClient(api_key=api_key)

    while True:
        tickers = get_tickers()
        if tickers:
            snapshots = await asyncio.to_thread(
                client.get_snapshot_all,
                "stocks",
                tickers=tickers,
            )
            for snap in snapshots:
                price_cache.update(
                    ticker=snap.ticker,
                    price=snap.last_trade.price,
                    timestamp=snap.last_trade.timestamp / 1000,  # ms -> seconds
                )

        await asyncio.sleep(interval)
```

## Error Handling

The client raises exceptions for HTTP errors:
- **401**: Invalid API key
- **403**: Insufficient plan permissions for the endpoint
- **429**: Rate limit exceeded (free tier: 5 req/min)
- **5xx**: Server errors (client has built-in retry)

## Notes

- The snapshot endpoint fetches **all requested tickers in one API call** — critical for staying within free-tier rate limits
- Timestamps from the API are Unix milliseconds; divide by 1000 for Unix seconds
- During market-closed hours, `last_trade.price` reflects the last traded price (may be after-hours)
- The `day` object resets at market open; during pre-market, values may reference the previous session
- `bid_size`/`ask_size` are reported in **shares** (not round lots) per SEC MDI rules effective November 2025
