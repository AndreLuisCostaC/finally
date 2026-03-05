# Market Simulator Design

Approach and code structure for simulating realistic stock prices when no Massive API key is configured.

## Overview

The simulator uses **Geometric Brownian Motion (GBM)** to generate realistic stock price paths. GBM is the standard model underlying Black-Scholes option pricing: prices evolve continuously with random noise, can never go negative, and produce the lognormal distribution observed in real markets.

Updates run at 500ms intervals, producing a continuous stream of price changes that feel live.

## GBM Math

At each time step, a stock price evolves as:

```
S(t+dt) = S(t) * exp((mu - sigma^2/2) * dt + sigma * sqrt(dt) * Z)
```

Where:
- `S(t)` = current price
- `mu` = annualized drift (expected return), e.g. `0.05` (5%)
- `sigma` = annualized volatility, e.g. `0.20` (20%)
- `dt` = time step as fraction of a trading year
- `Z` = standard normal random variable drawn from N(0,1)

For 500ms updates with ~252 trading days and ~6.5 hours per day:
```
dt = 0.5 / (252 * 6.5 * 3600) ≈ 8.5e-8
```

This tiny `dt` produces small, realistic per-tick moves that accumulate naturally over time.

## Correlated Moves

Real stocks don't move independently — tech stocks tend to move together. We use **Cholesky decomposition** of a correlation matrix to generate correlated random draws.

Given a correlation matrix `C`, compute `L = cholesky(C)`. Then for independent standard normals `Z_independent`:
```
Z_correlated = L @ Z_independent
```

Default correlation groups:
- **Tech**: AAPL, GOOGL, MSFT, AMZN, META, NVDA, NFLX — intra-group ~0.6
- **Finance**: JPM, V — intra-group ~0.5
- **Cross-group**: ~0.3 baseline
- **TSLA**: lower correlation with everything (~0.3) — high volatility, does its own thing
- **Unknown tickers**: treated as cross-group (~0.3 correlation with everything)

## Random Events

Every step, each ticker has a small probability (~0.001) of a random "event" — a sudden 2–5% move in either direction. This adds drama and keeps the dashboard visually interesting without distorting the simulation.

At 500ms per step with 10 tickers, expect roughly one event somewhere in the watchlist every 50 seconds.

## Seed Prices

Realistic starting prices for the default watchlist:

```python
SEED_PRICES: dict[str, float] = {
    "AAPL": 190.0,
    "GOOGL": 175.0,
    "MSFT": 420.0,
    "AMZN": 185.0,
    "TSLA": 250.0,
    "NVDA": 800.0,
    "META": 500.0,
    "JPM": 195.0,
    "V": 280.0,
    "NFLX": 600.0,
}
```

Tickers not in the seed list start at a random price between $50–$300.

## Per-Ticker Parameters

Each ticker has its own volatility and drift:

```python
TICKER_PARAMS: dict[str, dict] = {
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

DEFAULT_PARAMS = {"sigma": 0.25, "mu": 0.05}  # For unknown tickers
```

## Implementation

```python
import math
import random
import numpy as np

class GBMSimulator:
    """Generates correlated GBM price paths for multiple tickers."""

    DT = 8.5e-8  # 500ms as fraction of a trading year
    EVENT_PROB = 0.001

    def __init__(self, tickers: list[str]):
        self._prices: dict[str, float] = {}
        self._params: dict[str, dict] = {}
        self._tickers: list[str] = []
        self._cholesky: np.ndarray | None = None

        for ticker in tickers:
            self.add_ticker(ticker)

    def add_ticker(self, ticker: str) -> None:
        if ticker in self._prices:
            return
        self._tickers.append(ticker)
        self._prices[ticker] = SEED_PRICES.get(ticker, random.uniform(50, 300))
        self._params[ticker] = TICKER_PARAMS.get(ticker, DEFAULT_PARAMS)
        self._rebuild_cholesky()

    def remove_ticker(self, ticker: str) -> None:
        if ticker not in self._prices:
            return
        self._tickers.remove(ticker)
        del self._prices[ticker]
        del self._params[ticker]
        self._rebuild_cholesky()

    def step(self) -> dict[str, float]:
        """Advance one time step. Returns {ticker: new_price}."""
        n = len(self._tickers)
        if n == 0:
            return {}

        # Generate correlated random normals
        z_independent = np.random.standard_normal(n)
        z = self._cholesky @ z_independent if self._cholesky is not None else z_independent

        result = {}
        for i, ticker in enumerate(self._tickers):
            mu = self._params[ticker]["mu"]
            sigma = self._params[ticker]["sigma"]

            # GBM step
            drift = (mu - 0.5 * sigma**2) * self.DT
            diffusion = sigma * math.sqrt(self.DT) * z[i]
            self._prices[ticker] *= math.exp(drift + diffusion)

            # Random event
            if random.random() < self.EVENT_PROB:
                shock = random.uniform(0.02, 0.05) * random.choice([-1, 1])
                self._prices[ticker] *= (1 + shock)

            result[ticker] = round(self._prices[ticker], 2)

        return result

    def get_price(self, ticker: str) -> float | None:
        return self._prices.get(ticker)

    def _rebuild_cholesky(self) -> None:
        """Rebuild the Cholesky decomposition of the correlation matrix."""
        n = len(self._tickers)
        if n <= 1:
            self._cholesky = None
            return

        corr = np.eye(n)
        for i in range(n):
            for j in range(i + 1, n):
                rho = self._get_correlation(self._tickers[i], self._tickers[j])
                corr[i, j] = rho
                corr[j, i] = rho

        self._cholesky = np.linalg.cholesky(corr)

    def _get_correlation(self, t1: str, t2: str) -> float:
        """Return pairwise correlation between two tickers."""
        tech = {"AAPL", "GOOGL", "MSFT", "AMZN", "META", "NVDA", "NFLX"}
        finance = {"JPM", "V"}

        if t1 in tech and t2 in tech:
            return 0.6
        if t1 in finance and t2 in finance:
            return 0.5
        return 0.3  # TSLA, cross-sector, and unknown tickers
```

## File Structure

```
backend/
  app/
    market/
      simulator.py     # GBMSimulator class + SimulatorDataSource wrapper
      seed_prices.py   # SEED_PRICES, TICKER_PARAMS, DEFAULT_PARAMS constants
```

`seed_prices.py` contains the constant dictionaries only. `simulator.py` contains `GBMSimulator` and the `SimulatorDataSource` class (the `MarketDataSource` implementation that wraps `GBMSimulator` in an async loop).

## Behavior Notes

- **Prices never go negative**: GBM uses `exp()`, which is always positive
- **Per-tick moves are tiny**: with `sigma=0.22` (AAPL) and `dt=8.5e-8`, a single step changes price by ~0.05 cents on average. Moves accumulate naturally over time
- **Realistic intraday range**: with `sigma=0.50` (TSLA), simulated daily trading produces approximately the right intraday price range
- **Correlation matrix validity**: Cholesky decomposition requires a positive semi-definite matrix. The hardcoded correlations (all between 0 and 1, with 1 on diagonal) guarantee this
- **Dynamic ticker addition**: when a new ticker is added mid-session, the Cholesky matrix is rebuilt. This is O(n²) but n is small (<50 tickers) so it's negligible
- **Random events**: ~0.1% probability per step per ticker. With 10 tickers at 500ms, expect roughly one event per 50 seconds across the watchlist — enough to keep the dashboard visually dynamic
