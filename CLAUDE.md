# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Professor Psyduck** — Binance Futures trading bot with live signal scanning, Discord board, and autopilot execution.

## Running

```bash
python3 professor.py
```

**Important:** `config.py` has `FUTURES_URL = "https://fapi.binance.com"` (production). Change to testnet (`https://testnet.binancefuture.com`) and use testnet API keys. The current `config.py` contains real production keys — do not commit changes to it.

## Requirements

- Python 3.8+, `requests`, `pytz`
- `proxy.txt` — HTTP proxy credentials (format: `http://user:pass@host:port`, one per line). Required at runtime.
- `config.py` — valid Binance Futures API keys
- `proxies.py` — stub module (currently returns `{}`/no proxy); real proxy rotation via `proxy.txt` not yet implemented

## Architecture

```
professor.py   — Main entry point. Single-threaded loop running:
                 1. Scanner cycle: fetches 24h tickers + klines (parallel via ThreadPoolExecutor),
                    evaluates signals, updates Discord board, fires entries
                 2. SL/TP watchdog: price-check loop against dynamic SL and partial TP levels
                 3. Autopilot: runs every AUTOPILOT_INTERVAL seconds, opens positions when confidence >= 7/9

trading.py     — Order execution. All requests signed with HMAC-SHA256.
                 Key: place_order, market_close, set_leverage, get_account, get_positions,
                 calc_quantity_from_risk, get_symbol_precision, close_all_positions (CLI: --close-all)
                 Signing: params NOT sorted, `timestamp` + `recvWindow` + `signature` appended in that order

risk.py        — Risk management. DrawdownBreaker (10% daily loss circuit breaker), dynamic SL via ATR,
                 signal_quality_check, entry_score, trade journal. Not actively used by professor.py
                 (professor.py implements its own simplified SL/TP logic inline).

proxies.py     — Stub proxy module. Returns None/empty dict. `proxy.txt` exists but is not consumed.

utils/
  indicators.py — calc_rsi, calc_mom, calc_vol_ratio, calc_ema, calc_atr
  discord.py    — discord_req, discord_notify, build_board_embed, get_board_msg_id, save_board_msg_id
```

## Main Loop Behavior

The main loop in `professor.py:main()` runs sequentially (not truly concurrent):
1. `scan_cycle()` — fetches all tickers, parallel klines for top coins, evaluates signals, updates Discord board, executes immediate entries
2. `check_sl_tp()` — iterates open positions, checks against SL/TP levels, closes via market if triggered
3. `run_autopilot()` — runs only every `AUTOPILOT_INTERVAL` seconds (default 600s/10min), independent entry logic from scanner

Scan interval is controlled by `SCAN_INTERVAL` (default 0 = no delay between cycles).

## Strategy

- **Coin universe**: `COIN_UNIVERSE` — `"whitelist"` (strict), `"top_movers"` (top 50 by |change|), or `"all"`. Filtered by `MIN_VOLUME_24H` (default 10M USDT).
- **Signal types**:
  - `📈BOUNCE` — RSI < `RSI_OVERSOLD` (30) + momentum > `MOM_THRESHOLD` (0.15%) + price above EMA (if `USE_EMA_FILTER`)
  - `📉FADE` — RSI > `RSI_OVERBOUGHT` (70) + momentum < -`MOM_THRESHOLD` + price below EMA
  - `⚡SPIKE` — Volume ratio > `VOL_RATIO_MIN` (1.5x) + momentum alignment
- **Confidence score**: `min(9, 5 + int(|mom5| * 10) + int(vol_ratio))`. Autopilot requires `CONF_ALERT >= 7`.
- **Stop loss**: Dynamic ATR (`STOP_LOSS_ATR_MULT` x ATR, min `STOP_LOSS_PCT_FALLBACK` 2.5%). Algo orders (STOP_MARKET) return HTTP 412 on testnet — SL uses manual price-check watchdog.
- **Take profit**: Partial TP — close 50% at `TP_1_RATIO` (2x R:R), remaining at `TP_2_RATIO` (3x R:R)
- **Position sizing**: `balance * leverage * sl_pct / 100 / entry_price`, floored to symbol step size

## Discord Integration

- Board embed updated in-place each scan cycle via PATCH; if PATCH fails (msg deleted), creates new post and persists ID to `board_msg_id.txt`
- Signal alerts via webhook (`DISCORD_SIGNAL_WEBHOOK_URL`) and bot channel (`DISCORD_SIGNAL_CHANNEL_ID`)
- Board embed built by `build_board_embed()` in `utils/discord.py`; position field truncated at 1024 chars

## Self-Healing

```
Proxy errors >= 3 → counter reset (proxy rotation not yet functional)
Scan errors >= 5 → autopilot paused for 300s + Discord alert
Autopilot auto-resumes after cooldown; no persistent lock
Drawdown < -10% → Discord alert (checked in SL/TP loop)
```

## State Files

| File | Purpose |
|---|---|
| `board_msg_id.txt` | Discord embed message ID (persists across restarts) |
| `live_board_data.json` | Latest scanner board data (updated every scan cycle) |

## API quirks

- Algo orders (STOP_MARKET, TAKE_PROFIT_MARKET) return HTTP 412 on testnet — SL/TP uses manual price-check watchdog instead
- Time offset synced with Binance server on startup via `_sync_time()` in trading.py
- Signing: params in natural order, `timestamp` + `recvWindow` + `signature` appended to query string (NOT URL-encoded sorted)
