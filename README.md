# FinAlly — AI Trading Workstation

An AI-powered trading workstation with live market data, simulated portfolio trading, and an LLM chat assistant that can analyze positions and execute trades.

## Quick Start

```bash
cp .env.example .env
# Add your OPENROUTER_API_KEY to .env

docker build -t finally .
docker run -v finally-data:/app/db -p 8000:8000 --env-file .env finally
```

Open [http://localhost:8000](http://localhost:8000).

## Features

- Live-streaming prices for 10 default tickers (SSE)
- $10,000 virtual cash — buy/sell with market orders
- Portfolio heatmap, P&L chart, and positions table
- AI chat assistant (FinAlly) that can analyze your portfolio and execute trades

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `OPENROUTER_API_KEY` | Yes | OpenRouter key for LLM chat |
| `MASSIVE_API_KEY` | No | Real market data (simulator used if absent) |
| `LLM_MOCK` | No | Set `true` for deterministic mock responses (testing) |

## Architecture

- **Frontend**: Next.js static export, served by FastAPI
- **Backend**: FastAPI (Python/uv), SQLite database
- **Real-time**: SSE streaming at 500ms intervals
- **AI**: LiteLLM → OpenRouter (Cerebras inference), structured outputs

## Development

```bash
# Backend
cd backend && uv sync && uv run uvicorn app.main:app --reload

# Frontend
cd frontend && npm install && npm run dev
```

Run tests:

```bash
cd backend && uv run pytest
cd frontend && npm test
```
