"""
Risk Management Module — Professor Mode
- Per-trade SL/TP with actual order placement
- Daily drawdown circuit breaker
- Dynamic position sizing (Kelly fraction + volatility adjusted)
- Market regime filter
"""
import time
import requests
import threading
from datetime import datetime, timezone
from config import STOP_LOSS_PCT, TAKE_PROFIT_PCT, RISK_PER_TRADE, LEVERAGE, DISCORD_WEBHOOK, API_KEY, API_SECRET, FUTURES_URL as BASE_URL
from proxies import get_proxy

# ─── DRAWDOWN TRACKER ───────────────────────────────────────────────────────

class DrawdownBreaker:
    """Track daily P&L and halt trading if drawdown exceeds threshold."""
    
    def __init__(self, max_daily_loss_pct=10.0):
        self.max_daily_loss_pct = max_daily_loss_pct
        self.daily_start_balance = None
        self.daily_loss = 0.0
        self.lock = threading.Lock()
        self._today = None

    def check(self, current_balance):
        now = datetime.now(timezone.utc)
        today = now.date()

        with self.lock:
            # Reset if new day
            if self._today != today:
                self._today = today
                self.daily_start_balance = current_balance
                self.daily_loss = 0.0

            if self.daily_start_balance and self.daily_start_balance > 0:
                drawdown = (self.daily_start_balance - current_balance) / self.daily_start_balance * 100
                if drawdown >= self.max_daily_loss_pct:
                    return False, f"⛔ DRAWODOWN BREAKER: {drawdown:.1f}% daily loss exceeded {self.max_daily_loss_pct}%"
                
            return True, None

    def record_trade(self, pnl):
        """pnl in USDT (positive = profit, negative = loss)"""
        with self.lock:
            self.daily_loss += pnl

breaker = DrawdownBreaker(max_daily_loss_pct=10.0)

# ─── MARKET REGIME DETECTOR ──────────────────────────────────────────────────

def detect_regime(closes, volumes, lookback=20):
    """
    Detect market regime: TRENDING, RANGING, or VOLATILE
    Uses ADX + Bollinger Band width + momentum alignment
    """
    if len(closes) < lookback + 5:
        return "RANGING", 50.0

    # Bollinger Band width (volatility proxy)
    recent = closes[-lookback:]
    sma = sum(recent) / lookback
    variance = sum((p - sma) ** 2 for p in recent) / lookback
    std = variance ** 0.5
    bb_width = std / sma if sma > 0 else 0
    
    # Momentum
    mom = (closes[-1] - closes[-lookback]) / closes[-lookback] * 100 if closes[-lookback] > 0 else 0
    
    # Volume trend
    vol_recent = sum(volumes[-5:]) / 5
    vol_older = sum(volumes[-lookback:-5]) / (lookback - 5)
    vol_ratio = vol_recent / vol_older if vol_older > 0 else 1.0

    # ADX-like calculation
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in deltas[-lookback:]]
    losses = [-d if d < 0 else 0 for d in deltas[-lookback:]]
    avg_gain = sum(gains) / lookback
    avg_loss = sum(losses) / lookback
    rs = avg_gain / avg_loss if avg_loss > 0 else 999
    adx = 100 - (100 / (1 + rs)) if rs > 0 else 0

    # Regime logic
    if bb_width > 0.05:  # High volatility
        detected_regime = "VOLATILE"
    elif adx > 30 and abs(mom) > 3:
        detected_regime = "TRENDING"
    else:
        detected_regime = "RANGING"

    confidence = min(adx, 100)
    return detected_regime, confidence

# ─── KELLY CRITERION SIZING ──────────────────────────────────────────────────

def kelly_fraction(win_rate, avg_win, avg_loss, fraction=0.25):
    """
    Kelly Criterion position size fraction.
    Capped at 'fraction' (e.g. 0.25 = quarter Kelly for risk management)
    win_rate: 0.0 to 1.0
    avg_win, avg_loss: positive numbers
    """
    if avg_loss <= 0 or win_rate <= 0 or win_rate >= 1:
        return fraction  # safe default
    
    b = avg_win / avg_loss  # odds ratio
    q = 1 - win_rate
    kelly = (b * win_rate - q) / b
    
    # Bound and cap
    kelly = max(0, min(kelly, fraction))
    return kelly

# ─── VOLATILITY-ADJUSTED STOP LOSS ───────────────────────────────────────────

