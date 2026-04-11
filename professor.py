#!/usr/bin/env python3
"""
Professor Mode - Unified Trading System
Scanner + SL/TP Watchdog + Autopilot + Discord Board
"""

import time
import json
import requests
import warnings

warnings.filterwarnings("ignore")

from datetime import datetime
import pytz
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

# ─── CONFIG ───────────────────────────────────────────────────────────────────
from config import (
    DISCORD_BOARD_CHANNEL_ID,
    STOP_LOSS_PCT,
    TAKE_PROFIT_PCT,
    MAX_POSITIONS,
    LEVERAGE,
    FUTURES_URL as BASE_URL,
    COINS_WHITELIST,
    COIN_UNIVERSE,
    MIN_VOLUME_24H,
    RSI_OVERSOLD,
    RSI_OVERBOUGHT,
    MOM_THRESHOLD,
    VOL_RATIO_MIN,
    CONF_ALERT,
    USE_DYNAMIC_SL,
    STOP_LOSS_ATR_MULT,
    STOP_LOSS_PCT_FALLBACK,
    USE_EMA_FILTER,
    EMA_LENGTH,
    USE_PARTIAL_TP,
    TP_1_RATIO,
    TP_2_RATIO,
    AUTOPILOT_INTERVAL,
)

# ─── UTILS ───────────────────────────────────────────────────────────────────
from utils.indicators import (
    calc_rsi,
    calc_mom,
    calc_vol_ratio,
    calc_ema,
    calc_atr,
    calc_adx,
)
from utils.discord import (
    discord_notify,
    discord_req,
    build_board_embed,
    get_board_msg_id,
    save_board_msg_id,
)

LIVE_DATA_FILE = "live_board_data.json"
SCAN_INTERVAL = 0
SLTP_INTERVAL = 0

ALERT_COOLDOWN = 90
HISTORY_CANDLES = 20

# ─── MACRO / NEWS FILTERS ─────────────────────────────────────────────────────

# Known high-impact news windows (UTC). FOMC: ~14:00-15:30 UTC on meeting day.
# NFP: first Friday of month, 13:30-15:00 UTC.
# Format: (month_1based, hour, minute) — only hour/minute checked for simplicity
HIGH_IMPACT_FOMC = {
    (3, 14),
    (3, 15),  # March
    (6, 14),
    (6, 15),  # June
    (9, 14),
    (9, 15),  # September
    (12, 14),
    (12, 15),  # December
}


def is_high_impact_news_time():
    """Return event name if high-impact news window is active, else None."""
    now_utc = datetime.utcnow()
    # NFP: first Friday of month, 13:00-16:00 UTC
    if now_utc.weekday() == 4 and now_utc.day <= 7:
        if 13 <= now_utc.hour < 16:
            return "NFP"
    # FOMC: skip 13:45-16:00 UTC on known meeting months
    if (now_utc.month, now_utc.hour) in HIGH_IMPACT_FOMC and now_utc.minute >= 45:
        return "FOMC"
    if now_utc.hour == 15 and now_utc.minute <= 30:
        if now_utc.month in (3, 6, 9, 12):
            return "FOMC"
    return None


def get_btc_macro():
    """Fetch BTCUSDT klines and return macro dict: {mom, rsi, adx, bias}."""
    try:
        btc_kl = fetch_klines("BTCUSDT")
        if not btc_kl or len(btc_kl) < 20:
            return None
        closes = [float(k[4]) for k in btc_kl]
        btc_mom = calc_mom(closes, 5)
        btc_rsi = calc_rsi(closes)
        btc_adx = calc_adx(closes)
        bias = "NEUTRAL"
        if btc_mom > 0.5 and btc_rsi > 60:
            bias = "BTC_BULL"
        elif btc_mom < -0.5 and btc_rsi < 40:
            bias = "BTC_BEAR"
        return {"mom": btc_mom, "rsi": btc_rsi, "adx": btc_adx, "bias": bias}
    except:
        return None


# ─── STATE ────────────────────────────────────────────────────────────────────
latest_tickers = {}
ticker_history = defaultdict(list)
last_alert = {}
board_cycle = 0

# ─── SELF-HEALING STATE ───────────────────────────────────────────────────────
consecutive_proxy_errors = 0
consecutive_scan_errors = 0
autopilot_paused_until = 0
last_health_check = 0
HEALTH_CHECK_INTERVAL = 60
PROXY_ERROR_THRESHOLD = 3
SCAN_ERROR_THRESHOLD = 5
AUTOPILOT_COOLDOWN = 300
MAX_DRAWDOWN_PCT = -10

