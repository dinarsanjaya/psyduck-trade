# Trading Bot — OpenCode Session Guide

## Entry Points

- **`professor.py`** — Main unified system: live scanner, Discord board updater, SL/TP watchdog, and autopilot executor. Run with `python3 professor.py`.
- **`trading.py`** — Order execution: place/market/close orders, leverage, position sizing, account queries. CLI: `python3 trading.py --close-all`.
- **`risk.py`** — Risk management: drawdown breaker, dynamic SL (ATR-based), Kelly criterion sizing, entry quality scoring, trade journal. **Not actively used by professor.py** (inline SL/TP logic used instead).
- **`proxies.py`** — Stub module. Returns `None`/`{}` — no actual proxy routing. `proxy.txt` exists but is not consumed.
- **`config.py`** — All settings: API keys, SL/TP %, leverage, coin whitelist, strategy parameters, Discord tokens.
- **`utils/indicators.py`** — RSI, momentum, EMA, ATR, ADX, volume ratio calculations.
- **`utils/discord.py`** — Discord webhook/board helpers.

## Running

```bash
pip install -r requirements.txt
python3 professor.py
```

## Runtime Requirements

- **`requirements.txt`** — `requests`, `pytz`
- **`config.py`** — valid Binance Futures API keys. `FUTURES_URL = "https://fapi.binance.com"` (production).
- **`proxy.txt`** — proxy credentials (format: `http://user:pass@host:port`, one per line). Optional — not consumed by current `proxies.py` stub.
- Signing style: **params NOT sorted** (natural order), `timestamp` + `recvWindow` + `signature` appended to query string.
- All HTTP requests route through `get_proxy()` (currently stubbed to `None`).

## Strategy

**Coin Universe:** `COIN_UNIVERSE` setting in config (`"top_movers"`). `COINS_WHITELIST` includes BTC, ETH, BNB, SOL, XRP, ADA, AVAX, DOT, MATIC, LINK, UNI, ATOM, NEAR, AAVE, LTC, APT, ARB, OP + meme coins (DOGE, SHIB, PEPE, FLOKI, WIF).

**Signal Scoring (bullScore/bearScore):**
- RSI < 30 → bullScore +3 | RSI > 70 → bearScore +3
- MACD histogram > 0 → bullScore +2 | < 0 → bearScore +2
- EMA9 > EMA21 > EMA50 → bullScore +3 | EMA9 < EMA21 < EMA50 → bearScore +3
- BB position < 0.2 → bullScore +2 | > 0.8 → bearScore +2
- Volume > 2x avg + green → bullScore +1 | + red → bearScore +1
- Momentum > +2% → bullScore +1 | < -2% → bearScore +1

**Signal Decision:**
- bullScore >= 3 AND diff >= 1 → LONG, conf = min(5 + diff, 10)
- bearScore >= 3 AND diff <= -1 → SHORT, conf = min(5 + |diff|, 10)
- diff >= 1 → LEAN_LONG (conf 6) | diff <= -1 → LEAN_SHORT (conf 6)

**Risk Management:**
- SL: 1.5× ATR from entry
- TP1: 1.5× ATR (RR 1:1.5) — close 50%
- TP2: 3× ATR (RR 1:3) — close remaining

**Autopilot:** Runs every 15 min. Takes positions if `open_count < MAX_POSITIONS` and confidence >= 7.

## State Files

| File | Purpose |
|---|---|
| `board_msg_id.txt` | Discord embed message ID (persists across restarts) |

## Discord Integration

- Bot token, webhook URL, and channel IDs in `config.py` (`DISCORD_BOT_TOKEN`, `DISCORD_SIGNAL_WEBHOOK_URL`, `DISCORD_BOARD_CHANNEL_ID`, `DISCORD_SIGNAL_CHANNEL_ID`).
- Board message created once, updated in-place via PATCH each cycle.

## API quirks

- Algo orders (STOP_MARKET, TAKE_PROFIT_MARKET) return HTTP 412 on testnet — SL/TP uses manual price-check watchdog instead.
- Time offset synced with Binance server on startup via `_sync_time()` in `trading.py`.
