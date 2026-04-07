# Trading Bot — OpenCode Session Guide

## Entry Points

- **`professor.py`** — Main unified system: live scanner, Discord board updater, SL/TP watchdog, and autopilot executor. Run with `python3 professor.py`.
- **`trading.py`** — Order execution module: place/market/close orders, leverage, position sizing, account queries. Imported by `professor.py`.
- **`risk.py`** — Risk management: drawdown breaker, dynamic SL (ATR-based), Kelly criterion sizing, entry quality scoring, trade journal.
- **`proxies.py`** — Proxy rotation from `proxy.txt` (sticky per session). `reset_proxy()` re-randomizes.
- **`config.py`** — All settings: API keys, SL/TP %, leverage, coin whitelist, strategy parameters, Discord tokens.

## Running

```bash
python3 professor.py
```

## Runtime Requirements

- **`proxy.txt`** — proxy credentials (format: `http://user:pass@host:port`, one per line). Without it the bot raises `RuntimeError`.
- **`config.py`** — must have valid Binance Futures Testnet API keys (`FUTURES_URL = "https://testnet.binancefuture.com"`). Testnet keys required — demo keys don't fill orders.
- Signing style: **params NOT sorted** (natural order), `timestamp` + `recvWindow` + `signature` appended to query string.
- All HTTP requests route through sticky proxy (`get_proxy()`).

## Strategy (v2 — Improved)

**Coin Universe:** Strict whitelist of 15 quality coins. No garbage pumps/dumps.

**Signal Types:**
- 📈BOUNCE — RSI < 30 + momentum > 0.15% + price above EMA(20)
- 📉FADE — RSI > 70 + momentum < -0.15% + price below EMA(20)
- ⚡SPIKE — Volume spike > 1.5x + momentum alignment with EMA trend

**Entry Filters (all must pass):**
- Confidence >= 7/9
- EMA trend confirmation (price > EMA for LONG, price < EMA for SHORT)
- Volume ratio >= 1.5x

**Exit Rules:**
- Dynamic SL via ATR (2x ATR, min 2.5%)
- Partial TP: close 50% at 2:1 R:R, remaining at 3:1 R:R

**Autopilot:** Runs every 10 min. Takes positions if `open_count < MAX_POSITIONS` and confidence >= 7/9.

## State Files

| File | Purpose |
|---|---|
| `board_msg_id.txt` | Discord embed message ID (persists across restarts) |
| `live_board_data.json` | Latest scanner data (updated every scan cycle) |

## Discord Integration

- Bot token and webhook URL in `config.py`.
- `CHANNEL_ID = "1412841283315302543"` hardcoded in `professor.py`.
- Board message created once, updated in-place via PATCH each cycle.

## What NOT to Change

- Do not commit `proxy.txt` or any file containing credentials.
- Do not change `FUTURES_URL` to production — this bot is testnet only.
- Algo orders (STOP_MARKET, TAKE_PROFIT_MARKET) return HTTP 4120 on testnet — SL/TP uses manual price-check watchdog instead.