# ─── SELF-HEALING ─────────────────────────────────────────────────────────────


def self_heal_proxy():
    global consecutive_proxy_errors
    consecutive_proxy_errors += 1
    if consecutive_proxy_errors >= PROXY_ERROR_THRESHOLD:
        print(
            f"[SELF-HEAL] Proxy errors ({consecutive_proxy_errors}) >= {PROXY_ERROR_THRESHOLD} — resetting proxy"
        )
        consecutive_proxy_errors = 0
        return True
    return False


def self_heal_circuit_breaker():
    global consecutive_scan_errors, autopilot_paused_until
    consecutive_scan_errors += 1
    if consecutive_scan_errors >= SCAN_ERROR_THRESHOLD and autopilot_paused_until == 0:
        autopilot_paused_until = time.time() + AUTOPILOT_COOLDOWN
        print(
            f"[SELF-HEAL] Scan errors ({consecutive_scan_errors}) >= {SCAN_ERROR_THRESHOLD} — pausing autopilot for {AUTOPILOT_COOLDOWN}s"
        )
        discord_notify(
            f"⚠️ Autopilot PAUSED — {consecutive_scan_errors} consecutive errors",
            f"Autopilot will auto-resume in ~{AUTOPILOT_COOLDOWN // 60} minutes.\nScanning continues normally.",
            color=0xFFAA00,
        )
        return True
    return False


def self_heal_check_drawdown(positions):
    try:
        from trading import get_account

        acc = get_account()
        total_balance = next(
            (
                float(a.get("balance", 0)) + float(a.get("availableBalance", 0))
                for a in acc
                if a.get("asset") == "USDT"
            ),
            0,
        )
        if total_balance <= 0:
            return False
        open_pos = [p for p in positions if float(p.get("positionAmt", 0)) != 0]
        total_pnl = sum(float(p.get("unRealizedProfit", 0)) for p in open_pos)
        total_notional = sum(
            abs(float(p.get("positionAmt", 0)) * float(p.get("entryPrice", 1)))
            for p in open_pos
        )
        if total_notional > 0:
            drawdown_pct = (total_pnl / total_notional) * 100
            if drawdown_pct < MAX_DRAWDOWN_PCT:
                print(
                    f"[SELF-HEAL] Drawdown alert: {drawdown_pct:.1f}% < {MAX_DRAWDOWN_PCT}%"
                )
                discord_notify(
                    f"🚨 DRAWDOWN ALERT — {drawdown_pct:.1f}%",
                    f"Total PnL: `${total_pnl:+.2f}`\nNotional: `${total_notional:.2f}`\nBalance: `${total_balance:.2f}`",
                    color=0xFF0000,
                )
                return True
    except:
        pass
    return False


def self_heal_health_check():
    global \
        last_health_check, \
        consecutive_scan_errors, \
        consecutive_proxy_errors, \
        autopilot_paused_until
    now = time.time()
    if now - last_health_check < HEALTH_CHECK_INTERVAL:
        return
    last_health_check = now
    if autopilot_paused_until > 0 and now >= autopilot_paused_until:
        print(f"[SELF-HEAL] Autopilot cooldown expired — resuming")
        autopilot_paused_until = 0
        consecutive_scan_errors = 0
        discord_notify(
            "✅ Autopilot RESUMED",
            "Self-healing complete. Autopilot is active again.",
            color=0x00FF00,
        )
    consecutive_proxy_errors = 0
    consecutive_scan_errors = 0


# ─── BINANCE API ─────────────────────────────────────────────────────────────


def fetch_all_tickers():
    url = f"{BASE_URL}/fapi/v1/ticker/24hr"
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    return {t["symbol"]: t for t in r.json()}


def fetch_klines(symbol, limit=HISTORY_CANDLES):
    url = f"{BASE_URL}/fapi/v1/klines"
    for attempt in range(3):
        try:
            r = requests.get(
                url,
                params={"symbol": symbol, "interval": "1m", "limit": limit},
                timeout=10,
            )
            r.raise_for_status()
            return r.json()
        except:
            if attempt < 2:
                continue
            raise
    return None


def get_positions():
    from trading import get_positions as gp

    return gp()


def market_close(symbol, side, qty):
    from trading import market_close as mc

    return mc(symbol, side, qty)


# ─── SIGNAL CHECK ────────────────────────────────────────────────────────────


