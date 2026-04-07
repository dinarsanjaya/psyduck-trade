"""Technical indicators for signal generation."""

def calc_rsi(prices, period=14):
    if len(prices) < period + 1:
        return 50.0
    deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    gains = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period
    rs = ag / al if al > 0 else 999
    return 100 - 100 / (1 + rs)


def calc_mom(prices, bars=5):
    if len(prices) < bars + 1:
        return 0.0
    return (prices[-1] - prices[-bars]) / prices[-bars] * 100


def calc_vol_ratio(volumes):
    if len(volumes) < 10:
        return 1.0
    avg = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else sum(volumes) / len(volumes)
    return volumes[-1] / avg if avg > 0 else 1.0


def calc_ema(prices, length=20):
    """Calculate EMA. Returns None if not enough data."""
    if len(prices) < length:
        return None
    k = 2 / (length + 1)
    ema = sum(prices[:length]) / length
    for price in prices[length:]:
        ema = price * k + ema * (1 - k)
    return ema


def calc_atr(prices, period=14):
    """Calculate Average True Range for dynamic SL."""
    if len(prices) < period + 1:
        return None
    tr_list = []
    for i in range(1, min(len(prices), 50)):
        high = prices[i]
        low = prices[i]
        prev_close = prices[i - 1]
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        tr_list.append(tr)
    if len(tr_list) < period:
        return None
    return sum(tr_list[-period:]) / period
