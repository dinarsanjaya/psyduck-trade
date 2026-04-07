# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Professor Psyduck** — Binance Futures Testnet trading bot with live signal scanning, Discord board, and autopilot execution. All trading is testnet only.

## Running

```bash
python3 professor.py
```

## Requirements

- Python 3.8+, `requests`, `pytz`
- `proxy.txt` — HTTP proxy credentials (format: `http://user:pass@host:port`, one per line). Required at runtime.
- `config.py` — valid Binance Futures Testnet API keys (testnet keys only; demo keys don't fill orders)

## Architecture

```
professor.py   — Main entry point. Runs three concurrent loops:
                 1. Scanner cycle (every 30s): fetches 24h tickers + klines for whitelist coins, evaluates signals
                 2. SL/TP watchdog (every 30s): checks open positions against dynamic SL and partial TP levels
                 3. Autopilot (every 10min): opens new positions when confidence >= 7/9

trading.py     — Order execution module. All requests route through sticky proxy.
                 Key functions: place_order, market_close, set_leverage, get_account, get_positions,
                 calc_quantity_from_risk, get_symbol_precision
                 Signing: params NOT sorted, `timestamp` + `recvWindow` + `signature` appended in that order

risk.py        — Risk management: DrawdownBreaker (10% daily loss circuit breaker), dynamic SL via ATR,
                 signal_quality_check, entry_score, trade journal

proxies.py     — Sticky proxy per session (random pick from proxy.txt). reset_proxy() re-randomizes.
                 test_all_proxies() validates all proxies against test endpoint.
```

## Strategy

- **Coin universe**: Strict whitelist (default 8 coins). Filtered by `MIN_VOLUME_24H`.
- **Entry signals**: RSI < 30 (long) or RSI > 70 (short) + momentum > 0.15% + volume spike > 1.5x + EMA trend confirmation
- **Confidence**: 0-9 scale. Autopilot requires `CONF_ALERT >= 7`.
- **Stop loss**: Dynamic ATR (2x ATR or 2.5% fallback). Algo orders (STOP_MARKET) return HTTP 4120 on testnet — SL/TP uses manual watchdog instead.
- **Take profit**: Partial TP — close 50% at 2:1 R:R, remaining at 3:1 R:R
- **Max positions**: `MAX_POSITIONS` (default 10)
- **Leverage**: `LEVERAGE` (default 20x)

## Discord Integration

- Board message updated in-place (PATCH) each scan cycle; persists message ID in `board_msg_id.txt`
- Signal alerts sent via webhook + bot channel
- `DISCORD_BOT_TOKEN`, `DISCORD_SIGNAL_WEBHOOK_URL`, `DISCORD_BOARD_CHANNEL_ID`, `DISCORD_SIGNAL_CHANNEL_ID` in `config.py`

## Self-Healing

```
Proxy errors >= 3 → reset_proxy() + re-randomize
Scan errors >= 5 → pause autopilot for 5 minutes + Discord alert
Drawdown < -10% → Discord alert (positions checked via trading.py)
```

## State Files

| File | Purpose |
|---|---|
| `board_msg_id.txt` | Discord embed message ID (persists across restarts) |
| `live_board_data.json` | Latest scanner data (updated every scan cycle) |

## API quirks

- Algo orders (STOP_MARKET, TAKE_PROFIT_MARKET) return HTTP 4120 on testnet — code falls back to manual price-check watchdog
- All HTTP requests route through sticky proxy from `proxy.txt`
- Time sync with Binance server on startup (`_sync_time()`)