def check_signal(sym, ticker, klines_data, btc_macro=None):
    now = time.time()
    if sym in last_alert and (now - last_alert[sym]) < ALERT_COOLDOWN:
        return None
    kl = klines_data.get(sym)
    if not kl or len(kl) < 100:
        return None

    highs = [float(k[2]) for k in kl]
    lows = [float(k[3]) for k in kl]
    closes = [float(k[4]) for k in kl]
    volumes = [float(k[5]) for k in kl]
    opens = [float(k[1]) for k in kl]

    price = float(ticker.get("lastPrice", closes[-1]))
    change_pct = float(ticker.get("priceChangePercent", 0))

    rsi = calc_rsi(closes)
    mom5 = calc_mom(closes, 5)
    vol_ratio = calc_vol_ratio(volumes)
    atr = calc_atr(closes)
    adx = calc_adx(closes)
    macd_line, signal_line, histogram = calc_macd(closes)
    bb_upper, bb_middle, bb_lower, bb_position = calc_bollinger_bands(closes)
    ema_multi = calc_ema_multi(closes, (9, 21, 50))
    ema9 = ema_multi.get(9)
    ema21 = ema_multi.get(21)
    ema50 = ema_multi.get(50)

    # ADX filter: skip if market is ranging
    if adx is not None and adx < 25:
        return None

    news_event = is_high_impact_news_time()

    # ── Scoring System ─────────────────────────────────────────────────────
    bullScore = 0
    bearScore = 0

    # RSI scoring
    if rsi < 30:
        bullScore += 3
    elif rsi < 40:
        bullScore += 2
    elif rsi < 45:
        bullScore += 1

    if rsi > 70:
        bearScore += 3
    elif rsi > 60:
        bearScore += 2
    elif rsi > 55:
        bearScore += 1

    # MACD scoring
    if histogram is not None and histogram > 0:
        bullScore += 2
    elif histogram is not None and histogram < 0:
        bearScore += 2

    # EMA alignment scoring
    if ema9 is not None and ema21 is not None and ema50 is not None:
        if ema9 > ema21 > ema50:
            bullScore += 3
        elif ema9 < ema21 < ema50:
            bearScore += 3

    # Bollinger Bands scoring
    if bb_position is not None:
        if bb_position < 0.2:
            bullScore += 2
        elif bb_position > 0.8:
            bearScore += 2

    # Volume spike scoring
    if vol_ratio > 2.0:
        is_green = closes[-1] > opens[-1]
        if is_green:
            bullScore += 1
        else:
            bearScore += 1

    # Momentum scoring (5 bars)
    if mom5 > 2.0:
        bullScore += 1
    elif mom5 < -2.0:
        bearScore += 1

    # ── Signal Decision ────────────────────────────────────────────────────
    diff = bullScore - bearScore

    if bullScore >= 3 and diff >= 1:
        signal = "LONG"
        confidence = min(5 + diff, 10)
    elif bearScore >= 3 and diff <= -1:
        signal = "SHORT"
        confidence = min(5 + abs(diff), 10)
    elif diff >= 1:
        signal = "LEAN_LONG"
        confidence = 6
    elif diff <= -1:
        signal = "LEAN_SHORT"
        confidence = 6
    else:
        signal = "RSI_BULL" if rsi < 50 else "RSI_BEAR"
        confidence = 5

    # BTC macro bias
    if btc_macro and btc_macro.get("bias") == "BTC_BULL" and signal == "SHORT":
        confidence = max(6, confidence - 2)
    elif btc_macro and btc_macro.get("bias") == "BTC_BEAR" and signal == "LONG":
        confidence = max(6, confidence - 2)

    # News cooldown
    if news_event and confidence < 8:
        return None

    # Only LONG/SHORT (not LEAN) with conf >= 7 for autopilot entry
    if signal not in ("LONG", "SHORT"):
        return None
    if confidence < CONF_ALERT:
        return None

    # ── SL/TP via ATR ──────────────────────────────────────────────────────
    sl_pct = None
    tp1_pct = None
    tp2_pct = None
    if atr is not None and atr > 0:
        atr_pct = (atr / price) * 100
        sl_pct = atr_pct * 1.5
        tp1_pct = atr_pct * 1.5
        tp2_pct = atr_pct * 3.0
    else:
        sl_pct = STOP_LOSS_PCT
        tp1_pct = TAKE_PROFIT_PCT
        tp2_pct = TAKE_PROFIT_PCT * 2

    return {
        "symbol": sym,
        "signal": signal,
        "price": price,
        "change_pct": change_pct,
        "confidence": confidence,
        "rsi": round(rsi, 1),
        "mom5": round(mom5, 2),
        "vol_ratio": round(vol_ratio, 2),
        "atr": atr,
        "adx": round(adx, 1) if adx else None,
        "bullScore": bullScore,
        "bearScore": bearScore,
        "sl_pct": sl_pct,
        "tp1_pct": tp1_pct,
        "tp2_pct": tp2_pct,
        "ema9": ema9,
        "ema21": ema21,
        "ema50": ema50,
        "macd_hist": histogram,
        "bb_position": bb_position,
    }