def calc_dynamic_sl(prices, atr_multiplier=2.0):
    """
    Calculate dynamic stop-loss based on ATR (Average True Range).
    More adaptive than fixed percentage.
    """
    if len(prices) < 15:
        return STOP_LOSS_PCT  # fallback to config

    tr_list = []
    for i in range(1, min(len(prices), 50)):
        high = prices[i]
        low = prices[i]
        prev_close = prices[i-1]
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        tr_list.append(tr)

    atr = sum(tr_list[-14:]) / 14 if len(tr_list) >= 14 else tr_list[-1]
    price = prices[-1]
    atr_pct = atr / price * 100
    
    # Use larger of ATR-based or config SL
    sl_pct = max(atr_pct * atr_multiplier, STOP_LOSS_PCT)
    return round(sl_pct, 2)

# ─── ACTUAL SL/TP ORDER PLACEMENT ────────────────────────────────────────────

def place_stop_loss(symbol, side, entry_price, sl_pct, quantity):
    """
    Place STOP-LOSS order. Tries STOP_MARKET first; falls back to manual tracking.
    Demo exchange often blocks algo orders (4120) — in that case we log SL/TP
    prices for manual monitoring.
    """
    if side == "BUY":
        stop_price = round(entry_price * (1 - sl_pct / 100), 8)
        params = {
            "symbol": symbol, "side": "SELL", "type": "STOP_MARKET",
            "stopPrice": str(stop_price), "quantity": str(quantity), "reduceOnly": "true"
        }
    else:
        stop_price = round(entry_price * (1 + sl_pct / 100), 8)
        params = {
            "symbol": symbol, "side": "BUY", "type": "STOP_MARKET",
            "stopPrice": str(stop_price), "quantity": str(quantity), "reduceOnly": "true"
        }
    result = _place_algo_order(params)
    if result is None:
        # Demo API doesn't support — log for manual tracking
        print(f"[SL/TP NOTE] Demo API blocked. Manual SL @ {stop_price}")
        return {"sl_price": stop_price, "placed": False, "note": "manual"}
    return {"sl_price": stop_price, "placed": True, "order": result}

def place_take_profit(symbol, side, entry_price, tp_pct, quantity):
    """
    Place TAKE-PROFIT order. Same demo API limitation.
    """
    if side == "BUY":
        stop_price = round(entry_price * (1 + tp_pct / 100), 8)
        params = {
            "symbol": symbol, "side": "SELL", "type": "TAKE_PROFIT_MARKET",
            "stopPrice": str(stop_price), "quantity": str(quantity), "reduceOnly": "true"
        }
    else:
        stop_price = round(entry_price * (1 - tp_pct / 100), 8)
        params = {
            "symbol": symbol, "side": "BUY", "type": "TAKE_PROFIT_MARKET",
            "stopPrice": str(stop_price), "quantity": str(quantity), "reduceOnly": "true"
        }
    result = _place_algo_order(params)
    if result is None:
        print(f"[SL/TP NOTE] Demo API blocked. Manual TP @ {stop_price}")
        return {"tp_price": stop_price, "placed": False, "note": "manual"}
    return {"tp_price": stop_price, "placed": True, "order": result}

