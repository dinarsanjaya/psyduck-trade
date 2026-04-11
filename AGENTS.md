# Trading Bot — OpenCode Session Guide

## Entry Points

- **`professor.py`** — Main loop: scanner, Discord board, SL/TP watchdog, autopilot. Run with `python3 professor.py`.
- **`trading.py`** — Order execution, HMAC signing, API calls. CLI: `python3 trading.py --close-all`.
- **`risk.py`** — Risk engine: drawdown breaker, Kelly criterion sizing, entry quality scoring. **Not actively used by professor.py** — inline SL/TP logic used instead.
- **`proxies.py`** — Stub. Returns `None`/`{}` — no actual proxy routing. `proxy.txt` exists but is not consumed.
- **`config.py`** — All settings: API keys, SL/TP %, leverage, coin whitelist, strategy params, Discord tokens.
- **`utils/indicators.py`** — All technical indicator calculations.
- **`utils/discord.py`** — Discord webhook helpers, board embed builder.

## Running

```bash
pip install -r requirements.txt
python3 professor.py
```

## Runtime Requirements

- **`requirements.txt`** — `requests`, `pytz`
- **`config.py`** — valid Binance Futures API keys. `FUTURES_URL = "https://fapi.binance.com"` (production).
- **`proxy.txt`** — proxy credentials (format: `http://user:pass@host:port`). Optional — not consumed by `proxies.py` stub.
- Signing style: **params NOT sorted** (natural order), `timestamp` + `recvWindow` + `signature` appended to query string.
- All HTTP requests route through `get_proxy()` (currently stubbed to `None`).

## Strategy — bullScore/bearScore

### Indicator → Score Mapping

| Indicator | Bull Condition | Bear Condition |
|-----------|---------------|----------------|
| RSI | < 30 → +3, < 40 → +2, < 45 → +1 | > 70 → +3, > 60 → +2, > 55 → +1 |
| MACD histogram | > 0 → +2 | < 0 → +2 |
| EMA alignment | EMA9 > EMA21 > EMA50 → +3 | EMA9 < EMA21 < EMA50 → +3 |
| Bollinger position | < 0.2 → +2 | > 0.8 → +2 |
| Volume spike | > 2x avg + green → +1 | > 2x avg + red → +1 |
| Momentum 5-bar | > +2% → +1 | < -2% → +1 |

### Signal Decision

```
diff = bullScore - bearScore

LONG:   bullScore >= 3 AND diff >= 1 → confidence = min(5 + diff, 10)
SHORT:  bearScore >= 3 AND diff <= -1 → confidence = min(5 + |diff|, 10)
LEAN:   diff >= 1 → LEAN_LONG (conf 6, no entry) | diff <= -1 → LEAN_SHORT (conf 6, no entry)
TIE:    RSI tiebreaker (< 50 = bull, > 50 = bear)
```

**Autopilot entry threshold: confidence >= 7, signal = LONG or SHORT only.**

## Indicators Reference (`utils/indicators.py`)

| Function | Input | Output | Notes |
|----------|-------|--------|-------|
| `calc_rsi(prices, period=14)` | price list | float 0-100 | SMA-based |
| `calc_mom(prices, bars=5)` | price list | float (% change) | 5-bar default |
| `calc_vol_ratio(volumes)` | volume list | float | avg of last 20 |
| `calc_ema(prices, length=20)` | price list | float | single EMA |
| `calc_ema_multi(prices, (9,21,50))` | price list | dict | multi-EMA |
| `calc_atr(prices, period=14)` | price list | float (absolute) | True Range avg |
| `calc_adx(prices, period=14)` | price list | float 0-100 | Wilder smoothed |
| `calc_macd(prices)` | price list | (macd, signal, hist) | EMA12/26/9 |
| `calc_bollinger_bands(prices)` | price list | (upper, mid, lower, pos) | SMA20 ± 2σ |
| `is_bullish_candle(opens, closes)` | OHLC | bool | close > open |
| `volume_spike(volumes, threshold=2.0)` | volume list | (bool, bool) | threshold × avg |