# ─── SL/TP WATCHDOG ─────────────────────────────────────────────────────────


def check_sl_tp():
    try:
        positions = get_positions()
        if not positions:
            return

        open_count = sum(1 for p in positions if float(p.get("positionAmt", 0)) != 0)
        print(f"[SLTP] Checking {open_count} open positions")

        for p in positions:
            amt = float(p.get("positionAmt", 0))
            if amt == 0:
                continue
            entry = float(p.get("entryPrice", 0))
            sym = p["symbol"]
            upnl = float(p.get("unRealizedProfit", 0))

            direction = "LONG" if amt > 0 else "SHORT"
            side = "SELL" if amt > 0 else "BUY"
            abs_amt = abs(amt)

            # Get current price
            price = None
            url = f"{BASE_URL}/fapi/v1/ticker/price"
            for _ in range(2):
                try:
                    r = requests.get(url, params={"symbol": sym}, timeout=8)
                    if r.status_code == 200:
                        price = float(r.json()["price"])
                        break
                except:
                    continue
            if price is None:
                price = latest_tickers.get(sym, {}).get("lastPrice")
                if price:
                    price = float(price)
            if price is None or price == 0:
                continue

            # Dynamic SL using ATR
            try:
                kl = fetch_klines(sym)
                if kl and len(kl) >= 100:
                    closes = [float(k[4]) for k in kl]
                    atr = calc_atr(closes)
                    if atr:
                        atr_pct = atr / price * 100
                        sl_pct = max(atr_pct * 1.5, STOP_LOSS_PCT_FALLBACK)
                        tp1_pct = atr_pct * 1.5
                        tp2_pct = atr_pct * 3.0
                    else:
                        sl_pct = STOP_LOSS_PCT
                        tp1_pct = TAKE_PROFIT_PCT
                        tp2_pct = TAKE_PROFIT_PCT * 2
                else:
                    sl_pct = STOP_LOSS_PCT
                    tp1_pct = TAKE_PROFIT_PCT
                    tp2_pct = TAKE_PROFIT_PCT * 2
            except:
                sl_pct = STOP_LOSS_PCT
                tp1_pct = TAKE_PROFIT_PCT
                tp2_pct = TAKE_PROFIT_PCT * 2

            if direction == "LONG":
                sl_price = round(entry * (1 - sl_pct / 100), 8)
                tp1_price = round(entry * (1 + tp1_pct / 100), 8)
                tp2_price = round(entry * (1 + tp2_pct / 100), 8)
            else:
                sl_price = round(entry * (1 + sl_pct / 100), 8)
                tp1_price = round(entry * (1 - tp1_pct / 100), 8)
                tp2_price = round(entry * (1 - tp2_pct / 100), 8)

            # Check SL/TP trigger
            triggered = None
            if direction == "LONG" and price <= sl_price:
                triggered = ("SL", sl_price, price, abs_amt)
            elif direction == "SHORT" and price >= sl_price:
                triggered = ("SL", sl_price, price, abs_amt)
            elif USE_PARTIAL_TP:
                if direction == "LONG" and price >= tp1_price:
                    triggered = ("TP1", tp1_price, price, abs_amt / 2)
                elif direction == "SHORT" and price <= tp1_price:
                    triggered = ("TP1", tp1_price, price, abs_amt / 2)
                elif direction == "LONG" and price >= tp2_price:
                    triggered = ("TP2", tp2_price, price, abs_amt)
                elif direction == "SHORT" and price <= tp2_price:
                    triggered = ("TP2", tp2_price, price, abs_amt)

            if not triggered:
                if abs(upnl) > 10:
                    print(
                        f"[SLTP] {sym} {direction}: price={price:.4f} entry={entry:.4f} sl={sl_price:.4f} tp1={tp1_price:.4f} tp2={tp2_price:.4f} upnl={upnl:.2f}"
                    )
            else:
                level, level_price, current_price, close_qty = triggered
                print(
                    f"[SLTP] {sym} {direction} — {level} triggered! price={price:.4f} vs level={level_price:.4f}, qty={close_qty:.4f}"
                )

                result = market_close(sym, side, close_qty)
                if result and result.get("orderId"):
                    emoji = "✅" if level.startswith("TP") else "❌"
                    color = 0x00FF00 if level.startswith("TP") else 0xFF4444
                    level_name = level.replace("TP1", "TP 50%").replace(
                        "TP2", "TP 100%"
                    )
                    exit_price = current_price
                    fills = result.get("fills", [])
                    if fills:
                        try:
                            exit_price = sum(
                                float(f.get("price", 0)) for f in fills
                            ) / len(fills)
                        except:
                            pass
                    if exit_price and entry:
                        realized_pnl = (
                            (exit_price - entry) * close_qty
                            if direction == "LONG"
                            else (entry - exit_price) * close_qty
                        )
                    else:
                        realized_pnl = 0.0
                    print(f"\n{'=' * 55}")
                    print(f"{emoji} {sym} {level_name} HIT! Qty: {close_qty:.4f}")
                    print(
                        f"   Entry: ${entry} | Exit: ${exit_price:.4f} | Realized PnL: ${realized_pnl:+.4f}"
                    )
                    print(f"{'=' * 55}")
                    discord_notify(
                        f"{emoji} {sym} {level_name} Hit",
                        f"**Direction:** {direction}\n**Entry:** `${entry}`\n**Exit:** `${exit_price:.4f}`\n**Qty:** `{close_qty:.4f}`\n**Realized PnL:** `${realized_pnl:+.4f}`\n**Leverage:** {LEVERAGE}x",
                        color=color,
                    )
                time.sleep(0.5)

        try:
            self_heal_check_drawdown(positions)
        except:
            pass
    except Exception as e:
        print(f"[SL/TP ERROR] {e}")


