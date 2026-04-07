# Professor Psyduck — Binance Futures Trading Bot

Autonomous trading bot for Binance Futures Testnet with live signal scanning, Discord board, SL/TP watchdog, autopilot execution, and self-healing.

## Quick Start

```bash
# 1. Install dependencies
pip install requests pytz

# 2. Configure proxy
cp proxy.example.txt proxy.txt
# Edit proxy.txt with your proxy credentials (http://user:pass@host:port)

# 3. Configure API keys
cp config.example.py config.py
# Edit config.py with your Binance Futures Testnet API keys
# Get keys at: https://www.binance.com/en/my/settings/api-management (enable Testnet mode)

# 4. Run
python3 professor.py
```

## Features

- **Live Scanner** — Scans 15 whitelisted coins every 60s for RSI/volume/momentum signals
- **Discord Board** — Live-updated embed showing signals and open positions
- **Signal Notifications** — Discord webhook alerts for entries and exits
- **SL/TP Watchdog** — Monitors positions, auto-closes at dynamic SL or partial TP levels
- **Autopilot** — Auto-opens positions when high-confidence signals fire
- **Self-Healing** — Auto-reset proxy on errors, circuit breaker for autopilot
- **Proxy Rotation** — Sticky proxy per session, auto-reset on failure

## Strategy

| Parameter | Value |
|---|---|
| Entry signals | RSI < 30 (long) / RSI > 70 (short) + momentum > 0.15% + EMA confirmation |
| Min confidence | 7/9 |
| Stop loss | Dynamic ATR (2x) or 2.5% fallback |
| Take profit | 2:1 (50%) and 3:1 (50% remaining) |
| Max positions | 10 |
| Leverage | 20x |
| Scan interval | 60s |
| Autopilot interval | 10min |

## Whitelisted Coins

BTCUSDT, ETHUSDT, BNBUSDT, SOLUSDT, XRPUSDT, DOGEUSDT, ADAUSDT, AVAXUSDT, DOTUSDT, LINKUSDT, MATICUSDT, LTCUSDT, ATOMUSDT, UNIUSDT, ETCUSDT

## Files

```
professor.py      — Main entry point (Scanner + Discord + SL/TP + Autopilot)
trading.py        — Order execution module
risk.py           — Risk management
proxies.py        — Proxy rotation
config.example.py — Configuration template (copy to config.py)
proxy.example.txt — Proxy template (copy to proxy.txt)
AGENTS.md         — Developer guide
README.md         — This file
```

## Self-Healing Rules

- **Proxy errors >= 3** → Auto-reset proxy, retry
- **Scan errors >= 5** → Pause autopilot for 5 minutes, scanning continues
- **Drawdown < -10%** → Discord alert
- Autopilot auto-resumes after cooldown

## Requirements

- Python 3.8+
- `requests`, `pytz`
- Valid proxy (HTTP format)
- Binance Futures Testnet API keys

## Disclaimer

This bot trades on testnet only. Past performance does not guarantee future results. Use at your own risk.