**Kline requirement: minimum 100 candles for MACD/Bollinger to compute accurately.**

## Risk Management (SL/TP)

ATR-based dynamic SL/TP in both `check_sl_tp()` (watchdog) and `scan_cycle()` (entry).

```
SL  = entry × (1 - 1.5 × ATR/price)   [LONG]
SL  = entry × (1 + 1.5 × ATR/price)   [SHORT]
TP1 = entry × (1 + 1.5 × ATR/price)   [LONG]  — close 50%
TP2 = entry × (1 + 3.0 × ATR/price)   [LONG]  — close remaining
```

Fallback if ATR unavailable: `STOP_LOSS_PCT = 2.5%`, `TAKE_PROFIT_PCT = 5.0%`.

## Filters

| Filter | Behavior |
|--------|----------|
| ADX < 25 | Market ranging — skip all signals |
| FOMC news (14:45-15:30 UTC, months 3/6/9/12) | Skip unless conf >= 8 |
| NFP (first Friday 13:00-16:00 UTC) | Skip unless conf >= 8 |
| BTC macro bias | Reduce LONG confidence if BTC bearish, reduce SHORT if BTC bullish |
| Alert cooldown | 90s per symbol between alerts |
| Volume min | `MIN_VOLUME_24H` (default 10M USDT quote volume) |

## Discord Integration

- `DISCORD_BOT_TOKEN`, `DISCORD_SIGNAL_WEBHOOK_URL`, `DISCORD_BOARD_CHANNEL_ID`, `DISCORD_SIGNAL_CHANNEL_ID` in `config.py`
- Board: PATCH update each cycle; if PATCH fails (msg deleted) → create new post, save ID to `board_msg_id.txt`
- Signal alerts: entry/SL/TP notifications to `DISCORD_SIGNAL_WEBHOOK_URL`

## State Files

| File | Purpose |
|------|---------|
| `board_msg_id.txt` | Discord embed message ID (persists across restarts) |
| `live_board_data.json` | Latest scanner data (runtime output, updated each cycle) |

## Self-Healing

- Proxy errors >= 3 → counter reset (proxy rotation not yet functional)
- Scan errors >= 5 → autopilot paused 300s + Discord alert
- Drawdown < -10% → Discord alert
- Autopilot auto-resumes after cooldown

## API Quirks

- Signing: **params NOT sorted** (natural dict order), `timestamp` + `recvWindow` + `signature` appended
- Time offset synced on startup via `_sync_time()` in `trading.py`
- Algo orders (STOP_MARKET, TAKE_PROFIT_MARKET) return HTTP 412 on testnet — SL/TP uses manual price-check watchdog
- All HTTP requests route through `get_proxy()` (currently `None`)

## Config Key Params

```python
FUTURES_URL = "https://fapi.binance.com"     # production
STOP_LOSS_PCT = 2.5          # fallback SL %
TAKE_PROFIT_PCT = 5.0        # fallback TP %
STOP_LOSS_ATR_MULT = 1.5     # SL × ATR
TP_1_RATIO = 1.5             # TP1 × ATR
TP_2_RATIO = 3.0             # TP2 × ATR
LEVERAGE = 20                # margin multiplier
MAX_POSITIONS = 3            # concurrent positions
AUTOPILOT_INTERVAL = 900     # 15 minutes
RSI_OVERSOLD = 30            # not used in new scoring (kept for compat)
RSI_OVERBOUGHT = 70          # not used in new scoring (kept for compat)
MOM_THRESHOLD = 0.15         # not used in new scoring (kept for compat)
CONF_ALERT = 7               # min confidence for autopilot entry
COIN_UNIVERSE = "top_movers" # "top_movers" | "whitelist" | "all"
```
