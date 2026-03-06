"""SSE streaming endpoint for live price updates."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from datetime import UTC, datetime

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from .cache import PriceCache

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/stream", tags=["streaming"])

# Send a heartbeat comment every N seconds to keep proxies alive
HEARTBEAT_INTERVAL = 15.0
# How often to check for price updates and push to clients
TICK_INTERVAL = 0.5


def create_stream_router(price_cache: PriceCache) -> APIRouter:
    """Create the SSE streaming router with a reference to the price cache.

    This factory pattern lets us inject the PriceCache without globals.
    """

    @router.get("/prices")
    async def stream_prices(request: Request) -> StreamingResponse:
        """SSE endpoint for live price updates.

        Streams one event per ticker every ~500ms. Each event uses the
        normative SSE wire format defined in PLAN.md Section 6:

            event: price_update
            data: {"type":"price_update","data":{"ticker":"AAPL",...}}

        Includes:
          - retry: 3000 directive on initial connection
          - Heartbeat comment (: \\n\\n) every 15 seconds
        """
        return StreamingResponse(
            _generate_events(price_cache, request),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",  # Disable nginx buffering if proxied
            },
        )

    return router


def _format_price_event(ticker: str, price: float, previous_price: float, timestamp: float) -> str:
    """Format a single price update as an SSE event.

    Follows the normative wire contract from PLAN.md Section 6:
      - event: price_update
      - data: JSON with type wrapper and change_pct field
      - timestamp as ISO 8601 UTC string
    """
    change_pct = 0.0
    if previous_price != 0:
        change_pct = round((price - previous_price) / previous_price * 100, 4)

    iso_timestamp = datetime.fromtimestamp(timestamp, tz=UTC).isoformat().replace("+00:00", "Z")

    payload = json.dumps(
        {
            "type": "price_update",
            "data": {
                "ticker": ticker,
                "price": price,
                "previous_price": previous_price,
                "change_pct": change_pct,
                "timestamp": iso_timestamp,
            },
        }
    )
    return f"event: price_update\ndata: {payload}\n\n"


async def _generate_events(
    price_cache: PriceCache,
    request: Request,
    tick_interval: float = TICK_INTERVAL,
    heartbeat_interval: float = HEARTBEAT_INTERVAL,
) -> AsyncGenerator[str, None]:
    """Async generator that yields SSE-formatted price events.

    Sends one event per ticker every `tick_interval` seconds.
    Sends heartbeat comments every `heartbeat_interval` seconds.
    Stops when the client disconnects.
    """
    # Tell the client to reconnect after 3 seconds if the connection drops
    yield "retry: 3000\n\n"

    client_ip = request.client.host if request.client else "unknown"
    logger.info("SSE client connected: %s", client_ip)

    loop = asyncio.get_running_loop()
    last_heartbeat = loop.time()

    try:
        while True:
            # Check for client disconnect
            if await request.is_disconnected():
                logger.info("SSE client disconnected: %s", client_ip)
                break

            now = loop.time()

            # Send heartbeat if due
            if now - last_heartbeat >= heartbeat_interval:
                yield ":\n\n"
                last_heartbeat = now

            # Emit one event per ticker
            prices = price_cache.get_all()
            for ticker, update in prices.items():
                yield _format_price_event(
                    ticker=ticker,
                    price=update.price,
                    previous_price=update.previous_price,
                    timestamp=update.timestamp,
                )

            await asyncio.sleep(tick_interval)
    except asyncio.CancelledError:
        logger.info("SSE stream cancelled for: %s", client_ip)
