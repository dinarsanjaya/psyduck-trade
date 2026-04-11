# Professor Psyduck — Binance Futures Trading Bot

Production-ready autonomous trading bot for Binance Futures USDT-M perpetual. Scans coin universe for high-confidence signals, auto-executes entries, manages SL/TP via ATR-based watchdog, and maintains a live Discord board.

## Quick Start

```bash
pip install -r requirements.txt
# Edit config.py with your Binance Futures API keys
python3 professor.py
```

## Architecture

```
professor.py   Main loop: scanner → SL/TP watchdog → autopilot (sequential)
trading.py     Order execution, HMAC signing, API calls
config.py      All settings (API keys, params, Discord tokens) — NOT committed
utils/
  indicators.py  Technical indicators (RSI, MACD, Bollinger Bands, EMA, ATR, ADX, volume)
  discord.py    Discord webhooks, board embed builder
risk.py        Risk engine (drawdown breaker, Kelly criterion) — NOT used by professor.py
proxies.py      Stub (returns None/{}) — proxy not consumed
```

## Main Loop

Runs continuously in `professor.py:main()`:

1. **`scan_cycle()`** — fetches all USDT tickers, parallel klines for top coins via `ThreadPoolExecutor`, evaluates signals, executes immediate entries, updates Discord board
2. **`check_sl_tp()`** — iterates open positions, checks price against SL/TP levels, market-closes if triggered
3. **`run_autopilot()`** — runs every `AUTOPILOT_INTERVAL` (15 min), independent entry logic from scanner

Scan interval: `SCAN_INTERVAL` (default 0 = no delay between cycles). SL/TP check: `SLTP_INTERVAL` (default 0).

## Strategy — bullScore/bearScore Scoring

All indicators scored independently, highest总分 wins.

### Scoring Table

| Indicator | Condition | Score |
|-----------|-----------|-------|
| RSI | < 30 | bullScore +3 |
| RSI | < 40 | bullScore +2 |
| RSI | < 45 | bullScore +1 |
| RSI | > 70 | bearScore +3 |
| RSI | > 60 | bearScore +2 |
| RSI | > 55 | bearScore +1 |
| MACD histogram | > 0 | bullScore +2 |
| MACD histogram | < 0 | bearScore +2 |
| EMA9 > EMA21 > EMA50 | bullish | bullScore +3 |
| EMA9 < EMA21 < EMA50 | bearish | bearScore +3 |
| Bollinger position | < 0.2 (near lower) | bullScore +2 |
| Bollinger position | > 0.8 (near upper) | bearScore +2 |
| Volume > 2x avg | + green candle | bullScore +1 |
| Volume > 2x avg | + red candle | bearScore +1 |
| Momentum 5-bar | > +2% | bullScore +1 |
| Momentum 5-bar | < -2% | bearScore +1 |

### Signal Decision

```
diff = bullScore - bearScore

bullScore >= 3 AND diff >= 1 → LONG,  confidence = min(5 + diff, 10)
bearScore >= 3 AND diff <= -1 → SHORT, confidence = min(5 + |diff|, 10)
diff >= 1  → LEAN_LONG  (conf 6, no entry)
diff <= -1 → LEAN_SHORT (conf 6, no entry)
tie        → RSI tiebreaker
```

**Autopilot entry: confidence >= 7, signal must be LONG or SHORT (not LEAN).**

## Risk Management

| Parameter | Value |
|-----------|-------|
| Stop Loss | 1.5× ATR from entry (min 2.5% fallback) |
| TP1 | 1.5× ATR — close 50% |
| TP2 | 3× ATR — close remaining |
| Max positions | 3 |
| Leverage | 20× (configurable) |
| Autocloses | SL, TP1, TP2 via market order |

SL/TP uses **manual price-check watchdog** (not algo orders). Algo orders return HTTP 412 on some testnets.

## Indicators (from 100 Binance klines)

| Indicator | Formula | Used For |
|-----------|---------|----------|
| RSI(14) | SMA-based relative strength | Oversold/overbought |
| MACD | EMA12 - EMA26, signal EMA9 | Trend direction |
| Bollinger Bands | SMA20 ± 2σ | Mean reversion |
| EMA | EMA9, EMA21, EMA50 | Trend alignment |
| Volume ratio | current / avg(20) | Volume confirmation |
| Momentum | % change last 5 candles | Momentum strength |
| ATR(14) | (High-Low max) / price | Dynamic SL/TP |
| ADX(14) | Wilder smoothed DMI | Trend strength filter (ADX < 25 = ranging, skip) |

## Filters

- **ADX filter**: skip if ADX < 25 (market ranging)
- **News cooldown**: skip entries during high-impact news (FOMC 14:45-15:30 UTC, NFP first Friday 13:00-16:00 UTC) unless confidence >= 8
- **BTC macro bias**: reduce LONG confidence when BTC in bear macro, reduce SHORT confidence when BTC in bull macro
- **Alert cooldown**: 90s per symbol between alerts

## Discord Integration

- Bot token + webhook URL in `config.py`
- Board channel (`DISCORD_BOARD_CHANNEL_ID`): live embed updated via PATCH each cycle
- Signal channel (`DISCORD_SIGNAL_CHANNEL_ID`): entry/SL/TP alerts
- Board persists across restarts via `board_msg_id.txt`

## Data Requirements

- **Klines**: 100 candles for MACD/Bollinger, 50+ for ATR/ADX
- **Tickers**: 24h stats (priceChangePercent, quoteVolume)
- **Time sync**: `_sync_time()` on startup (Binance server time offset)

## Files Reference

| File | Description |
|------|-------------|
| `professor.py` | Main loop, signal scoring, Discord board, SL/TP watchdog |
| `trading.py` | `place_order`, `market_close`, `set_leverage`, `get_account`, `get_positions`, `calc_quantity_from_risk`, `--close-all` CLI |
| `config.py` | API keys, SL/TP %, leverage, coin whitelist, Discord tokens (gitignored) |
| `risk.py` | Drawdown breaker, Kelly criterion — **not used by professor.py** |
| `proxies.py` | Stub — returns `None/{}`, proxy.txt not consumed |
| `utils/indicators.py` | All indicator calculations |
| `utils/discord.py` | `discord_req`, `discord_notify`, `build_board_embed`, `get/save_board_msg_id` |

## State Files

| File | Purpose |
|------|---------|
| `board_msg_id.txt` | Discord embed message ID (persists across restarts) |
| `live_board_data.json` | Latest scanner data (runtime output, updated every cycle) |

## Self-Healing

- **Proxy errors >= 3** → counter reset (proxy rotation not yet functional)
- **Scan errors >= 5** → autopilot paused 300s + Discord alert
- **Drawdown < -10%** → Discord alert (checked in SL/TP loop)
- Autopilot auto-resumes after cooldown

## API Quirks

- Signing: **params NOT sorted**, `timestamp` + `recvWindow` + `signature` appended
- Time offset synced on startup via `_sync_time()`
- HTTP 412 on algo orders (STOP_MARKET) — watchdog used instead
- Rate limit: 1200 requests/minute per IP

## Disclaimer

Futures trading involves substantial risk of loss. This bot executes real orders. Past performance does not guarantee future results.