# ─── AUTOPILOT ────────────────────────────────────────────────────────────────


def run_autopilot():
    try:
        from trading import (
            place_order,
            set_leverage,
            calc_quantity_from_risk,
            get_account,
        )

        acc = get_account()
        usdt_balance = next(
            (
                float(a.get("availableBalance", 0))
                for a in acc
                if a.get("asset") == "USDT"
            ),
            0,
        )
        positions = get_positions()
        open_syms = {
            p["symbol"] for p in positions if float(p.get("positionAmt", 0)) != 0
        }
        open_count = len(open_syms)

        print(
            f"\n[AUTOPILOT] Balance: ${usdt_balance:.2f} | Positions: {open_count}/{MAX_POSITIONS}"
        )

        if COIN_UNIVERSE == "whitelist":
            coins_to_check = [
                (s, t) for s, t in latest_tickers.items() if s in COINS_WHITELIST
            ]
        elif COIN_UNIVERSE == "top_movers":
            coins_to_check = sorted(
                latest_tickers.items(),
                key=lambda x: abs(float(x[1].get("priceChangePercent", 0) or 0)),
                reverse=True,
            )[:50]
        else:
            coins_to_check = list(latest_tickers.items())
        coins_to_check.sort(
            key=lambda x: float(x[1].get("quoteVolume", 0) or 0), reverse=True
        )

        for sym, ticker in coins_to_check:
            if open_count >= MAX_POSITIONS:
                break
            if sym in open_syms:
                continue
            try:
                kl = fetch_klines(sym)
            except:
                continue
            if not kl or len(kl) < 100:
                continue

            sig = check_signal(sym, ticker, {sym: kl})
            if not sig:
                continue

            side = "BUY" if sig["signal"] == "LONG" else "SELL"
            try:
                set_leverage(sym, LEVERAGE)
                qty = calc_quantity_from_risk(
                    sym,
                    usdt_balance,
                    sig["price"],
                    sig.get("sl_pct") or STOP_LOSS_PCT_FALLBACK,
                    LEVERAGE,
                )
                if qty <= 0:
                    continue
                result = place_order(sym, side, "MARKET", qty)
                if result and result.get("orderId"):
                    fills = result.get("fills", [{}])
                    avg_price = (
                        float(fills[0].get("price", sig["price"]))
                        if fills
                        else sig["price"]
                    )
                    print(
                        f"  [ENTRY] {sym} {sig['signal']} @ ${avg_price} | qty={qty} | Lev={LEVERAGE}x | conf={sig['confidence']}/10"
                    )
                    discord_notify(
                        f"✅ {sym} {sig['signal']} Opened",
                        f"**Price:** `${avg_price}`\n**Qty:** `{qty}`\n**Leverage:** {LEVERAGE}x\n**Conf:** {sig['confidence']}/10\n**RSI:** {sig['rsi']:.0f} | **Mom:** {sig['mom5']:+.2f}%\n**BB:** {sig.get('bb_position', 'N/A')}",
                        color=0x00FF00 if sig["signal"] == "LONG" else 0xFF4444,
                    )
                    open_count += 1
                    time.sleep(1)
            except Exception as e:
                print(f"  [ENTRY ERROR] {sym}: {e}")
    except Exception as e:
        print(f"[AUTOPILOT ERROR] {e}")


