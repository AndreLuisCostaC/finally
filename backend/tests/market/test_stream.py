"""Tests for SSE stream formatting and event generation."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.market.cache import PriceCache
from app.market.stream import _format_price_event, _generate_events


class TestFormatPriceEvent:
    """Unit tests for _format_price_event helper."""

    def test_event_type_field(self):
        """SSE event must have 'event: price_update' field."""
        event = _format_price_event("AAPL", 192.31, 191.94, 1741185671.1)
        assert event.startswith("event: price_update\n")

    def test_data_field_present(self):
        """SSE event must have a 'data:' field."""
        event = _format_price_event("AAPL", 192.31, 191.94, 1741185671.1)
        assert "\ndata: " in event

    def test_double_newline_terminator(self):
        """SSE event must end with double newline."""
        event = _format_price_event("AAPL", 192.31, 191.94, 1741185671.1)
        assert event.endswith("\n\n")

    def test_data_is_valid_json(self):
        """SSE data field must be valid JSON."""
        event = _format_price_event("AAPL", 192.31, 191.94, 1741185671.1)
        data_line = [line for line in event.split("\n") if line.startswith("data: ")][0]
        payload = json.loads(data_line[len("data: "):])
        assert isinstance(payload, dict)

    def test_type_field_in_payload(self):
        """Payload must contain 'type': 'price_update'."""
        event = _format_price_event("AAPL", 192.31, 191.94, 1741185671.1)
        data_line = [line for line in event.split("\n") if line.startswith("data: ")][0]
        payload = json.loads(data_line[len("data: "):])
        assert payload["type"] == "price_update"

    def test_data_wrapper_in_payload(self):
        """Payload must contain nested 'data' object."""
        event = _format_price_event("AAPL", 192.31, 191.94, 1741185671.1)
        data_line = [line for line in event.split("\n") if line.startswith("data: ")][0]
        payload = json.loads(data_line[len("data: "):])
        assert "data" in payload
        assert isinstance(payload["data"], dict)

    def test_ticker_in_data(self):
        """Data object must contain the ticker."""
        event = _format_price_event("AAPL", 192.31, 191.94, 1741185671.1)
        data_line = [line for line in event.split("\n") if line.startswith("data: ")][0]
        payload = json.loads(data_line[len("data: "):])
        assert payload["data"]["ticker"] == "AAPL"

    def test_price_in_data(self):
        """Data object must contain the price."""
        event = _format_price_event("AAPL", 192.31, 191.94, 1741185671.1)
        data_line = [line for line in event.split("\n") if line.startswith("data: ")][0]
        payload = json.loads(data_line[len("data: "):])
        assert payload["data"]["price"] == 192.31

    def test_previous_price_in_data(self):
        """Data object must contain previous_price."""
        event = _format_price_event("AAPL", 192.31, 191.94, 1741185671.1)
        data_line = [line for line in event.split("\n") if line.startswith("data: ")][0]
        payload = json.loads(data_line[len("data: "):])
        assert payload["data"]["previous_price"] == 191.94

    def test_change_pct_field_name(self):
        """Data must use 'change_pct' (not 'change_percent')."""
        event = _format_price_event("AAPL", 192.31, 191.94, 1741185671.1)
        data_line = [line for line in event.split("\n") if line.startswith("data: ")][0]
        payload = json.loads(data_line[len("data: "):])
        assert "change_pct" in payload["data"]
        assert "change_percent" not in payload["data"]

    def test_change_pct_calculation(self):
        """change_pct must be (price - prev) / prev * 100."""
        # 192.31 - 191.94 = 0.37; 0.37/191.94 * 100 ≈ 0.1928
        event = _format_price_event("AAPL", 192.31, 191.94, 1741185671.1)
        data_line = [line for line in event.split("\n") if line.startswith("data: ")][0]
        payload = json.loads(data_line[len("data: "):])
        expected = round((192.31 - 191.94) / 191.94 * 100, 4)
        assert abs(payload["data"]["change_pct"] - expected) < 0.0001

    def test_timestamp_is_iso8601_utc(self):
        """Timestamp must be ISO 8601 UTC string ending in 'Z'."""
        event = _format_price_event("AAPL", 192.31, 191.94, 1741185671.1)
        data_line = [line for line in event.split("\n") if line.startswith("data: ")][0]
        payload = json.loads(data_line[len("data: "):])
        ts = payload["data"]["timestamp"]
        assert isinstance(ts, str)
        assert ts.endswith("Z"), f"Expected ISO 8601 UTC timestamp ending in Z, got: {ts}"

    def test_zero_previous_price_change_pct(self):
        """change_pct should be 0 when previous_price is 0."""
        event = _format_price_event("AAPL", 100.0, 0.0, 1741185671.1)
        data_line = [line for line in event.split("\n") if line.startswith("data: ")][0]
        payload = json.loads(data_line[len("data: "):])
        assert payload["data"]["change_pct"] == 0.0


def _parse_events(raw: str) -> list[str]:
    """Parse raw SSE output into a list of event blocks."""
    events = []
    current = []
    for line in raw.split("\n"):
        if line == "":
            if current:
                events.append("\n".join(current))
                current = []
        else:
            current.append(line)
    if current:
        events.append("\n".join(current))
    return events


@pytest.mark.asyncio
class TestGenerateEvents:
    """Tests for the _generate_events async generator."""

    async def test_retry_directive_first(self):
        """First event must be 'retry: 3000'."""
        cache = PriceCache()
        cache.update("AAPL", 190.0)

        request = MagicMock()
        request.client = MagicMock()
        request.client.host = "127.0.0.1"
        # Disconnect after first chunk
        call_count = 0

        async def is_disconnected():
            nonlocal call_count
            call_count += 1
            return call_count > 1

        request.is_disconnected = is_disconnected

        events = []
        async for chunk in _generate_events(cache, request, tick_interval=0.0):
            events.append(chunk)

        assert events[0] == "retry: 3000\n\n"

    async def test_one_event_per_ticker(self):
        """Each ticker gets its own event, not batched."""
        cache = PriceCache()
        cache.update("AAPL", 190.0)
        cache.update("GOOGL", 175.0)

        request = MagicMock()
        request.client = MagicMock()
        request.client.host = "127.0.0.1"
        call_count = 0

        async def is_disconnected():
            nonlocal call_count
            call_count += 1
            return call_count > 1

        request.is_disconnected = is_disconnected

        events = []
        async for chunk in _generate_events(cache, request, tick_interval=0.0):
            events.append(chunk)

        # events[0] is retry directive
        # remaining events should each contain exactly one ticker
        price_events = [e for e in events if e.startswith("event: price_update")]
        assert len(price_events) == 2

        tickers_seen = set()
        for event in price_events:
            data_line = [line for line in event.split("\n") if line.startswith("data: ")][0]
            payload = json.loads(data_line[len("data: "):])
            tickers_seen.add(payload["data"]["ticker"])

        assert tickers_seen == {"AAPL", "GOOGL"}

    async def test_price_event_format(self):
        """Price events follow the normative wire format."""
        cache = PriceCache()
        cache.update("AAPL", 190.0)

        request = MagicMock()
        request.client = MagicMock()
        request.client.host = "127.0.0.1"
        call_count = 0

        async def is_disconnected():
            nonlocal call_count
            call_count += 1
            return call_count > 1

        request.is_disconnected = is_disconnected

        events = []
        async for chunk in _generate_events(cache, request, tick_interval=0.0):
            events.append(chunk)

        price_events = [e for e in events if e.startswith("event: price_update")]
        assert len(price_events) == 1

        event = price_events[0]
        assert "event: price_update" in event
        data_line = [line for line in event.split("\n") if line.startswith("data: ")][0]
        payload = json.loads(data_line[len("data: "):])

        assert payload["type"] == "price_update"
        assert payload["data"]["ticker"] == "AAPL"
        assert "change_pct" in payload["data"]
        assert payload["data"]["timestamp"].endswith("Z")

    async def test_stops_on_disconnect(self):
        """Generator stops when client disconnects."""
        cache = PriceCache()
        cache.update("AAPL", 190.0)

        request = MagicMock()
        request.client = MagicMock()
        request.client.host = "127.0.0.1"

        # Disconnect immediately after first check
        request.is_disconnected = AsyncMock(return_value=True)

        events = []
        async for chunk in _generate_events(cache, request, tick_interval=0.0):
            events.append(chunk)

        # Should only have the retry directive before disconnecting
        assert events == ["retry: 3000\n\n"]
