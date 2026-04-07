# Professor Psyduck — Binance Futures Trading Bot

Autonomous trading bot for Binance Futures Testnet with live signal scanning, Discord board, and autopilot execution.

## Quick Start

```bash
# 1. Configure proxy
cp proxy.txt.example proxy.txt
# Edit proxy.txt with your proxy credentials (http://user:pass@host:port)

# 2. Configure API keys
# Edit config.py with your Binance Futures Testnet API keys
# Get keys at: https://www.binance.com/en/my/settings/api-management (enable Testnet)

# 3. Run
python3 professor.py
```

## Features

- **Live Scanner** — Scans 15 whitelisted coins every 10s for RSI/volume/momentum signals
- **Discord Board** — Live-updated embed showing signals, open positions, and PnL
- **Autopilot** — Auto-opens positions when high-confidence signals fire
- **SL/TP Watchdog** — Monitors positions and auto-closes at dynamic SL or partial TP levels
- **Proxy Rotation** — Sticky proxy per session, auto-reset on failure

## Strategy

| Parameter | Value |
|---|---|
| Entry signals | RSI < 30 (long) / RSI > 70 (short) + momentum + EMA trend confirmation |
| Min confidence | 7/9 |
| Stop loss | Dynamic ATR (2x) or 2.5% fallback |
| Take profit | 2:1 (50%) and 3:1 (50% remaining) |
| Max positions | 3 |
| Leverage | 10x |
| Scan interval | 10s |
| Autopilot interval | 10min |

## Files

```
professor.py   — Main entry point
trading.py     — Order execution
risk.py        — Risk management
proxies.py     — Proxy rotation
config.py      — Configuration
proxy.txt      — Proxy credentials
AGENTS.md      — Developer guide
README.md      — This file
```

## Requirements

- Python 3.8+
- `requests`, `pytz` (pip install requests pytz)
- Valid proxy (HTTP format)
- Binance Futures Testnet API keys

## Disclaimer

This bot trades on testnet only. Past performance does not guarantee future results. Use at your own risk.