# ─── SCAN CYCLE ─────────────────────────────────────────────────────────────


def scan_cycle():
    global latest_tickers, ticker_history, last_alert, board_cycle

    try:
        try:
            from trading import get_account, get_positions

            acc_bal = next((a for a in get_account() if a.get("asset") == "USDT"), {})
            open_pos_check = True
        except:
            acc_bal = {}
            open_pos_check = False

        all_tickers = fetch_all_tickers()
        usdt = {s: t for s, t in all_tickers.items() if s.endswith("USDT")}
        usdt = {
            s: t
            for s, t in usdt.items()
            if float(t.get("quoteVolume", 0) or 0) >= MIN_VOLUME_24H
        }

        if COIN_UNIVERSE == "top_movers":
            top = sorted(
                usdt.items(),
                key=lambda x: abs(float(x[1].get("priceChangePercent", 0) or 0)),
                reverse=True,
            )[:50]
        elif COIN_UNIVERSE == "whitelist":
            top = [(s, t) for s, t in usdt.items() if s in COINS_WHITELIST]
            top.sort(key=lambda x: float(x[1].get("quoteVolume", 0) or 0), reverse=True)
        else:
            top = sorted(
                usdt.items(),
                key=lambda x: float(x[1].get("quoteVolume", 0) or 0),
                reverse=True,
            )

        latest_tickers = dict(top)

        now = time.time()
        ts = datetime.now(pytz.timezone("Asia/Jakarta")).strftime("%H:%M:%S")

        for sym, ticker in top:
            price = float(ticker.get("lastPrice", 0))
            volume = float(ticker.get("volume", 0))
            change_pct = float(ticker.get("priceChangePercent", 0))
            ticker_history[sym].append((now, price, volume, change_pct))
            if len(ticker_history[sym]) > 20:
                ticker_history[sym] = ticker_history[sym][-20:]

        # Parallel klines fetch
        klines_cache = {}
        scan_count = len(COINS_WHITELIST) if COIN_UNIVERSE == "whitelist" else 50
        syms_to_fetch = [sym for sym, _ in top[:scan_count]]
        with ThreadPoolExecutor(max_workers=min(20, len(syms_to_fetch))) as executor:
            futures = {executor.submit(fetch_klines, sym): sym for sym in syms_to_fetch}
            for future in as_completed(futures):
                sym = futures[future]
                try:
                    kl = future.result()
                    if kl:
                        klines_cache[sym] = kl
                except:
                    pass

        rows = []
        for sym, ticker in top:
            price = float(ticker.get("lastPrice", 0))
            chg = float(ticker.get("priceChangePercent", 0))
            arrow = "▲" if chg > 0 else "▼" if chg < 0 else "─"

            rsi, mom5, vol_r, ema_val, atr_val, adx_val = 50, 0, 1, None, None, None
            kl = klines_cache.get(sym)
            if kl and len(kl) >= 20:
                closes = [float(k[4]) for k in kl]
                volumes = [float(k[5]) for k in kl]
                rsi = calc_rsi(closes)
                mom5 = calc_mom(closes, 5)
                vol_r = calc_vol_ratio(volumes)
                ema_val = calc_ema(closes, EMA_LENGTH) if USE_EMA_FILTER else None
                atr_val = calc_atr(closes) if USE_DYNAMIC_SL else None
                adx_val = calc_adx(closes)

            signal = ""
            if rsi < RSI_OVERSOLD and mom5 > MOM_THRESHOLD:
                signal = "📈BOUNCE"
            elif rsi > RSI_OVERBOUGHT and mom5 < -MOM_THRESHOLD:
                signal = "📉FADE"
            elif vol_r > VOL_RATIO_MIN and abs(mom5) > MOM_THRESHOLD:
                signal = "⚡SPIKE"

            rows.append(
                {
                    "symbol": sym,
                    "price": price,
                    "change_pct": chg,
                    "rsi": rsi,
                    "mom5": mom5,
                    "vol_ratio": vol_r,
                    "signal": signal,
                    "arrow": arrow,
                    "ema": ema_val,
                    "atr": atr_val,
                    "adx": adx_val,
                }
            )

        board_cycle += 1
        board_data = {
            "rows": rows,
            "ts": ts,
            "tracked": len(latest_tickers),
            "cycle": board_cycle,
        }

        with open(LIVE_DATA_FILE, "w") as f:
            json.dump(board_data, f)

        # Signal entry + immediate order
        positions = get_positions() if open_pos_check else None
        open_syms = (
            {p["symbol"] for p in positions if float(p.get("positionAmt", 0)) != 0}
            if positions
            else set()
        )
        open_count = len(open_syms)

        # BTC macro bias — computed once per scan cycle
        btc_macro = get_btc_macro()
        if btc_macro:
            adx_str = (
                f"{btc_macro['adx']:.0f}" if btc_macro.get("adx") is not None else "N/A"
            )
            print(
                f"  [MACRO] BTC: mom={btc_macro['mom']:+.2f}% RSI={btc_macro['rsi']:.0f} ADX={adx_str} bias={btc_macro['bias']}"
            )

        for sym, ticker in top[:scan_count]:
            if open_count >= MAX_POSITIONS:
                break
            if sym in open_syms:
                continue

            sig = check_signal(sym, ticker, klines_cache, btc_macro)
            if not sig:
                continue

            last_alert[sym] = now
            open_count += 1

            if USE_DYNAMIC_SL and sig.get("atr"):
                atr_pct = sig["atr"] / sig["price"] * 100
                sl_pct = max(atr_pct * 1.5, STOP_LOSS_PCT_FALLBACK)
                tp1_pct = atr_pct * 1.5
                tp2_pct = atr_pct * 3.0
            else:
                sl_pct = sig.get("sl_pct") or STOP_LOSS_PCT
                tp1_pct = sig.get("tp1_pct") or TAKE_PROFIT_PCT
                tp2_pct = sig.get("tp2_pct") or TAKE_PROFIT_PCT * 2

            sl = (
                sig["price"] * (1 - sl_pct / 100)
                if sig["signal"] == "LONG"
                else sig["price"] * (1 + sl_pct / 100)
            )
            tp1 = (
                sig["price"] * (1 + tp1_pct / 100)
                if sig["signal"] == "LONG"
                else sig["price"] * (1 - tp1_pct / 100)
            )
            tp2 = (
                sig["price"] * (1 + tp2_pct / 100)
                if sig["signal"] == "LONG"
                else sig["price"] * (1 - tp2_pct / 100)
            )
            emoji = "🟢" if sig["signal"] == "LONG" else "🔴"

            from trading import place_order, set_leverage, calc_quantity_from_risk

            try:
                set_leverage(sym, LEVERAGE)
                qty = calc_quantity_from_risk(
                    sym,
                    float(acc_bal.get("availableBalance", 0)) if acc_bal else 4000,
                    sig["price"],
                    STOP_LOSS_PCT_FALLBACK,
                    LEVERAGE,
                )
                if qty <= 0:
                    continue

                side = "BUY" if sig["signal"] == "LONG" else "SELL"
                result = place_order(sym, side, "MARKET", qty)

                if result and result.get("orderId"):
                    fills = result.get("fills", [{}])
                    avg_price = (
                        float(fills[0].get("price", sig["price"]))
                        if fills
                        else sig["price"]
                    )
                    print(f"\n{'=' * 55}")
                    print(f"  🚨 ENTRY: {sym} {sig['signal']} @ ${avg_price}")
                    print(
                        f"     Qty: {qty} | Lev: {LEVERAGE}x | Conf: {sig['confidence']}/9"
                    )
                    print(
                        f"     RSI: {sig['rsi']} | Mom: {sig['mom5']:+.2f}% | Vol: {sig['vol_ratio']}"
                    )
                    print(f"     SL: ${sl:.4f} | TP1: ${tp1:.4f} | TP2: ${tp2:.4f}")
                    print(f"{'=' * 55}")
                    discord_notify(
                        f"✅ ENTRY: {sym} {sig['signal']} — MARKET FILLED",
                        f"**Price:** `${avg_price}`\n**Qty:** `{qty}`\n**Leverage:** {LEVERAGE}x\n**Conf:** {sig['confidence']}/10\n**RSI:** `{sig['rsi']}` | **Mom:** `{sig['mom5']:+.2f}%`\n**SL:** `${sl:.4f}` | **TP1:** `${tp1:.4f}` | **TP2:** `${tp2:.4f}`",
                        color=0x00FF00 if sig["signal"] == "LONG" else 0xFF4444,
                    )
                    print(f"     SL: ${sl:.4f} | TP: ${tp:.4f}")
                    print(f"{'=' * 55}")
                    discord_notify(
                        f"✅ ENTRY: {sym} {sig['signal']} — MARKET FILLED",
                        f"**Price:** `${avg_price}`\n**Qty:** `{qty}`\n**Leverage:** {LEVERAGE}x\n**Conf:** {sig['confidence']}/9\n**RSI:** `{sig['rsi']}` | **Mom:** `{sig['mom5']:+.2f}%`\n**SL:** `${sl:.4f}` | **TP:** `${tp:.4f}`",
                        color=0x00FF00 if sig["signal"] == "LONG" else 0xFF4444,
                    )
                else:
                    print(f"  ❌ ORDER FAILED: {sym} {sig['signal']} — no fill")
                    discord_notify(
                        f"❌ ORDER FAILED: {sym} {sig['signal']}",
                        f"**Price:** `${sig['price']}`\n**Conf:** {sig['confidence']}/9\n**Status:** Order not filled",
                        color=0xFF4444,
                    )
            except Exception as e:
                print(f"  ❌ ENTRY ERROR: {sym}: {e}")

            time.sleep(1)

        # Update Discord board
        try:
            positions = get_positions()
        except:
            positions = None
        embed = build_board_embed(board_data, positions, BASE_URL)
        msg_id = get_board_msg_id()
        if msg_id:
            result = discord_req(
                "PATCH",
                f"/channels/{DISCORD_BOARD_CHANNEL_ID}/messages/{msg_id}",
                data={"content": None, "embeds": [embed]},
            )
            if not result:
                msg_id = None
            else:
                print(f"[DISCORD] Board updated: msg_id={msg_id}")
        if not msg_id:
            result = discord_req(
                "POST",
                f"/channels/{DISCORD_BOARD_CHANNEL_ID}/messages",
                data={"content": None, "embeds": [embed]},
            )
            if result and result.get("id"):
                save_board_msg_id(result["id"])
                print(f"[DISCORD] New board created: msg_id={result['id']}")

        hot = len([r for r in rows if r.get("signal")])
        print(
            f"  [SCAN #{board_cycle}] {ts} | {len(latest_tickers)} coins | {hot} signals"
        )

    except Exception as e:
        print(f"  [SCAN ERROR] {e}")
        self_heal_circuit_breaker()


