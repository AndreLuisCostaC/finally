"""FinAlly — Market Data Demo

Displays a live terminal dashboard of the GBM price simulator.
Demonstrates: real-time price generation, correlated moves, sparklines,
notable event detection, and the full PriceCache → SSE data pipeline.

Run with:
    cd backend
    uv run market_data_demo.py

Controls: Ctrl+C to exit early. Runs for 60 seconds then shows summary.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque

from rich.columns import Columns
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from app.market.cache import PriceCache
from app.market.seed_prices import SEED_PRICES, TICKER_PARAMS
from app.market.simulator import SimulatorDataSource

# Sparkline block characters, low → high intensity
SPARK_CHARS = "▁▂▃▄▅▆▇█"

# The default watchlist — all 10 tickers exercising the Cholesky correlation
TICKERS = ["AAPL", "GOOGL", "MSFT", "AMZN", "TSLA", "NVDA", "META", "JPM", "V", "NFLX"]

DURATION = 60  # seconds to run before printing summary


def sparkline(values: list[float]) -> str:
    """Render a list of prices as a unicode sparkline."""
    if len(values) < 2:
        return ""
    lo, hi = min(values), max(values)
    spread = hi - lo
    if spread == 0:
        return SPARK_CHARS[3] * len(values)
    n = len(SPARK_CHARS) - 1
    return "".join(SPARK_CHARS[int((v - lo) / spread * n)] for v in values)


def fmt_price(price: float) -> str:
    return f"{price:,.2f}" if price >= 1_000 else f"{price:.2f}"


def build_price_table(cache: PriceCache, history: dict[str, deque]) -> Table:
    table = Table(
        expand=True,
        border_style="bright_black",
        header_style="bold #ecad0a",
        pad_edge=True,
        padding=(0, 1),
        show_header=True,
    )
    table.add_column("Ticker", style="bold bright_white", width=7)
    table.add_column("Price", justify="right", width=10)
    table.add_column("Change", justify="right", width=9)
    table.add_column("Chg %", justify="right", width=8)
    table.add_column("Dir", width=3, justify="center")
    table.add_column("Sparkline (last 40 ticks)", width=44, no_wrap=True)

    for ticker in TICKERS:
        update = cache.get(ticker)
        if update is None:
            table.add_row(ticker, "—", "—", "—", "—", "")
            continue

        if update.direction == "up":
            color, arrow = "green", "▲"
        elif update.direction == "down":
            color, arrow = "red", "▼"
        else:
            color, arrow = "bright_black", "─"

        vals = list(history.get(ticker, []))
        spark = f"[bright_cyan]{sparkline(vals)}[/]" if len(vals) > 1 else "[bright_black]building…[/]"

        table.add_row(
            ticker,
            f"[{color}]${fmt_price(update.price)}[/]",
            f"[{color}]{update.change:+.2f}[/]",
            f"[{color}]{update.change_percent:+.2f}%[/]",
            f"[bold {color}]{arrow}[/]",
            spark,
        )

    return table


def build_stats_panel(cache: PriceCache, tick_count: int, event_count: int) -> Panel:
    """Right-side stats: seed vs current prices, σ params."""
    table = Table(
        expand=True,
        border_style="bright_black",
        header_style="bold #209dd7",
        pad_edge=False,
        padding=(0, 1),
        show_header=True,
    )
    table.add_column("Ticker", style="bold bright_white", width=7)
    table.add_column("Seed $", justify="right", width=8)
    table.add_column("Now $", justify="right", width=8)
    table.add_column("Session", justify="right", width=8)
    table.add_column("σ", justify="right", width=5)

    for ticker in TICKERS:
        seed = SEED_PRICES.get(ticker, 0.0)
        update = cache.get(ticker)
        sigma = TICKER_PARAMS.get(ticker, {}).get("sigma", 0.25)
        if update is None:
            table.add_row(ticker, f"${fmt_price(seed)}", "—", "—", f"{sigma:.0%}")
            continue
        session_pct = (update.price - seed) / seed * 100 if seed else 0.0
        color = "green" if session_pct > 0 else "red" if session_pct < 0 else "bright_black"
        table.add_row(
            ticker,
            f"${fmt_price(seed)}",
            f"${fmt_price(update.price)}",
            f"[{color}]{session_pct:+.2f}%[/]",
            f"{sigma:.0%}",
        )

    footer = Text.assemble(
        ("  Ticks: ", "bright_black"), (f"{tick_count}", "bright_white"),
        ("  |  Events: ", "bright_black"), (f"{event_count}", "bright_yellow"),
        ("  |  σ = annualised vol", "bright_black"),
    )
    return Panel(
        table,
        title="[bold #209dd7]Seed vs Current  |  Volatility[/]",
        border_style="bright_black",
        subtitle=footer,
    )


def build_event_log(events: deque) -> Panel:
    text = Text()
    if not events:
        text.append("Watching for notable moves (>1% tick change)…", style="bright_black italic")
    else:
        for evt in events:
            text.append(evt)
            text.append("\n")
    return Panel(
        text,
        title="[bold #ecad0a]Notable Moves  (>1% tick change)[/]",
        border_style="bright_black",
        height=9,
    )


def build_dashboard(
    cache: PriceCache,
    history: dict[str, deque],
    events: deque,
    tick_count: int,
    event_count: int,
    start_time: float,
) -> Layout:
    elapsed = time.time() - start_time
    remaining = max(0, DURATION - elapsed)

    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="main"),
        Layout(name="footer", size=11),
    )
    layout["main"].split_row(
        Layout(name="prices", ratio=3),
        Layout(name="stats", ratio=2),
    )

    # ── Header ──────────────────────────────────────────────────────────────
    header = Text.assemble(
        ("  FinAlly ", "bold #ecad0a"),
        ("Market Data Demo", "bold bright_white"),
        ("  ·  ", "bright_black"),
        ("GBM Simulator", "bold #209dd7"),
        ("  ·  ", "bright_black"),
        (f"elapsed {elapsed:5.1f}s", "bright_cyan"),
        ("  ·  ", "bright_black"),
        (f"remaining {remaining:4.1f}s", "bright_cyan"),
        ("  ·  ", "bright_black"),
        ("10 tickers  ·  500ms ticks  ·  Cholesky correlated", "bright_black"),
        ("  ·  ", "bright_black"),
        ("Ctrl+C to exit", "bright_black italic"),
    )
    layout["header"].update(Panel(header, border_style="#ecad0a"))

    # ── Prices ──────────────────────────────────────────────────────────────
    layout["prices"].update(
        Panel(
            build_price_table(cache, history),
            title="[bold bright_white]Live Prices[/]",
            border_style="bright_black",
        )
    )

    # ── Stats ────────────────────────────────────────────────────────────────
    layout["stats"].update(build_stats_panel(cache, tick_count, event_count))

    # ── Event log ────────────────────────────────────────────────────────────
    layout["footer"].update(build_event_log(events))

    return layout


def print_summary(cache: PriceCache, tick_count: int, event_count: int, duration: float) -> None:
    console = Console()
    console.print()
    console.rule("[bold #ecad0a]  FinAlly  Market Data Demo — Session Summary[/]")
    console.print()

    table = Table(
        border_style="bright_black",
        header_style="bold bright_white",
        expand=False,
        show_footer=False,
    )
    table.add_column("Ticker", style="bold bright_white", width=8)
    table.add_column("Seed Price", justify="right", width=12)
    table.add_column("Final Price", justify="right", width=12)
    table.add_column("Session %", justify="right", width=12)
    table.add_column("σ (vol)", justify="right", width=9)

    for ticker in TICKERS:
        seed = SEED_PRICES.get(ticker, 0.0)
        update = cache.get(ticker)
        sigma = TICKER_PARAMS.get(ticker, {}).get("sigma", 0.25)
        if update is None:
            continue
        final = update.price
        pct = (final - seed) / seed * 100 if seed else 0.0
        color = "green" if pct > 0 else "red" if pct < 0 else "bright_black"
        table.add_row(
            ticker,
            f"${fmt_price(seed)}",
            f"[{color}]${fmt_price(final)}[/]",
            f"[{color}]{pct:+.2f}%[/]",
            f"{sigma:.0%}",
        )

    console.print(table)
    console.print()

    stats = Text.assemble(
        ("  Duration: ", "bright_black"), (f"{duration:.1f}s", "bright_white"),
        ("  ·  Ticks: ", "bright_black"), (f"{tick_count}", "bright_white"),
        ("  ·  Notable moves: ", "bright_black"), (f"{event_count}", "bright_yellow"),
        ("  ·  Model: GBM + Cholesky correlated normals", "bright_black"),
    )
    console.print(stats)
    console.print()


async def run() -> None:
    cache = PriceCache()
    source = SimulatorDataSource(price_cache=cache, update_interval=0.5)

    # Per-ticker price history (40 points = 20 seconds of sparkline)
    history: dict[str, deque] = {t: deque(maxlen=40) for t in TICKERS}
    events: deque = deque(maxlen=14)  # event log (newest first)
    tick_count = 0
    event_count = 0

    await source.start(TICKERS)
    start_time = time.time()

    # Prime history with seed values
    for ticker in TICKERS:
        u = cache.get(ticker)
        if u:
            history[ticker].append(u.price)

    try:
        with Live(
            build_dashboard(cache, history, events, tick_count, event_count, start_time),
            refresh_per_second=4,
            screen=True,
        ) as live:
            last_version = cache.version
            while time.time() - start_time < DURATION:
                await asyncio.sleep(0.25)

                if cache.version == last_version:
                    continue
                last_version = cache.version
                tick_count += 1

                for ticker in TICKERS:
                    u = cache.get(ticker)
                    if u is None:
                        continue
                    history[ticker].append(u.price)

                    if abs(u.change_percent) > 1.0:
                        event_count += 1
                        arrow = "▲" if u.direction == "up" else "▼"
                        color = "green" if u.direction == "up" else "red"
                        ts = time.strftime("%H:%M:%S")
                        events.appendleft(
                            f"[bright_black]{ts}[/]  "
                            f"[bold {color}]{arrow} {ticker}[/]  "
                            f"[{color}]{u.change_percent:+.2f}%[/]  "
                            f"[bright_white]${fmt_price(u.price)}[/]"
                        )

                live.update(
                    build_dashboard(cache, history, events, tick_count, event_count, start_time)
                )

    except KeyboardInterrupt:
        pass
    finally:
        await source.stop()

    print_summary(cache, tick_count, event_count, time.time() - start_time)


if __name__ == "__main__":
    asyncio.run(run())