def _place_algo_order(params):
    """
    Execute signed POST request for algo order.
    Note: Demo exchange often returns 4120 for algo orders (STOP_MARKET, etc.).
    Returns the response or None.
    """
    import hmac, hashlib
    from config import API_KEY, API_SECRET, FUTURES_URL as BASE_URL
    from proxies import get_proxy
    import time
    
    ts = int(time.time() * 1000)
    recv = 60000
    params["timestamp"] = ts
    params["recvWindow"] = recv
    
    # Match trading.py signing style (NOT sorted)
    query = "&".join(f"{k}={v}" for k, v in params.items())
    sig = hmac.new(API_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()
    params["signature"] = sig
    
    url = f"{BASE_URL}/fapi/v1/order"
    headers = {"X-MBX-APIKEY": API_KEY}
    
    try:
        r = requests.post(url, params=params, headers=headers,
                         proxies={"http": get_proxy(), "https": get_proxy()},
                         timeout=15, verify=False)
        if r.status_code == 200:
            return r.json()
        else:
            print(f"[SL/TP ERROR] {r.status_code} {r.text[:150]}")
            return None
    except Exception as e:
        print(f"[SL/TP EXCEPTION] {e}")
        return None

# ─── SIGNAL QUALITY FILTER ────────────────────────────────────────────────────

def signal_quality_check(rsi, adx, bb_pos, momentum, vol_ratio, regime):
    """
    Additional quality gate on top of scanner scores.
    Returns (pass: bool, reason: str)
    """
    # Regime-based filtering
    quality_ok = False
    if regime == "RANGING":
        # In ranging markets, require tighter conditions
        if rsi < 35 or rsi > 65:
            quality_ok = True  # mean-reversion setup
        else:
            return False, "RANGING: RSI not at extremes (need <35 or >65)"
        
        if adx < 20:
            return False, "RANGING: ADX too weak (need >20 for valid signal)"
    
    elif regime == "TRENDING":
        # In trending markets, ride the momentum
        if adx < 25:
            return False, "TRENDING: ADX too weak (need >25)"
        if momentum < 1.0:
            return False, "TRENDING: momentum too low (need >1%)"
        quality_ok = True
    
    elif regime == "VOLATILE":
        # In volatile markets, widen SL and require high confidence
        if rsi < 30 or rsi > 70:
            quality_ok = True
        else:
            return False, "VOLATILE: RSI needs to be at extreme (<30 or >70)"
        if adx < 30:
            return False, "VOLATILE: ADX need >30 for strong signal"
        quality_ok = True

    # BB sanity
    if bb_pos < 0.05 or bb_pos > 0.95:
        return False, f"BB at extreme ({bb_pos}) — likely fakeout"

    # Volume sanity
    if vol_ratio < 0.3:
        return False, f"Volume too low ({vol_ratio} < 0.3) — weak conviction"

    return True, "PASS"

# ─── ENTRY QUALITY SCORE ─────────────────────────────────────────────────────

def entry_score(symbol, closes, highs, lows, volumes, regime, rsi, adx, bb_pos, momentum, vol_ratio):
    """
    Professor-grade entry quality score (0-100).
    Combines multiple factors with regime-aware weighting.
    """
    score = 0
    max_score = 100
    factors = []

    # Core momentum (heaviest weight)
    if regime == "TRENDING":
        if momentum > 5:   score += 25; factors.append(f"+25 mom {momentum:.1f}%")
        elif momentum > 2: score += 15; factors.append(f"+15 mom {momentum:.1f}%")
        elif momentum > 0: score += 5;  factors.append(f"+5 mom {momentum:.1f}%")
        elif momentum < -3: score -= 10; factors.append(f"-10 mom {momentum:.1f}%")
    else:
        # RANGING/VOLATILE: mean reversion setups
        if rsi < 35: score += 20; factors.append(f"+20 RSI {rsi}")
        elif rsi < 45: score += 10; factors.append(f"+10 RSI {rsi}")
        elif rsi > 65: score -= 5; factors.append(f"-5 RSI {rsi}")

    # Trend strength
    if adx > 40:   score += 20; factors.append(f"+20 ADX {adx}")
    elif adx > 30: score += 12; factors.append(f"+12 ADX {adx}")
    elif adx > 20: score += 5;  factors.append(f"+5 ADX {adx}")
    else:          score -= 5;  factors.append(f"-5 weak ADX {adx}")

    # Bollinger position (entry at extremes = better)
    if bb_pos < 0.15:   score += 15; factors.append(f"+15 BB {bb_pos}")
    elif bb_pos < 0.25: score += 8;  factors.append(f"+8 BB {bb_pos}")
    elif bb_pos > 0.85: score -= 5;  factors.append(f"-5 BB {bb_pos}")

    # Volume confirmation
    if vol_ratio > 2.0:  score += 15; factors.append(f"+15 vol {vol_ratio:.2f}")
    elif vol_ratio > 1.5: score += 8; factors.append(f"+8 vol {vol_ratio:.2f}")
    elif vol_ratio > 1.0: score += 3; factors.append(f"+3 vol {vol_ratio:.2f}")
    else:                score -= 3;  factors.append(f"-3 low vol {vol_ratio:.2f}")

    # RSI zone
    if 40 <= rsi <= 60:
        score -= 5  # neutral zone penalty unless trending confirmed

    score = max(0, min(score, max_score))
    return score, factors

# ─── POST-TRADE JOURNAL ──────────────────────────────────────────────────────

_journal = []

def journal_entry(sym, side, entry_price, sl, tp, qty, regime, quality_score, result=None):
    """Record trade for later analysis."""
    _journal.append({
        "symbol": sym,
        "side": side,
        "entry": entry_price,
        "sl": sl,
        "tp": tp,
        "qty": qty,
        "regime": regime,
        "quality_score": quality_score,
        "result": result,
        "time": datetime.now(timezone.utc).isoformat(),
    })

def get_journal_stats():
    if not _journal:
        return {"total": 0, "win_rate": 0, "avg_win": 0, "avg_loss": 0}
    
    closed = [j for j in _journal if j["result"] is not None]
    if not closed:
        return {"total": len(_journal), "open": len(_journal), "win_rate": None}
    
    wins = [j for j in closed if j["result"]["pnl"] > 0]
    losses = [j for j in closed if j["result"]["pnl"] <= 0]
    
    avg_win = sum(j["result"]["pnl"] for j in wins) / len(wins) if wins else 0
    avg_loss = abs(sum(j["result"]["pnl"] for j in losses) / len(losses)) if losses else 0
    
    return {
        "total": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(closed) if closed else 0,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "profit_factor": (avg_win * len(wins) / (avg_loss * len(losses))) if losses and avg_loss > 0 else None,
    }