# ─── MAIN ─────────────────────────────────────────────────────────────────────


def main():
    global autopilot_paused_until, consecutive_scan_errors
    print(f"\n{'=' * 60}")
    print(f"  🐤 PROFESSOR MODE - Unified Trading System")
    print(f"  Config: SL={STOP_LOSS_PCT}% | TP={TAKE_PROFIT_PCT}% | Lev={LEVERAGE}x")
    print(
        f"  Channels: Scanner({SCAN_INTERVAL}s) | SL/TP({SLTP_INTERVAL}s) | Autopilot({AUTOPILOT_INTERVAL / 60:.0f}min)"
    )
    print(f"{'=' * 60}\n")

    last_autopilot = 0

    while True:
        try:
            loop_start = time.time()
            scan_cycle()
            self_heal_health_check()
            check_sl_tp()

            if autopilot_paused_until > 0:
                if time.time() >= autopilot_paused_until:
                    autopilot_paused_until = 0
                    consecutive_scan_errors = 0
                    print("[AUTOPILOT] Resuming after cooldown")
                    discord_notify(
                        "✅ Autopilot RESUMED", "Self-healing complete.", color=0x00FF00
                    )
                else:
                    remaining = int(autopilot_paused_until - time.time())
                    if int(time.time()) % 60 == 0:
                        print(f"[AUTOPILOT] Paused — {remaining}s remaining")
            elif time.time() - last_autopilot >= AUTOPILOT_INTERVAL:
                run_autopilot()
                last_autopilot = time.time()

            elapsed = time.time() - loop_start
            time.sleep(max(1, SCAN_INTERVAL - elapsed))

        except KeyboardInterrupt:
            print("\nProfessor Mode stopped.")
            break
        except Exception as e:
            print(f"[MAIN ERROR] {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()
